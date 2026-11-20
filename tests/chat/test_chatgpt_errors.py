"""异常输入：损坏的导出、vault 里的异常文件。都必须报清楚，并且不误改任何文件。"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from eia.chat import chatgpt
from eia.chat.importer import import_conversations
from eia.cli import main
from eia.schema.io import read_document

from .conftest import T0, conversation, msg


def run(*args: str):
    return CliRunner().invoke(main, list(args))


def chat_files(root: Path) -> list[Path]:
    return sorted((root / "chats" / "chatgpt").rglob("*.md"))


def snapshot(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _bad_time(conv: dict[str, Any]) -> None:
    conv["create_time"] = "昨天"


def _bool_time(conv: dict[str, Any]) -> None:
    conv["update_time"] = True


def _bad_message_time(conv: dict[str, Any]) -> None:
    conv["mapping"]["c1-m0"]["message"]["create_time"] = "noon"


def _dangling_current(conv: dict[str, Any]) -> None:
    conv["current_node"] = "不存在"


def _cycle(conv: dict[str, Any]) -> None:
    conv["mapping"]["root"]["parent"] = "c1-m1"


def _missing_parent(conv: dict[str, Any]) -> None:
    conv["mapping"]["c1-m0"]["parent"] = "已删除的节点"


def _no_id(conv: dict[str, Any]) -> None:
    del conv["conversation_id"]
    del conv["id"]


def _mapping_is_list(conv: dict[str, Any]) -> None:
    conv["mapping"] = []


BREAKAGES: dict[str, tuple[Callable[[dict[str, Any]], None], str]] = {
    "时间不是数字": (_bad_time, "时间应为数字时间戳"),
    "时间是布尔值": (_bool_time, "时间应为数字时间戳"),
    "消息时间不是数字": (_bad_message_time, "时间应为数字时间戳"),
    "current_node 悬空": (_dangling_current, "current_node 指向不存在的消息"),
    "消息树有循环": (_cycle, "消息树里有循环"),
    "父节点不存在": (_missing_parent, "找不到消息"),
    "没有对话 ID": (_no_id, "缺少对话 ID"),
    "mapping 不是对象": (_mapping_is_list, "mapping 应为对象"),
}


@pytest.mark.parametrize("name", list(BREAKAGES))
def test_F4_05_each_kind_of_broken_conversation_stops_the_import(
    vault: Path, make_export, name: str
) -> None:
    breakage, expected = BREAKAGES[name]
    broken = conversation("c1", [("user", "问"), ("assistant", "答")])
    breakage(broken)
    export = make_export([conversation("ok", [("user", "正常的对话")]), broken])
    before = snapshot(vault)

    result = run("import", "chatgpt", str(export), "--vault", str(vault))

    assert result.exit_code == 1
    assert expected in result.output
    assert "没有写入任何文件" in result.output
    assert snapshot(vault) == before


def test_F4_05_conversation_that_is_not_an_object_is_refused(vault: Path, make_export) -> None:
    export = make_export([conversation("ok", [("user", "问")]), "不是对象"])
    result = run("import", "chatgpt", str(export), "--vault", str(vault))
    assert result.exit_code == 1
    assert "每个对话应为一个对象" in result.output
    assert chat_files(vault) == []


def test_F4_05_shard_that_is_not_a_list_is_refused(vault: Path, make_export) -> None:
    export = make_export([], extra_files={"conversations-001.json": json.dumps({"a": 1})})
    result = run("import", "chatgpt", str(export), "--vault", str(vault))
    assert result.exit_code == 1
    assert "应为对话列表" in result.output


def test_F4_05_error_message_names_the_file_and_position(vault: Path, make_export) -> None:
    broken = conversation("c-bad", [("user", "问")])
    del broken["mapping"]
    convs = [conversation(f"c{i}", [("user", "问")]) for i in range(3)] + [broken]
    result = run("import", "chatgpt", str(make_export(convs)), "--vault", str(vault))
    assert "conversations-000.json 第 4 个对话" in result.output


def test_F4_06_very_large_single_file_gives_a_memory_hint(
    vault: Path, make_export, monkeypatch
) -> None:
    monkeypatch.setattr(chatgpt, "_SINGLE_FILE_WARN_BYTES", 10)
    export = make_export([conversation("c1", [("user", "问")])], single_file=True)
    result = run("import", "chatgpt", str(export), "--vault", str(vault))
    assert result.exit_code == 0, result.output
    assert "提示  conversations.json 有 0 MB，读取会占用较多内存" in result.output


def test_F4_08_unusual_message_parts_are_handled(vault: Path, make_export) -> None:
    extra = [
        msg(
            "img",
            "user",
            parent="c1-m0",
            t=T0 + 10,
            content={
                "content_type": "multimodal_text",
                "parts": [{"content_type": "image_asset_pointer", "asset_pointer": "x"}],
            },
        ),
        msg(
            "weird",
            "assistant",
            parent="img",
            t=T0 + 20,
            content={"content_type": "text", "parts": "不是列表"},
        ),
        msg(
            "silent",
            "user",
            parent="weird",
            t=T0 + 30,
            content={
                "content_type": "multimodal_text",
                "parts": [{"content_type": "audio_transcription", "text": "   "}, {"x": 1}],
            },
        ),
        msg("ok", "assistant", parent="silent", t=T0 + 40),
    ]
    extra[-1]["message"]["content"]["parts"] = ["正常回答"]
    conv = conversation("c1", [("user", "问")], extra_nodes=extra)
    conv["current_node"] = "ok"
    conv["mapping"]["ok"]["message"]["create_time"] = None
    conversations, _ = chatgpt.read_export(make_export([conv]))
    import_conversations(vault, conversations)
    document = read_document(chat_files(vault)[0])
    assert "[图片]" in document.body
    assert "不是列表" not in document.body
    assert document.meta["message_count"] == 3  # 问、[图片]、正常回答


def test_F4_08_conversation_with_nothing_to_import_is_reported(vault: Path, make_export) -> None:
    empty = conversation("c-empty", [("user", "")], title="New chat")
    export = make_export([empty, conversation("c1", [("user", "问")])])
    result = run("import", "chatgpt", str(export), "--vault", str(vault))
    assert result.exit_code == 0, result.output
    assert "跳过  New chat  对话里没有可导入的消息" in result.output
    assert len(chat_files(vault)) == 1


# ---------- F6-07 vault 里的异常文件 ----------


def test_F6_07_broken_files_in_the_vault_do_not_stop_the_import(vault: Path, make_export) -> None:
    junk = vault / "chats" / "chatgpt" / "随手记.md"
    junk.write_text("没有字段块的文件", encoding="utf-8")
    gbk = vault / "notes" / "旧编码.md"
    gbk.write_bytes("---\ntitle: 中文\n---\n".encode("gbk"))
    result = run(
        "import",
        "chatgpt",
        str(make_export([conversation("c1", [("user", "问")])])),
        "--vault",
        str(vault),
    )
    assert result.exit_code == 0, result.output
    assert "新建 1" in result.output
    assert junk.read_text(encoding="utf-8") == "没有字段块的文件"
    assert gbk.read_bytes() == "---\ntitle: 中文\n---\n".encode("gbk")


def test_F6_07_existing_file_with_the_target_name_is_never_overwritten(
    vault: Path, make_export
) -> None:
    export = make_export([conversation("c1", [("user", "问")])])
    conversations, _ = chatgpt.read_export(export)
    planned = import_conversations(vault, conversations, dry_run=True).created[0]
    squatter = vault / planned
    squatter.parent.mkdir(parents=True, exist_ok=True)
    squatter.write_text("别人的文件，没有对话 ID", encoding="utf-8")

    result = import_conversations(vault, conversations)

    assert result.created == []
    assert result.skipped == [(planned, "同名文件已存在")]
    assert squatter.read_text(encoding="utf-8") == "别人的文件，没有对话 ID"


def test_F6_07_file_without_a_valid_updated_time_is_refreshed(vault: Path, make_export) -> None:
    import_conversations(
        vault,
        chatgpt.read_export(make_export([conversation("c1", [("user", "问")])], name="a.zip"))[0],
    )
    path = chat_files(vault)[0]
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text(
        "".join(line for line in lines if not line.startswith("updated:")), encoding="utf-8"
    )

    later = conversation("c1", [("user", "问"), ("assistant", "新回答")])
    result = import_conversations(vault, chatgpt.read_export(make_export([later], name="b.zip"))[0])

    assert len(result.updated) == 1
    assert "新回答" in path.read_text(encoding="utf-8")
    assert read_document(path).meta["updated"] is not None


def test_F6_07_files_are_matched_per_platform(vault: Path, make_export) -> None:
    other = vault / "chats" / "claude" / "同一个id.md"
    other.write_text(
        "---\nschema: 1\nid: chat-20250312-aaaaaaaaaaaa\ntype: chat\ntitle: x\n"
        "created: 2025-03-12T08:01:22Z\nstatus: inbox\n"
        "platform: claude\nconversation_id: c1\n---\n",
        encoding="utf-8",
    )
    before = other.read_bytes()
    export = make_export([conversation("c1", [("user", "问")])])
    result = run("import", "chatgpt", str(export), "--vault", str(vault))
    assert "新建 1" in result.output
    assert other.read_bytes() == before


def test_F6_07_zip_with_extra_unrelated_files_is_fine(vault: Path, tmp_path: Path) -> None:
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "sub/conversations-000.json", json.dumps([conversation("c1", [("user", "问")])])
        )
        archive.writestr("file_0001.dat", b"\x00\x01")
        archive.writestr("conversation_summaries.json", "[]")
    result = run("import", "chatgpt", str(path), "--vault", str(vault))
    assert result.exit_code == 0, result.output
    assert len(chat_files(vault)) == 1
