"""vault 条目的数据模型：frontmatter 规范 v1。"""

from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)

SCHEMA_VERSION = 1


class EntryType(StrEnum):
    headline = "headline"
    digest = "digest"
    clip = "clip"
    chat = "chat"
    note = "note"
    topic = "topic"
    profile = "profile"


class Status(StrEnum):
    inbox = "inbox"
    processed = "processed"
    archived = "archived"


class Platform(StrEnum):
    chatgpt = "chatgpt"
    claude = "claude"
    gemini = "gemini"


ID_PATTERN = r"^(headline|digest|clip|chat|note|topic|profile)-\d{8}-[a-z2-7]{6,12}$"
ID_RE = re.compile(ID_PATTERN)


def _check_tag(value: str) -> str:
    if not value or value != value.lower() or any(ch.isspace() for ch in value):
        raise ValueError("标签必须是小写且不含空格，多个词用 - 连接")
    return value


def _check_utc(value: dt.datetime) -> dt.datetime:
    if value.utcoffset() != dt.timedelta(0):
        raise ValueError("时间必须是 UTC（以 Z 结尾），如 2026-11-15T10:00:00Z")
    return value


def _check_not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("不能为空")
    return value


def _none_as_empty(value: object) -> object:
    """Obsidian 清空列表属性时会写成 `tags:`（空值），按空列表处理。"""
    return [] if value is None else value


Tag = Annotated[StrictStr, AfterValidator(_check_tag)]
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_check_utc)]
NonBlankStr = Annotated[StrictStr, AfterValidator(_check_not_blank)]


class Entry(BaseModel):
    """所有条目共有的字段。未知字段允许存在并原样保留。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schema")
    id: StrictStr = Field(pattern=ID_PATTERN)
    type: EntryType
    title: NonBlankStr
    created: UtcDatetime
    status: Status
    updated: UtcDatetime | None = None
    tags: Annotated[list[Tag], BeforeValidator(_none_as_empty)] = Field(default_factory=list)
    source: StrictStr | None = None
    url: StrictStr | None = None
    lang: StrictStr | None = None
    links: Annotated[list[StrictStr], BeforeValidator(_none_as_empty)] = Field(default_factory=list)


class Source(BaseModel):
    model_config = ConfigDict(extra="allow")

    publisher: NonBlankStr
    url: StrictStr


class HeadlineEntry(Entry):
    sources: list[Source]
    independent_count: StrictInt = Field(ge=0)
    cluster_id: StrictStr | None = None


class ChatEntry(Entry):
    platform: Platform
    conversation_id: NonBlankStr
    model: StrictStr | None = None
    message_count: StrictInt | None = None
    # 正文里是否导入了思考过程（docs/specs/vault-schema.delta-v1.md）
    thinking: StrictBool | None = None


class DigestEntry(Entry):
    date: dt.date
    score: StrictFloat | StrictInt | None = None


class ProfileEntry(Entry):
    """个人画像（docs/specs/vault-schema.delta-profile.md）。"""

    period: StrictStr | None = None
    chats: StrictInt | None = None
    model: StrictStr | None = None


MODELS: dict[str, type[Entry]] = {
    EntryType.headline.value: HeadlineEntry,
    EntryType.chat.value: ChatEntry,
    EntryType.digest.value: DigestEntry,
    EntryType.profile.value: ProfileEntry,
}


def model_for(entry_type: object) -> type[Entry]:
    """按 type 取对应的模型；type 不认识时退回通用模型（由它报出 type 的错误）。"""
    if isinstance(entry_type, str):
        return MODELS.get(entry_type, Entry)
    return Entry


def known_fields(model: type[Entry]) -> set[str]:
    return {field.alias or name for name, field in model.model_fields.items()}
