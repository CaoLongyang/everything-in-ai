"""F9-02 / F9-04：两个真实后端在 SDK 层被模拟。只改 config.yaml 就能换服务。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import openai
import pytest
from anthropic.resources.messages import Messages
from anthropic.types import Message, TextBlock
from openai.resources.chat.completions import Completions
from openai.resources.embeddings import Embeddings
from openai.types import CreateEmbeddingResponse, Embedding
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from pydantic import BaseModel

from eia.llm import Client, LLMCallError, LLMConfigError, LLMOutputError
from eia.llm._anthropic import AnthropicBackend
from eia.llm._openai import JSON_INSTRUCTION, OpenAICompatibleBackend
from eia.llm.config import load_llm_config

from .conftest import ANTHROPIC_LLM, OPENAI_LLM, PROMPT, SECRET

_REQUEST = httpx2.Request("POST", "https://example.invalid/v1")


class Answer(BaseModel):
    answer: str
    score: int


def _claude_message(text: str, stop_reason: str = "end_turn") -> Message:
    block = TextBlock.model_construct(type="text", text=text, citations=None)
    return Message.model_construct(content=[block], stop_reason=stop_reason, type="message")


def _chat(text: str | None, *, refusal: str | None = None, finish: str = "stop") -> ChatCompletion:
    message = ChatCompletionMessage.model_construct(content=text, refusal=refusal, role="assistant")
    choice = Choice.model_construct(finish_reason=finish, index=0, message=message)
    return ChatCompletion.model_construct(choices=[choice])


def _status(cls: type[Exception], code: int, message: str = "bad") -> Exception:
    response = httpx2.Response(code, request=_REQUEST)
    return cls(message, response=response, body=None)


class Recorder:
    """替换 SDK 方法：记录参数，按顺序返回或抛出。"""

    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:  # 挂在类上也不会绑定成方法，所以没有 self
        self.kwargs.append(kwargs)
        item = self.results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _client(root: Path) -> Client:
    return Client(load_llm_config(root), sleep=lambda _s: None)


def test_F9_02_anthropic_complete_and_json(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    create = Recorder(_claude_message("你好"), _claude_message('{"answer": "是", "score": 3}'))
    monkeypatch.setattr(Messages, "create", create)
    client = _client(make_vault(ANTHROPIC_LLM))

    assert client.complete(PROMPT, system="系统提示") == "你好"
    first = create.kwargs[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["system"] == "系统提示"
    assert first["messages"] == [{"role": "user", "content": PROMPT}]
    assert "output_config" not in first

    assert client.complete_json(PROMPT, Answer) == Answer(answer="是", score=3)
    second = create.kwargs[1]
    assert second["output_config"]["format"]["type"] == "json_schema"
    assert "system" not in second


def test_F9_02_openai_compatible_complete_json_and_embed(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = Recorder(_chat("你好"), _chat('```json\n{"answer": "否", "score": 1}\n```'))
    vectors = CreateEmbeddingResponse.model_construct(
        data=[
            Embedding.model_construct(embedding=[0.3, 0.4], index=1),
            Embedding.model_construct(embedding=[0.1, 0.2], index=0),
        ]
    )
    embeddings = Recorder(vectors)
    monkeypatch.setattr(Completions, "create", chat)
    monkeypatch.setattr(Embeddings, "create", embeddings)
    client = _client(make_vault(OPENAI_LLM))

    assert client.complete(PROMPT, system="系统提示") == "你好"
    assert chat.kwargs[0]["model"] == "deepseek-chat"
    assert chat.kwargs[0]["messages"][0] == {"role": "system", "content": "系统提示"}

    assert client.complete_json(PROMPT, Answer) == Answer(answer="否", score=1)
    sent = chat.kwargs[1]["messages"][-1]["content"]
    assert sent.startswith(PROMPT + JSON_INSTRUCTION) and '"score"' in sent

    assert client.embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert embeddings.kwargs[0] == {"model": "nomic-embed-text", "input": ["a", "b"]}
    assert client.embed([]) == []


def test_F9_02_base_url_and_key_reach_the_sdk(make_vault: Callable[..., Path]) -> None:
    client = _client(make_vault(OPENAI_LLM))
    backend = client._factory(client.config.text, 5.0)
    assert isinstance(backend, OpenAICompatibleBackend)
    sdk = backend._client
    assert str(sdk.base_url).startswith("https://api.deepseek.com")
    assert sdk.api_key == SECRET and sdk.max_retries == 0
    assert client.config.embed is not None
    local = client._factory(client.config.embed, 5.0)
    assert isinstance(local, OpenAICompatibleBackend)
    assert str(local._client.base_url).startswith("http://127.0.0.1:11434/v1")


def test_F9_02_embed_without_embed_config(make_vault: Callable[..., Path]) -> None:
    client = _client(make_vault(ANTHROPIC_LLM))
    with pytest.raises(LLMConfigError, match="没有配置 llm.embed"):
        client.embed(["a"])
    backend = AnthropicBackend(client.config.text, 5.0)
    with pytest.raises(LLMConfigError, match="不提供向量"):
        backend.embed(["a"])


def test_F9_02_missing_sdk_gives_install_hint(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "eia.llm._anthropic", None)
    client = _client(make_vault(ANTHROPIC_LLM))
    with pytest.raises(LLMConfigError, match=r"pip install -e \"\.\[llm\]\""):
        client.complete("hi")


@pytest.mark.parametrize(
    ("error", "transient", "expected"),
    [
        (anthropic.APIConnectionError(request=_REQUEST), True, "连不上 Claude"),
        (anthropic.APITimeoutError(request=_REQUEST), True, "连不上 Claude"),
        (_status(anthropic.RateLimitError, 429), True, "限流"),
        (_status(anthropic.InternalServerError, 529), True, "HTTP 529"),
        (_status(anthropic.AuthenticationError, 401, f"bad key {SECRET}"), False, "HTTP 401"),
        (anthropic.AnthropicError(f"odd {SECRET}"), False, "AnthropicError"),
    ],
)
def test_F9_04_anthropic_errors(
    make_vault: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    transient: bool,
    expected: str,
) -> None:
    create = Recorder(*[error] * 3)
    monkeypatch.setattr(Messages, "create", create)
    client = _client(make_vault(ANTHROPIC_LLM))  # max_retries: 2
    with pytest.raises(LLMCallError, match=expected) as info:
        client.complete(PROMPT)
    assert len(create.kwargs) == (3 if transient else 1)
    assert SECRET not in str(info.value)


@pytest.mark.parametrize(
    ("error", "transient", "expected"),
    [
        (openai.APIConnectionError(request=_REQUEST), True, "连不上 https://api.deepseek.com"),
        (_status(openai.RateLimitError, 429), True, "限流"),
        (_status(openai.InternalServerError, 503), True, "HTTP 503"),
        (_status(openai.AuthenticationError, 401, f"Incorrect key {SECRET}"), False, "HTTP 401"),
        (openai.OpenAIError(f"odd {SECRET}"), False, "OpenAIError"),
    ],
)
def test_F9_04_openai_errors(
    make_vault: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    transient: bool,
    expected: str,
) -> None:
    chat = Recorder(*[error] * 3)
    monkeypatch.setattr(Completions, "create", chat)
    monkeypatch.setattr(Embeddings, "create", Recorder(error))
    client = _client(make_vault(OPENAI_LLM))
    with pytest.raises(LLMCallError, match=expected) as info:
        client.complete(PROMPT)
    assert len(chat.kwargs) == (3 if transient else 1)
    assert SECRET not in str(info.value)
    if not transient:
        with pytest.raises(LLMCallError):
            client.embed(["a"])


@pytest.mark.parametrize(
    "reply",
    [_chat(None, refusal="I can't help with that"), _chat("", finish="content_filter")],
)
def test_F9_04_openai_refusal(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch, reply: ChatCompletion
) -> None:
    monkeypatch.setattr(Completions, "create", Recorder(reply))
    with pytest.raises(LLMOutputError, match="拒绝回答"):
        _client(make_vault(OPENAI_LLM)).complete(PROMPT)


def test_F9_04_anthropic_refusal_is_not_retried(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    create = Recorder(_claude_message("", stop_reason="refusal"))
    monkeypatch.setattr(Messages, "create", create)
    with pytest.raises(LLMOutputError, match="拒绝回答"):
        _client(make_vault(ANTHROPIC_LLM)).complete_json(PROMPT, Answer)
    assert len(create.kwargs) == 1


def test_F9_04_openai_empty_or_short_responses(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Completions, "create", Recorder(ChatCompletion.model_construct(choices=[])))
    one_vector = CreateEmbeddingResponse.model_construct(
        data=[Embedding.model_construct(embedding=[0.1], index=0)]
    )
    monkeypatch.setattr(Embeddings, "create", Recorder(one_vector))
    client = _client(make_vault(OPENAI_LLM))
    with pytest.raises(LLMOutputError, match="没有返回任何内容"):
        client.complete(PROMPT)
    with pytest.raises(LLMOutputError, match="返回了 1 个向量，应为 2 个"):
        client.embed(["a", "b"])


def test_F9_04_truncated_replies_fail_fast_with_advice(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-11-21：DeepSeek 默认先思考，长任务的思考占满 max_tokens，正文是空的。"""
    chat = Recorder(_chat("", finish="length"), _chat("不会用到"))
    monkeypatch.setattr(Completions, "create", chat)
    with pytest.raises(LLMOutputError, match="被截断.*llm.max_tokens.*extra_body"):
        _client(make_vault(OPENAI_LLM)).complete_json(PROMPT, Answer)
    assert len(chat.kwargs) == 1  # 截断不重试：重试只会再花一次钱

    create = Recorder(_claude_message("{", stop_reason="max_tokens"))
    monkeypatch.setattr(Messages, "create", create)
    with pytest.raises(LLMOutputError, match="Claude 的回复超过了 4096 token"):
        _client(make_vault(ANTHROPIC_LLM)).complete(PROMPT)


