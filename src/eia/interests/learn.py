"""`eia interests learn`：根据评价让 AI 提出兴趣清单的改进建议，写进草稿。

规格：docs/specs/interest-digest.md 第 9 节。

发出去的只有：现有的兴趣清单、被评价报道的标题和媒体（公开信息）、用户写的理由。不确认就不发。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from eia import llm
from eia.interests.config import Interest, InterestsError, load_interests
from eia.interests.draft import DraftItem
from eia.interests.feedback import Feedback, read_feedback
from eia.interests.prompts import LEARN_SYSTEM

MAX_FEEDBACK = 200
CHANGES = ("保留", "修改", "新增", "删除")


@dataclass
class LearnPlan:
    prompt: str
    feedback: int
    model: str
    service: str

    @property
    def chars(self) -> int:
        return len(self.prompt)


def _line(record: Feedback) -> str:
    verdict = {True: "相关", False: "不相关", None: ""}[record.relevant]
    parts = [p for p in (verdict, f"{record.stars} 星" if record.stars else "") if p]
    head = "、".join(parts) or "无评分"
    tail = f"｜理由：{record.why}" if record.why else ""
    matched = f"｜当时匹配的兴趣：{record.interest}" if record.interest else ""
    return f"- [{head}] {record.title}（{record.publisher}）{matched}{tail}"


def plan_learn(root: Path) -> LearnPlan:
    interests = load_interests(root)
    feedback = read_feedback(root)[-MAX_FEEDBACK:]
    if not feedback:
        raise InterestsError("还没有任何评价。先在今日兴趣里给几篇报道评价，再来提炼")
    current = "\n".join(
        f"- {i.name}：{i.description}" + (f"（不要：{i.exclude}）" if i.exclude else "")
        for i in interests
    )
    prompt = (
        f"现在的兴趣清单：\n{current or '（空）'}\n\n"
        f"最近的评价（{len(feedback)} 条）：\n" + "\n".join(_line(r) for r in feedback)
    )
    text = llm.get_client(root).config.text
    return LearnPlan(prompt, len(feedback), text.model, text.api_base or text.provider)


class _Suggested(BaseModel):
    name: str
    description: str = ""
    exclude: str = ""
    change: str = "保留"


class _Suggestions(BaseModel):
    interests: list[_Suggested]


def learn(root: Path, plan: LearnPlan) -> list[DraftItem]:
    """用户确认后才调用。删除建议默认不勾，其余默认勾上。"""
    result = llm.get_client(root).complete_json(plan.prompt, _Suggestions, system=LEARN_SYSTEM)
    items: list[DraftItem] = []
    seen: set[str] = set()
    # 提炼只改名字、说明和 exclude；搜索关键词沿用同名兴趣原来的
    searches = {interest.name: interest.search for interest in load_interests(root)}
    for s in result.interests:
        name = s.name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        change = s.change if s.change in CHANGES else "修改"
        interest = Interest(name, s.description.strip(), s.exclude.strip(), searches.get(name, ()))
        items.append(DraftItem(interest, checked=change != "删除", change=change))
    if not items:
        raise InterestsError("AI 没有给出任何建议，兴趣清单没有改动")
    return items
