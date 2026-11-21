"""把对话渲染成正文，以及在已有文件里替换对话部分。

正文格式是契约（docs/specs/chat-import.md 第 5 节）：
两条标记之间归程序；每条消息以 `### 我 · 时间` 或 `### <平台名> · 时间` 开头。
"""

from __future__ import annotations

import datetime as dt
import re

from eia.chat.model import Conversation, Message

START = "<!-- eia:conversation:start 这一段由 eia import 生成，重新导入时会被替换 -->"
END = "<!-- eia:conversation:end -->"
START_RE = re.compile(r"^<!-- eia:conversation:start\b.*-->[ \t]*\r?$", re.MULTILINE)
END_RE = re.compile(r"^<!-- eia:conversation:end -->[ \t]*\r?$", re.MULTILINE)
NOTES_HINT = "（这条线以下是你自己的笔记，重新导入不会动）"
THINKING_TITLE = "> [!note]- 思考过程"

SPEAKERS = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini"}
USER_LABEL = "我"
_HEADING_LABELS = (USER_LABEL, *SPEAKERS.values())
_LOOKS_SPECIAL = re.compile(
    r"^(?=### (?:" + "|".join(map(re.escape, _HEADING_LABELS)) + r") · |<!-- eia:)", re.MULTILINE
)


class MarkerError(Exception):
    """文件里的两条标记缺失、重复或顺序不对。"""


def escape_text(text: str) -> str:
    """消息里如果有行看起来像消息标题或标记，在行首加反斜杠。"""
    return _LOOKS_SPECIAL.sub("\\\\", text)


def speaker(conversation: Conversation, message: Message) -> str:
    if message.role == "user":
        return USER_LABEL
    return SPEAKERS.get(conversation.platform, conversation.platform)


def thinking_callout(text: str) -> str:
    """思考过程放进 Obsidian 默认收起的折叠块。每行都带 `> `，不会被误认成消息标题或标记。"""
    lines = text.replace("\r\n", "\n").split("\n")
    return "\n".join([THINKING_TITLE, *(f"> {line}" if line else ">" for line in lines)])


def _local(when: dt.datetime, tz: dt.tzinfo | None) -> str:
    return when.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def render_block(conversation: Conversation, tz: dt.tzinfo | None) -> str:
    """生成两条标记以及中间的对话，不含末尾换行。"""
    pieces = [START, ""]
    for message in conversation.messages:
        pieces += [f"### {speaker(conversation, message)} · {_local(message.time, tz)}", ""]
        if message.thinking:
            pieces += [thinking_callout(message.thinking), ""]
        pieces += [escape_text(message.text.replace("\r\n", "\n")), ""]
    pieces.append(END)
    return "\n".join(pieces)


def new_body(conversation: Conversation, tz: dt.tzinfo | None) -> str:
    return f"{render_block(conversation, tz)}\n\n{NOTES_HINT}\n"


def replace_block(body: str, conversation: Conversation, tz: dt.tzinfo | None) -> str:
    """只替换两条标记（含标记本身）之间的内容；标记之外逐字节不变。"""
    starts, ends = list(START_RE.finditer(body)), list(END_RE.finditer(body))
    if len(starts) != 1 or len(ends) != 1 or starts[0].start() > ends[0].start():
        raise MarkerError("对话标记缺失、重复或顺序不对")
    start, end = starts[0], ends[0]
    block = render_block(conversation, tz)
    if start.group(0).endswith("\r"):
        block = block.replace("\n", "\r\n")
    end_pos = end.end() - 1 if end.group(0).endswith("\r") else end.end()
    return body[: start.start()] + block + body[end_pos:]
