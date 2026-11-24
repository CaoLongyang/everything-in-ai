"""F11：新闻雷达。"""

from __future__ import annotations

import datetime as dt
import functools
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from eia.cli import main
from eia.headlines import run as radar
from eia.headlines.cluster import cluster_by_words, tokenize
from eia.headlines.config import Feed, HeadlinesConfigError, load_headlines_config
from eia.headlines.feeds import FeedError, Item, download, parse_feed
from eia.headlines.store import load_items, load_vectors, merge_items, save_items, save_vectors
from eia.llm import Client
from eia.llm.config import load_llm_config
from eia.llm.errors import TransientError
from eia.schema.io import read_document
from eia.schema.validate import validate_vault
from eia.schema.vault import CONFIG_TEMPLATE

from .conftest import (
    EMBED_LLM,
    FEEDS,
    NOW,
    FakeEmbedBackend,
    atom,
    fake_fetcher,
    fake_urlopen,
    hours_ago,
    http_error,
    rss,
    standard_feeds,
)

ROOT = Path(__file__).resolve().parents[2]


def pin_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """命令行入口不传 now，会用真实时间；固定成 NOW，否则样例报道过几天就出了时间窗口。"""
    monkeypatch.setattr(radar, "run", functools.partial(radar.run, now=NOW))


def _client(root: Path, backend: Any) -> Client:
    return Client(load_llm_config(root), lambda endpoint, timeout: backend, sleep=lambda _s: None)


def _run(root: Path, table: dict[str, Any], **kwargs: Any) -> radar.RunResult:
    backend = kwargs.pop("backend", FakeEmbedBackend())
    client = _client(root, backend) if load_headlines_config(root).cluster == "embed" else None
    return radar.run(
        root, now=kwargs.pop("now", NOW), fetcher=fake_fetcher(table), client=client, **kwargs
    )


def _headline_files(root: Path) -> list[Path]:
    return sorted((root / "inbox" / "headlines").glob("*.md"))


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("不应该访问网络")

    monkeypatch.setattr("urllib.request.urlopen", _forbidden)


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeEmbedBackend:
    """让 CLI 里的 llm.get_client 用假的向量模型。"""
    backend = FakeEmbedBackend()

    class _Client(Client):
        def __init__(self, config: Any) -> None:
            super().__init__(config, lambda endpoint, timeout: backend, sleep=lambda _s: None)

    monkeypatch.setattr("eia.llm.Client", _Client)
    return backend


# ---------- F11-01 配置 ----------


