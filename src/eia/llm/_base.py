"""后端的公共约定。每个后端只负责：发一次请求，把厂商的返回和错误翻译成这里的类型。

重试、JSON 校验、日志都在 client.py，后端不做。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

_MAX_DETAIL = 200


@dataclass(frozen=True)
class Reply:
    text: str
    refused: bool = False


class Backend(Protocol):
    def complete(
        self, prompt: str, system: str | None, json_schema: dict[str, Any] | None
    ) -> Reply:
        """json_schema 不为 None 时要求模型只输出符合它的 JSON。"""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def truncated(name: str, max_tokens: int) -> str:
    return (
        f"{name} 的回复超过了 {max_tokens} token 被截断，没有可用的结果。"
        "可以在 llm.max_tokens 调大上限；如果是 DeepSeek 等有思考模式的模型，"
        "也可以用 llm.extra_body 关闭思考（思考会占掉大部分额度）"
    )


def safe_detail(message: object, secret: str | None) -> str:
    """厂商错误信息可能带上 key；去掉 key 并截短后才能出现在报错里。"""
    text = str(message)
    if secret:
        text = text.replace(secret, "***")
    text = " ".join(text.split())
    return text if len(text) <= _MAX_DETAIL else text[:_MAX_DETAIL] + "…"
