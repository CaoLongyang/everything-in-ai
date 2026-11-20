"""条目 id 的生成。id 一旦生成不再改变。"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import secrets

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
RANDOM_SUFFIX_LEN = 6
CHAT_SUFFIX_LEN = 12  # 60 位：几万个对话也不会撞


def _utc_date(when: dt.datetime) -> str:
    return when.astimezone(dt.UTC).strftime("%Y%m%d")


def new_id(entry_type: str, when: dt.datetime) -> str:
    """普通条目：`<type>-<UTC日期>-<6位随机base32>`。"""
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(RANDOM_SUFFIX_LEN))
    return f"{entry_type}-{_utc_date(when)}-{suffix}"


def chat_id(platform: str, conversation_id: str, created: dt.datetime) -> str:
    """对话条目：后缀由 平台 + 对话 ID 哈希得到，重复导入得到同一个 id。"""
    digest = hashlib.sha256(f"{platform}:{conversation_id}".encode()).digest()
    suffix = base64.b32encode(digest).decode("ascii").lower()[:CHAT_SUFFIX_LEN]
    return f"chat-{_utc_date(created)}-{suffix}"


def short_suffix(entry_id: str) -> str:
    """文件名里用的 6 位后缀。"""
    return entry_id.rsplit("-", 1)[-1][:RANDOM_SUFFIX_LEN]
