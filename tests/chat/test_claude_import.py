from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from eia.chat import claude
from eia.chat.importer import import_conversations
from eia.chat.model import ExportFormatError
from eia.chat.render import END, END_RE, START_RE
from eia.cli import main
from eia.schema.io import read_document
from eia.schema.validate import validate_vault

from .conftest import (
    NIL,
    T0,
    claude_conversation,
    claude_msg,
    conversation,
    injected_block,
    iso,
    text_block,
    thinking_block,
    tool_result_block,
    tool_use_block,
)

HEADING = re.compile(r"^### (我|Claude) · \d{4}-\d{2}-\d{2} \d{2}:\d{2}$", re.MULTILINE)


def run(*args: str):
    return CliRunner().invoke(main, list(args))


def chat_files(root: Path) -> list[Path]:
    return sorted((root / "chats" / "claude").rglob("*.md"))


def split_messages(body: str) -> list[tuple[str, str]]:
    (start,) = START_RE.finditer(body)
    (end,) = END_RE.finditer(body)
    pieces = HEADING.split(body[start.end() : end.start()])
    return [(pieces[i], pieces[i + 1].strip()) for i in range(1, len(pieces), 2)]


def load(export: Path, *, include_thinking: bool = False) -> list:
    return claude.read_exports([export], include_thinking=include_thinking)[0]


def basic() -> list[dict]:
    return [
        claude_conversation(
            "c1", [("human", "合作解谜怎么设计？"), ("assistant", "让信息不对称。")]
        ),
        claude_conversation(
            "c2", [("human", "第二个问题"), ("assistant", "第二个回答")], name="另一个"
        ),
    ]


# ---------- F5-01 每个对话一个文件 ----------


def test_F5_01_one_file_per_conversation(vault: Path, make_claude_export) -> None:
    export = make_claude_export(
        [claude_conversation(f"c{i}", [("human", f"问{i}")]) for i in range(30)]
    )
    result = run("import", "claude", str(export), "--vault", str(vault))
    assert result.exit_code == 0, result.output
    assert len(chat_files(vault)) == 30
    assert "读取 30 个对话：新建 30，更新 0，未变 0，跳过 0" in result.output
    assert validate_vault(vault).issues == []


def test_F5_01_several_zips_are_read_and_duplicates_keep_the_newest(
    vault: Path, make_claude_export
) -> None:
    old = claude_conversation("c1", [("human", "旧版")])
    new = claude_conversation("c1", [("human", "旧版"), ("assistant", "新版")])
    first = make_claude_export([old, *basic()[1:]], name="conversations-000.zip")
    second = make_claude_export([new], name="conversations-001.zip")
    result = run("import", "claude", str(first), str(second), "--vault", str(vault))
    assert result.exit_code == 0, result.output
    assert len(chat_files(vault)) == 2
    texts = [path.read_text(encoding="utf-8") for path in chat_files(vault)]
    assert any("新版" in text for text in texts)


# ---------- F5-02 顺序、说话方、正文 ----------


def test_F5_02_order_speakers_and_text_match_the_export(vault: Path, make_claude_export) -> None:
    turns = [
        ("human", "第一句"),
        ("assistant", "回答一"),
        ("human", "第二句"),
        ("assistant", "回答二"),
    ]
    import_conversations(vault, load(make_claude_export([claude_conversation("c1", turns)])))
    document = read_document(chat_files(vault)[0])
    assert split_messages(document.body) == [
        ("我", "第一句"),
        ("Claude", "回答一"),
        ("我", "第二句"),
        ("Claude", "回答二"),
    ]
    meta = document.meta
    assert meta["platform"] == "claude" and meta["source"] == "claude"
    assert meta["conversation_id"] == "c1" and meta["message_count"] == 4
    assert meta["created"] == dt.datetime.fromtimestamp(T0, dt.UTC)
    assert "### 我 · 2025-03-12 16:01" in document.body  # vault 时区 Asia/Singapore


