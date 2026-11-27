"""访谈个性化：访谈设置、追问、跳过当信号、人生故事框架（docs/specs/profile.delta-interview.md）。

- 「访谈设置」：第一次出题时建好，之后程序只读；唯一的例外是在末尾追加「（自动）」不想聊的行。
- 以前的每一轮访谈：问过什么、你答了什么、每题属于哪个方面。
- 发给 AI 的内容在这里拼好；AI 出的题，程序再按设置和以前的问题过滤一遍。
"""

from __future__ import annotations

import datetime as dt
import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from eia.profile.material import ANSWER_MARK, INTERVIEW_GLOB, PROFILE_DIR
from eia.schema.io import FrontmatterError, read_document

SETTINGS = "访谈设置.md"
MAX_QUESTIONS = 8
MAX_FOLLOW_UPS = 3
SIMILAR = 0.85  # 问题文字相似到这个程度就算问过
AUTO = "（自动）"

# 人生故事框架的七个方面（Life Story Interview 的简化版）：key、名字、说明、对应画像里的哪些小节
ASPECTS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("family", "成长与家庭", "在哪长大、家里的人、小时候的事", ()),
    ("school", "求学", "为什么选这个学校、这个专业，读书时的转折", ("教育",)),
    ("work", "工作与项目", "做过什么、为什么做、做成了什么", ("职业目标", "技能与项目")),
    ("values", "价值观与信仰", "在意什么、相信什么、这些是怎么来的", ("价值观",)),
    ("people", "人际关系", "重要的人、朋友、怎么相处", ("社交关系",)),
    ("turns", "转折点", "改变了你的几件事", ("05 决策记录", "06 时间线")),
    ("now", "现在和将来", "最近在忙什么、想成为什么样的人", ("04 想做的清单",)),
)
ASPECT_NAMES = {key: name for key, name, _, _ in ASPECTS}
_BY_NAME = {name: key for key, name, _, _ in ASPECTS}

WANT_HEADING = "## 想聊的方面"
ALSO_HEADING = "## 也想聊（自己写，一行一个）"
AVOID_HEADING = "## 不想聊（一行一个；这些话题不会被问到）"

_QUESTION = re.compile(r"^###\s*(\d+)\.\s*(.+)$")
_META = re.compile(r"^（方面：(?P<aspect>[^·）]+)(?: · 追问 (?P<ref>[^）]+))?）$")
_WHY = re.compile(r"^（为什么问：(?P<why>.*)）$")
_AUTO_MARK = re.compile(r"<!-- eia:auto (\w+) -->")
_CHECKBOX = re.compile(r"^- \[(?P<mark>[ xX])\] (?P<name>[^：:]+)")


@dataclass
class Settings:
    want: list[str] = field(default_factory=list)  # 勾了的方面（key）
    also: list[str] = field(default_factory=list)  # 也想聊（你写的话题）
    avoid: list[str] = field(default_factory=list)  # 不想聊（方面名字或话题词）
    auto_done: set[str] = field(default_factory=set)  # 自动加过的方面：你删掉那行后不再自动加

    @property
    def avoided_aspects(self) -> set[str]:
        return {
            _BY_NAME[a.removeprefix(AUTO)] for a in self.avoid if a.removeprefix(AUTO) in _BY_NAME
        }

    @property
    def avoided_words(self) -> list[str]:
        return [a.removeprefix(AUTO) for a in self.avoid if a.removeprefix(AUTO)]


@dataclass(frozen=True)
class Asked:
    """以前问过的一题。"""

    ref: str  # 「2026-11-26 第 3 题」，追问时用来指回这一题
    question: str
    aspect: str  # key；旧访谈没标方面的是 ""
    answer: str


@dataclass(frozen=True)
class Round:
    path: Path
    day: str
    questions: tuple[Asked, ...]


def settings_text() -> str:
    lines = [
        "> 在这里告诉访谈你想聊什么、不想聊什么。程序只读这个文件；",
        "> 唯一的例外：同一个方面连续两轮的问题你都没答，",
        "> 会在「不想聊」末尾自动加一行（带「（自动）」），",
        "> 删掉那一行就恢复。",
        "",
        WANT_HEADING,
        "勾上的会优先问；全不勾 = 都可以问。",
        "",
        *(f"- [ ] {name}：{hint}" for _, name, hint, _ in ASPECTS),
        "",
        ALSO_HEADING,
        "",
        AVOID_HEADING,
        "",
    ]
    return "\n".join(lines)


