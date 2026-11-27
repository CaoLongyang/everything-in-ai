"""在随机端口上起一个真的 eia sync serve，用 urllib 发请求。对话内容全部是虚构的。"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from eia.schema.vault import init_vault
from eia.sync import server as sync_server
from eia.sync import store


@dataclass
class Reply:
    status: int
    body: dict[str, Any]


@dataclass
class Running:
    root: Path
    port: int
    token: str

    def call(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        token: str | None = "",
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
    ) -> Reply:
        data = raw if raw is not None else None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method=method
        )
        request.add_header("Content-Type", "application/json")
        if token is not None:
            request.add_header(sync_server.TOKEN_HEADER, token or self.token)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return Reply(response.status, json.loads(response.read()))
        except urllib.error.HTTPError as exc:
            return Reply(exc.code, json.loads(exc.read()))

    def send(self, platform: str, data: Any, **kwargs: Any) -> Reply:
        return self.call("POST", "/conversation", {"platform": platform, "data": data}, **kwargs)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    init_vault(root)
    (root / "config.yaml").write_text("schema: 1\ntimezone: Asia/Singapore\n", encoding="utf-8")
    return root


@pytest.fixture
def serve(vault: Path) -> Iterator[Callable[[], Running]]:
    servers: list[Any] = []

    def _start() -> Running:
        server = sync_server.make_server(vault, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        return Running(vault, server.server_address[1], store.token(vault))

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()
