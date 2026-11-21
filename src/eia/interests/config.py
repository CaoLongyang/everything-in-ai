"""config.yaml 的 interests 段和 digest 段（docs/specs/interest-digest.md 第 3、5、9 节）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from eia.schema.vault import CONFIG_NAME, is_vault


class InterestsError(Exception):
    """兴趣或 digest 配置有问题，或者缺少前置步骤。"""


@dataclass(frozen=True)
class Interest:
    name: str
    description: str = ""
    exclude: str = ""
    search: tuple[str, ...] = ()  # 按兴趣搜新闻用的关键词（headline-radar.delta-sources 第 5 节）

    @property
    def text(self) -> str:
        return f"{self.name}：{self.description}" if self.description else self.name

    def as_config(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name}
        if self.description:
            data["description"] = self.description
        if self.exclude:
            data["exclude"] = self.exclude
        if self.search:
            data["search"] = list(self.search)
        return data


def split_terms(value: Any) -> tuple[str, ...]:
    """关键词可以写成列表，也可以写成用顿号、逗号分隔的一行。去掉空的和重复的。"""
    raw = value if isinstance(value, list) else re.split(r"[、,，;；]", str(value or ""))
    terms: list[str] = []
    for term in raw:
        text = " ".join(str(term).split())
        if text and text not in terms:
            terms.append(text)
    return tuple(terms)


@dataclass(frozen=True)
class DigestConfig:
    min_score: float = 0.55
    per_interest: int = 5
    max_items: int = 30


def _section(root: Path, key: str) -> Any:
    if not is_vault(root):
        raise InterestsError(f"{root} 不是一个 vault（找不到 {CONFIG_NAME}），请先运行 eia init")
    try:
        data = yaml.safe_load((root / CONFIG_NAME).read_bytes().decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise InterestsError(f"{CONFIG_NAME} 无法读取：{exc}") from exc
    return data.get(key) if isinstance(data, dict) else None


def load_interests(root: Path) -> list[Interest]:
    """没有配置时返回空列表。"""
    section = _section(root, "interests")
    if section is None:
        return []
    if not isinstance(section, list):
        raise InterestsError("config.yaml 里的 interests 应该是一个列表")
    interests: list[Interest] = []
    for index, item in enumerate(section, start=1):
        if isinstance(item, str) and item.strip():
            interests.append(Interest(item.strip()))
            continue
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            raise InterestsError(f"interests 第 {index} 项缺少 name")
        interests.append(
            Interest(
                name=str(item["name"]).strip(),
                description=str(item.get("description") or "").strip(),
                exclude=str(item.get("exclude") or "").strip(),
                search=split_terms(item.get("search")),
            )
        )
    return interests


def load_digest_config(root: Path) -> DigestConfig:
    section = _section(root, "digest") or {}
    if not isinstance(section, dict):
        raise InterestsError("config.yaml 里的 digest 应该是一组键值")
    defaults = DigestConfig()
    min_score = _number(section, "min_score", defaults.min_score)
    if not 0 < min_score <= 1:
        raise InterestsError("digest.min_score 应该在 0 到 1 之间")
    per_interest = int(_number(section, "per_interest", defaults.per_interest))
    max_items = int(_number(section, "max_items", defaults.max_items))
    if per_interest < 1 or max_items < 1:
        raise InterestsError("digest.per_interest 和 digest.max_items 至少是 1")
    return DigestConfig(min_score, per_interest, max_items)


def _number(section: dict[str, Any], name: str, default: float) -> float:
    value = section.get(name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InterestsError(f"digest.{name} 应该是数字")
    return float(value)
