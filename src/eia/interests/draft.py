"""`eia interests draft / accept`：从「我」说过的话归纳兴趣草稿（SPEC-interest-digest 第 3 节）。

第 1 步（本机，不出电脑）：每个对话把我说的话拼起来，用本机向量模型算向量，按主题归组。
第 2 步（要确认）：每组挑几段代表性摘录发给文本模型起名。不确认就停在第 1 步。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from eia import llm
from eia.chat.reader import ReadChat, Skipped, iter_chats
from eia.db import history
from eia.interests.config import Interest, InterestsError, load_interests
from eia.interests.prompts import NAMING_SYSTEM
from eia.interests.vectors import embed_client, embed_texts
from eia.schema.configedit import update_section
from eia.schema.ids import new_id
from eia.schema.io import atomic_write, render_document
from eia.schema.model import SCHEMA_VERSION, EntryType, Status
from eia.schema.vault import load_config

if TYPE_CHECKING:  # numpy 在函数里才导入（可选依赖 [news]）
    import numpy as np

DRAFT = Path("interests") / "draft.md"
DRAFT_BACKUP = Path(".eia") / "interests" / "draft.bak.md"
MIN_MESSAGE_CHARS = 15
EMBED_CHARS = 1500
EXCERPT_CHARS = 200
EXCERPTS_PER_GROUP = 5
MIN_GROUP_CHATS = 2
MIN_GROUP_MESSAGES = 5
DEFAULT_MAX = 20
START = "<!-- eia:interests:start 这一段由 eia interests 生成，重新生成时会被替换 -->"
END = "<!-- eia:interests:end -->"
_ITEM = re.compile(r"^- \[([ xX])\] \*\*(.+?)\*\*\s*[：:]?\s*(.*)$")
_EXCLUDE = re.compile(r"^\s+- 不要[：:]\s*(.*)$")


@dataclass
class ChatDoc:
    chat: ReadChat
    text: str
    messages: int
    first: dt.datetime | None
    last: dt.datetime | None


@dataclass
class Group:
    docs: list[ChatDoc]
    excerpts: list[str]

    @property
    def messages(self) -> int:
        return sum(doc.messages for doc in self.docs)


@dataclass
class Plan:
    """第 1 步的结果：发给文本模型之前，先把要发什么告诉用户。"""

    groups: list[Group]
    model: str
    service: str
    skipped_files: int = 0

    @property
    def excerpt_count(self) -> int:
        return sum(len(group.excerpts) for group in self.groups)

    @property
    def excerpt_chars(self) -> int:
        return sum(len(text) for group in self.groups for text in group.excerpts)


@dataclass
class DraftItem:
    interest: Interest
    checked: bool = True
    change: str = ""  # 保留 / 修改 / 新增 / 删除；空表示从对话归纳的新草稿
    evidence: list[str] = field(default_factory=list)


def collect_docs(root: Path) -> tuple[list[ChatDoc], int]:
    """每个对话一份：只有我说的话（含笔记），太短的消息跳过。"""
    tz = load_config(root).timezone
    docs: list[ChatDoc] = []
    skipped = 0
    for item in iter_chats(root, tz):
        if isinstance(item, Skipped):
            skipped += 1
            continue
        mine = [m for m in item.user_messages() if len(m.text.strip()) >= MIN_MESSAGE_CHARS]
        parts = [m.text.strip() for m in mine]
        if item.notes:
            parts.append(item.notes)
        if not parts:
            continue
        times = [m.time for m in mine if m.time is not None]
        docs.append(
            ChatDoc(
                chat=item,
                text="\n".join(parts),
                messages=len(mine),
                first=min(times) if times else None,
                last=max(times) if times else None,
            )
        )
    return docs, skipped


def _embed_text(doc: ChatDoc) -> str:
    return f"{doc.chat.title}\n{doc.text}"[:EMBED_CHARS]


def excerpt(doc: ChatDoc) -> str:
    text = " ".join(f"{doc.chat.title}：{doc.text}".split())
    return text[:EXCERPT_CHARS]


def kmeans(vectors: np.ndarray, k: int, *, iterations: int = 25, seed: int = 0) -> np.ndarray:
    """球面 k-means（余弦相似度），k-means++ 初始化，固定随机种子：同样的输入得到同样的分组。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    n = len(vectors)
    centers = [vectors[int(rng.integers(n))]]
    for _ in range(1, k):
        sims = np.max(vectors @ np.stack(centers).T, axis=1)
        weights = np.clip(1.0 - sims, 0.0, None) ** 2
        total = float(weights.sum())
        index = int(rng.choice(n, p=weights / total)) if total > 0 else int(rng.integers(n))
        centers.append(vectors[index])
    matrix = np.stack(centers)
    labels = np.zeros(n, dtype=int)
    for step in range(iterations):
        new_labels = np.argmax(vectors @ matrix.T, axis=1)
        if step > 0 and np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for c in range(k):
            members = vectors[labels == c]
            if len(members):
                center = members.sum(axis=0)
                matrix[c] = center / (np.linalg.norm(center) or 1.0)
    return labels


