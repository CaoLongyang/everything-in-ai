"""个人画像测试的公共工具：虚构的对话和假的文本模型。不连网、不花钱。"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from eia.chat.importer import import_conversations
from eia.chat.model import Conversation, Message
from eia.llm import Client
from eia.llm._base import Reply
from eia.llm.config import load_llm_config
from eia.llm.errors import LLMCallError
from eia.profile.prompts import (
    ASK_SYSTEM,
    EXTRACT_SYSTEM,
    PART_AI,
    PART_CAREER,
    PART_CORE,
    PART_HISTORY,
)
from eia.schema.vault import CONFIG_NAME, init_vault

TODAY = dt.date(2026, 11, 26)
NOW = dt.datetime(2026, 11, 26, 2, 0, tzinfo=dt.UTC)

OWN_JAVA = "我在学 Java，下周就考数据结构了，链表还没搞懂。"
AI_ONLY = "这是AI自己的回答不应该被发送"
THINKING = "这是AI的思考过程不应该被发送"
ECHO = "链表的每个节点保存数据和指向下一个节点的引用，" * 40  # 贴回的 AI 回答，超过 500 字
SECRET_CODE = "SECRET_CODE_SHOULD_NOT_BE_SENT = 1"
TRACE = (
    "Traceback (most recent call last):\n"
    '  File "a.py", line 1, in <module>\n'
    "ZeroDivisionError: division by zero"
)
OWN_ECHO_COMMENT = "对，就是这个意思，我终于懂了。"
OWN_FAITH = "我相信信仰和理性并不冲突，这是我很在意的事。"
OWN_HOME = "我在马来西亚读计算机，平时一个人住。"
OLD = "很久以前说的话，不在范围里，不应该被发送。"
FAKE_QUOTE = "我从来没有说过这句被编出来的话"

LLM: dict[str, Any] = {
    "provider": "openai_compatible",
    "model": "deepseek-v4-flash",
    "api_base": "https://api.deepseek.com",
}

_CATEGORY_WORDS = {"java": "cognition", "信仰": "values", "马来西亚": "identity"}


def _conv(
    platform: str,
    cid: str,
    title: str,
    day: dt.datetime,
    messages: list[Message],
    thinking: bool | None = None,
) -> Conversation:
    return Conversation(
        platform, cid, title, day, messages[-1].time, None, tuple(messages), thinking
    )


def conversations() -> list[Conversation]:
    t1 = dt.datetime(2026, 11, 1, 2, 0, tzinfo=dt.UTC)
    t2 = dt.datetime(2026, 11, 10, 2, 0, tzinfo=dt.UTC)
    t3 = dt.datetime(2026, 10, 5, 2, 0, tzinfo=dt.UTC)
    old = dt.datetime(2026, 1, 1, 2, 0, tzinfo=dt.UTC)
    minute = dt.timedelta(minutes=1)
    return [
        _conv(
            "chatgpt",
            "java",
            "数据结构复习",
            t1,
            [
                Message("user", t1, OWN_JAVA),
                Message("assistant", t1 + minute, f"{AI_ONLY}\n\n{ECHO}"),
                Message(
                    "user",
                    t1 + 2 * minute,
                    f"{ECHO}\n\n{OWN_ECHO_COMMENT}\n\n```python\n{SECRET_CODE}\n```\n\n{TRACE}",
                ),
            ],
        ),
        _conv(
            "claude",
            "faith",
            "信仰与理性",
            t2,
            [
                Message("user", t2, OWN_FAITH),
                Message("assistant", t2 + minute, AI_ONLY, thinking=THINKING),
            ],
            thinking=True,
        ),
        _conv("gemini", "home", "生活", t3, [Message("user", t3, OWN_HOME)]),
        _conv("gemini", "old", "旧对话", old, [Message("user", old, OLD)]),
    ]


class FakeModel:
    """第 2 步：每行「我」的话给一条观察，另外故意编一句原话、引用一个不存在的对话；
    第 3 步：每类一条结论引用全部观察，再加一条引用不存在编号的。记录所有调用。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str]] = []
        self.fail_synth = False
        self.garble = ""  # 第 2 步：提示词里有这个标题的批次，回复不是 JSON
        self.network_down = False

    @property
    def extract_prompts(self) -> list[str]:
        return [p for s, p in self.calls if s == EXTRACT_SYSTEM]

    @property
    def synth_prompts(self) -> list[str]:
        """汇总的前 3 次请求（发观察的那几次）。"""
        return [p for s, p in self.calls if s in PART_REPLIES]

    @property
    def ask_prompts(self) -> list[str]:
        return [p for s, p in self.calls if s == ASK_SYSTEM]

    @property
    def ai_prompts(self) -> list[str]:
        return [p for s, p in self.calls if s == PART_AI]

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("画像不用向量")

    def complete(self, prompt: str, system: str | None, json_schema: Any) -> Reply:
        self.calls.append((system, prompt))
        if self.network_down:
            raise LLMCallError("连不上")
        if system == EXTRACT_SYSTEM:
            if self.garble and self.garble in prompt:
                return Reply("这不是 JSON")
            return Reply(json.dumps(self._extract(prompt), ensure_ascii=False))
        if self.fail_synth:
            raise LLMCallError("假装网络断了")
        if system == ASK_SYSTEM:
            questions = [
                {
                    "question": "你是怎么开始信基督教的？",
                    "why": "画像里信仰写得多，但不知道怎么开始的",
                },
                {"question": "   ", "why": "空问题应该被丢掉"},
                {"question": "为什么选择来马来西亚读书？"},
            ]
            return Reply(json.dumps({"questions": questions}, ensure_ascii=False))
        if system == PART_AI:
            return Reply(
                json.dumps(
                    {
                        "system_prompt": "我是一个虚构的学生。",
                        "guidelines": ["先讲为什么", "说中文"],
                    },
                    ensure_ascii=False,
                )
            )
        ids = re.findall(r"^\[(O\d+)\]", prompt, flags=re.MULTILINE)
        reply = PART_REPLIES[system](ids, len(self.calls))
        return Reply(json.dumps(reply, ensure_ascii=False))

    def _extract(self, prompt: str) -> dict[str, Any]:
        observations: list[dict[str, Any]] = []
        label = ""
        for line in prompt.splitlines():
            header = re.match(r"^=== (C\d+)：", line)
            if header:
                label = header[1]
                observations.append(
                    {
                        "chat": label,
                        "category": "interests",
                        "kind": "stated",
                        "text": "编的",
                        "quotes": [FAKE_QUOTE],
                    }
                )
                continue
            said = re.match(r"^\[(我|我的笔记|我贴回的 AI 回答)[^\]]*\] (.+)$", line)
            if not said or not label:
                continue
            text = said[2]
            category = next(
                (c for w, c in _CATEGORY_WORDS.items() if w in text.lower()), "interests"
            )
            if said[1] == "我贴回的 AI 回答":  # 只引用贴回的 AI 回答、没有自己的话：应被丢掉
                observations.append(
                    {"chat": label, "category": category, "kind": "inferred", "quotes": [text[:30]]}
                )
                continue
            observations.append(
                {
                    "chat": label,
                    "category": category,
                    "kind": "stated",
                    "text": f"你说：{text[:10]}",
                    "quotes": [text[:20]],
                }
            )
        observations.append(
            {
                "chat": "C99",
                "category": "values",
                "kind": "stated",
                "text": "不存在",
                "quotes": ["x"],
            }
        )
        return {"observations": observations}


