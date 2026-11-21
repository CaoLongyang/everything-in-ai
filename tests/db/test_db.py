"""F14：vault 的 SQLite 数据库。"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from eia.cli import main
from eia.db import DB_PATH, DatabaseError, database, schema
from eia.db import vectors as vec
from eia.db.index import search, sync
from eia.headlines.store import load_items, load_vectors
from eia.interests.feedback import rate, read_feedback
from eia.schedule.runs import read_runs
from eia.schema.io import render_document
from eia.schema.vault import init_vault

ROOT = Path(__file__).resolve().parents[2]
NOW = dt.datetime(2026, 11, 22, 1, 0, tzinfo=dt.UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    init_vault(root)
    return root


def _cli(*args: str) -> Result:
    return CliRunner().invoke(main, list(args))


def _note(root: Path, rel: str, title: str, body: str, **meta: object) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: dict[str, object] = {
        "schema": 1,
        "id": "note-20261122-abcdef",
        "type": "note",
        "title": title,
        "created": NOW,
        "status": "inbox",
        "tags": ["测试"],
        **meta,
    }
    path.write_text(render_document(fields, body), encoding="utf-8")
    return path


def _legacy(root: Path) -> None:
    eia = root / ".eia"
    (eia / "headlines").mkdir(parents=True)
    (eia / "interests").mkdir(parents=True)
    (eia / "runs").mkdir(parents=True)
    items = [
        {
            "url": f"https://n/{i}?utm_source=x",
            "title": f"T{i}",
            "summary": "",
            "publisher": "P",
            "published": "2026-11-21T10:00:00+00:00",
            "feed_url": "https://f",
        }
        for i in range(3)
    ]
    (eia / "headlines" / "items.jsonl").write_text(
        "".join(json.dumps(i) + "\n" for i in items) + "{broken\n", encoding="utf-8"
    )
    (eia / "headlines" / "vectors.json").write_text(
        json.dumps({"model": "m", "dim": 2, "vectors": {"https://n/0": [0.5, 0.25]}}),
        encoding="utf-8",
    )
    (eia / "interests" / "vectors.json").write_text(
        json.dumps({"model": "m", "vectors": {"k1": [1.0], "k2": [2.0]}}), encoding="utf-8"
    )
    (eia / "interests" / "shown.json").write_text(
        json.dumps({"https://n/1": "2026-11-21"}), encoding="utf-8"
    )
    run = {
        "job": "headlines",
        "started": "2026-11-21T00:00:00Z",
        "seconds": 1.5,
        "ok": True,
        "summary": "新建 1",
        "error": None,
    }
    (eia / "runs" / "headlines.jsonl").write_text(json.dumps(run) + "\n", encoding="utf-8")
    (root / "interests").mkdir(exist_ok=True)
    feedback = {
        "url": "https://n/2",
        "title": "T2",
        "publisher": "P",
        "interest": "x",
        "relevant": False,
        "stars": 2,
        "why": "不看",
        "rated": "2026-11-21T12:00:00Z",
    }
    (root / "interests" / "feedback.jsonl").write_text(
        json.dumps(feedback, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ---------- F14-01 迁移 ----------


def test_F14_01_old_files_are_imported_once_and_moved(vault: Path) -> None:
    _legacy(vault)
    assert [i.title for i in load_items(vault)] == ["T0", "T1", "T2"]
    assert load_vectors(vault, "m") == {"https://n/0": [0.5, 0.25]}
    with database(vault) as conn:
        assert vec.get(conn, "m", "text") == {"k1": [1.0], "k2": [2.0]}
        assert conn.execute("SELECT key, day FROM shown").fetchall()[0][1] == "2026-11-21"
        report = json.loads(
            conn.execute("SELECT value FROM meta WHERE key = 'legacy_import'").fetchone()[0]
        )
    assert {k: report[k] for k in ("items", "vectors", "runs", "shown", "feedback")} == {
        "items": 3,
        "vectors": 3,
        "runs": 1,
        "shown": 1,
        "feedback": 1,
    }
    assert [r.summary for r in read_runs(vault, "headlines")] == ["新建 1"]
    [fb] = read_feedback(vault)
    assert fb.relevant is False and fb.stars == 2 and fb.why == "不看"
    assert not (vault / ".eia" / "headlines" / "items.jsonl").exists()
    assert (vault / ".eia" / "old" / "headlines" / "items.jsonl").is_file()
    assert (vault / ".eia" / "old" / "runs" / "headlines.jsonl").is_file()
    assert (vault / "interests" / "feedback.jsonl").is_file()  # 评价导出文件留在原处
    # 再打开不会重复导入
    (vault / ".eia" / "headlines").mkdir(exist_ok=True)
    (vault / ".eia" / "headlines" / "items.jsonl").write_text(
        json.dumps(
            {
                "url": "https://late",
                "title": "L",
                "publisher": "P",
                "published": "2026-11-21T10:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert len(load_items(vault)) == 3


def test_F14_01_preview_reads_old_files_without_moving_them(vault: Path) -> None:
    _legacy(vault)
    assert len(load_items(vault, readonly=True)) == 3
    assert not (vault / DB_PATH).exists()
    assert (vault / ".eia" / "headlines" / "items.jsonl").is_file()


# ---------- F14-02 索引 ----------


def test_F14_02_incremental_index(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = _note(vault, "notes/a.md", "甲", "第一篇")
    _note(vault, "notes/b.md", "乙", "第二篇", tags=["x", "y"])
    (vault / "notes" / "broken.md").write_text("没有 frontmatter", encoding="utf-8")
    with database(vault) as conn:
        first = sync(conn, vault)
        assert (first.added, first.changed, first.removed) == (3, 0, 0)
        rows = {r["path"]: r for r in conn.execute("SELECT * FROM entries")}
        assert rows["notes/a.md"]["title"] == "甲" and rows["notes/a.md"]["type"] == "note"
        assert rows["notes/broken.md"]["error"]
        tags = conn.execute(
            "SELECT tag FROM entry_tags WHERE path = 'notes/b.md' ORDER BY tag"
        ).fetchall()
        assert [t[0] for t in tags] == ["x", "y"]

        reads: list[Path] = []
        import eia.db.index as index_mod

        original = index_mod.read_document
        monkeypatch.setattr(index_mod, "read_document", lambda p: reads.append(p) or original(p))
        assert sync(conn, vault).unchanged == 3 and reads == []  # 没变的不重新读

        _note(vault, "notes/a.md", "甲改过", "第一篇改过的正文")
        (vault / "notes" / "b.md").rename(vault / "notes" / "c.md")
        result = sync(conn, vault)
        assert (result.added, result.changed, result.removed) == (1, 1, 1)
        paths = {r[0] for r in conn.execute("SELECT path FROM entries")}
        assert paths == {"notes/a.md", "notes/c.md", "notes/broken.md"}
        assert (
            conn.execute("SELECT title FROM entries WHERE path = 'notes/a.md'").fetchone()[0]
            == "甲改过"
        )
        a.unlink()
        assert sync(conn, vault).removed == 1
        assert not conn.execute("SELECT 1 FROM entries_fts WHERE path = 'notes/a.md'").fetchone()


# ---------- F14-03 评价导出与恢复 ----------


def test_F14_03_feedback_is_exported_and_restored(vault: Path) -> None:
    rate(vault, "https://a/1", title="A", relevant=True, now=NOW)
    rate(vault, "https://a/2", title="B", stars=1, why="不想看", now=NOW)
    export = (vault / "interests" / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["title"] for line in export] == ["A", "B"]
    (vault / DB_PATH).unlink()
    for extra in (vault / DB_PATH).parent.glob("eia.db-*"):
        extra.unlink()
    result = _cli("db", "rebuild", "--vault", str(vault))
    assert "加上 --yes" in result.output
    result = _cli("db", "rebuild", "--vault", str(vault), "--yes")
    assert result.exit_code == 0, result.output
    records = {r.title: r for r in read_feedback(vault)}
    assert (
        records["A"].relevant is True and records["B"].stars == 1 and records["B"].why == "不想看"
    )


# ---------- F14-04 向量 ----------


def test_F14_04_vectors_are_binary_and_per_model(vault: Path) -> None:
    values = [0.1, -2.5, 3.14159, 1e-3]
    with database(vault) as conn:
        vec.put(conn, "m1", "text", {"k": values})
        stored = conn.execute("SELECT dim, data FROM vectors").fetchone()
        assert (
            stored["dim"] == 4 and isinstance(stored["data"], bytes) and len(stored["data"]) == 16
        )
        assert vec.get(conn, "m1", "text", ["k"])["k"] == pytest.approx(values, rel=1e-6)
        assert vec.get(conn, "m2", "text", ["k"]) == {}
        many = {f"k{i}": [float(i)] for i in range(1200)}  # 超过单条语句的参数上限
        vec.put(conn, "m1", "big", many)
        assert len(vec.get(conn, "m1", "big", list(many))) == 1200
        with pytest.raises(ValueError, match="不同维度"):
            vec.put(conn, "m1", "text", {"a": [1.0], "b": [1.0, 2.0]})


# ---------- F14-05 并发 ----------

_WRITER = """
import sys, datetime as dt
from pathlib import Path
from eia.interests.feedback import rate
from eia.schedule.runs import _append, RunRecord
root, tag = Path(sys.argv[1]), sys.argv[2]
for i in range(40):
    rate(root, f"https://{tag}/{i}", title=f"{tag}{i}", relevant=True)
    _append(root, RunRecord("headlines", "2026-11-22T00:00:00Z", 0.1, True, f"{tag}{i}"))
