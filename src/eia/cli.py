"""命令行入口：eia init / validate / new / import。"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import click

from eia.chat import chatgpt, claude
from eia.chat.importer import ImportResult, import_conversations
from eia.chat.model import ExportFormatError
from eia.schema.create import new_entry
from eia.schema.model import EntryType, Platform
from eia.schema.validate import ERROR, validate_vault
from eia.schema.vault import VaultError, init_vault

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


def _report(total: int, result: ImportResult, dry_run: bool) -> None:
    for path, reason in result.skipped:
        click.echo(f"跳过  {path}  {reason}")
    prefix = "（预览，未写入）" if dry_run else ""
    click.echo(
        f"{prefix}读取 {total} 个对话：新建 {len(result.created)}，"
        f"更新 {len(result.updated)}，未变 {result.unchanged}，跳过 {len(result.skipped)}"
    )
