from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from eia.schema.io import render_document
from eia.schema.validate import validate_vault

from .conftest import base_meta, chat_meta


def fields_with_errors(root: Path, rel: str) -> set[str]:
    return {i.field for i in validate_vault(root).errors if i.path == rel}


def test_F2_01_valid_vault_exits_zero(vault: Path, write, run) -> None:
    write(vault, "notes/a.md", base_meta())
    write(vault, "chats/claude/c.md", chat_meta())
    result = run("validate", str(vault))
    assert result.exit_code == 0, result.output
    assert "检查了 2 个文件：0 个错误，0 个警告" in result.output


@pytest.mark.parametrize("missing", ["schema", "id", "type", "title", "created", "status"])
def test_F2_02_missing_required_field_reports_path_and_field(
    vault: Path, write, run, missing: str
) -> None:
    meta = base_meta()
    del meta[missing]
    write(vault, "notes/a.md", meta)

    result = run("validate", str(vault))

    assert result.exit_code == 1
    assert f"错误  notes/a.md  [{missing}]  缺少必填字段" in result.output


@pytest.mark.parametrize(
    ("meta", "missing"),
    [
        (chat_meta(), "platform"),
        (chat_meta(), "conversation_id"),
        (
            base_meta(
                id="headline-20261115-abcdef", type="headline", sources=[], independent_count=0
            ),
            "sources",
        ),
        (
            base_meta(
                id="headline-20261115-abcdef", type="headline", sources=[], independent_count=0
            ),
            "independent_count",
        ),
        (
            base_meta(id="digest-20261115-abcdef", type="digest", date=dt.date(2026, 11, 15)),
            "date",
        ),
    ],
)
def test_F2_02_missing_type_specific_field_is_reported(
    vault: Path, write, meta: dict[str, Any], missing: str
) -> None:
    meta = dict(meta)
    del meta[missing]
    write(vault, "x.md", meta)
    assert missing in fields_with_errors(vault, "x.md")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", 2),
        ("type", "video"),
        ("status", "done"),
        ("title", ""),
        ("title", "   "),
        ("title", 2024),
        ("created", dt.datetime(2026, 11, 15, 10, 0)),
        ("created", dt.datetime(2026, 11, 15, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))),
        ("created", "昨天"),
        ("tags", ["AI"]),
        ("tags", ["two words"]),
        ("tags", "ai"),
        ("id", "note-1"),
        ("id", "clip-20261115-abcdef"),
    ],
)
def test_F2_03_invalid_value_is_reported(vault: Path, write, field: str, value: Any) -> None:
    write(vault, "notes/a.md", base_meta(**{field: value}))
    report = validate_vault(vault)
    assert not report.ok
    assert field in {i.field.split(".")[0] for i in report.errors if i.path == "notes/a.md"}


def test_F2_03_valid_optional_fields_are_accepted(vault: Path, write) -> None:
    meta = base_meta(
        updated=dt.datetime(2026, 11, 16, tzinfo=dt.UTC),
        tags=["ai", "游戏设计", "open-source"],
        source="claude",
        url="https://example.com",
        lang="zh",
        links=["note-20261101-abcdef"],
    )
    write(vault, "notes/a.md", meta)
    report = validate_vault(vault)
    assert report.issues == []


def test_F2_04_duplicate_id_is_an_error_on_both_files(vault: Path, write, run) -> None:
    write(vault, "notes/a.md", base_meta())
    write(vault, "notes/b.md", base_meta(title="另一篇"))
    result = run("validate", str(vault))
    assert result.exit_code == 1
    assert fields_with_errors(vault, "notes/a.md") == {"id"}
    assert fields_with_errors(vault, "notes/b.md") == {"id"}


def test_F2_05_same_conversation_twice_is_an_error(vault: Path, write) -> None:
    write(vault, "chats/claude/a.md", chat_meta())
    write(vault, "chats/claude/b.md", chat_meta(id="chat-20261115-bbbbbbbbbbbb"))
    assert "conversation_id" in fields_with_errors(vault, "chats/claude/a.md")
    assert "conversation_id" in fields_with_errors(vault, "chats/claude/b.md")


