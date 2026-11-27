"""网站接口返回的对话 JSON → 平台无关的 Conversation（docs/specs/chat-sync.md 第 4.3 节）。

ChatGPT、Claude 网页接口返回的单个对话，和官方导出包里每个对话的结构基本一样，
所以直接用导入官方导出包的解析函数，规则（取哪个分支、哪些内容不导入、附件占位）完全相同。

Gemini 的接口（batchexecute）返回没有字段名的嵌套数组。插件把一个对话的「轮」原样发来，
这里按作者的 gemini-chat-exporter（MIT）实测的位置拼成和它导出的 JSON 一样的结构，
再交给导入 Gemini 导出包的解析函数（docs/specs/chat-sync.delta-gemini.md）。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable
from typing import Any

from eia.chat import chatgpt, claude, gemini
from eia.chat.model import Conversation, ExportFormatError

PLATFORMS = ("chatgpt", "claude", "gemini")
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
    elif platform == "gemini":
        return _parse(gemini.parse_conversation, gemini_record(data), "Gemini 网页")
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


def gemini_record(data: Any) -> dict[str, Any]:
    """插件发来的 {conversationId, title, updated, url, turns} → Gemini 导出 JSON 的结构。

    每一轮（gemini-chat-exporter 的 DEVELOPMENT.md 第 13 节）：
    [0]=[c_id, r_id]  [2][0][0]=提问  [3][0]=回答候选（[0]=编号, [1]=正文段落）
    [3][3]=选中的候选编号  [4]=[秒, 纳秒]。同一轮的提问和回答共用这个时间。
    """
    if not isinstance(data, dict):
        raise SyncFormatError("Gemini 网页：每个对话应为一个对象")
    turns = data.get("turns")
    if not isinstance(turns, list):
        raise SyncFormatError("Gemini 网页：缺少 turns（网站的格式可能变了）")
    dated = [(stamp, turn) for turn in turns if (stamp := _stamp(_at(turn, 4))) is not None]
    if len(dated) != len(turns):
        raise SyncFormatError("Gemini 网页：有一轮没有时间（网站的格式可能变了）")
    messages: list[dict[str, Any]] = []
    for stamp, turn in sorted(dated, key=lambda pair: pair[0]):
        time = _iso(stamp)
        question = _at(turn, 2, 0, 0)
        if isinstance(question, str) and question:
            messages.append({"role": "user", "time": time, "content": question})
        answer = _answer(_at(turn, 3))
        if answer:
            messages.append({"role": "assistant", "time": time, "content": answer})
    times = [m["time"] for m in messages]
    updated = _stamp(data.get("updated"))
    return {
        "conversationId": data.get("conversationId"),
        "title": data.get("title"),
        "url": data.get("url"),
        "createdAt": times[0] if times else _iso(updated),
        "updatedAt": _iso(updated) if updated is not None else (times[-1] if times else None),
        "messages": messages,
    }


def _answer(response: Any) -> str:
    candidates = _at(response, 0)
    if not isinstance(candidates, list):
        return ""
    chosen = _at(response, 3)
    pick = next((c for c in candidates if _at(c, 0) == chosen and chosen is not None), None)
    pick = pick if pick is not None else (candidates[0] if candidates else None)
    parts = _at(pick, 1)
    if not isinstance(parts, list):
        return ""
    return "\n\n".join(part for part in parts if isinstance(part, str))


def _at(value: Any, *path: int) -> Any:
    for index in path:
        if not isinstance(value, list) or index >= len(value):
            return None
        value = value[index]
    return value


def _stamp(value: Any) -> float | None:
    """[秒, 纳秒] 或秒数 → 秒数。"""
    if isinstance(value, list) and value and isinstance(value[0], int | float):
        nanos = value[1] if len(value) > 1 and isinstance(value[1], int | float) else 0
        return float(value[0]) + float(nanos) / 1e9
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _iso(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    return dt.datetime.fromtimestamp(seconds, dt.UTC).isoformat(timespec="milliseconds")
