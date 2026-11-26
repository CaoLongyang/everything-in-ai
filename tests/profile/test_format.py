"""F18-09 至 F18-12：画像按细分章节、用标签概括（docs/specs/profile.delta-format.md）。"""

from __future__ import annotations

from pathlib import Path

from eia.profile import document as document_mod
from eia.profile.document import read_rejected
from eia.profile.synth import Point

from .conftest import OWN_JAVA, FakeModel
from .test_profile import _run, sub

# ---------- F18-09 细分章节、标签 ----------


def test_F18_09_chapters_subsections_and_labels(vault: Path) -> None:
    text = _run(vault).path.read_text(encoding="utf-8")
    assert "\n# 你：追问到底 · 信仰压力测试 · a · b · c\n" in text  # 去重、最多 5 个
    order = [
        "## 00 核心档案",
        "### 关于我　`留学生`",
        "### 价值观",
        "### 沟通偏好",
        "## 01 心理",
        "### 动机模式",
        "### 你身上的张力　`两面`",
        "### 你可能没意识到的你",
        "## 02 学业与职业",
        "### 教育　`先懂再动手`",
        "## 03 兴趣",
        "### 信仰　`改革宗`",
        "## 04 决策记录",
        "## 05 时间线",
        "## 06 给 AI 的说明",
        "### 系统提示词",
        "### 分析与对话准则",
        "## 未解问题",
    ]
    positions = [text.index(heading) for heading in order]
    assert positions == sorted(positions)
    assert "### 价值观\n\n（材料里没有）" in text  # 唯一的要点没有证据
    assert "### 沟通偏好\n\n（材料里没有）" in text
    for dropped in ("不该写在这一次", "不认识的小节", "没有标题的兴趣"):
        assert dropped not in text
    assert "  > [!note]- 证据（4 条）\n  > - 「" in text
    assert "````text\n我是一个虚构的学生。\n````" in text and "- 先讲为什么" in text


def test_F18_09_evidence_shows_at_most_eight(vault: Path) -> None:
    point = sub(_run(vault), "tensions").points[0]
    many = Point(point.text, "中", point.evidence * 4)
    lines = document_mod._point(many, {}, dated=False)
    assert sum(1 for line in lines if line.startswith("  > - 「")) == 8
    assert lines[-1] == "  > - ……还有 8 条"


# ---------- F18-10 日期和证据由程序算 ----------


def test_F18_10_dated_chapters_are_sorted_by_evidence(vault: Path) -> None:
    result = _run(vault)
    points = sub(result, "decisions").points
    assert [p.text for p in points] == ["先前的决定", "后来的决定"]  # AI 给的顺序相反
    text = result.path.read_text(encoding="utf-8")
    first = points[0].first_day
    assert f"- [ ] 不像我｜{first}｜先前的决定（把握：高｜1 个对话、1 天）" in text


# ---------- F18-11 「给 AI 的说明」只发结论 ----------


def test_F18_11_ai_instructions_get_conclusions_only(vault: Path, model: FakeModel) -> None:
    _run(vault)
    [prompt] = model.ai_prompts
    assert "核心标签：追问到底" in prompt and "怕麻烦人 ↔ 怕失望" in prompt
    assert "[O" not in prompt and "原话" not in prompt and OWN_JAVA[:10] not in prompt


# ---------- F18-12 新旧格式的「不像我」都认 ----------

OLD_FORMAT = """---
type: profile
---

## 认知与学习

### 推断

- [x] 不像我｜旧格式的结论（把握：中｜5 个对话）不对
  - 「原话」—— [[a|b]]
- [ ] 不像我｜没勾的（把握：高｜1 个对话、1 天）
"""

NEW_FORMAT = """---
type: profile
---

# 你：a

## 04 决策记录

- [X] 不像我｜2026-11-01｜新格式（把握：高｜1 个对话、1 天）原因：不是我
  > [!note]- 证据（1 条）
  > - 「原话」—— [[a|b]]
"""


def test_F18_12_old_and_new_formats(tmp_path: Path) -> None:
    old = tmp_path / "old.md"
    old.write_text(OLD_FORMAT, encoding="utf-8")
    assert [(r.text, r.why) for r in read_rejected(old)] == [("旧格式的结论", "不对")]
    new = tmp_path / "new.md"
    new.write_text(NEW_FORMAT, encoding="utf-8")
    assert [(r.text, r.why) for r in read_rejected(new)] == [("2026-11-01｜新格式", "不是我")]
