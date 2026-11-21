"""Markdown 条目的索引和全文搜索（docs/specs/storage.md 第 4 节、F14-02、F14-08）。

索引从 Markdown 生成，Markdown 才是内容的来源：增量更新只重新读修改时间或大小变了的文件。
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eia.db.schema import has_fts
from eia.schema.io import FrontmatterError, read_document
from eia.schema.validate import iter_markdown

FTS_MIN_CHARS = 3  # trigram 分词：少于 3 个字用不上索引
_FIELDS = ("id", "type", "title", "created", "updated", "status", "platform", "source")


@dataclass
class SyncResult:
    added: int = 0
    changed: int = 0
    removed: int = 0
    unchanged: int = 0


@dataclass(frozen=True)
class Hit:
    path: str
    title: str
    type: str | None
    snippet: str


def sync(conn: sqlite3.Connection, root: Path) -> SyncResult:
    result = SyncResult()
    known = {
        row["path"]: (row["mtime_ns"], row["size"])
        for row in conn.execute("SELECT path, mtime_ns, size FROM entries")
    }
    fts = has_fts(conn)
    seen: set[str] = set()
    for path in iter_markdown(root):
        rel = path.relative_to(root).as_posix()
        seen.add(rel)
        stat = os.stat(path)
        if known.get(rel) == (stat.st_mtime_ns, stat.st_size):
            result.unchanged += 1
            continue
        _index_file(conn, root, path, rel, stat, fts)
        if rel in known:
            result.changed += 1
        else:
            result.added += 1
    for rel in set(known) - seen:
        _remove(conn, rel, fts)
        result.removed += 1
    conn.commit()
    return result


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    return str(value)


def _index_file(
    conn: sqlite3.Connection, root: Path, path: Path, rel: str, stat: os.stat_result, fts: bool
) -> None:
    error = None
    meta: dict[str, Any] = {}
    body = ""
    try:
        document = read_document(path)
        meta, body = document.meta, document.body
    except FrontmatterError as exc:
        error = str(exc)
        body = path.read_bytes().decode("utf-8", errors="replace")
    _remove(conn, rel, fts)
    conn.execute(
        "INSERT INTO entries (path, id, type, title, created, updated, status, platform, source,"
        " message_count, mtime_ns, size, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            rel,
            *(_text(meta.get(f)) for f in _FIELDS),
            count if isinstance(count := meta.get("message_count"), int) else None,
            stat.st_mtime_ns,
            stat.st_size,
            error,
        ),
    )
    tags = meta.get("tags")
    if isinstance(tags, list):
        conn.executemany(
            "INSERT OR IGNORE INTO entry_tags VALUES (?, ?)",
            [(rel, str(t)) for t in tags if t is not None],
        )
    if fts:
        title = _text(meta.get("title")) or path.stem
        conn.execute(
            "INSERT INTO entries_fts (path, title, body) VALUES (?, ?, ?)", (rel, title, body)
        )


def _remove(conn: sqlite3.Connection, rel: str, fts: bool) -> None:
    conn.execute("DELETE FROM entries WHERE path = ?", (rel,))
    if fts:
        conn.execute("DELETE FROM entries_fts WHERE path = ?", (rel,))


def search(
    conn: sqlite3.Connection, query: str, *, entry_type: str | None = None, limit: int = 50
) -> list[Hit]:
    """3 个字及以上走全文索引；更短的逐条比对。"""
    query = " ".join(query.split())
    if not query:
        return []
    if not has_fts(conn):
        raise RuntimeError("这台电脑上的 SQLite 不支持全文搜索（需要 3.34 以上），搜索不可用")
    type_sql = " AND e.type = ?" if entry_type else ""
    type_args = (entry_type,) if entry_type else ()
    if len(query) >= FTS_MIN_CHARS:
        phrase = '"' + query.replace('"', '""') + '"'
        rows = conn.execute(
            "SELECT f.path, f.title, e.type, snippet(entries_fts, 2, '【', '】', '…', 16) AS snip"
            " FROM entries_fts f JOIN entries e ON e.path = f.path"
            f" WHERE entries_fts MATCH ?{type_sql} ORDER BY rank LIMIT ?",
            (phrase, *type_args, limit),
        ).fetchall()
        return [Hit(r["path"], r["title"], r["type"], r["snip"]) for r in rows]
    like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = conn.execute(
        "SELECT f.path, f.title, e.type, f.body"
        " FROM entries_fts f JOIN entries e ON e.path = f.path"
        f" WHERE (f.title LIKE ? ESCAPE '\\' OR f.body LIKE ? ESCAPE '\\'){type_sql}"
        " ORDER BY e.updated DESC LIMIT ?",
        (like, like, *type_args, limit),
    ).fetchall()
    return [Hit(r["path"], r["title"], r["type"], _snippet(r["body"], query)) for r in rows]


def _snippet(body: str, query: str, width: int = 30) -> str:
    match = re.search(re.escape(query), body, re.IGNORECASE)
    if match is None:
        return body[: width * 2]
    start, end = max(0, match.start() - width), min(len(body), match.end() + width)
    text = body[start : match.start()] + "【" + match.group(0) + "】" + body[match.end() : end]
    return ("…" if start else "") + " ".join(text.split()) + ("…" if end < len(body) else "")