def read_settings(path: Path) -> Settings:
    try:
        body = read_document(path).body
    except (FrontmatterError, OSError):
        return Settings()
    settings = Settings()
    section = ""
    for raw in body.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        settings.auto_done.update(_AUTO_MARK.findall(line))
        line = _AUTO_MARK.sub("", line).strip()
        if line.startswith("## "):
            section = line
            continue
        if not line or line.startswith(">"):
            continue
        if section == WANT_HEADING:
            box = _CHECKBOX.match(line)
            if box and box["mark"] != " " and box["name"].strip() in _BY_NAME:
                settings.want.append(_BY_NAME[box["name"].strip()])
        elif section.startswith("## 也想聊") and line.startswith("-"):
            settings.also.append(line.lstrip("- ").strip())
        elif section.startswith("## 不想聊") and line.startswith("-"):
            settings.avoid.append(line.lstrip("- ").strip())
    settings.also = [x for x in settings.also if x]
    settings.avoid = [x for x in settings.avoid if x]
    return settings


def add_auto_avoid(path: Path, aspects: Sequence[str]) -> None:
    """在文件末尾追加「（自动）方面」的行（只追加，不改你写的内容）。

    另起一行留一个看不见的记号（HTML 注释，Obsidian 阅读模式里不显示）：你删掉「（自动）」那行之后，
    记号还在，程序就不会再自动把这个方面加回去。
    """
    if not aspects:
        return
    text = path.read_text(encoding="utf-8")
    tail = "" if text.endswith("\n") else "\n"
    lines = "".join(f"- {AUTO}{ASPECT_NAMES[a]}\n<!-- eia:auto {a} -->\n" for a in aspects)
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(tail + lines)


def read_rounds(root: Path) -> list[Round]:
    """以前的每一轮访谈，按日期从早到晚。"""
    folder = root / PROFILE_DIR
    rounds: list[Round] = []
    for path in sorted(folder.glob(INTERVIEW_GLOB)) if folder.is_dir() else []:
        try:
            document = read_document(path)
        except FrontmatterError:
            continue
        created = document.meta.get("created")
        day = created.date().isoformat() if isinstance(created, dt.datetime) else path.stem[3:13]
        rounds.append(Round(path, day, tuple(_questions(path.stem, document.body))))
    return sorted(rounds, key=lambda r: (r.day, r.path.name))


def _questions(stem: str, body: str) -> list[Asked]:
    label = stem.removeprefix("访谈 ").strip()
    found: list[Asked] = []
    number, question, aspect = "", "", ""
    answer: list[str] = []
    answering = False

    def flush() -> None:
        if question:
            text = "\n".join(answer).strip()
            found.append(Asked(f"{label} 第 {number} 题", question, aspect, text))

    for line in [*body.replace("\r\n", "\n").split("\n"), "### "]:
        if line.startswith("### ") or line.startswith("---"):
            flush()
            match = _QUESTION.match(line.strip())
            number, question = (match[1], match[2].strip()) if match else ("", "")
            aspect, answer, answering = "", [], False
            continue
        meta = _META.match(line.strip())
        if meta and not answering:
            aspect = _BY_NAME.get(meta["aspect"].strip(), "")
            continue
        if line.startswith(ANSWER_MARK):
            answering = True
            answer.append(line.removeprefix(ANSWER_MARK))
        elif answering:
            answer.append(line.removeprefix(">").removeprefix(" "))
    return found


def skipped_aspects(rounds: Sequence[Round], already: set[str]) -> list[str]:
    """同一个方面，最近两轮出现过它的访谈里，它的题你全部没答 → 该自动加进「不想聊」。

    already：已经不想聊的、或者自动加过一次的方面，不再加。
    """
    out: list[str] = []
    for key, *_ in ASPECTS:
        if key in already:
            continue
        seen = [r for r in rounds if any(q.aspect == key for q in r.questions)]
        last_two = seen[-2:]
        if len(last_two) == 2 and all(
            not q.answer for r in last_two for q in r.questions if q.aspect == key
        ):
            out.append(key)
    return out


def coverage(rounds: Sequence[Round], profile_body: str) -> dict[str, tuple[int, int]]:
    """每个方面：(你已经写了几条回答, 画像里对应小节有几条要点)。"""
    points = _points_by_heading(profile_body)
    result: dict[str, tuple[int, int]] = {}
    for key, _, _, headings in ASPECTS:
        answers = sum(1 for r in rounds for q in r.questions if q.aspect == key and q.answer)
        result[key] = (answers, sum(points.get(h, 0) for h in headings))
    return result


