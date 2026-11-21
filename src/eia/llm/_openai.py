"""provider: openai_compatible。全仓库只有这里 import openai（F9-06）。

不是所有兼容服务都支持 JSON 模式，所以 JSON 输出一律用"prompt 里给出 schema + 解析校验"
（docs/specs/llm-adapter.delta-v1.md 第 3 节），校验和重试在 client.py。
"""

from __future__ import annotations

import json
from typing import Any

import openai
from openai.types.chat import ChatCompletionMessageParam

from eia.llm._base import Reply, safe_detail, truncated
from eia.llm.config import Endpoint
from eia.llm.errors import LLMCallError, LLMOutputError, TransientError

# 本地服务（如 Ollama）不需要 key，但 SDK 要求非空
_NO_KEY = "not-needed"

JSON_INSTRUCTION = (
    "\n\n只输出一个符合下面 JSON Schema 的 JSON 对象，不要输出任何其他文字或代码块标记：\n"
)


class OpenAICompatibleBackend:
    def __init__(self, endpoint: Endpoint, timeout_s: float) -> None:
        self._endpoint = endpoint
        self._client = openai.OpenAI(
            api_key=endpoint.api_key or _NO_KEY,
            base_url=endpoint.api_base,
            timeout=timeout_s,
            max_retries=0,
        )

    def complete(
        self, prompt: str, system: str | None, json_schema: dict[str, Any] | None
    ) -> Reply:
        if json_schema is not None:
            prompt = prompt + JSON_INSTRUCTION + json.dumps(json_schema, ensure_ascii=False)
        messages: list[ChatCompletionMessageParam] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            response = self._client.chat.completions.create(
                model=self._endpoint.model,
                messages=messages,
                max_tokens=self._endpoint.max_tokens,
                extra_body=self._endpoint.extra_body,
            )
        except openai.OpenAIError as exc:
            raise self._translate(exc) from None
        if not response.choices:
            raise LLMOutputError(f"{self._name} 没有返回任何内容")
        choice = response.choices[0]
        if choice.message.refusal or choice.finish_reason == "content_filter":
            return Reply("", refused=True)
        if choice.finish_reason == "length":
            raise LLMOutputError(truncated(self._name, self._endpoint.max_tokens))
        return Reply(choice.message.content or "")

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self._client.embeddings.create(model=self._endpoint.model, input=texts)
        except openai.OpenAIError as exc:
            raise self._translate(exc) from None
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        if len(vectors) != len(texts):
            raise LLMOutputError(f"{self._name} 返回了 {len(vectors)} 个向量，应为 {len(texts)} 个")
        return vectors

    @property
    def _name(self) -> str:
        return f"{self._endpoint.api_base}"

    def _translate(self, exc: openai.OpenAIError) -> LLMCallError:
        key = self._endpoint.api_key
        name = self._name
        if isinstance(exc, openai.APIConnectionError):
            return TransientError(f"连不上 {name} 或超时：{safe_detail(exc, key)}")
        if isinstance(exc, openai.RateLimitError):
            return TransientError(f"{name} 限流（HTTP 429）")
        if isinstance(exc, openai.APIStatusError):
            status = exc.status_code
            if status >= 500:
                return TransientError(f"{name} 服务端错误（HTTP {status}）")
            return LLMCallError(
                f"{name} 拒绝了请求（HTTP {status}）：{safe_detail(exc.message, key)}"
            )
        return LLMCallError(f"调用 {name} 出错：{type(exc).__name__}：{safe_detail(exc, key)}")
