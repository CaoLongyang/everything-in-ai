from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from eia.chat import gemini
from eia.chat.importer import import_conversations
from eia.chat.model import ExportFormatError
from eia.chat.reader import read_chat
from eia.chat.render import END, END_RE, START_RE
from eia.cli import main
from eia.schema.io import read_document
from eia.schema.validate import validate_vault

from .conftest import T0, claude_conversation, conversation, gemini_conversation, gemini_report

HEADING = re.compile(r"^### (我|Gemini) · (\d{4}-\d{2}-\d{2} \d{2}:\d{2})$", re.MULTILINE)
SGT = dt.timezone(dt.timedelta(hours=8))


def run(*args: str):
    return CliRunner().invoke(main, list(args))


def chat_files(root: Path) -> list[Path]:
    return sorted((root / "chats" / "gemini").rglob("*.md"))


def split_messages(body: str) -> list[tuple[str, str, str]]:
    (start,) = START_RE.finditer(body)
    (end,) = END_RE.finditer(body)
    pieces = HEADING.split(body[start.end() : end.start()])
    return [(pieces[i], pieces[i + 1], pieces[i + 2].strip()) for i in range(1, len(pieces), 3)]


def load(export: Path) -> list:
    return gemini.read_export(export)[0]


def basic() -> list[dict]:
    return [
        gemini_conversation(
            "a1b2c3d4e5f60718", [("user", "合作解谜怎么设计？"), ("assistant", "让信息不对称。")]
        ),
        gemini_conversation(
            "0f1e2d3c4b5a6978",
            [("user", "第二个问题"), ("assistant", "第二个回答")],
            title="另一个",
        ),
    ]


