"""F17：标出「我」消息里每段文字的来源。样例全部虚构。"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from eia.chat.importer import import_conversations
from eia.chat.model import Conversation, Message
from eia.chat.reader import ReadChat, ReadMessage
from eia.chat.source import Part, label_chat, normalize, split_message, text_of
from eia.cli import main

T = dt.datetime(2026, 3, 1, 8, 0, tzinfo=dt.UTC)
AI_RULES = (
    "Rust 的所有权规则有三条：每个值都有一个所有者；"
    "同一时间只能有一个所有者；所有者离开作用域时值被丢弃。"
)
ECHO = (
    "**Rust 的所有权规则有三条**：每个值都有一个所有者；\n"
    "同一时间只能有一个所有者；所有者离开作用域时值被丢弃。"
)
CODE = "```python\ndef f():\n\n    return 1 / 0\n```"
TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "a.py", line 3, in <module>\n'
    "    f()\n"
    "ZeroDivisionError: division by zero"
)


def _sources(parts: tuple[Part, ...]) -> list[str]:
    return [p.source for p in parts]


def _chat(*messages: tuple[str, str]) -> ReadChat:
    return ReadChat(
        id="x",
        path=Path("x.md"),
        title="虚构",
        platform="chatgpt",
        tags=(),
        updated=None,
        messages=tuple(
            ReadMessage("user" if r == "me" else "assistant", T, t) for r, t in messages
        ),
    )


# ---------- F17-01 贴回的 AI 回答 ----------


def test_F17_01_echo_of_earlier_ai_reply() -> None:
    parts = split_message(f"你说的这段我没懂：\n\n{ECHO}\n\n能举个例子吗？", normalize(AI_RULES))
    assert _sources(parts) == ["own", "ai", "own"]
    assert parts[1].text.strip() == ECHO  # 只差空白和 Markdown 符号也认得


def test_F17_01_part_of_a_reply_is_recognised() -> None:
    parts = split_message(
        "同一时间只能有一个所有者；所有者离开作用域时值被丢弃。", normalize(AI_RULES)
    )
    assert _sources(parts) == ["ai"]


def test_F17_01_short_matches_are_coincidence() -> None:
    assert _sources(split_message("好的，谢谢你", normalize("好的，谢谢你的耐心"))) == ["own"]


def test_F17_01_only_earlier_replies_count() -> None:
    chat = _chat(("me", AI_RULES), ("ai", AI_RULES), ("me", AI_RULES))
    first, reply, second = label_chat(chat)
    assert first is not None and second is not None and reply is None
    assert _sources(first) == ["own"]  # AI 是后来才说的：这是我先写的
    assert _sources(second) == ["ai"]


def test_F17_01_replies_are_not_glued_together() -> None:
    # 两条 AI 回答首尾拼起来碰巧等于我的段，不算贴回
    chat = _chat(
        ("ai", "第一条回答的结尾部分一二三四五六"),
        ("ai", "第二条回答开头七八九十"),
        ("me", "结尾部分一二三四五六第二条回答开头七八九十"),
    )
    assert _sources(label_chat(chat)[2] or ()) == ["own"]


# ---------- F17-02 代码、报错、附件 ----------


def test_F17_02_code_block_is_one_part() -> None:
    parts = split_message(f"看看这段：\n\n{CODE}\n\n为什么会出错", "")
    assert _sources(parts) == ["own", "code", "own"]
    assert parts[1].text.strip() == CODE


@pytest.mark.parametrize(
    "error",
    [
        TRACEBACK,
        'Exception in thread "main" java.lang.NullPointerException\n\tat Main.main(Main.java:5)',
        "error[E0308]: mismatched types\nerror: aborting due to previous error",
    ],
)
def test_F17_02_errors(error: str) -> None:
    assert _sources(split_message(f"运行后出现：\n\n{error}\n\n怎么办", "")) == [
        "own",
        "error",
        "own",
    ]


def test_F17_02_one_error_word_is_not_an_error() -> None:
    assert _sources(split_message("TypeError: 这个是什么意思？\n我看不懂", "")) == ["own"]


def test_F17_02_attachment_placeholder() -> None:
    text = "[附件：作业要求.txt，3000 字]\n[文件：a.pdf]\n[图片：b.png]\n[文件]\n\n帮我看看这个作业"
    assert _sources(split_message(text, "")) == ["attachment", "own"]
    assert _sources(split_message("[附件：a.txt，3 字]\n这一行是我写的", "")) == ["own"]


def test_F17_02_plain_text_is_own() -> None:
    assert _sources(split_message("我想学 Rust。\n\n从哪开始比较好？", "")) == ["own"]


# ---------- F17-03 拼回去等于原文 ----------


@pytest.mark.parametrize(
    "text",
    [
        f"  \n\n开头有空白\n\n{CODE}\n{TRACEBACK}\n\n\n  结尾有空格  \n\n",
        f"{CODE}\n\n{CODE}",
        f"{ECHO}\n\n{ECHO}\n\n[附件：a.txt，1 字]",
        "",
        "\n\n",
        "只有一行",
    ],
)
def test_F17_03_parts_join_back_to_the_message(text: str) -> None:
    parts = split_message(text, normalize(AI_RULES))
    assert "".join(p.text for p in parts) == text
    assert all(a.source != b.source for a, b in zip(parts, parts[1:], strict=False))


def test_F17_03_text_of_picks_sources() -> None:
    parts = split_message(f"第一句\n\n{CODE}\n\n第二句", "")
    assert text_of(parts, sources=("own",)) == "第一句\n\n第二句"
    assert text_of(parts, sources=("code",)) == CODE


# ---------- F17-04 / F17-06 命令 ----------

ECHO_ONLY = "所有者离开作用域时值被丢弃，这句话是什么意思呢。"


@pytest.fixture
def imported(vault: Path) -> Path:
    conversation = Conversation(
        platform="chatgpt",
        conversation_id="src-1",
        title="学 Rust",
        created=T,
        updated=T + dt.timedelta(minutes=5),
        model=None,
        messages=(
            Message("user", T, "什么是所有权？"),
            Message("assistant", T + dt.timedelta(minutes=1), AI_RULES + "\n\n" + ECHO_ONLY),
            Message("user", T + dt.timedelta(minutes=2), f"我没懂：\n\n{ECHO}\n\n{CODE}"),
            Message("assistant", T + dt.timedelta(minutes=3), "我们换个说法。"),
            Message("user", T + dt.timedelta(minutes=4), ECHO_ONLY),
        ),
    )
    import_conversations(vault, [conversation])
    return vault


def _run(root: Path, *args: str) -> str:
    result = CliRunner().invoke(main, ["chats", *args, "--vault", str(root)])
    assert result.exit_code == 0, result.output
    return result.output


def test_F17_04_jsonl_has_parts(imported: Path) -> None:
    records = [
        json.loads(line) for line in _run(imported, "dump", "--format", "jsonl").splitlines()
    ]
    mine = [r for r in records if r["role"] == "user"]
    assert [p["source"] for p in mine[1]["parts"]] == ["own", "ai", "code"]
    assert mine[1]["text"] == f"我没懂：\n\n{ECHO}\n\n{CODE}"  # 整条原文不变
    assert mine[1]["parts"][1]["text"] == ECHO
    assert all("parts" not in r for r in records if r["role"] == "assistant")


def test_F17_04_text_shows_labels(imported: Path) -> None:
    out = _run(imported, "dump", "--only-me")
    assert f"我没懂：\n\n〔来自 AI〕\n{ECHO}\n\n〔代码〕\n{CODE}" in out
    assert "### 我 · 2026-03-01 16:00\n\n什么是所有权？\n" in out  # 全是我写的，不加标签


def test_F17_04_own_only(imported: Path) -> None:
    path = next((imported / "chats" / "chatgpt").glob("*.md"))
    path.write_bytes(path.read_bytes() + "\n我的笔记：所有权就是谁负责释放内存\n".encode())
    records = [
        json.loads(line)
        for line in _run(imported, "dump", "--own-only", "--format", "jsonl").splitlines()
    ]
    assert [(r["role"], r["text"]) for r in records] == [
        ("user", "什么是所有权？"),
        ("user", "我没懂："),
        ("note", "我的笔记：所有权就是谁负责释放内存"),
    ]  # 整条都是贴回的消息不输出；AI 的回答不输出
    assert all("parts" not in r for r in records)
    assert "〔" not in _run(imported, "dump", "--own-only")


def test_F17_04_stats_by_source(imported: Path) -> None:
    out = _run(imported, "stats")
    own = len("什么是所有权？") + len("我没懂：")
    assert (
        f"按来源：我写的 {own} 字，来自 AI {len(ECHO) + len(ECHO_ONLY)} 字，"
        f"代码 {len(CODE)} 字，报错 0 字，附件 0 字"
    ) in out


def test_F17_06_reading_changes_nothing(imported: Path) -> None:
    def hashes() -> dict[str, str]:
        return {
            p.relative_to(imported).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(imported.rglob("*"))
            if p.is_file()
        }

    before = hashes()
    for args in (["dump", "--own-only"], ["dump", "--format", "jsonl"], ["stats"]):
        _run(imported, *args)
    assert hashes() == before