@pytest.mark.parametrize(
    ("headlines", "expected"),
    [
        (None, "没有 headlines 配置"),
        ({"feeds": []}, "headlines.feeds 是空的"),
        ({"feeds": "x"}, "应该是一个列表"),
        ({"feeds": ["x"]}, "第 1 项 应该写成"),
        ({"feeds": [{"publisher": "A", "url": "ftp://x"}]}, "不是 http(s)"),
        ({"feeds": [{"url": "https://x"}]}, "缺少 publisher"),
        ({"feeds": FEEDS, "cluster": "magic"}, "「magic」无法识别"),
        ({"feeds": FEEDS, "similarity": 2}, "0 到 1 之间"),
        ({"feeds": FEEDS, "similarity": "high"}, "应该是数字"),
        ({"feeds": FEEDS, "min_sources": 1.5}, "不小于 1 的整数"),
        ({"feeds": FEEDS, "window_hours": 0}, "大于 0"),
        ([1, 2], "应该是一组键值"),
    ],
)
def test_F11_01_bad_config_stops_before_any_network(
    make_vault: Callable[..., Path], no_network: None, headlines: Any, expected: str
) -> None:
    root = make_vault({"feeds": FEEDS})
    data = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    if headlines is None:
        del data["headlines"]
    else:
        data["headlines"] = headlines
    (root / "config.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    for command in ("run", "check"):
        result = CliRunner().invoke(main, ["headlines", command, "--vault", str(root)])
        assert result.exit_code == 1, result.output
        assert expected in result.output and "没有访问任何新闻源" in result.output


def test_F11_01_not_a_vault_or_broken_yaml(tmp_path: Path) -> None:
    with pytest.raises(HeadlinesConfigError, match="不是一个 vault"):
        load_headlines_config(tmp_path)
    (tmp_path / "config.yaml").write_bytes(b"headlines: [unclosed\n")
    with pytest.raises(HeadlinesConfigError, match="无法读取"):
        load_headlines_config(tmp_path)


def test_F11_01_defaults(make_vault: Callable[..., Path]) -> None:
    config = load_headlines_config(make_vault({"feeds": FEEDS}))
    assert (config.min_sources, config.window_hours, config.cluster, config.similarity) == (
        3,
        36.0,
        "embed",
        0.68,
    )
    lexical = load_headlines_config(make_vault({"feeds": FEEDS, "cluster": "lexical"}))
    assert lexical.similarity == 0.35


def test_F11_01_init_template_recommends_feeds_but_enables_nothing(tmp_path: Path) -> None:
    assert "headlines" not in (yaml.safe_load(CONFIG_TEMPLATE) or {})
    lines = CONFIG_TEMPLATE.splitlines()
    start = lines.index("# headlines:")
    block = [line.removeprefix("# ") for line in lines[start:] if line.startswith("# ")]
    (tmp_path / "config.yaml").write_text("\n".join(block), encoding="utf-8")
    config = load_headlines_config(tmp_path)
    # 2026-11-21 起（delta-sources）：国际新闻 5 个 + Hacker News + 3 个中文热榜，外加按兴趣搜
    assert len(config.feeds) == 9
    assert {feed.kind for feed in config.feeds} == {"rss", "hackernews", "hotlist"}
    assert len({feed.publisher for feed in config.feeds}) == 8
    assert config.min_sources == 3 and config.cluster == "embed"
    assert config.interest_search is not None and config.interest_search.country == "MY"


# ---------- F11-02 解析与单个源失败 ----------


def test_F11_02_rss_atom_and_rdf(make_vault: Callable[..., Path]) -> None:
    feed = Feed("https://f.example", "P")
    items = parse_feed(rss([("标题 <b>一</b>", "https://a", hours_ago(1))]), feed, NOW)
    assert items[0].title == "标题 一" and items[0].summary == "摘要 & summary"
    assert items[0].published == hours_ago(1) and items[0].publisher == "P"
    assert parse_feed(atom([("A", "https://b", hours_ago(2))]), feed, NOW)[0].url == "https://b"
    rdf = standard_feeds()["https://dw.example/rdf"]
    assert isinstance(rdf, bytes)
    assert parse_feed(rdf, feed, NOW)[0].published == NOW  # 没有时间的用抓取时间
    # 没有标题或链接的条目丢掉
    assert (
        parse_feed(
            rss([("", "https://c", None), ("T", "", None), ("T", "https://d", None)]), feed, NOW
        )[0].url
        == "https://d"
    )


@pytest.mark.parametrize(
    "data", [b"<html>not a feed", b"", b"<rss version='2.0'><channel></channel></rss>"]
)
def test_F11_02_unparseable(data: bytes) -> None:
    with pytest.raises(FeedError, match="不是能识别的 RSS"):
        parse_feed(data, Feed("https://f", "P"), NOW)


def test_F11_02_one_bad_feed_does_not_stop_the_others(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch, fake_llm: FakeEmbedBackend
) -> None:
    root = make_vault(llm=EMBED_LLM)
    table: dict[str, Any] = standard_feeds()
    table["https://dw.example/rdf"] = http_error(500)
    table["https://aj.example/all.xml"] = TimeoutError("timed out")
    table["https://bbc.example/zh.xml"] = b"<html>oops</html>"
    monkeypatch.setattr("eia.headlines.feeds.urllib.request.urlopen", fake_urlopen(table))
    result = CliRunner().invoke(main, ["headlines", "run", "--vault", str(root)])
    assert result.exit_code == 0, result.output
    assert "源失败  DW  https://dw.example/rdf  HTTP 500" in result.output
    assert "源失败  Al Jazeera  https://aj.example/all.xml  连不上：timed out" in result.output
    assert "源失败  BBC  https://bbc.example/zh.xml  不是能识别的 RSS" in result.output
    assert "2/5 个源可用" in result.output

    check = CliRunner().invoke(main, ["headlines", "check", "--vault", str(root)])
    assert check.exit_code == 1
    assert "正常  Guardian  https://guardian.example/rss  2 条，最新一条" in check.output
    assert "失败  DW  https://dw.example/rdf  HTTP 500" in check.output


def test_F11_02_check_flags_stale_feeds(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_vault({"feeds": [FEEDS[0]], "cluster": "lexical"})
    old = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    table: dict[str, Any] = {FEEDS[0]["url"]: rss([("Old", "https://o", old)])}
    monkeypatch.setattr("eia.headlines.feeds.urllib.request.urlopen", fake_urlopen(table))
    result = CliRunner().invoke(main, ["headlines", "check", "--vault", str(root)])
    assert result.exit_code == 0 and "超过 7 天没更新" in result.output


def test_F11_02_download_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    table: dict[str, Any] = {
        "https://big": b"x" * 5_000_001,
        "https://down": urllib.error.URLError("no route"),
        "https://ok": b"fine",
    }
    monkeypatch.setattr("eia.headlines.feeds.urllib.request.urlopen", fake_urlopen(table))
    with pytest.raises(FeedError, match="超过 5 MB"):
        download("https://big")
    with pytest.raises(FeedError, match="连不上：no route"):
        download("https://down")
    assert download("https://ok") == b"fine"


def test_F11_02_missing_feedparser_gives_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "feedparser", None)
    with pytest.raises(FeedError, match=r'pip install -e "\.\[news\]"'):
        parse_feed(b"<rss/>", Feed("https://f", "P"), NOW)


def test_F11_02_cli_without_optional_packages_still_starts() -> None:
    optional = "('numpy', 'feedparser', 'openai', 'anthropic')"
    code = f"import sys, eia.cli; print(sorted(m for m in {optional} if m in sys.modules))"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True
    )
    assert out.stdout.strip() == "[]"


# ---------- F11-03 / F11-04 归组与计数 ----------


def test_F11_03_embed_groups_across_languages(make_vault: Callable[..., Path]) -> None:
    root = make_vault(llm=EMBED_LLM)
    result = _run(root, standard_feeds())
    assert result.in_window == 7 and result.qualifying == 1
    [path] = _headline_files(root)
    meta = read_document(path).meta
    urls = {source["url"] for source in meta["sources"]}
    assert urls == {
        "https://bbc.example/1",
        "https://bbc.example/zh/1",
        "https://gu.example/1",
        "https://aj.example/1",
    }
    assert meta["title"] == "Strong earthquake hits southern Japan"  # 最早那篇
    assert meta["lang"] == "en" and meta["cluster_id"].startswith("c-")


def test_F11_03_lexical_groups_reworded_titles_and_keeps_topics_apart() -> None:
    def item(title: str, n: int) -> Item:
        return Item(f"https://x/{n}", title, "", f"P{n}", hours_ago(10 - n), "https://f")

    items = [
        item("Strong earthquake hits southern Japan", 1),
        item("Earthquake hits southern Japan, dozens injured", 2),
        item("Apple unveils new iPhone with bigger screen", 3),
        item("日本南部发生强烈地震", 4),
        item("日本南部强烈地震 数十人受伤", 5),
        item("Southern Japan earthquake: dozens injured", 6),
    ]
    groups = cluster_by_words(items, 0.35)
    titles = sorted(sorted(i.url[-1] for i in group) for group in groups)
    assert titles == [["1", "2", "6"], ["3"], ["4", "5"]]
    assert tokenize("地") == frozenset({"地"})
    assert "the" not in tokenize("The quake") and "quake" in tokenize("The quake")


def test_F11_04_same_publisher_counts_once(make_vault: Callable[..., Path]) -> None:
    root = make_vault(llm=EMBED_LLM)
    table = standard_feeds()
    # 去掉 Al Jazeera 的地震报道：剩 BBC 两个源 + Guardian，共 3 篇但只有 2 家
    table["https://aj.example/all.xml"] = rss(
        [("Phone news iphone", "https://aj.example/9", hours_ago(1))]
    )
    result = _run(root, table)
    assert result.qualifying == 0 and _headline_files(root) == []


# ---------- F11-05 / F11-06 写入与更新 ----------


