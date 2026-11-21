"""重试、超时、JSON 校验和日志。与具体厂商无关。

日志只记长度和耗时，不记 key 和 prompt 内容（F9-05）。
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from eia.llm._base import Backend, Reply
from eia.llm.config import ANTHROPIC, Endpoint, LLMConfig
from eia.llm.errors import LLMCallError, LLMConfigError, LLMOutputError, TransientError

log = logging.getLogger("eia.llm")

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")

BackendFactory = Callable[[Endpoint, float], Backend]

_MAX_BACKOFF_S = 30.0
_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```$", re.DOTALL)


def default_backend(endpoint: Endpoint, timeout_s: float) -> Backend:
    """按 provider 选后端。SDK 延迟导入：没装 [llm] 的人不用 AI 功能也能正常使用 eia。"""
    try:
        if endpoint.provider == ANTHROPIC:
            from eia.llm._anthropic import AnthropicBackend

            return AnthropicBackend(endpoint, timeout_s)
        from eia.llm._openai import OpenAICompatibleBackend

        return OpenAICompatibleBackend(endpoint, timeout_s)
    except ImportError as exc:
        raise LLMConfigError(
            f'缺少 AI 功能需要的依赖（{exc.name}）。请运行 pip install -e ".[llm]"'
        ) from exc


class Client:
    def __init__(
        self,
        config: LLMConfig,
        backend_factory: BackendFactory = default_backend,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._factory = backend_factory
        self._sleep = sleep
        self._text: Backend | None = None
        self._embed: Backend | None = None

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        reply = self._ask(prompt, system, None)
        return reply.text

    def complete_json(self, prompt: str, schema: type[T], *, system: str | None = None) -> T:
        json_schema = schema.model_json_schema()
        attempts = self.config.max_retries + 1
        problem = ""
        for attempt in range(1, attempts + 1):
            reply = self._ask(prompt, system, json_schema)
            try:
                return schema.model_validate_json(_strip_fence(reply.text))
            except ValidationError as exc:
                problem = f"{exc.error_count()} 处不符合格式"
            log.warning("模型输出不是合法的 JSON（%s），第 %d/%d 次", problem, attempt, attempts)
        raise LLMOutputError(f"模型输出 {attempts} 次都不合法：{problem}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.config.embed is None:
            raise LLMConfigError("没有配置 llm.embed，不能生成向量")
        if not texts:
            return []
        if self._embed is None:
            self._embed = self._factory(self.config.embed, self.config.timeout_s)
        backend = self._embed
        log.debug("embed：%d 段文本，共 %d 字", len(texts), sum(len(t) for t in texts))
        return self._retry(lambda: backend.embed(texts))

    def _ask(self, prompt: str, system: str | None, json_schema: dict[str, object] | None) -> Reply:
        if self._text is None:
            self._text = self._factory(self.config.text, self.config.timeout_s)
        backend = self._text
        log.debug(
            "complete：prompt %d 字，system %d 字，json=%s",
            len(prompt),
            len(system or ""),
            json_schema is not None,
        )
        started = time.monotonic()
        reply = self._retry(lambda: backend.complete(prompt, system, json_schema))
        log.debug("complete：%.2f 秒，输出 %d 字", time.monotonic() - started, len(reply.text))
        if reply.refused:
            raise LLMOutputError("模型拒绝回答这个请求")
        return reply

    def _retry(self, call: Callable[[], R]) -> R:
        attempts = self.config.max_retries + 1
        for attempt in range(1, attempts):
            try:
                return call()
            except TransientError as exc:
                delay = min(2.0 ** (attempt - 1), _MAX_BACKOFF_S)
                log.warning("%s，%.0f 秒后重试（第 %d/%d 次）", exc, delay, attempt, attempts)
                self._sleep(delay)
        try:
            return call()
        except TransientError as exc:
            raise LLMCallError(f"{exc}（已尝试 {attempts} 次）") from None


def _strip_fence(text: str) -> str:
    """有些模型不听话，会把 JSON 包在 ```json 代码块里。"""
    text = text.strip()
    match = _FENCE.match(text)
    return match.group(1).strip() if match else text
