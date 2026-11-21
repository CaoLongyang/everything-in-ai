"""对报道的评价：`interests/feedback.jsonl`（SPEC-interest-digest 第 9 节）。

放在 vault 里而不是 .eia/ 缓存：这是用户自己的数据，删缓存不能丢。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from eia.headlines.urls import url_key
from eia.interests.config import InterestsError
from eia.schema.io import atomic_write

FEEDBACK = Path("interests") / "feedback.jsonl"


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


def read_feedback(root: Path) -> list[Feedback]:
    path = root / FEEDBACK
    if not path.is_file():
        return []
    records: list[Feedback] = []
    for line in path.read_bytes().decode("utf-8", errors="replace").splitlines():
        try:
            records.append(Feedback(**json.loads(line)))
        except (ValueError, TypeError):
            continue
    return records


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
    key = url_key(record.url)
    kept = [r for r in read_feedback(root) if url_key(r.url) != key]
    path = root / FEEDBACK
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(asdict(r), ensure_ascii=False) for r in [*kept, record]]
    atomic_write(path, "".join(line + "\n" for line in lines))
    return record
