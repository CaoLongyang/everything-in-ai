from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

from eia.schema.io import (
    FrontmatterError,
    atomic_write,
    parse_text,
    read_document,
    render_document,
    update_frontmatter,
)

from .conftest import base_meta

HAND_EDITED = (
    "---\n"
    "# 用户自己写的注释\n"
    "title:   '带引号的：标题'\n"
    "schema: 1\n"
    'id: "note-20261115-abcdef"\n'
    "type: note\n"
    "created: 2026-11-15T10:00:00Z\n"
    "tags: [ai,  游戏设计]   # 行内注释\n"
    "status: inbox\n"
    "\n"
    "obsidian_plugin:\n"
    "    nested:   {a: 1, b: [x, y]}\n"
    "    text: |\n"
    "      多行\n"
    "      文本\n"
    "aliases:\n"
    "- 别名一\n"
    "- 别名二\n"
    "---\n"
    "\n"
    "# 正文\n"
    "\n"
    "---\n"
    "正文里也有分隔线和 title: 假字段\n"
)


def test_F7_01_no_change_roundtrip_is_byte_identical() -> None:
    assert update_frontmatter(HAND_EDITED, {}) == HAND_EDITED


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_F7_01_read_then_write_back_is_byte_identical(tmp_path: Path, newline: str) -> None:
    original = ("﻿" + HAND_EDITED).replace("\n", newline).encode("utf-8")
    path = tmp_path / "a.md"
    path.write_bytes(original)

    doc = read_document(path)
    atomic_write(path, update_frontmatter(doc.raw, {}))

    assert path.read_bytes() == original
    assert doc.meta["title"] == "带引号的：标题"
    assert doc.meta["tags"] == ["ai", "游戏设计"]


def test_F7_01_body_is_separated_at_the_first_closing_line() -> None:
    doc = parse_text(HAND_EDITED, Path("a.md"))
    assert doc.body.startswith("\n# 正文\n")
    assert "title: 假字段" in doc.body
    assert doc.meta["aliases"] == ["别名一", "别名二"]


def test_F7_02_failed_write_leaves_original_intact(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "a.md"
    path.write_bytes(b"original")

    def boom(src: object, dst: object) -> None:
        raise OSError("磁盘满了")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write(path, "new content")

    assert path.read_bytes() == b"original"
    assert [p.name for p in tmp_path.iterdir()] == ["a.md"]


def test_F7_02_successful_write_leaves_no_temp_files(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_bytes(b"original")
    atomic_write(path, "新内容")
    assert path.read_bytes() == "新内容".encode()
    assert [p.name for p in tmp_path.iterdir()] == ["a.md"]


def changed_lines(before: str, after: str) -> tuple[set[str], set[str]]:
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    return set(old) - set(new), set(new) - set(old)


def test_F7_03_changing_one_field_leaves_every_other_line_untouched() -> None:
    result = update_frontmatter(HAND_EDITED, {"status": "processed"})
    removed, added = changed_lines(HAND_EDITED, result)
    assert removed == {"status: inbox\n"}
    assert added == {"status: processed\n"}


def test_F7_03_changing_a_list_field_keeps_neighbours_and_comments() -> None:
    result = update_frontmatter(HAND_EDITED, {"aliases": ["新别名"]})
    removed, added = changed_lines(HAND_EDITED, result)
    assert removed == {"- 别名一\n", "- 别名二\n"}
    assert added == {"- 新别名\n"}
    assert parse_text(result, Path("a.md")).meta["aliases"] == ["新别名"]


def test_F7_03_blank_lines_and_comments_after_a_changed_list_stay_in_place() -> None:
    text = (
        "---\n"
        "tags:\n"
        "- 旧标签\n"
        "\n"
        "# 关于别名的注释\n"
        "aliases: [a]\n"
        "nested:\n"
        "  inner: 1\n"
        "  # 嵌套里的注释\n"
        "last: x\n"
        "---\n"
        "正文\n"
    )
    result = update_frontmatter(text, {"tags": ["新标签", "第二个"], "nested": {"inner": 2}})
    assert result == (
        "---\n"
        "tags:\n"
        "- 新标签\n"
        "- 第二个\n"
        "\n"
        "# 关于别名的注释\n"
        "aliases: [a]\n"
        "nested:\n"
        "  inner: 2\n"
        "  # 嵌套里的注释\n"
        "last: x\n"
        "---\n"
        "正文\n"
    )


def test_F7_03_adding_a_new_field_appends_without_touching_existing_lines() -> None:
    when = dt.datetime(2026, 11, 16, 8, 0, tzinfo=dt.UTC)
    result = update_frontmatter(HAND_EDITED, {"updated": when, "message_count": 12})
    removed, added = changed_lines(HAND_EDITED, result)
    assert removed == set()
    assert added == {"updated: 2026-11-16T08:00:00Z\n", "message_count: 12\n"}
    meta = parse_text(result, Path("a.md")).meta
    assert meta["updated"] == when
    assert meta["message_count"] == 12


def test_F7_03_user_fields_survive_an_update_of_other_fields() -> None:
    result = update_frontmatter(HAND_EDITED, {"title": "新标题", "status": "archived"})
    meta = parse_text(result, Path("a.md")).meta
    assert meta["tags"] == ["ai", "游戏设计"]
    assert meta["obsidian_plugin"] == {"nested": {"a": 1, "b": ["x", "y"]}, "text": "多行\n文本\n"}
    assert "# 用户自己写的注释\n" in result
    assert result.endswith("正文里也有分隔线和 title: 假字段\n")


def test_F7_03_crlf_files_stay_crlf_after_an_update() -> None:
    crlf = HAND_EDITED.replace("\n", "\r\n")
    result = update_frontmatter(crlf, {"status": "processed", "updated_by": "eia"})
    assert "\n" not in result.replace("\r\n", "")
    assert "status: processed\r\n" in result


def test_F7_03_update_without_frontmatter_is_refused() -> None:
    with pytest.raises(FrontmatterError):
        update_frontmatter("只有正文\n", {"status": "processed"})


def test_F7_04_new_files_are_written_readably() -> None:
    text = render_document(base_meta(tags=["游戏设计"]), "正文\n")
    assert text.startswith("---\nschema: 1\nid: note-20261115-abcdef\ntype: note\n")
    assert "title: 测试笔记\n" in text
    assert "created: 2026-11-15T10:00:00Z\n" in text
    assert "- 游戏设计\n" in text
    assert text.endswith("---\n\n正文\n")
    assert parse_text(text, Path("a.md")).meta == base_meta(tags=["游戏设计"])


@pytest.mark.parametrize("title", ["2024", "yes", "a: b", "# 不是注释", "'引号'", "- 横线", ""])
def test_F7_04_titles_that_look_like_yaml_survive_a_roundtrip(title: str) -> None:
    text = render_document(base_meta(title=title), "")
    assert parse_text(text, Path("a.md")).meta["title"] == title
