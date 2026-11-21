"""解析 config.yaml 的 headlines 段。

规格见 docs/specs/headline-radar.md 第 3 节和 docs/specs/headline-radar.delta-sources.md 第 3 节。
"""

from __future__ import annotations

import math
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from eia.schema.vault import CONFIG_NAME, is_vault

EMBED = "embed"
LEXICAL = "lexical"
# embed 的 0.68 是 2026-11-21 用 13 个真实源 + bge-m3 校准的：0.70 起中英文同一事件会分开
DEFAULT_SIMILARITY = {EMBED: 0.68, LEXICAL: 0.35}

RSS = "rss"
HACKERNEWS = "hackernews"
GOOGLE_NEWS = "google_news"
HOTLIST = "hotlist"
KINDS = (RSS, HACKERNEWS, GOOGLE_NEWS, HOTLIST)

# 热榜平台 → 计数时的媒体名。bilibili 直接访问 B站公开接口，其余经 NewsNow
HOTLIST_PLATFORMS = {
    "zhihu": "知乎",
    "weibo": "微博",
    "douyin": "抖音",
    "toutiao": "头条",
    "bilibili": "B站",
}
DEFAULT_NEWSNOW = "https://newsnow.busiyi.world"
HACKERNEWS_URL = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=50"
BILIBILI_URL = "https://api.bilibili.com/x/web-interface/popular?ps=30"
GOOGLE_NEWS_URL = "https://news.google.com/rss/search"
GOOGLE_NEWS_PUBLISHER = ""  # 空：每篇报道用自己的真实媒体名
_CJK = re.compile(r"[㐀-鿿]")


class HeadlinesConfigError(Exception):
    """没有 headlines 配置，或者配置不完整。不会访问网络。"""


@dataclass(frozen=True)
class Feed:
    """一个要抓的源。url 是实际请求的地址；kind 决定怎么解析。"""

    url: str
    publisher: str  # 计数用的媒体名；google_news 为空，按每篇报道的真实媒体名算
    kind: str = RSS
    min_score: int = 0
    label: str = ""  # 显示用的名字；空就用 publisher
    query: str = ""  # 按兴趣自动生成的搜索才有：发给 Google 的关键词（要让用户看见）

    @property
    def name(self) -> str:
        return self.label or self.publisher


@dataclass(frozen=True)
class InterestSearch:
    """按兴趣自动搜 Google News（delta-sources 第 5 节）。"""

    lang: str = "zh-CN"
    country: str = "US"


@dataclass(frozen=True)
class HeadlinesConfig:
    feeds: tuple[Feed, ...]
    min_sources: int
    window_hours: float
    cluster: str
    similarity: float
    interest_search: InterestSearch | None = None