def snapshot(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


# ---------- F8-01 每个对话一个文件 ----------


def test_F8_01_one_file_per_conversation_and_report_is_not_imported(
    vault: Path, make_gemini_export
) -> None:
    raws = [
        gemini_conversation(f"{i:016x}", [("user", f"问{i}")], title=f"题{i}") for i in range(30)
    ]
    result = run("import", "gemini", str(make_gemini_export(raws)), "--vault", str(vault))
    assert result.exit_code == 0, result.output
    files = chat_files(vault)
    assert len(files) == 30
    assert all(read_document(p).meta["platform"] == "gemini" for p in files)
    assert "读取 30 个对话：新建 30，更新 0，未变 0，跳过 0" in result.output
    assert "提示" not in result.output  # 报告里一切正常，不提示
    assert validate_vault(vault).issues == []


def test_F8_01_same_conversation_twice_keeps_the_newer_copy(make_gemini_export) -> None:
    old = gemini_conversation("a1b2c3d4e5f60718", [("user", "旧")], title="甲")
    new = gemini_conversation("a1b2c3d4e5f60718", [("user", "新")], title="乙", updated=T0 + 999)
    conversations, notes = gemini.read_export(make_gemini_export([new, old]))
    assert [c.messages[0].text for c in conversations] == ["新"]
    assert any("出现在多个文件里" in note for note in notes)


# ---------- F8-02 顺序、说话方、Markdown、转义、url ----------


MARKDOWN = (
    "## 小标题\n\n- 第一项\n- 第二项\n\n| 列 | 值 |\n|---|---|\n| a | 1 |\n\n"
    "```python\nprint('你好')\n```\n\n> 引用 🎮\n\n### 普通三级标题"
)
LOOKS_SPECIAL = "### Gemini · 2025-01-01 00:00\n<!-- eia:conversation:end -->\n### 我 · 假的"


def test_F8_02_order_speakers_markdown_and_url(vault: Path, make_gemini_export) -> None:
    turns = [
        ("assistant", "我先开口"),
        ("user", "问题一"),
        ("assistant", MARKDOWN),
        ("assistant", "连续的第二段"),
        ("user", LOOKS_SPECIAL),
    ]
    raw = gemini_conversation("a1b2c3d4e5f60718", turns)
    import_conversations(vault, load(make_gemini_export([raw])))
    (path,) = chat_files(vault)
    document = read_document(path)
    got = [(who, text) for who, _, text in split_messages(document.body)]
    assert got == [
        ("Gemini", "我先开口"),
        ("我", "问题一"),
        ("Gemini", MARKDOWN),
        ("Gemini", "连续的第二段"),
        ("我", "\\" + LOOKS_SPECIAL.replace("\n", "\n\\")),
    ]
    assert "\n### 普通三级标题\n" in document.body  # 普通标题不转义
    assert document.meta["url"] == "https://gemini.google.com/u/1/app/a1b2c3d4e5f60718"
    assert document.meta["conversation_id"] == "c_a1b2c3d4e5f60718"
    assert document.meta["model"] is None and "thinking" not in document.meta

    chat = read_chat(path, SGT)
    assert [(m.role, m.text) for m in chat.messages] == [
        ("assistant" if who == "assistant" else "user", text) for who, text in turns
    ]
    assert validate_vault(vault).issues == []


# ---------- F8-03 时间 ----------


def test_F8_03_times_come_from_the_export(vault: Path, make_gemini_export) -> None:
    turns = [("user", "一"), ("assistant", "答一"), ("user", "二"), ("assistant", "答二")]
    raw = gemini_conversation("a1b2c3d4e5f60718", turns, t=T0 + 0.789)
    (parsed,) = load(make_gemini_export([raw]))
    assert parsed.created == dt.datetime(2025, 3, 12, 8, 1, 22, tzinfo=dt.UTC)  # 毫秒被截掉
    assert parsed.updated == parsed.created + dt.timedelta(minutes=2)
    assert parsed.messages[0].time == parsed.messages[1].time  # 同一轮共用一个时间

    import_conversations(vault, [parsed])
    (path,) = chat_files(vault)
    assert path.name.startswith("2025-03-12-")
    assert [when for _, when, _ in split_messages(read_document(path).body)] == [
        "2025-03-12 16:01",
        "2025-03-12 16:01",
        "2025-03-12 16:03",
        "2025-03-12 16:03",
    ]
    meta = read_document(path).meta
    assert meta["created"] == parsed.created and meta["updated"] == parsed.updated


def test_F8_03_updated_is_never_earlier_than_the_last_message(make_gemini_export) -> None:
    raw = gemini_conversation("a1b2c3d4e5f60718", [("user", "一"), ("user", "二")], updated=T0)
    (parsed,) = load(make_gemini_export([raw]))
    assert parsed.updated == parsed.messages[-1].time > parsed.created


def test_F8_03_file_date_follows_the_vault_timezone(vault: Path, make_gemini_export) -> None:
    late = T0 + 16 * 3600  # UTC 3 月 13 日 00:01 = 新加坡 08:01
    raw = gemini_conversation("a1b2c3d4e5f60718", [("user", "深夜")], t=late - 3600)
    import_conversations(vault, load(make_gemini_export([raw])))
    (path,) = chat_files(vault)
    assert path.name.startswith("2025-03-13-")


# ---------- F8-04 空消息、空标题 ----------


def test_F8_04_empty_messages_and_blank_titles(vault: Path, make_gemini_export) -> None:
    kept = gemini_conversation(
        "a1b2c3d4e5f60718",
        [("user", "  "), ("assistant", "看到了图片"), ("system", "?")],
        title=" ",
    )
    empty = gemini_conversation("0f1e2d3c4b5a6978", [("user", ""), ("user", "\n")], title="空的")
    result = run("import", "gemini", str(make_gemini_export([kept, empty])), "--vault", str(vault))
    assert result.exit_code == 0, result.output
    (path,) = chat_files(vault)
    document = read_document(path)
    assert document.meta["title"] == "未命名对话"
    assert document.meta["message_count"] == 1
    assert [(who, text) for who, _, text in split_messages(document.body)] == [
        ("Gemini", "看到了图片")
    ]
    assert "跳过  空的  对话里没有可导入的消息" in result.output


# ---------- F8-05 重复导入：未变、变多、变少 ----------


def test_F8_05_unchanged_more_and_fewer_messages(vault: Path, make_gemini_export) -> None:
    cid = "a1b2c3d4e5f60718"
    two = [("user", "一"), ("assistant", "答一")]
    four = [*two, ("user", "二"), ("assistant", "答二")]
    run(
        "import",
        "gemini",
        str(make_gemini_export([gemini_conversation(cid, two)], name="a.zip")),
        "--vault",
        str(vault),
    )
    (path,) = chat_files(vault)

    again = run(
        "import",
        "gemini",
        str(make_gemini_export([gemini_conversation(cid, two)], name="b.zip")),
        "--vault",
        str(vault),
    )
    assert "新建 0，更新 0，未变 1，跳过 0" in again.output

    more = run(
        "import",
        "gemini",
        str(make_gemini_export([gemini_conversation(cid, four)], name="c.zip")),
        "--vault",
        str(vault),
    )
    assert "更新 1" in more.output
    assert read_document(path).meta["message_count"] == 4
    before = path.read_bytes()

    fewer = gemini_conversation(cid, two, updated=T0 + 9999)  # 更新时间变新，消息却少了
    result = run(
        "import", "gemini", str(make_gemini_export([fewer], name="d.zip")), "--vault", str(vault)
    )
    assert result.exit_code == 0, result.output
    assert "跳过 1" in result.output and "这次导出的消息比文件里少" in result.output
    assert path.read_bytes() == before


def test_F8_05_dry_run_writes_nothing(vault: Path, make_gemini_export) -> None:
    before = snapshot(vault)
    result = run(
        "import", "gemini", str(make_gemini_export(basic())), "--vault", str(vault), "--dry-run"
    )
    assert "（预览，未写入）读取 2 个对话：新建 2" in result.output
    assert snapshot(vault) == before


def test_F8_05_other_platforms_still_accept_fewer_messages(vault: Path, make_export) -> None:
    """消息变少的保护只对 Gemini 打开；ChatGPT 换了分支时消息变少是正常的。"""
    run(
        "import",
        "chatgpt",
        str(make_export([conversation("c1", [("user", "一"), ("user", "二")])], name="a.zip")),
        "--vault",
        str(vault),
    )
    shorter = conversation("c1", [("user", "一")], updated=T0 + 9999)
    result = run(
        "import", "chatgpt", str(make_export([shorter], name="b.zip")), "--vault", str(vault)
    )
    assert "更新 1" in result.output


# ---------- F8-06 用户改动在重新导入后保留 ----------


def test_F8_06_tags_fields_notes_renames_and_url_survive(vault: Path, make_gemini_export) -> None:
    cid = "a1b2c3d4e5f60718"
    import_conversations(
        vault, load(make_gemini_export([gemini_conversation(cid, [("user", "一")])], name="a.zip"))
    )
    (path,) = chat_files(vault)
    raw = path.read_text(encoding="utf-8").replace("tags: []", "tags:\n- 游戏设计")
    raw = raw.replace("schema: 1\n", "schema: 1\nmy_rating: 5  # 我的打分\n")
    raw = re.sub(r"^url: .*$", "url: https://example.com/我改的", raw, count=1, flags=re.M)
    notes = "\n我的笔记 🎮\n"
    moved = vault / "notes" / "改过名.md"
    moved.write_text(raw + notes, encoding="utf-8")
    path.unlink()

    later = gemini_conversation(cid, [("user", "一"), ("user", "再问"), ("assistant", "新回答")])
    result = import_conversations(
        vault, load(make_gemini_export([later], name="b.zip")), refuse_fewer=True
    )

    assert result.created == [] and result.updated == ["notes/改过名.md"]
    text = moved.read_text(encoding="utf-8")
    meta = read_document(moved).meta
    assert meta["tags"] == ["游戏设计"]
    assert meta["url"] == "https://example.com/我改的"
    assert "my_rating: 5  # 我的打分\n" in text
    assert text.endswith(END + "\n\n（这条线以下是你自己的笔记，重新导入不会动）\n" + notes)
    assert "新回答" in text


def test_F8_06_missing_url_is_filled_in_on_update(vault: Path, make_gemini_export) -> None:
    cid = "a1b2c3d4e5f60718"
    import_conversations(
        vault, load(make_gemini_export([gemini_conversation(cid, [("user", "一")])], name="a.zip"))
    )
    (path,) = chat_files(vault)
    stripped = re.sub(r"^url: .*\n", "", path.read_text(encoding="utf-8"), count=1, flags=re.M)
    path.write_text(stripped, encoding="utf-8")
    assert "url" not in read_document(path).meta
    later = gemini_conversation(cid, [("user", "一"), ("user", "再问")])
    import_conversations(vault, load(make_gemini_export([later], name="b.zip")))
    assert read_document(path).meta["url"] == "https://gemini.google.com/u/1/app/" + cid
    assert validate_vault(vault).issues == []


# ---------- F8-07 认不出的格式 ----------


def _members(**files: object) -> dict[str, str]:
    return {
        name: value if isinstance(value, str) else json.dumps(value)
        for name, value in files.items()
    }


OLD_FORMAT = {
    "conversationId": "a1b2c3d4e5f60718",
    "title": "旧版",
    "url": "https://gemini.google.com/u/1/app/a1b2c3d4e5f60718?pageId=none",
    "exportedAt": "2026-11-19T00:00:00.000Z",
    "messageCount": 1,
    "messages": [{"role": "user", "content": "你好", "blocks": [{"tag": "p", "text": "你好"}]}],
}


def _without(key: str) -> dict:
    raw = gemini_conversation("a1b2c3d4e5f60718", [("user", "一")])
    raw.pop(key)
    return raw


def _broken_message(**changes: object) -> dict:
    raw = gemini_conversation("a1b2c3d4e5f60718", [("user", "一")])
    raw["messages"][0].update(changes)
    return raw


def _old_message() -> dict:
    """对话带时间，但消息是旧版的样子（有 blocks、没有 time）。"""
    raw = gemini_conversation("a1b2c3d4e5f60718", [("user", "一")])
    raw["messages"] = OLD_FORMAT["messages"]
    return raw


@pytest.mark.parametrize(
    ("members", "expected"),
    [
        ({"0001_旧版.json": OLD_FORMAT}, "旧版插件的导出"),
        ({"0001_x.json": _broken_message(time=None)}, "应为 ISO 时间字符串"),
        ({"0001_x.json": _old_message()}, "旧版插件的导出"),
        (
            {"conversations.json": [claude_conversation("c1", [("human", "嗨")])]},
            "ChatGPT 或 Claude",
        ),
        ({"conversations-000.json": [conversation("c1", [("user", "嗨")])]}, "ChatGPT 或 Claude"),
        ({"0001_x.json": {"hello": 1}}, "不是认识的 Gemini 对话"),
        ({"0001_x.json": [1, 2]}, "不是认识的 Gemini 对话"),
        ({"0001_x.json": "{不是 JSON"}, "不是合法的 JSON"),
        ({"_report.json": gemini_report()}, "里没有 Gemini 对话"),
        ({"readme.txt": "随便"}, "里没有 Gemini 对话"),
        ({"0001_x.json": _without("conversationId")}, "缺少字段 conversationId"),
        ({"0001_x.json": _without("createdAt")}, "createdAt 应为 ISO 时间字符串"),
        ({"0001_x.json": {**_without("messages"), "messages": {}}}, "messages 应为列表"),
        ({"0001_x.json": _broken_message(time="昨天")}, "不是合法的时间"),
        ({"0001_x.json": _broken_message(time="2026-11-19T00:00:00")}, "缺少时区"),
        ({"0001_x.json": _broken_message(content=None)}, "content 应为文字"),
        ({"0001_x.json": {**_without("messages"), "messages": ["文字"]}}, "每条消息应为一个对象"),
    ],
)
def test_F8_07_unknown_formats_are_refused(
    vault: Path, make_gemini_export, members: dict, expected: str
) -> None:
    export = make_gemini_export(members=_members(**members))
    before = snapshot(vault)
    result = run("import", "gemini", str(export), "--vault", str(vault))
    assert result.exit_code == 1
    assert expected in result.output and "没有写入任何文件" in result.output
    assert snapshot(vault) == before


def test_F8_07_message_without_time_and_without_blocks(make_gemini_export) -> None:
    raw = gemini_conversation("a1b2c3d4e5f60718", [("user", "一")])
    del raw["messages"][0]["time"]
    with pytest.raises(ExportFormatError, match="缺少字段 time"):
        load(make_gemini_export([raw]))


def test_F8_07_not_a_zip_is_refused(vault: Path, tmp_path: Path) -> None:
    fake = tmp_path / "gemini.zip"
    fake.write_text("不是 zip", encoding="utf-8")
    result = run("import", "gemini", str(fake), "--vault", str(vault))
    assert result.exit_code == 1 and "无法作为 zip 打开" in result.output
    assert chat_files(vault) == []


# ---------- F8-08 汇总报告的提示 ----------


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        (
            {"listTotal": 5, "failures": [{"id": "x"}], "missing": [{"id": "y"}]},
            "插件报告侧边栏共 5 个对话，导出 3 个；2 个失败或缺失；0 个可能不完整",
        ),
        ({"possiblyIncomplete": [{"id": "x"}]}, "0 个失败或缺失；1 个可能不完整"),
        ({"stoppedManually": True}, "导出被手动停止"),
    ],
)
def test_F8_08_report_problems_are_printed(
    vault: Path, make_gemini_export, changes: dict, expected: str
) -> None:
    export = make_gemini_export(basic(), report=gemini_report(**changes))
    result = run("import", "gemini", str(export), "--vault", str(vault))
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert lines[-1].startswith("提示  ") and expected in lines[-1]
    assert "新建 2" in result.output


