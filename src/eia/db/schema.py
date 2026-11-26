"""表结构和版本迁移（docs/specs/storage.md 第 3 节）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(".eia") / "eia.db"
SCHEMA_VERSION = 2


class DatabaseError(Exception):
    """数据库打不开、版本不对，或者内容损坏。"""


SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS entries (
    path TEXT PRIMARY KEY,          -- 相对 vault 的路径，/ 分隔
    id TEXT, type TEXT, title TEXT, created TEXT, updated TEXT,
    status TEXT, platform TEXT, source TEXT, message_count INTEGER,
    mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL,
    error TEXT                      -- frontmatter 读不了时的原因
);
CREATE INDEX IF NOT EXISTS entries_type ON entries(type);
CREATE INDEX IF NOT EXISTS entries_updated ON entries(updated);

CREATE TABLE IF NOT EXISTS entry_tags (
    path TEXT NOT NULL REFERENCES entries(path) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (path, tag)
);

CREATE TABLE IF NOT EXISTS items (  -- 新闻雷达抓到的报道
    key TEXT PRIMARY KEY,           -- 规范化后的链接
    url TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
    publisher TEXT NOT NULL, published TEXT NOT NULL, feed_url TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_published ON items(published);

CREATE TABLE IF NOT EXISTS vectors (
    model TEXT NOT NULL,            -- Endpoint.cache_key：服务、地址、模型
    space TEXT NOT NULL,            -- item（报道）/ text（对话、兴趣、评价过的标题）
    key TEXT NOT NULL,
    dim INTEGER NOT NULL,
    data BLOB NOT NULL,             -- float32
    PRIMARY KEY (model, space, key)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT NOT NULL, started TEXT NOT NULL, seconds REAL NOT NULL,
    ok INTEGER NOT NULL, summary TEXT NOT NULL, error TEXT
);
CREATE INDEX IF NOT EXISTS runs_job ON runs(job, id);

CREATE TABLE IF NOT EXISTS shown (key TEXT PRIMARY KEY, day TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS feedback (
    key TEXT PRIMARY KEY,
    url TEXT NOT NULL, title TEXT NOT NULL, publisher TEXT NOT NULL,
    interest TEXT, relevant INTEGER, stars INTEGER, why TEXT, rated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interest_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    saved TEXT NOT NULL, source TEXT NOT NULL,
    interests TEXT NOT NULL         -- JSON：当时完整的兴趣清单
);
"""

FTS = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts"
    " USING fts5(path UNINDEXED, title, body, tokenize='trigram')"
)

# 以后加字段：在这里加 3: "ALTER TABLE ..."，并把 SCHEMA_VERSION 改成 3
MIGRATIONS: dict[int, str] = {
    # 个人画像第 2 步的观察（docs/specs/profile.md 第 3 节）。key 由对话材料、提示词版本、模型决定
    2: """
CREATE TABLE IF NOT EXISTS profile_observations (
    key TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    saved TEXT NOT NULL,
    data TEXT NOT NULL              -- JSON：这个对话的观察
);
""",
}


def ensure_schema(conn: sqlite3.Connection, *, writable: bool) -> None:
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone()
    version = 0
    if has_meta:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        version = int(row[0]) if row else 0
    if version > SCHEMA_VERSION:
        raise DatabaseError(
            f"数据库是新版本 eia 建的（表结构版本 {version}，这个程序只认识到 {SCHEMA_VERSION}）。"
            "请升级 eia；数据库没有被改动"
        )
    if version == SCHEMA_VERSION:
        return
    if not writable:
        raise DatabaseError("数据库需要升级，但现在是只读打开。去掉 --dry-run 运行一次即可")
    if version == 0:
        conn.executescript(SCHEMA_V1)
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('fts', ?)", ("1" if _try_fts(conn) else "0",)
        )
    for target in range(max(version, 1) + 1, SCHEMA_VERSION + 1):
        conn.executescript(MIGRATIONS[target])
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.commit()


def _try_fts(conn: sqlite3.Connection) -> bool:
    """SQLite 低于 3.34 没有 trigram 分词：全文搜索不可用，其他功能照常。"""
    try:
        conn.execute(FTS)
    except sqlite3.OperationalError:
        return False
    return True


def has_fts(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key = 'fts'").fetchone()
    return bool(row) and row[0] == "1"
