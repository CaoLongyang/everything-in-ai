"""第 1 步：本机整理要发给 AI 的材料（docs/specs/profile.md 第 2 节）。不联网，不改文件。

发：「我写的」段、笔记、贴回的 AI 回答（每段截到 500 字），
以及你主动写的访谈回答和「我的补充」（docs/specs/profile.delta-v2b.md 第 1 节）。
不发：代码、报错、附件占位、AI 的回答和思考过程。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from eia.chat.reader import ReadChat, Skipped, iter_chats
from eia.chat.source import label_chat
from eia.profile.prompts import PROMPT_VERSION
from eia.schema.io import FrontmatterError, read_document

SnippetKind = Literal["own", "ai", "note", "self"]
AI_SNIPPET_CHARS = 500
KIND_TAGS: dict[SnippetKind, str] = {
    "own": "我",
    "ai": "我贴回的 AI 回答",
    "note": "我的笔记",
    "self": "我主动补充的",
}
PROFILE_DIR = "profile"
SUPPLEMENT = "我的补充.md"
INTERVIEW_GLOB = "访谈 *.md"
ANSWER_MARK = "> 你的回答："
_QUESTION = re.compile(r"^### (?:\d+\.\s*)?(.+)$")
_DATED = re.compile(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?")


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


def collect_self(root: Path, tz: dt.tzinfo | None) -> list[ChatMaterial]:
    """访谈回答（每个回答一份，标题是问题）和「我的补充」（整个文件一份）。不受时间范围限制。"""
    folder = root / PROFILE_DIR
    materials: list[ChatMaterial] = []
    for path in sorted(folder.glob(INTERVIEW_GLOB)) if folder.is_dir() else []:
        day = _file_day(path, tz)
        for number, (question, answer) in enumerate(read_interview(path), start=1):
            chat = _self_chat(f"self-{path.stem}-{number}", path, question)
            materials.append(ChatMaterial(chat, (Snippet("self", answer, day),)))
    supplement = folder / SUPPLEMENT
    if supplement.is_file():
        snippets = tuple(
            Snippet("self", text, _written_day(text) or _file_day(supplement, tz))
            for text in read_supplement(supplement)
        )
        if snippets:
            chat = _self_chat("self-supplement", supplement, "我的补充")
            materials.append(ChatMaterial(chat, snippets))
    return materials


def read_interview(path: Path) -> list[tuple[str, str]]:
    """（问题, 你的回答）；没回答的不要。"""
    try:
        body = read_document(path).body
    except FrontmatterError:
        return []
    pairs: list[tuple[str, str]] = []
    question, answering = "", False
    answer: list[str] = []
    for line in [*body.replace("\r\n", "\n").split("\n"), "### "]:
        heading = _QUESTION.match(line.strip()) if line.startswith("### ") else None
        if line.startswith("### ") or line.startswith("---"):
            if question and "\n".join(answer).strip():
                pairs.append((question, "\n".join(answer).strip()))
            question = heading.group(1).strip() if heading else ""
            answer, answering = [], False
            continue
        if line.startswith(ANSWER_MARK):
            answering = True
            answer.append(line.removeprefix(ANSWER_MARK))
        elif answering:
            answer.append(line.removeprefix(">").removeprefix(" "))
    return pairs


def read_supplement(path: Path) -> list[str]:
    """「我的补充」里你写的每一段；引用块（写法提示）不算。"""
    try:
        body = read_document(path).body
    except FrontmatterError:
        return []
    kept = [line for line in body.replace("\r\n", "\n").split("\n") if not line.startswith(">")]
    return [part.strip() for part in "\n".join(kept).split("\n\n") if part.strip()]


def _self_chat(chat_id: str, path: Path, title: str) -> ReadChat:
    return ReadChat(
        id=chat_id, path=path, title=title, platform="self", tags=(), updated=None, messages=()
    )


def _file_day(path: Path, tz: dt.tzinfo | None) -> dt.date:
    return dt.datetime.fromtimestamp(path.stat().st_mtime, tz).date()


def _written_day(text: str) -> dt.date | None:
    """段落开头写了日期（2026-11 或 2026-11-20）就用它。"""
    match = _DATED.match(text)
    if match is None:
        return None
    try:
        return dt.date(int(match[1]), int(match[2]), int(match[3] or 1))
    except ValueError:
        return None
