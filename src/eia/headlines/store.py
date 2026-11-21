"""`.eia/headlines/` 里的暂存：抓到的报道和算好的向量。不是用户文件，可以随时删掉重来。

向量缓存记录模型名和维度；模型或维度变了，旧向量全部作废（SPEC-llm-adapter「给下游的注意」）。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from eia.headlines.feeds import Item
from eia.schema.io import atomic_write

STORE_DIR = Path(".eia") / "headlines"
ITEMS_FILE = "items.jsonl"
VECTORS_FILE = "vectors.json"


def store_dir(root: Path) -> Path:
    return root / STORE_DIR


def load_items(root: Path) -> list[Item]:
    path = store_dir(root) / ITEMS_FILE
    if not path.is_file():
        return []
    items: list[Item] = []
    for line in path.read_bytes().decode("utf-8", errors="replace").splitlines():
        try:
            data = json.loads(line)
            data["published"] = dt.datetime.fromisoformat(data["published"])
            items.append(Item(**data))
        except (ValueError, TypeError, KeyError):
            continue  # 坏掉的行丢掉即可，下次抓取会补回来
    return items


def merge_items(old: Iterable[Item], new: Iterable[Item], keep_after: dt.datetime) -> list[Item]:
    """按链接去重（先到的保留），丢掉太旧的，按发布时间排序。"""
    merged: dict[str, Item] = {}
    for item in [*old, *new]:
        if item.published >= keep_after and item.url not in merged:
            merged[item.url] = item
    return sorted(merged.values(), key=lambda item: (item.published, item.url))


def save_items(root: Path, items: list[Item]) -> None:
    lines = [
        json.dumps({**asdict(item), "published": item.published.isoformat()}, ensure_ascii=False)
        for item in items
    ]
    _write(store_dir(root) / ITEMS_FILE, "".join(line + "\n" for line in lines))


def load_vectors(root: Path, model: str) -> dict[str, list[float]]:
    """只返回同一个模型算出的向量；模型变了返回空。"""
    path = store_dir(root) / VECTORS_FILE
    if not path.is_file():
        return {}
    try:
        data: Any = json.loads(path.read_bytes().decode("utf-8"))
    except ValueError:
        return {}
    if not isinstance(data, dict) or data.get("model") != model:
        return {}
    vectors = data.get("vectors")
    dim = data.get("dim")
    if not isinstance(vectors, dict):
        return {}
    return {
        key: value
        for key, value in vectors.items()
        if isinstance(value, list) and len(value) == dim
    }


def save_vectors(root: Path, model: str, vectors: dict[str, list[float]]) -> None:
    dims = {len(vector) for vector in vectors.values()}
    if len(dims) > 1:
        raise ValueError("同一个模型返回了不同维度的向量")
    payload = {"model": model, "dim": dims.pop() if dims else 0, "vectors": vectors}
    _write(store_dir(root) / VECTORS_FILE, json.dumps(payload))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, text)
