"""命令行入口：eia init / validate / new / import / chats / headlines / llm。"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

import click

from eia import llm
from eia.chat import chatgpt, claude
from eia.chat.importer import ImportResult, import_conversations
from eia.chat.model import ExportFormatError
from eia.chat.reader import ReadChat, ReadMessage, Skipped, iter_chats
from eia.chat.render import SPEAKERS, USER_LABEL
from eia.headlines import run as headlines
from eia.headlines.config import HeadlinesConfigError
from eia.schema.create import new_entry
from eia.schema.model import EntryType, Platform
from eia.schema.validate import ERROR, validate_vault
from eia.schema.vault import VaultError, init_vault, load_config

_DIR = click.Path(file_okay=False, path_type=Path)


def _tolerant_output() -> None:
    """终端编码显示不了某个字符时用 ? 代替，而不是让命令崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


@click.group()
def main() -> None:
    """everything-in-ai：纯 Markdown 的个人知识库工具。"""
    _tolerant_output()


@main.command()
@click.argument("path", type=_DIR)
def init(path: Path) -> None:
    """在 PATH 创建 vault 的目录结构和配置模板。已有文件不会被改动。"""
    result = init_vault(path)
    for name in result.created:
        click.echo(f"已创建  {name}")
    for name in result.skipped:
        click.echo(f"已存在，跳过  {name}")
    click.echo(f"完成：{path}")


@main.command()
@click.argument("path", type=_DIR, default=".")
@click.option("--json", "as_json", is_flag=True, help="输出机器可读的 JSON。")
def validate(path: Path, as_json: bool) -> None:
    """检查 vault 里每个 Markdown 文件是否符合格式。只报告，不修改。"""
    report = validate_vault(path)
    if as_json:
        payload = {
            "files_checked": report.files_checked,
            "errors": [asdict(issue) for issue in report.errors],
            "warnings": [asdict(issue) for issue in report.warnings],
        }
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for issue in report.issues:
            label = "错误" if issue.level == ERROR else "警告"
            where = f"  [{issue.field}]" if issue.field else ""
            click.echo(f"{label}  {issue.path}{where}  {issue.message}")
        summary = f"{len(report.errors)} 个错误，{len(report.warnings)} 个警告"
        click.echo(f"检查了 {report.files_checked} 个文件：{summary}")
    if not report.ok:
        sys.exit(1)


@main.command()
@click.argument("entry_type", metavar="TYPE", type=click.Choice([t.value for t in EntryType]))
@click.option("--title", required=True, help="条目标题。")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option("--platform", type=click.Choice([p.value for p in Platform]), help="仅 chat 需要。")
@click.option("--conversation-id", help="仅 chat 需要。")
@click.option("--url", help="来源链接（可选）。")
def new(
    entry_type: str,
    title: str,
    vault: Path,
    platform: str | None,
    conversation_id: str | None,
    url: str | None,
) -> None:
    """新建一个带合法 frontmatter 的空条目。"""
    try:
        path = new_entry(
            vault, entry_type, title, platform=platform, conversation_id=conversation_id, url=url
        )
    except VaultError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"已创建  {path}")


_FILE = click.Path(exists=True, dir_okay=False, path_type=Path)


@main.group(name="import")
def import_group() -> None:
    """从平台的官方导出文件导入对话。"""


@import_group.command(name="chatgpt")
@click.argument("export", type=_FILE)
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option("--dry-run", is_flag=True, help="只报告会做什么，不写任何文件。")
def import_chatgpt(export: Path, vault: Path, dry_run: bool) -> None:
    """导入 ChatGPT 的「导出数据」zip（不用解压）。"""
    try:
        conversations, notes = chatgpt.read_export(export)
        for note in notes:
            click.echo(f"提示  {note}")
        result = import_conversations(vault, conversations, dry_run=dry_run)
    except (ExportFormatError, VaultError) as exc:
        raise click.ClickException(f"{exc}。没有写入任何文件") from exc
    _report(len(conversations), result, dry_run)


@import_group.command(name="claude")
@click.argument("exports", nargs=-1, required=True, type=_FILE)
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option("--dry-run", is_flag=True, help="只报告会做什么，不写任何文件。")
@click.option("--include-thinking", is_flag=True, help="把 Claude 的思考过程以折叠块导入。")
def import_claude(
    exports: tuple[Path, ...], vault: Path, dry_run: bool, include_thinking: bool
) -> None:
    """导入 Claude 导出里的 conversations-NNN.zip（不用解压，可以传多个）。"""
    try:
        conversations, notes = claude.read_exports(exports, include_thinking=include_thinking)
        for note in notes:
            click.echo(f"提示  {note}")
        result = import_conversations(vault, conversations, dry_run=dry_run)
    except (ExportFormatError, VaultError) as exc:
        raise click.ClickException(f"{exc}。没有写入任何文件") from exc
    _report(len(conversations), result, dry_run)


