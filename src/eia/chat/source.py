"""标出「我」消息里每段文字的来源（docs/specs/chat-reader.delta-paste.md）。

只标「这些字是谁写的」，不判断态度，不删内容。规则只认有证据的来源：
贴回的 AI 回答、代码块、报错、附件占位；其他都算我写的。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from eia.chat.reader import ReadChat

Source = Literal["own", "ai", "code", "error", "attachment"]
SOURCES: tuple[Source, ...] = ("own", "ai", "code", "error", "attachment")
SOURCE_LABELS: dict[Source, str] = {
    "own": "我写的",
    "ai": "来自 AI",
    "code": "代码",
    "error": "报错",
    "attachment": "附件",
}

MIN_ECHO_CHARS = 20  # 去掉空白和符号后少于这么多字的段不比较，防止「好的」碰巧一样

_FENCE = re.compile(r"^```.*?^```[ \t]*$", re.MULTILINE | re.DOTALL)
_GAP = re.compile(r"(\n[ \t]*\n(?:[ \t]*\n)*)")
_NOISE = re.compile(r"[\s*_`#>|\-]+")
_ERROR_LINE = re.compile(
    r"^(?:Traceback \(most recent call last\):"
    r'|\s+File ".*", line \d+'
    r"|\s+at [\w$.<>/]+\(.*\)"
    r"|Exception in thread "
    r"|error(?:\[E\d+\])?: "
    r"|(?:[\w$]+\.)*\w*(?:Error|Exception)(?:: |:?$))"
)
_ATTACHMENT_LINE = re.compile(
    r"^\[(?:附件：.*|文件|文件：.*|图片：.*)\]$"
)  # chat/claude.py 写的占位


@dataclass(frozen=True)
class Part:
    """一段文字和它的来源。text 带着它后面的空行，所有段直接连起来就是原消息。"""

    source: Source
    text: str


def normalize(text: str) -> str:
    """比较用：去掉空白和 Markdown 符号。复制网页上渲染好的回答时，这些符号会丢掉。"""
    return _NOISE.sub("", text)


def split_message(text: str, earlier_ai: str) -> tuple[Part, ...]:
    """把一条「我」的消息切段并标来源。earlier_ai 是这条消息之前所有 AI 回答经 normalize 后的文字。

    相邻、来源相同的段合并成一段。
    """
    parts: list[Part] = []
    leading = ""  # 消息开头的空白，并进第一段
    for chunk, is_code in _chunks(text):
        if not chunk.strip():
            if parts:
                parts[-1] = Part(parts[-1].source, parts[-1].text + chunk)
            else:
                leading += chunk
            continue
        source = _source(chunk, is_code, earlier_ai)
        if parts and source == parts[-1].source:
            parts[-1] = Part(source, parts[-1].text + chunk)
        else:
            parts.append(Part(source, leading + chunk))
            leading = ""
    if leading:
        parts.append(Part("own", leading))
    return tuple(parts)


def label_chat(chat: ReadChat) -> tuple[tuple[Part, ...] | None, ...]:
    """和 chat.messages 一一对应：「我」的消息给出分段，AI 的消息是 None。"""
    earlier: list[str] = []
    labels: list[tuple[Part, ...] | None] = []
    for message in chat.messages:
        if message.role == "assistant":
            earlier.append(normalize(message.text))
            labels.append(None)
        else:
            labels.append(split_message(message.text, "\0".join(earlier)))
    return tuple(labels)


def text_of(parts: Sequence[Part], *, sources: Sequence[Source]) -> str:
    """只拼出指定来源的段。"""
    return "\n\n".join(p.text.strip() for p in parts if p.source in sources and p.text.strip())


def _chunks(text: str) -> list[tuple[str, bool]]:
    """按空行切段，代码块整块一段；每段带着它后面的空白。"""
    pieces: list[tuple[str, bool]] = []
    position = 0
    for fence in _FENCE.finditer(text):
        pieces.extend(_paragraphs(text[position : fence.start()]))
        pieces.append((fence.group(), True))
        position = fence.end()
    pieces.extend(_paragraphs(text[position:]))
    return pieces


def _paragraphs(text: str) -> list[tuple[str, bool]]:
    tokens = _GAP.split(text)
    out: list[tuple[str, bool]] = []
    for index in range(0, len(tokens), 2):
        gap = tokens[index + 1] if index + 1 < len(tokens) else ""
        if tokens[index] + gap:
            out.append((tokens[index] + gap, False))
    return out


def _source(chunk: str, is_code: bool, earlier_ai: str) -> Source:
    key = normalize(chunk)
    if len(key) >= MIN_ECHO_CHARS and key in earlier_ai:
        return "ai"
    if is_code:
        return "code"
    lines = [line for line in chunk.strip().split("\n") if line.strip()]
    if _has_error_run(lines):
        return "error"
    if lines and all(_ATTACHMENT_LINE.match(line.strip()) for line in lines):
        return "attachment"
    return "own"


def _has_error_run(lines: list[str]) -> bool:
    run = 0
    for line in lines:
        run = run + 1 if _ERROR_LINE.match(line) else 0
        if run >= 2:
            return True
    return False