def test_F2_05_same_conversation_id_on_other_platform_is_fine(vault: Path, write) -> None:
    write(vault, "chats/claude/a.md", chat_meta())
    other = chat_meta(id="chat-20261115-bbbbbbbbbbbb", platform="chatgpt")
    write(vault, "chats/chatgpt/b.md", other)
    assert validate_vault(vault).ok


def test_F2_06_unknown_field_is_only_a_warning(vault: Path, write, run) -> None:
    write(vault, "notes/a.md", base_meta(my_rating=5))
    result = run("validate", str(vault))
    assert result.exit_code == 0, result.output
    assert "警告  notes/a.md  [my_rating]" in result.output


@pytest.mark.parametrize("ignored", [".obsidian", ".git", ".eia", "logseq"])
def test_F2_07_ignored_directories_are_not_checked(vault: Path, ignored: str) -> None:
    broken = vault / ignored / "sub" / "broken.md"
    broken.parent.mkdir(parents=True)
    broken.write_text("没有 frontmatter", encoding="utf-8")
    report = validate_vault(vault)
    assert report.ok
    assert report.files_checked == 0


def test_F2_07_config_yaml_is_not_treated_as_an_entry(vault: Path) -> None:
    report = validate_vault(vault)
    assert report.files_checked == 0
    assert report.issues == []


@pytest.mark.parametrize(
    "content",
    [
        "只有正文，没有字段块\n",
        "---\ntitle: [没有闭合\n---\n正文\n",
        "---\n- 一个列表\n- 不是键值\n---\n正文\n",
        "---\ntitle: 没有结束分隔线\n",
    ],
)
def test_F2_08_broken_frontmatter_is_reported_without_crashing(
    vault: Path, run, content: str
) -> None:
    (vault / "notes" / "a.md").write_text(content, encoding="utf-8")
    result = run("validate", str(vault))
    assert result.exit_code == 1
    assert "错误  notes/a.md  [frontmatter]" in result.output


def test_F2_08_non_utf8_file_is_reported_without_crashing(vault: Path, run) -> None:
    (vault / "notes" / "a.md").write_bytes("---\ntitle: 中文\n---\n".encode("gbk"))
    result = run("validate", str(vault))
    assert result.exit_code == 1
    assert "UTF-8" in result.output


def test_F2_09_gitignore_without_env_is_an_error(vault: Path, run) -> None:
    (vault / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    result = run("validate", str(vault))
    assert result.exit_code == 1
    assert "错误  .gitignore" in result.output


def test_F2_09_directory_that_is_not_a_vault_is_an_error(tmp_path: Path, run) -> None:
    result = run("validate", str(tmp_path))
    assert result.exit_code == 1
    assert "eia init" in result.output


def test_F2_10_json_output_is_machine_readable(vault: Path, write, run) -> None:
    meta = base_meta(extra_field="x")
    del meta["title"]
    write(vault, "notes/a.md", meta)
    result = run("validate", str(vault), "--json")
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["files_checked"] == 1
    assert payload["errors"] == [
        {"level": "error", "path": "notes/a.md", "field": "title", "message": "缺少必填字段"}
    ]
    assert [w["field"] for w in payload["warnings"]] == ["extra_field"]


def test_F2_11_validate_never_modifies_files(vault: Path, write, run) -> None:
    meta = base_meta()
    del meta["status"]
    write(vault, "notes/a.md", meta)
    before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
    run("validate", str(vault))
    after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
    assert before == after


@pytest.mark.parametrize("field", ["tags", "links"])
def test_F2_12_empty_list_written_by_obsidian_is_accepted(vault: Path, field: str) -> None:
    """Obsidian 把清空的列表属性写成 `tags:`（值为空），这应视为空列表。"""
    path = vault / "notes" / "a.md"
    raw = render_document(base_meta(), "正文\n").replace(
        "status: inbox\n", f"status: inbox\n{field}:\n"
    )
    path.write_text(raw, encoding="utf-8")
    report = validate_vault(vault)
    assert report.issues == []
