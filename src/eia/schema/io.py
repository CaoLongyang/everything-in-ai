"""读写带 frontmatter 的 Markdown 文件。所有文件 IO 都在这里。

写回已有文件时不重新序列化整个 frontmatter，只替换被改动的顶层字段所在的行，
其余内容（键顺序、引号、注释、换行符、正文）逐字节保持不变。见 docs/adr/0001。
"""

from __future__ import annotations

import datetime as dt
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_FRONTMATTER_RE = re.compile(
    r"\A(﻿?---[ \t]*\r?\n)(.*?)(^---[ \t]*(?:\r?\n|\Z))",
    re.DOTALL | re.MULTILINE,
)


class FrontmatterError(Exception):
    """文件的 frontmatter 缺失、损坏或无法安全修改。"""


@dataclass(frozen=True)
class Document:
    path: Path
    raw: str
    meta: dict[str, Any]
    body: str


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        """同一个对象出现两次（例如 created 和 updated 是同一个时间）时照写两遍，
        不写成 &id001 / *id001：人看不懂，按行修改字段时还会把文件弄坏。"""
        return True


def _represent_datetime(dumper: yaml.SafeDumper, value: dt.datetime) -> yaml.ScalarNode:
    if value.tzinfo is not None and value.utcoffset() == dt.timedelta(0):
        text = value.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    else:
        text = value.isoformat()
    return dumper.represent_scalar("tag:yaml.org,2002:timestamp", text)


_Dumper.add_representer(dt.datetime, _represent_datetime)


def _dump(data: dict[str, Any]) -> str:
    text: str = yaml.dump(
        data,
        Dumper=_Dumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=10**6,
    )
    return text


def _split(text: str) -> tuple[str, str, str] | None:
    """返回 (开头分隔行, frontmatter 文本, 其余部分)，三者拼起来等于原文。"""
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return None
    return match.group(1), match.group(2), text[match.end(2) :]


def _parse(fm_text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"frontmatter 不是合法的 YAML：{exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter 必须是 键: 值 的形式")
    return {str(key): value for key, value in data.items()}


def parse_text(text: str, path: Path) -> Document:
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise FrontmatterError("缺少 frontmatter（文件必须以 --- 开头的字段块开始）")
    return Document(path=path, raw=text, meta=_parse(match.group(2)), body=text[match.end() :])


def read_document(path: Path) -> Document:
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FrontmatterError("文件不是 UTF-8 编码") from exc
    return parse_text(text, path)


def render_document(meta: dict[str, Any], body: str) -> str:
    """生成一个新文件的全文。只用于新建，修改已有文件用 update_frontmatter。"""
    return f"---\n{_dump(meta)}---\n\n{body}"


def _trim_trailing_noise(segment: str) -> str:
    """去掉片段末尾的空行和注释行，让它们留在原处不被替换掉。"""
    lines = segment.splitlines(keepends=True)
    while len(lines) > 1 and (not lines[-1].strip() or lines[-1].lstrip().startswith("#")):
        lines.pop()
    return "".join(lines)


def _top_level_spans(fm_text: str) -> dict[str, tuple[int, int]]:
    try:
        node = yaml.compose(fm_text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"frontmatter 不是合法的 YAML：{exc}") from exc
    if node is None:
        return {}
    if not isinstance(node, yaml.MappingNode):
        raise FrontmatterError("frontmatter 必须是 键: 值 的形式")
    spans: dict[str, tuple[int, int]] = {}
    for key_node, value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode):
            start = key_node.start_mark.index
            kept = _trim_trailing_noise(fm_text[start : value_node.end_mark.index])
            spans[key_node.value] = (start, start + len(kept))
    return spans


def update_frontmatter(raw: str, changes: dict[str, Any]) -> str:
    """只改 changes 里的顶层字段，返回新的全文；其余部分逐字节不变。"""
    if not changes:
        return raw
    parts = _split(raw)
    if parts is None:
        raise FrontmatterError("缺少 frontmatter，无法修改字段")
    opener, fm_text, rest = parts
    newline = "\r\n" if opener.endswith("\r\n") else "\n"
    before = _parse(fm_text)
    spans = _top_level_spans(fm_text)

    def block(key: str, value: Any) -> str:
        return _dump({key: value}).replace("\n", newline)

    new_fm = fm_text
    existing = [key for key in changes if key in spans]
    for key in sorted(existing, key=lambda k: spans[k][0], reverse=True):
        start, end = spans[key]
        replacement = block(key, changes[key])
        if not new_fm[start:end].endswith(("\n", "\r\n")):
            replacement = replacement.rstrip("\r\n")
        new_fm = new_fm[:start] + replacement + new_fm[end:]
    additions = [key for key in changes if key not in spans]
    if additions:
        if new_fm and not new_fm.endswith("\n"):
            new_fm += newline
        new_fm += "".join(block(key, changes[key]) for key in additions)

    expected = {**before, **changes}
    if _parse(new_fm) != expected:
        raise FrontmatterError("无法安全地修改 frontmatter（结构过于特殊），文件未改动")
    return opener + new_fm + rest


def atomic_write(path: Path, text: str) -> None:
    """先写临时文件再替换；中途失败时原文件保持完好。"""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def create_new(path: Path, text: str) -> None:
    """新建文件；目标已存在时抛 FileExistsError，绝不覆盖。"""
    with path.open("xb") as handle:
        handle.write(text.encode("utf-8"))
