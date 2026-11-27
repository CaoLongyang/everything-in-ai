"""F18-21 至 F18-26：访谈个性化（docs/specs/profile.delta-interview.md）。内容全部是虚构的。"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from eia.profile import interview
from eia.profile import prompts as prompts_mod
from eia.profile import supplement as supplement_mod
from eia.profile.material import ANSWER_MARK
from eia.schema.io import read_document

from .conftest import NOW, FakeModel
from .test_profile import _run

SETTINGS = Path("profile") / interview.SETTINGS


def q(question: str, aspect: str = "", why: str = "", follow_up: str = "") -> dict[str, Any]:
    return {"question": question, "why": why, "aspect": aspect, "follow_up": follow_up}


def ask(vault: Path, model: FakeModel, reply: list[dict[str, Any]], day: int) -> Path:
    model.ask_reply = reply
    plan = supplement_mod.plan_ask(vault)
    path, _ = supplement_mod.run_ask(vault, plan, now=NOW + dt.timedelta(days=day))
    return path


def answer(path: Path, number: int, text: str) -> None:
    lines = path.read_text(encoding="utf-8").split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(f"### {number}. "))
    mark = next(i for i in range(start, len(lines)) if lines[i].startswith(ANSWER_MARK))
    lines[mark] = ANSWER_MARK + text
    path.write_text("\n".join(lines), encoding="utf-8")


def headings(path: Path) -> list[str]:
    return re.findall(r"^### \d+\. (.+)$", read_document(path).body, flags=re.MULTILINE)


def write_settings(vault: Path, *, want: tuple[str, ...] = (), avoid: tuple[str, ...] = ()) -> None:
    path = vault / SETTINGS
    text = path.read_text(encoding="utf-8")
    for name in want:
        text = text.replace(f"- [ ] {name}：", f"- [x] {name}：")
    text = text.rstrip("\n") + "\n" + "".join(f"- {a}\n" for a in avoid)
    path.write_text(text, encoding="utf-8")


def test_F18_21_settings_created_once_and_left_alone(vault: Path, model: FakeModel) -> None:
    _run(vault)
    assert not (vault / SETTINGS).exists()
    supplement_mod.plan_ask(vault)
    assert not (vault / SETTINGS).exists()  # 只预览、不加 --yes 时什么都不写
    ask(vault, model, [q("第一个问题", "family")], 0)
    text = (vault / SETTINGS).read_text(encoding="utf-8")
    for _, name, _, _ in interview.ASPECTS:
        assert f"- [ ] {name}：" in text
    assert read_document(vault / SETTINGS).meta["type"] == "note"
    write_settings(vault, want=("求学",))
    before = (vault / SETTINGS).read_bytes()
    answer(next((vault / "profile").glob("访谈 *.md")), 1, "答了")
    ask(vault, model, [q("第二个问题", "school")], 1)
    assert (vault / SETTINGS).read_bytes() == before


def test_F18_22_avoided_aspects_and_words_are_never_asked(vault: Path, model: FakeModel) -> None:
    _run(vault)
    ask(vault, model, [q("热身问题")], 0)
    write_settings(vault, avoid=("求学", "前任"))
    path = ask(
        vault,
        model,
        [
            q("你为什么选这个专业？", "school"),
            q("说说你的感情经历", "people", why="想知道前任对你的影响"),
            q("你和前任怎么认识的？", "people"),
            q("最近在忙什么？", "now"),
        ],
        1,
    )
    assert headings(path) == ["最近在忙什么？"]
    [_, prompt] = model.ask_prompts
    assert "不想聊（绝对不要问）：求学；前任" in prompt
    assert "- school：求学｜0｜" in prompt and "不想聊，不要问" in prompt


def test_F18_23_follow_ups_from_answers_and_no_repeats(vault: Path, model: FakeModel) -> None:
    _run(vault)
    first = ask(
        vault, model, [q("你最难忘的一次旅行是哪次？", "turns"), q("没答的问题", "family")], 0
    )
    answer(first, 1, "高中毕业那年一个人去了海边，回来后决定学设计")
    label = first.stem.removeprefix("访谈 ")
    ref = f"{label} 第 1 题"
    second = ask(
        vault,
        model,
        [
            q("你最难忘的一次旅行是哪一次？", "turns"),  # 几乎一样，算问过
            q("没答的问题", "family"),  # 问过、没答，也不再问
            q("一个人去海边时在想什么？", "turns", follow_up=ref),
            q("为什么那次之后决定学设计？", "school", follow_up=ref),
            q("家里人怎么看你学设计？", "family", follow_up=ref),
            q("第四个追问", "turns", follow_up=ref),
            q("编号不对的追问", "now", follow_up="不存在 第 9 题"),
            q("新的问题", "values"),
        ],
        1,
    )
    [_, prompt] = model.ask_prompts
    assert f"[{ref}] 问：你最难忘的一次旅行是哪次？\n答：高中毕业那年" in prompt
    assert "- 没答的问题" in prompt  # 问过的都列出来，但没答的不当作回答发
    assert prompt.count("答：") == 1
    assert headings(second) == [
        "一个人去海边时在想什么？",
        "为什么那次之后决定学设计？",
        "家里人怎么看你学设计？",
        "编号不对的追问",
        "新的问题",
    ]
    body = read_document(second).body
    assert body.count(f"追问 {ref}") == 3
    assert "（方面：转折点 · 追问" in body and "（方面：现在和将来）" in body


def test_F18_24_two_skipped_rounds_add_an_auto_line(vault: Path, model: FakeModel) -> None:
    _run(vault)
    one = ask(vault, model, [q("家里几口人？", "family"), q("最近在忙什么？", "now")], 0)
    answer(one, 2, "在忙毕业设计")
    two = ask(vault, model, [q("小时候住在哪？", "family"), q("周末一般做什么？", "now")], 1)
    answer(two, 2, "踢球")
    plan = supplement_mod.plan_ask(vault)
    assert plan.auto_avoid == ["family"]  # now 答了，不算
    model.ask_reply = [q("你和父母关系怎么样？", "family"), q("将来想住在哪？", "now")]
    third, _ = supplement_mod.run_ask(vault, plan, now=NOW + dt.timedelta(days=2))
    assert headings(third) == ["将来想住在哪？"]
    text = (vault / SETTINGS).read_text(encoding="utf-8")
    assert "- （自动）成长与家庭" in text
    settings = interview.read_settings(vault / SETTINGS)
    assert "family" in settings.avoided_aspects

    # 删掉那一行就恢复，而且不会马上又被加回去
    (vault / SETTINGS).write_text(text.replace("- （自动）成长与家庭\n", ""), encoding="utf-8")
    again = supplement_mod.plan_ask(vault)
    assert again.auto_avoid == [] and "family" not in again.settings.avoided_aspects


def test_F18_25_coverage_sent_and_wanted_aspects_first(vault: Path, model: FakeModel) -> None:
    _run(vault)
    first = ask(vault, model, [q("热身", "work")], 0)
    answer(first, 1, "做过一个小程序")
    write_settings(vault, want=("人际关系",))
    second = ask(vault, model, [q("在想做什么？", "now"), q("最好的朋友是谁？", "people")], 1)
    assert headings(second) == ["最好的朋友是谁？", "在想做什么？"]
    prompt = model.ask_prompts[-1]
    assert "想聊的方面：人际关系" in prompt
    assert re.search(r"- work：工作与项目｜1｜\d+", prompt)
    assert re.search(r"- family：成长与家庭｜0｜0", prompt)


def test_F18_26_prompts_have_no_author_specifics() -> None:
    texts = [
        value
        for name, value in vars(prompts_mod).items()
        if name.isupper() and isinstance(value, str)
    ]
    texts.append(supplement_mod.SUPPLEMENT_HINT)
    texts.append(interview.settings_text())
    for text in texts:
        for word in ("马来西亚", "基督", "室友", "签证在等"):
            assert word not in text, word
