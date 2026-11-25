"""合成的 ChatGPT、Claude 和 Gemini 导出：结构照真实导出（2026-11），内容全部是虚构的。"""

from __future__ import annotations

import datetime as dt
import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from eia.schema.vault import init_vault

T0 = 1741766482.0  # 2025-03-12T08:01:22Z


def msg(
    node_id: str,
    role: str,
    text: str | None = None,
    *,
    parent: str | None,
    t: float = T0,
    content: dict[str, Any] | None = None,
    hidden: bool = False,
) -> dict[str, Any]:
    body = content or {"content_type": "text", "parts": [text or ""]}
    metadata: dict[str, Any] = {"model_slug": "auto"}
    if hidden:
        metadata["is_visually_hidden_from_conversation"] = True
    return {
        "id": node_id,
        "parent": parent,
        "children": [],
        "message": {
            "id": node_id,
            "author": {"role": role, "name": None},
            "content": body,
            "create_time": t,
            "metadata": metadata,
            "recipient": None,
        },
    }


def conversation(
    cid: str,
    turns: list[tuple[str, str]],
    *,
    title: str = "虚构对话",
    t: float = T0,
    updated: float | None = None,
    extra_nodes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """turns 是 [(role, text), ...]，按顺序挂成一条链；extra_nodes 可加分支。"""
    mapping: dict[str, Any] = {
        "root": {"id": "root", "parent": None, "children": [], "message": None}
    }
    parent = "root"
    for index, (role, text) in enumerate(turns):
        node_id = f"{cid}-m{index}"
        mapping[node_id] = msg(node_id, role, text, parent=parent, t=t + index * 60)
        mapping[parent]["children"].append(node_id)
        parent = node_id
    for node in extra_nodes or []:
        mapping[node["id"]] = node
        if node["parent"] in mapping:
            mapping[node["parent"]]["children"].append(node["id"])
    return {
        "conversation_id": cid,
        "id": cid,
        "title": title,
        "create_time": t,
        "update_time": updated if updated is not None else t + len(turns) * 60,
        "current_node": parent,
        "default_model_slug": "auto",
        "mapping": mapping,
        "is_archived": False,
    }


@pytest.fixture
def make_export(tmp_path: Path) -> Callable[..., Path]:
    def _make(
        conversations: list[dict[str, Any]],
        *,
        name: str = "export.zip",
        shard_size: int = 100,
        single_file: bool = False,
        extra_files: dict[str, str] | None = None,
    ) -> Path:
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as archive:
            if single_file:
                archive.writestr("conversations.json", json.dumps(conversations))
            else:
                for start in range(0, max(len(conversations), 1), shard_size):
                    shard = conversations[start : start + shard_size]
                    archive.writestr(
                        f"conversations-{start // shard_size:03d}.json", json.dumps(shard)
                    )
            archive.writestr("user.json", json.dumps({"email": "secret@example.com"}))
            for file_name, content in (extra_files or {}).items():
                archive.writestr(file_name, content)
        return path

    return _make


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    init_vault(root)
    (root / "config.yaml").write_text("schema: 1\ntimezone: Asia/Singapore\n", encoding="utf-8")
    return root


# ---------- Claude 官方导出（结构照 2026-11 的真实导出，内容全部虚构） ----------

NIL = "00000000-0000-4000-8000-000000000000"


def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "citations": [], "start_timestamp": None}


def thinking_block(text: str) -> dict[str, Any]:
    return {"type": "thinking", "thinking": text, "summaries": [], "thinking_hidden": True}


def tool_use_block(name: str = "Bash") -> dict[str, Any]:
    return {"type": "tool_use", "id": "toolu_1", "name": name, "input": {"command": "ls 秘密"}}


def tool_result_block() -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "name": "",
        "content": [{"type": "text", "text": "工具输出不应出现"}],
        "is_error": False,
    }


def injected_block() -> dict[str, Any]:
    return {"type": "injected_prompt_block", "prompt": "系统插入的提示不应出现"}


