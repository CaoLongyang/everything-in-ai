"""解析浏览器插件导出的 Gemini 对话 zip（gemini_chats_YYYY-MM-DD.zip）。

zip 根目录下每个对话一个 JSON，另有一个汇总报告 JSON。格式由插件决定，不是 Google 的契约，
只认 2026-11 的新版（带时间）；格式说明见 docs/specs/chat-import.delta-gemini.md。
"""

from __future__ import annotations

import datetime as dt
import json
import zipfile
from pathlib import Path
from typing import Any

from eia.chat.model import Conversation, ExportFormatError, Message, Role

PLATFORM = "gemini"
UNTITLED = "未命名对话"
_ROLES: dict[str, Role] = {"user": "user", "assistant": "assistant"}
_REPORT_KEYS = frozenset({"listTotal", "exported"})
_HINT = "请传入浏览器插件导出的 gemini_chats_YYYY-MM-DD.zip（不用解压）"
_OLD_FORMAT = "这是旧版插件的导出，没有时间信息，请用新版插件重新导出"


def _time(value: Any, where: str) -> dt.datetime:
    if not isinstance(value, str):
        raise ExportFormatError(f"{where} 应为 ISO 时间字符串，实际是 {type(value).__name__}")
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ExportFormatError(f"{where} 不是合法的时间：{value}") from exc
    if parsed.tzinfo is None:
        raise ExportFormatError(f"{where} 缺少时区：{value}")
    return parsed.astimezone(dt.UTC).replace(microsecond=0)


def _message(raw: Any, where: str) -> Message | None:
    if not isinstance(raw, dict):
        raise ExportFormatError(f"{where}：每条消息应为一个对象")
    if "time" not in raw:
        if "blocks" in raw:
            raise ExportFormatError(_OLD_FORMAT)
        raise ExportFormatError(f"{where}：缺少字段 time")
    sender = raw.get("role")
    role = _ROLES.get(sender) if isinstance(sender, str) else None
    content = raw.get("content")
    if not isinstance(content, str):
        raise ExportFormatError(f"{where}：content 应为文字")
    when = _time(raw["time"], f"{where} time")
    text = content.strip()
    if role is None or not text:
        return None  # 不认识的说话方、空消息（只发了图片或文件，导出里没有）不导入
    return Message(role=role, time=when, text=text)


def parse_conversation(raw: dict[str, Any], where: str) -> Conversation:
    conversation_id = raw.get("conversationId")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise ExportFormatError(f"{where}：缺少字段 conversationId")
    where = f"{where}（对话 {conversation_id}）"
    raw_messages = raw["messages"]
    if not isinstance(raw_messages, list):
        raise ExportFormatError(f"{where}：messages 应为列表")
    if "createdAt" not in raw and any(isinstance(m, dict) and "blocks" in m for m in raw_messages):
        raise ExportFormatError(_OLD_FORMAT)
    created = _time(raw.get("createdAt"), f"{where} createdAt")
    updated = _time(raw.get("updatedAt"), f"{where} updatedAt")
    messages: list[Message] = []
    newest = max(created, updated)
    for index, raw_message in enumerate(raw_messages):
        message = _message(raw_message, f"{where} 第 {index + 1} 条消息")
        if message is not None:
            messages.append(message)
            newest = max(newest, message.time)
    title = raw.get("title")
    url = raw.get("url")
    return Conversation(
        platform=PLATFORM,
        conversation_id=conversation_id,
        title=title.strip() if isinstance(title, str) and title.strip() else UNTITLED,
        created=created,
        updated=newest,  # 防插件以后改了 updatedAt 的算法：取对话和消息里最晚的
        model=None,  # 导出里没有模型名
        messages=tuple(messages),
        thinking=None,  # 导出里没有思考过程，不写 thinking 字段
        url=url if isinstance(url, str) and url else None,
    )


def _count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def report_note(report: dict[str, Any]) -> str | None:
    """汇总报告里有失败、缺失、可能不完整或手动停止时，给一行提示；否则 None。"""
    failed = _count(report.get("failures")) + _count(report.get("missing"))
    partial = _count(report.get("possiblyIncomplete"))
    stopped = report.get("stoppedManually") is True
    if not (failed or partial or stopped):
        return None
    parts = [
        f"插件报告侧边栏共 {_count(report.get('listTotal'))} 个对话，"
        f"导出 {_count(report.get('exported'))} 个",
        f"{failed} 个失败或缺失",
        f"{partial} 个可能不完整",
    ]
    if stopped:
        parts.append("导出被手动停止")
    return "；".join(parts) + "。用插件补导出后再导入一次即可"


def _load(archive: zipfile.ZipFile, name: str, label: str) -> Any:
    try:
        return json.loads(archive.read(name).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportFormatError(f"{label} 中的 {name} 不是合法的 JSON：{exc}") from exc


def read_export(path: Path) -> tuple[list[Conversation], list[str]]:
    """读取插件导出的 zip，返回 (对话列表, 提示信息)。任何格式问题都抛 ExportFormatError。"""
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExportFormatError(f"{path.name} 无法作为 zip 打开：{exc}。{_HINT}") from exc
    newest: dict[str, Conversation] = {}
    notes: list[str] = []
    report: str | None = None
    with archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".json")]
        if any(Path(n).name in ("conversations.json", "conversations-000.json") for n in names):
            raise ExportFormatError(
                f"{path.name} 像是 ChatGPT 或 Claude 的导出，请用 eia import chatgpt / claude"
            )
        for name in sorted(names):
            data = _load(archive, name, path.name)
            if isinstance(data, dict) and "messages" in data:
                conversation = parse_conversation(data, f"{path.name} 中的 {name}")
                known = newest.get(conversation.conversation_id)
                if known is not None:
                    notes.append(
                        f"对话 {conversation.conversation_id} 出现在多个文件里，使用较新的那份"
                    )
                    if known.updated >= conversation.updated:
                        continue
                newest[conversation.conversation_id] = conversation
            elif isinstance(data, dict) and _REPORT_KEYS <= data.keys():
                note = report_note(data)
                if note is not None:
                    report = note
            else:
                raise ExportFormatError(f"{path.name} 中的 {name} 不是认识的 Gemini 对话。{_HINT}")
    if not newest:
        raise ExportFormatError(f"{path.name} 里没有 Gemini 对话。{_HINT}")
    if report is not None:
        notes.append(report)
    return list(newest.values()), notes
