"""llm 测试的公共工具。全部模拟返回，不连网、不花钱。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from eia.llm._base import Reply
from eia.llm.config import Endpoint
from eia.schema.vault import CONFIG_NAME, init_vault

SECRET = "sk-test-SECRET-0123456789"  # 假 key，只用来检查它不会被打印 gitleaks:allow
PROMPT = "这是一段只应该出现在请求里的私密提问内容 ABCDEFG"

ANTHROPIC_LLM: dict[str, Any] = {
    "provider": "anthropic",
    "model": "claude-opus-5-5",
    "api_key_env": "EIA_TEST_ANTHROPIC_KEY",
    "max_retries": 2,
    "timeout_s": 5,
}

OPENAI_LLM: dict[str, Any] = {
    "provider": "openai_compatible",
    "model": "deepseek-chat",
    "api_base": "https://api.deepseek.com",
    "api_key_env": "EIA_TEST_DEEPSEEK_KEY",
    "embed": {
        "provider": "openai_compatible",
        "model": "nomic-embed-text",
        "api_base": "http://127.0.0.1:11434/v1",
    },
    "max_retries": 2,
}


@pytest.fixture
def make_vault(tmp_path: Path) -> Callable[..., Path]:
    """建一个 vault，llm 段按参数写进 config.yaml；llm=None 表示不写。"""

    def _make(llm: dict[str, Any] | None = None, *, raw: str | None = None) -> Path:
        root = tmp_path / "vault"
        init_vault(root)
        if raw is not None:
            (root / CONFIG_NAME).write_bytes(raw.encode("utf-8"))
        elif llm is not None:
            data = {"schema": 1, "timezone": None, "llm": llm}
            text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
            (root / CONFIG_NAME).write_bytes(text.encode("utf-8"))
        return root

    return _make


@pytest.fixture(autouse=True)
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EIA_TEST_ANTHROPIC_KEY", SECRET)
    monkeypatch.setenv("EIA_TEST_DEEPSEEK_KEY", SECRET)


class FakeBackend:
    """按顺序返回 replies 里的结果；元素是异常就抛出。记录每次调用。"""

    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, Any]] = []

    def _next(self) -> Any:
        item = self.replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def complete(
        self, prompt: str, system: str | None, json_schema: dict[str, Any] | None
    ) -> Reply:
        self.calls.append(("complete", (prompt, system, json_schema)))
        item = self._next()
        return item if isinstance(item, Reply) else Reply(str(item))

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(("embed", texts))
        result: list[list[float]] = self._next()
        return result


def factory_for(backend: FakeBackend) -> Callable[[Endpoint, float], FakeBackend]:
    def _factory(endpoint: Endpoint, timeout_s: float) -> FakeBackend:
        return backend

    return _factory