@main.group(name="chats")
def chats_group() -> None:
    """读回 vault 里已导入的对话（只读，不改任何文件）。"""


_DATE = click.DateTime(formats=["%Y-%m-%d"])


@chats_group.command(name="dump")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option("--only-me", is_flag=True, help="只输出我说的话（和我的笔记）。")
@click.option("--no-notes", is_flag=True, help="不输出我在对话文件里写的笔记。")
@click.option("--platform", type=click.Choice([p.value for p in Platform]), help="只看一个平台。")
@click.option("--since", type=_DATE, help="从这天起（含），按消息时间，格式 2026-01-31。")
@click.option("--until", type=_DATE, help="到这天为止（含）。")
@click.option(
    "--format", "fmt", type=click.Choice(["text", "jsonl"]), default="text", show_default=True
)
def chats_dump(
    vault: Path,
    only_me: bool,
    no_notes: bool,
    platform: str | None,
    since: dt.datetime | None,
    until: dt.datetime | None,
    fmt: str,
) -> None:
    """按说话方输出对话。jsonl 每行一条消息，给其他程序用；跳过的文件写到 stderr。"""
    tz = _vault_timezone(vault)
    window = (since.date() if since else None, until.date() if until else None)
    for item in iter_chats(vault, tz, platform=platform):
        if isinstance(item, Skipped):
            click.echo(f"跳过  {item.path.relative_to(vault).as_posix()}  {item.reason}", err=True)
            continue
        picked = [
            m
            for m in item.messages
            if (not only_me or m.role == "user") and _in_window(m.time, window, tz)
        ]
        if not no_notes and item.notes and _in_window(item.updated, window, tz):
            picked.append(ReadMessage("note", None, item.notes))
        if not picked:
            continue
        if fmt == "jsonl":
            for message in picked:
                click.echo(json.dumps(_record(item, message), ensure_ascii=False))
        else:
            click.echo(f"== {item.title}（{item.platform} · {item.id}）\n")
            for message in picked:
                click.echo(f"### {_label(item, message, tz)}\n\n{message.text}\n")


@chats_group.command(name="stats")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
def chats_stats(vault: Path) -> None:
    """每个平台：对话数、我的消息数和字数、写了笔记的对话数。"""
    tz = _vault_timezone(vault)
    totals: dict[str, list[int]] = {}
    skipped = 0
    for item in iter_chats(vault, tz):
        if isinstance(item, Skipped):
            skipped += 1
            continue
        row = totals.setdefault(item.platform or "（未知平台）", [0, 0, 0, 0])
        mine = item.user_messages()
        row[0] += 1
        row[1] += len(mine)
        row[2] += sum(len(m.text) for m in mine)
        row[3] += 1 if item.notes else 0
    for name, (chats, messages, chars, notes) in sorted(totals.items()):
        click.echo(
            f"{name}：{chats} 个对话，我的消息 {messages} 条、{chars} 字，{notes} 个对话写了笔记"
        )
    if not totals:
        click.echo("没有找到对话")
    if skipped:
        click.echo(f"跳过 {skipped} 个读不了的对话文件（用 eia chats dump 查看原因）")


def _vault_timezone(vault: Path) -> dt.tzinfo | None:
    try:
        return load_config(vault).timezone
    except VaultError as exc:
        raise click.ClickException(str(exc)) from exc


def _in_window(
    when: dt.datetime | None,
    window: tuple[dt.date | None, dt.date | None],
    tz: dt.tzinfo | None,
) -> bool:
    since, until = window
    if since is None and until is None:
        return True
    if when is None:
        return False
    day = when.astimezone(tz).date()
    return (since is None or day >= since) and (until is None or day <= until)


def _label(chat: ReadChat, message: ReadMessage, tz: dt.tzinfo | None) -> str:
    if message.role == "note":
        return "我的笔记"
    who = USER_LABEL if message.role == "user" else SPEAKERS.get(chat.platform, chat.platform)
    when = message.time.astimezone(tz).strftime("%Y-%m-%d %H:%M") if message.time else "时间未知"
    return f"{who} · {when}"


def _record(chat: ReadChat, message: ReadMessage) -> dict[str, object]:
    record: dict[str, object] = {
        "id": chat.id,
        "platform": chat.platform,
        "title": chat.title,
        "role": message.role,
        "time": message.time.isoformat() if message.time else None,
        "text": message.text,
    }
    if message.thinking:
        record["thinking"] = message.thinking
    return record


@main.group(name="headlines")
def headlines_group() -> None:
    """新闻雷达：多家独立媒体都在报的事，写进 inbox/headlines/。"""


