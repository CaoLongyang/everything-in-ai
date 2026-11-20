from __future__ import annotations

import datetime as dt
import re
import tracemalloc
import zipfile
from pathlib import Path

from click.testing import CliRunner

from eia.chat import chatgpt
from eia.chat.importer import import_conversations
from eia.chat.render import END, END_RE, START_RE
from eia.cli import main
from eia.schema.io import read_document
from eia.schema.validate import validate_vault

from .conftest import T0, conversation, msg

HEADING = re.compile(r"^### (我|ChatGPT) · \d{4}-\d{2}-\d{2} \d{2}:\d{2}$", re.MULTILINE)


def run(*args: str):
    return CliRunner().invoke(main, list(args))


def chat_files(root: Path) -> list[Path]:
    return sorted((root / "chats" / "chatgpt").rglob("*.md"))


def split_messages(body: str) -> list[tuple[str, str]]:
    """按约定的标题格式拆分消息，模拟 V1 chat-reader 的读法。"""
    (start,) = START_RE.finditer(body)
    (end,) = END_RE.finditer(body)  # 标记按整行匹配，不按子串
    block = body[start.end() : end.start()]
    pieces = HEADING.split(block)
    return [(pieces[i], pieces[i + 1].strip()) for i in range(1, len(pieces), 2)]


def basic() -> list[dict]:
    return [
        conversation("c1", [("user", "合作解谜怎么设计？"), ("assistant", "让信息不对称。")]),
        conversation("c2", [("user", "第二个问题"), ("assistant", "第二个回答")], title="另一个"),
    ]


# ---------- F4 导入 ChatGPT 对话 ----------


def test_F4_01_one_file_per_conversation(vault: Path, make_export) -> None:
    export = make_export([conversation(f"c{i}", [("user", f"问题{i}")]) for i in range(250)])
    result = run("import", "chatgpt", str(export), "--vault", str(vault))
    assert result.exit_code == 0, result.output
    assert len(chat_files(vault)) == 250
    assert "读取 250 个对话：新建 250，更新 0，未变 0，跳过 0" in result.output


def test_F4_01_old_single_file_export_is_supported(vault: Path, make_export) -> None:
    export = make_export(basic(), single_file=True)
    result = run("import", "chatgpt", str(export), "--vault", str(vault))
    assert result.exit_code == 0, result.output
    assert len(chat_files(vault)) == 2


def test_F4_02_order_speakers_and_text_match_the_export(vault: Path, make_export) -> None:
    turns = [
        ("user", "第一句"),
        ("assistant", "回答一"),
        ("user", "第二句"),
        ("assistant", "回答二"),
    ]
    import_conversations(vault, chatgpt.read_export(make_export([conversation("c1", turns)]))[0])
    body = read_document(chat_files(vault)[0]).body
    assert split_messages(body) == [
        ("我", "第一句"),
        ("ChatGPT", "回答一"),
        ("我", "第二句"),
        ("ChatGPT", "回答二"),
    ]


def test_F4_02_frontmatter_fields_come_from_the_export(vault: Path, make_export) -> None:
    import_conversations(vault, chatgpt.read_export(make_export(basic()))[0])
    meta = read_document(chat_files(vault)[0]).meta
    assert meta["platform"] == "chatgpt"
    assert meta["source"] == "chatgpt"
    assert meta["conversation_id"] in {"c1", "c2"}
    assert meta["created"] == dt.datetime(2025, 3, 12, 8, 1, 22, tzinfo=dt.UTC)
    assert meta["message_count"] == 2
    assert meta["tags"] == []
    assert meta["status"] == "inbox"


def test_F4_02_times_in_body_follow_vault_timezone(vault: Path, make_export) -> None:
    import_conversations(vault, chatgpt.read_export(make_export(basic()))[0])
    body = read_document(chat_files(vault)[0]).body
    assert "### 我 · 2025-03-12 16:01" in body  # 08:01 UTC = 16:01 新加坡


def test_F4_03_code_blocks_chinese_and_emoji_survive(vault: Path, make_export) -> None:
    text = "看这段：\n\n```python\nprint('你好 🎮')\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    export = make_export([conversation("c1", [("user", "问"), ("assistant", text)])])
    import_conversations(vault, chatgpt.read_export(export)[0])
    assert split_messages(read_document(chat_files(vault)[0]).body)[1] == ("ChatGPT", text)


def test_F4_04_every_imported_file_passes_validate(vault: Path, make_export) -> None:
    convs = [
        conversation(f"c{i}", [("user", "问"), ("assistant", "答")], title=t)
        for i, t in enumerate(["普通", 'a<b>:"/\\|?*', "", "超长" * 50, "2024"])
    ]
    import_conversations(vault, chatgpt.read_export(make_export(convs))[0])
    report = validate_vault(vault)
    assert report.files_checked == 5
    assert report.issues == []


