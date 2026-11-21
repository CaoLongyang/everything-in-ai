"""文本向量缓存（数据库的 vectors 表，空间 text），按模型作废。

对话、兴趣说明、评价过的标题都用它。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from eia import llm
from eia.db import database
from eia.db import vectors as vec
from eia.interests.config import InterestsError

SPACE = "text"
BATCH = 32


def embed_client(root: Path, *, require_local: bool = False) -> llm.Client:
    try:
        client = llm.get_client(root)
    except llm.LLMConfigError as exc:
        raise InterestsError(
            f"需要在 llm.embed 配置向量模型（例如本机 Ollama 的 bge-m3）：{exc}"
        ) from exc
    endpoint = client.config.embed
    if endpoint is None:
        raise InterestsError("需要在 llm.embed 配置向量模型（例如本机 Ollama 的 bge-m3）")
    if require_local and not endpoint.is_local:
        raise InterestsError(
            f"向量模型不在本机（{endpoint.api_base}）。分析对话的第一步必须在本机做，"
            "否则你说过的所有话都会发出去。请把 llm.embed 配成本机 Ollama"
        )
    return client


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def embed_texts(
    root: Path, client: llm.Client, texts: Sequence[str], *, save: bool = True
) -> list[list[float]]:
    endpoint = client.config.embed
    assert endpoint is not None
    keys = [_key(text) for text in texts]
    with database(root, readonly=not save) as conn:
        cache = vec.get(conn, endpoint.cache_key, SPACE, keys)
    missing = sorted(
        {key: text for key, text in zip(keys, texts, strict=True) if key not in cache}.items()
    )
    new: dict[str, list[float]] = {}
    for start in range(0, len(missing), BATCH):
        batch = missing[start : start + BATCH]
        for (key, _), vector in zip(batch, client.embed([text for _, text in batch]), strict=True):
            new[key] = vector
    if save and new:
        with database(root) as conn:
            vec.put(conn, endpoint.cache_key, SPACE, new)
    cache.update(new)
    return [cache[key] for key in keys]