"""


def test_F14_05_two_processes_write_at_the_same_time(vault: Path) -> None:
    with database(vault):
        pass  # 先建好库，两个进程同时打开
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _WRITER, str(vault), tag], cwd=ROOT, stderr=subprocess.PIPE
        )
        for tag in ("a", "b")
    ]
    for proc in procs:
        _, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err.decode("utf-8", "replace")
    assert len(read_feedback(vault)) == 80
    assert len(read_runs(vault, "headlines")) == 80
    lines = (vault / "interests" / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 80


# ---------- F14-06 表结构版本 ----------


def test_F14_06_older_schema_is_migrated(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with database(vault):
        pass
    monkeypatch.setattr(schema, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(schema, "MIGRATIONS", {2: "ALTER TABLE runs ADD COLUMN note TEXT;"})
    with database(vault) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(runs)")]
        version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
    assert "note" in columns and version == "2"


def test_F14_06_newer_schema_is_refused_untouched(vault: Path) -> None:
    with database(vault) as conn:
        conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    before = (vault / DB_PATH).read_bytes()
    with pytest.raises(DatabaseError, match="新版本 eia 建的"):
        with database(vault):
            pass
    assert (vault / DB_PATH).read_bytes() == before
    result = _cli("db", "status", "--vault", str(vault))
    assert result.exit_code == 1 and "请升级 eia" in result.output


def test_F14_06_readonly_cannot_upgrade(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with database(vault):
        pass
    monkeypatch.setattr(schema, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(schema, "MIGRATIONS", {2: "SELECT 1;"})
    with pytest.raises(DatabaseError, match="去掉 --dry-run"):
        with database(vault, readonly=True):
            pass


# ---------- F14-07 现有功能不再写散文件 ----------


def test_F14_07_features_write_only_the_database(vault: Path) -> None:
    from eia.schedule.runs import run_job

    rate(vault, "https://a/1", title="A", relevant=True, now=NOW)
    run_job(vault, "headlines")  # 没有 headlines 配置：记一条失败的运行记录
    leftovers = sorted(
        p.relative_to(vault).as_posix() for p in (vault / ".eia").rglob("*") if p.is_file()
    )
    assert all(p.startswith(".eia/eia.db") or p.startswith(".eia/locks") for p in leftovers), (
        leftovers
    )
    assert read_runs(vault, "headlines")[0].ok is False


# ---------- F14-08 全文搜索 ----------


def test_F14_08_search(vault: Path) -> None:
    _note(vault, "notes/car.md", "买车笔记", "马来西亚汽车保险怎么买比较划算")
    _note(vault, "notes/rust.md", "Learning Rust", "The borrow checker explained", type="note")
    _note(
        vault,
        "chats/claude/c.md",
        "对话",
        "我们聊了汽车",
        type="chat",
        platform="claude",
        conversation_id="c1",
        id="chat-20261122-abcdefabcdef",
    )
    with database(vault) as conn:
        sync(conn, vault)
        long_hits = search(conn, "汽车保险")
        assert [h.path for h in long_hits] == ["notes/car.md"]
        assert "【汽车保险】" in long_hits[0].snippet
        assert [h.path for h in search(conn, "borrow checker")] == ["notes/rust.md"]
        short = {h.path for h in search(conn, "汽车")}  # 2 个字：逐条比对
        assert short == {"notes/car.md", "chats/claude/c.md"}
        assert [h.path for h in search(conn, "汽车", entry_type="chat")] == ["chats/claude/c.md"]
        assert "【汽车】" in search(conn, "汽车", entry_type="chat")[0].snippet
        assert search(conn, "   ") == []
        assert search(conn, '"引号"') == []

    result = _cli("search", "汽车保险", "--vault", str(vault))
    assert result.exit_code == 0 and "notes/car.md" in result.output
    assert "没有找到" in _cli("search", "完全没有的内容", "--vault", str(vault)).output


def test_F14_08_without_fts(vault: Path) -> None:
    with database(vault) as conn:
        conn.execute("UPDATE meta SET value = '0' WHERE key = 'fts'")
        with pytest.raises(RuntimeError, match="不支持全文搜索"):
            search(conn, "汽车保险")
    assert "不支持全文搜索" in _cli("search", "汽车保险", "--vault", str(vault)).output


def test_F14_08_status_command(vault: Path) -> None:
    _note(vault, "notes/a.md", "甲", "正文")
    result = _cli("db", "status", "--vault", str(vault))
    assert result.exit_code == 0, result.output
    assert "全文搜索可用" in result.output and "条目索引" in result.output
    assert "新增 1" in result.output
    assert _cli("db", "status", "--vault", str(vault / "nope")).exit_code == 1
    with closing(sqlite3.connect(vault / DB_PATH)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
