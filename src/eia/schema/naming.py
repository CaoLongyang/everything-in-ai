"""文件名规则：`YYYY-MM-DD-<slug>-<id后缀6位>.md`，在 Windows 上也合法。"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata

from eia.schema.ids import short_suffix

MAX_SLUG_BYTES = 60
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_SEPARATORS = re.compile(r"[\s\-]+")


def slugify(title: str, max_bytes: int = MAX_SLUG_BYTES) -> str:
    """把标题变成可用于文件名的片段：保留中文，去掉 Windows 保留字符，限制字节数。"""
    text = unicodedata.normalize("NFC", title)
    text = _FORBIDDEN.sub(" ", text)
    text = _SEPARATORS.sub("-", text).strip("-. ")
    encoded = text.encode("utf-8")
    if len(encoded) > max_bytes:
        text = encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip("-. ")
    return text or "untitled"


def entry_filename(title: str, entry_id: str, created: dt.datetime, tz: dt.tzinfo | None) -> str:
    """文件名里的日期按 vault 配置的时区计算；tz 为 None 时用本机时区。"""
    local_date = created.astimezone(tz).date().isoformat()
    return f"{local_date}-{slugify(title)}-{short_suffix(entry_id)}.md"
