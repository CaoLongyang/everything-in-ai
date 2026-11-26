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
from eia.profile.prompts import CATEGORIES
from eia.profile.synth import HIDDEN, Conclusion, Rejected, Section
from eia.schema.ids import new_id
from eia.schema.io import FrontmatterError, create_new, read_document, render_document
from eia.schema.model import SCHEMA_VERSION, EntryType, Status
from eia.schema.validate import iter_markdown
from eia.schema.vault import dir_for

QUOTE_CHARS = 100
ALIAS_CHARS = 30  # 链接显示的对话标题；Gemini 常拿第一句话当标题，很长
EVIDENCE_SHOWN = 5
REJECT_BOX = "不像我"
REJECTED_HEADING = "## 你说过不像你的"
_CHECKED = re.compile(r"^- \[[xX]\] " + REJECT_BOX + r"｜(?P<text>.+?)（把握：[^）]*）(?P<why>.*)$")
_CARRIED = re.compile(r"^- (?P<text>.+?)(?:——原因：(?P<why>.*))?$")
_SECTION_NAMES = {**CATEGORIES, HIDDEN: "你可能没意识到的你"}


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
    in_carried = False
    for line in read_document(path).body.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            in_carried = line.strip() == REJECTED_HEADING
            continue
        match = _CHECKED.match(line.strip())
        if match is not None:
            text = match["text"].strip()
            found[text] = Rejected(text, _why(match["why"]))
            continue
        carried = _CARRIED.match(line.strip()) if in_carried else None
        if carried is not None:
            text = carried["text"].strip()
            found.setdefault(text, Rejected(text, (carried["why"] or "").strip()))
    return list(found.values())


def write_profile(
    root: Path,
    sections: Sequence[Section],
    rejected: Sequence[Rejected],
    *,
    links: dict[str, ChatLink],
    period: tuple[dt.date, dt.date],
    chats: int,
    model: str,
    now: dt.datetime,
    tz: dt.tzinfo | None,
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
        body = render_body(sections, rejected, links=links, span=span, chats=chats)
        try:
            create_new(path, render_document(meta, body))
        except FileExistsError:
            continue
        return path
    raise FileExistsError(f"{folder} 里今天的画像太多了")


def render_body(
    sections: Sequence[Section],
    rejected: Sequence[Rejected],
    *,
    links: dict[str, ChatLink],
    span: str,
    chats: int,
) -> str:
    out = [
        f"> 根据 {chats} 个对话（{span}）生成。「推断」是 AI 的判断，不是你说过的话。",
        "> 觉得哪条不像你，把 `[ ]` 改成 `[x]`，可以在后面写原因。下次生成时会避开。",
        "",
    ]
    for section in sections:
        out += [f"## {_SECTION_NAMES[section.key]}", ""]
        for kind, heading in (("stated", "你说过的"), ("inferred", "推断")):
            items = [c for c in section.conclusions if c.kind == kind]
            if section.key == HIDDEN and kind == "stated":
                continue
            out += [f"### {heading}", ""]
            out += [line for c in items for line in _conclusion(c, links)] or ["（没有）"]
            out.append("")
        out += ["### 未解问题", ""]
        out += [f"- {q}" for q in section.open_questions] or ["（没有）"]
        out.append("")
        if section.omitted:
            out += [f"> 观察太多，最早的 {section.omitted} 条没有参与这一类的汇总。", ""]
    if rejected:
        out += [REJECTED_HEADING, "", "> 下次生成时也会避开这些。想撤回，删掉那一行就行。", ""]
        out += [f"- {r.text}" + (f"——原因：{r.why}" if r.why else "") for r in rejected]
        out.append("")
    return "\n".join(out)


def _conclusion(c: Conclusion, links: dict[str, ChatLink]) -> list[str]:
    count = f"{c.chats} 个对话" + (f"、{c.days} 天" if c.days else "")
    lines = [f"- [ ] {REJECT_BOX}｜{_one_line(c.text)}（把握：{c.confidence}｜{count}）"]
    for obs in c.evidence[:EVIDENCE_SHOWN]:
        lines.append(f"  - {_evidence(obs, links)}")
    if len(c.evidence) > EVIDENCE_SHOWN:
        lines.append(f"  - ……还有 {len(c.evidence) - EVIDENCE_SHOWN} 条")
    return lines


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
