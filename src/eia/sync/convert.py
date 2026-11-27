"""网站接口返回的对话 JSON → 平台无关的 Conversation（docs/specs/chat-sync.md 第 4.3 节）。

ChatGPT、Claude 网页接口返回的单个对话，和官方导出包里每个对话的结构基本一样，
所以直接用导入官方导出包的解析函数，规则（取哪个分支、哪些内容不导入、附件占位）完全相同。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable
from typing import Any

from eia.chat import chatgpt, claude
from eia.chat.model import Conversation, ExportFormatError

PLATFORMS = ("chatgpt", "claude")
_URLS = {
    "chatgpt": "https://chatgpt.com/c/{id}",
    "claude": "https://claude.ai/chat/{id}",
}


class SyncFormatError(Exception):
    """插件发来的内容认不出：平台不认识，或者网站返回的格式变了。"""


def to_conversation(platform: str, data: Any) -> Conversation:
    if platform == "chatgpt":
        parsed = _parse(chatgpt.parse_conversation, data, "ChatGPT 网页")
    elif platform == "claude":
        parsed = _parse(claude.parse_conversation, data, "Claude 网页")
    else:
        raise SyncFormatError(f"不认识的平台：{platform!r}（只支持 {'、'.join(PLATFORMS)}）")
    url = _URLS[platform].format(id=parsed.conversation_id)
    return dataclasses.replace(parsed, url=url)


def list_time(value: Any) -> dt.datetime | None:
    """对话列表里的更新时间：ChatGPT 可能是秒数或 ISO 字符串，Claude 是 ISO 字符串。截到秒。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return dt.datetime.fromtimestamp(value, dt.UTC).replace(microsecond=0)
    if isinstance(value, str) and value:
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.UTC)
        return parsed.astimezone(dt.UTC).replace(microsecond=0)
    return None


def _parse(parse: Callable[[Any, str], Conversation], data: Any, where: str) -> Conversation:
    try:
        conversation = parse(data, where)
    except ExportFormatError as exc:
        raise SyncFormatError(f"{exc}（网站的格式可能变了）") from exc
    return conversation
