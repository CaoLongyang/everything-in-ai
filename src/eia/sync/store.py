"""口令、运行记录、哪些对话要取（docs/specs/chat-sync.md 第 4.2、4.3 节）。

口令存在 `.eia/eia.db` 的 meta 表；`eia db rebuild` 会换一个新口令，要在插件里重新填。
运行记录用 runs 表（job = sync），只记平台、对话编号、消息数和结果，不记标题和内容。
"""

from __future__ import annotations

import datetime as dt
import hmac
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eia.db import database
from eia.schema.io import FrontmatterError, read_document
from eia.schema.validate import iter_markdown
from eia.sync.convert import list_time

JOB = "sync"
KEEP_LOG = 500
_TOKEN_KEY = "sync_token"


@dataclass(frozen=True)
class LogLine:
    started: str
    ok: bool
    summary: str
    error: str | None


def token(root: Path) -> str:
    """插件要填的口令；第一次调用时生成。"""
    with database(root) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (_TOKEN_KEY,)).fetchone()
        if row:
            return str(row[0])
        value = secrets.token_urlsafe(24)
        conn.execute("INSERT INTO meta VALUES (?, ?)", (_TOKEN_KEY, value))
        return value


def check_token(expected: str, given: str | None) -> bool:
    return given is not None and hmac.compare_digest(expected.encode(), given.encode())


def log(root: Path, now: dt.datetime, *, ok: bool, summary: str, error: str | None = None) -> None:
    with database(root) as conn:
        conn.execute(
            "INSERT INTO runs (job, started, seconds, ok, summary, error)"
            " VALUES (?, ?, 0, ?, ?, ?)",
            (JOB, now.astimezone(dt.UTC).isoformat(timespec="seconds"), int(ok), summary, error),
        )
        conn.execute(
            "DELETE FROM runs WHERE job = ? AND id NOT IN"
            " (SELECT id FROM runs WHERE job = ? ORDER BY id DESC LIMIT ?)",
            (JOB, JOB, KEEP_LOG),
        )


def recent(root: Path, limit: int = 30) -> list[LogLine]:
    with database(root, readonly=True) as conn:
        rows = conn.execute(
            "SELECT started, ok, summary, error FROM runs WHERE job = ? ORDER BY id DESC LIMIT ?",
            (JOB, limit),
        ).fetchall()
    return [LogLine(r["started"], bool(r["ok"]), r["summary"], r["error"]) for r in rows]


def known_updates(root: Path, platform: str) -> dict[str, dt.datetime | None]:
    """vault 里这个平台每个对话记录的更新时间（frontmatter 的 updated）。"""
    found: dict[str, dt.datetime | None] = {}
    for path in iter_markdown(root):
        try:
            meta = read_document(path).meta
        except FrontmatterError:
            continue
        conversation = meta.get("conversation_id")
        if meta.get("platform") != platform or not isinstance(conversation, str):
            continue
        found[conversation] = _as_utc(meta.get("updated"))
    return found


def plan(
    known: dict[str, dt.datetime | None], items: Iterable[dict[str, Any]]
) -> tuple[list[str], int]:
    """对话列表里哪些要取：vault 里没有的，和更新时间比 vault 新的。

    返回 (要取的编号, 列表里认不出的条数)。

    第一次同步也一样：vault 里已经从官方导出包导入过、没有变的，不用再取。
    """
    need: list[str] = []
    bad = 0
    for item in items:
        conversation = item.get("id") if isinstance(item, dict) else None
        if not isinstance(conversation, str) or not conversation:
            bad += 1
            continue
        updated = list_time(item.get("updated"))
        mine = known.get(conversation)
        if conversation not in known or mine is None or updated is None or updated > mine:
            need.append(conversation)
    return list(dict.fromkeys(need)), bad


def summary(platform: str, conversation: str, messages: int, outcome: str) -> str:
    return f"{platform} {conversation} {messages} 条消息：{outcome}"


def _as_utc(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return (value if value.tzinfo else value.replace(tzinfo=dt.UTC)).astimezone(dt.UTC)
    if isinstance(value, str):
        return list_time(value)
    return None
