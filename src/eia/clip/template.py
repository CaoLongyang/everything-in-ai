"""`eia clip template`：生成 Obsidian Web Clipper 的模板，剪下的网页直接符合 vault-schema。

规格：docs/specs/web-clip.md（第 3.1 节是插件的实际行为和这里的写法依据）。

插件没有随机数，也不能把时间转成 UTC，所以：
- id 后缀用 Unix 秒数（与时区无关），数字换成字母；
- UTC 日期用 `date_modify` 减去时差（它只返回日期，正好是 UTC 日期）；
- UTC 小时用两轮字面替换：本地小时 → UTC 小时的字母编码 → 数字。
时差在生成模板时按 vault 的时区写死。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eia.schema.io import atomic_write
from eia.schema.model import EntryType
from eia.schema.vault import dir_for, load_config

TEMPLATE_NAME = "eia 剪藏"
DEFAULT_FILE = "eia-clipper.json"
SCHEMA_VERSION = "0.1.0"  # 插件导出模板时写的版本（obsidian-clipper import-export.ts）
CLIP_TAG = "clip"
PROPERTY_TYPES = ("text", "multitext", "number", "checkbox", "date", "datetime")
_LETTERS = "abcdefghij"  # 数字 0–9 对应的字母，都在 id 允许的 [a-z2-7] 里


class ClipError(Exception):
    """没法为这个时区生成模板。"""


@dataclass(frozen=True)
class TemplateResult:
    template: dict[str, Any]
    offset_hours: int
    zone: str
    has_dst: bool


def _quoted_pairs(pairs: list[tuple[str, str]]) -> str:
    return "(" + ",".join(f'"{a}":"{b}"' for a, b in pairs) + ")"


def _encode(text: str) -> str:
    return "".join(_LETTERS[int(ch)] for ch in text)


DIGITS_TO_LETTERS = _quoted_pairs([(str(d), _LETTERS[d]) for d in range(10)])
LETTERS_TO_DIGITS = _quoted_pairs([(_LETTERS[d], str(d)) for d in range(10)])


def hour_pairs(offset_hours: int) -> str:
    """本地两位小时 → UTC 小时的字母编码。输出全是字母，后面的组不会再命中。"""
    return _quoted_pairs(
        [(f"{h:02d}", _encode(f"{(h - offset_hours) % 24:02d}")) for h in range(24)]
    )


def utc_date_expr(offset_hours: int) -> str:
    """UTC 日期 YYYY-MM-DD。"""
    if offset_hours == 0:
        return '{{time|date:"YYYY-MM-DD"}}'
    sign = "-" if offset_hours > 0 else "+"
    return f'{{{{time|date_modify:"{sign}{abs(offset_hours)} hours"}}}}'


def _utc_date_compact(offset_hours: int) -> str:
    return utc_date_expr(offset_hours)[:-2] + '|replace:"-":""}}'


def id_suffix_expr() -> str:
    """Unix 秒数后 6 位 + 前面的位，换成字母（10 位）。文件名取前 6 位，所以变化快的放前面。"""
    return (
        f'{{{{time|date:"X"|slice:-6|replace:{DIGITS_TO_LETTERS}}}}}'
        f'{{{{time|date:"X"|slice:0,-6|replace:{DIGITS_TO_LETTERS}}}}}'
    )


def created_expr(offset_hours: int) -> str:
    hour = f'{{{{time|date:"HH"|replace:{hour_pairs(offset_hours)}|replace:{LETTERS_TO_DIGITS}}}}}'
    return f'{utc_date_expr(offset_hours)}T{hour}:{{{{time|date:"mm:ss"}}}}Z'


def build_template(offset_hours: int) -> dict[str, Any]:
    """字段顺序和插件导出的一致，插件能直接导入。"""
    properties = [
        ("schema", "1", "number"),
        ("id", f"clip-{_utc_date_compact(offset_hours)}-{id_suffix_expr()}", "text"),
        ("type", EntryType.clip.value, "text"),
        ("title", "{{title}}", "text"),
        ("created", created_expr(offset_hours), "datetime"),
        ("status", "inbox", "text"),
        ("source", "{{domain}}", "text"),
        ("url", "{{url}}", "text"),
        ("author", "{{author}}", "text"),
        ("published", "{{published}}", "text"),
        ("tags", CLIP_TAG, "multitext"),
    ]
    name_suffix = f'{{{{time|date:"X"|slice:-6|replace:{DIGITS_TO_LETTERS}}}}}'
    return {
        "schemaVersion": SCHEMA_VERSION,
        "name": TEMPLATE_NAME,
        "behavior": "create",
        "noteContentFormat": "{{content}}",
        "properties": [{"name": n, "value": v, "type": t} for n, v, t in properties],
        "triggers": [],
        "noteNameFormat": f'{{{{time|date:"YYYY-MM-DD"}}}}-{{{{title|safe_name}}}}-{name_suffix}',
        "path": dir_for(EntryType.clip.value),
    }


def _offset(tz: dt.tzinfo | None, when: dt.datetime) -> dt.timedelta:
    local = when.astimezone(tz) if tz is not None else when.astimezone()
    offset = local.utcoffset()
    assert offset is not None
    return offset


def zone_offset(tz: dt.tzinfo | None, now: dt.datetime) -> tuple[int, bool]:
    """当前时差（整小时）和这个时区一年里时差会不会变（夏令时）。"""
    offset = _offset(tz, now)
    seconds = int(offset.total_seconds())
    if seconds % 3600:
        raise ClipError(
            f"时区的时差是 {offset}，不是整小时，模板没法换算 UTC 时间"
            "（插件不能转 UTC，模板只能按整小时换算）"
        )
    year = now.year
    samples = {
        _offset(tz, dt.datetime(year, month, 15, 12, tzinfo=dt.UTC)) for month in (1, 4, 7, 10)
    }
    return seconds // 3600, len(samples) > 1


def generate(root: Path, *, now: dt.datetime | None = None) -> TemplateResult:
    tz = load_config(root).timezone
    now = now or dt.datetime.now(dt.UTC)
    hours, has_dst = zone_offset(tz, now)
    zone = str(tz) if tz is not None else "本机时区"
    return TemplateResult(build_template(hours), hours, zone, has_dst)


def write_template(result: TemplateResult, path: Path) -> None:
    atomic_write(path, json.dumps(result.template, ensure_ascii=False, indent="\t") + "\n")
