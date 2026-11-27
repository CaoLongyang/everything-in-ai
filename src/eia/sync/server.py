"""`eia sync serve`：只听 127.0.0.1，接收插件发来的对话（docs/specs/chat-sync.md 第 4.3 节）。

接口（都要带 `X-Eia-Token`）：
- `GET  /status`：插件开始同步前问一下 eia 在不在。
- `POST /plan`：`{"platform", "items": [{"id", "updated"}]}` → `{"need": [编号]}`，哪些对话要取。
- `POST /conversation`：`{"platform", "data": 网站返回的原始对话}`
  → `{"result": "新建|更新|未变|跳过", "reason"}`。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from eia.chat.importer import import_conversations
from eia.sync import store
from eia.sync.convert import PLATFORMS, SyncFormatError, to_conversation

DEFAULT_PORT = 8771
MAX_BYTES = 20 * 1024 * 1024
TOKEN_HEADER = "X-Eia-Token"
_LOCAL_HOSTS = ("127.0.0.1", "localhost")


@dataclass(frozen=True)
class Outcome:
    result: str  # 新建 / 更新 / 未变 / 跳过
    reason: str = ""
    messages: int = 0


def receive(root: Path, platform: str, data: Any) -> Outcome:
    """把一个对话写进 vault，规则和导入官方导出包一样；消息比文件里少时不改。"""
    conversation = to_conversation(platform, data)
    result = import_conversations(root, [conversation], refuse_fewer=True)
    count = len(conversation.messages)
    if result.created:
        return Outcome("新建", messages=count)
    if result.updated:
        return Outcome("更新", messages=count)
    if result.unchanged:
        return Outcome("未变", messages=count)
    reason = result.skipped[0][1] if result.skipped else "没有可导入的消息"
    return Outcome("跳过", reason, count)


class _Rejected(Exception):
    def __init__(self, status: HTTPStatus, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def make_server(
    root: Path,
    *,
    port: int = DEFAULT_PORT,
    host: str = "127.0.0.1",
    now: Callable[[], dt.datetime] | None = None,
) -> HTTPServer:
    """建好服务（还没开始跑）。port=0 时由系统挑一个空端口（测试用）。"""
    clock = now or (lambda: dt.datetime.now(dt.UTC))
    expected = store.token(root)

    class Handler(BaseHTTPRequestHandler):
        server_version = "eia-sync/1"
        _body = b""

        def do_GET(self) -> None:
            self._handle(self._get)

        def do_POST(self) -> None:
            self._handle(self._post)

        def do_OPTIONS(self) -> None:
            self._reply(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "不支持"})

        def log_message(self, format: str, *args: Any) -> None:
            return  # 不打印请求行：网址里可能有对话编号以外的东西

        def _handle(self, action: Callable[[], dict[str, Any]]) -> None:
            try:
                # 先读完请求体再回复：不读完就拒绝，Windows 上对方会收到「连接被重置」
                self._body = self._read()
                self._guard()
                self._reply(HTTPStatus.OK, action())
            except _Rejected as exc:
                self._reply(exc.status, {"error": exc.reason})

        def _guard(self) -> None:
            if self.client_address[0] != "127.0.0.1":
                raise _Rejected(HTTPStatus.FORBIDDEN, "只接受本机的请求")
            hostname = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            if hostname not in _LOCAL_HOSTS:
                raise _Rejected(HTTPStatus.FORBIDDEN, "Host 不对")
            origin = self.headers.get("Origin") or ""
            if origin.startswith(("http://", "https://")):
                raise _Rejected(HTTPStatus.FORBIDDEN, "不接受网页发来的请求，只接受插件")
            if not store.check_token(expected, self.headers.get(TOKEN_HEADER)):
                raise _Rejected(HTTPStatus.UNAUTHORIZED, "口令不对：用 eia sync token 查看")

        def _get(self) -> dict[str, Any]:
            if self.path != "/status":
                raise _Rejected(HTTPStatus.NOT_FOUND, "没有这个地址")
            return {"ok": True, "platforms": list(PLATFORMS)}

        def _post(self) -> dict[str, Any]:
            body = self._json()
            platform = body.get("platform")
            if platform not in PLATFORMS:
                raise _Rejected(HTTPStatus.BAD_REQUEST, f"不认识的平台：{platform!r}")
            if not isinstance(platform, str):
                raise _Rejected(HTTPStatus.BAD_REQUEST, "platform 应该是字符串")
            if self.path == "/plan":
                items = body.get("items")
                if not isinstance(items, list):
                    raise _Rejected(HTTPStatus.BAD_REQUEST, "items 应该是列表")
                need, bad = store.plan(store.known_updates(root, platform), items)
                return {"need": need, "unreadable": bad}
            if self.path == "/conversation":
                return self._conversation(platform, body.get("data"))
            raise _Rejected(HTTPStatus.NOT_FOUND, "没有这个地址")

        def _conversation(self, platform: str, data: Any) -> dict[str, Any]:
            label = "（没有编号）"
            if isinstance(data, dict):
                found = data.get("conversation_id") or data.get("id") or data.get("uuid")
                label = found if isinstance(found, str) and found else label
            try:
                outcome = receive(root, platform, data)
            except SyncFormatError as exc:
                store.log(
                    root,
                    clock(),
                    ok=False,
                    summary=store.summary(platform, label, 0, "跳过"),
                    error="格式认不出",
                )
                raise _Rejected(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc
            store.log(
                root,
                clock(),
                ok=outcome.result != "跳过",
                summary=store.summary(platform, label, outcome.messages, outcome.result),
                error=outcome.reason or None,
            )
            return {"result": outcome.result, "reason": outcome.reason}

        def _read(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError as exc:
                raise _Rejected(HTTPStatus.BAD_REQUEST, "Content-Length 不对") from exc
            if length > MAX_BYTES:
                self.close_connection = True
                raise _Rejected(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "一次最多 20 MB")
            return self.rfile.read(length) if length > 0 else b""

        def _json(self) -> dict[str, Any]:
            try:
                body = json.loads(self._body or b"null")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise _Rejected(HTTPStatus.BAD_REQUEST, "不是合法的 JSON") from exc
            if not isinstance(body, dict):
                raise _Rejected(HTTPStatus.BAD_REQUEST, "应该是一个 JSON 对象")
            return body

        def _reply(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return HTTPServer((host, port), Handler)
