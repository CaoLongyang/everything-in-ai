"""兴趣测试的公共工具：虚构的对话、假的向量模型和文本模型。不连网、不花钱。"""

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
from eia.headlines.feeds import Item
from eia.headlines.store import save_items
from eia.llm import Client
from eia.llm._base import Reply
from eia.llm.config import load_llm_config
from eia.schema.vault import CONFIG_NAME, init_vault

NOW = dt.datetime(2026, 11, 22, 1, 0, tzinfo=dt.UTC)
AI_MARKER = "这是AI的回答不应该被归纳"
THINKING_MARKER = "这是AI的思考过程"

# 假的向量模型按关键词给方向：同一话题的文字很像，不同话题几乎正交
TOPICS: dict[str, tuple[str, ...]] = {
    "car": ("汽车", "驾照", "car"),
    "java": ("java", "编程", "代码"),
    "faith": ("信仰", "上帝", "神学"),
    "phone": ("手机", "iphone"),
}
NAMES = {"car": "汽车生活", "java": "Java 编程", "faith": "神学与信仰"}

LLM: dict[str, Any] = {
    "provider": "openai_compatible",
    "model": "deepseek-v4-flash",
    "api_base": "https://api.deepseek.com",
    "embed": {
        "provider": "openai_compatible",
        "model": "bge-m3",
        "api_base": "http://127.0.0.1:11434/v1",
    },
}


def vector(text: str) -> list[float]:
    lowered = text.lower()
    out = [0.0] * (len(TOPICS) + 1)
    for index, words in enumerate(TOPICS.values()):
        out[index] = float(sum(lowered.count(word) for word in words))
    out[-1] = 0.15
    return out


class FakeModel:
    """embed 按 TOPICS 给向量；complete 读提示词里每一组的关键词来起名。记录所有调用。"""

    def __init__(self, learn_reply: dict[str, Any] | None = None) -> None:
        self.embedded: list[str] = []
        self.prompts: list[str] = []
        self.learn_reply = learn_reply

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return [vector(t) for t in texts]

    def complete(self, prompt: str, system: str | None, json_schema: Any) -> Reply:
        self.prompts.append(prompt)
        if self.learn_reply is not None:
            return Reply(json.dumps(self.learn_reply, ensure_ascii=False))
        named = []
        for index, section in enumerate(re.split(r"\[第 \d+ 组\]", prompt)[1:]):
            topic = next(
                (t for t, words in TOPICS.items() if any(w in section.lower() for w in words)),
                None,
            )
            if topic in NAMES:
                named.append({"group": index, "name": NAMES[topic], "description": f"{topic} 相关"})
            else:
                named.append({"group": index, "name": "杂项", "skip": True})
        return Reply(json.dumps({"interests": named}, ensure_ascii=False))


def _chat(cid: str, title: str, mine: list[str], day: int) -> Conversation:
    t = dt.datetime(2026, 3, day, 8, 0, tzinfo=dt.UTC)
    messages: list[Message] = []
    for n, text in enumerate(mine):
        messages.append(Message("user", t + dt.timedelta(minutes=2 * n), text))
        messages.append(
            Message(
                "assistant",
                t + dt.timedelta(minutes=2 * n + 1),
                f"{AI_MARKER}：手机 iphone",
                thinking=THINKING_MARKER,
            )
        )
    messages.append(Message("user", t + dt.timedelta(minutes=30), "好的谢谢"))  # 太短，跳过
    return Conversation(
        "claude", cid, title, t, t + dt.timedelta(minutes=30), None, tuple(messages), True
    )


def chats() -> list[Conversation]:
    result = []
    for n in range(4):
        result.append(
            _chat(
                f"car{n}", f"买车问题{n}", [f"马来西亚考驾照要多久，汽车保险怎么买 {n}"] * 2, 1 + n
            )
        )
        result.append(
            _chat(f"java{n}", f"作业{n}", [f"这段 java 代码为什么输出这个结果 {n}"] * 2, 10 + n)
        )
        result.append(
            _chat(f"faith{n}", f"思考{n}", [f"上帝预定和自由意志的信仰问题 {n}"] * 2, 20 + n)
        )
    return result


@pytest.fixture
def model() -> FakeModel:
    return FakeModel()


@pytest.fixture
def use_model(monkeypatch: pytest.MonkeyPatch) -> Callable[[FakeModel], None]:
    def _use(fake: FakeModel) -> None:
        def _client(vault: Path | None = None) -> Client:
            config = load_llm_config(vault or Path("."))
            return Client(config, lambda endpoint, timeout: fake, sleep=lambda _s: None)

        monkeypatch.setattr("eia.llm.get_client", _client)

    return _use


@pytest.fixture
def vault(tmp_path: Path, model: FakeModel, use_model: Callable[[FakeModel], None]) -> Path:
    root = tmp_path / "vault"
    init_vault(root)
    text = (root / CONFIG_NAME).read_text(encoding="utf-8")
    text += "\n# 用户自己的注释\n" + yaml.safe_dump(
        {"llm": LLM}, allow_unicode=True, sort_keys=False
    )
    (root / CONFIG_NAME).write_text(text, encoding="utf-8")
    import_conversations(root, chats())
    use_model(model)
    return root


def item(n: int, title: str, hours_ago: float = 2, publisher: str | None = None) -> Item:
    return Item(
        url=f"https://news.example/{n}?utm_source=rss",
        title=title,
        summary="",
        publisher=publisher or f"媒体{n}",
        published=NOW - dt.timedelta(hours=hours_ago),
        feed_url="https://news.example/rss",
    )


def stock_items(root: Path, items: list[Item]) -> None:
    save_items(root, items)


def set_interests(root: Path, interests: list[dict[str, str]], **digest: Any) -> None:
    data = yaml.safe_load((root / CONFIG_NAME).read_text(encoding="utf-8"))
    data["interests"] = interests
    if digest:
        data["digest"] = digest
    (root / CONFIG_NAME).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
