"""第 1 步：本机整理要发给 AI 的材料（docs/specs/profile.md 第 2 节）。不联网，不改文件。

发：「我写的」段、笔记、贴回的 AI 回答（每段截到 500 字）。
不发：代码、报错、附件占位、AI 的回答和思考过程。
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from eia.chat.reader import ReadChat, Skipped, iter_chats
from eia.chat.source import label_chat
from eia.profile.prompts import PROMPT_VERSION

SnippetKind = Literal["own", "ai", "note"]
AI_SNIPPET_CHARS = 500
KIND_TAGS: dict[SnippetKind, str] = {"own": "我", "ai": "我贴回的 AI 回答", "note": "我的笔记"}


@dataclass(frozen=True)
class Snippet:
    kind: SnippetKind
    text: str
    day: dt.date | None

    def render(self) -> str:
        when = f" · {self.day.isoformat()}" if self.day else ""
        return f"[{KIND_TAGS[self.kind]}{when}] {self.text}"


@dataclass(frozen=True)
class ChatMaterial:
    chat: ReadChat
    snippets: tuple[Snippet, ...]

    @property
    def chars(self) -> int:
        """要发出的内容字数（只算你的文字，不算标题和标签）。"""
        return sum(len(s.text) for s in self.snippets)

    @property
    def first_day(self) -> dt.date | None:
        return min((s.day for s in self.snippets if s.day), default=None)

    def header(self, label: str) -> str:
        day = self.first_day.isoformat() if self.first_day else "日期未知"
        return f"=== {label}：{self.chat.title}（{self.chat.platform} · {day}）"

    def key(self, model: str) -> str:
        """缓存用：材料、提示词版本、模型任何一个变了，就要重新分析。"""
        digest = hashlib.sha256()
        for part in (PROMPT_VERSION, model, self.chat.id, self.chat.title):
            digest.update(part.encode("utf-8") + b"\0")
        for snippet in self.snippets:
            digest.update(snippet.render().encode("utf-8") + b"\0")
        return digest.hexdigest()


def collect(
    root: Path, tz: dt.tzinfo | None, since: dt.date, until: dt.date
) -> tuple[list[ChatMaterial], int]:
    """范围内每个有材料的对话一份。返回（材料，读不了的对话文件数）。"""
    materials: list[ChatMaterial] = []
    skipped = 0
    for chat in iter_chats(root, tz):
        if isinstance(chat, Skipped):
            skipped += 1
            continue
        snippets: list[Snippet] = []
        for message, parts in zip(chat.messages, label_chat(chat), strict=True):
            day = _day(message.time, tz)
            if parts is None or day is None or not since <= day <= until:
                continue
            for part in parts:
                text = part.text.strip()
                if not text:
                    continue
                if part.source == "own":
                    snippets.append(Snippet("own", text, day))
                elif part.source == "ai":
                    snippets.append(Snippet("ai", text[:AI_SNIPPET_CHARS], day))
        note_day = _day(chat.updated, tz)
        if chat.notes and note_day is not None and since <= note_day <= until:
            snippets.append(Snippet("note", chat.notes, note_day))
        if snippets:
            materials.append(ChatMaterial(chat, tuple(snippets)))
    return materials, skipped


def _day(when: dt.datetime | None, tz: dt.tzinfo | None) -> dt.date | None:
    return when.astimezone(tz).date() if when is not None else None
