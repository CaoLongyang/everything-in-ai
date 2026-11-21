"""eia headlines run / check 的主流程（docs/specs/headline-radar.md 第 4 节）。"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

from eia import llm
from eia.headlines.cluster import cluster_by_vectors, cluster_by_words, embed_text
from eia.headlines.config import (
    EMBED,
    Feed,
    HeadlinesConfig,
    HeadlinesConfigError,
    google_news_feed,
    load_headlines_config,
    search_lang,
)
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

    statuses = fetch_all(all_feeds(root, config), now, fetcher)
    fresh = [item for status in statuses for item in status.items]
    window = dt.timedelta(hours=config.window_hours)
    items = merge_items(load_items(root, readonly=dry_run), fresh, keep_after=now - 2 * window)
    if not dry_run:
        save_items(root, items)
    recent = [item for item in items if item.published >= now - window]

    if config.cluster == EMBED:
        assert client is not None
        vectors = item_vectors(root, client, recent, save=not dry_run)
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


def item_vectors(
    root: Path, client: llm.Client, items: list[Item], *, save: bool
) -> list[list[float]]:
    """报道的向量，缓存在 .eia/headlines/。save=True 时缓存只保留这些报道的向量。"""
    endpoint = client.config.embed
    assert endpoint is not None
    model = endpoint.cache_key
    cache = load_vectors(root, model, readonly=not save)
    missing = [item for item in items if item.url not in cache]
    for start in range(0, len(missing), EMBED_BATCH):
        batch = missing[start : start + EMBED_BATCH]
        for item, vector in zip(batch, client.embed([embed_text(i) for i in batch]), strict=True):
            cache[item.url] = vector
    if save:
        save_vectors(root, model, {item.url: cache[item.url] for item in items})
    return [cache[item.url] for item in items]


def check(
    root: Path, *, now: dt.datetime | None = None, fetcher: Fetcher = download
) -> list[FeedStatus]:
    config = load_headlines_config(root)
    return fetch_all(all_feeds(root, config), now or dt.datetime.now(dt.UTC), fetcher)


def all_feeds(root: Path, config: HeadlinesConfig) -> tuple[Feed, ...]:
    """配置里列的源 + 按兴趣自动生成的搜索（delta-sources 第 5 节）。"""
    feeds = config.feeds + interest_feeds(root, config)
    if not feeds:
        raise HeadlinesConfigError(
            "没有可抓的源：headlines.feeds 是空的，按兴趣搜索也没有兴趣可搜（先确认兴趣清单）"
        )
    return feeds


def interest_feeds(root: Path, config: HeadlinesConfig) -> tuple[Feed, ...]:
    """每条兴趣一个 Google News 搜索：用兴趣的 search 关键词，没有就用兴趣名。"""
    search = config.interest_search
    if search is None:
        return ()
    from eia.interests.config import InterestsError, load_interests

    try:
        interests = load_interests(root)
    except InterestsError as exc:
        raise HeadlinesConfigError(str(exc)) from exc
    feeds: list[Feed] = []
    for interest in interests:
        query = " OR ".join(interest.search or (interest.name,))
        feed = google_news_feed(
            query,
            search_lang(query, search),
            search.country,
            window_hours=config.window_hours,
            label=f"按兴趣搜「{query}」",
        )
        feeds.append(replace(feed, query=query))
    return tuple(feeds)