def claude_msg(
    uuid: str,
    sender: str,
    blocks: list[dict[str, Any]],
    *,
    parent: str,
    t: float = T0,
    files: list[dict[str, Any]] | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "uuid": uuid,
        "parent_message_uuid": parent,
        "sender": sender,
        "text": "顶层 text 字段不应被使用",
        "content": blocks,
        "attachments": attachments or [],
        "files": files or [],
        "created_at": iso(t),
        "updated_at": iso(t),
    }


def claude_conversation(
    cid: str,
    turns: list[tuple[str, str | list[dict[str, Any]]]],
    *,
    name: str = "虚构对话",
    t: float = T0,
    updated: float | None = None,
    extra: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """turns 是 [(sender, 文字或内容块列表), ...]，按顺序挂成一条链；extra 可加分支。"""
    messages: list[dict[str, Any]] = []
    parent = NIL
    for index, (sender, content) in enumerate(turns):
        blocks = [text_block(content)] if isinstance(content, str) else content
        uuid = f"{cid}-m{index}"
        messages.append(claude_msg(uuid, sender, blocks, parent=parent, t=t + index * 60))
        parent = uuid
    messages += extra or []
    return {
        "uuid": cid,
        "name": name,
        "summary": "平台自动生成的摘要不应出现",
        "created_at": iso(t),
        "updated_at": iso(updated if updated is not None else t + len(turns) * 60),
        "account": {"uuid": "acct-虚构"},
        "chat_messages": messages,
    }


@pytest.fixture
def make_claude_export(tmp_path: Path) -> Callable[..., Path]:
    def _make(
        conversations: list[dict[str, Any]] | None = None,
        *,
        name: str = "conversations-000.zip",
        raw: str | None = None,
        members: dict[str, str] | None = None,
    ) -> Path:
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as archive:
            if members is not None:
                for member, content in members.items():
                    archive.writestr(member, content)
            else:
                body = raw if raw is not None else json.dumps(conversations or [])
                archive.writestr("conversations.json", body)
        return path

    return _make


# ---------- Gemini 浏览器插件导出（结构照 2026-11-25 的新版导出，内容全部虚构） ----------


def gemini_conversation(
    cid: str,
    turns: list[tuple[str, str]],
    *,
    title: str = "虚构的 Gemini 对话",
    t: float = T0,
    updated: float | None = None,
) -> dict[str, Any]:
    """turns 是 (role, content)；同一轮（我 + 紧接着的 Gemini）共用一个时间，和真实导出一样。"""
    messages: list[dict[str, Any]] = []
    stamp = t
    for index, (role, content) in enumerate(turns):
        if role == "user" and index > 0:
            stamp += 120
        elif role == "assistant" and index > 0 and turns[index - 1][0] == "assistant":
            stamp += 30  # Gemini 连续发言，各自有时间
        messages.append({"role": role, "time": iso(stamp)[:23] + "Z", "content": content})
    last = stamp if updated is None else updated
    return {
        "conversationId": f"c_{cid}",
        "title": title,
        "url": f"https://gemini.google.com/u/1/app/{cid}",
        "createdAt": iso(t)[:23] + "Z",
        "updatedAt": iso(last)[:23] + "Z",
        "exportedAt": iso(T0 + 10**7)[:23] + "Z",
        "turnCount": sum(1 for role, _ in turns if role == "user"),
        "messageCount": len(messages),
        "messages": messages,
    }


def gemini_report(**changes: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "generatedAt": iso(T0 + 10**7)[:23] + "Z",
        "listTotal": 3,
        "exported": 3,
        "stoppedManually": False,
        "failures": [],
        "missing": [],
        "possiblyIncomplete": [],
    }
    report.update(changes)
    return report


@pytest.fixture
def make_gemini_export(tmp_path: Path) -> Callable[..., Path]:
    def _make(
        conversations: list[dict[str, Any]] | None = None,
        *,
        name: str = "gemini_chats_2026-11-25.zip",
        report: dict[str, Any] | None = None,
        members: dict[str, str] | None = None,
    ) -> Path:
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as archive:
            if members is not None:
                for member, content in members.items():
                    archive.writestr(member, content)
                return path
            for index, raw in enumerate(conversations or []):
                archive.writestr(f"{index + 1:04d}_{raw['title']}.json", json.dumps(raw))
            archive.writestr("_report.json", json.dumps(report or gemini_report()))
        return path

    return _make
