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


def _bad(base: str, token: str) -> str:
    try:
        _post(base, {"action": "rm_rf"}, token)
    except urllib.error.HTTPError as exc:
        return exc.read().decode("utf-8")
    return ""