def load_headlines_config(root: Path) -> HeadlinesConfig:
    if not is_vault(root):
        raise HeadlinesConfigError(
            f"{root} 不是一个 vault（找不到 {CONFIG_NAME}），请先运行 eia init"
        )
    try:
        data = yaml.safe_load((root / CONFIG_NAME).read_bytes().decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise HeadlinesConfigError(f"{CONFIG_NAME} 无法读取：{exc}") from exc
    section = data.get("headlines") if isinstance(data, dict) else None
    if section is None:
        raise HeadlinesConfigError(
            f"{CONFIG_NAME} 里没有 headlines 配置。不会访问任何新闻源（模板见 eia init 生成的注释）"
        )
    if not isinstance(section, dict):
        raise HeadlinesConfigError(f"{CONFIG_NAME} 里的 headlines 应该是一组键值")

    cluster = str(section.get("cluster") or EMBED)
    if cluster not in DEFAULT_SIMILARITY:
        raise HeadlinesConfigError(
            f"headlines.cluster「{cluster}」无法识别（可选：embed / lexical）"
        )
    similarity = _number(section, "similarity", DEFAULT_SIMILARITY[cluster])
    if not 0 < similarity <= 1:
        raise HeadlinesConfigError("headlines.similarity 应该在 0 到 1 之间")
    min_sources = _number(section, "min_sources", 3)
    if min_sources < 1 or min_sources != int(min_sources):
        raise HeadlinesConfigError("headlines.min_sources 应该是不小于 1 的整数")
    window = _number(section, "window_hours", 36)
    if window <= 0:
        raise HeadlinesConfigError("headlines.window_hours 应该大于 0")
    newsnow = str(section.get("newsnow") or DEFAULT_NEWSNOW).strip().rstrip("/")
    if not newsnow.startswith(("http://", "https://")):
        raise HeadlinesConfigError("headlines.newsnow 应该是 http(s) 地址")
    search = _interest_search(section.get("interest_search"))
    feeds = _feeds(section.get("feeds"), window=window, newsnow=newsnow, searching=bool(search))
    return HeadlinesConfig(feeds, int(min_sources), window, cluster, similarity, search)


def _interest_search(value: Any) -> InterestSearch | None:
    if value is None or value is False:
        return None
    if not isinstance(value, dict):
        raise HeadlinesConfigError("headlines.interest_search 应该写成 enabled: true 这样的键值")
    if not value.get("enabled"):
        return None
    defaults = InterestSearch()
    return InterestSearch(
        lang=str(value.get("lang") or defaults.lang).strip(),
        country=str(value.get("country") or defaults.country).strip().upper(),
    )


def _feeds(value: Any, *, window: float, newsnow: str, searching: bool) -> tuple[Feed, ...]:
    if not value:
        if searching:
            return ()  # 只按兴趣搜也可以
        raise HeadlinesConfigError("headlines.feeds 是空的：至少列出一个新闻源")
    if not isinstance(value, list):
        raise HeadlinesConfigError("headlines.feeds 应该是一个列表")
    return tuple(
        parse_source(item, f"headlines.feeds 第 {index} 项", window=window, newsnow=newsnow)
        for index, item in enumerate(value, start=1)
    )


def parse_source(item: Any, where: str, *, window: float, newsnow: str) -> Feed:
    if not isinstance(item, dict):
        raise HeadlinesConfigError(f"{where} 应该写成 url: ... publisher: ...")
    kind = str(item.get("type") or RSS).strip()
    if kind == RSS:
        url = str(item.get("url") or "").strip()
        publisher = str(item.get("publisher") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise HeadlinesConfigError(f"{where} 缺少 url，或者不是 http(s) 地址")
        if not publisher:
            raise HeadlinesConfigError(f"{where} 缺少 publisher（媒体名，同名的源只算一家）")
        return Feed(url, publisher)
    if kind == HACKERNEWS:
        min_score = _int(item, "min_score", 100, where)
        return Feed(HACKERNEWS_URL, "Hacker News", HACKERNEWS, min_score)
    if kind == GOOGLE_NEWS:
        query = str(item.get("query") or "").strip()
        if not query:
            raise HeadlinesConfigError(f"{where} 缺少 query（要搜的关键词）")
        lang = str(item.get("lang") or "en").strip()
        country = str(item.get("country") or "US").strip().upper()
        return google_news_feed(query, lang, country, window_hours=window)
    if kind == HOTLIST:
        platform = str(item.get("platform") or "").strip().lower()
        if platform not in HOTLIST_PLATFORMS:
            choices = " / ".join(HOTLIST_PLATFORMS)
            raise HeadlinesConfigError(
                f"{where}的 platform「{platform}」无法识别（可选：{choices}）"
            )
        url = BILIBILI_URL if platform == "bilibili" else f"{newsnow}/api/s?id={platform}"
        return Feed(
            url, HOTLIST_PLATFORMS[platform], HOTLIST, label=f"{HOTLIST_PLATFORMS[platform]}热榜"
        )
    raise HeadlinesConfigError(f"{where}的 type「{kind}」无法识别（可选：{' / '.join(KINDS)}）")


def google_news_feed(
    query: str, lang: str, country: str, *, window_hours: float, label: str = ""
) -> Feed:
    """Google News 搜索。hl / gl / ceid 的拼法照 Horizon 的 google_news.py。"""
    days = max(1, math.ceil(window_hours / 24))
    ceid_lang = {"zh-CN": "zh-Hans", "zh-TW": "zh-Hant", "zh-HK": "zh-Hant"}.get(lang, lang)
    params = {
        "q": f"{query} when:{days}d",
        "hl": lang,
        "gl": country,
        "ceid": f"{country}:{ceid_lang}",
    }
    url = f"{GOOGLE_NEWS_URL}?{urllib.parse.urlencode(params)}"
    return Feed(url, GOOGLE_NEWS_PUBLISHER, GOOGLE_NEWS, label=label or f"Google News：{query}")


def search_lang(query: str, search: InterestSearch) -> str:
    """有中文就用配置的语言搜，否则用英文（英文关键词配中文界面搜不到几篇）。"""
    return search.lang if _CJK.search(query) else "en"


def _int(item: dict[str, Any], name: str, default: int, where: str) -> int:
    value = item.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HeadlinesConfigError(f"{where}的 {name} 应该是不小于 0 的整数")
    return value


def _number(section: dict[str, Any], name: str, default: float) -> float:
    value = section.get(name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise HeadlinesConfigError(f"headlines.{name} 应该是数字")
    return float(value)