def test_F5_02_several_text_blocks_and_rich_text_survive(vault: Path, make_claude_export) -> None:
    rich = "代码：\n\n```python\nprint('你好 🎮')\n```\n\n| 表 | 格 |\n|---|---|\n| 一 | 二 |"
    blocks = [text_block("第一段"), tool_use_block(), tool_result_block(), text_block(rich)]
    raw = claude_conversation("c1", [("human", "问"), ("assistant", blocks)])
    import_conversations(vault, load(make_claude_export([raw])))
    (_, answer) = split_messages(read_document(chat_files(vault)[0]).body)[1]
    assert answer == "第一段\n\n" + rich


def test_F5_02_top_level_text_field_is_not_used(vault: Path, make_claude_export) -> None:
    import_conversations(vault, load(make_claude_export(basic())))
    for path in chat_files(vault):
        assert "顶层 text 字段" not in path.read_text(encoding="utf-8")


# ---------- F5-03 不导入的内容与占位 ----------


def test_F5_03_tools_injections_thinking_and_summary_are_left_out(
    vault: Path, make_claude_export
) -> None:
    human = [injected_block(), text_block("帮我看看")]
    assistant = [
        thinking_block("内部推理"),
        tool_use_block(),
        tool_result_block(),
        text_block("看完了"),
    ]
    tool_only = [thinking_block("再想想"), tool_use_block("Edit"), tool_result_block()]
    raw = claude_conversation(
        "c1", [("human", human), ("assistant", assistant), ("assistant", tool_only)]
    )
    import_conversations(vault, load(make_claude_export([raw])))
    document = read_document(chat_files(vault)[0])
    text = chat_files(vault)[0].read_text(encoding="utf-8")
    for hidden in (
        "系统插入的提示",
        "内部推理",
        "再想想",
        "工具输出",
        "秘密",
        "自动生成的摘要",
        "acct-",
    ):
        assert hidden not in text
    assert split_messages(document.body) == [("我", "帮我看看"), ("Claude", "看完了")]
    assert document.meta["message_count"] == 2


def test_F5_03_files_and_attachments_become_placeholders(vault: Path, make_claude_export) -> None:
    human = claude_msg(
        "c1-m0",
        "human",
        [{"type": "image", "file_uuid": "f1"}, text_block("这张图和文档怎么样")],
        parent=NIL,
        files=[
            {"file_uuid": "f1", "file_name": "截图.PNG"},
            {"file_uuid": "f2", "file_name": "报告.pdf"},
        ],
        attachments=[
            {"file_name": "粘贴的文本.txt", "extracted_content": "长" * 1234, "file_size": 9}
        ],
    )
    assistant = claude_msg(
        "c1-m1",
        "assistant",
        [text_block("改好了")],
        parent="c1-m0",
        t=T0 + 60,
        files=[{"file_uuid": "f3", "file_name": "结果.md"}],
    )
    raw = claude_conversation("c1", [], extra=[human, assistant])
    import_conversations(vault, load(make_claude_export([raw])))
    messages = split_messages(read_document(chat_files(vault)[0]).body)
    assert messages == [
        (
            "我",
            "[附件：粘贴的文本.txt，1234 字]\n[图片：截图.PNG]\n[文件：报告.pdf]"
            "\n\n这张图和文档怎么样",
        ),
        ("Claude", "改好了\n\n[文件：结果.md]"),
    ]
    assert "长长长" not in chat_files(vault)[0].read_text(encoding="utf-8")


def test_F5_03_blocks_missing_from_files_still_get_a_placeholder(
    vault: Path, make_claude_export
) -> None:
    blocks = [
        {"type": "image", "file_uuid": "x1"},
        {"type": "document", "file_uuid": "x2", "title": "说明书"},
        {"type": "document"},
        {"type": "image", "file_uuid": "x1"},
        {"type": "未来的新类型", "text": "忽略"},
    ]
    attachment = {"file_name": "", "extracted_content": "短"}
    message = claude_msg("c1-m0", "human", blocks, parent=NIL, attachments=[attachment])
    raw = claude_conversation("c1", [], extra=[message])
    import_conversations(vault, load(make_claude_export([raw])))
    ((_, text),) = split_messages(read_document(chat_files(vault)[0]).body)
    assert text == "[附件：粘贴的文本，1 字]\n[图片]\n[文件：说明书]\n[文件]"


