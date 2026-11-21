"""按组限量地挑选：分数从高到低，每组有上限，最后截总数。

算法改写自 Horizon（https://github.com/Thysrael/Horizon，MIT License，Copyright (c) 2026 Thysrael）
`src/orchestrator.py` 的 `apply_balanced_digest`，版权声明见 THIRD_PARTY_NOTICES.md。
eia 的改动：组就是兴趣，每组上限相同；去掉了配置里的分组映射和日志。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def balanced(
    scored: Sequence[tuple[T, str, float]], *, per_group: int, max_items: int
) -> list[tuple[T, str, float]]:
    """scored 是 (东西, 组名, 分数)。返回保留的，仍按分数从高到低。"""
    ordered = sorted(scored, key=lambda entry: -entry[2])
    counts: dict[str, int] = defaultdict(int)
    selected: list[tuple[T, str, float]] = []
    for entry in ordered:
        group = entry[1]
        if counts[group] >= per_group:
            continue
        selected.append(entry)
        counts[group] += 1
        if len(selected) >= max_items:
            break
    return selected
