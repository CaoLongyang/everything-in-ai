"""校验 vault：只报告问题，从不修改文件。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_core import ErrorDetails

from eia.schema.io import FrontmatterError, read_document
from eia.schema.model import ID_RE, EntryType, known_fields, model_for
from eia.schema.vault import CONFIG_NAME, GITIGNORE_NAME, IGNORED_DIRS, is_vault

ERROR = "error"
WARNING = "warning"

_MESSAGES = {
    "missing": "缺少必填字段",
    "string_type": "应为字符串",
    "int_type": "应为整数",
    "list_type": "应为列表",
    "date_type": "应为日期，如 2026-11-15",
    "timezone_aware": "时间必须带时区（UTC，以 Z 结尾），如 2026-11-15T10:00:00Z",
    "datetime_type": "应为时间，如 2026-11-15T10:00:00Z",
    "datetime_parsing": "应为时间，如 2026-11-15T10:00:00Z",
    "datetime_from_date_parsing": "应为时间，如 2026-11-15T10:00:00Z",
    "string_pattern_mismatch": "格式应为 <type>-<8位日期>-<6到12位小写字母或2-7>",
    "greater_than_equal": "不能为负数",
}


@dataclass(frozen=True)
class Issue:
    level: str
    path: str
    field: str
    message: str


@dataclass
class Report:
    files_checked: int = 0
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.level == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.level == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors


def _translate(error: ErrorDetails) -> str:
    kind = error["type"]
    if kind == "enum":
        return f"取值不合法，允许的值：{error.get('ctx', {}).get('expected', '')}"
    if kind == "literal_error":
        return f"取值不合法，必须是 {error.get('ctx', {}).get('expected', '')}"
    if kind == "value_error":
        return str(error["msg"]).removeprefix("Value error, ")
    return _MESSAGES.get(kind, str(error["msg"]))


def check_meta(meta: dict[str, Any]) -> list[tuple[str, str, str]]:
    """检查一个条目的 frontmatter，返回 (级别, 字段, 说明) 列表。"""
    found: list[tuple[str, str, str]] = []
    model = model_for(meta.get("type"))
    try:
        model.model_validate(meta)
    except ValidationError as exc:
        for error in exc.errors():
            name = ".".join(str(part) for part in error["loc"])
            found.append((ERROR, name, _translate(error)))
    entry_id, entry_type = meta.get("id"), meta.get("type")
    types = {item.value for item in EntryType}
    if isinstance(entry_id, str) and ID_RE.match(entry_id) and entry_type in types:
        if not entry_id.startswith(f"{entry_type}-"):
            found.append((ERROR, "id", f"id 的前缀与 type「{entry_type}」不一致"))
    for key in meta:
        if key not in known_fields(model):
            found.append((WARNING, key, "未知字段（已保留，不影响使用）"))
    return found


def iter_markdown(root: Path) -> Iterator[Path]:
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in IGNORED_DIRS)
        for name in sorted(files):
            if name.lower().endswith(".md"):
                yield Path(current) / name


def _check_gitignore(root: Path) -> list[Issue]:
    target = root / GITIGNORE_NAME
    hint = "必须包含一行 .env，防止 API key 被提交到 Git"
    if not target.is_file():
        return [Issue(ERROR, GITIGNORE_NAME, "", f"文件不存在；{hint}")]
    lines = {line.strip() for line in target.read_text(encoding="utf-8").splitlines()}
    if not lines & {".env", "/.env"}:
        return [Issue(ERROR, GITIGNORE_NAME, "", hint)]
    return []


def _duplicates(seen: dict[Any, list[str]], field_name: str, label: str) -> list[Issue]:
    issues: list[Issue] = []
    for key, paths in seen.items():
        if len(paths) > 1:
            for path in paths:
                others = "、".join(other for other in paths if other != path)
                issues.append(Issue(ERROR, path, field_name, f"{label}重复：{key}，另见 {others}"))
    return issues


def validate_vault(root: Path) -> Report:
    report = Report()
    if not is_vault(root):
        message = "找不到 config.yaml：这不是一个 vault，请先运行 eia init"
        report.issues.append(Issue(ERROR, CONFIG_NAME, "", message))
        return report
    report.issues.extend(_check_gitignore(root))
    ids: dict[str, list[str]] = {}
    conversations: dict[str, list[str]] = {}
    for path in iter_markdown(root):
        report.files_checked += 1
        rel = path.relative_to(root).as_posix()
        try:
            meta = read_document(path).meta
        except FrontmatterError as exc:
            report.issues.append(Issue(ERROR, rel, "frontmatter", str(exc)))
            continue
        for level, name, message in check_meta(meta):
            report.issues.append(Issue(level, rel, name, message))
        entry_id = meta.get("id")
        if isinstance(entry_id, str):
            ids.setdefault(entry_id, []).append(rel)
        platform, conversation = meta.get("platform"), meta.get("conversation_id")
        if meta.get("type") == EntryType.chat.value and platform and conversation:
            conversations.setdefault(f"{platform} / {conversation}", []).append(rel)
    report.issues.extend(_duplicates(ids, "id", "id "))
    report.issues.extend(_duplicates(conversations, "conversation_id", "同一个对话"))
    return report
