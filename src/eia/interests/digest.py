"""`eia digest run`：用确认过的兴趣给最近的报道打分，写成当天的 digest 条目。

规格：SPEC-interest-digest 第 4、9 节。

全部在本机算：报道向量复用新闻雷达的缓存，兴趣和评价过的报道用 .eia/interests/ 的缓存。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eia import llm
from eia.db import database
from eia.headlines.config import HeadlinesConfigError, load_headlines_config
from eia.headlines.feeds import Item
from eia.headlines.run import item_vectors
from eia.headlines.store import load_items
from eia.headlines.urls import url_key
from eia.interests.config import (
    DigestConfig,
    Interest,
    InterestsError,
    load_digest_config,
    load_interests,
)
from eia.interests.feedback import Feedback, read_feedback
from eia.interests.select import balanced
from eia.interests.vectors import embed_client, embed_texts
from eia.schema.ids import new_id
from eia.schema.io import (
    FrontmatterError,
    atomic_write,
    create_new,
    parse_text,
    read_document,
    render_document,
    update_frontmatter,
)
from eia.schema.model import SCHEMA_VERSION, EntryType, Status
from eia.schema.naming import entry_filename
from eia.schema.vault import dir_for, load_config

SOURCE = "eia-interests"
SHOWN_DAYS = 7
NEAR = 0.85  # 和评价过的报道这么像，就当作同一类
LIKE_BONUS = 0.05
DEFAULT_WINDOW_HOURS = 36.0
START = "<!-- eia:digest:start 这一段由 eia digest 生成，重新运行时会被替换 -->"
END = "<!-- eia:digest:end -->"
NOTES_HINT = "（这条线以下是你自己的笔记，重新运行不会动）"
_START_RE = re.compile(r"^<!-- eia:digest:start\b.*-->[ \t]*\r?$", re.MULTILINE)
_END_RE = re.compile(r"^<!-- eia:digest:end -->[ \t]*\r?$", re.MULTILINE)


@dataclass(frozen=True)
class Pick:
    item: Item
    interest: str
    score: float


@dataclass
class DigestResult:
    candidates: int
    picks: list[Pick] = field(default_factory=list)
    dropped_by_feedback: int = 0
    dropped_by_exclude: int = 0
    path: str | None = None
    created: bool = False


def run_digest(
    root: Path,
    *,
    now: dt.datetime | None = None,
    client: llm.Client | None = None,
    dry_run: bool = False,
) -> DigestResult:
    interests = load_interests(root)
    if not interests:
        raise InterestsError(
            "还没有兴趣清单。先运行 eia interests draft 生成草稿，确认后再运行这个命令"
        )
    config = load_digest_config(root)
    tz = load_config(root).timezone
    now = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    client = client or embed_client(root)

    try:
        window = load_headlines_config(root).window_hours
    except HeadlinesConfigError:
        window = DEFAULT_WINDOW_HOURS
    shown = _load_shown(root, now, readonly=dry_run)
    today = now.astimezone(tz).date()
    recent = [
        item
        for item in load_items(root, readonly=dry_run)
        if item.published >= now - dt.timedelta(hours=window)
        and shown.get(url_key(item.url), today.isoformat()) == today.isoformat()
    ]
    result = DigestResult(candidates=len(recent))
    if not recent:
        raise InterestsError("最近没有报道可以挑。先运行 eia headlines run 抓取新闻")

    picks, dropped_feedback, dropped_exclude = score_items(
        root,
        client,
        recent,
        interests,
        read_feedback(root, readonly=dry_run),
        config,
        save=not dry_run,
    )
    result.dropped_by_feedback = dropped_feedback
    result.dropped_by_exclude = dropped_exclude
    chosen = balanced(
        [(p.item, p.interest, p.score) for p in picks],
        per_group=config.per_interest,
        max_items=config.max_items,
    )
    result.picks = [Pick(item, interest, score) for item, interest, score in chosen]
    if not dry_run:
        path, created = _write(root, result.picks, interests, today, now, tz)
        result.path, result.created = path, created
        for pick in result.picks:
            shown[url_key(pick.item.url)] = today.isoformat()
        _save_shown(root, shown, now)
    return result


def score_items(
    root: Path,
    client: llm.Client,
    items: list[Item],
    interests: list[Interest],
    feedback: list[Feedback],
    config: DigestConfig,
    *,
    save: bool = True,
) -> tuple[list[Pick], int, int]:
    import numpy as np

    def unit(rows: list[list[float]]) -> Any:
        matrix = np.asarray(rows, dtype=float)
        if matrix.size == 0:
            return np.zeros((0, 1))
        return matrix / np.clip(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12, None)

    item_vecs = unit(item_vectors(root, client, items, save=False))
    interest_vecs = unit(embed_texts(root, client, [i.text for i in interests], save=save))
    excludes = [(n, i.exclude) for n, i in enumerate(interests) if i.exclude]
    exclude_vecs = unit(embed_texts(root, client, [text for _, text in excludes], save=save))
    liked = [f for f in feedback if f.liked]
    disliked = [f for f in feedback if f.disliked]
    liked_vecs = unit(embed_texts(root, client, [f.title for f in liked], save=save))
    disliked_vecs = unit(embed_texts(root, client, [f.title for f in disliked], save=save))
    disliked_keys = {url_key(f.url) for f in disliked}
    liked_keys = {url_key(f.url) for f in liked}

    sims = item_vecs @ interest_vecs.T
    picks: list[Pick] = []
    dropped_feedback = dropped_exclude = 0
    for row, item in enumerate(items):
        best = int(np.argmax(sims[row]))
        score = float(sims[row, best])
        key = url_key(item.url)
        if key in disliked_keys or (
            len(disliked) and float(np.max(disliked_vecs @ item_vecs[row])) >= NEAR
        ):
            dropped_feedback += 1
            continue
        if key in liked_keys or (len(liked) and float(np.max(liked_vecs @ item_vecs[row])) >= NEAR):
            score += LIKE_BONUS
        if score < config.min_score:
            continue
        for position, (interest_index, _) in enumerate(excludes):
            if interest_index == best and float(exclude_vecs[position] @ item_vecs[row]) > score:
                break
        else:
            picks.append(Pick(item, interests[best].name, round(score, 3)))
            continue
        dropped_exclude += 1
    return picks, dropped_feedback, dropped_exclude


def render_block(picks: list[Pick], interests: list[Interest], tz: dt.tzinfo | None) -> str:
    lines = [START, ""]
    if not picks:
        lines += ["今天没有和你的兴趣足够相关的报道。", ""]
    for interest in interests:
        group = [p for p in picks if p.interest == interest.name]
        if not group:
            continue
        lines += [f"### {interest.name}", ""]
        for pick in group:
            when = pick.item.published.astimezone(tz).strftime("%m-%d %H:%M")
            lines.append(
                f"- **{pick.item.publisher}** · {when} · [{pick.item.title}]({pick.item.url})"
                f" · 相关度 {pick.score:.2f}"
            )
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


def find_digest(root: Path, day: dt.date) -> Path | None:
    folder = root / dir_for(EntryType.digest.value)
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.md")):
        try:
            meta = read_document(path).meta
        except FrontmatterError:
            continue
        if meta.get("source") == SOURCE and str(meta.get("date")) == day.isoformat():
            return path
    return None


def _write(
    root: Path,
    picks: list[Pick],
    interests: list[Interest],
    day: dt.date,
    now: dt.datetime,
    tz: dt.tzinfo | None,
) -> tuple[str, bool]:
    block = render_block(picks, interests, tz)
    stamp = now.replace(microsecond=0)
    top = max((p.score for p in picks), default=0.0)
    existing = find_digest(root, day)
    if existing is not None:
        raw = existing.read_bytes().decode("utf-8")
        text = update_frontmatter(raw, {"updated": stamp, "score": round(top, 3)})
        body = parse_text(text, existing).body
        head = text[: len(text) - len(body)]
        starts, ends = list(_START_RE.finditer(body)), list(_END_RE.finditer(body))
        if len(starts) != 1 or len(ends) != 1 or starts[0].start() > ends[0].start():
            raise InterestsError(f"{existing.name} 里的 digest 标记缺失或重复，没有改动它")
        new_body = body[: starts[0].start()] + block + body[ends[0].end() :]
        atomic_write(existing, head + new_body)
        return existing.relative_to(root).as_posix(), False
    entry_id = new_id(EntryType.digest.value, stamp)
    title = f"今日兴趣 {day.isoformat()}"
    meta: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "id": entry_id,
        "type": EntryType.digest.value,
        "title": title,
        "created": stamp,
        "updated": stamp,
        "status": Status.inbox.value,
        "tags": [],
        "source": SOURCE,
        "date": day,
        "score": round(top, 3),
    }
    folder = root / dir_for(EntryType.digest.value)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / entry_filename(title, entry_id, stamp, tz)
    create_new(target, render_document(meta, f"{block}\n\n{NOTES_HINT}\n"))
    return target.relative_to(root).as_posix(), True


def _load_shown(root: Path, now: dt.datetime, *, readonly: bool = False) -> dict[str, str]:
    oldest = (now - dt.timedelta(days=SHOWN_DAYS)).date().isoformat()
    with database(root, readonly=readonly) as conn:
        rows = conn.execute("SELECT key, day FROM shown WHERE day >= ?", (oldest,)).fetchall()
    return {row["key"]: row["day"] for row in rows}


def _save_shown(root: Path, shown: dict[str, str], now: dt.datetime) -> None:
    oldest = (now - dt.timedelta(days=SHOWN_DAYS)).date().isoformat()
    with database(root) as conn:
        conn.execute("DELETE FROM shown WHERE day < ?", (oldest,))
        conn.executemany("INSERT OR REPLACE INTO shown VALUES (?, ?)", list(shown.items()))