def test_F4_05_unknown_format_stops_and_writes_nothing(vault: Path, make_export) -> None:
    good = conversation("c1", [("user", "问")])
    broken = conversation("c2", [("user", "问")])
    del broken["mapping"]
    export = make_export([good, broken])
    result = run("import", "chatgpt", str(export), "--vault", str(vault))
    assert result.exit_code == 1
    assert "mapping" in result.output
    assert "没有写入任何文件" in result.output
    assert chat_files(vault) == []


def test_F4_05_wrong_kind_of_zip_gives_a_clear_hint(vault: Path, tmp_path: Path) -> None:
    export = tmp_path / "memory.zip"
    with zipfile.ZipFile(export, "w") as archive:
        archive.writestr("memory/profile.md", "# 记忆")
    result = run("import", "chatgpt", str(export), "--vault", str(vault))
    assert result.exit_code == 1
    assert "conversations.json" in result.output


def test_F4_05_file_that_is_not_a_zip_is_refused(vault: Path, tmp_path: Path) -> None:
    export = tmp_path / "x.zip"
    export.write_text("不是 zip", encoding="utf-8")
    result = run("import", "chatgpt", str(export), "--vault", str(vault))
    assert result.exit_code == 1
    assert chat_files(vault) == []


def test_F4_05_broken_json_is_refused(vault: Path, make_export) -> None:
    export = make_export([], extra_files={"conversations-001.json": "{不是 json"})
    result = run("import", "chatgpt", str(export), "--vault", str(vault))
    assert result.exit_code == 1
    assert "JSON" in result.output


def test_F4_05_account_files_are_never_read(vault: Path, make_export) -> None:
    import_conversations(vault, chatgpt.read_export(make_export(basic()))[0])
    for path in vault.rglob("*.md"):
        assert "secret@example.com" not in path.read_text(encoding="utf-8")


def test_F4_06_large_sharded_export_stays_within_memory(vault: Path, make_export) -> None:
    long_text = "这是一段比较长的虚构回答。" * 200
    convs = [
        conversation(f"c{i}", [("user", "问"), ("assistant", long_text)] * 5) for i in range(1000)
    ]
    export = make_export(convs)
    del convs
    tracemalloc.start()
    try:
        conversations, _ = chatgpt.read_export(export)
        import_conversations(vault, conversations)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(chat_files(vault)) == 1000
    assert peak < 300 * 1024 * 1024


def test_F4_07_only_the_visible_branch_is_imported(vault: Path, make_export) -> None:
    old_answer = msg("old", "assistant", "被重新生成掉的旧回答", parent="c1-m0", t=T0 + 30)
    edited = msg("edit", "user", "编辑前的问题", parent="root", t=T0 - 30)
    conv = conversation(
        "c1", [("user", "问题"), ("assistant", "新回答")], extra_nodes=[old_answer, edited]
    )
    import_conversations(vault, chatgpt.read_export(make_export([conv]))[0])
    body = read_document(chat_files(vault)[0]).body
    assert split_messages(body) == [("我", "问题"), ("ChatGPT", "新回答")]
    assert "旧回答" not in body and "编辑前" not in body


def test_F4_08_thinking_system_and_empty_parts_are_left_out(vault: Path, make_export) -> None:
    extra = [
        msg("sys", "system", "系统提示", parent="c1-m1", t=T0 + 100),
        msg(
            "think",
            "assistant",
            parent="sys",
            t=T0 + 101,
            content={"content_type": "thoughts", "thoughts": [{"content": "内部推理"}]},
        ),
        msg(
            "recap",
            "assistant",
            parent="think",
            t=T0 + 102,
            content={"content_type": "reasoning_recap", "content": "已思考 12 秒"},
        ),
        msg("hidden", "user", "隐藏消息", parent="recap", t=T0 + 103, hidden=True),
        msg(
            "empty",
            "user",
            parent="hidden",
            t=T0 + 104,
            content={"content_type": "text", "parts": ["", "  "]},
        ),
        msg("tool", "tool", "工具输出", parent="empty", t=T0 + 105),
        msg(
            "img",
            "user",
            parent="tool",
            t=T0 + 106,
            content={
                "content_type": "multimodal_text",
                "parts": [
                    {
                        "content_type": "image_asset_pointer",
                        "asset_pointer": "file-service://x",
                        "width": 1024,
                        "height": 768,
                    },
                    "这张图是什么？",
                ],
            },
        ),
        msg(
            "voice",
            "user",
            parent="img",
            t=T0 + 107,
            content={
                "content_type": "multimodal_text",
                "parts": [
                    {
                        "content_type": "audio_transcription",
                        "text": "语音里说的话",
                        "direction": "in",
                    },
                ],
            },
        ),
    ]
    conv = conversation("c1", [("user", "问"), ("assistant", "答")], extra_nodes=extra)
    conv["current_node"] = "voice"
    import_conversations(vault, chatgpt.read_export(make_export([conv]))[0])
    document = read_document(chat_files(vault)[0])
    assert [text for _, text in split_messages(document.body)] == [
        "问",
        "答",
        "[图片，1024×768]\n\n这张图是什么？",
        "（语音）语音里说的话",
    ]
    for gone in ("系统提示", "内部推理", "已思考", "隐藏消息", "工具输出"):
        assert gone not in document.body
    assert document.meta["message_count"] == 4


