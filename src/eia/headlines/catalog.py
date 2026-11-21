"""eia 自带的推荐源目录（docs/specs/headline-radar.delta-sources.md 第 6 节）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any

import yaml

from eia.headlines.config import DEFAULT_NEWSNOW, Feed, parse_source


@dataclass(frozen=True)
class CatalogSource:
    config: dict[str, Any]  # 原样写进 config.yaml 的 headlines.feeds
    feed: Feed

    @property
    def key(self) -> str:
        return source_key(self.config)


@dataclass(frozen=True)
class CatalogGroup:
    name: str
    note: str
    sources: tuple[CatalogSource, ...]


def source_key(config: dict[str, Any]) -> str:
    """判断两个源是不是同一个：比较去掉空值后的配置（rss 不写 type 和写 type: rss 一样）。"""
    clean = {k: v for k, v in config.items() if v not in (None, "") and k != "type"}
    clean["type"] = config.get("type") or "rss"
    return json.dumps(clean, ensure_ascii=False, sort_keys=True)


def load_catalog() -> tuple[str, list[CatalogGroup]]:
    """返回（实测日期，分组）。目录里每一项都按 config.yaml 的规则解析一遍，写错了直接报错。"""
    text = resources.files("eia.headlines").joinpath("catalog.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    groups: list[CatalogGroup] = []
    for group in data["groups"]:
        sources = tuple(
            CatalogSource(
                dict(item),
                parse_source(
                    item, f"推荐源「{group['name']}」", window=36, newsnow=DEFAULT_NEWSNOW
                ),
            )
            for item in group["sources"]
        )
        groups.append(CatalogGroup(str(group["name"]), str(group.get("note") or ""), sources))
    return str(data["checked"]), groups
