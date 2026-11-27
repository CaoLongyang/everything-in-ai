"""你主动补充给画像的东西：「我的补充」和访谈（docs/specs/profile.delta-v2b.md 第 1 节）。

- 「我的补充」：第一次生成画像时建一个空的，之后程序只读、不改。
- 访谈：`eia profile ask` 根据最新画像里空着、不确定的地方出问题，每次新建一个文件。
  只发画像的结论、「访谈设置」、以前问过的问题和你写过的回答，不发对话
  （docs/specs/profile.delta-interview.md）。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from eia import llm
from eia.profile import interview
from eia.profile.document import latest_profile
from eia.profile.material import ANSWER_MARK, PROFILE_DIR, SUPPLEMENT
from eia.profile.prompts import ASK_SYSTEM
from eia.schema.ids import new_id
from eia.schema.io import create_new, read_document, render_document
from eia.schema.model import SCHEMA_VERSION, EntryType, Status

_FOOTNOTE = re.compile(r"\[\^\d+\]")

SUPPLEMENT_HINT = """> 这里写你想让画像知道、但聊天记录里没有的事，或者已经变了的事。
> 程序只读、不改这个文件。每段最好以日期开头，这样画像知道是什么时候的情况。比如：
> 2026-11 搬家了，现在和朋友合租。
> 2025-09 换了专业，因为……
"""


class ProfileAskError(Exception):
    """还没有画像，出不了问题。"""


class _Question(BaseModel):
    question: str
    why: str = ""
    aspect: str = ""  # 七个方面的 key（interview.ASPECTS）
    follow_up: str = ""  # 追问的话：指回以前哪一题
    depth: str = "light"  # light / medium / deep


class _Questions(BaseModel):
    questions: list[_Question]


@dataclass
class AskPlan:
    profile: Path
    text: str  # 要发出去的全部内容
    model: str
    service: str
    settings: interview.Settings
    rounds: list[interview.Round]
    auto_avoid: list[str]  # 这次要自动加进「不想聊」的方面（key）
    answers: int  # 发出去的以前的回答有几条
    today: dt.date

    @property
    def chars(self) -> int:
        return len(self.text)


def ensure_supplement(root: Path, *, now: dt.datetime | None = None) -> Path:
    """「我的补充」不存在就建一个空的；已经有了就不动。"""
    path = root / PROFILE_DIR / SUPPLEMENT
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    when = (now or dt.datetime.now(dt.UTC)).replace(microsecond=0)
    meta = note_meta("我的补充", when)
    try:
        create_new(path, render_document(meta, SUPPLEMENT_HINT + "\n"))
    except FileExistsError:
        pass
    return path


def plan_ask(root: Path, *, now: dt.datetime | None = None) -> AskPlan:
    path = latest_profile(root)
    if path is None:
        raise ProfileAskError("还没有画像。先运行 eia profile run --yes 生成一份，再来出访谈问题")
    body = read_document(path).body
    settings_path = root / PROFILE_DIR / interview.SETTINGS
    settings = (
        interview.read_settings(settings_path) if settings_path.exists() else interview.Settings()
    )
    rounds = interview.read_rounds(root)
    auto = interview.skipped_aspects(rounds, settings.avoided_aspects | settings.auto_done)
    settings.avoid += [interview.AUTO + interview.ASPECT_NAMES[k] for k in auto]
    cover = interview.coverage(rounds, body)
    today = (now or dt.datetime.now(dt.UTC)).date()
    text = interview.prompt_text(conclusions(body), settings, rounds, cover, today)
    config = llm.get_client(root).config.text
    answers = sum(1 for r in rounds for q in r.questions if q.answer and not q.undecided)
    return AskPlan(
        path,
        text,
        config.model,
        config.api_base or config.provider,
        settings,
        rounds,
        auto,
        answers,
        today,
    )


def ensure_settings(root: Path, *, now: dt.datetime | None = None) -> Path:
    """「访谈设置」不存在就建一个；已经有了就不动。"""
    path = root / PROFILE_DIR / interview.SETTINGS
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    when = (now or dt.datetime.now(dt.UTC)).replace(microsecond=0)
    try:
        create_new(path, render_document(note_meta("访谈设置", when), interview.settings_text()))
    except FileExistsError:
        pass
    return path


def conclusions(body: str) -> str:
    """画像正文去掉证据（原话都在证据脚注里）和说明，只剩结论。"""
    kept: list[str] = []
    in_evidence = False
    for line in body.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            in_evidence = line.strip() == "## 证据"
        if (
            in_evidence
            or line.startswith("[^")
            or line.startswith("> 这份画像")
            or line.startswith("> 根据")
        ):
            continue
        kept.append(_FOOTNOTE.sub("", line))
    return "\n".join(kept).strip()


def run_ask(root: Path, plan: AskPlan, *, now: dt.datetime | None = None) -> tuple[Path, int]:
    reply = llm.get_client(root).complete_json(plan.text, _Questions, system=ASK_SYSTEM)
    raw = [(q.question, q.why, q.aspect, q.follow_up, q.depth) for q in reply.questions]
    questions = interview.pick(raw, plan.settings, plan.rounds, plan.today)
    when = (now or dt.datetime.now(dt.UTC)).replace(microsecond=0)
    settings_path = ensure_settings(root, now=when)
    if not interview.read_settings(settings_path).has_depth:
        interview.add_depth_section(settings_path)
    interview.add_auto_avoid(settings_path, plan.auto_avoid)
    folder = root / PROFILE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    day = when.date().isoformat()
    body = [
        f"> 这些问题是根据「{plan.profile.stem}」里空着、不确定或证据很少的地方出的。",
        "> 想答几个就答几个，不想答的空着（空着的不会再问）。",
        "> 答案写在「你的回答：」后面，可以写好几行。",
        "> 想聊什么、不想聊什么，在同一个文件夹的「访谈设置」里改。",
        "> 写完后再生成一次画像（eia profile run --yes），回答就会用上。程序不会改这个文件。",
        "",
    ]
    for number, q in enumerate(questions, start=1):
        body += [f"### {number}. {q.question}", ""]
        tags = [interview.ASPECT_NAMES[q.aspect]] if q.aspect else []
        if q.follow_up:
            tags.append(f"追问 {q.follow_up}")
        if q.level == "deep":
            tags.append("深一点")
        if tags:
            body += [f"（方面：{' · '.join(tags)}）" if q.aspect else f"（{tags[0]}）", ""]
        if q.why:
            body += [f"（为什么问：{q.why}）", ""]
        body += [ANSWER_MARK, ""]
    for n in range(1, 1000):
        title = f"访谈 {day}" if n == 1 else f"访谈 {day} {n}"
        path = folder / f"{title}.md"
        try:
            create_new(path, render_document(note_meta(title, when), "\n".join(body)))
        except FileExistsError:
            continue
        return path, len(questions)
    raise FileExistsError(f"{folder} 里今天的访谈太多了")


def note_meta(title: str, when: dt.datetime) -> dict[str, object]:
    return {
        "schema": SCHEMA_VERSION,
        "id": new_id(EntryType.note.value, when),
        "type": EntryType.note.value,
        "title": title,
        "created": when,
        "status": Status.inbox.value,
        "tags": [],
        "source": "eia-profile",
    }
