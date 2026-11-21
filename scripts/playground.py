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
from eia.chat.reader import ChatReadError, read_chat
from eia.cli import main
from eia.headlines.config import HeadlinesConfigError, load_headlines_config
from eia.schema.io import FrontmatterError, read_document
from eia.schema.validate import iter_markdown
from eia.schema.vault import CONFIG_NAME, VaultError, is_vault, load_config

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
            "platform": meta.get("platform"),
            "messages": meta.get("message_count"),
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


def action_conversations(params: dict[str, Any]) -> dict[str, Any]:
    """vault 里所有对话，最近更新的在前。"""
    files = [f for f in action_files(params)["files"] if f.get("type") == "chat"]
    files.sort(key=lambda f: f["updated"], reverse=True)
    return {"conversations": files}


def action_conversation(params: dict[str, Any]) -> dict[str, Any]:
    """用 eia 的 chat-reader 读一个对话：和 eia chats dump 读到的完全一样。"""
    vault = Path(_vault(params)).resolve()
    target = (vault / str(params.get("path") or "")).resolve()
    if not target.is_relative_to(vault) or target.suffix.lower() != ".md" or not target.is_file():
        raise ValueError("只能打开 vault 里的 .md 文件")
    try:
        chat = read_chat(target, load_config(vault).timezone)
    except ChatReadError as exc:
        return {"error": f"这个对话读不了：{exc}"}
    return {
        "path": target.relative_to(vault).as_posix(),
        "absolute": str(target),
        "title": chat.title,
        "platform": chat.platform,
        "tags": list(chat.tags),
        "notes": chat.notes,
        "messages": [
            {
                "role": m.role,
                "time": m.time.strftime("%Y-%m-%d %H:%M") if m.time else None,
                "text": m.text,
                "thinking": m.thinking,
            }
            for m in chat.messages
        ],
    }


# ---------- 设置：在网页里改 config.yaml 和 .env ----------

ENV_FILE = ".env"
BACKUP = Path(".eia") / "config.yaml.bak"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TOP_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:")

