"""provider: anthropic。全仓库只有这里 import anthropic（F9-06）。"""

from __future__ import annotations

from typing import Any

import anthropic
from anthropic.types import OutputConfigParam

from eia.llm._base import MAX_TOKENS, Reply, safe_detail
from eia.llm.config import Endpoint
from eia.llm.errors import LLMCallError, LLMConfigError, TransientError


class AnthropicBackend:
    def __init__(self, endpoint: Endpoint, timeout_s: float) -> None:
        self._endpoint = endpoint
        # 重试由 client.py 统一做，SDK 自己不重试，免得次数叠加
        self._client = anthropic.Anthropic(
            api_key=endpoint.api_key,
            base_url=endpoint.api_base,
            timeout=timeout_s,
            max_retries=0,
        )

    def complete(
        self, prompt: str, system: str | None, json_schema: dict[str, Any] | None
    ) -> Reply:
        kwargs: dict[str, Any] = {}
        if system is not None:
            kwargs["system"] = system
        if json_schema is not None:
            config: OutputConfigParam = {
                "format": {"type": "json_schema", "schema": anthropic.transform_schema(json_schema)}
            }
            kwargs["output_config"] = config
        try:
            message = self._client.messages.create(
                model=self._endpoint.model,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
        except anthropic.AnthropicError as exc:
            raise self._translate(exc) from None
        if message.stop_reason == "refusal":
            return Reply("", refused=True)
        text = "".join(block.text for block in message.content if block.type == "text")
        return Reply(text)

    def embed(self, texts: list[str]) -> list[list[float]]:
        # 配置阶段已经拦截 embed.provider: anthropic，这里是兜底
        raise LLMConfigError("Claude 不提供向量模型")

    def _translate(self, exc: anthropic.AnthropicError) -> LLMCallError:
        key = self._endpoint.api_key
        if isinstance(exc, anthropic.APIConnectionError):
            return TransientError(f"连不上 Claude 或超时：{safe_detail(exc, key)}")
        if isinstance(exc, anthropic.RateLimitError):
            return TransientError("Claude 限流（HTTP 429）")
        if isinstance(exc, anthropic.APIStatusError):
            status = exc.status_code
            if status >= 500:
                return TransientError(f"Claude 服务端错误（HTTP {status}）")
            return LLMCallError(
                f"Claude 拒绝了请求（HTTP {status}）：{safe_detail(exc.message, key)}"
            )
        return LLMCallError(f"调用 Claude 出错：{type(exc).__name__}：{safe_detail(exc, key)}")
