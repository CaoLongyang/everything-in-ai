"""新闻源扩展（docs/specs/headline-radar.delta-sources.md，F15）。全部用虚构数据，不连网络。"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from eia.cli import main
from eia.headlines import run as headlines
from eia.headlines.catalog import load_catalog, source_key
from eia.headlines.config import (
    BILIBILI_URL,
    DEFAULT_NEWSNOW,
    HACKERNEWS_URL,
    Feed,
    HeadlinesConfigError,
    google_news_feed,
    load_headlines_config,
)
from eia.headlines.feeds import (
    FeedError,
    parse_google_news,
    parse_hackernews,
    parse_hotlist,
)
from eia.schema.vault import CONFIG_NAME, init_vault

from .conftest import NOW, fake_fetcher, fake_urlopen, hours_ago, http_error, rss


def google_rss(entries: list[tuple[str, str, str]]) -> bytes:
    """(标题, 链接, 媒体名)。Google News 的标题末尾带「 - 媒体名」。"""
    items = "".join(
        f"<item><title>{title} - {source}</title><link>{link}</link>"
        f"<pubDate>{hours_ago(3):%a, %d %b %Y %H:%M:%S} GMT</pubDate>"
        f'<source url="https://{source.lower()}.example">{source}</source></item>'
        for title, link, source in entries
    )
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>"q" - Google News</title>'
        f"{items}</channel></rss>"
    ).encode()


def hn_json(hits: list[tuple[str, str | None, int]]) -> bytes:
    return json.dumps(
        {
            "hits": [
                {"title": t, "url": u, "points": p, "objectID": str(i), "created_at_i": 1795000000}
                for i, (t, u, p) in enumerate(hits)
            ]
        }
    ).encode()


def newsnow_json(titles: list[str], *, platform: str = "weibo") -> bytes:
    items = []
    for rank, title in enumerate(titles, start=1):
        query = urllib.parse.quote(title)
        url = (
            f"https://s.weibo.com/weibo?q={query}&t=31&band_rank={rank}&Refer=top"
            if platform == "weibo"
            else f"https://www.{platform}.example/q/{rank}"
        )
        items.append({"id": str(rank), "title": title, "url": url, "extra": {"hover": "摘要"}})
    return json.dumps({"status": "success", "id": platform, "items": items}).encode()


def bili_json(titles: list[str]) -> bytes:
    videos = [{"title": t, "bvid": f"BV{i}", "desc": "简介"} for i, t in enumerate(titles)]
    return json.dumps({"code": 0, "data": {"list": videos}}).encode()


def write_config(root: Path, headlines_section: dict[str, Any], **extra: Any) -> Path:
    init_vault(root)
    data: dict[str, Any] = {"schema": 1, "timezone": "Asia/Singapore", **extra}
    data["headlines"] = headlines_section
    (root / CONFIG_NAME).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return root


def hotlist_url(platform: str, base: str = DEFAULT_NEWSNOW) -> str:
    return BILIBILI_URL if platform == "bilibili" else f"{base}/api/s?id={platform}"


# ---------- F15-01 配置 ----------


def test_F15_01_old_rss_config_still_works_and_types_are_parsed(tmp_path: Path) -> None:
    root = write_config(
        tmp_path / "v",
        {
            "cluster": "lexical",
            "feeds": [
                {"publisher": "BBC", "url": "https://bbc.example/rss"},
                {"type": "rss", "publisher": "DW", "url": "https://dw.example/rss"},
                {"type": "hackernews"},
                {"type": "google_news", "query": "Malaysia car", "country": "my"},
                {"type": "hotlist", "platform": "zhihu"},
            ],
        },
    )
    feeds = load_headlines_config(root).feeds
    assert [f.kind for f in feeds] == ["rss", "rss", "hackernews", "google_news", "hotlist"]
    assert feeds[0] == Feed("https://bbc.example/rss", "BBC")
    assert feeds[2].min_score == 100 and feeds[2].url == HACKERNEWS_URL
    assert "gl=MY" in feeds[3].url and feeds[3].name == "Google News：Malaysia car"
    assert feeds[4].publisher == "知乎" and feeds[4].name == "知乎热榜"


@pytest.mark.parametrize(
    ("feed", "message"),
    [
        ({"type": "reddit", "subreddit": "x"}, "第 2 项的 type「reddit」无法识别"),
        ({"type": "google_news"}, "第 2 项 缺少 query"),
        ({"type": "hotlist", "platform": "xhs"}, "第 2 项的 platform「xhs」无法识别"),
        ({"type": "hackernews", "min_score": "high"}, "第 2 项的 min_score 应该是"),
        ({"type": "rss", "url": "https://x"}, "第 2 项 缺少 publisher"),
    ],
)
def test_F15_01_bad_source_reports_which_item(
    tmp_path: Path, feed: dict[str, Any], message: str
) -> None:
    root = write_config(
        tmp_path / "v", {"feeds": [{"publisher": "BBC", "url": "https://bbc.example"}, feed]}
    )
    with pytest.raises(HeadlinesConfigError, match=message):
        load_headlines_config(root)


# ---------- F15-02 Hacker News ----------


def test_F15_02_hackernews_keeps_only_popular_stories() -> None:
    feed = Feed(HACKERNEWS_URL, "Hacker News", "hackernews", min_score=100)
    data = hn_json([("Big launch", "https://a.example", 350), ("Small", "https://b.example", 20)])
    data_ask = hn_json([("Ask HN: something", None, 150)])
    items = parse_hackernews(data, feed, NOW)
    assert [(i.title, i.publisher, i.summary) for i in items] == [
        ("Big launch", "Hacker News", "350 分")
    ]
    # 没有外链的帖子（Ask HN）指向讨论页
    assert parse_hackernews(data_ask, feed, NOW)[0].url == "https://news.ycombinator.com/item?id=0"
    with pytest.raises(FeedError, match="没有 hits"):
        parse_hackernews(b'{"x": 1}', feed, NOW)
    with pytest.raises(FeedError, match="不是 JSON"):
        parse_hackernews(b"<html>", feed, NOW)


# ---------- F15-03 Google News ----------


def test_F15_03_google_news_url_and_real_publishers() -> None:
    feed = google_news_feed("马来西亚 汽车", "zh-CN", "MY", window_hours=36)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(feed.url).query)
    assert query == {
        "q": ["马来西亚 汽车 when:2d"],
        "hl": ["zh-CN"],
        "gl": ["MY"],
        "ceid": ["MY:zh-Hans"],
    }
    data = google_rss(
        [
            ("New car tax announced", "https://news.google.com/a", "The Star"),
            ("New car tax announced - The Star", "https://news.google.com/b", "The Star"),
            ("Car tax: what changes", "https://news.google.com/c", "Malay Mail"),
        ]
    )
    items = parse_google_news(data, feed, NOW)
    assert [(i.title, i.publisher) for i in items] == [
        ("New car tax announced", "The Star"),
        ("New car tax announced", "The Star"),  # 标题带两遍媒体名的也去干净
        ("Car tax: what changes", "Malay Mail"),
    ]
    assert items[0].published == hours_ago(3).replace(microsecond=0)


def test_F15_03_google_news_with_no_results_is_not_an_error() -> None:
    feed = google_news_feed("nothing", "en", "US", window_hours=36)
    assert parse_google_news(google_rss([]), feed, NOW) == []
    with pytest.raises(FeedError, match="不是 RSS"):
        parse_google_news(b"<html><body>captcha</body></html>", feed, NOW)


# ---------- F15-04 热榜 ----------


def test_F15_04_hotlists_parse_and_weibo_links_are_stable() -> None:
    weibo = Feed(hotlist_url("weibo"), "微博", "hotlist")
    today = parse_hotlist(newsnow_json(["青海地震", "新车发布"]), weibo, NOW)
    tomorrow = parse_hotlist(newsnow_json(["新车发布", "青海地震"]), weibo, NOW)
    assert [i.title for i in today] == ["青海地震", "新车发布"]
    assert all(i.publisher == "微博" and i.published == NOW for i in today)
    assert today[0].summary == "摘要"
    # 排名变了，链接不变（否则同一条热搜每天都算新的）
    assert {i.url for i in today} == {i.url for i in tomorrow}
    assert "band_rank" not in today[0].url

    bili = Feed(BILIBILI_URL, "B站", "hotlist")
    videos = parse_hotlist(bili_json(["新车评测"]), bili, NOW)
    assert videos[0].url == "https://www.bilibili.com/video/BV0" and videos[0].publisher == "B站"
    with pytest.raises(FeedError, match="B站返回错误"):
        parse_hotlist(b'{"code": -352, "message": "risk"}', bili, NOW)
    with pytest.raises(FeedError, match="热榜是空的"):
        parse_hotlist(b'{"items": []}', weibo, NOW)


def test_F15_04_newsnow_address_is_configurable_and_failures_stay_local(tmp_path: Path) -> None:
    base = "https://my-newsnow.example"
    root = write_config(
        tmp_path / "v",
        {
            "cluster": "lexical",
            "newsnow": base + "/",
            "feeds": [
                {"type": "hotlist", "platform": "zhihu"},
                {"type": "hotlist", "platform": "weibo"},
                {"type": "hotlist", "platform": "bilibili"},
            ],
        },
    )
    assert [f.url for f in load_headlines_config(root).feeds][:2] == [
        f"{base}/api/s?id=zhihu",
        f"{base}/api/s?id=weibo",
    ]
    table: dict[str, bytes | Exception] = {
        f"{base}/api/s?id=zhihu": newsnow_json(["问题一"], platform="zhihu"),
        f"{base}/api/s?id=weibo": FeedError("HTTP 403"),
        BILIBILI_URL: bili_json(["视频一"]),
    }
    result = headlines.run(root, now=NOW, fetcher=fake_fetcher(table))
    errors = {s.feed.name: s.error for s in result.feeds}
    assert errors == {"知乎热榜": None, "微博热榜": "HTTP 403", "B站热榜": None}


# ---------- F15-05 计数 ----------


def test_F15_05_platforms_count_once_google_news_counts_real_media(tmp_path: Path) -> None:
    google = {"type": "google_news", "query": "car tax", "country": "MY"}
    root = write_config(
        tmp_path / "v",
        {
            "cluster": "lexical",
            "min_sources": 3,
            "feeds": [
                {"type": "hotlist", "platform": "zhihu"},
                {"type": "hotlist", "platform": "weibo"},
                {"type": "hotlist", "platform": "douyin"},
                google,
            ],
        },
    )
    google_url = load_headlines_config(root).feeds[3].url
    table: dict[str, bytes | Exception] = {
        # 青海地震：三个平台都上了热榜 → 3 家
        hotlist_url("zhihu"): newsnow_json(["青海德令哈发生地震", "某明星官宣"], platform="zhihu"),
        hotlist_url("weibo"): newsnow_json(["青海德令哈发生地震"]),
        hotlist_url("douyin"): newsnow_json(["青海德令哈发生地震"], platform="douyin"),
        # 车税：一次搜索里 3 家媒体 → 3 家；摩托：同一家媒体的 3 篇 → 1 家
        google_url: google_rss(
            [
                ("Malaysia announces new car tax rules", "https://g.example/1", "The Star"),
                ("Malaysia announces new car tax rules", "https://g.example/2", "Malay Mail"),
                ("Malaysia announces new car tax rules", "https://g.example/3", "FMT"),
                ("Motorcycle helmet rules change", "https://g.example/4", "Paul Tan"),
                ("Motorcycle helmet rules change", "https://g.example/5", "Paul Tan"),
                ("Motorcycle helmet rules change", "https://g.example/6", "Paul Tan"),
            ]
        ),
    }
    result = headlines.run(root, now=NOW, fetcher=fake_fetcher(table))
    assert result.qualifying == 2
    titles = sorted(Path(path).name for path in result.written.created)
    assert len(titles) == 2
    assert any("地震" in t for t in titles) and any("car-tax" in t for t in titles)


# ---------- F15-06 按兴趣搜 ----------


def _interest_vault(tmp_path: Path, *, enabled: bool) -> Path:
    return write_config(
        tmp_path / "v",
        {
            "cluster": "lexical",
            "feeds": [{"publisher": "BBC", "url": "https://bbc.example/rss"}],
            "interest_search": {"enabled": enabled, "lang": "zh-CN", "country": "my"},
        },
        interests=[
            {"name": "马来西亚用车", "search": ["Malaysia car insurance", "Proton"]},
            {"name": "改革宗神学"},
        ],
    )


def test_F15_06_each_interest_becomes_a_search(tmp_path: Path) -> None:
    root = _interest_vault(tmp_path, enabled=True)
    config = load_headlines_config(root)
    feeds = headlines.all_feeds(root, config)
    searches = [f for f in feeds if f.query]
    assert [f.query for f in searches] == ["Malaysia car insurance OR Proton", "改革宗神学"]
    # 英文关键词用英文搜，中文关键词用配置的语言
    langs = [urllib.parse.parse_qs(urllib.parse.urlsplit(f.url).query)["hl"] for f in searches]
    assert langs == [["en"], ["zh-CN"]]
    assert all("gl=MY" in f.url for f in searches)

    off_root = _interest_vault(tmp_path / "off", enabled=False)
    off = load_headlines_config(off_root)
    assert off.interest_search is None
    assert not [f for f in headlines.all_feeds(off_root, off) if f.query]


def test_F15_06_run_prints_the_keywords_sent_to_google(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _interest_vault(tmp_path, enabled=True)
    feeds = headlines.all_feeds(root, load_headlines_config(root))
    table: dict[str, bytes | Exception] = {f.url: google_rss([]) for f in feeds if f.query}
    table["https://bbc.example/rss"] = rss([("Something", "https://bbc.example/1", hours_ago(1))])
    monkeypatch.setattr("eia.headlines.feeds.urllib.request.urlopen", fake_urlopen(table))
    result = CliRunner().invoke(main, ["headlines", "run", "--vault", str(root), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "按兴趣搜 Google News 的关键词：Malaysia car insurance OR Proton；改革宗神学" in (
        result.output
    )
    assert "3/3 个源可用" in result.output


def test_F15_06_search_only_without_any_feed(tmp_path: Path) -> None:
    root = write_config(
        tmp_path / "v", {"cluster": "lexical", "interest_search": {"enabled": True}}
    )
    config = load_headlines_config(root)
    assert config.feeds == ()
    with pytest.raises(HeadlinesConfigError, match="没有可抓的源"):
        headlines.all_feeds(root, config)


# ---------- F15-07 推荐源目录 ----------


def test_F15_07_every_catalog_entry_is_a_valid_source() -> None:
    checked, groups = load_catalog()
    assert checked == "2026-11-21"
    names = [g.name for g in groups]
    assert {"国际新闻", "中文热榜", "科技", "AI", "马来西亚", "基督教"} <= set(names)
    keys = [s.key for g in groups for s in g.sources]
    assert len(keys) == len(set(keys))  # 没有重复
    kinds = {s.feed.kind for g in groups for s in g.sources}
    assert kinds == {"rss", "hackernews", "hotlist"}
    assert source_key({"publisher": "A", "url": "u"}) == source_key(
        {"type": "rss", "publisher": "A", "url": "u"}
    )


def test_F15_07_catalog_command_marks_sources_in_use(tmp_path: Path) -> None:
    root = write_config(
        tmp_path / "v",
        {
            "feeds": [
                {"type": "hotlist", "platform": "zhihu"},
                {"publisher": "X", "url": "https://x"},
            ]
        },
    )
    result = CliRunner().invoke(main, ["headlines", "catalog", "--vault", str(root)])
    assert result.exit_code == 0, result.output
    zhihu = next(line for line in result.output.splitlines() if "知乎热榜" in line)
    assert "已在用" in zhihu and "{type: hotlist, platform: zhihu}" in zhihu
    weibo = next(line for line in result.output.splitlines() if "微博热榜" in line)
    assert "已在用" not in weibo
    # 需要引号的地址（带 ?）复制出去也是合法的 YAML
    cna = next(line for line in result.output.splitlines() if "channelnewsasia" in line)
    snippet = yaml.safe_load(cna[cna.index("{") :])
    assert snippet["url"].endswith("?_format=xml")


# ---------- F15-09 check ----------


def test_F15_09_check_covers_every_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = write_config(
        tmp_path / "v",
        {
            "feeds": [
                {"type": "hackernews"},
                {"type": "google_news", "query": "nothing here"},
                {"type": "hotlist", "platform": "bilibili"},
                {"type": "hotlist", "platform": "toutiao"},
            ]
        },
    )
    google_url = load_headlines_config(root).feeds[1].url
    table: dict[str, bytes | Exception] = {
        HACKERNEWS_URL: hn_json([("Launch", "https://a.example", 500)]),
        google_url: google_rss([]),
        BILIBILI_URL: bili_json(["视频"]),
        hotlist_url("toutiao"): http_error(403),
    }
    monkeypatch.setattr("eia.headlines.feeds.urllib.request.urlopen", fake_urlopen(table))
    result = CliRunner().invoke(main, ["headlines", "check", "--vault", str(root)])
    assert result.exit_code == 1  # 头条失败

    def lines(name: str) -> str:
        return next(line for line in result.output.splitlines() if name in line)

    assert lines("Hacker News").startswith("正常") and "1 条" in lines("Hacker News")
    assert lines("Google News：nothing here").startswith("正常") and "0 条" in lines("nothing here")
    assert lines("B站热榜").startswith("正常")
    assert lines("头条热榜").startswith("失败") and "HTTP 403" in lines("头条热榜")
