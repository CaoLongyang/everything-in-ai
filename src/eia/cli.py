"""命令行入口：eia 的所有子命令（init / validate / import / headlines / clip / run 等）。"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path

import click
import yaml

from eia import llm
from eia.chat import chatgpt, claude, gemini
from eia.chat.importer import ImportResult, import_conversations
from eia.chat.model import ExportFormatError
from eia.chat.reader import ReadChat, ReadMessage, Skipped, iter_chats
from eia.chat.render import SPEAKERS, USER_LABEL
from eia.chat.source import SOURCE_LABELS, SOURCES, Part, label_chat, text_of
from eia.clip import template as clip_template
from eia.db import DatabaseError, database
from eia.db import ops as db_ops
from eia.db.index import search as db_search
from eia.db.index import sync
from eia.headlines import run as headlines
from eia.headlines.config import HeadlinesConfigError
from eia.headlines.store import load_items
from eia.headlines.urls import url_key
from eia.interests import digest as digest_mod
from eia.interests import draft as draft_mod
from eia.interests import feedback as feedback_mod
from eia.interests import learn as learn_mod
from eia.interests.config import InterestsError
from eia.profile import run as profile_mod
from eia.profile import supplement as supplement_mod
from eia.schedule import windows as schedule_windows
from eia.schedule.runs import JOBS, read_runs, run_job
from eia.schedule.windows import ScheduleError, WindowsScheduler
from eia.schema.create import new_entry
from eia.schema.model import EntryType, Platform
from eia.schema.validate import ERROR, validate_vault
from eia.schema.vault import CONFIG_NAME, VaultError, init_vault, is_vault, load_config
from eia.sync import server as sync_server
from eia.sync import store as sync_store

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


@import_group.command(name="gemini")
@click.argument("export", type=_FILE)
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option("--dry-run", is_flag=True, help="只报告会做什么，不写任何文件。")
@click.option(
    "--include-thinking", is_flag=True, help="Gemini 的导出里没有思考过程，这个选项没有效果。"
)
def import_gemini(export: Path, vault: Path, dry_run: bool, include_thinking: bool) -> None:
    """导入浏览器插件导出的 gemini_chats_YYYY-MM-DD.zip（不用解压）。"""
    if include_thinking:
        click.echo("提示  Gemini 的导出里没有思考过程，--include-thinking 没有效果")
    try:
        conversations, notes = gemini.read_export(export)
        # 插件可能某次没加载完：消息比文件里少时不覆盖
        result = import_conversations(vault, conversations, dry_run=dry_run, refuse_fewer=True)
    except (ExportFormatError, VaultError) as exc:
        raise click.ClickException(f"{exc}。没有写入任何文件") from exc
    _report(len(conversations), result, dry_run)
    for note in notes:
        click.echo(f"提示  {note}")


@main.group(name="chats")
def chats_group() -> None:
    """读回 vault 里已导入的对话（只读，不改任何文件）。"""


_DATE = click.DateTime(formats=["%Y-%m-%d"])


@chats_group.command(name="dump")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option("--only-me", is_flag=True, help="只输出我说的话（和我的笔记）。")
@click.option("--no-notes", is_flag=True, help="不输出我在对话文件里写的笔记。")
@click.option(
    "--own-only",
    is_flag=True,
    help="只输出我自己写的段：去掉贴回的 AI 回答、代码、报错、附件（同时只看我的话）。",
)
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
    own_only: bool,
    platform: str | None,
    since: dt.datetime | None,
    until: dt.datetime | None,
    fmt: str,
) -> None:
    """按说话方输出对话。jsonl 每行一条消息，给其他程序用；跳过的文件写到 stderr。

    「我」的消息带每段文字的来源（docs/specs/chat-reader.delta-paste.md）。
    """
    tz = _vault_timezone(vault)
    only_me = only_me or own_only
    window = (since.date() if since else None, until.date() if until else None)
    for item in iter_chats(vault, tz, platform=platform):
        if isinstance(item, Skipped):
            click.echo(f"跳过  {item.path.relative_to(vault).as_posix()}  {item.reason}", err=True)
            continue
        picked: list[tuple[ReadMessage, tuple[Part, ...] | None]] = []
        for message, parts in zip(item.messages, label_chat(item), strict=True):
            if (only_me and message.role != "user") or not _in_window(message.time, window, tz):
                continue
            if own_only and parts is not None:
                own = text_of(parts, sources=("own",))
                if own:
                    picked.append((replace(message, text=own), None))
                continue
            picked.append((message, parts))
        if not no_notes and item.notes and _in_window(item.updated, window, tz):
            picked.append((ReadMessage("note", None, item.notes), None))
        if not picked:
            continue
        if fmt == "jsonl":
            for message, parts in picked:
                click.echo(json.dumps(_record(item, message, parts), ensure_ascii=False))
        else:
            click.echo(f"== {item.title}（{item.platform} · {item.id}）\n")
            for message, parts in picked:
                click.echo(f"### {_label(item, message, tz)}\n\n{_shown(message, parts)}\n")


@chats_group.command(name="stats")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
def chats_stats(vault: Path) -> None:
    """每个平台：对话数、我的消息数和字数、写了笔记的对话数。"""
    tz = _vault_timezone(vault)
    totals: dict[str, list[int]] = {}
    by_source: dict[str, dict[str, int]] = {}
    skipped = 0
    for item in iter_chats(vault, tz):
        if isinstance(item, Skipped):
            skipped += 1
            continue
        name = item.platform or "（未知平台）"
        row = totals.setdefault(name, [0, 0, 0, 0])
        mine = item.user_messages()
        row[0] += 1
        row[1] += len(mine)
        row[2] += sum(len(m.text) for m in mine)
        row[3] += 1 if item.notes else 0
        counted = by_source.setdefault(name, dict.fromkeys(SOURCES, 0))
        for parts in label_chat(item):
            for part in parts or ():
                counted[part.source] += len(part.text.strip())
    for name, (chats, messages, chars, notes) in sorted(totals.items()):
        click.echo(
            f"{name}：{chats} 个对话，我的消息 {messages} 条、{chars} 字，{notes} 个对话写了笔记"
        )
        counted = by_source[name]
        click.echo("  按来源：" + "，".join(f"{SOURCE_LABELS[s]} {counted[s]} 字" for s in SOURCES))
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


def _shown(message: ReadMessage, parts: tuple[Part, ...] | None) -> str:
    """text 格式：不是我写的段前面加一行来源标签。"""
    if parts is None or all(part.source == "own" for part in parts):
        return message.text
    shown = []
    for part in parts:
        text = part.text.strip()
        if text:
            label = "" if part.source == "own" else f"〔{SOURCE_LABELS[part.source]}〕\n"
            shown.append(label + text)
    return "\n\n".join(shown)


def _record(
    chat: ReadChat, message: ReadMessage, parts: tuple[Part, ...] | None = None
) -> dict[str, object]:
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
    if parts is not None:
        record["parts"] = [
            {"source": part.source, "text": part.text.strip()}
            for part in parts
            if part.text.strip()
        ]
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
    queries = [status.feed.query for status in result.feeds if status.feed.query]
    if queries:
        click.echo(f"按兴趣搜 Google News 的关键词：{'；'.join(queries)}")
    failed = [status for status in result.feeds if status.error]
    for status in failed:
        click.echo(f"源失败  {status.feed.name}  {status.feed.url}  {status.error}")
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
        where = f"{status.feed.name}  {status.feed.url}"
        if status.error:
            failed = True
            click.echo(f"失败  {where}  {status.error}")
            continue
        if not status.items:  # 搜索没搜到东西是正常的
            click.echo(f"正常  {where}  0 条")
            continue
        newest = max(item.published for item in status.items)
        age = (now - newest).total_seconds() / 3600
        stale = "，超过 7 天没更新" if age > 24 * 7 else ""
        click.echo(f"正常  {where}  {len(status.items)} 条，最新一条 {age:.0f} 小时前{stale}")
    if failed:
        sys.exit(1)


@headlines_group.command(name="catalog")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
def headlines_catalog(vault: Path) -> None:
    """列出 eia 自带的推荐源，标出已经在用的。不访问网络。"""
    from eia.headlines.catalog import load_catalog, source_key

    in_use: set[str] = set()
    with contextlib.suppress(OSError, yaml.YAMLError, AttributeError):
        data = yaml.safe_load((vault / CONFIG_NAME).read_bytes().decode("utf-8")) or {}
        for item in (data.get("headlines") or {}).get("feeds") or []:
            if isinstance(item, dict):
                in_use.add(source_key(item))
    checked, groups = load_catalog()
    click.echo(
        f"推荐源目录（{checked} 实测可用）。"
        "复制 {...} 那一段到 config.yaml 的 headlines.feeds 下。"
    )
    for group in groups:
        click.echo(f"\n## {group.name}" + (f"（{group.note}）" if group.note else ""))
        for source in group.sources:
            mark = "已在用" if source.key in in_use else "      "
            snippet = yaml.safe_dump(
                source.config,
                default_flow_style=True,
                allow_unicode=True,
                sort_keys=False,
                width=500,
            ).strip()
            click.echo(f"  {mark}  {source.feed.name:24}  {snippet}")


@main.group(name="interests")
def interests_group() -> None:
    """兴趣清单：从对话生成草稿、确认、根据评价改进。"""


def _interests_error(exc: Exception) -> click.ClickException:
    return click.ClickException(str(exc))


@interests_group.command(name="draft")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option("--max", "max_interests", type=click.IntRange(1, 50), default=20, show_default=True)
@click.option(
    "--yes", is_flag=True, help="确认把摘录发给文本模型起名。不加就只在本机归组并告诉你要发什么。"
)
def interests_draft(vault: Path, max_interests: int, yes: bool) -> None:
    """从你说过的话里归纳兴趣草稿，写到 interests/draft.md。"""
    try:
        plan = draft_mod.plan_draft(vault, max_interests=max_interests)
        click.echo(f"本机归组完成：{len(plan.groups)} 组（只用了你说的话，没有发出任何内容）。")
        click.echo(
            f"下一步要把 {plan.excerpt_count} 段摘录、共 {plan.excerpt_chars} 字发给 "
            f"{plan.model}（{plan.service}）起名。"
        )
        if not yes:
            click.echo("确认的话加上 --yes 再运行一次；不加就到此为止。")
            return
        items = draft_mod.name_groups(vault, plan, max_interests=max_interests)
        items = draft_mod.merge_with_current(vault, items)
        path = draft_mod.write_draft(vault, items, heading="## 从你的对话里归纳的兴趣")
    except (InterestsError, llm.LLMError, VaultError) as exc:
        raise _interests_error(exc) from exc
    click.echo(f"已写草稿  {path.relative_to(vault).as_posix()}（{len(items)} 项）")
    click.echo("在 Obsidian 或试用页面里勾选、修改，然后运行 eia interests accept")


@interests_group.command(name="accept")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
def interests_accept(vault: Path) -> None:
    """把草稿里勾上的项写进 config.yaml（整份替换兴趣清单，旧配置会备份）。"""
    try:
        chosen = draft_mod.accept(vault)
    except (InterestsError, VaultError) as exc:
        raise _interests_error(exc) from exc
    click.echo(f"兴趣清单已更新，共 {len(chosen)} 项：" + "、".join(i.name for i in chosen))


@interests_group.command(name="learn")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option("--yes", is_flag=True, help="确认把兴趣清单和评价发给文本模型。")
def interests_learn(vault: Path, yes: bool) -> None:
    """根据你的评价，让 AI 提出兴趣清单的改进建议（写进草稿，确认后才生效）。"""
    try:
        plan = learn_mod.plan_learn(vault)
        click.echo(
            f"要把兴趣清单和 {plan.feedback} 条评价（报道标题、你写的理由）共 {plan.chars} 字"
            f"发给 {plan.model}（{plan.service}）。"
        )
        if not yes:
            click.echo("确认的话加上 --yes 再运行一次；不加就到此为止。")
            return
        items = learn_mod.learn(vault, plan)
        path = draft_mod.write_draft(vault, items, heading="## 根据你的评价提出的修改建议")
    except (InterestsError, llm.LLMError, VaultError) as exc:
        raise _interests_error(exc) from exc
    changes = [i for i in items if i.change != "保留"]
    click.echo(f"已写草稿  {path.relative_to(vault).as_posix()}：{len(changes)} 处建议修改")
    click.echo("看过后运行 eia interests accept 才会生效")


@main.group(name="profile")
def profile_group() -> None:
    """个人画像：AI 读你说过的话，写「数字化的你」（docs/specs/profile.md）。"""


@profile_group.command(name="run")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option("--since", type=_DATE, help="从这天起（含），按消息时间。默认最近 3 个月。")
@click.option("--until", type=_DATE, help="到这天为止（含），默认今天。")
@click.option(
    "--yes", is_flag=True, help="确认把材料发给文本模型。不加就只在本机整理并告诉你要发多少。"
)
def profile_run(
    vault: Path, since: dt.datetime | None, until: dt.datetime | None, yes: bool
) -> None:
    """生成一份新的画像，写到 profile/（不覆盖旧版本）。"""
    try:
        plan = profile_mod.plan_profile(
            vault,
            since=since.date() if since else None,
            until=until.date() if until else None,
        )
        click.echo(
            f"范围 {plan.since} ~ {plan.until}：{len(plan.materials)} 个对话，"
            f"你的材料 {plan.chars} 字（只有你写的、笔记和贴回的 AI 回答；代码、报错、附件不发）。"
        )
        if plan.skipped_files:
            click.echo(
                f"跳过 {plan.skipped_files} 个读不了的对话文件（用 eia chats dump 查看原因）"
            )
        reused = len(plan.materials) - len(plan.to_send)
        if reused:
            click.echo(f"其中 {reused} 个对话以前分析过、内容没变，不用再发。")
        click.echo(
            f"第 2 步要把 {len(plan.to_send)} 个对话、{plan.send_chars} 字发给 "
            f"{plan.model}（{plan.service}）；第 3 步只发第 2 步得到的观察和原话摘录。"
        )
        if not yes:
            click.echo("确认的话加上 --yes 再运行一次；不加就到此为止，什么都没有发出。")
            return
        result = profile_mod.run_profile(
            vault, plan, progress=lambda n, total: click.echo(f"  第 2 步：第 {n}/{total} 批")
        )
    except (profile_mod.ProfileError, llm.LLMError, VaultError, DatabaseError) as exc:
        raise click.ClickException(str(exc)) from exc
    counts = "，".join(f"{title} {n} 条" for title, n in result.counts.items())
    labels = " · ".join(result.profile.labels)
    click.echo(f"已写画像  {result.path.relative_to(vault).as_posix()}")
    click.echo(f"你：{labels}")
    click.echo(f"要点：{counts}")
    click.echo(f"共 {result.observations} 条观察；原话对不上、被丢掉的 {result.dropped} 条")
    if result.rejected:
        click.echo(f"避开了你说过不像你的 {result.rejected} 条结论")
    if result.skipped:
        names = "、".join(result.skipped[:5]) + ("等" if len(result.skipped) > 5 else "")
        click.echo(
            f"有 {len(result.skipped)} 个对话这次没分析成（AI 的回复格式不对，重试也没用）："
            f"{names}。没算进这份画像，下次运行会重试"
        )


@profile_group.command(name="ask")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option(
    "--yes", is_flag=True, help="确认把画像的结论发给文本模型出题。不加就只告诉你要发多少。"
)
def profile_ask(vault: Path, yes: bool) -> None:
    """根据最新画像里空着、不确定的地方出访谈问题，写到 profile/访谈 <日期>.md，你来回答。"""
    try:
        plan = supplement_mod.plan_ask(vault)
        click.echo(
            f"要把「{plan.profile.stem}」的结论共 {plan.chars} 字"
            f"发给 {plan.model}（{plan.service}）出访谈问题。只发结论，不发你的对话和原话。"
        )
        if not yes:
            click.echo("确认的话加上 --yes 再运行一次；不加就到此为止，什么都没有发出。")
            return
        path, count = supplement_mod.run_ask(vault, plan)
    except (supplement_mod.ProfileAskError, llm.LLMError, VaultError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"已写访谈  {path.relative_to(vault).as_posix()}：{count} 个问题")
    click.echo("在 Obsidian 里打开它，把回答写在「你的回答：」后面；下次生成画像时就会用上")


@main.group(name="digest")
def digest_group() -> None:
    """按兴趣挑选资讯，写进 inbox/digest/。"""


@digest_group.command(name="run")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option("--dry-run", is_flag=True, help="只报告会挑出什么，不写文件。")
def digest_run(vault: Path, dry_run: bool) -> None:
    """用确认过的兴趣给最近的报道打分（本机计算，不花钱）。"""
    try:
        result = digest_mod.run_digest(vault, dry_run=dry_run)
    except (InterestsError, VaultError) as exc:
        raise _interests_error(exc) from exc
    except llm.LLMError as exc:
        raise click.ClickException(
            f"向量模型调用失败：{exc}。如果用的是本地 Ollama，请确认它已经启动"
        ) from exc
    click.echo(_digest_summary(result, dry_run))
    for pick in result.picks:
        click.echo(
            f"  {pick.score:.2f}  [{pick.interest}]  {pick.item.title}（{pick.item.publisher}）"
        )


def _digest_summary(result: digest_mod.DigestResult, dry_run: bool) -> str:
    prefix = "（预览，未写入）" if dry_run else ""
    where = f"，{'新建' if result.created else '更新'} {result.path}" if result.path else ""
    return (
        f"{prefix}{result.candidates} 篇候选，挑出 {len(result.picks)} 篇"
        f"（因评价排除 {result.dropped_by_feedback}，"
        f"因「不要」排除 {result.dropped_by_exclude}）{where}"
    )


@digest_group.command(name="rate")
@click.argument("url")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option("--relevant/--irrelevant", default=None, help="相关 / 不相关。")
@click.option("--stars", type=click.IntRange(1, 5), help="1–5 星。")
@click.option("--why", help="一句理由，例如「只关心芯片，不关心手机」。")
def digest_rate(
    url: str, vault: Path, relevant: bool | None, stars: int | None, why: str | None
) -> None:
    """评价一篇报道。三种方式可以任选、可以组合；同一篇再评会覆盖。"""
    item = next((i for i in load_items(vault) if url_key(i.url) == url_key(url)), None)
    try:
        record = feedback_mod.rate(
            vault,
            url,
            title=item.title if item else "",
            publisher=item.publisher if item else "",
            relevant=relevant,
            stars=stars,
            why=why,
        )
    except InterestsError as exc:
        raise _interests_error(exc) from exc
    click.echo(f"已记下  {record.title}")


@main.group(name="db")
def db_group() -> None:
    """vault 的数据库（.eia/eia.db）：索引、向量、报道、运行记录、评价。"""


def _db_report(state: db_ops.Status) -> None:
    labels = {
        "entries": "条目索引",
        "entry_tags": "标签",
        "items": "抓到的报道",
        "vectors": "向量",
        "runs": "运行记录",
        "shown": "已看过的报道",
        "feedback": "评价",
        "interest_history": "兴趣历史",
        "profile_observations": "画像观察",
    }
    click.echo(
        f"数据库 {state.size_bytes / 1_000_000:.1f} MB，表结构版本 {state.schema_version}，"
        f"全文搜索{'可用' if state.fts else '不可用（SQLite 版本太低）'}"
    )
    for table, count in state.counts.items():
        click.echo(f"  {labels[table]:<8} {count}")
    if state.index is not None:
        i = state.index
        click.echo(
            f"索引已更新：新增 {i.added}，修改 {i.changed}，删除 {i.removed}，未变 {i.unchanged}"
        )
    if state.legacy:
        moved = {k: v for k, v in state.legacy.items() if k != "at" and v}
        if moved:
            text = "、".join(f"{k} {v}" for k, v in moved.items())
            click.echo(f"从旧版本的数据文件迁移过：{text}（旧文件在 .eia/old/）")


@db_group.command(name="status")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
def db_status(vault: Path) -> None:
    """数据库大小、每张表多少行；顺便把索引更新到最新。"""
    if not is_vault(vault):
        raise click.ClickException(f"{vault} 不是一个 vault（找不到 config.yaml）")
    try:
        _db_report(db_ops.status(vault))
    except DatabaseError as exc:
        raise click.ClickException(str(exc)) from exc


@db_group.command(name="rebuild")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option("--yes", is_flag=True, help="确认删掉重建。")
def db_rebuild(vault: Path, yes: bool) -> None:
    """删掉数据库重建：重新索引 Markdown、从 interests/feedback.jsonl 恢复评价。

    抓到的报道和向量会丢，下次运行时重新抓、重新算；运行记录和兴趣历史也会丢。
    """
    if not is_vault(vault):
        raise click.ClickException(f"{vault} 不是一个 vault（找不到 config.yaml）")
    if not yes:
        click.echo(
            "会删掉 .eia/eia.db 再重建：报道、向量、运行记录、兴趣历史会丢，评价会从导出文件恢复。"
        )
        click.echo("确认的话加上 --yes 再运行一次")
        return
    _db_report(db_ops.rebuild(vault))


@main.command(name="search")
@click.argument("query")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option(
    "--type", "entry_type", type=click.Choice([t.value for t in EntryType]), help="只搜一种条目。"
)
@click.option("--limit", type=click.IntRange(1, 500), default=20, show_default=True)
def search_command(vault: Path, query: str, entry_type: str | None, limit: int) -> None:
    """在 vault 的所有条目里搜全文（标题和正文）。"""
    if not is_vault(vault):
        raise click.ClickException(f"{vault} 不是一个 vault（找不到 config.yaml）")
    try:
        with database(vault) as conn:
            sync(conn, vault)
            hits = db_search(conn, query, entry_type=entry_type, limit=limit)
    except (DatabaseError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    if not hits:
        click.echo("没有找到")
    for hit in hits:
        click.echo(f"{hit.path}  {hit.title}\n    {hit.snippet}")


@main.group(name="clip")
def clip_group() -> None:
    """网页剪藏：给 Obsidian Web Clipper 用的模板。"""


@clip_group.command(name="template")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    help=f"模板保存到哪里，默认 vault 里的 {clip_template.DEFAULT_FILE}。",
)
def clip_template_command(vault: Path, out: Path | None) -> None:
    """按 vault 的时区生成插件模板；在插件里导入一次，剪下的网页就直接合规。"""
    if not is_vault(vault):
        raise click.ClickException(f"{vault} 不是一个 vault（找不到 config.yaml）")
    try:
        result = clip_template.generate(vault)
    except (clip_template.ClipError, VaultError) as exc:
        raise click.ClickException(str(exc)) from exc
    path = out or vault / clip_template.DEFAULT_FILE
    clip_template.write_template(result, path)
    sign = "+" if result.offset_hours >= 0 else "-"
    click.echo(f"已生成  {path}（时区 {result.zone}，UTC{sign}{abs(result.offset_hours)}）")
    click.echo("导入：插件设置 → 模板 → 导入，选这个文件；再在插件设置里把 vault 选成这个 vault。")
    click.echo(f"剪下的网页会存到 {result.template['path']}/。")
    if result.has_dst:
        click.echo(
            "提醒  这个时区有夏令时，模板里的时差是写死的；换季后重新运行这个命令并再导入一次。"
        )


@main.group(name="sync")
def sync_group() -> None:
    """接收浏览器插件每天同步的 ChatGPT、Claude 对话（docs/specs/chat-sync.md）。"""


def _sync_vault(vault: Path) -> None:
    if not is_vault(vault):
        raise click.ClickException(f"{vault} 不是一个 vault（找不到 config.yaml）")


@sync_group.command(name="serve")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option(
    "--port", type=int, default=sync_server.DEFAULT_PORT, show_default=True, help="插件里填同一个"
)
def sync_serve(vault: Path, port: int) -> None:
    """在本机运行接收服务（只听 127.0.0.1），Ctrl+C 停止。"""
    _sync_vault(vault)
    try:
        server = sync_server.make_server(vault, port=port)
    except OSError as exc:
        raise click.ClickException(
            f"端口 {port} 用不了（{exc.strerror or exc}）：可能已经有一个 eia sync serve 在运行，"
            "或者换一个端口（--port），插件里也改成同一个"
        ) from exc
    click.echo(f"在 http://127.0.0.1:{port} 等插件发对话来，写进 {vault.resolve()}")
    click.echo("插件要填的口令：eia sync token；按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        click.echo("已停止")
    finally:
        server.server_close()


@sync_group.command(name="token")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
def sync_token(vault: Path) -> None:
    """显示插件要填的口令（第一次运行时生成）。不要发给别人。"""
    _sync_vault(vault)
    click.echo(sync_store.token(vault))


@sync_group.command(name="status")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option("--limit", type=int, default=30, show_default=True, help="显示最近几条。")
def sync_status(vault: Path, limit: int) -> None:
    """最近同步了哪些对话、有没有失败（只有平台、编号、消息数和结果）。"""
    _sync_vault(vault)
    try:
        lines = sync_store.recent(vault, limit)
    except DatabaseError as exc:
        raise click.ClickException(str(exc)) from exc
    if not lines:
        click.echo("还没有同步过。装好插件、运行 eia sync serve 后，插件每天会同步一次")
        return
    for line in lines:
        mark = "  " if line.ok else "✗ "
        why = f"（{line.error}）" if line.error else ""
        click.echo(f"{mark}{line.started}  {line.summary}{why}")


@main.command(name="run")
@click.argument("job", type=click.Choice(sorted(JOBS)))
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
def run_command(job: str, vault: Path) -> None:
    """跑一次 JOB 并写运行记录。定时任务调用的就是这个命令。"""
    if not is_vault(vault):
        raise click.ClickException(f"{vault} 不是一个 vault（找不到 config.yaml）")
    record = run_job(vault, job)
    detail = f"：{record.error}" if record.error else ""
    click.echo(
        f"{'成功' if record.ok else '失败'}  {job}  {record.seconds} 秒  {record.summary}{detail}"
    )
    if not record.ok:
        sys.exit(1)


@main.group(name="schedule")
def schedule_group() -> None:
    """在系统的定时工具里登记 eia run（V1 只支持 Windows 任务计划程序）。"""


def _windows(job: str, vault: Path) -> WindowsScheduler:
    if not schedule_windows.supported():
        click.echo(schedule_windows.manual_hint(job, vault), err=True)
        sys.exit(1)
    return WindowsScheduler()


@schedule_group.command(name="install")
@click.argument("job", type=click.Choice(sorted(JOBS)))
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
@click.option("--at", "times", multiple=True, help="每天几点跑，如 08:00；可以写多次。默认 08:00。")
def schedule_install(job: str, vault: Path, times: tuple[str, ...]) -> None:
    """登记每天定时运行。已登记的会被更新。错过的时间开机后补跑。"""
    if not is_vault(vault):
        raise click.ClickException(f"{vault} 不是一个 vault（找不到 config.yaml）")
    windows = _windows(job, vault)
    try:
        name = windows.install(job, vault, times)
    except ScheduleError as exc:
        raise click.ClickException(str(exc)) from exc
    at = "、".join(f"{h:02d}:{m:02d}" for h, m in schedule_windows.parse_times(times))
    click.echo(f"已登记  {name}  每天 {at}（错过了开机后补跑；需要联网；不弹窗口）")
    click.echo(f'可以先手动跑一次试试：eia run {job} --vault "{vault.resolve()}"')


@schedule_group.command(name="remove")
@click.argument("job", type=click.Choice(sorted(JOBS)))
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
def schedule_remove(job: str, vault: Path) -> None:
    """删除这个 vault 的定时任务。运行记录保留。"""
    try:
        name = _windows(job, vault).remove(job, vault)
    except ScheduleError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"已删除  {name}")


@schedule_group.command(name="list")
def schedule_list() -> None:
    """列出 eia 在这台电脑上登记的所有定时任务。"""
    try:
        tasks = _windows("headlines", Path(".")).tasks()
    except ScheduleError as exc:
        raise click.ClickException(str(exc)) from exc
    if not tasks:
        click.echo("还没有登记任何定时任务。用 eia schedule install headlines --vault <路径> 登记")
    for task in tasks:
        warn = "" if task.vault_exists else "  ⚠ vault 已经不存在，可以用 eia schedule remove 删掉"
        click.echo(f"{task.job}  {task.vault}  下次 {task.next_run}  上次 {task.last_run}{warn}")


@schedule_group.command(name="status")
@click.option("--vault", type=_DIR, default=".", help="vault 所在目录，默认当前目录。")
def schedule_status(vault: Path) -> None:
    """这个 vault 的每个任务：是否已登记、下次运行时间、最近 5 次结果。"""
    for job in sorted(JOBS):
        task = None
        if schedule_windows.supported():
            try:
                task = WindowsScheduler().find(job, vault)
            except ScheduleError as exc:
                click.echo(f"{job}：查不到定时任务（{exc}）")
        state = f"已登记，下次 {task.next_run}" if task else "没有登记定时运行"
        click.echo(f"[{job}] {state}")
        records = read_runs(vault, job)[-5:]
        if not records:
            click.echo("  还没有运行记录")
        for record in reversed(records):
            mark = "成功" if record.ok else "失败"
            detail = f"：{record.error}" if record.error else ""
            click.echo(f"  {record.started}  {mark}  {record.seconds} 秒  {record.summary}{detail}")


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
