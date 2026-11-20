"""所有需要 AI 的模块的唯一入口。模块代码只写 `from eia import llm`，不出现厂商名。

用哪家服务、哪个模型完全由 vault 的 config.yaml 决定
（SPEC-llm-adapter、SPEC-DELTA-llm-adapter-v1）。vault 默认是当前目录。
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from eia.llm.client import Client
from eia.llm.config import LLMConfig, load_llm_config
from eia.llm.errors import LLMCallError, LLMConfigError, LLMError, LLMOutputError

T = TypeVar("T", bound=BaseModel)


def get_client(vault: Path | None = None) -> Client:
    """按 vault 的配置创建客户端。连续调用多次时，复用同一个客户端更省事。"""
    return Client(load_llm_config(vault or Path(".")))


def complete(prompt: str, *, system: str | None = None, vault: Path | None = None) -> str:
    return get_client(vault).complete(prompt, system=system)


def complete_json(
    prompt: str, schema: type[T], *, system: str | None = None, vault: Path | None = None
) -> T:
    return get_client(vault).complete_json(prompt, schema, system=system)


def embed(texts: list[str], *, vault: Path | None = None) -> list[list[float]]:
    return get_client(vault).embed(texts)


__all__ = [
    "Client",
    "LLMCallError",
    "LLMConfig",
    "LLMConfigError",
    "LLMError",
    "LLMOutputError",
    "complete",
    "complete_json",
    "embed",
    "get_client",
    "load_llm_config",
]
