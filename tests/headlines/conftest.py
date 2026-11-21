"""新闻雷达测试的公共工具。所有新闻都是虚构的；网络全部被替换，不连真实网站。"""

from __future__ import annotations

import datetime as dt
import urllib.error
from collections.abc import Callable
from email.message import Message
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from eia.llm._base import Reply
from eia.schema.vault import CONFIG_NAME, init_vault

NOW = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.UTC)

# 每个话题一个方向：假的向量模型按关键词给出向量，同一话题的报道（不分语言）彼此很像
TOPICS: dict[str, tuple[str, ...]] = {
    "quake": ("earthquake", "地震", "强震"),
    "summit": ("summit", "峰会"),
    "phone": ("iphone", "手机"),
    "football": ("cup final", "决赛"),
}


def rss(entries: list[tuple[str, str, dt.datetime | None]], *, summary: str = "") -> bytes:
    items = []
    for title, link, when in entries:
        date = f"<pubDate>{format_datetime(when)}</pubDate>" if when else ""
        items.append(
            f"<item><title>{title}</title><link>{link}</link>"
            f"<description>{summary or '&lt;p&gt;摘要 &amp; summary&lt;/p&gt;'}</description>"
            f"{date}</item>"
        )
    body = "".join(items)
    head = '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
    return f"{head}{body}</channel></rss>".encode()


def atom(entries: list[tuple[str, str, dt.datetime]]) -> bytes:
    items = "".join(
        f'<entry><title>{title}</title><link href="{link}"/>'
        f"<updated>{when.strftime('%Y-%m-%dT%H:%M:%SZ')}</updated><summary>s</summary></entry>"
        for title, link, when in entries
    )
    return (
        f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>{items}</feed>'
    ).encode()


def rdf_without_dates(entries: list[tuple[str, str]]) -> bytes:
    items = "".join(
        f'<item rdf:about="{link}"><title>{title}</title><link>{link}</link></item>'
        for title, link in entries
    )
    return (
        '<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns="http://purl.org/rss/1.0/"><channel rdf:about="x"><title>t</title></channel>'
        f"{items}</rdf:RDF>"
    ).encode()


def hours_ago(hours: float) -> dt.datetime:
    return NOW - dt.timedelta(hours=hours)


FEEDS = [
    {"publisher": "BBC", "url": "https://bbc.example/world.xml"},
    {"publisher": "BBC", "url": "https://bbc.example/zh.xml"},
    {"publisher": "Guardian", "url": "https://guardian.example/rss"},
    {"publisher": "Al Jazeera", "url": "https://aj.example/all.xml"},
    {"publisher": "DW", "url": "https://dw.example/rdf"},
]

EMBED_LLM: dict[str, Any] = {
    "provider": "openai_compatible",
    "model": "deepseek-v4-flash",
    "api_base": "https://api.deepseek.com",
    "embed": {
        "provider": "openai_compatible",
        "model": "bge-m3",
        "api_base": "http://127.0.0.1:11434/v1",
    },
}


def standard_feeds() -> dict[str, bytes | Exception]:
    """quake：BBC 两个源 + Guardian + Al Jazeera（3 家）；summit：只有 2 家；phone：1 家。"""
    return {
        "https://bbc.example/world.xml": rss(
            [
                ("Strong earthquake hits southern Japan", "https://bbc.example/1", hours_ago(5)),
                ("Leaders gather for climate summit", "https://bbc.example/2", hours_ago(4)),
            ]
        ),
        "https://bbc.example/zh.xml": rss(
            [("日本南部发生强烈地震", "https://bbc.example/zh/1", hours_ago(4.5))]
        ),
        "https://guardian.example/rss": rss(
            [
                ("Earthquake in Japan leaves dozens injured", "https://gu.example/1", hours_ago(3)),
                ("Climate summit opens amid protests", "https://gu.example/2", hours_ago(2)),
            ]
        ),
        "https://aj.example/all.xml": atom(
            [("Japan earthquake: rescue under way", "https://aj.example/1", hours_ago(2))]
        ),
        "https://dw.example/rdf": rdf_without_dates(
            [("New iPhone unveiled", "https://dw.example/1")]
        ),
    }


@pytest.fixture
def make_vault(tmp_path: Path) -> Callable[..., Path]:
    def _make(
        headlines: dict[str, Any] | None = None,
        *,
        llm: dict[str, Any] | None = None,
        cluster: str = "embed",
    ) -> Path:
        root = tmp_path / "vault"
        init_vault(root)
        data: dict[str, Any] = {"schema": 1, "timezone": "Asia/Singapore"}
        if llm is not None:
            data["llm"] = llm
        data["headlines"] = (
            headlines
            if headlines is not None
            else {"feeds": FEEDS, "min_sources": 3, "cluster": cluster}
        )
        text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        (root / CONFIG_NAME).write_bytes(text.encode("utf-8"))
        return root

    return _make


class FakeEmbedBackend:
    """按 TOPICS 的关键词给向量：同一话题很像，不同话题几乎正交。记录每次调用的文本数。"""

    def __init__(self) -> None:
        self.batches: list[int] = []

    def complete(self, prompt: str, system: str | None, json_schema: Any) -> Reply:
        raise AssertionError("新闻雷达不应该调用文本模型")

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(len(texts))
        return [_vector(text) for text in texts]


def _vector(text: str) -> list[float]:
    lowered = text.lower()
    vector = [0.0] * (len(TOPICS) + 1)
    for index, words in enumerate(TOPICS.values()):
        if any(word in lowered for word in words):
            vector[index] = 1.0
    vector[-1] = 0.2 + (len(text) % 7) * 0.01  # 一点"噪声"，让向量不完全相同
    return vector


def fake_fetcher(table: dict[str, bytes | Exception]) -> Callable[[str], bytes]:
    def _fetch(url: str) -> bytes:
        value = table[url]
        if isinstance(value, Exception):
            raise value
        return value

    return _fetch


class FakeResponse:
    def __init__(self, data: bytes, status: int = 200) -> None:
        self._data = data
        self.status = status

    def read(self, limit: int) -> bytes:
        return self._data[:limit]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def fake_urlopen(table: dict[str, bytes | Exception]) -> Callable[..., FakeResponse]:
    def _open(request: Any, timeout: float) -> FakeResponse:
        value = table[request.full_url]
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)

    return _open


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "boom", Message(), None)
