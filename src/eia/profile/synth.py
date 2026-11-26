"""第 3 步：按大类把观察汇总成结论（docs/specs/profile.md 第 3、4 节）。

只发观察和原话，不发整段对话。AI 引用不存在的观察编号就丢掉；一条证据都没有的结论改成未解问题。
每条结论来自几个对话、几天，由程序按证据数，不听 AI 的。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from eia.llm import Client
from eia.profile.extract import Observation
from eia.profile.prompts import CATEGORIES, HIDDEN_SYSTEM, SYNTH_SYSTEM

SYNTH_CHARS = 60_000  # 一次汇总最多发这么多字的观察；超过的先丢最早的
QUOTE_CHARS = 120
HIDDEN = "hidden"

Confidence = Literal["高", "中", "低"]
_KIND_NAMES = {"stated": "说过", "inferred": "推断", "excerpt": "原话"}


class _Concluded(BaseModel):
    kind: Literal["stated", "inferred"]
    text: str
    confidence: Confidence
    evidence: list[str]


class _Section(BaseModel):
    conclusions: list[_Concluded]
    open_questions: list[str] = []


@dataclass(frozen=True)
class Rejected:
    """你在上一版里标了「不像我」的结论。"""

    text: str
    why: str = ""


@dataclass(frozen=True)
class Conclusion:
    kind: Literal["stated", "inferred"]
    text: str
    confidence: Confidence
    evidence: tuple[Observation, ...]

    @property
    def chats(self) -> int:
        return len({o.chat_id for o in self.evidence})

    @property
    def days(self) -> int:
        return len(set().union(*(o.days for o in self.evidence)))


@dataclass
class Section:
    key: str  # cognition / interests / values / identity / hidden
    conclusions: list[Conclusion] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    omitted: int = 0  # 超过 SYNTH_CHARS 没发出去的观察


def synthesize(
    client: Client,
    observations: Sequence[Observation],
    rejected: Sequence[Rejected],
    *,
    titles: dict[str, str],
) -> list[Section]:
    """每个大类一次请求，再加一次「你可能没意识到的你」。"""
    sections: list[Section] = []
    for key, name in CATEGORIES.items():
        mine = [o for o in observations if o.category == key]
        if not mine:
            sections.append(Section(key))
            continue
        system = SYNTH_SYSTEM.replace("{category}", name)
        sections.append(_ask(client, key, system, mine, rejected, titles))
    if observations:
        sections.append(_ask(client, HIDDEN, HIDDEN_SYSTEM, list(observations), rejected, titles))
    else:
        sections.append(Section(HIDDEN))
    return sections


def _ask(
    client: Client,
    key: str,
    system: str,
    observations: list[Observation],
    rejected: Sequence[Rejected],
    titles: dict[str, str],
) -> Section:
    numbered, omitted = _fit(observations, titles, with_category=key == HIDDEN)
    lines = [line for _, line in numbered]
    if rejected:
        lines.append("\nTA说过不像TA的结论（不要再这样写）：")
        lines.extend(f"- {r.text}" + (f"（原因：{r.why}）" if r.why else "") for r in rejected)
    reply = client.complete_json("\n".join(lines), _Section, system=system)
    by_id = dict(numbered_ids(numbered))
    section = Section(key, omitted=omitted)
    for concluded in reply.conclusions:
        text = concluded.text.strip()
        if not text:
            continue
        evidence = tuple(
            dict.fromkeys(by_id[e.strip()] for e in concluded.evidence if e.strip() in by_id)
        )
        if not evidence:
            section.open_questions.append(text)
            continue
        kind: Literal["stated", "inferred"] = "inferred" if key == HIDDEN else concluded.kind
        section.conclusions.append(Conclusion(kind, text, concluded.confidence, evidence))
    section.open_questions.extend(q.strip() for q in reply.open_questions if q.strip())
    return section


def numbered_ids(numbered: list[tuple[Observation, str]]) -> list[tuple[str, Observation]]:
    return [(f"O{index}", obs) for index, (obs, _) in enumerate(numbered, start=1)]


def _fit(
    observations: list[Observation], titles: dict[str, str], *, with_category: bool
) -> tuple[list[tuple[Observation, str]], int]:
    """编号并写成一行一条；总字数超过 SYNTH_CHARS 时保留最近的。"""
    newest_first = sorted(observations, key=lambda o: max(o.days, default=""), reverse=True)
    kept: list[Observation] = []
    size = 0
    for obs in newest_first:
        length = len(_line("O0000", obs, titles, with_category))
        if kept and size + length > SYNTH_CHARS:
            break
        kept.append(obs)
        size += length
    kept.sort(key=lambda o: (max(o.days, default=""), o.chat_id))
    numbered = [
        (obs, _line(f"O{index}", obs, titles, with_category))
        for index, obs in enumerate(kept, start=1)
    ]
    return numbered, len(observations) - len(kept)


def _line(label: str, obs: Observation, titles: dict[str, str], with_category: bool) -> str:
    quotes = "".join(
        f"「{q.text[:QUOTE_CHARS]}」"
        if q.kind != "ai"
        else f"（贴回的 AI 回答）「{q.text[:QUOTE_CHARS]}」"
        for q in obs.quotes
    )
    category = f"{CATEGORIES[obs.category]}｜" if with_category else ""
    days = "、".join(sorted(obs.days)) or "日期未知"
    text = f"{obs.text}｜" if obs.text else ""
    title = titles.get(obs.chat_id, "")
    kind = _KIND_NAMES[obs.kind]
    return f"[{label}] {category}（{kind}）{text}原话：{quotes}｜对话：{title}｜{days}"