def test_F5_03_conversation_with_nothing_to_import_is_reported(
    vault: Path, make_claude_export
) -> None:
    raw = claude_conversation("c1", [("assistant", [tool_use_block()]), ("human", "   ")])
    result = run("import", "claude", str(make_claude_export([raw])), "--vault", str(vault))
    assert result.exit_code == 0, result.output
    assert chat_files(vault) == []
    assert "对话里没有可导入的消息" in result.output


def test_F5_03_unknown_senders_are_left_out(vault: Path, make_claude_export) -> None:
    raw = claude_conversation("c1", [("human", "问"), ("system", "系统消息"), ("assistant", "答")])
    import_conversations(vault, load(make_claude_export([raw])))
    assert [s for s, _ in split_messages(read_document(chat_files(vault)[0]).body)] == [
        "我",
        "Claude",
    ]


# ---------- F5-04 消息树 ----------


def test_F5_04_only_the_newest_branch_is_imported(vault: Path, make_claude_export) -> None:
    old = claude_msg("old", "assistant", [text_block("旧回答")], parent="c1-m0", t=T0 + 30)
    raw = claude_conversation("c1", [("human", "问"), ("assistant", "新回答")], extra=[old])
    import_conversations(vault, load(make_claude_export([raw])))
    messages = split_messages(read_document(chat_files(vault)[0]).body)
    assert messages == [("我", "问"), ("Claude", "新回答")]


def test_F5_04_branch_created_later_wins_even_if_listed_first(
    vault: Path, make_claude_export
) -> None:
    root = claude_msg("r", "human", [text_block("问")], parent=NIL)
    late = claude_msg("late", "assistant", [text_block("重新生成的")], parent="r", t=T0 + 500)
    early = claude_msg("early", "assistant", [text_block("第一次的")], parent="r", t=T0 + 60)
    raw = claude_conversation("c1", [], extra=[late, root, early])
    import_conversations(vault, load(make_claude_export([raw])))
    messages = split_messages(read_document(chat_files(vault)[0]).body)
    assert messages == [("我", "问"), ("Claude", "重新生成的")]


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        (
            [
                claude_msg("a", "human", [text_block("1")], parent="b"),
                claude_msg("b", "assistant", [text_block("2")], parent="a"),
            ],
            "没有根",
        ),
        (
            [
                claude_msg("a", "human", [text_block("1")], parent=NIL),
                claude_msg("b", "human", [text_block("2")], parent=NIL),
            ],
            "多个根",
        ),
        ([claude_msg("a", "human", [text_block("1")], parent="不存在")], "不存在的消息"),
        (
            [
                claude_msg("a", "human", [text_block("1")], parent=NIL),
                claude_msg("a", "assistant", [text_block("2")], parent="a"),
            ],
            "重复",
        ),
    ],
)
def test_F5_04_broken_trees_stop_the_import(
    vault: Path, make_claude_export, messages: list, expected: str
) -> None:
    raw = claude_conversation("c1", [], extra=messages)
    export = make_claude_export([claude_conversation("ok", [("human", "好的")]), raw])
    result = run("import", "claude", str(export), "--vault", str(vault))
    assert result.exit_code == 1
    assert expected in result.output
    assert "没有写入任何文件" in result.output
    assert chat_files(vault) == []


def test_F5_04_cycle_below_the_root_is_detected() -> None:
    messages = [
        claude_msg("r", "human", [text_block("1")], parent=NIL),
        claude_msg("a", "assistant", [text_block("2")], parent="b"),
        claude_msg("b", "human", [text_block("3")], parent="a"),
    ]
    with pytest.raises(ExportFormatError, match="循环|到不了"):
        claude.parse_conversation(claude_conversation("c1", [], extra=messages), "测试")


# ---------- F5-05 认不出的文件 ----------


