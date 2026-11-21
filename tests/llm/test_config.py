"""F9-01：配置不完整时说清缺什么，不发请求。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from eia.cli import main
from eia.llm import get_client
from eia.llm.config import load_llm_config, read_env_file
from eia.llm.errors import LLMConfigError

from .conftest import ANTHROPIC_LLM, OPENAI_LLM, SECRET


def _without(base: dict[str, Any], *names: str) -> dict[str, Any]:
    return {k: v for k, v in base.items() if k not in names}


@pytest.fixture
def forbid_requests(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """任何 SDK 客户端被创建都记下来：配置有问题时一个都不该有。"""
    created: list[str] = []

    def _sdk(*args: Any, **kwargs: Any) -> Any:
        created.append("sdk")
        raise AssertionError("配置有问题时不应创建 SDK 客户端")

    monkeypatch.setattr("anthropic.Anthropic", _sdk)
    monkeypatch.setattr("openai.OpenAI", _sdk)
    return created


@pytest.mark.parametrize(
    ("llm", "expected"),
    [
        (None, "没有 llm 配置"),
        (_without(ANTHROPIC_LLM, "provider"), "缺少 llm.provider"),
        (_without(ANTHROPIC_LLM, "model"), "缺少 llm.model"),
        ({**ANTHROPIC_LLM, "api_key_env": "EIA_TEST_NOT_SET"}, "环境变量 EIA_TEST_NOT_SET"),
        (_without(ANTHROPIC_LLM, "api_key_env"), "缺少 llm.api_key_env"),
        ({**ANTHROPIC_LLM, "provider": "litellm"}, "「litellm」无法识别"),
        (_without(OPENAI_LLM, "api_base"), "缺少 llm.api_base"),
        (
            {**OPENAI_LLM, "embed": {**ANTHROPIC_LLM, "model": "x"}},
            "Claude 不提供向量",
        ),
        ({**OPENAI_LLM, "embed": {"model": "x"}}, "缺少 llm.embed.provider"),
    ],
)
def test_F9_01_missing_config_is_reported_and_nothing_is_sent(
    make_vault: Callable[..., Path],
    forbid_requests: list[str],
    llm: dict[str, Any] | None,
    expected: str,
) -> None:
    root = make_vault(llm)
    result = CliRunner().invoke(main, ["llm", "test", "--vault", str(root)])
    assert result.exit_code == 1
    assert expected in result.output
    assert "没有发出任何请求" in result.output
    assert forbid_requests == []


def test_F9_01_not_a_vault(tmp_path: Path) -> None:
    with pytest.raises(LLMConfigError, match="不是一个 vault"):
        load_llm_config(tmp_path)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("llm: [1, 2]\n", "应该是一组键值"),
        ("llm:\n  provider: anthropic\n  model: m\n  api_key_env: K\n  embed: 3\n", "llm.embed"),
        ("llm: {provider: anthropic, model: m, api_key_env: K, max_retries: -1}\n", "max_retries"),
        ("llm: {provider: anthropic, model: m, api_key_env: K, max_retries: 1.5}\n", "整数"),
        ("llm: {provider: anthropic, model: m, api_key_env: K, timeout_s: yes}\n", "timeout_s"),
        ("llm: [unclosed\n", "无法读取"),
    ],
)
def test_F9_01_malformed_values(make_vault: Callable[..., Path], raw: str, expected: str) -> None:
    root = make_vault(raw=raw)
    with pytest.raises(LLMConfigError, match=expected):
        load_llm_config(root, environ={"K": "x"})


def test_F9_01_key_comes_from_env_file_and_environment_wins(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_vault({**ANTHROPIC_LLM, "api_key_env": "EIA_FROM_FILE"})
    (root / ".env").write_bytes(
        b"# comment\n\nexport EIA_FROM_FILE=\"from-file\"\nnot a pair\nOTHER='x'\n"
    )
    assert load_llm_config(root, environ={}).text.api_key == "from-file"
    assert load_llm_config(root, environ={"EIA_FROM_FILE": "env"}).text.api_key == "env"
    assert read_env_file(root / "missing") == {}


def test_F9_01_unreadable_env_file(make_vault: Callable[..., Path]) -> None:
    root = make_vault(ANTHROPIC_LLM)
    (root / ".env").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(LLMConfigError, match=r"\.env 无法读取"):
        load_llm_config(root, environ={})


def test_F9_01_defaults_and_local_service_without_key(make_vault: Callable[..., Path]) -> None:
    root = make_vault(_without(OPENAI_LLM, "api_key_env", "max_retries"))
    config = load_llm_config(root, environ={})
    assert config.text.api_key is None and not config.text.has_key
    assert config.max_retries == 3 and config.timeout_s == 60.0
    assert config.embed is not None and config.embed.api_base == "http://127.0.0.1:11434/v1"


def test_F9_01_show_works_without_key_but_says_so(make_vault: Callable[..., Path]) -> None:
    root = make_vault({**ANTHROPIC_LLM, "api_key_env": "EIA_TEST_NOT_SET"})
    result = CliRunner().invoke(main, ["llm", "show", "--vault", str(root)])
    assert result.exit_code == 0, result.output
    assert "EIA_TEST_NOT_SET（未设置）" in result.output
    assert "[向量]  未配置" in result.output


def test_F9_01_show_reports_config_error(make_vault: Callable[..., Path]) -> None:
    result = CliRunner().invoke(main, ["llm", "show", "--vault", str(make_vault(None))])
    assert result.exit_code == 1
    assert "没有 llm 配置" in result.output


def test_F9_01_public_client_reads_vault(make_vault: Callable[..., Path]) -> None:
    root = make_vault(OPENAI_LLM)
    assert get_client(root).config.text.api_key == SECRET


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"max_tokens": 0}, "max_tokens 应该是正整数"),
        ({"max_tokens": True}, "max_tokens"),
        ({"extra_body": "x"}, "extra_body 应该是一组键值"),
    ],
)
def test_F9_01_bad_max_tokens_or_extra_body(
    make_vault: Callable[..., Path], extra: dict[str, Any], expected: str
) -> None:
    with pytest.raises(LLMConfigError, match=expected):
        load_llm_config(make_vault({**OPENAI_LLM, **extra}))
