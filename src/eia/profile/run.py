"""`eia profile run`：把四步串起来（docs/specs/profile.md 第 3、8 节）。

不加 --yes 只做第 1 步：告诉你要发多少，什么都不发。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from eia import llm
from eia.profile import extract as extract_mod
from eia.profile.document import ChatLink, latest_profile, read_rejected, write_profile
from eia.profile.extract import Observation
from eia.profile.material import ChatMaterial, collect
from eia.profile.synth import Profile, synthesize
from eia.schema.vault import load_config

DEFAULT_MONTHS = 3


class ProfileError(Exception):
    """范围内没有可用的对话，或者参数不对。"""


@dataclass
class Plan:
    since: dt.date
    until: dt.date
    materials: list[ChatMaterial]
    cached: dict[str, list[Observation]]  # 材料的 key → 已有的观察
    model: str
    service: str
    skipped_files: int = 0

    @property
    def to_send(self) -> list[ChatMaterial]:
        return [m for m in self.materials if m.key(self.model) not in self.cached]

    @property
    def chars(self) -> int:
        return sum(m.chars for m in self.materials)

    @property
    def send_chars(self) -> int:
        return sum(m.chars for m in self.to_send)


@dataclass
class Result:
    path: Path
    profile: Profile
    observations: int
    dropped: int
    extract_requests: int
    rejected: int
    counts: dict[str, int] = field(default_factory=dict)  # 每章的要点数


def default_since(until: dt.date, months: int = DEFAULT_MONTHS) -> dt.date:
    """往前推几个整月；那个月没有这一天时取月底（05-31 往前 3 个月是 02-28）。"""
    month = until.month - months
    year = until.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    for day in range(until.day, 0, -1):
        try:
            return dt.date(year, month, day)
        except ValueError:
            continue
    raise ProfileError(f"算不出 {until} 往前 {months} 个月的日期")


def plan_profile(
    root: Path,
    *,
    since: dt.date | None = None,
    until: dt.date | None = None,
    today: dt.date | None = None,
) -> Plan:
    tz = load_config(root).timezone
    until = until or today or dt.datetime.now(tz).date()
    since = since or default_since(until)
    if since > until:
        raise ProfileError(f"开始日期 {since} 晚于结束日期 {until}")
    materials, skipped = collect(root, tz, since, until)
    if not materials:
        raise ProfileError(f"{since} ~ {until} 之间没有你说过的话，换个时间范围试试（--since）")
    text = llm.get_client(root).config.text
    cached = extract_mod.cached(root, (m.key(text.model) for m in materials))
    return Plan(
        since, until, materials, cached, text.model, text.api_base or text.provider, skipped
    )


def run_profile(
    root: Path,
    plan: Plan,
    *,
    now: dt.datetime | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Result:
    """第 2–4 步。任何一步出错都不写画像文件；第 2 步已完成的对话留在缓存里。"""
    tz = load_config(root).timezone
    client = llm.get_client(root)
    extracted = extract_mod.extract(root, client, plan.to_send, progress=progress)
    observations: list[Observation] = []
    for material in plan.materials:
        key = material.key(plan.model)
        if key in plan.cached:
            observations.extend(plan.cached[key])
        else:
            observations.extend(extracted.observations.get(material.chat.id, []))
    previous = latest_profile(root)
    rejected = read_rejected(previous)
    titles = {m.chat.id: m.chat.title for m in plan.materials}
    profile = synthesize(client, observations, rejected, titles=titles)
    links = {m.chat.id: ChatLink(m.chat.title, m.chat.path.stem) for m in plan.materials}
    path = write_profile(
        root,
        profile,
        rejected,
        links=links,
        period=(plan.since, plan.until),
        chats=len(plan.materials),
        model=plan.model,
        now=now or dt.datetime.now(dt.UTC),
        tz=tz,
    )
    return Result(
        path=path,
        profile=profile,
        observations=len(observations),
        dropped=extracted.dropped,
        extract_requests=extracted.requests,
        rejected=len(rejected),
        counts={c.title: sum(len(s.points) for s in c.subsections) for c in profile.chapters},
    )