def test_F11_05_update_keeps_user_edits_and_finds_renamed_files(
    make_vault: Callable[..., Path],
) -> None:
    root = make_vault(llm=EMBED_LLM)
    _run(root, standard_feeds())
    [path] = _headline_files(root)
    raw = path.read_text(encoding="utf-8").replace("tags: []", "tags:\n- 日本")
    renamed = path.with_name("我改过的名字.md")
    renamed.write_text(raw + "\n我的笔记：关注后续\n", encoding="utf-8")
    path.unlink()

    table = standard_feeds()
    table["https://dw.example/rdf"] = rss(
        [("Japan earthquake toll rises", "https://dw.example/2", hours_ago(1))]
    )
    later = NOW + dt.timedelta(hours=1)
    result = _run(root, table, now=later)
    assert result.written.updated == ["inbox/headlines/我改过的名字.md"]
    assert _headline_files(root) == [renamed]
    document = read_document(renamed)
    assert document.meta["tags"] == ["日本"]
    assert document.meta["independent_count"] == 4
    assert document.meta["updated"] == later
    assert "我的笔记：关注后续" in document.body
    assert (
        "**DW** · 11-21 19:00 · [Japan earthquake toll rises](https://dw.example/2)"
        in document.body
    )

    # 什么都没变时不改文件
    before = renamed.read_bytes()
    again = _run(root, table, now=later + dt.timedelta(minutes=5))
    assert again.written.unchanged == 1 and renamed.read_bytes() == before


def test_F11_05_sources_never_shrink_and_count_dropping_does_not_delete(
    make_vault: Callable[..., Path],
) -> None:
    root = make_vault(llm=EMBED_LLM)
    _run(root, standard_feeds())
    [path] = _headline_files(root)
    # 三天后：旧报道都滑出窗口
    result = _run(
        root,
        {url: rss([]) for url in standard_feeds()}
        | {"https://dw.example/rdf": rss([("x iphone", "https://n", NOW + dt.timedelta(days=3))])},
        now=NOW + dt.timedelta(days=3),
    )
    assert result.qualifying == 0
    assert path.exists() and len(read_document(path).meta["sources"]) == 4


def test_F11_05_broken_headline_files_are_skipped_not_touched(
    make_vault: Callable[..., Path],
) -> None:
    root = make_vault(llm=EMBED_LLM)
    _run(root, standard_feeds())
    [path] = _headline_files(root)
    broken = path.read_text(encoding="utf-8").replace("<!-- eia:headline:end -->", "")
    path.write_text(broken, encoding="utf-8")
    (root / "inbox" / "headlines" / "junk.md").write_bytes(b"no frontmatter")
    table = standard_feeds()
    table["https://dw.example/rdf"] = rss(
        [("Japan earthquake toll rises", "https://dw.example/2", hours_ago(1))]
    )
    result = _run(root, table)
    reasons = dict(result.written.skipped)
    assert "标记缺失" in reasons[path.relative_to(root).as_posix()]
    assert "缺少 frontmatter" in reasons["inbox/headlines/junk.md"]
    assert path.read_text(encoding="utf-8") == broken


def test_F11_06_generated_files_pass_validate(make_vault: Callable[..., Path]) -> None:
    root = make_vault(llm=EMBED_LLM)
    _run(root, standard_feeds())
    report = validate_vault(root)
    assert report.ok, report.issues
    assert not [issue for issue in report.warnings if "headlines" in issue.path]


# ---------- F11-07 预览 ----------


def test_F11_07_dry_run_writes_nothing(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch, fake_llm: FakeEmbedBackend
) -> None:
    pin_now(monkeypatch)
    root = make_vault(llm=EMBED_LLM)
    monkeypatch.setattr(
        "eia.headlines.feeds.urllib.request.urlopen", fake_urlopen(standard_feeds())
    )
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    result = CliRunner().invoke(main, ["headlines", "run", "--dry-run", "--vault", str(root)])
    assert result.exit_code == 0, result.output
    assert "（预览，未写入）5/5 个源可用" in result.output and "新建 1" in result.output
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == before
    assert not (root / ".eia").exists()


def test_F11_07_real_run_reports_and_caches(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch, fake_llm: FakeEmbedBackend
) -> None:
    pin_now(monkeypatch)
    root = make_vault(llm=EMBED_LLM)
    monkeypatch.setattr(
        "eia.headlines.feeds.urllib.request.urlopen", fake_urlopen(standard_feeds())
    )
    result = CliRunner().invoke(main, ["headlines", "run", "--vault", str(root)])
    assert result.exit_code == 0, result.output
    assert "新建  inbox/headlines/" in result.output
    assert (root / ".eia" / "eia.db").is_file() and len(load_items(root)) == 7
    # 第二次运行：向量全部来自缓存，不再调用模型
    calls = len(fake_llm.batches)
    again = CliRunner().invoke(main, ["headlines", "run", "--vault", str(root)])
    assert "新建 0，更新 0，未变 1" in again.output
    assert len(fake_llm.batches) == calls


