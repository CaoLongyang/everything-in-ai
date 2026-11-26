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
import sys
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
from eia.chat.source import label_chat
from eia.cli import main
from eia.headlines.config import HeadlinesConfigError, load_headlines_config
from eia.schema.configedit import replace_section, save_config_text
from eia.schema.io import FrontmatterError, read_document
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
            "publishers": len({feed.name for feed in headlines.feeds}),
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
    profiles = action_profiles(params)["profiles"]
    if profiles:
        info["profile"] = profiles[0]
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
    if platform not in ("chatgpt", "claude", "gemini"):
        raise ValueError("平台只能是 chatgpt、claude 或 gemini")
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
        *_flag(params, "own_only", "--own-only"),
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


def action_schedule_status(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("schedule", "status", "--vault", _vault(params))


def action_schedule_install(params: dict[str, Any]) -> dict[str, Any]:
    times = [t for t in str(params.get("at") or "08:00").replace("，", ",").split(",") if t.strip()]
    args = ["schedule", "install", "headlines", "--vault", _vault(params)]
    for value in times:
        args += ["--at", value.strip()]
    return run_cli(*args)


def action_schedule_remove(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("schedule", "remove", "headlines", "--vault", _vault(params))


def action_schedule_run_now(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("run", "headlines", "--vault", _vault(params))


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
    """列出 vault 里的 Markdown 文件。用数据库索引（增量更新），不再每次读 988 个文件。"""
    from eia.db import database
    from eia.db.index import sync

    vault = Path(_vault(params))
    if not is_vault(vault):
        raise ValueError(f"{vault} 不是一个 vault")
    with database(vault) as conn:
        sync(conn, vault)
        rows = conn.execute(
            "SELECT path, title, type, updated, created, platform, message_count, error"
            " FROM entries ORDER BY path"
        ).fetchall()
    files = []
    for r in rows:
        entry: dict[str, Any] = {
            "path": r["path"],
            "folder": r["path"].rpartition("/")[0] or "（根目录）",
            "title": r["title"] or Path(r["path"]).stem,
            "type": r["type"],
            "updated": r["updated"] or r["created"] or "",
            "platform": r["platform"],
            "messages": r["message_count"],
        }
        if r["error"]:
            entry["broken"] = True
        files.append(entry)
    return {"files": files}


def action_search(params: dict[str, Any]) -> dict[str, Any]:
    """全文搜索（标题和正文）。3 个字及以上走索引，更短的逐条比对。"""
    from eia.db import database
    from eia.db.index import search, sync

    vault = Path(_vault(params))
    query = _clean(params.get("q"))
    if not query:
        return {"hits": []}
    started = time.monotonic()
    with database(vault) as conn:
        sync(conn, vault)
        try:
            hits = search(conn, query, entry_type=_clean(params.get("type")) or None, limit=100)
        except RuntimeError as exc:
            return {"error": str(exc)}
    return {
        "hits": [
            {"path": h.path, "title": h.title, "type": h.type, "snippet": h.snippet} for h in hits
        ],
        "seconds": round(time.monotonic() - started, 3),
    }


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
                # 「我」的消息里每段文字的来源（docs/specs/chat-reader.delta-paste.md）
                "parts": [
                    {"source": p.source, "text": p.text.strip()} for p in parts if p.text.strip()
                ]
                if parts is not None
                else None,
            }
            for m, parts in zip(chat.messages, label_chat(chat), strict=True)
        ],
    }


# ---------- 画像（docs/specs/profile.md） ----------


def _profile_range(params: dict[str, Any]) -> list[str]:
    return [*_opt(params, "since", "--since"), *_opt(params, "until", "--until")]


def action_profile_plan(params: dict[str, Any]) -> dict[str, Any]:
    """不加 --yes：只在本机整理，告诉你要发多少，什么都不发。"""
    return run_cli("profile", "run", "--vault", _vault(params), *_profile_range(params))


