"""改 config.yaml 的某一段，其他行（包括注释）原样保留。

修改前把旧文件备份到 `.eia/config.yaml.bak`，写入用 atomic_write。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from eia.schema.io import atomic_write
from eia.schema.vault import CONFIG_NAME, VaultError

BACKUP = Path(".eia") / "config.yaml.bak"
_TOP_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:")


def _top_key(line: str) -> str | None:
    match = _TOP_KEY.match(line)
    return match.group(1) if match else None


def replace_section(text: str, key: str, section: Any) -> str:
    """替换（section 为 None 时删除；不存在时追加）顶层的 `key:` 段。"""
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if _top_key(line) == key), None)
    block = ""
    if section is not None:
        block = yaml.safe_dump({key: section}, allow_unicode=True, sort_keys=False, width=1000)
    if start is None:
        if not block:
            return text
        if not text or text.endswith("\n\n"):
            sep = ""
        else:
            sep = "\n" if text.endswith("\n") else "\n\n"
        return text + sep + block
    end = start + 1
    # 属于这一段的行：空行、缩进的行，以及顶格写的列表项（YAML 允许 `key:` 下面的 `- x` 不缩进）
    while end < len(lines) and (
        not lines[end].strip() or lines[end][0] in " \t" or lines[end].startswith("-")
    ):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1  # 段后的空行留给下一段
    return "".join(lines[:start]) + block + "".join(lines[end:])


def save_config_text(root: Path, text: str) -> Path:
    """检查是合法的 YAML 映射后保存；返回备份文件路径。"""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise VaultError(f"不是合法的 YAML，没有保存：{exc}") from exc
    if data is not None and not isinstance(data, dict):
        raise VaultError("配置文件最外层必须是 键: 值 的形式，没有保存")
    path = root / CONFIG_NAME
    backup = root / BACKUP
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(path.read_bytes())
    atomic_write(path, text)
    return backup


def update_section(root: Path, key: str, section: Any) -> Path:
    path = root / CONFIG_NAME
    text = path.read_bytes().decode("utf-8")
    return save_config_text(root, replace_section(text, key, section))