def test_F4_09_dry_run_writes_nothing(vault: Path, make_export) -> None:
    before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
    result = run("import", "chatgpt", str(make_export(basic())), "--vault", str(vault), "--dry-run")
    after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
    assert result.exit_code == 0, result.output
    assert before == after
    assert "（预览，未写入）读取 2 个对话：新建 2" in result.output


def test_F4_10_lookalike_lines_in_messages_are_escaped(vault: Path, make_export) -> None:
    sneaky = (
        "正常一行\n### 我 · 2020-01-01 00:00\n### ChatGPT · 伪造\n"
        "<!-- eia:conversation:end -->\n结尾"
    )
    export = make_export([conversation("c1", [("user", sneaky), ("assistant", "答")])])
    import_conversations(vault, chatgpt.read_export(export)[0])
    body = read_document(chat_files(vault)[0]).body
    messages = split_messages(body)
    assert [who for who, _ in messages] == ["我", "ChatGPT"]
    assert messages[0][1] == sneaky.replace("\n###", "\n\\###").replace("\n<!--", "\n\\<!--")
    assert len(END_RE.findall(body)) == 1


# ---------- F6 重复导入 ----------


def test_F6_01_importing_twice_changes_nothing(vault: Path, make_export) -> None:
    export = make_export(basic())
    run("import", "chatgpt", str(export), "--vault", str(vault))
    before = {p: p.read_bytes() for p in vault.rglob("*.md")}
    result = run("import", "chatgpt", str(export), "--vault", str(vault))
    after = {p: p.read_bytes() for p in vault.rglob("*.md")}
    assert "新建 0，更新 0，未变 2，跳过 0" in result.output
    assert before == after


def test_F6_02_new_messages_update_the_same_file(vault: Path, make_export, tmp_path: Path) -> None:
    first = conversation("c1", [("user", "问"), ("assistant", "答")])
    import_conversations(vault, chatgpt.read_export(make_export([first], name="a.zip"))[0])
    later = conversation(
        "c1", [("user", "问"), ("assistant", "答"), ("user", "追问"), ("assistant", "再答")]
    )
    result = import_conversations(vault, chatgpt.read_export(make_export([later], name="b.zip"))[0])
    files = chat_files(vault)
    assert len(files) == 1 and len(result.updated) == 1
    document = read_document(files[0])
    assert [t for _, t in split_messages(document.body)] == ["问", "答", "追问", "再答"]
    assert document.meta["message_count"] == 4
    assert document.meta["updated"] == dt.datetime.fromtimestamp(T0 + 240, dt.UTC)
    assert validate_vault(vault).issues == []


def test_F6_03_user_tags_status_and_fields_survive_reimport(vault: Path, make_export) -> None:
    import_conversations(vault, chatgpt.read_export(make_export(basic()[:1], name="a.zip"))[0])
    path = chat_files(vault)[0]
    raw = path.read_text(encoding="utf-8")
    raw = raw.replace("tags: []", "tags:\n- 游戏设计\n- 灵感").replace(
        "status: inbox", "status: processed"
    )
    raw = raw.replace("schema: 1\n", "schema: 1\nmy_rating: 5  # 我的打分\n")
    path.write_text(raw, encoding="utf-8")

    later = conversation(
        "c1", [("user", "合作解谜怎么设计？"), ("assistant", "让信息不对称。"), ("user", "再问")]
    )
    result = import_conversations(vault, chatgpt.read_export(make_export([later], name="b.zip"))[0])

    assert len(result.updated) == 1
    text = path.read_text(encoding="utf-8")
    meta = read_document(path).meta
    assert meta["tags"] == ["游戏设计", "灵感"]
    assert meta["status"] == "processed"
    assert meta["my_rating"] == 5
    assert "my_rating: 5  # 我的打分\n" in text


