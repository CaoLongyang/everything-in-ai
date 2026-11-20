"""F9-03 / F9-04 / F9-05：重试、JSON 校验、日志里不出现 key 和 prompt。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel

from eia.llm import Client, LLMCallError, LLMOutputError
from eia.llm._base import Reply, safe_detail
from eia.llm.config import load_llm_config
from eia.llm.errors import TransientError

from .conftest import ANTHROPIC_LLM, OPENAI_LLM, PROMPT, SECRET, FakeBackend, factory_for


class Answer(BaseModel):
    answer: str
    score: int


GOOD = '{"answer": "是", "score": 3}'


def _client(root: Path, backend: FakeBackend, sleeps: list[float]) -> Client:
    return Client(load_llm_config(root), factory_for(backend), sleep=sleeps.append)


def test_F9_03_invalid_output_is_retried_then_accepted(make_vault: Callable[..., Path]) -> None:
    backend = FakeBackend(["不是 JSON", '{"answer": "是"}', GOOD])
    result = _client(make_vault(ANTHROPIC_LLM), backend, []).complete_json(PROMPT, Answer)
    assert result == Answer(answer="是", score=3)
    assert len(backend.calls) == 3
    schema = backend.calls[0][1][2]
    assert schema["required"] == ["answer", "score"]


def test_F9_03_gives_up_after_max_retries(make_vault: Callable[..., Path]) -> None:
    backend = FakeBackend(["{}"] * 10)
    with pytest.raises(LLMOutputError, match="3 次都不合法"):
        _client(make_vault(ANTHROPIC_LLM), backend, []).complete_json(PROMPT, Answer)
    assert len(backend.calls) == 3  # max_retries: 2 → 最多 1 + 2 次


def test_F9_03_zero_retries_means_one_attempt(make_vault: Callable[..., Path]) -> None:
    backend = FakeBackend(["oops"] * 3)
    root = make_vault({**ANTHROPIC_LLM, "max_retries": 0})
    with pytest.raises(LLMOutputError, match="1 次都不合法"):
        _client(root, backend, []).complete_json(PROMPT, Answer)
    assert len(backend.calls) == 1


def test_F9_04_transient_errors_are_retried_with_backoff(make_vault: Callable[..., Path]) -> None:
    sleeps: list[float] = []
    backend = FakeBackend([TransientError("限流"), TransientError("限流"), "好"])
    assert _client(make_vault(ANTHROPIC_LLM), backend, sleeps).complete(PROMPT) == "好"
    assert sleeps == [1.0, 2.0]


def test_F9_04_transient_errors_exhausted(make_vault: Callable[..., Path]) -> None:
    sleeps: list[float] = []
    backend = FakeBackend([TransientError("网络断了")] * 5)
    with pytest.raises(LLMCallError, match=r"网络断了（已尝试 3 次）") as info:
        _client(make_vault(ANTHROPIC_LLM), backend, sleeps).complete(PROMPT)
    assert type(info.value) is LLMCallError
    assert len(backend.calls) == 3 and sleeps == [1.0, 2.0]


def test_F9_04_non_transient_error_is_not_retried(make_vault: Callable[..., Path]) -> None:
    backend = FakeBackend([LLMCallError("HTTP 401"), "不会用到"])
    with pytest.raises(LLMCallError, match="HTTP 401"):
        _client(make_vault(ANTHROPIC_LLM), backend, []).complete(PROMPT)
    assert len(backend.calls) == 1


def test_F9_04_embed_retries_too(make_vault: Callable[..., Path]) -> None:
    backend = FakeBackend([TransientError("限流"), [[1.0]]])
    assert _client(make_vault(OPENAI_LLM), backend, []).embed(["a"]) == [[1.0]]


def test_F9_04_refusal(make_vault: Callable[..., Path]) -> None:
    backend = FakeBackend([Reply("", refused=True)])
    with pytest.raises(LLMOutputError, match="拒绝回答"):
        _client(make_vault(ANTHROPIC_LLM), backend, []).complete(PROMPT)


def test_F9_05_logs_never_contain_key_or_prompt(
    make_vault: Callable[..., Path], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="eia.llm")
    backend = FakeBackend([TransientError("限流"), "坏的", GOOD, [[0.0]]])
    client = _client(make_vault(OPENAI_LLM), backend, [])
    client.complete_json(PROMPT, Answer, system=PROMPT)
    client.embed([PROMPT])
    text = caplog.text
    assert caplog.records, "应该有日志"
    assert f"prompt {len(PROMPT)} 字" in text
    assert SECRET not in text and PROMPT not in text


def test_F9_05_error_details_are_scrubbed_and_shortened() -> None:
    detail = safe_detail(f"key   {SECRET}\n" + "x" * 500, SECRET)
    assert SECRET not in detail and detail.startswith("key *** x")
    assert len(detail) == 201 and detail.endswith("…")
    assert safe_detail("ok", None) == "ok"
