"""chat-sync：eia 这边的接收服务（docs/specs/chat-sync.md 第 4.3 节）。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from eia.chat import chatgpt, claude
from eia.chat.importer import import_conversations
from eia.cli import main
from eia.db import database
from eia.schema.io import read_document, update_frontmatter
from eia.schema.validate import validate_vault
from eia.sync import store
from tests.chat.conftest import (
    NIL,
    T0,
    claude_conversation,
    claude_msg,
    conversation,
    iso,
    text_block,
)

from .conftest import Running

HEADING = re.compile(r"^### (我|ChatGPT|Claude) · (\d{4}-\d{2}-\d{2} \d{2}:\d{2})$", re.MULTILINE)


def chat_files(root: Path, platform: str) -> list[Path]:
    return sorted((root / "chats" / platform).rglob("*.md"))


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".eia" not in p.parts
    }


def gpt(cid: str = "g1", turns: int = 2, **kwargs: Any) -> dict[str, Any]:
    pairs = [
        ("user", f"虚构的问题 {i}") if i % 2 == 0 else ("assistant", f"虚构的回答 {i}")
        for i in range(turns)
    ]
    return conversation(cid, pairs, **kwargs)


def headings(path: Path) -> list[tuple[str, str]]:
    return HEADING.findall(read_document(path).body)


# ---------- F19-01 只接受本机插件、带对口令的请求 ----------


def test_F19_01_rejects_bad_token_host_and_web_origin(serve: Callable[[], Running]) -> None:
    running = serve()
    before = snapshot(running.root)
    data = gpt()
    assert running.send("chatgpt", data, token=None).status == 401
    assert running.send("chatgpt", data, token="wrong-token").status == 401
    assert running.send("chatgpt", data, headers={"Host": "evil.example:80"}).status == 403
    web = running.send("chatgpt", data, headers={"Origin": "https://chatgpt.com"})
    assert web.status == 403
    assert running.call("GET", "/status", token=None).status == 401
    assert snapshot(running.root) == before
    assert chat_files(running.root, "chatgpt") == []

    ok = running.call("GET", "/status")
    assert ok.status == 200 and ok.body["ok"] is True
    extension = running.send("chatgpt", data, headers={"Origin": "chrome-extension://abc"})
    assert extension.status == 200


def test_F19_01_token_is_stable_and_shown_by_cli(vault: Path) -> None:
    first = store.token(vault)
    assert len(first) >= 30
    assert store.token(vault) == first
    result = CliRunner().invoke(main, ["sync", "token", "--vault", str(vault)])
    assert result.exit_code == 0 and result.output.strip() == first


# ---------- F19-02 ChatGPT ----------


def test_F19_02_chatgpt_conversation_becomes_valid_chat(serve: Callable[[], Running]) -> None:
    running = serve()
    branch = chatgpt_branch()
    reply = running.send("chatgpt", branch)
    assert reply.status == 200 and reply.body["result"] == "新建"
    (path,) = chat_files(running.root, "chatgpt")
    document = read_document(path)
    assert document.meta["conversation_id"] == "g-branch"
    assert document.meta["url"] == "https://chatgpt.com/c/g-branch"
    assert document.meta["message_count"] == 2
    body = document.body
    assert "当前分支的回答" in body and "被放弃的回答" not in body
    # 每条消息的时间来自 create_time（vault 时区 Asia/Singapore，比 UTC 早 8 小时）
    assert headings(path) == [("我", "2025-03-12 16:01"), ("ChatGPT", "2025-03-12 16:06")]
    assert validate_vault(running.root).issues == []


def chatgpt_branch() -> dict[str, Any]:
    data = conversation("g-branch", [("user", "虚构的问题")])
    mapping = data["mapping"]
    user = "g-branch-m0"
    for node_id, text, minutes in (("old", "被放弃的回答", 2), ("new", "当前分支的回答", 5)):
        mapping[node_id] = {
            "id": node_id,
            "parent": user,
            "children": [],
            "message": {
                "id": node_id,
                "author": {"role": "assistant"},
                "content": {"content_type": "text", "parts": [text]},
                "create_time": T0 + minutes * 60,
                "metadata": {},
            },
        }
        mapping[user]["children"].append(node_id)
    data["current_node"] = "new"
    data["update_time"] = T0 + 600
    return data


# ---------- F19-03 Claude ----------


def test_F19_03_claude_conversation_follows_latest_branch(serve: Callable[[], Running]) -> None:
    running = serve()
    data = claude_conversation("c-1", [("human", "虚构的问题")])
    data["chat_messages"] += [
        claude_msg("c-old", "assistant", [text_block("旧的回答")], parent="c-1-m0", t=T0 + 60),
        claude_msg(
            "c-new",
            "assistant",
            [text_block("重新生成的回答")],
            parent="c-1-m0",
            t=T0 + 300,
            files=[{"file_name": "虚构.pdf"}],
        ),
    ]
    data["updated_at"] = iso(T0 + 300)
    reply = running.send("claude", data)
    assert reply.body["result"] == "新建"
    (path,) = chat_files(running.root, "claude")
    document = read_document(path)
    assert document.meta["url"] == "https://claude.ai/chat/c-1"
    assert "重新生成的回答" in document.body and "旧的回答" not in document.body
    assert "虚构.pdf" in document.body  # 附件只留占位
    assert headings(path) == [("我", "2025-03-12 16:01"), ("Claude", "2025-03-12 16:06")]
    assert NIL not in document.body


# ---------- F19-04 和官方导出包是同一个文件 ----------


def test_F19_04_sync_then_official_import_same_file(serve: Callable[[], Running]) -> None:
    running = serve()
    running.send("chatgpt", gpt("g1", 2))
    (path,) = chat_files(running.root, "chatgpt")
    edited = update_frontmatter(
        path.read_text(encoding="utf-8"), {"tags": ["我加的"], "mood": "自加"}
    )
    path.write_text(edited + "\n我的笔记\n", encoding="utf-8")

    official = chatgpt.parse_conversation(gpt("g1", 4), "官方导出")
    result = import_conversations(running.root, [official])
    assert result.updated and not result.created
    (again,) = chat_files(running.root, "chatgpt")
    document = read_document(again)
    assert document.meta["tags"] == ["我加的"] and document.meta["mood"] == "自加"
    assert document.meta["message_count"] == 4
    assert "我的笔记" in document.body


def test_F19_04_official_import_then_sync_same_file(serve: Callable[[], Running]) -> None:
    running = serve()
    first = claude_conversation("c-9", [("human", "虚构"), ("assistant", "虚构回答")])
    import_conversations(running.root, [claude.parse_conversation(first, "官方导出")])
    (path,) = chat_files(running.root, "claude")
    path.write_text(
        update_frontmatter(path.read_text(encoding="utf-8"), {"tags": ["保留"]}), encoding="utf-8"
    )
    later = claude_conversation(
        "c-9",
        [("human", "虚构"), ("assistant", "虚构回答"), ("human", "追问")],
        updated=T0 + 900,
    )
    assert running.send("claude", later).body["result"] == "更新"
    (again,) = chat_files(running.root, "claude")
    assert read_document(again).meta["tags"] == ["保留"]
    assert read_document(again).meta["message_count"] == 3


# ---------- F19-05 没变的不改、变少的不改、不用取 ----------


def test_F19_05_unchanged_and_fewer_messages_leave_file(serve: Callable[[], Running]) -> None:
    running = serve()
    running.send("chatgpt", gpt("g1", 4))
    before = snapshot(running.root)
    assert running.send("chatgpt", gpt("g1", 4)).body["result"] == "未变"
    fewer = running.send("chatgpt", gpt("g1", 2, updated=T0 + 9999))
    assert fewer.body["result"] == "跳过" and "少" in fewer.body["reason"]
    assert snapshot(running.root) == before


def test_F19_05_plan_skips_conversations_vault_already_has(serve: Callable[[], Running]) -> None:
    running = serve()
    running.send("chatgpt", gpt("same", 2))
    running.send("chatgpt", gpt("older", 2))
    items = [
        {"id": "same", "updated": T0 + 120.7},  # 和 vault 一样（小数秒不算更新）
        {"id": "older", "updated": iso(T0 + 3600)},  # 网站上更新过
        {"id": "new", "updated": T0},  # vault 里没有
        {"updated": T0},  # 认不出
    ]
    reply = running.call("POST", "/plan", {"platform": "chatgpt", "items": items})
    assert reply.status == 200
    assert reply.body == {"need": ["older", "new"], "unreadable": 1}


# ---------- F19-06 坏请求 ----------


def test_F19_06_bad_requests_get_reasons(serve: Callable[[], Running]) -> None:
    running = serve()
    before = snapshot(running.root)
    assert running.send("deepseek", gpt()).status == 400
    broken = running.call("POST", "/conversation", raw=b"{not json")
    assert broken.status == 400 and "JSON" in broken.body["error"]
    unknown = running.send("chatgpt", {"title": "没有 mapping"})
    assert unknown.status == 422 and "格式" in unknown.body["error"]
    huge = running.call("POST", "/conversation", raw=b"x", headers={"Content-Length": "99999999"})
    assert huge.status == 413
    assert running.call("GET", "/nothing").status == 404
    assert snapshot(running.root) == before
    assert running.call("GET", "/status").status == 200  # 服务还在


# ---------- F19-07 运行记录不带内容 ----------


def test_F19_07_log_has_no_titles_or_content(serve: Callable[[], Running]) -> None:
    running = serve()
    running.send("chatgpt", gpt("g1", 2, title="机密标题"))
    running.send("chatgpt", {"conversation_id": "bad", "title": "机密标题"})
    with database(running.root, readonly=True) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM runs WHERE job = 'sync'")]
    dumped = json.dumps(rows, ensure_ascii=False)
    assert len(rows) == 2
    assert "机密标题" not in dumped and "虚构的问题" not in dumped
    assert running.token not in dumped
    assert "chatgpt g1 2 条消息：新建" in dumped
    result = CliRunner().invoke(main, ["sync", "status", "--vault", str(running.root)])
    assert result.exit_code == 0 and "新建" in result.output and "格式认不出" in result.output