def _points_by_heading(body: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    chapter, sub = "", ""
    for line in body.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            chapter, sub = line[3:].strip(), ""
        elif line.startswith("### "):
            sub = line[4:].strip()
        elif line.startswith("- [ ] ") or line.startswith("- [x] "):
            for name in (chapter, sub):
                if name:
                    counts[name] = counts.get(name, 0) + 1
    return counts


def prompt_text(
    conclusions: str,
    settings: Settings,
    rounds: Sequence[Round],
    cover: dict[str, tuple[int, int]],
) -> str:
    """发给 AI 的全部内容：画像结论、访谈设置、各方面的覆盖、以前的问题和你的回答。"""
    want = "、".join(ASPECT_NAMES[k] for k in settings.want) or "没勾（都可以问）"
    lines = [
        "## 画像结论",
        conclusions,
        "",
        "## 访谈设置",
        f"想聊的方面：{want}",
        f"也想聊：{'；'.join(settings.also) or '（没写）'}",
        f"不想聊（绝对不要问）：{'；'.join(settings.avoided_words) or '（没写）'}",
        "",
        "## 七个方面现在有多少（方面 key：名字｜你已经写了几条回答｜画像里有几条要点）",
    ]
    lines += [
        f"- {key}：{name}｜{cover[key][0]}｜{cover[key][1]}"
        + ("｜不想聊，不要问" if key in settings.avoided_aspects else "")
        for key, name, _, _ in ASPECTS
    ]
    asked = [q for r in rounds for q in r.questions]
    if asked:
        lines += ["", "## 以前问过的问题（不要再问，换个说法也不行）"]
        lines += [f"- {q.question}" for q in asked]
    answered = [q for q in asked if q.answer]
    if answered:
        lines += ["", "## TA以前的回答（可以从这里追问；follow_up 填方括号里的编号）"]
        lines += [f"[{q.ref}] 问：{q.question}\n答：{q.answer}" for q in answered]
    return "\n".join(lines)


@dataclass(frozen=True)
class Picked:
    question: str
    why: str
    aspect: str  # key 或 ""
    follow_up: str  # 指回的那一题（Asked.ref）或 ""


def pick(
    raw: Sequence[tuple[str, str, str, str]],
    settings: Settings,
    rounds: Sequence[Round],
) -> list[Picked]:
    """AI 出的 (问题, 为什么问, 方面, 追问哪一题)，程序再过滤、排序、限量。"""
    asked = [_norm(q.question) for r in rounds for q in r.questions]
    # AI 填出处时常带上方括号、换掉空格（实测：第二轮的追问一个都没认出来），比对时去掉这些
    refs = {_ref_key(q.ref): q.ref for r in rounds for q in r.questions if q.answer}
    words = [w for w in settings.avoided_words if w]
    kept: list[Picked] = []
    seen: list[str] = []
    for question, why, aspect, follow_up in raw:
        text = " ".join(question.split())
        if not text:
            continue
        key = aspect.strip() if aspect.strip() in ASPECT_NAMES else ""
        if key and key in settings.avoided_aspects:
            continue
        if any(w in text or w in why for w in words):
            continue
        norm = _norm(text)
        if any(_similar(norm, old) for old in [*asked, *seen]):
            continue
        seen.append(norm)
        ref = refs.get(_ref_key(follow_up), "") if follow_up.strip() else ""
        kept.append(Picked(text, " ".join(why.split()), key, ref))
    follow_ups = [p for p in kept if p.follow_up][:MAX_FOLLOW_UPS]
    others = [p for p in kept if not p.follow_up]
    others.sort(key=lambda p: p.aspect not in settings.want)  # 勾了的方面排前面，其余保持 AI 的顺序
    return [*follow_ups, *others][:MAX_QUESTIONS]


def _ref_key(ref: str) -> str:
    """「[2026-11-26 第 3 题]」「2026-11-26第3题」「【2026-11-26 第3题】」都算同一个出处。"""
    return re.sub(r"[\s\[\]【】()（）「」#:：]", "", ref).removeprefix("访谈")


def _norm(text: str) -> str:
    return re.sub(r"[\s，。？！、,.?!：:；;「」“”\"']", "", text)


def _similar(a: str, b: str) -> bool:
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= SIMILAR
