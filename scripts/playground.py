"""本机试用页面：在浏览器里点一点，试用 eia 目前所有功能。开发工具，不属于产品功能。

用法：
    .venv\\Scripts\\python scripts\\playground.py [--vault <路径>] [--port 8765]

只监听 127.0.0.1。每个请求都要带页面里的随机令牌，并检查 Host，
防止别的网站在你浏览时偷偷调用这些命令。命令在本进程里直接执行，输出和终端一模一样。
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shlex
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml
from click.testing import CliRunner

from eia import llm
from eia.cli import main
from eia.headlines.config import HeadlinesConfigError, load_headlines_config
from eia.schema.io import FrontmatterError, read_document
from eia.schema.validate import iter_markdown
from eia.schema.vault import CONFIG_NAME, is_vault

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).with_name("playground.html")
MAX_BODY = 1_000_000
# CliRunner 会临时替换 sys.stdout，同一时间只能跑一条命令
_LOCK = threading.Lock()


def run_cli(*args: str) -> dict[str, Any]:
    with _LOCK:
        started = time.monotonic()
        result = CliRunner().invoke(main, list(args))
    return {
        "command": "eia " + " ".join(shlex.quote(a) for a in args),
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "seconds": round(time.monotonic() - started, 2),
    }


def _vault(params: dict[str, Any]) -> str:
    value = str(params.get("vault") or "").strip()
    if not value:
        raise ValueError("请先填写 vault 路径")
    return value


def _flag(params: dict[str, Any], name: str, option: str) -> list[str]:
    return [option] if params.get(name) else []


def _opt(params: dict[str, Any], name: str, option: str) -> list[str]:
    value = str(params.get(name) or "").strip()
    return [option, value] if value else []


def action_info(params: dict[str, Any]) -> dict[str, Any]:
    vault = Path(_vault(params))
    info: dict[str, Any] = {"vault": str(vault), "is_vault": is_vault(vault)}
    if not info["is_vault"]:
        return info
    try:
        config = llm.load_llm_config(vault, require_key=False)
        info["llm"] = {
            "text": f"{config.text.provider} · {config.text.model}",
            "key": config.text.has_key or not config.text.api_key_env,
            "embed": f"{config.embed.provider} · {config.embed.model}" if config.embed else None,
        }
    except llm.LLMConfigError as exc:
        info["llm_error"] = str(exc)
    try:
        headlines = load_headlines_config(vault)
        info["headlines"] = {
            "feeds": len(headlines.feeds),
            "publishers": len({feed.publisher for feed in headlines.feeds}),
            "min_sources": headlines.min_sources,
            "cluster": headlines.cluster,
        }
    except HeadlinesConfigError as exc:
        info["headlines_error"] = str(exc)
    counts = {"chats": 0, "headlines": 0}
    for name, folder in (("chats", "chats"), ("headlines", "inbox/headlines")):
        if (vault / folder).is_dir():
            counts[name] = sum(1 for _ in (vault / folder).rglob("*.md"))
    info["counts"] = counts
    return info


def action_exports(params: dict[str, Any]) -> dict[str, Any]:
    """只列文件名和大小，不读内容。"""
    folder = ROOT / "exports"
    files = sorted(folder.glob("*.zip")) if folder.is_dir() else []
    return {
        "files": [
            {"path": str(path), "name": path.name, "mb": round(path.stat().st_size / 1e6, 1)}
            for path in files
        ]
    }


def action_init(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("init", _vault(params))


def action_validate(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("validate", _vault(params))


def action_import(params: dict[str, Any]) -> dict[str, Any]:
    platform = str(params.get("platform"))
    if platform not in ("chatgpt", "claude"):
        raise ValueError("平台只能是 chatgpt 或 claude")
    path = str(params.get("path") or "").strip()
    if not path:
        raise ValueError("请填写导出文件的路径")
    args = [
        "import",
        platform,
        path,
        "--vault",
        _vault(params),
        *_flag(params, "dry_run", "--dry-run"),
    ]
    if platform == "claude":
        args += _flag(params, "include_thinking", "--include-thinking")
    return run_cli(*args)


def action_chats_stats(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("chats", "stats", "--vault", _vault(params))


def action_chats_dump(params: dict[str, Any]) -> dict[str, Any]:
    args = [
        "chats",
        "dump",
        "--vault",
        _vault(params),
        "--format",
        "jsonl",
        *_flag(params, "only_me", "--only-me"),
        *_flag(params, "no_notes", "--no-notes"),
        *_opt(params, "platform", "--platform"),
        *_opt(params, "since", "--since"),
        *_opt(params, "until", "--until"),
    ]
    result = run_cli(*args)
    records = [json.loads(line) for line in result["stdout"].splitlines() if line.strip()]
    query = str(params.get("q") or "").strip().lower()
    if query:
        records = [
            r
            for r in records
            if query in str(r["text"]).lower() or query in str(r["title"]).lower()
        ]
    limit = int(params.get("limit") or 200)
    result["total"] = len(records)
    result["records"] = records[:limit]
    result["stdout"] = f"（{len(records)} 条，jsonl 输出已解析成下面的列表）"
    return result


def action_llm_show(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("llm", "show", "--vault", _vault(params))


def action_llm_test(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("llm", "test", "--vault", _vault(params))


def action_llm_ask(params: dict[str, Any]) -> dict[str, Any]:
    prompt = str(params.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("请输入问题")
    started = time.monotonic()
    try:
        answer = llm.get_client(Path(_vault(params))).complete(prompt)
    except llm.LLMError as exc:
        return {"error": str(exc)}
    return {"answer": answer, "seconds": round(time.monotonic() - started, 2)}


def action_headlines_check(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("headlines", "check", "--vault", _vault(params))


def action_headlines_run(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli(
        "headlines", "run", "--vault", _vault(params), *_flag(params, "dry_run", "--dry-run")
    )


def action_headlines_list(params: dict[str, Any]) -> dict[str, Any]:
    folder = Path(_vault(params)) / "inbox" / "headlines"
    entries = []
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            meta = read_document(path).meta
        except FrontmatterError:
            continue
        sources = [s for s in meta.get("sources") or [] if isinstance(s, dict)]
        entries.append(
            {
                "file": path.name,
                "title": meta.get("title"),
                "count": meta.get("independent_count"),
                "created": str(meta.get("created")),
                "updated": str(meta.get("updated")),
                "tags": meta.get("tags") or [],
                "sources": [
                    {
                        "publisher": s.get("publisher"),
                        "title": s.get("title") or s.get("url"),
                        "url": s.get("url"),
                        "published": str(s.get("published") or ""),
                    }
                    for s in sources
                ],
            }
        )
    entries.sort(key=lambda e: (-(e["count"] or 0), e["updated"]))
    return {"entries": entries}


def action_files(params: dict[str, Any]) -> dict[str, Any]:
    """列出 vault 里的 Markdown 文件（和 eia validate 看的范围一样）。只读 frontmatter。"""
    vault = Path(_vault(params))
    if not is_vault(vault):
        raise ValueError(f"{vault} 不是一个 vault")
    files = []
    for path in iter_markdown(vault):
        rel = path.relative_to(vault).as_posix()
        entry: dict[str, Any] = {"path": rel, "folder": rel.rpartition("/")[0] or "（根目录）"}
        files.append(entry | _summary(path))
    return {"files": files}


_SUMMARIES: dict[Path, tuple[float, dict[str, Any]]] = {}
_HEAD = re.compile(rb"\A(?:\xef\xbb\xbf)?---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)", re.S | re.M)
_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _summary(path: Path) -> dict[str, Any]:
    """列表只要标题、类型、时间：只读文件开头的 frontmatter，按修改时间缓存。"""
    mtime = path.stat().st_mtime
    cached = _SUMMARIES.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    with path.open("rb") as handle:
        head = handle.read(64_000)
    match = _HEAD.match(head)
    try:
        meta = yaml.load(match.group(1).decode("utf-8"), Loader=_Loader) if match else None
    except (yaml.YAMLError, UnicodeDecodeError):
        meta = None
    if isinstance(meta, dict):
        summary = {
            "title": str(meta.get("title") or path.stem),
            "type": meta.get("type"),
            "updated": str(meta.get("updated") or meta.get("created") or ""),
        }
    else:
        summary = {"title": path.stem, "type": None, "updated": "", "broken": True}
    _SUMMARIES[path] = (mtime, summary)
    return summary


def action_file(params: dict[str, Any]) -> dict[str, Any]:
    vault = Path(_vault(params)).resolve()
    target = (vault / str(params.get("path") or "")).resolve()
    if not target.is_relative_to(vault) or target.suffix.lower() != ".md" or not target.is_file():
        raise ValueError("只能打开 vault 里的 .md 文件")
    try:
        document = read_document(target)
        meta: dict[str, Any] = document.meta
        body, error = document.body, None
    except FrontmatterError as exc:
        meta, error = {}, str(exc)
        body = target.read_bytes().decode("utf-8", errors="replace")
    return {
        "path": target.relative_to(vault).as_posix(),
        "absolute": str(target),
        "meta": meta,
        "body": body,
        "error": error,
    }


ACTIONS = {
    name.removeprefix("action_"): func
    for name, func in globals().items()
    if name.startswith("action_") and callable(func)
}


def make_handler(token: str, port: int, default_vault: str) -> type[BaseHTTPRequestHandler]:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in allowed_hosts

        def do_GET(self) -> None:
            if not self._host_ok():
                self._json(HTTPStatus.FORBIDDEN, {"error": "Host 不对"})
                return
            if self.path not in ("/", "/index.html"):
                self._json(HTTPStatus.NOT_FOUND, {"error": "没有这个页面"})
                return
            html = PAGE.read_text(encoding="utf-8")
            html = html.replace("__TOKEN__", token).replace(
                "__DEFAULT_VAULT__", json.dumps(default_vault)
            )
            self._send(HTTPStatus.OK, html.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self) -> None:
            if not self._host_ok() or self.headers.get("X-Playground-Token") != token:
                self._json(HTTPStatus.FORBIDDEN, {"error": "令牌不对，请刷新页面"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if self.path != "/api" or length > MAX_BODY:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "请求不对"})
                return
            try:
                request = json.loads(self.rfile.read(length).decode("utf-8"))
                action = ACTIONS[str(request.get("action"))]
                self._json(HTTPStatus.OK, action(request.get("params") or {}))
            except KeyError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "没有这个操作"})
            except (ValueError, OSError) as exc:
                self._json(HTTPStatus.OK, {"error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:
            """不打印每个请求（里面可能有路径），只在出错时由上面返回给页面。"""

    return Handler


def serve(vault: str, port: int, open_browser: bool) -> None:
    token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(token, port, vault))
    url = f"http://127.0.0.1:{port}/"
    print(f"试用页面：{url}   （Ctrl+C 停止）")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("已停止")
    finally:
        server.server_close()


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="eia 本机试用页面")
    parser.add_argument("--vault", default="", help=f"默认打开的 vault（目录里要有 {CONFIG_NAME}）")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()
    serve(args.vault, args.port, not args.no_browser)


if __name__ == "__main__":
    main_cli()
