"""试用页面 scripts/playground.py 的冒烟测试：安全检查和几个操作。"""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from eia.schema.vault import init_vault

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("playground", ROOT / "scripts" / "playground.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


playground = _load()


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, str, Path]]:
    vault = tmp_path / "vault"
    init_vault(vault)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), playground.make_handler("tok", 0, str(vault)))
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = playground.make_handler("tok", port, str(vault))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", "tok", vault
    httpd.shutdown()
    httpd.server_close()


def _post(base: str, payload: dict[str, Any], token: str | None, host: str | None = None) -> Any:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Playground-Token"] = token
    if host is not None:
        headers["Host"] = host
    request = urllib.request.Request(
        base + "/api", data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def test_page_is_served_with_token(server: tuple[str, str, Path]) -> None:
    base, _, vault = server
    with urllib.request.urlopen(base + "/", timeout=10) as response:
        html = response.read().decode("utf-8")
    assert 'const TOKEN = "tok"' in html and "__TOKEN__" not in html
    assert json.dumps(str(vault)) in html


@pytest.mark.parametrize(
    ("token", "host"), [(None, None), ("wrong", None), ("tok", "evil.example")]
)
def test_requests_without_token_or_from_other_hosts_are_refused(
    server: tuple[str, str, Path], token: str | None, host: str | None
) -> None:
    base, _, vault = server
    with pytest.raises(urllib.error.HTTPError) as info:
        _post(base, {"action": "init", "params": {"vault": str(vault / "x")}}, token, host)
    assert info.value.code == 403
    assert not (vault / "x").exists()


def test_actions(server: tuple[str, str, Path]) -> None:
    base, token, vault = server
    info = _post(base, {"action": "info", "params": {"vault": str(vault)}}, token)
    assert info["is_vault"] and "没有 llm 配置" in info["llm_error"]
    assert info["counts"] == {"chats": 0, "headlines": 0}

    checked = _post(base, {"action": "validate", "params": {"vault": str(vault)}}, token)
    assert checked["exit_code"] == 0 and checked["command"].startswith("eia validate")

    dump = _post(
        base, {"action": "chats_dump", "params": {"vault": str(vault), "only_me": True}}, token
    )
    assert dump["total"] == 0 and dump["records"] == []

    assert _post(base, {"action": "headlines_list", "params": {"vault": str(vault)}}, token) == {
        "entries": []
    }
    assert "请先填写" in _post(base, {"action": "validate", "params": {}}, token)["error"]
    assert "没有这个操作" in str(_bad(base, token))


def test_browse_files(server: tuple[str, str, Path]) -> None:
    base, token, vault = server
    note = vault / "notes" / "想法.md"
    note.write_text("---\ntitle: 我的想法\ntype: note\n---\n\n正文 **重点**\n", encoding="utf-8")
    (vault / "notes" / "坏的.md").write_bytes(b"no frontmatter\n")
    (vault / "secret.txt").write_text("不该被读到", encoding="utf-8")

    listed = _post(base, {"action": "files", "params": {"vault": str(vault)}}, token)["files"]
    by_path = {f["path"]: f for f in listed}
    assert by_path["notes/想法.md"]["title"] == "我的想法"
    assert by_path["notes/坏的.md"]["broken"] is True

    opened = _post(
        base, {"action": "file", "params": {"vault": str(vault), "path": "notes/想法.md"}}, token
    )
    assert opened["meta"]["title"] == "我的想法" and "**重点**" in opened["body"]
    broken = _post(
        base, {"action": "file", "params": {"vault": str(vault), "path": "notes/坏的.md"}}, token
    )
    assert broken["body"] == "no frontmatter\n" and "frontmatter" in broken["error"]

    outside = vault.parent / "outside.md"
    outside.write_text("---\ntitle: x\n---\n", encoding="utf-8")
    for path in ("../outside.md", str(outside), "secret.txt", "notes/没有.md"):
        result = _post(
            base, {"action": "file", "params": {"vault": str(vault), "path": path}}, token
        )
        assert result == {"error": "只能打开 vault 里的 .md 文件"}, path


def test_chat_viewer(server: tuple[str, str, Path]) -> None:
    import datetime as dt

    from eia.chat.importer import import_conversations
    from eia.chat.model import Conversation, Message

    base, token, vault = server
    t = dt.datetime(2026, 3, 1, 8, 0, tzinfo=dt.UTC)
    messages = (
        Message("user", t, "你好"),
        Message("assistant", t + dt.timedelta(minutes=1), "你好！", thinking="打个招呼"),
    )
    conversation = Conversation("claude", "c1", "打招呼", t, t, None, messages, thinking=True)
    import_conversations(vault, [conversation])

    listed = _post(base, {"action": "conversations", "params": {"vault": str(vault)}}, token)
    [item] = listed["conversations"]
    assert item["title"] == "打招呼" and item["platform"] == "claude" and item["messages"] == 2

    opened = _post(
        base,
        {"action": "conversation", "params": {"vault": str(vault), "path": item["path"]}},
        token,
    )
    assert [(m["role"], m["text"], m["thinking"]) for m in opened["messages"]] == [
        ("user", "你好", ""),
        ("assistant", "你好！", "打个招呼"),
    ]
    assert opened["messages"][0]["time"] is not None

    (vault / "chats" / "claude" / "坏.md").write_text(
        "---\ntype: chat\n---\n无标记\n", encoding="utf-8"
    )
    broken = _post(
        base,
        {"action": "conversation", "params": {"vault": str(vault), "path": "chats/claude/坏.md"}},
        token,
    )
    assert "这个对话读不了" in broken["error"]
    escape = _post(
        base, {"action": "conversation", "params": {"vault": str(vault), "path": "../x.md"}}, token
    )
    assert escape == {"error": "只能打开 vault 里的 .md 文件"}


def test_replace_section_keeps_everything_else() -> None:
    text = "# 注释\nschema: 1\nllm:\n  provider: old\n  model: x\n\n# 下一段\ntimezone:\n"
    new = playground.replace_section(text, "llm", {"provider": "anthropic", "model": "m"})
    assert (
        new == "# 注释\nschema: 1\nllm:\n  provider: anthropic\n  model: m\n\n# 下一段\ntimezone:\n"
    )
    assert (
        playground.replace_section(text, "llm", None)
        == "# 注释\nschema: 1\n\n# 下一段\ntimezone:\n"
    )
    appended = playground.replace_section("schema: 1\n", "headlines", {"min_sources": 3})
    assert appended == "schema: 1\n\nheadlines:\n  min_sources: 3\n"
    # 注释里的 "# llm:" 不算
    assert playground.replace_section("# llm:\n", "llm", None) == "# llm:\n"


def test_settings_form_produces_a_working_config(server: tuple[str, str, Path]) -> None:
    from eia.headlines.config import load_headlines_config
    from eia.llm.config import load_llm_config

    base, token, vault = server
    got = _post(base, {"action": "config_get", "params": {"vault": str(vault)}}, token)
    assert got["llm"] is None and got["headlines"] is None
    assert len(got["recommended_feeds"]) == 13 and got["env_names"] == []
    assert got["check"]["llm"]["ok"] is False

    form = {
        "vault": str(vault),
        "llm": {
            "enabled": True,
            "provider": "openai_compatible",
            "model": "deepseek-v4-flash",
            "api_base": "https://api.deepseek.com",
            "api_key_env": "EIA_PLAY_KEY",
            "embed": {"enabled": True, "model": "bge-m3", "api_base": ""},
        },
        "headlines": {
            "enabled": True,
            "min_sources": "4",
            "window_hours": "24",
            "similarity": "",
            "cluster": "embed",
            "feeds": got["recommended_feeds"][:5],
        },
    }
    saved = _post(base, {"action": "config_save_form", "params": form}, token)
    assert saved["saved"] and saved["check"]["headlines"] == {"ok": True}
    assert saved["check"]["llm"]["text_key_missing"] is True
    text = (vault / "config.yaml").read_text(encoding="utf-8")
    assert "# everything-in-ai vault 配置" in text  # 模板注释还在
    assert (vault / ".eia" / "config.yaml.bak").is_file()
    config = load_headlines_config(vault)
    assert config.min_sources == 4 and len(config.feeds) == 5 and config.similarity == 0.68

    key = _post(
        base,
        {
            "action": "env_set_key",
            "params": {"vault": str(vault), "name": "EIA_PLAY_KEY", "value": "sk-x"},
        },
        token,
    )
    assert key == {"saved": True, "env_names": ["EIA_PLAY_KEY"], "gitignored": True}
    assert "sk-x" not in json.dumps(key)
    llm_config = load_llm_config(vault, environ={})
    assert llm_config.text.api_key == "sk-x"
    assert llm_config.embed is not None and llm_config.embed.api_base == "http://127.0.0.1:11434/v1"

    again = _post(
        base,
        {
            "action": "env_set_key",
            "params": {"vault": str(vault), "name": "EIA_PLAY_KEY", "value": "sk-y"},
        },
        token,
    )
    assert again["saved"]
    assert (vault / ".env").read_text(encoding="utf-8").count("EIA_PLAY_KEY=") == 1

    off = {**form, "llm": {"enabled": False}, "headlines": {"enabled": False}}
    _post(base, {"action": "config_save_form", "params": off}, token)
    text = (vault / "config.yaml").read_text(encoding="utf-8")
    assert "\nllm:" not in text and "\nheadlines:" not in text


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [("BAD NAME", "x", "只能用字母"), ("OK", "", "不能为空"), ("OK", "a\nb", "换行")],
)
def test_env_key_validation(
    server: tuple[str, str, Path], name: str, value: str, error: str
) -> None:
    base, token, vault = server
    result = _post(
        base,
        {"action": "env_set_key", "params": {"vault": str(vault), "name": name, "value": value}},
        token,
    )
    assert error in result["error"]


def test_raw_config_save_rejects_broken_yaml(server: tuple[str, str, Path]) -> None:
    base, token, vault = server
    before = (vault / "config.yaml").read_bytes()
    for text, error in (("llm: [oops", "不是合法的 YAML"), ("- a\n- b\n", "最外层")):
        result = _post(
            base,
            {"action": "config_save_raw", "params": {"vault": str(vault), "text": text}},
            token,
        )
        assert error in result["error"]
        assert (vault / "config.yaml").read_bytes() == before
    ok = _post(
        base,
        {"action": "config_save_raw", "params": {"vault": str(vault), "text": "schema: 1\n"}},
        token,
    )
    assert ok["saved"] and (vault / "config.yaml").read_text(encoding="utf-8") == "schema: 1\n"


def _bad(base: str, token: str) -> str:
    try:
        _post(base, {"action": "rm_rf"}, token)
    except urllib.error.HTTPError as exc:
        return exc.read().decode("utf-8")
    return ""