def test_F6_04_renamed_and_moved_files_are_found_by_id(vault: Path, make_export) -> None:
    import_conversations(vault, chatgpt.read_export(make_export(basic()[:1], name="a.zip"))[0])
    original = chat_files(vault)[0]
    moved = vault / "notes" / "我改过名字.md"
    original.rename(moved)
    later = conversation(
        "c1", [("user", "合作解谜怎么设计？"), ("assistant", "让信息不对称。"), ("user", "再问")]
    )
    result = import_conversations(vault, chatgpt.read_export(make_export([later], name="b.zip"))[0])
    assert result.created == []
    assert result.updated == ["notes/我改过名字.md"]
    assert chat_files(vault) == []
    assert "再问" in moved.read_text(encoding="utf-8")


def test_F6_05_files_with_broken_markers_are_left_untouched(vault: Path, make_export) -> None:
    import_conversations(vault, chatgpt.read_export(make_export(basic()[:1], name="a.zip"))[0])
    path = chat_files(vault)[0]
    damaged = path.read_text(encoding="utf-8").replace(END, "")
    path.write_text(damaged, encoding="utf-8")
    later = conversation(
        "c1", [("user", "合作解谜怎么设计？"), ("user", "再问")], updated=T0 + 9999
    )
    result = run(
        "import", "chatgpt", str(make_export([later], name="b.zip")), "--vault", str(vault)
    )
    assert result.exit_code == 0, result.output
    assert path.read_text(encoding="utf-8") == damaged
    assert "跳过  chats/chatgpt/" in result.output
    assert "跳过 1" in result.output


def test_F6_05_duplicate_files_for_one_conversation_are_skipped(vault: Path, make_export) -> None:
    import_conversations(vault, chatgpt.read_export(make_export(basic()[:1], name="a.zip"))[0])
    path = chat_files(vault)[0]
    copy = path.with_name("副本.md")
    copy.write_bytes(path.read_bytes())
    later = conversation("c1", [("user", "再问")], updated=T0 + 9999)
    result = import_conversations(vault, chatgpt.read_export(make_export([later], name="b.zip"))[0])
    assert result.updated == [] and len(result.skipped) == 1
    assert copy.read_bytes() == path.read_bytes()


def test_F6_06_user_notes_outside_markers_are_byte_identical(vault: Path, make_export) -> None:
    import_conversations(vault, chatgpt.read_export(make_export(basic()[:1], name="a.zip"))[0])
    path = chat_files(vault)[0]
    notes = "\n我的笔记：信息不对称可以用在我的游戏里。\n\n- 要点一\n- 要点二 🎮\n"
    path.write_bytes(path.read_bytes() + notes.encode("utf-8"))
    later = conversation(
        "c1", [("user", "合作解谜怎么设计？"), ("assistant", "让信息不对称。"), ("user", "再问")]
    )
    import_conversations(vault, chatgpt.read_export(make_export([later], name="b.zip"))[0])
    text = path.read_text(encoding="utf-8")
    assert text.endswith(END + "\n\n（这条线以下是你自己的笔记，重新导入不会动）\n" + notes)
    assert "再问" in text


def test_F6_06_crlf_files_keep_their_notes_and_line_endings(vault: Path, make_export) -> None:
    import_conversations(vault, chatgpt.read_export(make_export(basic()[:1], name="a.zip"))[0])
    path = chat_files(vault)[0]
    crlf = path.read_bytes().replace(b"\n", b"\r\n") + "笔记\r\n".encode()
    path.write_bytes(crlf)
    later = conversation(
        "c1", [("user", "合作解谜怎么设计？"), ("user", "再问")], updated=T0 + 9999
    )
    import_conversations(vault, chatgpt.read_export(make_export([later], name="b.zip"))[0])
    data = path.read_bytes()
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert data.endswith("笔记\r\n".encode())
    assert "再问" in data.decode("utf-8")


def test_F4_09_dry_run_does_not_touch_files_that_would_be_updated(vault: Path, make_export) -> None:
    import_conversations(vault, chatgpt.read_export(make_export(basic()[:1], name="a.zip"))[0])
    before = {p: p.read_bytes() for p in vault.rglob("*.md")}
    later = conversation("c1", [("user", "再问")], updated=T0 + 9999)
    export = make_export([later], name="b.zip")
    result = run("import", "chatgpt", str(export), "--vault", str(vault), "--dry-run")
    assert "更新 1" in result.output
    assert {p: p.read_bytes() for p in vault.rglob("*.md")} == before
