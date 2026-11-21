"""去重用的链接规范化：同一篇报道带不同的跟踪参数时，算同一个链接。

改写自 Horizon（https://github.com/Thysrael/Horizon，MIT License，Copyright (c) 2026 Thysrael）
`src/orchestrator.py` 的 `_deduplication_url_key`，版权声明见 THIRD_PARTY_NOTICES.md。
eia 的改动：返回字符串而不是元组；另外去掉 BBC 等 RSS 带的 `at_medium`、`at_campaign`。
"""

from __future__ import annotations

from urllib.parse import unquote_plus, urlsplit

_TRACKING_QUERY_PARAMETERS = frozenset(
    {
        "_ga",
        "dclid",
        "fbclid",
        "gclid",
        "igshid",
        "li_fat_id",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "ttclid",
        "twclid",
        "vero_id",
        # eia 加的：RSS 里常见的来源标记
        "at_medium",
        "at_campaign",
        "traffic_source",
    }
)


def url_key(url: str) -> str:
    """保守的规范化：只去掉跟踪参数、默认端口、末尾斜杠，大小写只改协议和域名。"""
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError:
        return url.strip()
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if (scheme, port) in {("http", 80), ("https", 443)}:
        port = None
    path = parsed.path.rstrip("/") or "/"
    query = [
        part
        for part in (parsed.query.split("&") if parsed.query else [])
        if not _is_tracking(unquote_plus(part.partition("=")[0]).lower())
    ]
    netloc = host if port is None else f"{host}:{port}"
    tail = f"?{'&'.join(query)}" if query else ""
    return f"{scheme}://{netloc}{path}{tail}"


def _is_tracking(name: str) -> bool:
    return name.startswith("utm_") or name in _TRACKING_QUERY_PARAMETERS
