"""新闻雷达的暂存：抓到的报道和算好的向量，存在 vault 的数据库里（docs/specs/storage.md）。

不是用户写的内容，可以随时删掉重来。向量缓存按模型名作废（docs/specs/llm-adapter.md「给下游的注意」）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from pathlib import Path

from eia.db import database
from eia.db import vectors as vec
from eia.headlines.feeds import Item
from eia.headlines.urls import url_key

SPACE = "item"


def load_items(root: Path, *, readonly: bool = False) -> list[Item]:
    with database(root, readonly=readonly) as conn:
        rows = conn.execute(
            "SELECT url, title, summary, publisher, published, feed_url FROM items"
            " ORDER BY published, url"
        ).fetchall()
    return [
        Item(
            url=r["url"],
            title=r["title"],
            summary=r["summary"],
            publisher=r["publisher"],
            published=dt.datetime.fromisoformat(r["published"]),
            feed_url=r["feed_url"],
        )
        for r in rows
    ]


def merge_items(old: Iterable[Item], new: Iterable[Item], keep_after: dt.datetime) -> list[Item]:
    """按规范化后的链接去重（先到的保留），丢掉太旧的，按发布时间排序。"""
    merged: dict[str, Item] = {}
    for item in [*old, *new]:
        key = url_key(item.url)
        if item.published >= keep_after and key not in merged:
            merged[key] = item
    return sorted(merged.values(), key=lambda item: (item.published, item.url))


def save_items(root: Path, items: list[Item]) -> None:
    """暂存的报道整体替换成 items。"""
    rows = [
        (
            url_key(i.url),
            i.url,
            i.title,
            i.summary,
            i.publisher,
            i.published.astimezone(dt.UTC).isoformat(),
            i.feed_url,
        )
        for i in items
    ]
    with database(root) as conn:
        conn.execute("DELETE FROM items")
        conn.executemany("INSERT OR IGNORE INTO items VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def load_vectors(root: Path, model: str, *, readonly: bool = False) -> dict[str, list[float]]:
    """只返回同一个模型算出的向量；模型变了返回空。"""
    with database(root, readonly=readonly) as conn:
        return vec.get(conn, model, SPACE)


def save_vectors(root: Path, model: str, vectors: dict[str, list[float]]) -> None:
    with database(root) as conn:
        vec.replace_space(conn, model, SPACE, vectors)
