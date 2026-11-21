"""兴趣清单的历史快照（SPEC-storage 第 3 节）。

每次确认或保存兴趣清单时存一份，给 V2 的「兴趣随时间的变化」。

不导出成文本（作者 2026-11-21 决定）：当前的清单本来就在 config.yaml 里。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from eia.db import database


def record(root: Path, interests: Sequence[dict[str, Any]], source: str) -> None:
    saved = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with database(root) as conn:
        conn.execute(
            "INSERT INTO interest_history (saved, source, interests) VALUES (?, ?, ?)",
            (saved, source, json.dumps(list(interests), ensure_ascii=False)),
        )


def snapshots(root: Path) -> list[tuple[str, str, list[dict[str, Any]]]]:
    with database(root) as conn:
        rows = conn.execute(
            "SELECT saved, source, interests FROM interest_history ORDER BY id"
        ).fetchall()
    return [(r["saved"], r["source"], json.loads(r["interests"])) for r in rows]