# 只预填确认过的模型名；其他留空，让用户去服务商文档查
PROVIDER_PRESETS = [
    {
        "name": "DeepSeek",
        "provider": "openai_compatible",
        "model": "deepseek-v4-flash",
        "api_base": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    {
        "name": "Claude",
        "provider": "anthropic",
        "model": "claude-haiku-5-5",
        "api_base": "",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    {
        "name": "OpenAI",
        "provider": "openai_compatible",
        "model": "",
        "api_base": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
    },
    {
        "name": "通义千问",
        "provider": "openai_compatible",
        "model": "",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
    },
    {
        "name": "Kimi",
        "provider": "openai_compatible",
        "model": "",
        "api_base": "https://api.moonshot.cn/v1",
        "api_key_env": "MOONSHOT_API_KEY",
    },
    {
        "name": "本地 Ollama",
        "provider": "openai_compatible",
        "model": "",
        "api_base": "http://127.0.0.1:11434/v1",
        "api_key_env": "",
    },
]


def _config_path(params: dict[str, Any]) -> Path:
    vault = Path(_vault(params))
    if not is_vault(vault):
        raise ValueError(f"{vault} 不是一个 vault（找不到 {CONFIG_NAME}），先在「概览」里新建")
    return vault / CONFIG_NAME


def _env_names(vault: Path) -> list[str]:
    from eia.llm.config import read_env_file

    return sorted(name for name, value in read_env_file(vault / ENV_FILE).items() if value)


def recommended_feeds() -> list[dict[str, str]]:
    """从 eia init 的配置模板里取推荐源，保证和模板一致。"""
    from eia.schema.vault import CONFIG_TEMPLATE

    lines = CONFIG_TEMPLATE.splitlines()
    block = [line.removeprefix("# ") for line in lines[lines.index("# headlines:") :]]
    data = yaml.safe_load("\n".join(line for line in block if not line.startswith("#")))
    return [dict(feed) for feed in data["headlines"]["feeds"]]


def _check_config(vault: Path) -> dict[str, Any]:
    report: dict[str, Any] = {}
    try:
        config = llm.load_llm_config(vault, require_key=False)
        report["llm"] = {
            "ok": True,
            "text_key_missing": bool(config.text.api_key_env and not config.text.has_key),
            "has_embed": config.embed is not None,
        }
    except llm.LLMConfigError as exc:
        report["llm"] = {"ok": False, "error": str(exc)}
    try:
        load_headlines_config(vault)
        report["headlines"] = {"ok": True}
    except HeadlinesConfigError as exc:
        report["headlines"] = {"ok": False, "error": str(exc)}
    return report


def action_config_get(params: dict[str, Any]) -> dict[str, Any]:
    path = _config_path(params)
    text = path.read_bytes().decode("utf-8", errors="replace")
    data = yaml.safe_load(text) if text.strip() else {}
    data = data if isinstance(data, dict) else {}
    return {
        "path": str(path),
        "text": text,
        "llm": data.get("llm") if isinstance(data.get("llm"), dict) else None,
        "headlines": data.get("headlines") if isinstance(data.get("headlines"), dict) else None,
        "env_names": _env_names(path.parent),
        "presets": PROVIDER_PRESETS,
        "recommended_feeds": recommended_feeds(),
        "check": _check_config(path.parent),
    }


def _save_config(path: Path, text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return {"error": f"不是合法的 YAML，没有保存：{exc}"}
    if data is not None and not isinstance(data, dict):
        return {"error": "配置文件最外层必须是 键: 值 的形式，没有保存"}
    backup = path.parent / BACKUP
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(path.read_bytes())
    from eia.schema.io import atomic_write

    atomic_write(path, text)
    return {"saved": True, "backup": str(backup), "check": _check_config(path.parent)}


def action_config_save_raw(params: dict[str, Any]) -> dict[str, Any]:
    return _save_config(_config_path(params), str(params.get("text") or ""))


def replace_section(text: str, key: str, section: dict[str, Any] | None) -> str:
    """替换（或删除、追加）顶层的 key: 段。其他行，包括注释，原样保留。"""
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if _top_key(line) == key), None)
    end = start
    if start is not None:
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t"):
            end += 1
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1  # 段后的空行留给下一段
    block = ""
    if section is not None:
        block = yaml.safe_dump({key: section}, allow_unicode=True, sort_keys=False, width=1000)
    if start is None:
        if not block:
            return text
        sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return text + sep + block
    return "".join(lines[:start]) + block + "".join(lines[end:])


def _top_key(line: str) -> str | None:
    match = _TOP_KEY.match(line)
    return match.group(1) if match else None


def _clean(value: Any) -> str:
    return str(value or "").strip()


def action_config_save_form(params: dict[str, Any]) -> dict[str, Any]:
    path = _config_path(params)
    text = path.read_bytes().decode("utf-8")
    form_llm = params.get("llm")
    if isinstance(form_llm, dict):
        section: dict[str, Any] | None = None
        if form_llm.get("enabled"):
            section = {
                "provider": _clean(form_llm.get("provider")),
                "model": _clean(form_llm.get("model")),
            }
            for name in ("api_base", "api_key_env"):
                if _clean(form_llm.get(name)):
                    section[name] = _clean(form_llm.get(name))
            embed = form_llm.get("embed")
            if isinstance(embed, dict) and embed.get("enabled"):
                section["embed"] = {
                    "provider": "openai_compatible",
                    "model": _clean(embed.get("model")),
                    "api_base": _clean(embed.get("api_base")) or "http://127.0.0.1:11434/v1",
                }
            section["max_retries"] = 2
            section["timeout_s"] = 60
        text = replace_section(text, "llm", section)
    form_hl = params.get("headlines")
    if isinstance(form_hl, dict):
        hl: dict[str, Any] | None = None
        if form_hl.get("enabled"):
            feeds = [
                {"publisher": _clean(f.get("publisher")), "url": _clean(f.get("url"))}
                for f in form_hl.get("feeds") or []
                if isinstance(f, dict) and _clean(f.get("url"))
            ]
            hl = {
                "min_sources": int(form_hl.get("min_sources") or 3),
                "window_hours": float(form_hl.get("window_hours") or 36),
                "cluster": _clean(form_hl.get("cluster")) or "embed",
            }
            if form_hl.get("similarity") not in (None, ""):
                hl["similarity"] = float(form_hl["similarity"])
            hl["feeds"] = feeds
        text = replace_section(text, "headlines", hl)
    return _save_config(path, text)


def action_env_set_key(params: dict[str, Any]) -> dict[str, Any]:
    """把 key 写进 vault 的 .env。值不回传、不打印，页面只知道「已设置」。"""
    vault = _config_path(params).parent
    name, value = _clean(params.get("name")), _clean(params.get("value"))
    if not _ENV_NAME.match(name):
        raise ValueError("环境变量名只能用字母、数字和下划线，例如 DEEPSEEK_API_KEY")
    if not value or "\n" in value or "\r" in value:
        raise ValueError("key 不能为空，也不能有换行")
    env = vault / ENV_FILE
    lines = env.read_bytes().decode("utf-8-sig").splitlines() if env.is_file() else []
    kept = [
        line
        for line in lines
        if line.split("=", 1)[0].strip().removeprefix("export ").strip() != name
    ]
    kept.append(f"{name}={value}")
    from eia.schema.io import atomic_write

    atomic_write(env, "\n".join(kept) + "\n")
    gitignore = vault / ".gitignore"
    ignored = gitignore.is_file() and ".env" in gitignore.read_text(encoding="utf-8").split()
    return {"saved": True, "env_names": _env_names(vault), "gitignored": ignored}


def action_ollama_models(params: dict[str, Any]) -> dict[str, Any]:
    """列出本机 Ollama 里的模型（只访问 127.0.0.1）。"""
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as response:
            data = json.loads(response.read())
    except (OSError, ValueError):
        return {"running": False, "models": []}
    models = [
        {
            "name": m.get("name"),
            "embedding": "embedding" in (m.get("capabilities") or [])
            or "embed" in str(m.get("name"))
            or str(m.get("name")).startswith("bge"),
        }
        for m in data.get("models") or []
    ]
    return {"running": True, "models": models}


def action_open_config(params: dict[str, Any]) -> dict[str, Any]:
    """用系统默认程序打开 config.yaml（Windows 上一般是记事本）。只在本机生效。"""
    import os
    import subprocess
    import sys

    path = _config_path(params)
    if params.get("reveal"):
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path.parent)])
    elif sys.platform == "win32":
        os.startfile(path)  # 用户点了按钮才打开，打开的是 vault 自己的配置文件
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
    return {"opened": str(path)}


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
            except (ValueError, OSError, VaultError) as exc:
                self._json(HTTPStatus.OK, {"error": str(exc)})
            except Exception as exc:  # 试用工具：任何意外都告诉页面，而不是断开连接
                self._json(HTTPStatus.OK, {"error": f"出错了：{type(exc).__name__}：{exc}"})

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
