"""新建一个带合法 frontmatter 的空条目。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from eia.schema.ids import chat_id, new_id
from eia.schema.io import create_new, render_document
from eia.schema.model import SCHEMA_VERSION, EntryType, Status
from eia.schema.naming import entry_filename
from eia.schema.vault import VaultError, dir_for, load_config

_MAX_ATTEMPTS = 20


def _type_fields(
    entry_type: str,
    created: dt.datetime,
    platform: str | None,
    conversation_id: str | None,
    url: str | None,
) -> dict[str, Any]:
    if entry_type == EntryType.chat.value:
        if not platform or not conversation_id:
            raise VaultError("新建 chat 条目需要 --platform 和 --conversation-id")
        return {"platform": platform, "conversation_id": conversation_id}
    if entry_type == EntryType.headline.value:
        return {"sources": [], "independent_count": 0}
    if entry_type == EntryType.digest.value:
        return {"date": created.date()}
    return {"url": url} if url else {}


def new_entry(
    root: Path,
    entry_type: str,
    title: str,
    *,
    platform: str | None = None,
    conversation_id: str | None = None,
    url: str | None = None,
    now: dt.datetime | None = None,
) -> Path:
    """在 vault 里新建条目并返回文件路径。已有文件绝不覆盖。"""
    if not title.strip():
        raise VaultError("标题不能为空")
    config = load_config(root)
    created = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC).replace(microsecond=0)
    extra = _type_fields(entry_type, created, platform, conversation_id, url)
    folder = root / dir_for(entry_type, platform)
    folder.mkdir(parents=True, exist_ok=True)
    for _ in range(_MAX_ATTEMPTS):
        if entry_type == EntryType.chat.value:
            entry_id = chat_id(str(platform), str(conversation_id), created)
        else:
            entry_id = new_id(entry_type, created)
        meta: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "id": entry_id,
            "type": entry_type,
            "title": title,
            "created": created,
            "status": Status.inbox.value,
            "tags": [],
            **extra,
        }
        path = folder / entry_filename(title, entry_id, created, config.timezone)
        try:
            create_new(path, render_document(meta, f"# {title}\n"))
        except FileExistsError:
            if entry_type == EntryType.chat.value:
                raise VaultError(f"这个对话已经存在：{path.name}") from None
            continue
        return path
    raise VaultError("无法生成不重复的文件名，请重试")
