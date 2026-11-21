"""对报道的评价（docs/specs/interest-digest.md 第 9 节、docs/specs/storage.md F14-03）。

存在数据库的 feedback 表，**每次写入同时导出**到 `interests/feedback.jsonl`：
这是用户自己的数据，数据库删了要能从导出文件完整恢复（`eia db rebuild`）。
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from eia.db import database
from eia.db.legacy import FEEDBACK_EXPORT
from eia.headlines.urls import url_key
from eia.interests.config import InterestsError
from eia.schema.io import atomic_write

FEEDBACK = FEEDBACK_EXPORT


@dataclass(frozen=True)
class Feedback:
    url: str
    title: str
    publisher: str
    interest: str | None
    relevant: bool | None
    stars: int | None
    why: str | None
    rated: str

    @property
    def liked(self) -> bool:
        return self.relevant is True or (self.stars is not None and self.stars >= 4)

    @property
    def disliked(self) -> bool:
        return self.relevant is False or (self.stars is not None and self.stars <= 2)


def _rows(conn: sqlite3.Connection) -> list[Feedback]:
    rows = conn.execute(
        "SELECT url, title, publisher, interest, relevant, stars, why, rated FROM feedback"
        " ORDER BY rated, key"
    ).fetchall()
    return [
        Feedback(
            url=r["url"],
            title=r["title"],
            publisher=r["publisher"],
            interest=r["interest"],
            relevant=None if r["relevant"] is None else bool(r["relevant"]),
            stars=r["stars"],
            why=r["why"],
            rated=r["rated"],
        )
        for r in rows
    ]


def read_feedback(root: Path, *, readonly: bool = False) -> list[Feedback]:
    with database(root, readonly=readonly) as conn:
        return _rows(conn)


def export(root: Path, conn: sqlite3.Connection) -> None:
    lines = [json.dumps(asdict(r), ensure_ascii=False) for r in _rows(conn)]
    path = root / FEEDBACK
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, "".join(line + "\n" for line in lines))


def rate(
    root: Path,
    url: str,
    *,
    title: str = "",
    publisher: str = "",
    interest: str | None = None,
    relevant: bool | None = None,
    stars: int | None = None,
    why: str | None = None,
    now: dt.datetime | None = None,
) -> Feedback:
    """记一条评价。同一篇（按规范化链接）再评覆盖旧的。三种方式至少给一种。"""
    why = (why or "").strip() or None
    if relevant is None and stars is None and why is None:
        raise InterestsError("至少要选「相关 / 不相关」、打几颗星或写一句理由中的一种")
    if stars is not None and not 1 <= stars <= 5:
        raise InterestsError("星数是 1 到 5")
    when = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC).replace(microsecond=0)
    record = Feedback(
        url=url.strip(),
        title=title.strip() or url.strip(),
        publisher=publisher.strip(),
        interest=interest,
        relevant=relevant,
        stars=stars,
        why=why,
        rated=when.isoformat().replace("+00:00", "Z"),
    )
    with database(root) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                url_key(record.url),
                record.url,
                record.title,
                record.publisher,
                record.interest,
                None if relevant is None else int(relevant),
                stars,
                why,
                record.rated,
            ),
        )
        export(root, conn)
    return record