def test_F5_05_manifest_file_is_refused_with_a_hint(vault: Path, tmp_path: Path) -> None:
    manifest = tmp_path / "manifest-abc.json"
    manifest.write_text(json.dumps({"data_files": []}), encoding="utf-8")
    result = run("import", "claude", str(manifest), "--vault", str(vault))
    assert result.exit_code == 1
    assert "conversations-NNN.zip" in result.output
    assert chat_files(vault) == []


def test_F5_05_other_category_zip_is_refused(vault: Path, make_claude_export) -> None:
    export = make_claude_export(name="projects-000.zip", members={"projects/p1.json": "{}"})
    result = run("import", "claude", str(export), "--vault", str(vault))
    assert result.exit_code == 1
    assert "conversations.json" in result.output and "conversations-NNN.zip" in result.output


def test_F5_05_chatgpt_export_is_refused(vault: Path, make_claude_export) -> None:
    chatgpt_style = json.dumps([conversation("g1", [("user", "问")])])
    export = make_claude_export(raw=chatgpt_style)
    result = run("import", "claude", str(export), "--vault", str(vault))
    assert result.exit_code == 1
    assert "eia import chatgpt" in result.output
    assert list(vault.rglob("*.md")) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("{不是 json", "不是合法的 JSON"),
        (json.dumps({"a": 1}), "应为对话列表"),
        (json.dumps(["字符串"]), "应为一个对象"),
    ],
)
def test_F5_05_broken_json_is_refused(
    vault: Path, make_claude_export, raw: str, expected: str
) -> None:
    result = run("import", "claude", str(make_claude_export(raw=raw)), "--vault", str(vault))
    assert result.exit_code == 1
    assert expected in result.output


def _broken(**changes: object) -> dict:
    raw = claude_conversation("c1", [("human", "问")])
    for key, value in changes.items():
        if key.startswith("msg_"):
            raw["chat_messages"][0][key[4:]] = value
        else:
            raw[key] = value
    return raw


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (_broken(uuid=""), "uuid"),
        (_broken(created_at=123), "created_at"),
        (_broken(updated_at="昨天"), "updated_at"),
        (_broken(updated_at="2026-01-01T00:00:00"), "时区"),
        (_broken(chat_messages={}), "chat_messages"),
        (_broken(msg_uuid=None), "uuid"),
        (_broken(msg_parent_message_uuid=5), "parent_message_uuid"),
        (_broken(msg_content="文字"), "content"),
        (_broken(msg_created_at=None), "created_at"),
        (_broken(msg_sender=None), "sender"),
    ],
)
def test_F5_05_each_kind_of_broken_conversation_stops_the_import(
    vault: Path, make_claude_export, raw: dict, expected: str
) -> None:
    result = run("import", "claude", str(make_claude_export([raw])), "--vault", str(vault))
    assert result.exit_code == 1
    assert expected in result.output
    assert "conversations.json 第 1 个对话" in result.output
    assert chat_files(vault) == []


def test_F5_05_messages_that_are_not_objects_are_refused() -> None:
    raw = claude_conversation("c1", [])
    raw["chat_messages"] = ["字符串"]
    with pytest.raises(ExportFormatError, match="每条消息应为一个对象"):
        claude.parse_conversation(raw, "测试")


# ---------- F5-06 更新时间 ----------


def test_F5_06_updated_is_the_latest_of_conversation_and_messages(
    vault: Path, make_claude_export
) -> None:
    raw = claude_conversation("c1", [("human", "问"), ("assistant", "答")], updated=T0 - 100)
    raw["chat_messages"][1]["updated_at"] = iso(T0 + 999.75)
    (parsed,) = load(make_claude_export([raw]))
    assert parsed.updated == dt.datetime.fromtimestamp(T0 + 999, dt.UTC)
    assert parsed.created == dt.datetime.fromtimestamp(T0, dt.UTC)


