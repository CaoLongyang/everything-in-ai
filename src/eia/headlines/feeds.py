"""下载并解析各种源：RSS / Atom、Google News、Hacker News、热榜。全仓库只有这里 import feedparser。

下载用标准库自己做（feedparser 只负责解析字节），这样超时、大小上限、User-Agent 都由我们控制。
urllib 会自动使用系统代理设置。

Google News 取真实媒体名、Hacker News 按分数过滤的做法参考了 Horizon
（https://github.com/Thysrael/Horizon，MIT License，Copyright (c) 2026 Thysrael）的
`src/scrapers/google_news.py`、`src/scrapers/hackernews.py`，版权声明见 THIRD_PARTY_NOTICES.md。
"""

from __future__ import annotations

import calendar
import datetime as dt
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from eia.headlines.config import GOOGLE_NEWS, HACKERNEWS, HOTLIST, Feed

# 「Mozilla/5.0 (compatible; 名字)」是爬虫自报身份的通行写法（不冒充浏览器）。
# 只写 eia-headlines/1 时 NewsNow 会拒绝（2026-11-21 实测 403）。
USER_AGENT = "Mozilla/5.0 (compatible; eia-headlines/1)"
TIMEOUT_S = 20
MAX_BYTES = 5_000_000
MAX_SUMMARY = 500
MAX_ITEMS = 30  # Google News、热榜、Hacker News 每个源最多取这么多条
_TAG = re.compile(r"<[^>]+>")


class FeedError(Exception):
    """这个源抓不到或者解析不了。只影响这一个源。"""


