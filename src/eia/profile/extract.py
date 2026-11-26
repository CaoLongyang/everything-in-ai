"""第 2 步：逐个对话提取观察（docs/specs/profile.md 第 3 节）。

几个短对话拼成一批发，太长的切开。AI 给的每句原话都去材料里核对，
对不上的丢掉；一条观察至少要有一句核对过的、你自己写的原话。
结果按对话存进 .eia/eia.db，对话没变就不重新发。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from eia.chat.source import normalize
from eia.db import database
from eia.llm import Client, LLMOutputError
from eia.profile.material import ChatMaterial, Snippet, SnippetKind
from eia.profile.prompts import CATEGORIES, EXTRACT_SYSTEM

BATCH_CHARS = 10_000
MIN_QUOTE_CHARS = 2  # 去掉空白和符号后

Category = Literal["cognition", "interests", "values", "identity"]
ObservationKind = Literal["stated", "inferred", "excerpt"]


class _Observed(BaseModel):
    chat: str
    category: Category
    kind: ObservationKind
    text: str = ""
    quotes: list[str]


class _Extracted(BaseModel):
    observations: list[_Observed]


@dataclass(frozen=True)
class Quote:
    text: str
    kind: SnippetKind
    day: str | None  # ISO 日期，存进数据库方便


@dataclass(frozen=True)
class Observation:
    chat_id: str
    category: Category
    kind: ObservationKind
    text: str
    quotes: tuple[Quote, ...]

    @property
    def days(self) -> set[str]:
        return {q.day for q in self.quotes if q.day}


@dataclass
class ExtractResult:
    observations: dict[str, list[Observation]] = field(default_factory=dict)  # 按对话 id
    dropped: int = 0  # 原话对不上被丢掉的观察
    requests: int = 0
    # 模型回复几次都不合格式、这次没分析成的对话（docs/specs/profile.delta-skip.md）。
    # 不写缓存，下次运行会重试
    skipped: list[ChatMaterial] = field(default_factory=list)


@dataclass(frozen=True)
class _Unit:
    """一次请求里的一块：一个对话的全部或一部分。"""

    material: ChatMaterial
    snippets: tuple[Snippet, ...]
    last: bool  # 这个对话的最后一块

    @property
    def chars(self) -> int:
        return sum(len(s.text) for s in self.snippets)


def cached(root: Path, keys: Iterable[str]) -> dict[str, list[Observation]]:
    wanted = list(keys)
    found: dict[str, list[Observation]] = {}
    with database(root) as conn:
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            marks = ",".join("?" * len(chunk))
            for row in conn.execute(
                f"SELECT key, data FROM profile_observations WHERE key IN ({marks})", chunk
            ):
                found[row["key"]] = [_load(item) for item in json.loads(row["data"])]
    return found


def extract(
    root: Path,
    client: Client,
    materials: list[ChatMaterial],
    *,
    progress: Callable[[int, int], None] | None = None,
) -> ExtractResult:
    """把材料分批发给模型。每个对话做完就存一次，中途出错时已完成的不丢。

    某一批的回复重试几次都不合格式：跳过这一批里的对话，接着做后面的（一个长对话切成几批时，
    有一批失败整个对话就算没做成）。网络、key 这类错误照旧停下：后面的批次多半也会失败。
    """
    model = client.config.text.model
    batches = _batches(materials)
    result = ExtractResult()
    pending: dict[str, list[Observation]] = {}
    failed: set[str] = set()
    for number, batch in enumerate(batches, start=1):
        if progress is not None:
            progress(number, len(batches))
        labels = _labels(batch)
        try:
            reply = client.complete_json(_prompt(batch, labels), _Extracted, system=EXTRACT_SYSTEM)
        except LLMOutputError:
            reply = _Extracted(observations=[])
            failed.update(unit.material.chat.id for unit in batch)
        result.requests += 1
        by_label = {label: material for material, label in labels.items()}
        for observed in reply.observations:
            material = by_label.get(observed.chat.strip())
            checked = _check(observed, material) if material is not None else None
            if material is None or checked is None:
                result.dropped += 1
                continue
            pending.setdefault(material.chat.id, []).append(checked)
        for unit in batch:
            if not unit.last:
                continue
            chat_id = unit.material.chat.id
            done = pending.pop(chat_id, [])
            if chat_id in failed:
                result.skipped.append(unit.material)
                continue
            _save(root, unit.material.key(model), chat_id, done)
            result.observations[chat_id] = done
    return result


def _batches(materials: list[ChatMaterial]) -> list[list[_Unit]]:
    units: list[_Unit] = []
    for material in materials:
        chunks = _split(material.snippets)
        units.extend(
            _Unit(material, chunk, index == len(chunks) - 1) for index, chunk in enumerate(chunks)
        )
    batches: list[list[_Unit]] = []
    size = 0
    for unit in units:
        if not batches or size + unit.chars > BATCH_CHARS:
            batches.append([])
            size = 0
        batches[-1].append(unit)
        size += unit.chars
    return batches


def _split(snippets: tuple[Snippet, ...]) -> list[tuple[Snippet, ...]]:
    """太长的对话切成不超过 BATCH_CHARS 的几块；单段太长的按字数切开。"""
    pieces: list[Snippet] = []
    for snippet in snippets:
        for start in range(0, len(snippet.text), BATCH_CHARS):
            pieces.append(
                Snippet(snippet.kind, snippet.text[start : start + BATCH_CHARS], snippet.day)
            )
    chunks: list[list[Snippet]] = [[]]
    size = 0
    for piece in pieces:
        if chunks[-1] and size + len(piece.text) > BATCH_CHARS:
            chunks.append([])
            size = 0
        chunks[-1].append(piece)
        size += len(piece.text)
    return [tuple(chunk) for chunk in chunks]


def _labels(batch: list[_Unit]) -> dict[ChatMaterial, str]:
    labels: dict[ChatMaterial, str] = {}
    for unit in batch:
        labels.setdefault(unit.material, f"C{len(labels) + 1}")
    return labels


def _prompt(batch: list[_Unit], labels: dict[ChatMaterial, str]) -> str:
    blocks: dict[str, list[str]] = {}
    for unit in batch:
        label = labels[unit.material]
        lines = blocks.setdefault(label, [unit.material.header(label)])
        lines.extend(snippet.render() for snippet in unit.snippets)
    categories = "、".join(f"{k}（{v}）" for k, v in CATEGORIES.items())
    return f"类别：{categories}\n\n" + "\n\n".join("\n".join(lines) for lines in blocks.values())


def _check(observed: _Observed, material: ChatMaterial) -> Observation | None:
    """原话逐句去材料里找。找不到的原话去掉；一句你自己写的都没有，整条丢掉。"""
    quotes: list[Quote] = []
    for raw in observed.quotes:
        text = " ".join(raw.split())
        key = normalize(text)
        if len(key) < MIN_QUOTE_CHARS:
            continue
        snippet = next((s for s in material.snippets if key in normalize(s.text)), None)
        if snippet is not None:
            day = snippet.day.isoformat() if snippet.day else None
            quotes.append(Quote(text, snippet.kind, day))
    if not any(q.kind != "ai" for q in quotes):
        return None
    return Observation(
        material.chat.id, observed.category, observed.kind, observed.text.strip(), tuple(quotes)
    )


def _save(root: Path, key: str, chat_id: str, observations: list[Observation]) -> None:
    saved = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    data = json.dumps([asdict(o) for o in observations], ensure_ascii=False)
    with database(root) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO profile_observations (key, chat_id, saved, data)"
            " VALUES (?, ?, ?, ?)",
            (key, chat_id, saved, data),
        )


def _load(item: dict[str, object]) -> Observation:
    quotes = item.get("quotes")
    return Observation(
        chat_id=str(item["chat_id"]),
        category=_category(item["category"]),
        kind=_kind(item["kind"]),
        text=str(item.get("text") or ""),
        quotes=tuple(
            Quote(str(q["text"]), _snippet_kind(q["kind"]), q.get("day") or None)
            for q in (quotes if isinstance(quotes, list) else [])
            if isinstance(q, dict)
        ),
    )


def _category(value: object) -> Category:
    for name in ("cognition", "interests", "values", "identity"):
        if value == name:
            return name
    raise ValueError(f"缓存里的类别不认识：{value!r}")


def _kind(value: object) -> ObservationKind:
    for name in ("stated", "inferred", "excerpt"):
        if value == name:
            return name
    raise ValueError(f"缓存里的观察类型不认识：{value!r}")


def _snippet_kind(value: object) -> SnippetKind:
    for name in ("own", "ai", "note"):
        if value == name:
            return name
    raise ValueError(f"缓存里的原话来源不认识：{value!r}")
