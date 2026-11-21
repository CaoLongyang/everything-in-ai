"""解析 ChatGPT 官方导出的 zip。

只读 `conversations.json` 或 `conversations-NNN.json`，账号、设置等其他文件一律不读。
格式说明见 docs/specs/chat-import.md 第 2 节。
"""

from __future__ import annotations

import datetime as dt
import json
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from eia.chat.model import Conversation, ExportFormatError, Message, Role

PLATFORM = "chatgpt"
UNTITLED = "未命名对话"
_SHARD_RE = re.compile(r"(?:^|/)conversations(?:-(\d+))?\.json$")
_SINGLE_FILE_WARN_BYTES = 300 * 1024 * 1024
_ROLES: dict[str, Role] = {"user": "user", "assistant": "assistant"}


def _shards(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    found = [info for info in archive.infolist() if _SHARD_RE.search(info.filename)]

    def order(info: zipfile.ZipInfo) -> tuple[int, str]:
        match = _SHARD_RE.search(info.filename)
        number = match.group(1) if match else None
        return (int(number) if number is not None else -1, info.filename)

    return sorted(found, key=order)


def _time(value: Any, where: str) -> dt.datetime:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ExportFormatError(f"{where}：时间应为数字时间戳，实际是 {type(value).__name__}")
    return dt.datetime.fromtimestamp(value, dt.UTC).replace(microsecond=0)


def _image_placeholder(part: dict[str, Any]) -> str:
    width, height = part.get("width"), part.get("height")
    if isinstance(width, int) and isinstance(height, int):
        return f"[图片，{width}×{height}]"
    return "[图片]"


def _part_text(part: Any) -> str:
    if isinstance(part, str):
        return part.strip()
    if isinstance(part, dict):
        kind = part.get("content_type")
        if kind == "image_asset_pointer":
            return _image_placeholder(part)
        if kind == "audio_transcription":
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                return f"（语音）{text.strip()}"
    return ""


def _message_text(content: dict[str, Any]) -> str:
    if content.get("content_type") not in ("text", "multimodal_text"):
        return ""  # thoughts、reasoning_recap、代码执行结果等都不导入
    parts = content.get("parts") or []
    if not isinstance(parts, list):
        return ""
    return "\n\n".join(text for text in (_part_text(part) for part in parts) if text)


def _visible_path(mapping: dict[str, Any], current: Any, where: str) -> list[dict[str, Any]]:
    """从 current_node 沿 parent 走回根，返回你实际看到的那一版消息，从旧到新。"""
    if not isinstance(current, str) or current not in mapping:
        raise ExportFormatError(f"{where}：current_node 指向不存在的消息")
    path: list[dict[str, Any]] = []
    seen: set[str] = set()
    node_id: Any = current
    while isinstance(node_id, str):
        if node_id in seen:
            raise ExportFormatError(f"{where}：消息树里有循环")
        seen.add(node_id)
        node = mapping.get(node_id)
        if not isinstance(node, dict):
            raise ExportFormatError(f"{where}：找不到消息 {node_id}")
        path.append(node)
        node_id = node.get("parent")
    path.reverse()
    return path


def parse_conversation(raw: Any, where: str) -> Conversation:
    if not isinstance(raw, dict):
        raise ExportFormatError(f"{where}：每个对话应为一个对象")
    for key in ("mapping", "current_node", "create_time", "update_time"):
        if key not in raw:
            raise ExportFormatError(f"{where}：缺少字段 {key}")
    conversation_id = raw.get("conversation_id") or raw.get("id")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise ExportFormatError(f"{where}：缺少对话 ID（conversation_id）")
    where = f"{where}（对话 {conversation_id}）"
    mapping = raw["mapping"]
    if not isinstance(mapping, dict):
        raise ExportFormatError(f"{where}：mapping 应为对象")
    created = _time(raw["create_time"], f"{where} create_time")
    updated = _time(raw["update_time"], f"{where} update_time")

    messages: list[Message] = []
    for node in _visible_path(mapping, raw["current_node"], where):
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        author = message.get("author")
        author_role = author.get("role") if isinstance(author, dict) else None
        role = _ROLES.get(author_role) if isinstance(author_role, str) else None
        metadata = message.get("metadata") or {}
        if role is None or (
            isinstance(metadata, dict) and metadata.get("is_visually_hidden_from_conversation")
        ):
            continue
        content = message.get("content")
        text = _message_text(content) if isinstance(content, dict) else ""
        if not text:
            continue
        stamp = message.get("create_time")
        when = _time(stamp, f"{where} 消息时间") if stamp is not None else created
        messages.append(Message(role=role, time=when, text=text))

    title = raw.get("title")
    model = raw.get("default_model_slug")
    return Conversation(
        platform=PLATFORM,
        conversation_id=conversation_id,
        title=title.strip() if isinstance(title, str) and title.strip() else UNTITLED,
        created=created,
        updated=max(updated, created),
        model=model if isinstance(model, str) and model else None,
        messages=tuple(messages),
    )


def _iter_raw(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> Iterator[Any]:
    try:
        data = json.loads(archive.read(info).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportFormatError(f"{info.filename} 不是合法的 JSON：{exc}") from exc
    if not isinstance(data, list):
        raise ExportFormatError(f"{info.filename}：应为对话列表")
    yield from data


def read_export(path: Path) -> tuple[list[Conversation], list[str]]:
    """读取整个导出，返回 (对话列表, 提示信息)。任何格式问题都抛 ExportFormatError。"""
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExportFormatError(f"{path.name} 无法作为 zip 打开：{exc}") from exc
    notes: list[str] = []
    with archive:
        shards = _shards(archive)
        if not shards:
            raise ExportFormatError(
                f"{path.name} 里找不到 conversations.json。"
                "请确认这是 ChatGPT 的「导出数据」，不是记忆或其他导出"
            )
        conversations: list[Conversation] = []
        for info in shards:
            if info.file_size > _SINGLE_FILE_WARN_BYTES:
                size = info.file_size // (1024 * 1024)
                notes.append(f"{info.filename} 有 {size} MB，读取会占用较多内存")
            for index, raw in enumerate(_iter_raw(archive, info)):
                conversations.append(
                    parse_conversation(raw, f"{info.filename} 第 {index + 1} 个对话")
                )
    return conversations, notes
