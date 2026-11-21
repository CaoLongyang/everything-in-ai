"""把旧版本留下的散文件导入数据库（docs/specs/storage.md 第 5 节、F14-01）。

只做一次：导入后在 meta 里记下 `legacy_import`。旧文件移到 `.eia/old/`（不删）；
`interests/feedback.jsonl` 是评价的导出文件，留在原处。
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from eia.db import vectors as vec

LEGACY = {
    "items": Path(".eia") / "headlines" / "items.jsonl",
    "item_vectors": Path(".eia") / "headlines" / "vectors.json",
    "text_vectors": Path(".eia") / "interests" / "vectors.json",
    "shown": Path(".eia") / "interests" / "shown.json",
    "runs": Path(".eia") / "runs",
}
FEEDBACK_EXPORT = Path("interests") / "feedback.jsonl"
OLD_DIR = Path(".eia") / "old"


def import_legacy(conn: sqlite3.Connection, root: Path, *, move: bool) -> dict[str, int] | None:
    if conn.execute("SELECT 1 FROM meta WHERE key = 'legacy_import'").fetchone():
        return None
    from eia.headlines.urls import url_key

    report = {"items": 0, "vectors": 0, "runs": 0, "shown": 0, "feedback": 0}
    for line in _lines(root / LEGACY["items"]):
        try:
            data = json.loads(line)
            published = dt.datetime.fromisoformat(data["published"]).astimezone(dt.UTC)
            row = (
                url_key(data["url"]),
                data["url"],
                data["title"],
                data.get("summary", ""),
                data["publisher"],
                published.isoformat(),
                data.get("feed_url", ""),
            )
        except (ValueError, KeyError, TypeError):
            continue
        conn.execute("INSERT OR IGNORE INTO items VALUES (?, ?, ?, ?, ?, ?, ?)", row)
        report["items"] += 1
    for name, space in (("item_vectors", "item"), ("text_vectors", "text")):
        data = _json(root / LEGACY[name])
        if isinstance(data, dict) and isinstance(data.get("vectors"), dict) and data.get("model"):
            good = {k: v for k, v in data["vectors"].items() if isinstance(v, list) and v}
            dims = {len(v) for v in good.values()}
            if len(dims) == 1:
                vec.put(conn, str(data["model"]), space, good)
                report["vectors"] += len(good)
    folder = root / LEGACY["runs"]
    for path in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
        for line in _lines(path):
            try:
                r = json.loads(line)
                conn.execute(
                    "INSERT INTO runs (job, started, seconds, ok, summary, error)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        r["job"],
                        r["started"],
                        float(r["seconds"]),
                        int(bool(r["ok"])),
                        r["summary"],
                        r.get("error"),
                    ),
                )
                report["runs"] += 1
            except (ValueError, KeyError, TypeError):
                continue
    shown = _json(root / LEGACY["shown"])
    if isinstance(shown, dict):
        conn.executemany(
            "INSERT OR REPLACE INTO shown VALUES (?, ?)",
            [(str(k), str(v)) for k, v in shown.items()],
        )
        report["shown"] = len(shown)
    report["feedback"] = import_feedback(conn, root)
    conn.execute(
        "INSERT INTO meta VALUES ('legacy_import', ?)",
        (json.dumps({"at": dt.datetime.now(dt.UTC).isoformat(), **report}),),
    )
    conn.commit()
    if move:
        for rel in LEGACY.values():
            source = root / rel
            if source.exists():
                target = root / OLD_DIR / rel.relative_to(".eia")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
    return report


def import_feedback(conn: sqlite3.Connection, root: Path) -> int:
    """从导出文件读回评价（迁移和 rebuild 都用）。已有的同一篇以导出文件为准。"""
    from eia.headlines.urls import url_key

    count = 0
    for line in _lines(root / FEEDBACK_EXPORT):
        try:
            r: dict[str, Any] = json.loads(line)
            stars = r.get("stars")
            relevant = r.get("relevant")
            conn.execute(
                "INSERT OR REPLACE INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    url_key(r["url"]),
                    r["url"],
                    r.get("title") or r["url"],
                    r.get("publisher") or "",
                    r.get("interest"),
                    None if relevant is None else int(bool(relevant)),
                    int(stars) if stars is not None else None,
                    r.get("why"),
                    r["rated"],
                ),
            )
            count += 1
        except (ValueError, KeyError, TypeError):
            continue
    return count


def _lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_bytes().decode("utf-8", errors="replace").splitlines()


def _json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except ValueError:
        return None
