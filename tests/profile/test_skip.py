"""F18-13：某一批的回复几次都不合格式，跳过那批里的对话，其他照常（docs/specs/profile.delta-skip.md）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from eia.cli import main
from eia.db import database
from eia.llm.errors import LLMCallError
from eia.profile import extract as extract_mod
from eia.profile import run as run_mod

from .conftest import OWN_FAITH, TODAY, FakeModel
from .test_profile import _run


def _cached_titles(vault: Path) -> int:
    with database(vault) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM profile_observations").fetchone()[0])


def test_F18_13_a_garbled_batch_is_skipped_and_retried_next_time(
    vault: Path, model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    batches = extract_mod._batches  # 一块一批，只坏「信仰与理性」那批
    monkeypatch.setattr(extract_mod, "_batches", lambda ms: [[u] for b in batches(ms) for u in b])
    model.garble = "信仰与理性"
    result = _run(vault)
    assert result.skipped == ["信仰与理性"]
    assert _cached_titles(vault) == 2  # 坏掉的那个不进缓存
    text = result.path.read_text(encoding="utf-8")
    assert "另有 1 个对话这次没分析成" in text and OWN_FAITH[:10] not in text
    assert "chats: 2" in text  # 画像只依据真正分析了的对话

    model.garble = ""
    model.calls.clear()
    plan = run_mod.plan_profile(vault, today=TODAY)
    assert [m.chat.title for m in plan.to_send] == ["信仰与理性"]  # 下次只重试它
    again = run_mod.run_profile(vault, plan)
    assert again.skipped == [] and "没分析成" not in again.path.read_text(encoding="utf-8")


def test_F18_13_cli_lists_the_skipped(vault: Path, model: FakeModel) -> None:
    model.garble = "信仰与理性"  # 所有对话在同一批：整批跳过
    result = CliRunner().invoke(
        main, ["profile", "run", "--vault", str(vault), "--until", TODAY.isoformat(), "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert "有 3 个对话这次没分析成" in result.output and "下次运行会重试" in result.output


def test_F18_13_network_errors_still_stop(vault: Path, model: FakeModel) -> None:
    model.network_down = True
    with pytest.raises(LLMCallError):
        _run(vault)
    assert _cached_titles(vault) == 0