@dataclass(frozen=True)
class Item:
    url: str
    title: str
    summary: str
    publisher: str
    published: dt.datetime  # UTC；源里没有时间时用第一次抓到的时间
    feed_url: str


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            data: bytes = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise FeedError(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise FeedError(f"连不上：{reason}") from None
    if len(data) > MAX_BYTES:
        raise FeedError(f"内容超过 {MAX_BYTES // 1_000_000} MB，不像是新闻源")
    return data


def fetch_feed(
    feed: Feed, now: dt.datetime, fetcher: Callable[[str], bytes] = download
) -> list[Item]:
    data = fetcher(feed.url)
    if feed.kind == GOOGLE_NEWS:
        return parse_google_news(data, feed, now)
    if feed.kind == HACKERNEWS:
        return parse_hackernews(data, feed, now)
    if feed.kind == HOTLIST:
        return parse_hotlist(data, feed, now)
    return parse_feed(data, feed, now)


def _feedparser(data: bytes) -> Any:
    try:
        import feedparser
    except ImportError as exc:
        raise FeedError(
            '缺少新闻雷达需要的依赖 feedparser。请运行 pip install -e ".[news]"'
        ) from exc
    return feedparser.parse(data)


def parse_feed(data: bytes, feed: Feed, now: dt.datetime) -> list[Item]:
    parsed = _feedparser(data)
    if not parsed.entries:
        problem = parsed.get("bozo_exception")
        detail = f"：{type(problem).__name__}" if problem else ""
        raise FeedError(f"不是能识别的 RSS / Atom，或者里面没有条目{detail}")
    items: list[Item] = []
    for entry in parsed.entries:
        url = str(entry.get("link") or "").strip()
        title = _clean(entry.get("title"))
        if not url or not title:
            continue
        items.append(
            Item(
                url=url,
                title=title,
                summary=_clean(entry.get("summary"))[:MAX_SUMMARY],
                publisher=feed.publisher,
                published=_time(entry) or now,
                feed_url=feed.url,
            )
        )
    return items


def parse_google_news(data: bytes, feed: Feed, now: dt.datetime) -> list[Item]:
    """搜索结果。publisher 取每篇报道的真实媒体（<source>），标题去掉末尾的「 - 媒体名」。

    搜不到东西是正常的（返回空列表），但返回的必须是 RSS。
    """
    parsed = _feedparser(data)
    if not parsed.entries and not parsed.feed.get("title"):
        raise FeedError("Google News 返回的不是 RSS")
    items: list[Item] = []
    for entry in parsed.entries[:MAX_ITEMS]:
        url = str(entry.get("link") or "").strip()
        title = _clean(entry.get("title"))
        source = entry.get("source")
        publisher = _clean(source.get("title") if isinstance(source, dict) else None)
        if not publisher and " - " in title:
            publisher = title.rsplit(" - ", 1)[1]
        if not url or not title or not publisher:
            continue
        while title.endswith(f" - {publisher}"):  # 有的标题带两遍媒体名
            title = title.removesuffix(f" - {publisher}").strip()
        items.append(Item(url, title, "", publisher, _time(entry) or now, feed.url))
    return items


def parse_hackernews(data: bytes, feed: Feed, now: dt.datetime) -> list[Item]:
    """Algolia 的 Hacker News 接口（首页帖子），低于 min_score 分的不要。"""
    hits = _json(data).get("hits")
    if not isinstance(hits, list):
        raise FeedError("Hacker News 返回的格式不认识（没有 hits）")
    items: list[Item] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        title = _clean(hit.get("title"))
        points = hit.get("points")
        if not title or not isinstance(points, int) or points < feed.min_score:
            continue
        story = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        url = str(hit.get("url") or "").strip() or story
        published = _timestamp(hit.get("created_at_i")) or now
        items.append(Item(url, title, f"{points} 分", feed.publisher, published, feed.url))
    return items[:MAX_ITEMS]


def parse_hotlist(data: bytes, feed: Feed, now: dt.datetime) -> list[Item]:
    """热榜：B站热门（公开接口）或 NewsNow（知乎、微博、抖音、头条）。

    热榜没有发布时间，用抓到的时间。
    """
    payload = _json(data)
    rows: list[tuple[str, str, str]] = []  # (链接, 标题, 摘要)
    if "bilibili.com" in feed.url:
        if payload.get("code") != 0:
            raise FeedError(f"B站返回错误：{payload.get('message') or payload.get('code')}")
        for video in (payload.get("data") or {}).get("list") or []:
            bvid = str(video.get("bvid") or "").strip()
            if bvid:
                link = f"https://www.bilibili.com/video/{bvid}"
                rows.append((link, _clean(video.get("title")), _clean(video.get("desc"))))
    else:
        entries = payload.get("items")
        if not isinstance(entries, list):
            raise FeedError("NewsNow 返回的格式不认识（没有 items）")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            extra = entry.get("extra")
            hover = extra.get("hover") if isinstance(extra, dict) else None
            link = _stable_link(str(entry.get("url") or entry.get("mobileUrl") or "").strip())
            rows.append((link, _clean(entry.get("title")), _clean(hover)))
    items = [
        Item(link, title, summary[:MAX_SUMMARY], feed.publisher, now, feed.url)
        for link, title, summary in rows
        if link.startswith(("http://", "https://")) and title
    ]
    if not items:
        raise FeedError("热榜是空的")
    return items[:MAX_ITEMS]


def _stable_link(url: str) -> str:
    """微博热搜的链接带当天的排名（band_rank），排名一变就成了「新」报道。只留搜索词。"""
    parts = urllib.parse.urlsplit(url)
    if parts.netloc == "s.weibo.com":
        query = urllib.parse.parse_qs(parts.query).get("q")
        if query:
            return f"https://s.weibo.com/weibo?{urllib.parse.urlencode({'q': query[0]})}"
    return url


def _json(data: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise FeedError(f"返回的不是 JSON：{type(exc).__name__}") from None
    if not isinstance(payload, dict):
        raise FeedError("返回的 JSON 格式不认识")
    return payload


def _timestamp(value: Any) -> dt.datetime | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return dt.datetime.fromtimestamp(value, dt.UTC)
    return None


def _clean(value: Any) -> str:
    text = html.unescape(_TAG.sub(" ", str(value or "")))
    return " ".join(text.split())


def _time(entry: Any) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return dt.datetime.fromtimestamp(calendar.timegm(value), dt.UTC)
    return None
