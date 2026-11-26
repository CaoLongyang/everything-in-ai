"""第 3 步：把观察汇总成画像（docs/specs/profile.delta-format.md 第 1、3 节）。

分 4 次请求：核心档案和心理、学业职业和兴趣、决策和时间线，最后根据前面的结论写「给 AI 的说明」。
只发观察和原话，不发整段对话。AI 引用不存在的观察编号就丢掉；一条证据都没有的要点改成未解问题。
对话数、天数、日期由程序按证据算，不听 AI 的。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from eia.llm import Client
from eia.profile.extract import Observation
from eia.profile.prompts import CATEGORIES, PART_AI, PART_CAREER, PART_CORE, PART_HISTORY

SYNTH_CHARS = 60_000  # 一次汇总最多发这么多字的观察；超过的先丢最早的
QUOTE_CHARS = 60
MAX_LABELS = 5
INTEREST = "interest"

Confidence = Literal["高", "中", "低"]
_KIND_NAMES = {"stated": "说过", "inferred": "推断", "excerpt": "原话"}

# 章 → 固定的小节（key, 标题）。03 兴趣的小节由 AI 定；04–06 只有一个不显示标题的小节
CHAPTERS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "core",
        "00 核心档案",
        (("about", "关于我"), ("values", "价值观"), ("communication", "沟通偏好")),
    ),
    (
        "psychology",
        "01 心理",
        (
            ("motivation", "动机模式"),
            ("behavior", "行为模式"),
            ("social", "社交关系"),
            ("hidden", "你可能没意识到的你"),
        ),
    ),
    (
        "career",
        "02 学业与职业",
        (("education", "教育"), ("goals", "职业目标"), ("skills", "技能与项目")),
    ),
    ("interests", "03 兴趣", ()),
    ("wishes", "04 想做的清单", (("wishes", ""),)),
    ("decisions", "05 决策记录", (("decisions", ""),)),
    ("timeline", "06 时间线", (("timeline", ""),)),
)
_PARTS = (
    (PART_CORE, ("core", "psychology")),
    (PART_CAREER, ("career", "interests")),
    (PART_HISTORY, ("wishes", "decisions", "timeline")),
)
DATED = ("decisions", "timeline")  # 这两章的要点按日期排，前面写日期
WISHES = "wishes"
WISH_STATUSES = ("在做", "在想", "放下了")
HIDDEN = "hidden"
HIDDEN_MIN_CHATS = 2  # 「你可能没意识到的你」每条至少几个不同对话（profile.delta-v2b.md 第 6 节）


class _Point(BaseModel):
    text: str
    confidence: Confidence
    evidence: list[str]
    kind: Literal["state", "trait"] = "trait"  # 状态（会结束的事）还是特点
    status: str = ""  # 只有想做的清单用：在做 / 在想 / 放下了


class _Sub(BaseModel):
    key: str
    title: str = ""
    label: str = ""
    summary: str = ""
    points: list[_Point] = []


class _Part(BaseModel):
    labels: list[str] = []
    subsections: list[_Sub]
    open_questions: list[str] = []


class _Instructions(BaseModel):
    system_prompt: str
    guidelines: list[str] = []


@dataclass(frozen=True)
class Rejected:
    """你在上一版里勾掉的结论：「不像我」，或者原因以「过时」开头的「已经变了」。"""

    text: str
    why: str = ""
    outdated: bool = False


@dataclass(frozen=True)
class Point:
    text: str
    confidence: Confidence
    evidence: tuple[Observation, ...]
    kind: Literal["state", "trait"] = "trait"
    status: str = ""

    @property
    def last_day(self) -> str | None:
        return max(self._days, default=None)

    @property
    def chats(self) -> int:
        return len({o.chat_id for o in self.evidence})

    @property
    def days(self) -> int:
        return len(self._days)

    @property
    def first_day(self) -> str | None:
        return min(self._days, default=None)

    @property
    def _days(self) -> set[str]:
        return set().union(*(o.days for o in self.evidence))


@dataclass
class Subsection:
    key: str
    title: str
    label: str = ""
    summary: str = ""
    points: list[Point] = field(default_factory=list)


@dataclass
class Chapter:
    key: str
    title: str
    subsections: list[Subsection]


@dataclass
class Profile:
    labels: list[str]
    chapters: list[Chapter]
    open_questions: list[str] = field(default_factory=list)
    system_prompt: str = ""
    guidelines: list[str] = field(default_factory=list)
    omitted: int = 0  # 超过 SYNTH_CHARS 没发出去的观察

    @property
    def points(self) -> int:
        return sum(len(s.points) for c in self.chapters for s in c.subsections)


def synthesize(
    client: Client,
    observations: Sequence[Observation],
    rejected: Sequence[Rejected],
    *,
    titles: dict[str, str],
) -> Profile:
    numbered, omitted = _fit(list(observations), titles)
    by_id = {f"O{i}": obs for i, (obs, _) in enumerate(numbered, start=1)}
    lines = [line for _, line in numbered]
    wrong = [r for r in rejected if not r.outdated]
    changed = [r for r in rejected if r.outdated]
    if wrong:
        lines.append("\nTA说过不像TA的结论（不要再这样写）：")
        lines.extend(f"- {r.text}" + (f"（原因：{r.why}）" if r.why else "") for r in wrong)
    if changed:
        lines.append(
            "\nTA说过已经过时的状态（当时没错，现在变了）：写进 timeline 当作过去的事，"
            "括号里是TA说的现在的情况，写进相应的小节："
        )
        lines.extend(f"- {r.text}" + (f"（现在：{r.why}）" if r.why else "") for r in changed)
    prompt = "\n".join(lines)
    profile = Profile(labels=[], chapters=_empty_chapters(), omitted=omitted)
    chapters = {c.key: c for c in profile.chapters}
    for system, keys in _PARTS:
        reply = (
            client.complete_json(prompt, _Part, system=system)
            if numbered
            else _Part(subsections=[])
        )
        if not profile.labels:
            profile.labels = _labels(reply.labels)
        profile.open_questions.extend(q.strip() for q in reply.open_questions if q.strip())
        for sub in reply.subsections:
            _fill(sub, [chapters[k] for k in keys], by_id, profile.open_questions)
    for key in DATED:
        for dated in chapters[key].subsections:
            dated.points.sort(key=lambda p: p.first_day or "")
    if profile.points:
        instructions = client.complete_json(summary_text(profile), _Instructions, system=PART_AI)
        profile.system_prompt = instructions.system_prompt.strip()
        profile.guidelines = [g.strip() for g in instructions.guidelines if g.strip()]
    return profile


def summary_text(profile: Profile) -> str:
    """第 4 次请求发的内容：前面各章的结论，不带观察和原话。"""
    lines = ["核心标签：" + " · ".join(profile.labels)]
    for chapter in profile.chapters:
        lines.append(f"\n## {chapter.title}")
        for sub in chapter.subsections:
            if not sub.points:
                continue
            head = " ".join(part for part in (sub.title, sub.label and f"【{sub.label}】") if part)
            if head or sub.summary:
                lines.append(f"### {head} {sub.summary}".rstrip())
            lines.extend(f"- {p.text}" for p in sub.points)
    return "\n".join(lines)


def _empty_chapters() -> list[Chapter]:
    return [
        Chapter(key, title, [Subsection(k, t) for k, t in subs]) for key, title, subs in CHAPTERS
    ]


def _labels(raw: list[str]) -> list[str]:
    return list(dict.fromkeys(label.strip() for label in raw if label.strip()))[:MAX_LABELS]


def _fill(
    sub: _Sub, chapters: list[Chapter], by_id: dict[str, Observation], questions: list[str]
) -> None:
    key = sub.key.strip()
    target: Subsection | None = None
    if key == INTEREST:
        interests = next((c for c in chapters if c.key == "interests"), None)
        if interests is None or not sub.title.strip():
            return  # 没有标题的兴趣不知道放哪
        target = Subsection(INTEREST, sub.title.strip())
        interests.subsections.append(target)
    else:
        target = next((s for c in chapters for s in c.subsections if s.key == key), None)
    if target is None:
        return  # 不是这次请求该写的小节
    target.label = target.label or sub.label.strip()
    target.summary = target.summary or sub.summary.strip()
    for point in sub.points:
        text = point.text.strip()
        if not text:
            continue
        evidence = tuple(
            dict.fromkeys(by_id[e.strip()] for e in point.evidence if e.strip() in by_id)
        )
        if not evidence:
            questions.append(text)
            continue
        if key == HIDDEN and len({o.chat_id for o in evidence}) < HIDDEN_MIN_CHATS:
            questions.append(text)  # 一个对话撑不起「你没意识到的你」
            continue
        status = ""
        if key == WISHES:
            status = point.status.strip() if point.status.strip() in WISH_STATUSES else "在想"
        target.points.append(Point(text, point.confidence, evidence, point.kind, status))


def _fit(
    observations: list[Observation], titles: dict[str, str]
) -> tuple[list[tuple[Observation, str]], int]:
    """编号并写成一行一条；总字数超过 SYNTH_CHARS 时保留最近的。"""
    newest_first = sorted(observations, key=lambda o: max(o.days, default=""), reverse=True)
    kept: list[Observation] = []
    size = 0
    for obs in newest_first:
        length = len(_line("O0000", obs, titles))
        if kept and size + length > SYNTH_CHARS:
            break
        kept.append(obs)
        size += length
    kept.sort(key=lambda o: (max(o.days, default=""), o.chat_id))
    numbered = [(obs, _line(f"O{i}", obs, titles)) for i, obs in enumerate(kept, start=1)]
    return numbered, len(observations) - len(kept)


def _line(label: str, obs: Observation, titles: dict[str, str]) -> str:
    quote = next((q for q in obs.quotes if q.kind != "ai"), obs.quotes[0] if obs.quotes else None)
    said = f"「{' '.join(quote.text.split())[:QUOTE_CHARS]}」" if quote else ""
    days = "、".join(sorted(obs.days)) or "日期未知"
    text = f"{obs.text}｜" if obs.text else ""
    kind = _KIND_NAMES[obs.kind]
    if any(q.kind == "self" for q in obs.quotes):
        kind += "·主动补充"  # 访谈回答和「我的补充」：TA最新、最可信的说法
    title = titles.get(obs.chat_id, "")
    return (
        f"[{label}] {CATEGORIES[obs.category]}｜（{kind}）{text}原话：{said}｜对话：{title}｜{days}"
    )
