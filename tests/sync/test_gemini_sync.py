"""chat-sync 的 Gemini 部分：插件发来接口的原始「轮」，eia 拼成对话（chat-sync.delta-gemini.md）。

结构照作者的 gemini-chat-exporter 实测的位置，内容全部是虚构的。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from eia.chat import gemini
from eia.chat.importer import import_conversations
from eia.schema.io import read_document
from eia.schema.validate import validate_vault

from .conftest import Running

T = 1764061700  # 2025-11-25T09:08:20Z


def turn(
    rid: str, question: str, answers: list[tuple[str, list[str]]], chosen: str, t: int
) -> list:
    return [
        ["c_g1", rid],
        None,
        [[question]],
        [[[cid, parts] for cid, parts in answers], None, None, chosen],
        [t, 250_000_000],
    ]


def payload(turns: list[list], **changes: Any) -> dict[str, Any]:
    data = {
        "conversationId": "c_g1",
        "title": "虚构的 Gemini 对话",
        "updated": [T + 600, 0],
        "url": "https://gemini.google.com/u/1/app/g1",
        "turns": turns,
        "possiblyIncomplete": False,
    }
    return {**data, **changes}


def test_F19_11_gemini_turns_become_valid_chat(serve: Callable[[], Running]) -> None:
    running = serve()
    turns = [
        turn("r2", "第二个问题", [("rc2", ["第二个回答"])], "rc2", T + 300),
        turn(
            "r1",
            "第一个问题",
            [("rc1a", ["草稿一"]), ("rc1b", ["选中的回答", "第二段"])],
            "rc1b",
            T,
        ),
    ]
    reply = running.send("gemini", payload(turns))
    assert reply.status == 200 and reply.body["result"] == "新建"
    (path,) = sorted((running.root / "chats" / "gemini").rglob("*.md"))
    document = read_document(path)
    assert document.meta["conversation_id"] == "c_g1"
    assert document.meta["url"] == "https://gemini.google.com/u/1/app/g1"
    assert document.meta["message_count"] == 4
    body = document.body
    # 按时间排序；只取选中的回答；段落之间空一行
    assert body.index("第一个问题") < body.index("第二个问题")
    assert "选中的回答\n\n第二段" in body and "草稿一" not in body
    # 同一轮的提问和回答共用一个时间（vault 时区 Asia/Singapore）
    assert body.count("· 2025-11-25 17:08") == 2 and body.count("· 2025-11-25 17:13") == 2
    assert validate_vault(running.root).issues == []


def test_F19_11_same_file_as_gemini_export(serve: Callable[[], Running]) -> None:
    running = serve()
    exported = {
        "conversationId": "c_g1",
        "title": "虚构的 Gemini 对话",
        "url": "https://gemini.google.com/u/1/app/g1",
        "createdAt": "2025-11-25T09:08:20.250Z",
        "updatedAt": "2025-11-25T09:08:20.250Z",
        "messages": [
            {"role": "user", "time": "2025-11-25T09:08:20.250Z", "content": "第一个问题"},
            {"role": "assistant", "time": "2025-11-25T09:08:20.250Z", "content": "回答"},
        ],
    }
    import_conversations(running.root, [gemini.parse_conversation(exported, "导出")])
    turns = [
        turn("r1", "第一个问题", [("rc1", ["回答"])], "rc1", T),
        turn("r2", "追问", [("rc2", ["再回答"])], "rc2", T + 300),
    ]
    assert running.send("gemini", payload(turns)).body["result"] == "更新"
    files = sorted((running.root / "chats" / "gemini").rglob("*.md"))
    assert len(files) == 1 and read_document(files[0]).meta["message_count"] == 4


def test_F19_11_unreadable_gemini_turns_rejected(serve: Callable[[], Running]) -> None:
    running = serve()
    assert running.send("gemini", payload("不是列表")).status == 422
    no_time = [[["c_g1", "r1"], None, [["问"]], [[["rc", ["答"]]]], None]]
    assert running.send("gemini", payload(no_time)).status == 422
    assert not list((running.root / "chats" / "gemini").rglob("*.md"))
