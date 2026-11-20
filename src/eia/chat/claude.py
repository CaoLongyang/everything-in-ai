"""解析 Claude 官方导出的 conversations-NNN.zip。

只读其中的 `conversations.json`；projects、memories、账号信息等其他分类一律不读。
格式说明见 SPEC-DELTA-chat-import-v1.md。
"""

from __future__ import annotations

import datetime as dt
import json
import zipfile
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from eia.chat.model import Conversation, ExportFormatError, Message, Role

PLATFORM = "claude"
UNTITLED = "未命名对话"
NIL_UUID = "00000000-0000-4000-8000-000000000000"
CONVERSATIONS = "conversations.json"
UNNAMED_PASTE = "粘贴的文本"
_ROLES: dict[str, Role] = {"human": "user", "assistant": "assistant"}
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".heic"})
_HINT = "请传入 Claude 导出里的 conversations-NNN.zip（不是 manifest，也不是 projects 等其他分类）"


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


def _required_str(raw: dict[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ExportFormatError(f"{where}：缺少字段 {key}")
    return value


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _file_line(name: str) -> str:
    if not name:
        return "[文件]"
    kind = "图片" if Path(name).suffix.lower() in _IMAGE_SUFFIXES else "文件"
    return f"[{kind}：{name}]"


def _placeholders(raw: dict[str, Any], blocks: list[dict[str, Any]]) -> list[str]:
    """附件、文件、图片只留一行占位，不导入内容（SPEC-DELTA 第 2 节）。"""
    lines: list[str] = []
    for attachment in raw.get("attachments") or []:
        if isinstance(attachment, dict):
            name = _clean(attachment.get("file_name")) or UNNAMED_PASTE
            content = attachment.get("extracted_content")
            size = len(content) if isinstance(content, str) else 0
            lines.append(f"[附件：{name}，{size} 字]")
    seen: set[str] = set()
    for item in raw.get("files") or []:
        if isinstance(item, dict):
            uuid = item.get("file_uuid")
            if isinstance(uuid, str):
                seen.add(uuid)
            lines.append(_file_line(_clean(item.get("file_name"))))
    for block in blocks:
        kind = block.get("type")
        if kind not in ("image", "document"):
            continue
        uuid = block.get("file_uuid")
        if isinstance(uuid, str):
            if uuid in seen:
                continue  # 已经在 files[] 里写过占位
            seen.add(uuid)
        if kind == "image":
            lines.append("[图片]")
        else:
            title = _clean(block.get("title"))
            lines.append(f"[文件：{title}]" if title else "[文件]")
    return lines


def _joined(blocks: list[dict[str, Any]], kind: str, key: str) -> str:
    texts = (_clean(block.get(key)) for block in blocks if block.get("type") == kind)
    return "\n\n".join(text for text in texts if text)


def _message(raw: dict[str, Any], where: str, include_thinking: bool) -> Message | None:
    sender = raw.get("sender")
    if not isinstance(sender, str):
        raise ExportFormatError(f"{where}：缺少字段 sender")
    role = _ROLES.get(sender)
    if role is None:
        return None
    content = raw.get("content")
    if not isinstance(content, list):
        raise ExportFormatError(f"{where}：content 应为列表")
    blocks = [block for block in content if isinstance(block, dict)]
    # 工具调用、工具结果、系统插入的提示都不导入；顶层 text 字段带英文占位，不用
    text = _joined(blocks, "text", "text")
    placeholders = "\n".join(_placeholders(raw, blocks))
    parts = [placeholders, text] if role == "user" else [text, placeholders]
    body = "\n\n".join(part for part in parts if part)
    if not body:
        return None
    thinking = _joined(blocks, "thinking", "thinking") if include_thinking else ""
    when = _time(raw.get("created_at"), f"{where} created_at")
    return Message(role=role, time=when, text=body, thinking=thinking)


def _visible_path(
    raw_messages: list[Any], where: str
) -> tuple[list[dict[str, Any]], dt.datetime | None]:
    """沿父子链从根走到最新的叶子；返回 (路径上的消息, 所有消息里最晚的更新时间)。"""
    by_id: dict[str, dict[str, Any]] = {}
    order: dict[str, tuple[dt.datetime, int]] = {}
    latest: dt.datetime | None = None
    for index, raw in enumerate(raw_messages):
        at = f"{where} 第 {index + 1} 条消息"
        if not isinstance(raw, dict):
            raise ExportFormatError(f"{at}：每条消息应为一个对象")
        uuid = _required_str(raw, "uuid", at)
        _required_str(raw, "parent_message_uuid", at)
        if uuid in by_id:
            raise ExportFormatError(f"{where}：消息 {uuid} 重复出现")
        created = _time(raw.get("created_at"), f"{at} created_at")
        stamp = raw.get("updated_at")
        changed = _time(stamp, f"{at} updated_at") if stamp is not None else created
        latest = changed if latest is None else max(latest, changed)
        by_id[uuid] = raw
        order[uuid] = (created, index)
    if not by_id:
        return [], None

    children: dict[str, list[str]] = {uuid: [] for uuid in by_id}
    roots: list[str] = []
    for uuid, raw in by_id.items():
        parent = raw["parent_message_uuid"]
        if parent == NIL_UUID:
            roots.append(uuid)
        elif parent in by_id:
            children[parent].append(uuid)
        else:
            raise ExportFormatError(f"{where}：消息 {uuid} 的父节点指向不存在的消息")
    if not roots:
        raise ExportFormatError(f"{where}：消息树没有根（消息树里有循环）")
    if len(roots) > 1:
        raise ExportFormatError(f"{where}：消息树有多个根")

    reachable: list[str] = []
    queue = deque(roots)
    while queue:
        uuid = queue.popleft()
        reachable.append(uuid)
        queue.extend(children[uuid])
    if len(reachable) != len(by_id):
        raise ExportFormatError(f"{where}：有消息到不了根（消息树里有循环）")

    leaf = max((uuid for uuid in reachable if not children[uuid]), key=order.__getitem__)
    path: list[dict[str, Any]] = []
    node: str = leaf
    while True:
        path.append(by_id[node])
        parent = by_id[node]["parent_message_uuid"]
        if parent == NIL_UUID:
            break
        node = parent
    path.reverse()
    return path, latest


def parse_conversation(raw: Any, where: str, *, include_thinking: bool = False) -> Conversation:
    if not isinstance(raw, dict):
        raise ExportFormatError(f"{where}：每个对话应为一个对象")
    if "chat_messages" not in raw:
        if "mapping" in raw:
            raise ExportFormatError(f"{where}：这像是 ChatGPT 的导出，请用 eia import chatgpt")
        raise ExportFormatError(f"{where}：缺少字段 chat_messages")
    conversation_id = _required_str(raw, "uuid", where)
    where = f"{where}（对话 {conversation_id}）"
    created = _time(raw.get("created_at"), f"{where} created_at")
    updated = _time(raw.get("updated_at"), f"{where} updated_at")
    raw_messages = raw["chat_messages"]
    if not isinstance(raw_messages, list):
        raise ExportFormatError(f"{where}：chat_messages 应为列表")

    path, latest = _visible_path(raw_messages, where)
    messages: list[Message] = []
    for raw_message in path:
        at = f"{where} 消息 {raw_message['uuid']}"
        message = _message(raw_message, at, include_thinking)
        if message is not None:
            messages.append(message)

    # 对话的 updated_at 常常早于最后一条消息，取最晚的，否则重复导入会漏掉新消息
    newest = max(t for t in (created, updated, latest) if t is not None)
    return Conversation(
        platform=PLATFORM,
        conversation_id=conversation_id,
        title=_clean(raw.get("name")) or UNTITLED,
        created=created,
        updated=newest,
        model=None,  # 导出里没有模型名
        messages=tuple(messages),
        thinking=include_thinking,
    )


def _read_one(path: Path, include_thinking: bool) -> list[Conversation]:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExportFormatError(f"{path.name} 无法作为 zip 打开：{exc}。{_HINT}") from exc
    with archive:
        try:
            data = archive.read(CONVERSATIONS)
        except KeyError as exc:
            raise ExportFormatError(f"{path.name} 里找不到 {CONVERSATIONS}。{_HINT}") from exc
    try:
        conversations = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportFormatError(f"{path.name} 中的 {CONVERSATIONS} 不是合法的 JSON：{exc}") from exc
    if not isinstance(conversations, list):
        raise ExportFormatError(f"{path.name} 中的 {CONVERSATIONS}：应为对话列表")
    return [
        parse_conversation(
            raw,
            f"{path.name} 中 {CONVERSATIONS} 第 {index + 1} 个对话",
            include_thinking=include_thinking,
        )
        for index, raw in enumerate(conversations)
    ]


def read_exports(
    paths: Sequence[Path], *, include_thinking: bool = False
) -> tuple[list[Conversation], list[str]]:
    """读取一个或多个 conversations-NNN.zip，返回 (对话列表, 提示信息)。

    任何格式问题都抛 ExportFormatError。同一个对话出现在多个文件里时，用更新时间较晚的那份。
    """
    newest: dict[str, Conversation] = {}
    notes: list[str] = []
    for path in paths:
        for conversation in _read_one(path, include_thinking):
            known = newest.get(conversation.conversation_id)
            if known is not None:
                notes.append(
                    f"对话 {conversation.conversation_id} 出现在多个文件里，使用较新的那份"
                )
                if known.updated >= conversation.updated:
                    continue
            newest[conversation.conversation_id] = conversation
    return list(newest.values()), notes
