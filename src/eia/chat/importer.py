"""把解析好的对话写进 vault：新建、更新或跳过。

先完整解析导出、算好每个对话要做什么，再统一写入；解析出错时一个文件都不写。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eia.chat.model import Conversation
from eia.chat.render import MarkerError, new_body, replace_block
from eia.schema.ids import chat_id
from eia.schema.io import (
    FrontmatterError,
    atomic_write,
    create_new,
    parse_text,
    read_document,
    render_document,
    update_frontmatter,
)
from eia.schema.model import SCHEMA_VERSION, EntryType, Status
from eia.schema.naming import entry_filename
from eia.schema.validate import iter_markdown
from eia.schema.vault import dir_for, load_config


@dataclass
class ImportResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)


def _as_utc(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.astimezone(dt.UTC)
    return None


def index_chats(root: Path, platform: str) -> dict[str, list[Path]]:
    """按 conversation_id 找出 vault 里已有的对话文件。按 id 找，不按文件名。"""
    found: dict[str, list[Path]] = {}
    for path in iter_markdown(root):
        try:
            meta = read_document(path).meta
        except FrontmatterError:
            continue
        conversation = meta.get("conversation_id")
        if meta.get("platform") == platform and isinstance(conversation, str):
            found.setdefault(conversation, []).append(path)
    return found


def _new_meta(conversation: Conversation) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "id": chat_id(conversation.platform, conversation.conversation_id, conversation.created),
        "type": EntryType.chat.value,
        "title": conversation.title,
        "created": conversation.created,
        "updated": conversation.updated,
        "status": Status.inbox.value,
        "tags": [],
        "source": conversation.platform,
        "platform": conversation.platform,
        "conversation_id": conversation.conversation_id,
        "model": conversation.model,
        "message_count": len(conversation.messages),
        **_thinking_field(conversation),
    }


def _thinking_field(conversation: Conversation) -> dict[str, Any]:
    """只有支持 --include-thinking 的平台才写 thinking 字段。"""
    return {} if conversation.thinking is None else {"thinking": conversation.thinking}


def _same_thinking(meta: dict[str, Any], conversation: Conversation) -> bool:
    """文件里的 thinking 和这次导入的开关是否一致；字段缺失视为 false。"""
    if conversation.thinking is None:
        return True
    return (meta.get("thinking") is True) == conversation.thinking


def _updated_text(raw: str, conversation: Conversation, tz: dt.tzinfo | None) -> str:
    changes = {
        "title": conversation.title,
        "updated": conversation.updated,
        "model": conversation.model,
        "message_count": len(conversation.messages),
        **_thinking_field(conversation),
    }
    text = update_frontmatter(raw, changes)
    body = parse_text(text, Path()).body
    head = text[: len(text) - len(body)]
    return head + replace_block(body, conversation, tz)


def import_conversations(
    root: Path, conversations: Sequence[Conversation], *, dry_run: bool = False
) -> ImportResult:
    config = load_config(root)
    result = ImportResult()
    platforms = {conversation.platform for conversation in conversations}
    existing: dict[tuple[str, str], list[Path]] = {}
    for platform in platforms:
        for key, paths in index_chats(root, platform).items():
            existing[(platform, key)] = paths

    for conversation in conversations:
        if not conversation.messages:
            result.skipped.append((conversation.title, "对话里没有可导入的消息"))
            continue
        paths = existing.get((conversation.platform, conversation.conversation_id), [])
        if len(paths) > 1:
            names = "、".join(path.relative_to(root).as_posix() for path in paths)
            result.skipped.append((names, "同一个对话在 vault 里有多个文件，请先删掉多余的"))
            continue
        if not paths:
            folder = root / dir_for(EntryType.chat.value, conversation.platform)
            meta = _new_meta(conversation)
            name = entry_filename(
                conversation.title, meta["id"], conversation.created, config.timezone
            )
            target = folder / name
            if not dry_run:
                folder.mkdir(parents=True, exist_ok=True)
                try:
                    create_new(
                        target, render_document(meta, new_body(conversation, config.timezone))
                    )
                except FileExistsError:
                    result.skipped.append((target.relative_to(root).as_posix(), "同名文件已存在"))
                    continue
            result.created.append(target.relative_to(root).as_posix())
            continue

        path = paths[0]
        rel = path.relative_to(root).as_posix()
        try:
            document = read_document(path)
        except FrontmatterError as exc:
            result.skipped.append((rel, str(exc)))
            continue
        known = _as_utc(document.meta.get("updated"))
        if (
            known is not None
            and known >= conversation.updated
            and _same_thinking(document.meta, conversation)
        ):
            result.unchanged += 1
            continue
        try:
            text = _updated_text(document.raw, conversation, config.timezone)
        except (MarkerError, FrontmatterError) as exc:
            result.skipped.append((rel, f"{exc}，文件未改动"))
            continue
        if not dry_run:
            atomic_write(path, text)
        result.updated.append(rel)
    return result
