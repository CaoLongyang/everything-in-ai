"""vault 的 SQLite 数据库 `.eia/eia.db`（SPEC-storage.md）。

内容的唯一来源仍是 Markdown；这里放索引、向量、抓到的报道、运行记录、评价等机器用的数据。
数据库随时可以删掉，用 `eia db rebuild` 重建（只损失缓存）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from eia.db.schema import DB_PATH, DatabaseError, ensure_schema

__all__ = ["DB_PATH", "DatabaseError", "connect", "database"]


def connect(root: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """打开 vault 的数据库。

    readonly=True 用于预览（--dry-run）：数据库存在就只读打开；不存在就在内存里建一个空库，
    并把旧版本留下的数据文件读进来（不移动它们），保证预览结果和真跑一致，又不写任何文件。
    """
    from eia.db.legacy import import_legacy

    path = root / DB_PATH
    if readonly and path.is_file():
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)
    elif readonly:
        conn = sqlite3.connect(":memory:")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=5)
    try:
        writable = not (readonly and path.is_file())
        _configure(conn, writable=writable)
        ensure_schema(conn, writable=writable)
        if writable:
            # 只读打开现有库时不导入；内存库导入但不移动旧文件（预览不写任何文件）
            import_legacy(conn, root, move=not readonly)
    except BaseException:
        conn.close()  # 打开到一半失败（例如版本太新）也要关掉，不留着连接
        raise
    return conn


def _configure(conn: sqlite3.Connection, *, writable: bool) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")  # 定时任务和网站同时写时等一等，而不是报错
    conn.execute("PRAGMA foreign_keys = ON")
    if writable:
        conn.execute("PRAGMA journal_mode = WAL")


@contextmanager
def database(root: Path, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
    """`with database(root) as conn:`，正常结束时提交，出错时回滚，最后关闭。"""
    conn = connect(root, readonly=readonly)
    try:
        yield conn
        if not readonly:
            conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