def action_profile_run(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("profile", "run", "--vault", _vault(params), *_profile_range(params), "--yes")


def action_profile_ask_plan(params: dict[str, Any]) -> dict[str, Any]:
    """访谈第 1 步：告诉你要发多少，什么都不发。"""
    return run_cli("profile", "ask", "--vault", _vault(params))


def action_profile_ask(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("profile", "ask", "--vault", _vault(params), "--yes")


_LABELS_LINE = re.compile(r"^(?:\*\*一句话的你：\*\*|# 你：)\s*(.*)$", re.MULTILINE)


def _profile_labels(body: str) -> list[str]:
    match = _LABELS_LINE.search(body)
    return [x.strip() for x in match[1].split("·") if x.strip()] if match else []


def action_profiles(params: dict[str, Any]) -> dict[str, Any]:
    """vault 里所有画像，最新的在前。"""
    vault = Path(_vault(params)).resolve()
    items = []
    for path in sorted((vault / "profile").glob("*.md")) if (vault / "profile").is_dir() else []:
        try:
            document = read_document(path)
        except FrontmatterError:
            continue
        meta = document.meta
        if meta.get("type") != "profile":
            continue
        items.append(
            {
                "path": path.relative_to(vault).as_posix(),
                "title": str(meta.get("title") or path.stem),
                "created": str(meta.get("created") or ""),
                "period": str(meta.get("period") or ""),
                "chats": meta.get("chats"),
                "model": str(meta.get("model") or ""),
                "labels": _profile_labels(document.body),
            }
        )
    items.sort(key=lambda item: item["created"], reverse=True)
    return {"profiles": items}


def action_profile(params: dict[str, Any]) -> dict[str, Any]:
    vault = Path(_vault(params)).resolve()
    target = (vault / str(params.get("path") or "")).resolve()
    if not target.is_relative_to(vault / "profile") or target.suffix.lower() != ".md":
        raise ValueError("只能打开 profile/ 里的画像")
    document = read_document(target)
    return {
        "path": target.relative_to(vault).as_posix(),
        "absolute": str(target),
        "meta": {k: str(v) for k, v in document.meta.items()},
        "body": document.body,
    }


# ---------- 工具：数据库、剪藏模板 ----------


def action_db_status(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("db", "status", "--vault", _vault(params))


def action_db_rebuild(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("db", "rebuild", "--vault", _vault(params), "--yes")


def action_clip_template(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("clip", "template", "--vault", _vault(params))


# ---------- 设置：在网页里改 config.yaml 和 .env ----------

ENV_FILE = ".env"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 只预填确认过的模型名；其他留空，让用户去服务商文档查
PROVIDER_PRESETS = [
    {
        "name": "DeepSeek",
        "provider": "openai_compatible",
        "model": "deepseek-v4-flash",
        "api_base": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "no_thinking": True,  # 2026-11-21 实测：默认的思考模式会占掉大部分输出额度
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


FEED_KEYS = ("type", "publisher", "url", "min_score", "query", "lang", "country", "platform")


def _describe(config: dict[str, Any]) -> dict[str, Any]:
    """页面上显示一个源：名字、类型、地址或关键词。配置写错的也照样列出来，标上错误。"""
    from eia.headlines.catalog import source_key
    from eia.headlines.config import DEFAULT_NEWSNOW, parse_source

    try:
        feed = parse_source(config, "这个源", window=36, newsnow=DEFAULT_NEWSNOW)
        name, kind, error = feed.name, feed.kind, ""
    except HeadlinesConfigError as exc:
        name, kind, error = str(config.get("publisher") or config.get("type") or "?"), "", str(exc)
    detail = config.get("url") or config.get("query") or config.get("platform") or ""
    return {
        "src": config,
        "key": source_key(config),
        "name": name,
        "kind": kind,
        "detail": str(detail),
        "error": error,
    }


def catalog_groups() -> list[dict[str, Any]]:
    from eia.headlines.catalog import load_catalog

    checked, groups = load_catalog()
    return [
        {
            "name": group.name,
            "note": group.note,
            "checked": checked,
            "sources": [_describe(source.config) for source in group.sources],
        }
        for group in groups
    ]


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
    headlines = data.get("headlines") if isinstance(data.get("headlines"), dict) else None
    feeds = (headlines or {}).get("feeds") or []
    return {
        "path": str(path),
        "text": text,
        "llm": data.get("llm") if isinstance(data.get("llm"), dict) else None,
        "headlines": headlines,
        "current_feeds": [_describe(f) for f in feeds if isinstance(f, dict)],
        "env_names": _env_names(path.parent),
        "presets": PROVIDER_PRESETS,
        "catalog": catalog_groups(),
        "check": _check_config(path.parent),
    }


def _save_config(path: Path, text: str) -> dict[str, Any]:
    try:
        backup = save_config_text(path.parent, text)
    except VaultError as exc:
        return {"error": str(exc)}
    return {"saved": True, "backup": str(backup), "check": _check_config(path.parent)}


def action_config_save_raw(params: dict[str, Any]) -> dict[str, Any]:
    return _save_config(_config_path(params), str(params.get("text") or ""))


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
            # 表单里没有的设置（max_tokens、extra_body 的其他项）从现有配置里原样保留
            old = (yaml.safe_load(text) or {}).get("llm") or {}
            old = old if isinstance(old, dict) else {}
            if isinstance(old.get("max_tokens"), int):
                section["max_tokens"] = old["max_tokens"]
            extra = (
                dict(old.get("extra_body") or {}) if isinstance(old.get("extra_body"), dict) else {}
            )
            if form_llm.get("no_thinking"):
                extra["thinking"] = {"type": "disabled"}
            else:
                extra.pop("thinking", None)
            if extra:
                section["extra_body"] = extra
            section["max_retries"] = 2
            section["timeout_s"] = 60
        text = replace_section(text, "llm", section)
    form_hl = params.get("headlines")
    if isinstance(form_hl, dict):
        hl: dict[str, Any] | None = None
        if form_hl.get("enabled"):
            # 各种类型的源原样保存，只留认识的键（不让页面写进别的东西）
            feeds = [
                {key: f[key] for key in FEED_KEYS if f.get(key) not in (None, "")}
                for f in form_hl.get("feeds") or []
                if isinstance(f, dict)
            ]
            hl = {
                "min_sources": int(form_hl.get("min_sources") or 3),
                "window_hours": float(form_hl.get("window_hours") or 36),
                "cluster": _clean(form_hl.get("cluster")) or "embed",
            }
            if form_hl.get("similarity") not in (None, ""):
                hl["similarity"] = float(form_hl["similarity"])
            old_hl = (yaml.safe_load(text) or {}).get("headlines") or {}
            if isinstance(old_hl, dict) and old_hl.get("newsnow"):
                hl["newsnow"] = old_hl["newsnow"]  # 表单里没有，原样保留
            search = form_hl.get("interest_search")
            if isinstance(search, dict) and search.get("enabled"):
                hl["interest_search"] = {
                    "enabled": True,
                    "lang": _clean(search.get("lang")) or "zh-CN",
                    "country": _clean(search.get("country")).upper() or "US",
                }
            hl["feeds"] = [f for f in feeds if f]
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


# ---------- 兴趣 ----------


def action_interests_plan(params: dict[str, Any]) -> dict[str, Any]:
    """第 1 步：本机归组，并告诉用户第 2 步要发什么。不调用文本模型。"""
    return run_cli("interests", "draft", "--vault", _vault(params))


def action_interests_name(params: dict[str, Any]) -> dict[str, Any]:
    """第 2 步：用户在页面上点了确认，才把摘录发给文本模型起名。"""
    return run_cli("interests", "draft", "--vault", _vault(params), "--yes")


def action_interests_learn_plan(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("interests", "learn", "--vault", _vault(params))


def action_interests_learn(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("interests", "learn", "--vault", _vault(params), "--yes")


def action_interests_get(params: dict[str, Any]) -> dict[str, Any]:
    from eia.interests import draft as draft_mod
    from eia.interests.config import InterestsError, load_interests
    from eia.interests.feedback import read_feedback

    vault = Path(_vault(params))
    draft_path = vault / draft_mod.DRAFT
    draft = []
    if draft_path.is_file():
        text = draft_path.read_bytes().decode("utf-8")
        evidence: dict[str, str] = {}
        name = None
        for line in text.splitlines():
            match = re.match(r"^- \[[ xX]\] \*\*(.+?)\*\*", line)
            if match:
                name = match.group(1)
            elif name and line.strip().startswith("- 依据："):
                evidence[name] = line.strip().removeprefix("- 依据：")
        draft = [
            {
                "name": i.interest.name,
                "description": i.interest.description,
                "exclude": i.interest.exclude,
                "search": "、".join(i.interest.search),
                "checked": i.checked,
                "evidence": re.sub(
                    r"\[\[[^|\]]+\|([^\]]+)\]\]", r"「\1」", evidence.get(i.interest.name, "")
                ),
            }
            for i in draft_mod.parse_draft(text)
        ]
    try:
        current = [i.as_config() for i in load_interests(vault)]
    except InterestsError as exc:
        return {"error": str(exc)}
    return {"draft": draft, "current": current, "feedback": len(read_feedback(vault))}


def action_interests_save(params: dict[str, Any]) -> dict[str, Any]:
    """页面上勾选、修改后直接写进 config.yaml 的 interests 段（效果同 eia interests accept）。"""
    from eia.interests.config import Interest, split_terms
    from eia.schema.configedit import update_section

    vault = Path(_vault(params))
    chosen = [
        Interest(
            _clean(i.get("name")),
            _clean(i.get("description")),
            _clean(i.get("exclude")),
            split_terms(i.get("search")),
        )
        for i in params.get("items") or []
        if isinstance(i, dict) and i.get("checked") and _clean(i.get("name"))
    ]
    if not chosen:
        return {"error": "一项都没勾，没有改动配置"}
    data = [i.as_config() for i in chosen]
    backup = update_section(vault, "interests", data)
    from eia.db import history

    history.record(vault, data, "playground")
    return {"saved": len(chosen), "backup": str(backup)}


def action_digest_run(params: dict[str, Any]) -> dict[str, Any]:
    return run_cli("digest", "run", "--vault", _vault(params))


def action_digest_picks(params: dict[str, Any]) -> dict[str, Any]:
    """今天会挑出的报道（预览，不写文件），附上已有的评价。"""
    from eia.headlines.urls import url_key
    from eia.interests.config import InterestsError
    from eia.interests.digest import run_digest
    from eia.interests.feedback import read_feedback

    vault = Path(_vault(params))
    try:
        result = run_digest(vault, dry_run=True)
    except (InterestsError, llm.LLMError) as exc:
        return {"error": str(exc)}
    rated = {url_key(f.url): f for f in read_feedback(vault)}
    picks = []
    for pick in result.picks:
        fb = rated.get(url_key(pick.item.url))
        picks.append(
            {
                "url": pick.item.url,
                "title": pick.item.title,
                "publisher": pick.item.publisher,
                "interest": pick.interest,
                "score": pick.score,
                "published": pick.item.published.isoformat(),
                "relevant": fb.relevant if fb else None,
                "stars": fb.stars if fb else None,
                "why": fb.why if fb else None,
            }
        )
    return {
        "picks": picks,
        "candidates": result.candidates,
        "dropped_feedback": result.dropped_by_feedback,
        "dropped_exclude": result.dropped_by_exclude,
    }


def action_digest_rate(params: dict[str, Any]) -> dict[str, Any]:
    from eia.interests.config import InterestsError
    from eia.interests.feedback import rate

    stars = params.get("stars")
    try:
        record = rate(
            Path(_vault(params)),
            _clean(params.get("url")),
            title=_clean(params.get("title")),
            publisher=_clean(params.get("publisher")),
            interest=_clean(params.get("interest")) or None,
            relevant=params.get("relevant") if isinstance(params.get("relevant"), bool) else None,
            stars=int(stars) if stars not in (None, "", 0) else None,
            why=_clean(params.get("why")) or None,
        )
    except InterestsError as exc:
        return {"error": str(exc)}
    return {"saved": True, "title": record.title}


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


class LocalServer(ThreadingHTTPServer):
    # Windows 上 SO_REUSEADDR 会让两个程序同时占用同一个端口，端口被占用也发现不了
    allow_reuse_address = sys.platform != "win32"


def bind(token: str, port: int, vault: str) -> ThreadingHTTPServer:
    """端口被占用或被 Windows 保留（WinError 10013）时，往后换几个，最后让系统随便给一个。"""
    for candidate in [*range(port, port + 10), 0]:
        try:
            server = LocalServer(("127.0.0.1", candidate), make_handler(token, 0, vault))
        except OSError:
            continue
        actual = server.server_address[1]
        # Host 检查要用真实端口
        server.RequestHandlerClass = make_handler(token, actual, vault)
        if candidate != port:
            print(f"端口 {port} 用不了（被占用或被系统保留），换成了 {actual}")
        return server
    raise OSError("找不到能用的端口")


def serve(vault: str, port: int, open_browser: bool) -> None:
    token = secrets.token_urlsafe(24)
    server = bind(token, port, vault)
    port = server.server_address[1]
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
