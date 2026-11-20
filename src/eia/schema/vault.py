"""vault 的目录结构、初始化和配置读取。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from eia.schema.model import EntryType, Platform

CONFIG_NAME = "config.yaml"
GITIGNORE_NAME = ".gitignore"
IGNORED_DIRS = frozenset({".git", ".eia", ".obsidian", "logseq"})

TYPE_DIRS: dict[str, str] = {
    EntryType.headline.value: "inbox/headlines",
    EntryType.digest.value: "inbox/digest",
    EntryType.clip.value: "inbox/clips",
    EntryType.note.value: "notes",
    EntryType.topic.value: "topics",
}
CHAT_DIR = "chats"

CONFIG_TEMPLATE = """\
# everything-in-ai vault 配置
schema: 1

# 文件名里的日期按这个时区计算；留空则使用本机时区。
# 例：timezone: Asia/Singapore
timezone:
"""

GITIGNORE_TEMPLATE = """\
# API key 等私密配置，永远不进 Git
.env
# 工具自己的缓存和运行记录
.eia/
"""


class VaultError(Exception):
    """vault 不存在或配置有问题。"""


@dataclass
class InitResult:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Config:
    timezone: dt.tzinfo | None


def vault_dirs() -> list[str]:
    chat_dirs = [f"{CHAT_DIR}/{platform.value}" for platform in Platform]
    return [*TYPE_DIRS.values(), *chat_dirs]


def dir_for(entry_type: str, platform: str | None = None) -> str:
    if entry_type == EntryType.chat.value:
        if platform is None:
            raise VaultError("chat 条目需要指定平台")
        return f"{CHAT_DIR}/{platform}"
    return TYPE_DIRS[entry_type]


def init_vault(root: Path) -> InitResult:
    """创建目录和模板文件。已存在的文件一律不动。"""
    result = InitResult()
    root.mkdir(parents=True, exist_ok=True)
    for rel in vault_dirs():
        target = root / rel
        if target.is_dir():
            result.skipped.append(rel + "/")
        else:
            target.mkdir(parents=True)
            result.created.append(rel + "/")
    for name, content in ((CONFIG_NAME, CONFIG_TEMPLATE), (GITIGNORE_NAME, GITIGNORE_TEMPLATE)):
        target = root / name
        if target.exists():
            result.skipped.append(name)
        else:
            target.write_bytes(content.encode("utf-8"))
            result.created.append(name)
    return result


def is_vault(root: Path) -> bool:
    return (root / CONFIG_NAME).is_file()


def load_config(root: Path) -> Config:
    if not is_vault(root):
        raise VaultError(f"{root} 不是一个 vault（找不到 {CONFIG_NAME}），请先运行 eia init")
    try:
        data = yaml.safe_load((root / CONFIG_NAME).read_bytes().decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise VaultError(f"{CONFIG_NAME} 无法读取：{exc}") from exc
    name = (data or {}).get("timezone") if isinstance(data or {}, dict) else None
    if name is None or name == "":
        return Config(timezone=None)
    try:
        return Config(timezone=ZoneInfo(str(name)))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise VaultError(
            f"{CONFIG_NAME} 里的 timezone「{name}」无法识别。"
            "请使用 Asia/Singapore 这样的名称；Windows 上还需要 pip install tzdata"
        ) from exc