@headlines_group.command(name="run")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option("--dry-run", is_flag=True, help="只报告会做什么，不写 vault，也不写 .eia/ 缓存。")
def headlines_run(vault: Path, dry_run: bool) -> None:
    """抓取 → 归组 → 达到门槛的写成 headline 条目。"""
    try:
        result = headlines.run(vault, dry_run=dry_run)
    except (HeadlinesConfigError, VaultError) as exc:
        raise click.ClickException(f"{exc}。没有访问任何新闻源") from exc
    except llm.LLMError as exc:
        raise click.ClickException(
            f"向量模型调用失败：{exc}。如果用的是本地 Ollama，请确认它已经启动。没有写入任何条目"
        ) from exc
    failed = [status for status in result.feeds if status.error]
    for status in failed:
        click.echo(f"源失败  {status.feed.publisher}  {status.feed.url}  {status.error}")
    written = result.written
    for path, reason in written.skipped:
        click.echo(f"跳过  {path}  {reason}")
    prefix = "（预览，未写入）" if dry_run else ""
    click.echo(
        f"{prefix}{len(result.feeds) - len(failed)}/{len(result.feeds)} 个源可用；"
        f"时间窗口内 {result.in_window} 篇报道，{result.groups} 组，"
        f"{result.qualifying} 组达到门槛：\n"
        f"新建 {len(written.created)}，更新 {len(written.updated)}，未变 {written.unchanged}"
    )
    for path in written.created:
        click.echo(f"新建  {path}")
    for path in written.updated:
        click.echo(f"更新  {path}")


@headlines_group.command(name="check")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
def headlines_check(vault: Path) -> None:
    """逐个源测试能否访问、最新一条是什么时候。有源失败时退出码 1。"""
    now = dt.datetime.now(dt.UTC)
    try:
        statuses = headlines.check(vault, now=now)
    except (HeadlinesConfigError, VaultError) as exc:
        raise click.ClickException(f"{exc}。没有访问任何新闻源") from exc
    failed = False
    for status in statuses:
        where = f"{status.feed.publisher}  {status.feed.url}"
        if status.error:
            failed = True
            click.echo(f"失败  {where}  {status.error}")
            continue
        newest = max(item.published for item in status.items)
        age = (now - newest).total_seconds() / 3600
        stale = "，超过 7 天没更新" if age > 24 * 7 else ""
        click.echo(f"正常  {where}  {len(status.items)} 条，最新一条 {age:.0f} 小时前{stale}")
    if failed:
        sys.exit(1)


@main.group(name="llm")
def llm_group() -> None:
    """查看和测试 AI 服务配置（config.yaml 的 llm 段）。"""


@llm_group.command(name="show")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
def llm_show(vault: Path) -> None:
    """打印解析后的配置。key 只显示有没有设置，不显示内容。"""
    try:
        config = llm.load_llm_config(vault, require_key=False)
    except llm.LLMConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    endpoints = [("文本", config.text)]
    if config.embed is not None:
        endpoints.append(("向量", config.embed))
    for label, endpoint in endpoints:
        click.echo(f"[{label}]")
        click.echo(f"  provider     {endpoint.provider}")
        click.echo(f"  model        {endpoint.model}")
        click.echo(f"  api_base     {endpoint.api_base or '（官方默认地址）'}")
        if endpoint.api_key_env:
            state = "已设置" if endpoint.has_key else "未设置"
            click.echo(f"  api_key_env  {endpoint.api_key_env}（{state}）")
        else:
            click.echo("  api_key_env  （不需要 key）")
    if config.embed is None:
        click.echo("[向量]  未配置")
    click.echo(f"max_retries {config.max_retries}，timeout_s {config.timeout_s:g}")


@llm_group.command(name="test")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
def llm_test(vault: Path) -> None:
    """发一个极短的请求，报告延迟和是否成功。会产生一次很小的费用。"""
    try:
        client = llm.get_client(vault)
    except llm.LLMConfigError as exc:
        raise click.ClickException(f"{exc}。没有发出任何请求") from exc
    checks: list[tuple[str, Callable[[], object]]] = [
        ("文本", lambda: client.complete("只回复 OK 两个字母。")),
    ]
    if client.config.embed is not None:
        checks.append(("向量", lambda: client.embed(["测试"])))
    failed = False
    for label, call in checks:
        started = time.monotonic()
        try:
            call()
        except llm.LLMError as exc:
            click.echo(f"失败  {label}：{exc}")
            failed = True
            continue
        click.echo(f"成功  {label}：{(time.monotonic() - started) * 1000:.0f} 毫秒")
    if failed:
        sys.exit(1)


def _report(total: int, result: ImportResult, dry_run: bool) -> None:
    for path, reason in result.skipped:
        click.echo(f"跳过  {path}  {reason}")
    prefix = "（预览，未写入）" if dry_run else ""
    click.echo(
        f"{prefix}读取 {total} 个对话：新建 {len(result.created)}，"
        f"更新 {len(result.updated)}，未变 {result.unchanged}，跳过 {len(result.skipped)}"
    )
