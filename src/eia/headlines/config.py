"""解析 config.yaml 的 headlines 段（docs/specs/headline-radar.md 第 3 节）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from eia.schema.vault import CONFIG_NAME, is_vault

EMBED = "embed"
LEXICAL = "lexical"
# embed 的 0.68 是 2026-11-21 用 13 个真实源 + bge-m3 校准的：0.70 起中英文同一事件会分开
DEFAULT_SIMILARITY = {EMBED: 0.68, LEXICAL: 0.35}


class HeadlinesConfigError(Exception):
    """没有 headlines 配置，或者配置不完整。不会访问网络。"""


@dataclass(frozen=True)
class Feed:
    url: str
    publisher: str


@dataclass(frozen=True)
class HeadlinesConfig:
    feeds: tuple[Feed, ...]
    min_sources: int
    window_hours: float
    cluster: str
    similarity: float


def load_headlines_config(root: Path) -> HeadlinesConfig:
    if not is_vault(root):
        raise HeadlinesConfigError(
            f"{root} 不是一个 vault（找不到 {CONFIG_NAME}），请先运行 eia init"
        )
    try:
        data = yaml.safe_load((root / CONFIG_NAME).read_bytes().decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise HeadlinesConfigError(f"{CONFIG_NAME} 无法读取：{exc}") from exc
    section = data.get("headlines") if isinstance(data, dict) else None
    if section is None:
        raise HeadlinesConfigError(
            f"{CONFIG_NAME} 里没有 headlines 配置。不会访问任何新闻源（模板见 eia init 生成的注释）"
        )
    if not isinstance(section, dict):
        raise HeadlinesConfigError(f"{CONFIG_NAME} 里的 headlines 应该是一组键值")

    feeds = _feeds(section.get("feeds"))
    cluster = str(section.get("cluster") or EMBED)
    if cluster not in DEFAULT_SIMILARITY:
        raise HeadlinesConfigError(
            f"headlines.cluster「{cluster}」无法识别（可选：embed / lexical）"
        )
    similarity = _number(section, "similarity", DEFAULT_SIMILARITY[cluster])
    if not 0 < similarity <= 1:
        raise HeadlinesConfigError("headlines.similarity 应该在 0 到 1 之间")
    min_sources = _number(section, "min_sources", 3)
    if min_sources < 1 or min_sources != int(min_sources):
        raise HeadlinesConfigError("headlines.min_sources 应该是不小于 1 的整数")
    window = _number(section, "window_hours", 36)
    if window <= 0:
        raise HeadlinesConfigError("headlines.window_hours 应该大于 0")
    return HeadlinesConfig(feeds, int(min_sources), window, cluster, similarity)


def _feeds(value: Any) -> tuple[Feed, ...]:
    if not value:
        raise HeadlinesConfigError("headlines.feeds 是空的：至少列出一个新闻源")
    if not isinstance(value, list):
        raise HeadlinesConfigError("headlines.feeds 应该是一个列表")
    feeds: list[Feed] = []
    for index, item in enumerate(value, start=1):
        where = f"headlines.feeds 第 {index} 项"
        if not isinstance(item, dict):
            raise HeadlinesConfigError(f"{where} 应该写成 url: ... publisher: ...")
        url = str(item.get("url") or "").strip()
        publisher = str(item.get("publisher") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise HeadlinesConfigError(f"{where} 缺少 url，或者不是 http(s) 地址")
        if not publisher:
            raise HeadlinesConfigError(f"{where} 缺少 publisher（媒体名，同名的源只算一家）")
        feeds.append(Feed(url, publisher))
    return tuple(feeds)


def _number(section: dict[str, Any], name: str, default: float) -> float:
    value = section.get(name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise HeadlinesConfigError(f"headlines.{name} 应该是数字")
    return float(value)
