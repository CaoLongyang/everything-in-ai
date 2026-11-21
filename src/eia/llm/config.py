"""解析 config.yaml 的 llm 段（SPEC-DELTA-llm-adapter-v1 第 2 节）。

key 只从环境变量或 vault 根目录的 .env 读，环境变量优先。config.yaml 里只写变量名。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from eia.llm.errors import LLMConfigError
from eia.schema.vault import CONFIG_NAME, is_vault

ANTHROPIC = "anthropic"
OPENAI_COMPATIBLE = "openai_compatible"
PROVIDERS = (ANTHROPIC, OPENAI_COMPATIBLE)
ENV_FILE = ".env"

DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class Endpoint:
    """一个具体的服务：发给谁、用哪个模型、key 在哪。"""

    provider: str
    model: str
    api_base: str | None
    api_key_env: str | None
    api_key: str | None  # 从环境变量解析出的值；不打印、不写文件

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    @property
    def cache_key(self) -> str:
        """向量缓存用：服务、地址、模型任何一个变了，旧向量都作废。"""
        return f"{self.provider}:{self.api_base}:{self.model}"

    @property
    def is_local(self) -> bool:
        """服务在本机（数据不出电脑）。"""
        from urllib.parse import urlsplit

        host = (urlsplit(self.api_base or "").hostname or "").lower()
        return host in {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class LLMConfig:
    text: Endpoint
    embed: Endpoint | None
    max_retries: int
    timeout_s: float


def load_llm_config(
    root: Path, *, environ: Mapping[str, str] | None = None, require_key: bool = True
) -> LLMConfig:
    """读取并校验 llm 配置。require_key=False 时缺 key 不报错（给 eia llm show 用）。"""
    if not is_vault(root):
        raise LLMConfigError(f"{root} 不是一个 vault（找不到 {CONFIG_NAME}），请先运行 eia init")
    try:
        data = yaml.safe_load((root / CONFIG_NAME).read_bytes().decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise LLMConfigError(f"{CONFIG_NAME} 无法读取：{exc}") from exc
    section = data.get("llm") if isinstance(data, dict) else None
    if section is None:
        raise LLMConfigError(f"{CONFIG_NAME} 里没有 llm 配置。不会使用任何 AI 服务")
    if not isinstance(section, dict):
        raise LLMConfigError(f"{CONFIG_NAME} 里的 llm 应该是一组键值")

    env = _environment(root, environ)
    text = _endpoint(section, "llm", env, require_key=require_key)
    embed_section = section.get("embed")
    embed: Endpoint | None = None
    if embed_section is not None:
        if not isinstance(embed_section, dict):
            raise LLMConfigError(f"{CONFIG_NAME} 里的 llm.embed 应该是一组键值")
        embed = _endpoint(embed_section, "llm.embed", env, require_key=require_key)
        if embed.provider == ANTHROPIC:
            raise LLMConfigError(
                "llm.embed.provider 不能是 anthropic：Claude 不提供向量模型。"
                "可以用 openai_compatible，例如本地 Ollama"
            )
    return LLMConfig(
        text=text,
        embed=embed,
        max_retries=_number(section, "max_retries", DEFAULT_MAX_RETRIES, integer=True),
        timeout_s=_number(section, "timeout_s", DEFAULT_TIMEOUT_S, integer=False),
    )


def _endpoint(
    section: dict[str, Any], where: str, env: Mapping[str, str], *, require_key: bool
) -> Endpoint:
    provider = _text(section, "provider")
    if provider is None:
        raise LLMConfigError(f"缺少 {where}.provider（可选：{' / '.join(PROVIDERS)}）")
    if provider not in PROVIDERS:
        raise LLMConfigError(
            f"{where}.provider「{provider}」无法识别（可选：{' / '.join(PROVIDERS)}）"
        )
    model = _text(section, "model")
    if model is None:
        raise LLMConfigError(f"缺少 {where}.model（该服务里的模型名）")
    api_base = _text(section, "api_base")
    if provider == OPENAI_COMPATIBLE and api_base is None:
        raise LLMConfigError(
            f"缺少 {where}.api_base：openai_compatible 必须写明发给哪个地址，"
            "例如 https://api.openai.com/v1、https://api.deepseek.com、http://127.0.0.1:11434/v1"
        )
    key_env = _text(section, "api_key_env")
    if key_env is None and provider == ANTHROPIC:
        raise LLMConfigError(
            f"缺少 {where}.api_key_env（key 所在的环境变量名，如 ANTHROPIC_API_KEY）"
        )
    key = (env.get(key_env) or None) if key_env else None
    if require_key and key_env and not key:
        raise LLMConfigError(
            f"环境变量 {key_env} 不存在或为空（{where}.api_key_env 指向它）。"
            f"请设置这个环境变量，或写进 vault 根目录的 {ENV_FILE}"
        )
    return Endpoint(provider, model, api_base, key_env, key)


def _text(section: dict[str, Any], name: str) -> str | None:
    value = section.get(name)
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _number(section: dict[str, Any], name: str, default: float, *, integer: bool) -> Any:
    value = section.get(name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise LLMConfigError(f"llm.{name} 应该是不小于 0 的数字")
    if integer and not isinstance(value, int):
        raise LLMConfigError(f"llm.{name} 应该是整数")
    return value if integer else float(value)


def _environment(root: Path, environ: Mapping[str, str] | None) -> dict[str, str]:
    merged = read_env_file(root / ENV_FILE)
    merged.update(os.environ if environ is None else environ)
    return merged


def read_env_file(path: Path) -> dict[str, str]:
    """最简单的 KEY=VALUE 格式；# 开头是注释，值两边的引号会去掉。"""
    if not path.is_file():
        return {}
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise LLMConfigError(f"{ENV_FILE} 无法读取：{type(exc).__name__}") from exc
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.removeprefix("export ").partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[name.strip()] = value
    return result
