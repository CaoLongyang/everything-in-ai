"""F18-09 至 F18-12：画像按细分章节、用标签概括（docs/specs/profile.delta-format.md、
profile.delta-layout.md）。"""

from __future__ import annotations

import re
from pathlib import Path

from eia.profile import document as document_mod
from eia.profile.document import read_rejected
from eia.profile.synth import Point

from .conftest import OWN_JAVA, FakeModel
from .test_profile import _run, sub

# ---------- F18-09 细分章节、标签 ----------


def test_F18_09_chapters_subsections_and_labels(vault: Path) -> None:
    text = _run(vault).path.read_text(encoding="utf-8")
    assert "\n**一句话的你：** 追问到底 · 信仰压力测试 · a · b · c\n" in text  # 去重、最多 5 个
    assert "- **00 核心档案**：关于我\n" in text  # 目录只列有内容的小节
    assert "- **03 兴趣**：信仰\n" in text
    order = [
        "# 个人画像",
        "## 目录",
        "## 00 核心档案",
        "### 关于我\n\n> **留学生** —— 基本情况",
        "### 价值观",
        "### 沟通偏好",
        "## 01 心理",
        "### 动机模式",
        "### 你可能没意识到的你\n\n> **两面**\n",
        "## 02 学业与职业",
        "### 教育\n\n> **先懂再动手**",
        "## 03 兴趣",
        "### 信仰\n\n> **改革宗**",
        "## 04 想做的清单",
        "## 05 决策记录",
        "## 06 时间线",
        "## 07 给 AI 的说明",
        "### 系统提示词",
        "### 分析与对话准则",
        "## 未解问题",
        "## 证据",
    ]
    positions = [text.index(heading) for heading in order]
    assert positions == sorted(positions)
    assert "### 价值观\n\n（材料里没有）" in text  # 唯一的要点没有证据
    assert "### 沟通偏好\n\n（材料里没有）" in text
    for dropped in ("不该写在这一次", "不认识的小节", "没有标题的兴趣"):
        assert dropped not in text
    assert re.search(r"\n- \[ \] 关于\d+\[\^1\]\n", text)  # 正文只有要点和脚注编号
    assert "\n[^1]: 把握高 · 1 个对话、1 天：「" in text
    assert "````text\n我是一个虚构的学生。\n````" in text and "- 先讲为什么" in text


def test_F18_09_evidence_shows_at_most_eight(vault: Path) -> None:
    point = sub(_run(vault), "hidden").points[0]
    many = Point(point.text, "中", point.evidence * 4)
    notes: list[str] = []
    line = document_mod._point(many, {}, notes, dated=False)
    assert line == f"- [ ] {point.text}[^1]"
    [note] = notes
    assert note.count("「") == 8 and note.endswith("；……还有 8 条")


# ---------- F18-10 日期和证据由程序算 ----------


def test_F18_10_dated_chapters_are_sorted_by_evidence(vault: Path) -> None:
    result = _run(vault)
    points = sub(result, "decisions").points
    assert [p.text for p in points] == ["先前的决定", "后来的决定"]  # AI 给的顺序相反
    text = result.path.read_text(encoding="utf-8")
    assert f"- [ ] **{points[0].first_day}** 先前的决定[^" in text


# ---------- F18-11 「给 AI 的说明」只发结论 ----------


def test_F18_11_ai_instructions_get_conclusions_only(vault: Path, model: FakeModel) -> None:
    _run(vault)
    [prompt] = model.ai_prompts
    assert "核心标签：追问到底" in prompt and "跨话题的同一种劲头" in prompt
    assert "[O" not in prompt and "原话" not in prompt and OWN_JAVA[:10] not in prompt


# ---------- F18-12 各版格式的「不像我」都认 ----------

FIRST_FORMAT = """---
type: profile
---

## 认知与学习

### 推断

- [x] 不像我｜旧格式的结论（把握：中｜5 个对话）不对
  - 「原话」—— [[a|b]]
- [ ] 不像我｜没勾的（把握：高｜1 个对话、1 天）
"""

SECOND_FORMAT = """---
type: profile
---

## 04 决策记录

- [X] 不像我｜2026-11-01｜第二版（把握：高｜1 个对话、1 天）原因：不是我
  > [!note]- 证据（1 条）
"""

CURRENT_FORMAT = """---
type: profile
---

## 01 心理

- [x] 你似乎很怕麻烦别人[^3] 原因：其实还好
- [ ] 没勾的[^4]

## 04 决策记录

- [X] **2026-11-01** 决定换方向[^5]

## 证据

[^3]: 把握高 · 1 个对话：「原话」
"""


def test_F18_12_all_formats(tmp_path: Path) -> None:
    cases = [
        (FIRST_FORMAT, [("旧格式的结论", "不对")]),
        (SECOND_FORMAT, [("2026-11-01｜第二版", "不是我")]),
        (CURRENT_FORMAT, [("你似乎很怕麻烦别人", "其实还好"), ("2026-11-01｜决定换方向", "")]),
    ]
    for index, (content, expected) in enumerate(cases):
        path = tmp_path / f"{index}.md"
        path.write_text(content, encoding="utf-8")
        assert [(r.text, r.why) for r in read_rejected(path)] == expected