def _point(text: str, ids: list[str], **extra: str) -> dict[str, Any]:
    return {"text": text, "confidence": "高", "evidence": ids, **extra}


def _core(ids: list[str], n: int) -> dict[str, Any]:
    return {
        "labels": ["追问到底", "信仰压力测试", "追问到底", "a", "b", "c", "d"],
        "subsections": [
            {
                "key": "about",
                "label": "留学生",
                "summary": "基本情况",
                "points": [_point(f"关于{n}", ids[:1]), _point("你在找室友", ids, kind="state")],
            },
            {
                "key": "values",
                "label": "信仰压力测试",
                "points": [_point("没有证据的结论", ["O999"])],
            },
            {
                "key": "hidden",
                "label": "两面",
                "points": [
                    _point("跨话题的同一种劲头", ids),
                    _point("只有一个对话的猜测", ids[:1]),
                ],
            },
            {"key": "tensions", "points": [_point("张力不再有这一节", ids)]},
            {"key": "education", "points": [_point("不该写在这一次", ids)]},
            {"key": "nonsense", "points": [_point("不认识的小节", ids)]},
        ],
        "open_questions": ["一个问题"],
    }


def _career(ids: list[str], n: int) -> dict[str, Any]:
    return {
        "subsections": [
            {
                "key": "education",
                "label": "先懂再动手",
                "points": [_point("你在学数据结构", ids[:1])],
            },
            {
                "key": "interest",
                "title": "信仰",
                "label": "改革宗",
                "points": [_point("你读神学", ids[-1:])],
            },
            {"key": "interest", "title": "", "points": [_point("没有标题的兴趣", ids)]},
        ]
    }


def _history(ids: list[str], n: int) -> dict[str, Any]:
    return {
        "subsections": [
            {
                "key": "wishes",
                "points": [
                    _point("卡牌游戏", ids, status="在做"),
                    _point("记账工具", ids[:1], status="乱写的状态"),
                ],
            },
            {
                "key": "decisions",
                "points": [_point("后来的决定", ids[-1:]), _point("先前的决定", ids[:1])],
            },
            {"key": "timeline", "points": [_point("一件事", ids)]},
        ]
    }


PART_REPLIES: dict[str | None, Callable[[list[str], int], dict[str, Any]]] = {
    PART_CORE: _core,
    PART_CAREER: _career,
    PART_HISTORY: _history,
}


@pytest.fixture
def model() -> FakeModel:
    return FakeModel()


@pytest.fixture
def vault(tmp_path: Path, model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    init_vault(root)
    config = {"schema": 1, "timezone": "Asia/Singapore", "llm": LLM}
    (root / CONFIG_NAME).write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    import_conversations(root, conversations())

    def _client(vault: Path | None = None) -> Client:
        return Client(
            load_llm_config(vault or Path(".")),
            lambda endpoint, timeout: model,
            sleep=lambda _s: None,
        )

    monkeypatch.setattr("eia.llm.get_client", _client)
    return root


def chat_file(root: Path, platform: str) -> Path:
    return next((root / "chats" / platform).glob("*.md"))


def append(path: Path, text: str) -> None:
    path.write_bytes(path.read_bytes() + text.encode("utf-8"))


Runner = Callable[..., Any]
