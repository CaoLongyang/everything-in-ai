"""把达到门槛的组写成 headline 条目（SPEC-headline-radar 第 4 节第 5 步、第 5 节）。

已有条目按来源链接的重合找回（不依赖文件名，用户可以改名）。更新时只改 sources、
independent_count、updated 三个字段和两条标记之间的列表；用户的 tags、自加字段、笔记不动。
sources 只增不减：报道滑出时间窗口后仍然留在文件里。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eia.headlines.feeds import Item
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
from eia.schema.vault import dir_for

START = "<!-- eia:headline:start 这一段由 eia headlines 生成，重新运行时会被替换 -->"
END = "<!-- eia:headline:end -->"
_START_RE = re.compile(r"^<!-- eia:headline:start\b.*-->[ \t]*\r?$", re.MULTILINE)
_END_RE = re.compile(r"^<!-- eia:headline:end -->[ \t]*\r?$", re.MULTILINE)
_CJK = re.compile(r"[㐀-鿿]")


@dataclass
class WriteResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)


def publishers(items: Sequence[Item]) -> set[str]:
    return {item.publisher for item in items}


def write_groups(
    root: Path,
    groups: Sequence[Sequence[Item]],
    *,
    now: dt.datetime,
    tz: dt.tzinfo | None,
    dry_run: bool,
) -> WriteResult:
    result = WriteResult()
    by_url, documents = _index(root, result)
    claimed: set[Path] = set()
    # 大的组先认领已有文件，避免两个组抢同一个文件
    for group in sorted(groups, key=len, reverse=True):
        counts: dict[Path, int] = {}
        for item in group:
            path = by_url.get(item.url)
            if path is not None and path not in claimed:
                counts[path] = counts.get(path, 0) + 1
        if counts:
            path = max(counts, key=lambda p: (counts[p], p.as_posix()))
            claimed.add(path)
            _update(root, path, documents[path], group, now, tz, dry_run, result)
        else:
            _create(root, group, now, tz, dry_run, result)
    return result


def _index(root: Path, result: WriteResult) -> tuple[dict[str, Path], dict[Path, str]]:
    folder = root / dir_for(EntryType.headline.value)
    by_url: dict[str, Path] = {}
    documents: dict[Path, str] = {}
    if not folder.is_dir():
        return by_url, documents
    for path in sorted(folder.rglob("*.md")):
        try:
            document = read_document(path)
        except FrontmatterError as exc:
            result.skipped.append((path.relative_to(root).as_posix(), str(exc)))
            continue
        sources = document.meta.get("sources")
        if document.meta.get("type") != EntryType.headline.value or not isinstance(sources, list):
            continue
        documents[path] = document.raw
        for source in sources:
            if isinstance(source, dict) and isinstance(source.get("url"), str):
                by_url.setdefault(source["url"], path)
    return by_url, documents


def _source(item: Item) -> dict[str, Any]:
    return {
        "publisher": item.publisher,
        "url": item.url,
        "title": item.title,
        "published": item.published.astimezone(dt.UTC).replace(microsecond=0),
    }


def _sorted_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(source: dict[str, Any]) -> tuple[str, str]:
        published = source.get("published")
        when = published.isoformat() if isinstance(published, dt.datetime) else "9999"
        return when, str(source.get("url"))

    return sorted(sources, key=key)


def _count(sources: list[dict[str, Any]]) -> int:
    return len({str(source.get("publisher")) for source in sources})


def render_block(sources: list[dict[str, Any]], tz: dt.tzinfo | None) -> str:
    lines = [START, ""]
    for source in sources:
        published = source.get("published")
        when = (
            published.astimezone(tz).strftime("%m-%d %H:%M")
            if isinstance(published, dt.datetime)
            else "时间未知"
        )
        title = str(source.get("title") or source.get("url"))
        lines.append(f"- **{source.get('publisher')}** · {when} · [{title}]({source.get('url')})")
    lines += ["", END]
    return "\n".join(lines)


def _create(
    root: Path,
    group: Sequence[Item],
    now: dt.datetime,
    tz: dt.tzinfo | None,
    dry_run: bool,
    result: WriteResult,
) -> None:
    sources = _sorted_sources([_source(item) for item in group])
    first = min(group, key=lambda item: (item.published, item.url))
    created = first.published.astimezone(dt.UTC).replace(microsecond=0)
    entry_id = new_id(EntryType.headline.value, created)
    meta: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "id": entry_id,
        "type": EntryType.headline.value,
        "title": first.title,
        "created": created,
        "updated": now.astimezone(dt.UTC).replace(microsecond=0),
        "status": Status.inbox.value,
        "tags": [],
        "lang": "zh" if _CJK.search(first.title) else "en",
        "sources": sources,
        "independent_count": _count(sources),
        "cluster_id": "c-" + hashlib.sha256(first.url.encode()).hexdigest()[:12],
    }
    folder = root / dir_for(EntryType.headline.value)
    target = folder / entry_filename(first.title, entry_id, created, tz)
    rel = target.relative_to(root).as_posix()
    if not dry_run:
        folder.mkdir(parents=True, exist_ok=True)
        try:
            create_new(target, render_document(meta, render_block(sources, tz) + "\n"))
        except FileExistsError:
            result.skipped.append((rel, "同名文件已存在"))
            return
    result.created.append(rel)


def _update(
    root: Path,
    path: Path,
    raw: str,
    group: Sequence[Item],
    now: dt.datetime,
    tz: dt.tzinfo | None,
    dry_run: bool,
    result: WriteResult,
) -> None:
    rel = path.relative_to(root).as_posix()
    old = [s for s in parse_text(raw, path).meta.get("sources", []) if isinstance(s, dict)]
    known = {source.get("url") for source in old}
    added = [_source(item) for item in group if item.url not in known]
    if not added:
        result.unchanged += 1
        return
    sources = _sorted_sources(old + added)
    try:
        text = update_frontmatter(
            raw,
            {
                "sources": sources,
                "independent_count": _count(sources),
                "updated": now.astimezone(dt.UTC).replace(microsecond=0),
            },
        )
        text = _replace_block(text, sources, path, tz)
    except (FrontmatterError, ValueError) as exc:
        result.skipped.append((rel, f"{exc}，文件未改动"))
        return
    if not dry_run:
        atomic_write(path, text)
    result.updated.append(rel)


def _replace_block(
    text: str, sources: list[dict[str, Any]], path: Path, tz: dt.tzinfo | None
) -> str:
    """只替换两条标记（含标记本身）之间的内容；标记之外逐字节不变。"""
    body = parse_text(text, path).body
    head = text[: len(text) - len(body)]
    starts, ends = list(_START_RE.finditer(body)), list(_END_RE.finditer(body))
    if len(starts) != 1 or len(ends) != 1 or starts[0].start() > ends[0].start():
        raise ValueError("headline 标记缺失、重复或顺序不对")
    block = render_block(sources, tz)
    if starts[0].group(0).endswith("\r"):
        block = block.replace("\n", "\r\n")
    end_pos = ends[0].end() - 1 if ends[0].group(0).endswith("\r") else ends[0].end()
    return head + body[: starts[0].start()] + block + body[end_pos:]
