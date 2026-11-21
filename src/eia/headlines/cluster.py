"""把讲同一件事的报道归成一组（SPEC-headline-radar 第 4 节第 3 步）。

两种做法都是按时间顺序贪心归组：每条报道和已有的组比，最像的那组够像就并进去，否则自成一组。
- embed：和组的平均向量比余弦相似度，能跨语言。
- lexical：和组里每一条比词的重合度（Jaccard），取最大。中文按相邻两个字切，英文按词切。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from eia.headlines.feeds import Item

if TYPE_CHECKING:  # numpy 在函数里才导入：没装 [news] 的人也能正常使用 eia 的其他命令
    import numpy as np

_WORD = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[㐀-鿿]+")
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or says that the to was "
    "were will with after over new".split()
)


def embed_text(item: Item) -> str:
    return f"{item.title}\n{item.summary[:300]}"


def cluster_by_vectors(
    items: Sequence[Item], vectors: Sequence[Sequence[float]], threshold: float
) -> list[list[Item]]:
    import numpy as np

    if len(items) != len(vectors):
        raise ValueError("报道数量和向量数量不一致")
    groups: list[list[Item]] = []
    sums: list[np.ndarray] = []
    centroids = np.zeros((0, 0))
    for item, raw in zip(items, vectors, strict=True):
        vector = np.asarray(raw, dtype=float)
        norm = float(np.linalg.norm(vector))
        vector = vector / norm if norm else vector
        if groups:
            scores = centroids @ vector
            best = int(np.argmax(scores))
            if scores[best] >= threshold:
                groups[best].append(item)
                sums[best] = sums[best] + vector
                centroids[best] = _unit(sums[best])
                continue
        groups.append([item])
        sums.append(vector)
        centroids = np.vstack([centroids, vector]) if centroids.size else vector[None, :]
    return groups


def cluster_by_words(items: Sequence[Item], threshold: float) -> list[list[Item]]:
    groups: list[list[Item]] = []
    group_tokens: list[list[frozenset[str]]] = []
    for item in items:
        tokens = tokenize(f"{item.title} {item.summary}")
        best, best_score = -1, 0.0
        for index, members in enumerate(group_tokens):
            score = max(_jaccard(tokens, other) for other in members)
            if score > best_score:
                best, best_score = index, score
        if best >= 0 and best_score >= threshold:
            groups[best].append(item)
            group_tokens[best].append(tokens)
        else:
            groups.append([item])
            group_tokens.append([tokens])
    return groups


def tokenize(text: str) -> frozenset[str]:
    lowered = text.lower()
    words = {w for w in _WORD.findall(lowered) if len(w) > 1 and w not in _STOPWORDS}
    for run in _CJK.findall(lowered):
        words.update(run[i : i + 2] for i in range(len(run) - 1))
        if len(run) == 1:
            words.add(run)
    return frozenset(words)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _unit(vector: np.ndarray) -> np.ndarray:
    import numpy as np

    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector
