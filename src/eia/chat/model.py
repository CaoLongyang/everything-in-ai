"""与平台无关的对话数据。各平台的解析器都输出这个结构。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

Role = Literal["user", "assistant"]


class ExportFormatError(Exception):
    """导出文件的格式不认识。出现时整次导入停止，不写任何文件。"""


@dataclass(frozen=True)
class Message:
    role: Role
    time: dt.datetime
    text: str
    thinking: str = ""  # 模型的思考过程；只有用户要求导入时才有内容


@dataclass(frozen=True)
class Conversation:
    platform: str
    conversation_id: str
    title: str
    created: dt.datetime
    updated: dt.datetime
    model: str | None
    messages: tuple[Message, ...]
    # 这次导入是否包含思考过程；None 表示该平台不支持这个选项，不写进 frontmatter
    thinking: bool | None = None
    # 回到原对话的链接；导出里没有时为 None，不写进 frontmatter
    url: str | None = None
