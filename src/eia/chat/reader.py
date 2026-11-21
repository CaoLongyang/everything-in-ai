"""从 vault 里已导入的对话文件读回消息，能只取「我」说的话（docs/specs/chat-reader.md）。

只读：不改任何文件。正文格式是 render.py 写出的契约，这里是它的反向。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from eia.chat.render import END_RE, NOTES_HINT, SPEAKERS, START_RE, THINKING_TITLE, USER_LABEL
from eia.schema.io import FrontmatterError, read_document
from eia.schema.model import EntryType
from eia.schema.validate import iter_markdown
from eia.schema.vault import CHAT_DIR

ReadRole = Literal["user", "assistant", "note"]

_LABELS = (USER_LABEL, *SPEAKERS.values())
_LABEL_PATTERN = "|".join(map(re.escape, _LABELS))
_HEADING = re.compile(r"^### (" + _LABEL_PATTERN + r") · (.*)$", re.MULTILINE)
_ESCAPED = re.compile(r"^\\(?=### (?:" + _LABEL_PATTERN + r") · |<!-- eia:)", re.MULTILINE)
_TIME_FORMAT = "%Y-%m-%d %H:%M"


class ChatReadError(Exception):
    """对话文件读不了：frontmatter 坏了，或者对话标记缺失、重复。"""


@dataclass(frozen=True)
class ReadMessage:
    role: ReadRole
    time: dt.datetime | None
    text: str
    thinking: str = ""


@dataclass(frozen=True)
class ReadChat:
    id: str
    path: Path
    title: str
    platform: str
    tags: tuple[str, ...]
    updated: dt.datetime | None
    messages: tuple[ReadMessage, ...]
    notes: str = ""

    def user_messages(self) -> tuple[ReadMessage, ...]:
        return tuple(message for message in self.messages if message.role == "user")


@dataclass(frozen=True)
class Skipped:
    path: Path
    reason: str


def read_chat(path: Path, tz: dt.tzinfo | None) -> ReadChat:
    try:
        document = read_document(path)
    except FrontmatterError as exc:
        raise ChatReadError(str(exc)) from exc
    body = document.body.replace("\r\n", "\n")
    starts, ends = list(START_RE.finditer(body)), list(END_RE.finditer(body))
    if len(starts) != 1 or len(ends) != 1 or starts[0].start() > ends[0].start():
        raise ChatReadError("对话标记缺失、重复或顺序不对")
    start, end = starts[0], ends[0]
    meta = document.meta
    return ReadChat(
        id=str(meta.get("id") or ""),
        path=path,
        title=str(meta.get("title") or ""),
        platform=str(meta.get("platform") or ""),
        tags=_tags(meta.get("tags")),
        updated=_aware(meta.get("updated")),
        messages=_messages(body[start.end() : end.start()], tz),
        notes=_notes(body[: start.start()] + body[end.end() :]),
    )


def is_chat_file(root: Path, path: Path, meta: dict[str, Any] | None) -> bool:
    if meta is not None and meta.get("type") == EntryType.chat.value:
        return True
    parts = path.relative_to(root).parts
    return len(parts) > 1 and parts[0] == CHAT_DIR


def iter_chats(
    root: Path, tz: dt.tzinfo | None, *, platform: str | None = None
) -> Iterator[ReadChat | Skipped]:
    """遍历 vault 里的对话文件。非对话文件直接忽略；坏掉的对话文件给出 Skipped。"""
    for path in iter_markdown(root):
        try:
            meta: dict[str, Any] | None = read_document(path).meta
        except FrontmatterError:
            meta = None
        if not is_chat_file(root, path, meta):
            continue
        if platform is not None and meta is not None and meta.get("platform") != platform:
            continue
        try:
            yield read_chat(path, tz)
        except ChatReadError as exc:
            yield Skipped(path, str(exc))


def _messages(block: str, tz: dt.tzinfo | None) -> tuple[ReadMessage, ...]:
    headings = list(_HEADING.finditer(block))
    messages: list[ReadMessage] = []
    for index, heading in enumerate(headings):
        stop = headings[index + 1].start() if index + 1 < len(headings) else len(block)
        section = block[heading.end() : stop]
        # render.py 写的是：标题行、空行、[思考过程、空行]、正文、空行。
        # section 从标题行末尾开始，所以开头是两个换行，结尾是正文后的两个换行。
        section = section.removeprefix("\n").removeprefix("\n")
        section = section[:-2] if section.endswith("\n\n") else section.rstrip("\n")
        thinking, text = _split_thinking(section)
        role: ReadRole = "user" if heading.group(1) == USER_LABEL else "assistant"
        messages.append(
            ReadMessage(role, _parse_time(heading.group(2), tz), _unescape(text), thinking)
        )
    return tuple(messages)


def _split_thinking(section: str) -> tuple[str, str]:
    if not section.startswith(THINKING_TITLE + "\n") and section != THINKING_TITLE:
        return "", section
    lines = section.split("\n")[1:]
    quoted: list[str] = []
    while lines and lines[0].startswith(">"):
        quoted.append(lines.pop(0).removeprefix(">").removeprefix(" "))
    if lines and lines[0] == "":
        lines.pop(0)
    return "\n".join(quoted), "\n".join(lines)


def _unescape(text: str) -> str:
    return _ESCAPED.sub("", text)


def _parse_time(text: str, tz: dt.tzinfo | None) -> dt.datetime | None:
    try:
        naive = dt.datetime.strptime(text.strip(), _TIME_FORMAT)
    except ValueError:
        return None  # 「时间未知」或者被手动改过
    if tz is None:
        return naive.astimezone()  # 本机时区，与导入时一致
    return naive.replace(tzinfo=tz)


def _notes(outside: str) -> str:
    lines = [line for line in outside.split("\n") if line.strip() != NOTES_HINT]
    return "\n".join(lines).strip()


def _tags(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value if item is not None)
    if isinstance(value, str) and value:
        return (value,)
    return ()


def _aware(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)
    return None
