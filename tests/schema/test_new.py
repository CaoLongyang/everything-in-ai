from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

from eia.schema.create import new_entry
from eia.schema.ids import chat_id
from eia.schema.io import read_document
from eia.schema.naming import MAX_SLUG_BYTES, slugify
from eia.schema.validate import validate_vault

FORBIDDEN = set('<>:"/\\|?*')
NOW = dt.datetime(2026, 11, 15, 18, 30, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("entry_type", "extra", "folder"),
    [
        ("note", [], "notes"),
        ("topic", [], "topics"),
        ("clip", ["--url", "https://example.com/a"], "inbox/clips"),
        ("digest", [], "inbox/digest"),
        ("headline", [], "inbox/headlines"),
        ("chat", ["--platform", "claude", "--conversation-id", "abc-123"], "chats/claude"),
    ],
)
def test_F3_01_every_type_creates_a_file_that_passes_validate(
    vault: Path, run, entry_type: str, extra: list[str], folder: str
) -> None:
    result = run("new", entry_type, "--title", "测试条目", "--vault", str(vault), *extra)
    assert result.exit_code == 0, result.output
    files = list((vault / folder).glob("*.md"))
    assert len(files) == 1
    report = validate_vault(vault)
    assert report.issues == []
    assert read_document(files[0]).meta["type"] == entry_type


def test_F3_01_new_outside_a_vault_fails_with_a_hint(tmp_path: Path, run) -> None:
    result = run("new", "note", "--title", "x", "--vault", str(tmp_path))
    assert result.exit_code == 1
    assert "eia init" in result.output
    assert list(tmp_path.iterdir()) == []


def test_F3_01_chat_without_platform_fails_and_creates_nothing(vault: Path, run) -> None:
    result = run("new", "chat", "--title", "x", "--vault", str(vault))
    assert result.exit_code == 1
    assert list(vault.rglob("*.md")) == []


@pytest.mark.parametrize(
    "title",
    [
        "关于游戏设计的一些想法",
        'a<b>c:d"e/f\\g|h?i*j',
        "结尾有点和空格. . ",
        "CON",
        "超长标题" * 40,
        "emoji 🎮 和 tab\t换行\n混在一起",
        "???",
    ],
)
def test_F3_02_awkward_titles_give_windows_safe_filenames(vault: Path, title: str) -> None:
    path = new_entry(vault, "note", title, now=NOW)

    assert path.is_file()
    name = path.name
    assert not (set(name) & FORBIDDEN)
    assert not any(ord(ch) < 32 for ch in name)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-.+-[a-z2-7]{6}\.md", name)
    assert len(slugify(title).encode("utf-8")) <= MAX_SLUG_BYTES
    assert not name.removesuffix(".md").endswith((".", " "))
    assert len(name.encode("utf-8")) < 120
    assert read_document(path).meta["title"] == title
    assert validate_vault(vault).issues == []


def test_F3_02_slug_is_nfc_normalised() -> None:
    decomposed = "café"
    assert slugify(decomposed) == "café"


def test_F3_03_same_title_same_day_gives_two_files(vault: Path) -> None:
    first = new_entry(vault, "note", "同一个标题", now=NOW)
    second = new_entry(vault, "note", "同一个标题", now=NOW)
    assert first != second
    assert first.is_file() and second.is_file()
    assert validate_vault(vault).issues == []


def test_F3_04_filename_date_follows_configured_timezone(vault: Path) -> None:
    (vault / "config.yaml").write_text("schema: 1\ntimezone: Asia/Singapore\n", encoding="utf-8")
    path = new_entry(vault, "note", "深夜笔记", now=NOW)  # 18:30 UTC = 次日 02:30 新加坡
    meta = read_document(path).meta
    assert path.name.startswith("2026-11-16-")
    assert meta["id"].startswith("note-20261115-")
    assert meta["created"] == NOW


def test_F3_04_unknown_timezone_is_a_clear_error(vault: Path, run) -> None:
    (vault / "config.yaml").write_text("timezone: Mars/Olympus\n", encoding="utf-8")
    result = run("new", "note", "--title", "x", "--vault", str(vault))
    assert result.exit_code == 1
    assert "timezone" in result.output


def test_F3_05_chat_id_is_deterministic_per_conversation() -> None:
    a = chat_id("claude", "conv-1", NOW)
    assert a == chat_id("claude", "conv-1", NOW)
    assert a != chat_id("chatgpt", "conv-1", NOW)
    assert a != chat_id("claude", "conv-2", NOW)
    assert re.fullmatch(r"chat-20261115-[a-z2-7]{12}", a)


def test_F3_05_creating_the_same_conversation_twice_is_refused(vault: Path) -> None:
    from eia.schema.vault import VaultError

    new_entry(vault, "chat", "对话", platform="claude", conversation_id="c1", now=NOW)
    with pytest.raises(VaultError):
        new_entry(vault, "chat", "对话", platform="claude", conversation_id="c1", now=NOW)
    assert len(list(vault.rglob("*.md"))) == 1
