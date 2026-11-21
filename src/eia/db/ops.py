"""`eia db status / rebuild`（SPEC-storage 第 6 节）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from eia.db import database
from eia.db.index import SyncResult, sync
from eia.db.legacy import import_feedback
from eia.db.schema import DB_PATH, SCHEMA_VERSION, has_fts

TABLES = (
    "entries",
    "entry_tags",
    "items",
    "vectors",
    "runs",
    "shown",
    "feedback",
    "interest_history",
)


@dataclass
class Status:
    size_bytes: int
    schema_version: int
    fts: bool
    counts: dict[str, int] = field(default_factory=dict)
    index: SyncResult | None = None
    legacy: dict[str, object] | None = None


def status(root: Path) -> Status:
    with database(root) as conn:
        index = sync(conn, root)
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
        legacy_row = conn.execute("SELECT value FROM meta WHERE key = 'legacy_import'").fetchone()
        fts = has_fts(conn)
    files = [root / DB_PATH, *(root / DB_PATH).parent.glob("eia.db-*")]
    size = sum(p.stat().st_size for p in files if p.is_file())
    legacy = json.loads(legacy_row[0]) if legacy_row else None
    return Status(size, SCHEMA_VERSION, fts, counts, index, legacy)


def rebuild(root: Path) -> Status:
    """删掉数据库重建：重新索引 Markdown，从导出文件恢复评价。报道和向量要重新抓、重新算。"""
    for path in [root / DB_PATH, *(root / DB_PATH).parent.glob("eia.db-*")]:
        path.unlink(missing_ok=True)
    with database(root) as conn:
        conn.execute("DELETE FROM feedback")
        import_feedback(conn, root)
    return status(root)
