"""第 4 步：写画像文件；读上一版里你标了「不像我」的结论（docs/specs/profile.md 第 5、7 节）。

每次新建一个文件，不覆盖旧版本，也不改你已有的画像。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from eia.profile.extract import Observation
from eia.profile.synth import DATED, WISHES, Point, Profile, Rejected
from eia.schema.ids import new_id
from eia.schema.io import FrontmatterError, create_new, read_document, render_document
from eia.schema.model import SCHEMA_VERSION, EntryType, Status
from eia.schema.validate import iter_markdown
from eia.schema.vault import dir_for

QUOTE_CHARS = 100
ALIAS_CHARS = 30  # 链接显示的对话标题；Gemini 常拿第一句话当标题，很长
EVIDENCE_SHOWN = 8
EMPTY = "（材料里没有）"
REJECT_BOX = "不像我"
REJECTED_HEADING = "## 你说过不像你的"
CHANGED_HEADING = "## 你说过已经变了的"
OUTDATED = "过时"  # 勾掉一条、原因以它开头：当时没错，现在变了（profile.delta-v2b.md 第 3.2 节）
# 第一、二版：「- [x] 不像我｜结论（把握：…）原因」
_CHECKED = re.compile(r"^- \[[xX]\] " + REJECT_BOX + r"｜(?P<text>.+?)（把握：[^）]*）(?P<why>.*)$")
# 现在：「- [x] **日期** 结论[^3] 原因」，日期只有决策记录和时间线有
_CHECKED_NOTE = re.compile(
    r"^- \[[xX]\] (?:\*\*(?P<day>\d{4}-\d{2}-\d{2}|日期未知)\*\* )?"
    r"(?P<text>.+?)\[\^\d+\](?P<why>.*)$"
)
_CARRIED = re.compile(r"^- (?P<text>.+?)(?:——原因：(?P<why>.*))?$")


@dataclass(frozen=True)
class ChatLink:
    title: str
    stem: str  # 对话文件名去掉 .md，用来写 [[链接]]


def latest_profile(root: Path) -> Path | None:
    """最新一份画像（按 created）。读不了的文件跳过。"""
    best: tuple[str, Path] | None = None
    for path in iter_markdown(root):
        try:
            meta = read_document(path).meta
        except FrontmatterError:
            continue
        if meta.get("type") != EntryType.profile.value:
            continue
        created = str(meta.get("created") or "")
        if best is None or created > best[0]:
            best = (created, path)
    return best[1] if best else None


def read_rejected(path: Path | None) -> list[Rejected]:
    """勾了「不像我」的结论，加上那份画像里已经带过来的「你说过不像你的」。"""
    if path is None:
        return []
    found: dict[str, Rejected] = {}
    carried_kind: bool | None = None  # 在哪一节：None 不是带过来的；False 不像我；True 已经变了
    for line in read_document(path).body.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            heading = line.strip()
            carried_kind = (
                False
                if heading == REJECTED_HEADING
                else True
                if heading == CHANGED_HEADING
                else None
            )
            continue
        match = _CHECKED.match(line.strip())
        if match is not None:
            text = match["text"].strip()
            found[text] = _rejected(text, match["why"])
            continue
        noted = _CHECKED_NOTE.match(line.strip())
        if noted is not None:
            text = noted["text"].strip().replace("**", "")
            text = f"{noted['day']}｜{text}" if noted["day"] else text
            found[text] = _rejected(text, noted["why"])
            continue
        carried = _CARRIED.match(line.strip()) if carried_kind is not None else None
        if carried is not None:
            text = carried["text"].strip()
            why = (carried["why"] or "").strip()
            found.setdefault(text, Rejected(text, why, outdated=bool(carried_kind)))
    return list(found.values())


def write_profile(
    root: Path,
    profile: Profile,
    rejected: Sequence[Rejected],
    *,
    links: dict[str, ChatLink],
    period: tuple[dt.date, dt.date],
    chats: int,
    model: str,
    now: dt.datetime,
    tz: dt.tzinfo | None,
    skipped: int = 0,
) -> Path:
    folder = root / dir_for(EntryType.profile.value)
    folder.mkdir(parents=True, exist_ok=True)
    day = now.astimezone(tz).date().isoformat()
    span = f"{period[0].isoformat()} ~ {period[1].isoformat()}"
    for number in range(1, 1000):
        title = f"{day} 画像" if number == 1 else f"{day} 画像 {number}"
        path = folder / f"{title}.md"
        if path.exists():
            continue
        meta = {
            "schema": SCHEMA_VERSION,
            "id": new_id(EntryType.profile.value, now),
            "type": EntryType.profile.value,
            "title": title,
            "created": now.replace(microsecond=0),
            "status": Status.inbox.value,
            "tags": [],
            "source": "eia-profile",
            "period": span,
            "chats": chats,
            "model": model,
        }
        body = render_body(profile, rejected, links=links, span=span, chats=chats, skipped=skipped)
        try:
            create_new(path, render_document(meta, body))
        except FileExistsError:
            continue
        return path
    raise FileExistsError(f"{folder} 里今天的画像太多了")


def render_body(
    profile: Profile,
    rejected: Sequence[Rejected],
    *,
    links: dict[str, ChatLink],
    span: str,
    chats: int,
    skipped: int = 0,
) -> str:
    notes: list[str] = []  # 文末的证据脚注
    out = [
        "# 个人画像",
        "",
        f"> 根据 {chats} 个对话（{span}）生成。写「你似乎……」的是 AI 的推断，不是你说过的话。",
        "> 每条后面的小数字是证据，在文末。觉得哪条不像你，勾上它前面的方框，后面可以写原因；"
        "下次生成时会避开。",
        "> 这份画像包含你的个人信息（学号、住址等），分享或同步到网上之前先看一遍。",
        *(
            [
                f"> 另有 {skipped} 个对话这次没分析成（AI 的回复格式不对），"
                "没算进这份画像；下次生成时会重试。"
            ]
            if skipped
            else []
        ),
        "",
        "**一句话的你：** " + (" · ".join(profile.labels) or "（还没有）"),
        "",
        "## 目录",
        "",
    ]
    for chapter in profile.chapters:
        names = "、".join(_one_line(s.title) for s in chapter.subsections if s.title and s.points)
        out.append(f"- **{chapter.title}**" + (f"：{names}" if names else ""))
    out += ["- **07 给 AI 的说明**：系统提示词、分析与对话准则", ""]
    if profile.omitted:
        out += [f"> 观察太多，按时间均匀挑了一部分，有 {profile.omitted} 条没有参与汇总。", ""]
    for chapter in profile.chapters:
        out += ["---", "", f"## {chapter.title}", ""]
        if not chapter.subsections:
            out += [EMPTY, ""]
        for sub in chapter.subsections:
            if sub.title:
                out += [f"### {_one_line(sub.title)}", ""]
            if not sub.points:
                out += [EMPTY, ""]
                continue
            intro = " —— ".join(
                part
                for part in (sub.label and f"**{_one_line(sub.label)}**", _one_line(sub.summary))
                if part
            )
            if intro:
                out += [f"> {intro}", ""]
            for point in sub.points:
                out.append(
                    _point(
                        point, links, notes, dated=chapter.key in DATED, wish=chapter.key == WISHES
                    )
                )
            out.append("")
    out += ["---", "", "## 07 给 AI 的说明", "", "### 系统提示词", ""]
    if profile.system_prompt:
        out += ["> 可以直接复制给 ChatGPT、Claude 等 AI，让它一开始就了解你。", ""]
        out += ["````text", profile.system_prompt.replace("````", "```"), "````", ""]
    else:
        out += [EMPTY, ""]
    out += ["### 分析与对话准则", ""]
    out += [f"- {_one_line(g)}" for g in profile.guidelines] or [EMPTY]
    out += ["", "---", "", "## 未解问题", ""]
    out += [f"- {_one_line(q)}" for q in dict.fromkeys(profile.open_questions)] or ["（没有）"]
    out.append("")
    wrong = [r for r in rejected if not r.outdated]
    changed = [r for r in rejected if r.outdated]
    if wrong:
        out += [REJECTED_HEADING, "", "> 下次生成时也会避开这些。想撤回，删掉那一行就行。", ""]
        out += [f"- {r.text}" + (f"——原因：{r.why}" if r.why else "") for r in wrong]
        out.append("")
    if changed:
        out += [
            CHANGED_HEADING,
            "",
            "> 当时没错、现在变了的。下次生成时写进时间线当作过去的事。",
            "",
        ]
        out += [f"- {r.text}" + (f"——原因：{r.why}" if r.why else "") for r in changed]
        out.append("")
    if notes:
        out += ["---", "", "## 证据", "", *notes, ""]
    return "\n".join(out)


def _point(
    point: Point, links: dict[str, ChatLink], notes: list[str], *, dated: bool, wish: bool = False
) -> str:
    """正文一行；证据作为脚注加到 notes。"""
    number = len(notes) + 1
    count = f"{point.chats} 个对话" + (f"、{point.days} 天" if point.days else "")
    shown = [_evidence(obs, links) for obs in point.evidence[:EVIDENCE_SHOWN]]
    if len(point.evidence) > EVIDENCE_SHOWN:
        shown.append(f"……还有 {len(point.evidence) - EVIDENCE_SHOWN} 条")
    notes.append(f"[^{number}]: 把握{point.confidence} · {count}：" + "；".join(shown))
    text = _one_line(point.text)
    if wish:
        first, last = _month_day(point.first_day), _month_day(point.last_day)
        return f"- [ ] **{text}** · {point.status} · 首次 {first} · 最近 {last}[^{number}]"
    when = f"**{point.first_day or '日期未知'}** " if dated else ""
    as_of = f"（截至 {point.last_day[:7]}）" if point.kind == "state" and point.last_day else ""
    return f"- [ ] {when}{text}{as_of}[^{number}]"


def _month_day(day: str | None) -> str:
    return day[5:] if day else "日期未知"


def _rejected(text: str, why: str) -> Rejected:
    reason = _why(why)
    if reason.startswith(OUTDATED):
        return Rejected(
            text, reason.removeprefix(OUTDATED).lstrip("：:，, ").strip(), outdated=True
        )
    return Rejected(text, reason)


def _evidence(obs: Observation, links: dict[str, ChatLink]) -> str:
    own = next((q for q in obs.quotes if q.kind != "ai"), obs.quotes[0])
    quote = _one_line(own.text)
    quote = quote if len(quote) <= QUOTE_CHARS else quote[:QUOTE_CHARS] + "…"
    link = links.get(obs.chat_id)
    alias = _one_line(link.title).replace("|", "｜") if link else ""
    alias = alias if len(alias) <= ALIAS_CHARS else alias[:ALIAS_CHARS] + "…"
    where = f"[[{link.stem}|{alias}]]" if link else "（对话文件找不到了）"
    pasted = "；同时贴回了 AI 的一段话" if any(q.kind == "ai" for q in obs.quotes) else ""
    return f"「{quote}」—— {where}{pasted}"


def _one_line(text: str) -> str:
    return " ".join(text.replace("[[", "[ [").replace("]]", "] ]").split())


def _why(text: str) -> str:
    return text.strip().removeprefix("原因").lstrip("：:，, ").strip()
