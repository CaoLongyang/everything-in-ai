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
from eia.profile.prompts import EXTRACT_SYSTEM
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

    @property
    def extract_prompts(self) -> list[str]:
        return [p for s, p in self.calls if s == EXTRACT_SYSTEM]

    @property
    def synth_prompts(self) -> list[str]:
        return [p for s, p in self.calls if s != EXTRACT_SYSTEM]

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("画像不用向量")

    def complete(self, prompt: str, system: str | None, json_schema: Any) -> Reply:
        self.calls.append((system, prompt))
        if system == EXTRACT_SYSTEM:
            return Reply(json.dumps(self._extract(prompt), ensure_ascii=False))
        if self.fail_synth:
            raise LLMCallError("假装网络断了")
        ids = re.findall(r"^\[(O\d+)\]", prompt, flags=re.MULTILINE)
        return Reply(
            json.dumps(
                {
                    "conclusions": [
                        {
                            "kind": "stated",
                            "text": f"结论{len(self.calls)}",
                            "confidence": "高",
                            "evidence": ids,
                        },
                        {
                            "kind": "inferred",
                            "text": "没有证据的结论",
                            "confidence": "低",
                            "evidence": ["O999"],
                        },
                    ],
                    "open_questions": ["一个问题"],
                },
                ensure_ascii=False,
            )
        )

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
