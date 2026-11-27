"""F18-30 至 F18-34：聊聊（docs/specs/profile.delta-chat.md）。内容全部是虚构的。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from eia.cli import main
from eia.profile import chat as chat_mod
from eia.profile import interview
from eia.profile.material import read_interview
from eia.schema.io import read_document

from .conftest import NOW, FakeModel
from .test_profile import _run


def t(
    question: str,
    reply: str = "嗯嗯。",
    aspect: str = "now",
    depth: str = "light",
    follow_up: str = "",
) -> dict[str, Any]:
    return {
        "reply": reply,
        "question": question,
        "aspect": aspect,
        "depth": depth,
        "follow_up": follow_up,
    }


def session(vault: Path, model: FakeModel, turns: list[dict[str, Any]]) -> chat_mod.ChatSession:
    _run(vault)
    model.chat_replies = list(turns)
    return chat_mod.plan_chat(vault, now=NOW)


def test_F18_30_nothing_sent_without_confirmation(vault: Path, model: FakeModel) -> None:
    _run(vault)
    result = CliRunner().invoke(main, ["profile", "chat", "--vault", str(vault)], input="n\n")
    assert result.exit_code == 0, result.output
    assert "发给 deepseek-v4-flash" in result.output and "不发你的聊天记录" in result.output
    assert "什么都没有发出" in result.output
    assert model.chat_prompts == []
    assert not list((vault / "profile").glob("访谈 *聊天*.md"))
    assert not (vault / "profile" / interview.SETTINGS).exists()


def test_F18_31_each_answer_saved_as_an_interview_file(vault: Path, model: FakeModel) -> None:
    chat = session(
        vault,
        model,
        [
            t("你在哪长大？", reply="你好呀，想跟你随便聊聊。", aspect="family"),
            t("海边城市长大是什么感觉？", reply="海边长大，听起来挺自在的。", aspect="family"),
            t("最近在忙什么？", reply="嗯，那时候应该很开心。"),
        ],
    )
    first = chat.start()
    assert first.said == "你好呀，想跟你随便聊聊。" and first.question == "你在哪长大？"
    assert chat.path is None or not chat.path.exists()  # 还没答，不建文件
    second = chat.answer("一个海边城市")
    path = chat.path
    assert path is not None and path.name == f"访谈 {NOW.date().isoformat()} 聊天.md"
    first_id = read_document(path).meta["id"]
    assert second.said == "海边长大，听起来挺自在的。"
    chat.answer("每天放学去海边\n很自由")
    pairs = read_interview(path)
    assert pairs == [
        ("你在哪长大？", "一个海边城市"),
        ("海边城市长大是什么感觉？", "每天放学去海边\n很自由"),
    ]
    body = read_document(path).body
    assert "（它：海边长大，听起来挺自在的。）" in body
    assert "听起来挺自在" not in " ".join(answer for _, answer in pairs)
    assert read_document(path).meta["id"] == first_id  # 重写文件 id 不变
    rounds = interview.read_rounds(vault)
    assert [q.aspect for q in rounds[-1].questions] == ["family", "family"]

    # 下一次出题、聊天都认它：问过的不再问，回答可以拿来追问
    plan = chat_mod.plan_chat(vault, now=NOW + dt.timedelta(days=1)).plan
    assert "- 你在哪长大？" in plan.text
    assert f"[{NOW.date().isoformat()} 聊天 第 1 题] 问：你在哪长大？" in plan.text


def test_F18_32_program_checks_every_question(vault: Path, model: FakeModel) -> None:
    chat = session(
        vault,
        model,
        [
            t("你心里最大的矛盾是什么？", depth="deep"),  # 开头不能问深的
            t("你在哪长大？", aspect="family"),
            t("你在哪长大呢？", aspect="family"),  # 问过了（几乎一样）
            t("那小时候最喜欢做什么？", aspect="family"),
            t("深 1", depth="deep"),
            t("深 2", depth="deep"),
            t("深 3", depth="deep"),  # 默认一次聊天最多 2 个深的
            t("深 4", depth="deep"),
        ],
    )
    (vault / "profile").mkdir(exist_ok=True)
    assert chat.start().question == "你在哪长大？"
    assert chat.answer("海边").question == "那小时候最喜欢做什么？"
    assert chat.answer("游泳").question == "深 1"
    assert chat.answer("嗯").question == "深 2"
    fallback = chat.answer("嗯")
    assert fallback.question == chat_mod.FALLBACKS[0][0]  # 深 3、深 4 都不行 → 固定的轻松问题
    assert "已经够了" in model.chat_prompts[-1]


def test_F18_32_avoided_aspects_and_words(vault: Path, model: FakeModel) -> None:
    _run(vault)
    settings = vault / "profile" / interview.SETTINGS
    settings.parent.mkdir(exist_ok=True)
    settings.write_text(
        "---\ntype: note\n---\n" + interview.settings_text() + "- 求学\n- 前任\n", encoding="utf-8"
    )
    model.chat_replies = [
        t("为什么选这个专业？", aspect="school"),
        t("你和前任怎么认识的？", aspect="people"),
        t("周末一般做什么？"),
    ]
    chat = chat_mod.plan_chat(vault, now=NOW)
    assert chat.start().question == chat_mod.FALLBACKS[0][0]
    assert chat.answer("看书").question == "周末一般做什么？"


def test_F18_33_commands(vault: Path, model: FakeModel) -> None:
    chat = session(
        vault,
        model,
        [
            t("家里几口人？", aspect="family"),
            t("小时候住哪？", aspect="family"),
            t("小时候住哪儿呀？", aspect="family"),  # 说过不想聊家里 → 不行
            t("最近在忙什么？"),
            t("将来想做什么？"),
            t("最近读什么书？"),
        ],
    )
    chat.start()
    assert chat.answer("换个话题").question == "小时候住哪？"
    after_avoid = chat.answer("不想聊这个")
    assert after_avoid.question == "最近在忙什么？"
    assert "不想聊：成长与家庭" in model.chat_prompts[-1]
    assert chat.answer("还没想好").question == "将来想做什么？"
    end = chat.answer("先到这")
    assert end.question == "" and chat.ended
    pairs = [(q.question, q.answer) for q in interview.read_rounds(vault)[-1].questions]
    assert pairs == [
        ("家里几口人？", ""),
        ("小时候住哪？", ""),
        ("最近在忙什么？", "还没想好"),
    ]  # 「先到这」时没答的那题不存
    assert interview.read_rounds(vault)[-1].questions[-1].undecided


def test_F18_34_full_chat_in_terminal(vault: Path, model: FakeModel) -> None:
    _run(vault)
    model.chat_replies = [
        t("你在哪长大？", reply="你好！", aspect="family"),
        t("那里冬天冷吗？", reply="北方啊，冬天一定很有感觉。", aspect="family"),
    ]
    result = CliRunner().invoke(
        main, ["profile", "chat", "--vault", str(vault)], input="y\n北方小城\n先到这\n"
    )
    assert result.exit_code == 0, result.output
    assert "它：你好！" in result.output and "问：你在哪长大？" in result.output
    assert "它：北方啊，冬天一定很有感觉。" in result.output
    assert "已存  profile/访谈" in result.output
    [path] = list((vault / "profile").glob("访谈 *聊天.md"))
    assert read_interview(path) == [("你在哪长大？", "北方小城")]


def test_F18_35_one_question_at_a_time_and_reading_the_room(vault: Path, model: FakeModel) -> None:
    from eia.profile.prompts import CHAT_SYSTEM

    assert "不要自称「新朋友」" in CHAT_SYSTEM and "reply 里不要提问" in CHAT_SYSTEM
    chat = session(
        vault,
        model,
        [
            t("最近在玩什么游戏？", reply="嗨，随便聊聊？"),
            t("那局是怎么赢的？", reply="这个我挺好奇的。你平时一天怎么过的？"),
            t("周末一般做什么？"),
            t("想聊聊游戏，还是聊聊做饭？"),
        ],
    )
    assert chat.start().said == "嗨，随便聊聊？"  # 开场的招呼可以带问号
    second = chat.answer("最近在打一款策略游戏，上周赢了一局特别难的")
    assert second.said == "这个我挺好奇的。"  # 回应里的问句删掉，一次只问一个问题
    assert "答得很短" not in model.chat_prompts[-1]
    chat.answer("运气")
    chat.answer("躺着")
    assert "答得很短或者跳过了" in model.chat_prompts[-1]  # 连着两次很短 → 让它换话题
