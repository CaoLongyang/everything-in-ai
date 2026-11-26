"""F18-14 至 F18-19：访谈和补充、少说空话、时间感、想做的清单。

规格见 docs/specs/profile.delta-v2b.md。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from click.testing import CliRunner

from eia.cli import main
from eia.profile import run as run_mod
from eia.profile import supplement as supplement_mod
from eia.profile.document import read_rejected
from eia.profile.material import ANSWER_MARK, SUPPLEMENT
from eia.schema.io import read_document

from .conftest import NOW, OWN_JAVA, TODAY, FakeModel
from .test_profile import _run, sub

# ---------- F18-14 访谈 ----------


def test_F18_14_ask_needs_a_profile_and_yes(vault: Path, model: FakeModel) -> None:
    with pytest.raises(supplement_mod.ProfileAskError, match="还没有画像"):
        supplement_mod.plan_ask(vault)
    _run(vault)
    model.calls.clear()
    result = CliRunner().invoke(main, ["profile", "ask", "--vault", str(vault)])
    assert result.exit_code == 0, result.output
    assert "什么都没有发出" in result.output and model.ask_prompts == []


def test_F18_14_ask_sends_conclusions_only_and_writes_a_new_file(
    vault: Path, model: FakeModel
) -> None:
    _run(vault)
    plan = supplement_mod.plan_ask(vault)
    first, count = supplement_mod.run_ask(vault, plan, now=NOW)
    [prompt] = model.ask_prompts
    assert "跨话题的同一种劲头" in prompt  # 结论发了
    assert OWN_JAVA[:10] not in prompt and "[^" not in prompt and "「" not in prompt  # 原话没发
    assert count == 2 and first.name == "访谈 2026-11-26.md"
    text = first.read_text(encoding="utf-8")
    assert read_document(first).meta["type"] == "note"
    assert "### 1. 你是怎么开始信基督教的？" in text and "（为什么问：" in text
    assert text.count(ANSWER_MARK) == 2

    before = first.read_bytes()
    second, _ = supplement_mod.run_ask(vault, plan, now=NOW)
    assert second.name == "访谈 2026-11-26 2.md" and first.read_bytes() == before


# ---------- F18-15 回答和补充当作材料 ----------


def test_F18_15_answers_and_supplement_are_sent_as_my_words(vault: Path, model: FakeModel) -> None:
    _run(vault)
    supplement = vault / "profile" / SUPPLEMENT
    assert supplement.is_file()  # 第一次生成画像时建好
    plan = supplement_mod.plan_ask(vault)
    interview, _ = supplement_mod.run_ask(vault, plan, now=NOW)
    text = interview.read_text(encoding="utf-8")
    text = text.replace(
        ANSWER_MARK, f"{ANSWER_MARK}高中时朋友带我去了教会，\n> 后来自己读圣经。", 1
    )  # 只回答第一题
    interview.write_text(text, encoding="utf-8")
    supplement.write_text(
        supplement.read_text(encoding="utf-8") + "\n2026-11 已经找到室友了，和朋友合租。\n",
        encoding="utf-8",
    )
    supplement_before = supplement.read_bytes()

    model.calls.clear()
    plan = run_mod.plan_profile(vault, today=TODAY)
    titles = [m.chat.title for m in plan.to_send]
    assert titles == ["你是怎么开始信基督教的？", "我的补充"]  # 没回答的题不发
    run_mod.run_profile(vault, plan, now=NOW + dt.timedelta(hours=1))
    sent = "\n".join(model.extract_prompts)
    assert "[我主动补充的 · " in sent and "高中时朋友带我去了教会，\n后来自己读圣经。" in sent
    assert "[我主动补充的 · 2026-11-01] 2026-11 已经找到室友了" in sent  # 段首写的日期
    assert "程序只读" not in sent and "为什么选择来马来西亚读书" not in sent  # 提示和空题不发
    assert supplement.read_bytes() == supplement_before  # 之后不改它
    assert "（说过·主动补充）" in "".join(model.synth_prompts)  # 汇总时也标明是主动补充的


# ---------- F18-16 「没意识到的你」至少 2 个对话 ----------


def test_F18_16_hidden_needs_two_conversations_others_do_not(vault: Path) -> None:
    result = _run(vault)
    assert [p.text for p in sub(result, "hidden").points] == ["跨话题的同一种劲头"]
    assert "只有一个对话的猜测" in result.profile.open_questions
    about = [p.text for p in sub(result, "about").points]
    assert any(t.startswith("关于") for t in about)  # 关于我里一个对话的要点照常保留


# ---------- F18-17 状态带「截至」 ----------


def test_F18_17_states_carry_an_as_of_date(vault: Path) -> None:
    result = _run(vault)
    text = result.path.read_text(encoding="utf-8")
    assert "- [ ] 你在找室友（截至 2026-11）[^" in text
    assert not any("截至" in line for line in text.splitlines() if line.startswith("- [ ] 关于"))


# ---------- F18-18 「过时」 ----------


def test_F18_18_outdated_is_not_the_same_as_wrong(vault: Path, model: FakeModel) -> None:
    path = _run(vault).path
    text = path.read_text(encoding="utf-8")
    line = next(x for x in text.splitlines() if x.startswith("- [ ] 你在找室友"))
    path.write_text(
        text.replace(line, line.replace("- [ ]", "- [x]") + " 过时：11 月已经找到了"),
        encoding="utf-8",
    )
    [rejected] = read_rejected(path)
    assert rejected.outdated and rejected.why == "11 月已经找到了"
    assert rejected.text == "你在找室友（截至 2026-11）"

    model.calls.clear()
    second = _run(vault, now=NOW + dt.timedelta(hours=1)).path
    prompt = model.synth_prompts[0]
    assert "已经过时的状态" in prompt and "（现在：11 月已经找到了）" in prompt
    assert "不像TA的结论" not in prompt
    carried = second.read_text(encoding="utf-8")
    assert "## 你说过已经变了的" in carried and "## 你说过不像你的" not in carried
    assert [r.outdated for r in read_rejected(second)] == [True]  # 再下一版仍然记得


# ---------- F18-19 想做的清单 ----------


def test_F18_19_wishes_replace_tensions(vault: Path) -> None:
    result = _run(vault)
    text = result.path.read_text(encoding="utf-8")
    assert "- [ ] **卡牌游戏** · 在做 · 首次 10-05 · 最近 11-10[^" in text
    assert "- [ ] **记账工具** · 在想 · " in text  # 不认识的状态写「在想」
    assert "张力" not in text and "张力不再有这一节" not in text
    assert "## 04 想做的清单" in text and "## 07 给 AI 的说明" in text
