"""聊聊：一次只问一个问题的朋友式访谈（docs/specs/profile.delta-chat.md）。

终端（`eia profile chat`）和网页试用台用的都是这里的 `ChatSession`。

- 每一轮 AI 回一个 JSON：回应你刚说的话 + 下一个问题。程序按访谈设置、以前问过的问题、
  深浅限制检查问题；不合格让 AI 重出一次，还不行换成一个固定的轻松问题。
- 每答一题就把整次聊天存进 `profile/访谈 <日期> 聊天.md`，格式和访谈文件一样，
  所以画像、追问、「问过的不再问」「跳过两轮」都直接认它。它的回应存成「（它：……）」，
  写在问题下面、「你的回答」上面，程序读回答时不会把它当成你说的话。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from eia import llm
from eia.profile import interview
from eia.profile.material import ANSWER_MARK, PROFILE_DIR
from eia.profile.prompts import CHAT_SYSTEM
from eia.profile.supplement import AskPlan, ensure_settings, note_meta, plan_ask
from eia.schema.io import atomic_write, create_new, render_document

REPLY_CHARS = 120
LIGHT_FIRST = 2  # 头几个问题必须是轻松的
WRAP_AT = 10  # 聊到这么多题，提醒一句「今天先聊到这？」
_ENDS = ("。", "！", "？", "!", "?", "…", "~", "～")

# 你说这些话时，程序直接处理，不当作回答
SKIP_WORDS = ("换个话题", "换一个", "换个问题", "跳过", "下一个")
AVOID_WORDS = ("不想聊这个", "不想聊", "不想说", "不想回答")
END_WORDS = ("先到这", "先到这里", "结束", "不聊了", "退出", "拜拜", "再见")

# AI 出的问题都不合格时，换成这些轻松的（不针对任何人）
FALLBACKS: tuple[tuple[str, str], ...] = (
    ("最近有什么让你开心的小事吗？", "now"),
    ("这周有没有哪件事让你印象比较深？", "now"),
    ("最近有在看什么书、剧或者视频吗？", "now"),
    ("你平时休息的时候最喜欢做什么？", "now"),
    ("最近在忙些什么？", "now"),
)


class _Turn(BaseModel):
    reply: str = ""
    question: str = ""
    aspect: str = ""
    depth: str = "light"
    follow_up: str = ""


@dataclass
class Item:
    """这次聊天里的一题。"""

    said: str  # 它在这题之前说的话（招呼或回应）
    question: str
    aspect: str
    level: str
    follow_up: str
    answer: str | None = None  # None = 还没答；"" = 跳过


@dataclass
class Turn:
    """给界面显示的一轮：它说的话 + 问题。question 为空表示聊天结束。"""

    said: str
    question: str
    number: int
    wrap_hint: bool = False  # 已经聊了不少，界面上提示可以说「先到这」
    saved: Path | None = None


@dataclass
class ChatSession:
    root: Path
    plan: AskPlan
    now: dt.datetime
    items: list[Item] = field(default_factory=list)
    avoid_now: set[str] = field(default_factory=set)  # 这次聊天里说过不想聊的方面
    path: Path | None = None
    ended: bool = False
    meta: dict[str, object] | None = None  # 第一次存时定下，之后重写文件也不变（id 不能变）

    @property
    def label(self) -> str:
        return self._target().stem.removeprefix("访谈 ")

    def start(self) -> Turn:
        """确认之后调用：补好访谈设置，问第一个问题。"""
        settings_path = ensure_settings(self.root, now=self.now)
        if not interview.read_settings(settings_path).has_depth:
            interview.add_depth_section(settings_path)
        interview.add_auto_avoid(settings_path, self.plan.auto_avoid)
        return self._next(note="刚开始聊：reply 是一句简短的招呼，然后问第一个问题。")

    def answer(self, text: str) -> Turn:
        """你回答了当前这一题（或者说了「换个话题」「先到这」之类）。"""
        if self.ended or not self.items:
            raise RuntimeError("聊天已经结束了")
        current = self.items[-1]
        said = text.strip()
        command = _command(said)
        if command == "end":
            if current.answer is None:
                self.items.pop()  # 没答的这一题不算问过，也不算跳过
            self.ended = True
            saved = self._save()
            return Turn("好，今天就聊到这。谢谢你愿意说这么多。", "", len(self.items), saved=saved)
        if command in ("skip", "avoid"):
            current.answer = ""
            if command == "avoid" and current.aspect:
                self.avoid_now.add(current.aspect)
            self._save()
            note = (
                "TA说不想聊刚才那个话题：换一个完全不同的方面，回应一句「好，那我们聊点别的」之类，"
                "不要追问原因。"
                if command == "avoid"
                else "TA想换个话题：换一个别的方面问。"
            )
            return self._next(note=note)
        current.answer = said
        self._save()
        return self._next(note="回应TA刚说的话，再问下一个问题。")

    # ---------- 出下一题 ----------

    def _next(self, *, note: str) -> Turn:
        client = llm.get_client(self.root)
        problem = ""
        for _ in range(2):
            reply = client.complete_json(self._prompt(note, problem), _Turn, system=CHAT_SYSTEM)
            problem = self._check(reply)
            if not problem:
                break
        said = _trim(reply.reply)
        if problem:
            question, aspect = self._fallback()
            item = Item(said, question, aspect, "light", "")
        else:
            refs = self._refs()
            ref = refs.get(interview.ref_key(reply.follow_up), "") if reply.follow_up else ""
            level = reply.depth if reply.depth in interview.LEVELS else "light"
            item = Item(said, " ".join(reply.question.split()), reply.aspect, level, ref)
        self.items.append(item)
        return Turn(item.said, item.question, len(self.items), wrap_hint=len(self.items) > WRAP_AT)

    def _check(self, turn: _Turn) -> str:
        """问题不合格时说明原因（让 AI 重出）；合格返回空字符串。"""
        question = " ".join(turn.question.split())
        if not question:
            return "没有给出问题"
        aspect = turn.aspect if turn.aspect in interview.ASPECT_NAMES else ""
        turn.aspect = aspect
        settings = self.plan.settings
        if aspect and (aspect in settings.avoided_aspects or aspect in self.avoid_now):
            return f"「{interview.ASPECT_NAMES[aspect]}」TA不想聊"
        for word in settings.avoided_words:
            if word and (word in question or word in turn.reply):
                return f"问到了TA不想聊的「{word}」"
        level = turn.depth if turn.depth in interview.LEVELS else "light"
        if len(self.items) < LIGHT_FIRST and level != "light":
            return "刚开始聊，要先问轻松的事实和经历"
        _, deep_cap = interview.CAPS[settings.depth]
        medium_cap = 0 if settings.depth == "light" else 99
        caps = {"light": 99, "medium": medium_cap, "deep": deep_cap}
        used = sum(1 for item in self.items if item.level == level)
        if used >= caps[level]:
            return "这次聊天里深一点的问题已经够了，问轻松一点的"
        plain = interview.norm(question)
        asked = [
            interview.norm(q.question)
            for r in self.plan.rounds
            for q in r.questions
            if not q.reaskable(self.plan.today)
        ] + [interview.norm(item.question) for item in self.items]
        if any(interview.similar(plain, old) for old in asked):
            return "这个问题问过了"
        return ""

    def _fallback(self) -> tuple[str, str]:
        asked = [interview.norm(item.question) for item in self.items] + [
            interview.norm(q.question) for r in self.plan.rounds for q in r.questions
        ]
        for question, aspect in FALLBACKS:
            if not any(interview.similar(interview.norm(question), old) for old in asked):
                return question, aspect
        return FALLBACKS[0]

    def _prompt(self, note: str, problem: str) -> str:
        settings = self.plan.settings
        _, deep_cap = interview.CAPS[settings.depth]
        deep_used = sum(1 for item in self.items if item.level == "deep")
        lines = [
            self.plan.text,
            "",
            "## 这次聊天",
            f"已经问了 {len(self.items)} 个问题；深一点的（deep）已经问了 {deep_used} 个，"
            f"这次最多 {deep_cap} 个"
            + ("；中等的（medium）也不要问" if settings.depth == "light" else "")
            + "。",
        ]
        if len(self.items) < LIGHT_FIRST:
            lines.append("还在开头：这一题必须是 light。")
        if self.avoid_now:
            names = "、".join(interview.ASPECT_NAMES[a] for a in sorted(self.avoid_now))
            lines.append(f"这次聊天里TA说过不想聊：{names}（不要再问）。")
        for number, item in enumerate(self.items, start=1):
            lines.append(f"它：{item.said}")
            lines.append(f"问（第 {number} 题）：{item.question}")
            if item.answer is not None:
                lines.append(f"[{self.label} 第 {number} 题] 答：{item.answer or '（跳过了）'}")
        lines += ["", note]
        if problem:
            lines.append(f"上一次给的问题不行：{problem}。请换一个。")
        return "\n".join(lines)

    def _refs(self) -> dict[str, str]:
        refs = {
            interview.ref_key(q.ref): q.ref
            for r in self.plan.rounds
            for q in r.questions
            if q.answer and not q.undecided
        }
        for number, item in enumerate(self.items, start=1):
            if item.answer:
                ref = f"{self.label} 第 {number} 题"
                refs[interview.ref_key(ref)] = ref
        return refs

    # ---------- 存文件 ----------

    def _target(self) -> Path:
        if self.path is not None:
            return self.path
        folder = self.root / PROFILE_DIR
        day = self.now.date().isoformat()
        for n in range(1, 1000):
            title = f"访谈 {day} 聊天" if n == 1 else f"访谈 {day} 聊天 {n}"
            candidate = folder / f"{title}.md"
            if not candidate.exists():
                self.path = candidate
                return candidate
        raise FileExistsError(f"{folder} 里今天的聊天太多了")

    def _save(self) -> Path | None:
        """把整次聊天写进文件：第一次答题时新建，之后整份重写（只写答过的题）。"""
        done = [item for item in self.items if item.answer is not None]
        if not done:
            return None
        path = self._target()
        body = [
            "> 这是和 AI「聊聊」的记录。「（它：……）」是它说的话，「你的回答」是你说的。",
            "> 可以在这里改错字、补几句；下次生成画像时会用上你的回答。空着的是你跳过的。",
            "",
        ]
        for number, item in enumerate(done, start=1):
            body += [f"### {number}. {item.question}", ""]
            if item.said:
                body += [f"（它：{item.said}）", ""]
            tags = [interview.ASPECT_NAMES[item.aspect]] if item.aspect else []
            if item.follow_up:
                tags.append(f"追问 {item.follow_up}")
            if item.level == "deep":
                tags.append("深一点")
            if tags and item.aspect:
                body += [f"（方面：{' · '.join(tags)}）", ""]
            answer = (item.answer or "").split("\n")
            body.append(ANSWER_MARK + answer[0])
            body += [f"> {line}" for line in answer[1:]]
            body.append("")
        if self.meta is None:
            self.meta = note_meta(path.stem, self.now)
        text = render_document(self.meta, "\n".join(body))
        if path.exists():
            atomic_write(path, text)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            create_new(path, text)
        return path


def plan_chat(root: Path, *, now: dt.datetime | None = None) -> ChatSession:
    """准备一次聊天（不发任何东西）：和出访谈题一样读画像、设置、以前的回答。"""
    when = (now or dt.datetime.now(dt.UTC)).replace(microsecond=0)
    return ChatSession(root, plan_ask(root, now=when), when)


def _command(text: str) -> str:
    plain = text.strip().strip("。.！!~～ ")
    if not plain:
        return "skip"
    if len(plain) <= 8:
        if any(plain.startswith(w) for w in END_WORDS):
            return "end"
        if any(plain.startswith(w) for w in AVOID_WORDS):
            return "avoid"
        if any(plain.startswith(w) for w in SKIP_WORDS):
            return "skip"
    return ""


def _trim(text: str) -> str:
    said = " ".join(text.split())
    if len(said) <= REPLY_CHARS:
        return said
    cut = said[:REPLY_CHARS]
    last = max(cut.rfind(end) for end in _ENDS)
    return cut[: last + 1] if last > 0 else cut + "…"
