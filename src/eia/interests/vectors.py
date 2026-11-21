"""文本向量缓存（`.eia/interests/vectors.json`），按模型作废。对话、兴趣说明、评价过的标题都用它。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from eia import llm
from eia.interests.config import InterestsError
from eia.schema.io import atomic_write

CACHE = Path(".eia") / "interests" / "vectors.json"
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
    path = root / CACHE
    cache: dict[str, list[float]] = {}
    if path.is_file():
        try:
            data: Any = json.loads(path.read_bytes().decode("utf-8"))
        except ValueError:
            data = {}
        if isinstance(data, dict) and data.get("model") == endpoint.cache_key:
            cache = {k: v for k, v in (data.get("vectors") or {}).items() if isinstance(v, list)}
    keys = [_key(text) for text in texts]
    missing = sorted(
        {key: text for key, text in zip(keys, texts, strict=True) if key not in cache}.items()
    )
    for start in range(0, len(missing), BATCH):
        batch = missing[start : start + BATCH]
        for (key, _), vector in zip(batch, client.embed([text for _, text in batch]), strict=True):
            cache[key] = vector
    if save and missing:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps({"model": endpoint.cache_key, "vectors": cache}))
    return [cache[key] for key in keys]
