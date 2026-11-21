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

# ---------- AI 服务（可选；不配置就不会调用任何 AI）----------
# key 不写在这里：写进本目录的 .env（已被 .gitignore 排除），例如 DEEPSEEK_API_KEY=sk-...
# llm:
#   provider: openai_compatible        # anthropic | openai_compatible
#   model: deepseek-v4-flash
#   api_base: https://api.deepseek.com
#   api_key_env: DEEPSEEK_API_KEY
#   extra_body: {thinking: {type: disabled}}   # DeepSeek：关闭思考模式，更快、更省
#   embed:                             # 向量模型，新闻雷达要用；本地 Ollama 不花钱、数据不出电脑
#     provider: openai_compatible
#     model: bge-m3                    # 先运行 ollama pull bge-m3
#     api_base: http://127.0.0.1:11434/v1

# ---------- 新闻雷达 eia headlines（可选）----------
# 下面的源 2026-11 实测可用。同一个 publisher 的多个源只算一家。去掉行首的 # 才生效。
# headlines:
#   min_sources: 3                     # 至少几家独立媒体在报才算大事
#   window_hours: 36
#   cluster: embed                     # embed（需要上面的 llm.embed）| lexical
#   feeds:
#     - {publisher: BBC, url: https://feeds.bbci.co.uk/news/world/rss.xml}
#     - {publisher: BBC, url: https://feeds.bbci.co.uk/zhongwen/simp/rss.xml}
#     - {publisher: The Guardian, url: https://www.theguardian.com/world/rss}
#     - {publisher: Al Jazeera, url: https://www.aljazeera.com/xml/rss/all.xml}
#     - {publisher: New York Times, url: https://rss.nytimes.com/services/xml/rss/nyt/World.xml}
#     - {publisher: New York Times, url: https://cn.nytimes.com/rss/}
#     - {publisher: DW, url: https://rss.dw.com/rdf/rss-en-all}
#     - {publisher: DW, url: https://rss.dw.com/rdf/rss-chi-all}
#     - {publisher: RFI, url: https://www.rfi.fr/cn/rss}
#     - {publisher: NPR, url: https://feeds.npr.org/1004/rss.xml}
#     - {publisher: CNA, url: "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml"}
#     - {publisher: ABC News Australia, url: https://www.abc.net.au/news/feed/2942460/rss.xml}
#     - {publisher: 中国新闻网, url: https://www.chinanews.com.cn/rss/scroll-news.xml}
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
