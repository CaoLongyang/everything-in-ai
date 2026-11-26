"""F18：个人画像。全部用假的文本模型，不连网。"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from eia.cli import main
from eia.db import database
from eia.profile import document as document_mod
from eia.profile import extract as extract_mod
from eia.profile import run as run_mod
from eia.profile.document import ChatLink, read_rejected
from eia.profile.material import AI_SNIPPET_CHARS
from eia.profile.synth import Subsection
from eia.schema.io import read_document
from eia.schema.validate import validate_vault

from .conftest import (
    AI_ONLY,
    ECHO,
    FAKE_QUOTE,
    NOW,
    OLD,
    OWN_ECHO_COMMENT,
    OWN_FAITH,
    OWN_HOME,
    OWN_JAVA,
    SECRET_CODE,
    THINKING,
    TODAY,
    FakeModel,
    append,
    chat_file,
)


def _cli(vault: Path, *args: str) -> Result:
    return CliRunner().invoke(
        main, ["profile", "run", "--vault", str(vault), "--until", TODAY.isoformat(), *args]
    )


def _run(vault: Path, now: dt.datetime = NOW) -> run_mod.Result:
    plan = run_mod.plan_profile(vault, today=TODAY)
    return run_mod.run_profile(vault, plan, now=now)


def _profiles(vault: Path) -> list[Path]:
    return sorted((vault / "profile").glob("*.md"))


# ---------- F18-01 不确认就不发；说要发多少就发多少 ----------


def test_F18_01_nothing_is_sent_without_yes(vault: Path, model: FakeModel) -> None:
    result = _cli(vault)
    assert result.exit_code == 0, result.output
    assert "范围 2026-08-26 ~ 2026-11-26：3 个对话" in result.output
    assert "什么都没有发出" in result.output and "deepseek-v4-flash" in result.output
    assert model.calls == [] and _profiles(vault) == []


def test_F18_01_announced_numbers_match_what_is_sent(vault: Path, model: FakeModel) -> None:
    plan = run_mod.plan_profile(vault, today=TODAY)
    output = _cli(vault).output
    assert f"第 2 步要把 3 个对话、{plan.send_chars} 字发给" in output
    run_mod.run_profile(vault, plan, now=NOW)
    sent = "\n".join(model.extract_prompts)
    assert len(set(re.findall(r"^=== C\d+：(.+?)（", sent, flags=re.MULTILINE))) == 3
    for material in plan.to_send:
        for snippet in material.snippets:
            assert snippet.text in sent
    assert plan.send_chars == sum(len(s.text) for m in plan.to_send for s in m.snippets)


def test_F18_01_default_range_is_three_months() -> None:
    assert run_mod.default_since(dt.date(2026, 11, 26)) == dt.date(2026, 8, 26)
    assert run_mod.default_since(dt.date(2026, 5, 31)) == dt.date(2026, 2, 28)
    assert run_mod.default_since(dt.date(2026, 2, 15)) == dt.date(2025, 11, 15)


def test_F18_01_bad_ranges(vault: Path, model: FakeModel) -> None:
    result = _cli(vault, "--since", "2026-12-01")
    assert result.exit_code == 1 and "晚于结束日期" in result.output
    result = CliRunner().invoke(
        main,
        ["profile", "run", "--vault", str(vault), "--since", "2020-01-01", "--until", "2020-02-01"],
    )
    assert result.exit_code == 1 and "没有你说过的话" in result.output
    assert model.calls == []


# ---------- F18-02 只发该发的 ----------


def test_F18_02_only_my_words_in_range_are_sent(vault: Path, model: FakeModel) -> None:
    _run(vault)
    sent = "\n".join(model.extract_prompts)
    for text in (OWN_JAVA, OWN_FAITH, OWN_HOME, OWN_ECHO_COMMENT, ECHO[:AI_SNIPPET_CHARS]):
        assert text in sent
    assert ECHO[: AI_SNIPPET_CHARS + 1] not in sent  # 贴回的 AI 回答截到 500 字
    for text in (AI_ONLY, THINKING, SECRET_CODE, "Traceback", OLD):
        assert text not in sent
    assert (
        "[我贴回的 AI 回答 · 2026-11-01]" in sent
        and "=== C1：数据结构复习（chatgpt · 2026-11-01）" in sent
    )


def test_F18_02_notes_are_sent(vault: Path, model: FakeModel) -> None:
    append(_home(vault), "\n我自己的笔记：想以后去新加坡工作\n")
    _run(vault)
    assert "[我的笔记 · " in "\n".join(model.extract_prompts)
    assert "想以后去新加坡工作" in "\n".join(model.extract_prompts)


def _home(vault: Path) -> Path:
    return next(p for p in (vault / "chats" / "gemini").glob("*.md") if "生活" in p.name)


def test_F18_02_long_conversations_are_split(
    vault: Path, model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract_mod, "BATCH_CHARS", 30)
    plan = run_mod.plan_profile(vault, today=TODAY)
    run_mod.run_profile(vault, plan, now=NOW)
    assert len(model.extract_prompts) > 3
    assert all(
        sum(len(line) for line in p.splitlines() if line.startswith("[")) < 30 * 3
        for p in model.extract_prompts
    )
    sent = "".join(model.extract_prompts)
    assert OWN_FAITH[:20] in sent


# ---------- F18-03 原话对不上就丢掉 ----------


def test_F18_03_made_up_quotes_are_dropped(vault: Path, model: FakeModel) -> None:
    result = _run(vault)
    # 每批：每个对话一条编的原话 + 一条不存在的对话 C99；贴回 AI 回答而没有自己的话的一条
    assert result.dropped == 3 + 1 + 1
    text = result.path.read_text(encoding="utf-8")
    assert FAKE_QUOTE not in text and "编的" not in text
    assert f"「{OWN_JAVA[:20]}」" in text and f"「{OWN_FAITH[:20]}」" in text


def test_F18_03_quotes_survive_spacing_changes(vault: Path) -> None:
    plan = run_mod.plan_profile(vault, today=TODAY)
    material = next(m for m in plan.materials if m.chat.title == "数据结构复习")
    observed = extract_mod._Observed(
        chat="C1", category="cognition", kind="stated", text="t", quotes=["我在学  Java，下周就考"]
    )
    checked = extract_mod._check(observed, material)
    assert checked is not None and checked.quotes[0].day == "2026-11-01"


# ---------- F18-04 证据由程序核对和计数 ----------


def sub(result: run_mod.Result, key: str) -> Subsection:
    return next(s for c in result.profile.chapters for s in c.subsections if s.key == key)


def test_F18_04_evidence_is_checked_and_counted(vault: Path) -> None:
    result = _run(vault)
    text = result.path.read_text(encoding="utf-8")
    body, questions = text.split("## 未解问题")
    assert "没有证据的结论" not in body  # 只引用了不存在的 O999
    assert "- 没有证据的结论" in questions and "- 一个问题" in questions
    [tension] = sub(result, "tensions").points
    assert (tension.chats, tension.days) == (3, 3)
    assert "怕麻烦人 ↔ 怕失望：一句话（把握：高｜3 个对话、3 天）" in text


# ---------- F18-05 画像文件 ----------


def test_F18_05_profile_file_is_valid_and_linked(vault: Path) -> None:
    result = _run(vault)
    assert result.path == vault / "profile" / "2026-11-26 画像.md"
    meta = read_document(result.path).meta
    assert meta["type"] == "profile" and meta["chats"] == 3 and meta["model"] == "deepseek-v4-flash"
    assert meta["period"] == "2026-08-26 ~ 2026-11-26"
    report = validate_vault(vault)
    assert report.errors == [], report.errors
    stem = chat_file(vault, "claude").stem
    assert f"[[{stem}|信仰与理性]]" in result.path.read_text(encoding="utf-8")
    evidence = sub(result, "tensions").points[0].evidence[0]
    long = ChatLink("长" * 40, "x")
    assert f"[[x|{'长' * 30}…]]" in document_mod._evidence(evidence, {evidence.chat_id: long})
    assert "包含你的个人信息" in result.path.read_text(encoding="utf-8")


def test_F18_05_existing_profiles_are_not_overwritten(vault: Path) -> None:
    first = _run(vault).path
    first.write_bytes(first.read_bytes() + "\n我在画像里加的笔记\n".encode())
    before = first.read_bytes()
    second = _run(vault, now=NOW + dt.timedelta(hours=1)).path
    assert second.name == "2026-11-26 画像 2.md" and first.read_bytes() == before


# ---------- F18-06 没变的对话不再发 ----------


def test_F18_06_unchanged_conversations_are_not_resent(vault: Path, model: FakeModel) -> None:
    _run(vault)
    model.calls.clear()
    plan = run_mod.plan_profile(vault, today=TODAY)
    assert plan.to_send == [] and plan.send_chars == 0
    assert "其中 3 个对话以前分析过" in _cli(vault).output
    run_mod.run_profile(vault, plan, now=NOW + dt.timedelta(hours=1))
    assert model.extract_prompts == [] and model.synth_prompts  # 观察从缓存来，照样汇总

    append(chat_file(vault, "claude"), "\n新写的笔记：信仰让我安心\n")
    plan = run_mod.plan_profile(vault, today=TODAY)
    assert [m.chat.title for m in plan.to_send] == ["信仰与理性"]


# ---------- F18-07 「不像我」 ----------


def test_F18_07_rejections_reach_the_next_run(vault: Path, model: FakeModel) -> None:
    path = _run(vault).path
    text = path.read_text(encoding="utf-8")
    line = next(x for x in text.splitlines() if x.startswith("- [ ] 不像我｜"))
    conclusion = line.removeprefix("- [ ] 不像我｜").split("（把握：")[0]
    path.write_text(
        text.replace(line, line.replace("- [ ]", "- [x]") + " 原因：我不是这样的"), encoding="utf-8"
    )
    assert [(r.text, r.why) for r in read_rejected(path)] == [(conclusion, "我不是这样的")]

    model.calls.clear()
    second = _run(vault, now=NOW + dt.timedelta(hours=1))
    assert second.rejected == 1
    assert all(f"- {conclusion}（原因：我不是这样的）" in p for p in model.synth_prompts)
    carried = second.path.read_text(encoding="utf-8")
    assert "## 你说过不像你的" in carried and f"- {conclusion}——原因：我不是这样的" in carried

    model.calls.clear()  # 第三次：没再勾新的，第二版带过来的那条仍然有效
    _run(vault, now=NOW + dt.timedelta(hours=2))
    assert all(conclusion in p for p in model.synth_prompts)


# ---------- F18-08 出错时不写半成品；不碰其他文件 ----------


def _hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.relative_to(root).as_posix().startswith((".eia/", "profile/"))
    }


def test_F18_08_failure_keeps_observations_and_writes_nothing(
    vault: Path, model: FakeModel
) -> None:
    model.fail_synth = True
    result = _cli(vault, "--yes")
    assert result.exit_code == 1 and "假装网络断了" in result.output
    assert _profiles(vault) == []
    with database(vault) as conn:
        assert conn.execute("SELECT COUNT(*) FROM profile_observations").fetchone()[0] == 3

    model.fail_synth = False
    model.calls.clear()
    result = _cli(vault, "--yes")
    assert result.exit_code == 0, result.output
    assert model.extract_prompts == [] and len(_profiles(vault)) == 1
    assert "已写画像  profile/" in result.output


def test_F18_08_other_files_are_untouched(vault: Path) -> None:
    before = _hashes(vault)
    result = _cli(vault, "--yes")
    assert result.exit_code == 0, result.output
    assert "第 2 步：第 1/1 批" in result.output and "原话对不上、被丢掉的 5 条" in result.output
    assert _hashes(vault) == before
