"""下载并解析 RSS / Atom。全仓库只有这里 import feedparser。

下载用标准库自己做（feedparser 只负责解析字节），这样超时、大小上限、User-Agent 都由我们控制。
urllib 会自动使用系统代理设置。
"""

from __future__ import annotations

import calendar
import datetime as dt
import html
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from eia.headlines.config import Feed

USER_AGENT = "eia-headlines/1"
TIMEOUT_S = 20
MAX_BYTES = 5_000_000
MAX_SUMMARY = 500
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
        raise FeedError(f"内容超过 {MAX_BYTES // 1_000_000} MB，不像是 RSS")
    return data


def fetch_feed(
    feed: Feed, now: dt.datetime, fetcher: Callable[[str], bytes] = download
) -> list[Item]:
    return parse_feed(fetcher(feed.url), feed, now)


def parse_feed(data: bytes, feed: Feed, now: dt.datetime) -> list[Item]:
    try:
        import feedparser
    except ImportError as exc:
        raise FeedError(
            '缺少新闻雷达需要的依赖 feedparser。请运行 pip install -e ".[news]"'
        ) from exc
    parsed = feedparser.parse(data)
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


def _clean(value: Any) -> str:
    text = html.unescape(_TAG.sub(" ", str(value or "")))
    return " ".join(text.split())


def _time(entry: Any) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return dt.datetime.fromtimestamp(calendar.timegm(value), dt.UTC)
    return None
