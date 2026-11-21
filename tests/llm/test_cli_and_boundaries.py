"""F9-05 / F9-06 / F9-07：命令输出、代码边界。"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from eia.cli import main
from eia.llm.errors import LLMCallError, TransientError

from .conftest import ANTHROPIC_LLM, OPENAI_LLM, SECRET, FakeBackend

SRC = Path(__file__).resolve().parents[2] / "src" / "eia"
BACKEND_FILES = {"llm/_anthropic.py", "llm/_openai.py"}
_VENDOR_IMPORT = re.compile(r"^\s*(?:import|from)\s+(?:anthropic|openai)\b", re.MULTILINE)


def _run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[object]], FakeBackend]:
    def _install(replies: list[object]) -> FakeBackend:
        backend = FakeBackend(replies)
        monkeypatch.setattr("eia.llm.Client", _bound_client(backend))
        monkeypatch.setattr("eia.llm.client.time.sleep", lambda _s: None)
        return backend

    return _install


def _bound_client(backend: FakeBackend) -> type:
    from eia.llm.client import Client
    from eia.llm.config import LLMConfig

    class _Client(Client):
        def __init__(self, config: LLMConfig) -> None:
            super().__init__(config, lambda endpoint, timeout: backend)

    return _Client


def test_F9_07_llm_test_reports_latency(
    make_vault: Callable[..., Path], fake: Callable[[list[object]], FakeBackend]
) -> None:
    backend = fake(["OK", [[0.1, 0.2]]])
    code, output = _run("llm", "test", "--vault", str(make_vault(OPENAI_LLM)))
    assert code == 0, output
    assert re.search(r"成功  文本：\d+ 毫秒", output)
    assert re.search(r"成功  向量：\d+ 毫秒", output)
    assert [kind for kind, _ in backend.calls] == ["complete", "embed"]


def test_F9_07_llm_test_failure_explains_and_exits_1(
    make_vault: Callable[..., Path], fake: Callable[[list[object]], FakeBackend]
) -> None:
    fake([LLMCallError("Claude 拒绝了请求（HTTP 401）：invalid x-api-key")])
    code, output = _run("llm", "test", "--vault", str(make_vault(ANTHROPIC_LLM)))
    assert code == 1
    assert "失败  文本：Claude 拒绝了请求（HTTP 401）" in output
    assert "向量" not in output


def test_F9_07_llm_test_partial_failure(
    make_vault: Callable[..., Path], fake: Callable[[list[object]], FakeBackend]
) -> None:
    fake(["OK", *[TransientError("连不上 http://127.0.0.1:11434/v1 或超时")] * 3])
    code, output = _run("llm", "test", "--vault", str(make_vault(OPENAI_LLM)))
    assert code == 1
    assert "成功  文本" in output
    assert "失败  向量：连不上 http://127.0.0.1:11434/v1 或超时（已尝试 3 次）" in output


def test_F9_02_module_level_shortcuts(
    make_vault: Callable[..., Path], fake: Callable[[list[object]], FakeBackend]
) -> None:
    from pydantic import BaseModel

    from eia import llm

    class Answer(BaseModel):
        ok: bool

    fake(["好", '{"ok": true}', [[1.0]]])
    root = make_vault(OPENAI_LLM)
    assert llm.complete("hi", vault=root) == "好"
    assert llm.complete_json("hi", Answer, vault=root) == Answer(ok=True)
    assert llm.embed(["a"], vault=root) == [[1.0]]


def test_F9_05_show_never_prints_the_key(make_vault: Callable[..., Path]) -> None:
    code, output = _run("llm", "show", "--vault", str(make_vault(OPENAI_LLM)))
    assert code == 0, output
    assert SECRET not in output
    assert "EIA_TEST_DEEPSEEK_KEY（已设置）" in output
    assert "api_key_env  （不需要 key）" in output
    assert "nomic-embed-text" in output
    assert "max_retries 2，timeout_s 60" in output


def test_F9_06_only_backend_files_import_vendor_sdks() -> None:
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if _VENDOR_IMPORT.search(path.read_text(encoding="utf-8"))
        and path.relative_to(SRC).as_posix() not in BACKEND_FILES
    ]
    assert offenders == []
    for name in BACKEND_FILES:  # 规则本身没失效
        assert _VENDOR_IMPORT.search((SRC / name).read_text(encoding="utf-8"))