def test_F9_02_max_tokens_and_extra_body_reach_the_service(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = Recorder(_chat("好"))
    monkeypatch.setattr(Completions, "create", chat)
    extra = {"thinking": {"type": "disabled"}}
    _client(make_vault({**OPENAI_LLM, "max_tokens": 8000, "extra_body": extra})).complete(PROMPT)
    assert chat.kwargs[0]["max_tokens"] == 8000 and chat.kwargs[0]["extra_body"] == extra

    create = Recorder(_claude_message("好"))
    monkeypatch.setattr(Messages, "create", create)
    _client(make_vault({**ANTHROPIC_LLM, "extra_body": {"x": 1}})).complete(PROMPT)
    assert create.kwargs[0]["extra_body"] == {"x": 1} and create.kwargs[0]["max_tokens"] == 4096


def test_F9_03_empty_replies_are_reported_as_empty(
    make_vault: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Completions, "create", Recorder(*[_chat("")] * 3, _chat('{"answer": 1}')))
    with pytest.raises(LLMOutputError, match="回复是空的"):
        _client(make_vault(OPENAI_LLM)).complete_json(PROMPT, Answer)
    monkeypatch.setattr(Completions, "create", Recorder(*[_chat('{"answer": "x"}')] * 3))
    with pytest.raises(LLMOutputError, match=r"例如 score：missing"):
        _client(make_vault(OPENAI_LLM)).complete_json(PROMPT, Answer)