# ---------- F11-08 向量 ----------


def test_F11_08_embed_needs_llm_embed(make_vault: Callable[..., Path], no_network: None) -> None:
    for llm in (None, {**EMBED_LLM, "embed": None}):
        root = make_vault(llm=llm)
        result = CliRunner().invoke(main, ["headlines", "run", "--vault", str(root)])
        assert result.exit_code == 1
        assert "需要在 llm.embed 配置向量模型" in result.output


def test_F11_08_ollama_down(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Down(FakeEmbedBackend):
        def embed(self, texts: list[str]) -> list[list[float]]:
            raise TransientError("连不上 http://127.0.0.1:11434/v1 或超时")

    class _Client(Client):
        def __init__(self, config: Any) -> None:
            super().__init__(config, lambda endpoint, timeout: Down(), sleep=lambda _s: None)

    monkeypatch.setattr("eia.llm.Client", _Client)
    monkeypatch.setattr(
        "eia.headlines.feeds.urllib.request.urlopen", fake_urlopen(standard_feeds())
    )
    root = make_vault(llm=EMBED_LLM)
    result = CliRunner().invoke(main, ["headlines", "run", "--vault", str(root)])
    assert result.exit_code == 1
    assert "向量模型调用失败" in result.output and "请确认它已经启动" in result.output
    assert _headline_files(root) == []


def test_F11_08_vectors_are_batched_and_cache_resets_on_model_change(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(radar, "EMBED_BATCH", 3)
    root = make_vault(llm=EMBED_LLM)
    backend = FakeEmbedBackend()
    _run(root, standard_feeds(), backend=backend)
    assert backend.batches == [3, 3, 1]
    model = "openai_compatible:http://127.0.0.1:11434/v1:bge-m3"
    assert len(load_vectors(root, model)) == 7
    assert load_vectors(root, "openai_compatible:http://127.0.0.1:11434/v1:other") == {}

    data = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    data["llm"]["embed"]["model"] = "qwen3-embedding"
    (root / "config.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    backend = FakeEmbedBackend()
    _run(root, standard_feeds(), backend=backend)
    assert sum(backend.batches) == 7  # 换了模型，全部重算


def test_F11_08_store_tolerates_damage(tmp_path: Path) -> None:
    item = Item("https://a", "A", "", "P", NOW, "https://f")
    (tmp_path / "config.yaml").write_text("schema: 1\n", encoding="utf-8")
    save_items(tmp_path, [item])
    assert load_items(tmp_path) == [item]
    save_items(tmp_path, [])  # 整体替换
    assert load_items(tmp_path) == []
    old = Item("https://old", "O", "", "P", NOW - dt.timedelta(days=9), "https://f")
    dup = Item("https://a", "dup", "", "P", NOW, "https://f")
    assert merge_items([item, old], [dup], keep_after=NOW - dt.timedelta(days=3)) == [item]

    assert load_vectors(tmp_path, "m") == {}


def test_F11_08_mismatched_vectors_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不同维度"):
        save_vectors(tmp_path, "m", {"a": [1.0, 2.0], "b": [1.0]})
    save_vectors(tmp_path, "m", {"a": [1.0, 2.0]})
    assert load_vectors(tmp_path, "m") == {"a": [1.0, 2.0]}
    assert load_vectors(tmp_path, "other-model") == {}
    save_vectors(tmp_path, "other-model", {"b": [3.0]})  # 换模型：旧模型的向量一起清掉
    assert load_vectors(tmp_path, "m") == {}


def test_F11_05_tracking_parameters_do_not_duplicate_sources() -> None:
    from eia.headlines.urls import url_key

    base = "https://www.bbc.co.uk/news/articles/abc"
    assert url_key(base + "?at_medium=RSS&at_campaign=rss") == url_key(base)
    assert url_key("HTTPS://WWW.BBC.CO.UK:443/news/articles/abc/?utm_source=x") == url_key(base)
    assert url_key(base + "?id=1&fbclid=z") == base + "?id=1"
    assert url_key("https://a.example/x?page=2") != url_key("https://a.example/x?page=3")
    assert url_key("http://[bad") == "http://[bad"
    one = Item(base + "?at_medium=RSS", "t", "", "BBC", NOW, "f")
    two = Item(base, "t", "", "BBC", NOW, "f")
    assert len(merge_items([one], [two], keep_after=NOW - dt.timedelta(days=1))) == 1
