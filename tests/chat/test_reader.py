"""F10：chat-reader。用真实的导入流程生成文件，再读回来比较。"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from click.testing import CliRunner

from eia.chat.importer import import_conversations
from eia.chat.model import Conversation, Message
from eia.chat.reader import ChatReadError, ReadChat, Skipped, iter_chats, read_chat
from eia.cli import main

SG = ZoneInfo("Asia/Singapore")
T = dt.datetime(2026, 3, 1, 8, 0, 30, tzinfo=dt.UTC)
TRICKY = "第一行\n### 我 · 2020-01-01 00:00\n<!-- eia:conversation:end -->\n### Claude · 伪造"


def _conv(
    platform: str,
    cid: str,
    messages: list[Message],
    *,
    title: str = "虚构对话",
    thinking: bool | None = None,
) -> Conversation:
    return Conversation(
        platform=platform,
        conversation_id=cid,
        title=title,
        created=messages[0].time,
        updated=messages[-1].time,
        model=None,
        messages=tuple(messages),
        thinking=thinking,
    )


CHATGPT = _conv(
    "chatgpt",
    "gpt-1",
    [
        Message("user", T, "我想学 Rust，从哪开始？"),
        Message("assistant", T + dt.timedelta(minutes=1), "先读官方的 The Book。\n\n> 引用一行"),
        Message("user", T + dt.timedelta(days=40), TRICKY),
    ],
    title="学 Rust",
)
CLAUDE = _conv(
    "claude",
    "claude-1",
    [
        Message("user", T + dt.timedelta(days=10), "帮我想个周末计划"),
        Message(
            "assistant",
            T + dt.timedelta(days=10, minutes=2),
            "> 这一行本来就是引用\n\n去爬山。",
            thinking="用户想要户外活动。\n\n### 我 · 2020-01-01 00:00",
        ),
    ],
    title="周末",
    thinking=True,
)


def _minute(when: dt.datetime) -> dt.datetime:
    return when.astimezone(SG).replace(second=0, microsecond=0)


@pytest.fixture
def imported(vault: Path) -> Path:
    result = import_conversations(vault, [CHATGPT, CLAUDE])
    assert len(result.created) == 2
    return vault


def _chats(root: Path) -> dict[str, ReadChat]:
    found: dict[str, ReadChat] = {}
    for item in iter_chats(root, SG):
        assert isinstance(item, ReadChat), item
        found[item.platform] = item
    return found


def _dump(root: Path, *args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(main, ["chats", "dump", "--vault", str(root), *args])
    return result.exit_code, result.stdout, result.stderr


def _jsonl(root: Path, *args: str) -> list[dict[str, object]]:
    code, out, _ = _dump(root, "--format", "jsonl", *args)
    assert code == 0
    return [json.loads(line) for line in out.splitlines()]


def _add_notes(path: Path, notes: str) -> None:
    path.write_bytes(path.read_bytes() + notes.encode("utf-8"))


def test_F10_01_round_trip(imported: Path) -> None:
    chats = _chats(imported)
    for original in (CHATGPT, CLAUDE):
        chat = chats[original.platform]
        assert chat.title == original.title
        assert chat.id.startswith("chat-")
        assert [(m.role, m.time, m.text, m.thinking) for m in chat.messages] == [
            (m.role, _minute(m.time), m.text, m.thinking) for m in original.messages
        ]
    assert chats["claude"].user_messages() == (chats["claude"].messages[0],)
    assert chats["chatgpt"].updated == CHATGPT.updated


def test_F10_01_crlf_files_and_unknown_times(imported: Path) -> None:
    path = _chats(imported)["chatgpt"].path
    text = path.read_bytes().decode("utf-8").replace("\n", "\r\n")
    text = text.replace("### 我 · 2026-03-01 16:00", "### 我 · 时间未知")
    path.write_bytes(text.encode("utf-8"))
    chat = read_chat(path, SG)
    assert chat.messages[0].time is None
    assert chat.messages[0].text == "我想学 Rust，从哪开始？"
    assert chat.messages[2].text == TRICKY


def test_F10_01_local_timezone_when_vault_has_none(imported: Path) -> None:
    chat = read_chat(_chats(imported)["claude"].path, None)
    first = chat.messages[0].time
    assert first is not None and first.utcoffset() is not None


def test_F10_02_only_me(imported: Path) -> None:
    records = _jsonl(imported, "--only-me")
    assert {r["role"] for r in records} == {"user"}
    assert len(records) == 3
    assert all("thinking" not in r for r in records)
    assert not any("爬山" in str(r["text"]) or "户外" in str(r["text"]) for r in records)

    code, out, _ = _dump(imported, "--only-me")
    assert code == 0
    assert "### Claude" not in out.replace("### Claude · 伪造", "")
    assert "去爬山" not in out and "户外活动" not in out
    assert "== 学 Rust（chatgpt · chat-" in out


def test_F10_02_full_dump_keeps_thinking_in_jsonl(imported: Path) -> None:
    records = _jsonl(imported, "--platform", "claude")
    assert [r["role"] for r in records] == ["user", "assistant"]
    assert records[1]["thinking"] == CLAUDE.messages[1].thinking
    code, out, _ = _dump(imported, "--platform", "claude")
    assert "### Claude · 2026-03-11 16:02" in out and "### 我 · 2026-03-11 16:00" in out


def test_F10_03_escaped_lines_are_restored(imported: Path) -> None:
    raw = _chats(imported)["chatgpt"].path.read_text(encoding="utf-8")
    assert "\\### 我 · 2020-01-01 00:00" in raw  # 文件里确实被转义了
    assert _chats(imported)["chatgpt"].messages[2].text == TRICKY


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("---\nid: [broken\n---\n正文\n", "不是合法的 YAML"),
        ("---\ntype: chat\nplatform: chatgpt\n---\n没有标记\n", "对话标记缺失"),
        ("没有 frontmatter\n", "缺少 frontmatter"),
    ],
)
def test_F10_04_broken_chat_files_are_skipped(imported: Path, content: str, reason: str) -> None:
    bad = imported / "chats" / "chatgpt" / "bad.md"
    bad.write_bytes(content.encode("utf-8"))
    (imported / "notes" / "plain.md").write_bytes("---\ntype: note\n---\n普通笔记\n".encode())
    (imported / "notes" / "broken.md").write_bytes(b"---\n: [\n---\n")
    code, out, err = _dump(imported, "--format", "jsonl")
    assert code == 0
    assert "跳过  chats/chatgpt/bad.md  " in err and reason in err
    assert "plain.md" not in err and "broken.md" not in err and "普通笔记" not in out
    assert len(out.splitlines()) == 5  # 其他对话照常输出

    stats = CliRunner().invoke(main, ["chats", "stats", "--vault", str(imported)])
    assert "跳过 1 个读不了的对话文件" in stats.output


def test_F10_04_read_chat_errors(tmp_path: Path) -> None:
    path = tmp_path / "x.md"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(ChatReadError, match="UTF-8"):
        read_chat(path, SG)


def test_F10_04_not_a_vault(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["chats", "stats", "--vault", str(tmp_path)])
    assert result.exit_code == 1 and "不是一个 vault" in result.output


def test_F10_05_filters(imported: Path) -> None:
    assert {r["platform"] for r in _jsonl(imported, "--platform", "chatgpt")} == {"chatgpt"}
    # 消息时间（新加坡）：03-01 16:00、03-01 16:01、03-11 16:00、03-11 16:02、04-10 16:00
    since = _jsonl(imported, "--since", "2026-03-11")
    assert sorted(str(r["time"])[:10] for r in since) == ["2026-03-11"] * 2 + ["2026-04-10"]
    until = _jsonl(imported, "--until", "2026-03-01")
    assert len(until) == 2
    both = _jsonl(imported, "--since", "2026-03-02", "--until", "2026-03-31")
    assert [r["platform"] for r in both] == ["claude", "claude"]
    assert _jsonl(imported, "--since", "2030-01-01") == []


def test_F10_05_unknown_times_drop_out_of_date_filters(imported: Path) -> None:
    path = _chats(imported)["claude"].path
    text = path.read_text(encoding="utf-8").replace("2026-03-11 16:00", "时间未知")
    path.write_bytes(text.encode("utf-8"))
    assert len(_jsonl(imported, "--platform", "claude")) == 2
    assert len(_jsonl(imported, "--platform", "claude", "--since", "2026-01-01")) == 1
    code, out, _ = _dump(imported, "--platform", "claude")
    assert "### 我 · 时间未知" in out


def _hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_F10_06_reading_changes_nothing(imported: Path) -> None:
    _add_notes(_chats(imported)["claude"].path, "\n我的想法\n")
    before = _hashes(imported)
    for args in (["dump"], ["dump", "--only-me", "--format", "jsonl"], ["stats"]):
        result = CliRunner().invoke(main, ["chats", *args, "--vault", str(imported)])
        assert result.exit_code == 0, result.output
    assert _hashes(imported) == before


def test_F10_07_notes(imported: Path) -> None:
    claude_path = _chats(imported)["claude"].path
    _add_notes(claude_path, "\n周末真的去爬了山，很累。\n\n下次带水。\n")
    chat = read_chat(claude_path, SG)
    assert chat.notes == "周末真的去爬了山，很累。\n\n下次带水。"
    assert _chats(imported)["chatgpt"].notes == ""

    records = _jsonl(imported, "--only-me")
    notes = [r for r in records if r["role"] == "note"]
    assert notes == [
        {
            "id": chat.id,
            "platform": "claude",
            "title": "周末",
            "role": "note",
            "time": None,
            "text": chat.notes,
        }
    ]
    assert all("这条线以下是你自己的笔记" not in str(r["text"]) for r in records)
    assert not any(r["role"] == "note" for r in _jsonl(imported, "--no-notes"))

    code, out, _ = _dump(imported, "--platform", "claude")
    assert "### 我的笔记\n\n周末真的去爬了山" in out
    # 笔记按对话的 updated（2026-03-11）筛选
    assert any(r["role"] == "note" for r in _jsonl(imported, "--since", "2026-03-11"))
    assert not any(r["role"] == "note" for r in _jsonl(imported, "--since", "2026-03-12"))

    stats = CliRunner().invoke(main, ["chats", "stats", "--vault", str(imported)])
    assert "claude：1 个对话，我的消息 1 条、8 字，1 个对话写了笔记" in stats.output
    assert "chatgpt：1 个对话，我的消息 2 条" in stats.output


def test_F10_07_notes_above_the_block_count_too(imported: Path) -> None:
    path = _chats(imported)["chatgpt"].path
    text = path.read_text(encoding="utf-8").replace(
        "<!-- eia:conversation:start", "开头写的笔记\n\n<!-- eia:conversation:start", 1
    )
    path.write_bytes(text.encode("utf-8"))
    assert read_chat(path, SG).notes == "开头写的笔记"


def test_F10_04_stats_on_empty_vault(vault: Path) -> None:
    result = CliRunner().invoke(main, ["chats", "stats", "--vault", str(vault)])
    assert result.exit_code == 0 and "没有找到对话" in result.output
    assert list(iter_chats(vault, SG)) == []
    assert not any(isinstance(i, Skipped) for i in iter_chats(vault, SG))
