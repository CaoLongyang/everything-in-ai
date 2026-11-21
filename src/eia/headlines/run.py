"""eia headlines run / check 的主流程（SPEC-headline-radar 第 4 节）。"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from eia import llm
from eia.headlines.cluster import cluster_by_vectors, cluster_by_words, embed_text
from eia.headlines.config import EMBED, Feed, HeadlinesConfigError, load_headlines_config
from eia.headlines.feeds import FeedError, Item, download, fetch_feed
from eia.headlines.store import load_items, load_vectors, merge_items, save_items, save_vectors
from eia.headlines.writer import WriteResult, publishers, write_groups
from eia.schema.vault import load_config

Fetcher = Callable[[str], bytes]
EMBED_BATCH = 64
_WORKERS = 8


@dataclass
class FeedStatus:
    feed: Feed
    items: list[Item] = field(default_factory=list)
    error: str | None = None


@dataclass
class RunResult:
    feeds: list[FeedStatus]
    in_window: int
    groups: int
    qualifying: int
    written: WriteResult


def fetch_all(feeds: tuple[Feed, ...], now: dt.datetime, fetcher: Fetcher) -> list[FeedStatus]:
    def one(feed: Feed) -> FeedStatus:
        try:
            return FeedStatus(feed, fetch_feed(feed, now, fetcher))
        except FeedError as exc:
            return FeedStatus(feed, error=str(exc))

    with ThreadPoolExecutor(min(_WORKERS, len(feeds))) as pool:
        return list(pool.map(one, feeds))


def run(
    root: Path,
    *,
    dry_run: bool = False,
    now: dt.datetime | None = None,
    fetcher: Fetcher = download,
    client: llm.Client | None = None,
) -> RunResult:
    config = load_headlines_config(root)
    tz = load_config(root).timezone
    if config.cluster == EMBED and client is None:
        client = _embed_client(root)
    now = now or dt.datetime.now(dt.UTC)

    statuses = fetch_all(config.feeds, now, fetcher)
    fresh = [item for status in statuses for item in status.items]
    window = dt.timedelta(hours=config.window_hours)
    items = merge_items(load_items(root), fresh, keep_after=now - 2 * window)
    if not dry_run:
        save_items(root, items)
    recent = [item for item in items if item.published >= now - window]

    if config.cluster == EMBED:
        assert client is not None
        vectors = _vectors(root, client, recent, dry_run)
        groups = cluster_by_vectors(recent, vectors, config.similarity)
    else:
        groups = cluster_by_words(recent, config.similarity)
    big = [group for group in groups if len(publishers(group)) >= config.min_sources]
    written = write_groups(root, big, now=now, tz=tz, dry_run=dry_run)
    return RunResult(statuses, len(recent), len(groups), len(big), written)


def _embed_client(root: Path) -> llm.Client:
    reason = (
        "headlines.cluster 是 embed，需要在 llm.embed 配置向量模型（例如本地 Ollama 的 bge-m3）"
    )
    try:
        client = llm.get_client(root)
    except llm.LLMConfigError as exc:
        raise HeadlinesConfigError(f"{reason}：{exc}") from exc
    if client.config.embed is None:
        raise HeadlinesConfigError(reason)
    return client


def _vectors(root: Path, client: llm.Client, items: list[Item], dry_run: bool) -> list[list[float]]:
    endpoint = client.config.embed
    assert endpoint is not None
    model = f"{endpoint.provider}:{endpoint.api_base}:{endpoint.model}"
    cache = load_vectors(root, model)
    missing = [item for item in items if item.url not in cache]
    for start in range(0, len(missing), EMBED_BATCH):
        batch = missing[start : start + EMBED_BATCH]
        for item, vector in zip(batch, client.embed([embed_text(i) for i in batch]), strict=True):
            cache[item.url] = vector
    if not dry_run:
        save_vectors(root, model, {item.url: cache[item.url] for item in items})
    return [cache[item.url] for item in items]


def check(
    root: Path, *, now: dt.datetime | None = None, fetcher: Fetcher = download
) -> list[FeedStatus]:
    config = load_headlines_config(root)
    return fetch_all(config.feeds, now or dt.datetime.now(dt.UTC), fetcher)