def test_F5_06_new_messages_update_the_file_even_if_conversation_time_is_stale(
    vault: Path, make_claude_export
) -> None:
    first = claude_conversation("c1", [("human", "问"), ("assistant", "答")], updated=T0)
    import_conversations(vault, load(make_claude_export([first], name="a.zip")))
    later = claude_conversation(
        "c1", [("human", "问"), ("assistant", "答"), ("human", "追问")], updated=T0
    )
    result = import_conversations(vault, load(make_claude_export([later], name="b.zip")))
    assert len(result.updated) == 1
    document = read_document(chat_files(vault)[0])
    assert [t for _, t in split_messages(document.body)] == ["问", "答", "追问"]
    assert document.meta["message_count"] == 3


# ---------- F5-07 重复导入（F6 的要求用 Claude 样例再测） ----------


def test_F5_07_importing_twice_changes_nothing(vault: Path, make_claude_export) -> None:
    export = make_claude_export(basic())
    run("import", "claude", str(export), "--vault", str(vault))
    before = {p: p.read_bytes() for p in vault.rglob("*.md")}
    result = run("import", "claude", str(export), "--vault", str(vault))
    assert "新建 0，更新 0，未变 2，跳过 0" in result.output
    assert before == {p: p.read_bytes() for p in vault.rglob("*.md")}


def test_F5_07_tags_fields_notes_and_renames_survive_reimport(
    vault: Path, make_claude_export
) -> None:
    import_conversations(vault, load(make_claude_export(basic()[:1], name="a.zip")))
    path = chat_files(vault)[0]
    raw = path.read_text(encoding="utf-8").replace("tags: []", "tags:\n- 游戏设计")
    raw = raw.replace("schema: 1\n", "schema: 1\nmy_rating: 5  # 我的打分\n")
    notes = "\n我的笔记 🎮\n"
    moved = vault / "notes" / "改过名.md"
    moved.write_text(raw + notes, encoding="utf-8")
    path.unlink()

    turns = [("human", "合作解谜怎么设计？"), ("assistant", "让信息不对称。"), ("human", "再问")]
    later = claude_conversation("c1", turns)
    result = import_conversations(vault, load(make_claude_export([later], name="b.zip")))

    assert result.created == [] and result.updated == ["notes/改过名.md"]
    text = moved.read_text(encoding="utf-8")
    assert read_document(moved).meta["tags"] == ["游戏设计"]
    assert "my_rating: 5  # 我的打分\n" in text
    assert text.endswith(END + "\n\n（这条线以下是你自己的笔记，重新导入不会动）\n" + notes)
    assert "再问" in text


def test_F5_07_broken_markers_are_skipped_and_dry_run_writes_nothing(
    vault: Path, make_claude_export
) -> None:
    import_conversations(vault, load(make_claude_export(basic(), name="a.zip")))
    damaged_path, other = chat_files(vault)
    damaged = damaged_path.read_text(encoding="utf-8").replace(END, "")
    damaged_path.write_text(damaged, encoding="utf-8")
    newer = [
        claude_conversation(raw["uuid"], [("human", "再问")], name=raw["name"], updated=T0 + 9999)
        for raw in basic()
    ]
    export = make_claude_export(newer, name="b.zip")
    before = {p: p.read_bytes() for p in vault.rglob("*.md")}
    preview = run("import", "claude", str(export), "--vault", str(vault), "--dry-run")
    assert "（预览，未写入）" in preview.output and "更新 1" in preview.output
    assert before == {p: p.read_bytes() for p in vault.rglob("*.md")}

    result = run("import", "claude", str(export), "--vault", str(vault))
    assert result.exit_code == 0, result.output
    assert damaged_path.read_text(encoding="utf-8") == damaged
    assert "跳过 1" in result.output
    assert "再问" in other.read_text(encoding="utf-8")


def test_F5_07_chatgpt_files_with_the_same_id_are_not_touched(
    vault: Path, make_claude_export, make_export
) -> None:
    run(
        "import",
        "chatgpt",
        str(make_export([conversation("c1", [("user", "GPT")])])),
        "--vault",
        str(vault),
    )
    before = {p: p.read_bytes() for p in vault.rglob("*.md")}
    result = run("import", "claude", str(make_claude_export(basic()[:1])), "--vault", str(vault))
    assert "新建 1" in result.output
    assert all(p.read_bytes() == data for p, data in before.items())


# ---------- F5-08 --include-thinking ----------