def plan_draft(root: Path, *, max_interests: int = DEFAULT_MAX) -> Plan:
    """第 1 步：本机向量归组。只用本机的向量模型；不调用文本模型。"""
    import numpy as np

    client = embed_client(root, require_local=True)
    docs, skipped = collect_docs(root)
    if len(docs) < MIN_GROUP_CHATS * 2:
        raise InterestsError(f"只找到 {len(docs)} 个有内容的对话，太少了，先导入一些对话")
    raw = np.asarray(embed_texts(root, client, [_embed_text(doc) for doc in docs]), dtype=float)
    vectors = raw / np.clip(np.linalg.norm(raw, axis=1, keepdims=True), 1e-12, None)
    # 先多分几组（最多兴趣数的两倍），小组和不成主题的组之后会被去掉
    k = max(2, min(max_interests * 2, len(docs) // 4))
    labels = kmeans(vectors, k)
    groups: list[Group] = []
    for c in range(k):
        members = np.flatnonzero(labels == c)
        group_docs = [docs[int(i)] for i in members]
        if (
            len(group_docs) < MIN_GROUP_CHATS
            or sum(d.messages for d in group_docs) < MIN_GROUP_MESSAGES
        ):
            continue
        center = vectors[members].mean(axis=0)
        order = members[np.argsort(-(vectors[members] @ center))]
        reps = [docs[int(i)] for i in order[:EXCERPTS_PER_GROUP]]
        groups.append(Group(group_docs, [excerpt(doc) for doc in reps]))
    groups.sort(key=lambda g: (-len(g.docs), -g.messages))
    # 多发一些组给文本模型：有的组会被判为不是长期兴趣（skip）
    groups = groups[: max_interests + max_interests // 2]
    text = llm.get_client(root).config.text
    return Plan(groups, text.model, text.api_base or text.provider, skipped)


class _Named(BaseModel):
    group: int
    name: str
    description: str = ""
    skip: bool = False


class _Naming(BaseModel):
    interests: list[_Named]


def name_groups(root: Path, plan: Plan, *, max_interests: int = DEFAULT_MAX) -> list[DraftItem]:
    """第 2 步（用户确认后）：把摘录发给文本模型起名。"""
    if not plan.groups:
        return []
    prompt = "\n\n".join(
        f"[第 {i} 组] 共 {len(g.docs)} 个对话、{g.messages} 条消息\n"
        + "\n".join(f"- {text}" for text in g.excerpts)
        for i, g in enumerate(plan.groups)
    )
    result = llm.get_client(root).complete_json(prompt, _Naming, system=NAMING_SYSTEM)
    items: list[DraftItem] = []
    seen: set[str] = set()
    for named in result.interests:
        if named.skip or not 0 <= named.group < len(plan.groups) or not named.name.strip():
            continue
        name = named.name.strip()
        if name in seen:
            continue
        seen.add(name)
        group = plan.groups[named.group]
        items.append(
            DraftItem(Interest(name, named.description.strip()), evidence=_evidence(group))
        )
    return items[:max_interests]


def _evidence(group: Group) -> list[str]:
    firsts = [d.first for d in group.docs if d.first]
    lasts = [d.last for d in group.docs if d.last]
    span = f"，{min(firsts):%Y-%m} 至 {max(lasts):%Y-%m}" if firsts and lasts else ""
    top = sorted(group.docs, key=lambda d: -d.messages)[:3]
    links = "、".join(f"[[{_link(d.chat.path)}|{d.chat.title}]]" for d in top)
    return [f"{len(group.docs)} 个对话、{group.messages} 条消息{span}；例如 {links}"]


def _link(path: Path) -> str:
    parts = path.with_suffix("").parts
    index = parts.index("chats") if "chats" in parts else max(0, len(parts) - 3)
    return "/".join(parts[index:])


def merge_with_current(root: Path, items: list[DraftItem]) -> list[DraftItem]:
    """现有的兴趣放在最前面（勾着），草稿里同名的不重复列。"""
    current = load_interests(root)
    names = {interest.name for interest in current}
    kept = [DraftItem(interest, change="保留") for interest in current]
    return kept + [item for item in items if item.interest.name not in names]


def render_draft(items: list[DraftItem], *, heading: str) -> str:
    lines = [
        START,
        "",
        heading,
        "",
        "勾上的就是你的兴趣清单，可以直接改名字和说明；「不要」后面写不想看的方向。",
        "改完运行 `eia interests accept`（或在试用页面点「确认」）才会生效。",
        "",
    ]
    for item in items:
        mark = "x" if item.checked else " "
        tag = f"（{item.change}）" if item.change and item.change != "保留" else ""
        lines.append(f"- [{mark}] **{item.interest.name}**：{item.interest.description}{tag}")
        lines.append(f"  - 不要：{item.interest.exclude}")
        lines.extend(f"  - 依据：{text}" for text in item.evidence)
    lines += ["", END, "", "（这条线以下是你自己的笔记，重新生成不会动）", ""]
    return "\n".join(lines)


def write_draft(
    root: Path, items: list[DraftItem], *, heading: str, now: dt.datetime | None = None
) -> Path:
    """覆盖 interests/draft.md（旧草稿备份到 .eia/interests/）。"""
    now = (now or dt.datetime.now(dt.UTC)).replace(microsecond=0)
    path = root / DRAFT
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        backup = root / DRAFT_BACKUP
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(path.read_bytes())
    meta = {
        "schema": SCHEMA_VERSION,
        "id": new_id(EntryType.note.value, now),
        "type": EntryType.note.value,
        "title": "兴趣草稿",
        "created": now,
        "status": Status.inbox.value,
        "tags": [],
        "source": "eia-interests",
    }
    atomic_write(path, render_document(meta, render_draft(items, heading=heading)))
    return path


def parse_draft(text: str) -> list[DraftItem]:
    items: list[DraftItem] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        match = _ITEM.match(line)
        if match:
            description = re.sub(r"（(保留|修改|新增|删除)）\s*$", "", match.group(3)).strip()
            items.append(
                DraftItem(Interest(match.group(2).strip(), description), match.group(1) != " ")
            )
            continue
        exclude = _EXCLUDE.match(line)
        if exclude and items:
            last = items[-1]
            items[-1] = DraftItem(
                Interest(last.interest.name, last.interest.description, exclude.group(1).strip()),
                last.checked,
            )
    return items


def accept(root: Path) -> list[Interest]:
    """把草稿里勾上的项写进 config.yaml 的 interests 段（整段替换）。"""
    path = root / DRAFT
    if not path.is_file():
        raise InterestsError("还没有兴趣草稿，先运行 eia interests draft")
    chosen = [
        item.interest for item in parse_draft(path.read_bytes().decode("utf-8")) if item.checked
    ]
    if not chosen:
        raise InterestsError("草稿里一项都没勾，没有改动配置")
    data = [interest.as_config() for interest in chosen]
    update_section(root, "interests", data)
    history.record(root, data, "accept")
    return chosen