def test_F8_08_report_with_odd_values_and_no_report_at_all(make_gemini_export) -> None:
    odd = gemini_report(listTotal="很多", exported=True, failures=3, missing=None)
    assert gemini.report_note(odd) == (
        "插件报告侧边栏共 0 个对话，导出 0 个；3 个失败或缺失；0 个可能不完整。"
        "用插件补导出后再导入一次即可"
    )
    members = _members(**{f"000{i}_x.json": raw for i, raw in enumerate(basic())})
    conversations, notes = gemini.read_export(make_gemini_export(members=members))
    assert len(conversations) == 2 and notes == []


def test_F8_08_include_thinking_has_no_effect_but_says_so(vault: Path, make_gemini_export) -> None:
    result = run(
        "import",
        "gemini",
        str(make_gemini_export(basic())),
        "--vault",
        str(vault),
        "--include-thinking",
    )
    assert result.exit_code == 0, result.output
    assert "--include-thinking 没有效果" in result.output
    assert all("thinking" not in read_document(p).meta for p in chat_files(vault))


def test_F8_09_long_multiline_titles_are_cut_to_one_line(vault: Path) -> None:
    from eia.chat.importer import TITLE_CHARS, clean_title
    from eia.chat.model import Conversation, Message

    t0 = dt.datetime(2026, 5, 13, 2, 0, tzinfo=dt.UTC)
    code = "帮我写注释\nimport java.io.File;\n" + "x = 1;\n" * 500
    first = Conversation(
        "gemini", "g-title", code, t0, t0, None, (Message("user", t0, "帮我写注释"),)
    )
    import_conversations(vault, [first])
    [path] = chat_files(vault)
    assert read_document(path).meta["title"] == "帮我写注释"
    assert "\n" not in path.name and "import" not in path.name

    later = t0 + dt.timedelta(days=1)
    again = Conversation(
        "gemini", "g-title", "  长" * 100, t0, later, None, (Message("user", later, "再问一句"),)
    )
    import_conversations(vault, [again])  # 重新导入时也一样处理
    title = read_document(path).meta["title"]
    assert len(title) <= TITLE_CHARS + 1 and title.endswith("…") and "\n" not in title
    assert clean_title("  \n  标题  \n第二行") == "标题" and clean_title("") == ""