THINKING = "先想第一步\n\n### Claude · 2025-01-01 00:00\n<!-- eia:conversation:end -->"


def thinking_export(make_claude_export, name: str = "conversations-000.zip") -> Path:
    blocks = [
        thinking_block(THINKING),
        tool_use_block(),
        thinking_block("第二段"),
        text_block("答案"),
    ]
    raw = claude_conversation("c1", [("human", "问"), ("assistant", blocks)])
    return make_claude_export([raw], name=name)


def test_F5_08_thinking_is_a_collapsed_callout_before_the_answer(
    vault: Path, make_claude_export
) -> None:
    result = run(
        "import",
        "claude",
        str(thinking_export(make_claude_export)),
        "--vault",
        str(vault),
        "--include-thinking",
    )
    assert result.exit_code == 0, result.output
    document = read_document(chat_files(vault)[0])
    messages = split_messages(document.body)
    assert [s for s, _ in messages] == ["我", "Claude"]
    assert messages[1][1] == (
        "> [!note]- 思考过程\n"
        "> 先想第一步\n"
        ">\n"
        "> ### Claude · 2025-01-01 00:00\n"
        "> <!-- eia:conversation:end -->\n"
        ">\n"
        "> 第二段\n"
        "\n"
        "答案"
    )
    assert document.meta["thinking"] is True
    assert validate_vault(vault).issues == []


def test_F5_08_default_import_records_that_thinking_is_off(vault: Path, make_claude_export) -> None:
    import_conversations(vault, load(thinking_export(make_claude_export)))
    document = read_document(chat_files(vault)[0])
    assert document.meta["thinking"] is False
    assert "先想第一步" not in document.body


def test_F5_08_switching_the_option_rewrites_without_new_messages(
    vault: Path, make_claude_export
) -> None:
    export = thinking_export(make_claude_export)
    run("import", "claude", str(export), "--vault", str(vault))
    path = chat_files(vault)[0]
    path.write_bytes(path.read_bytes() + "\n我的笔记\n".encode())

    on = run("import", "claude", str(export), "--vault", str(vault), "--include-thinking")
    assert "更新 1" in on.output
    text = path.read_text(encoding="utf-8")
    assert "> 先想第一步" in text and "thinking: true" in text and text.endswith("\n我的笔记\n")

    again = run("import", "claude", str(export), "--vault", str(vault), "--include-thinking")
    assert "未变 1" in again.output

    off = run("import", "claude", str(export), "--vault", str(vault))
    assert "更新 1" in off.output
    text = path.read_text(encoding="utf-8")
    assert "先想第一步" not in text and "thinking: false" in text and text.endswith("\n我的笔记\n")


def test_F5_08_files_imported_before_the_field_existed_count_as_off(
    vault: Path, make_claude_export
) -> None:
    export = thinking_export(make_claude_export)
    import_conversations(vault, load(export))
    path = chat_files(vault)[0]
    path.write_text(path.read_text(encoding="utf-8").replace("thinking: false\n", ""), "utf-8")
    result = run("import", "claude", str(export), "--vault", str(vault))
    assert "未变 1" in result.output


def test_F5_08_chatgpt_files_never_get_a_thinking_field(vault: Path, make_export) -> None:
    run(
        "import",
        "chatgpt",
        str(make_export([conversation("g1", [("user", "问")])])),
        "--vault",
        str(vault),
    )
    (path,) = vault.rglob("*.md")
    assert "thinking" not in read_document(path).meta


# ---------- F5-09 空标题与缺少模型名 ----------


def test_F5_09_blank_name_and_missing_model(vault: Path, make_claude_export) -> None:
    blank = claude_conversation("c1", [("human", "问")], name="   ")
    missing = claude_conversation("c2", [("human", "问")])
    del missing["name"]
    import_conversations(vault, load(make_claude_export([blank, missing])))
    metas = [read_document(path).meta for path in chat_files(vault)]
    assert [meta["title"] for meta in metas] == ["未命名对话", "未命名对话"]
    assert all(meta["model"] is None for meta in metas)
    assert validate_vault(vault).issues == []
