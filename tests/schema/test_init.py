from __future__ import annotations

from pathlib import Path

EXPECTED_DIRS = [
    "inbox/headlines",
    "inbox/digest",
    "inbox/clips",
    "chats/chatgpt",
    "chats/claude",
    "chats/gemini",
    "notes",
    "topics",
]


def test_F1_01_init_creates_dirs_config_and_gitignore(tmp_path: Path, run) -> None:
    root = tmp_path / "v"
    result = run("init", str(root))
    assert result.exit_code == 0, result.output
    for rel in EXPECTED_DIRS:
        assert (root / rel).is_dir(), rel
    assert (root / "config.yaml").is_file()
    assert (root / ".gitignore").is_file()


def test_F1_01_fresh_vault_passes_validate(tmp_path: Path, run) -> None:
    root = tmp_path / "v"
    run("init", str(root))
    result = run("validate", str(root))
    assert result.exit_code == 0, result.output


def test_F1_02_gitignore_contains_env_and_tool_dir(tmp_path: Path, run) -> None:
    root = tmp_path / "v"
    run("init", str(root))
    lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lines
    assert ".eia/" in lines


def test_F1_03_init_never_overwrites_or_deletes_existing_files(tmp_path: Path, run) -> None:
    root = tmp_path / "v"
    (root / "notes").mkdir(parents=True)
    (root / "config.yaml").write_text("timezone: Asia/Singapore\n", encoding="utf-8")
    (root / ".gitignore").write_text("mine\n", encoding="utf-8")
    (root / "notes" / "old.md").write_text("我的旧笔记", encoding="utf-8")

    result = run("init", str(root))

    assert result.exit_code == 0, result.output
    assert (root / "config.yaml").read_text(encoding="utf-8") == "timezone: Asia/Singapore\n"
    assert (root / ".gitignore").read_text(encoding="utf-8") == "mine\n"
    assert (root / "notes" / "old.md").read_text(encoding="utf-8") == "我的旧笔记"
    assert "已存在，跳过  config.yaml" in result.output
    assert (root / "topics").is_dir()


def test_F1_03_init_twice_changes_nothing(tmp_path: Path, run) -> None:
    root = tmp_path / "v"
    run("init", str(root))
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = run("init", str(root))
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert result.exit_code == 0
    assert before == after
