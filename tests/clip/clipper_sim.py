"""用 Python 复现 Obsidian Web Clipper 渲染模板的方式，只覆盖 eia 模板用到的部分。

依据（2026-11-24 读的源码，见 docs/specs/web-clip.md 第 3.1 节）：
- obsidian-clipper `shared.ts`：`{{time}}` = dayjs().format('YYYY-MM-DDTHH:mm:ssZ')；
  generateFrontmatter 的写法。
- knap 0.6.0：`date`（day.js 格式，`[...]` 原样输出，`X` 是 Unix 秒）、`date_modify`（只返回日期）、
  `replace`（多组按顺序执行）、`slice`。
真实插件的行为最终以手动验收 M-W1 为准。
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

_EXPR = re.compile(r"\{\{(.*?)\}\}")
_PAIR = re.compile(r'"((?:[^"\\]|\\.)*)":"((?:[^"\\]|\\.)*)"')
_TOKENS = re.compile(r"\[[^\]]*\]|YYYY|MM|DD|HH|mm|ss|X|Z")
_MODIFY = re.compile(r"^([+-])\s*(\d+)\s*(\w+?)s?$")
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def dayjs_time(when: dt.datetime, tz: dt.tzinfo) -> str:
    """插件的 {{time}}：本机时间带时差。"""
    return dayjs_format(when.astimezone(tz), "YYYY-MM-DDTHH:mm:ssZ")


def dayjs_format(local: dt.datetime, fmt: str) -> str:
    def token(match: re.Match[str]) -> str:
        t = match.group(0)
        if t.startswith("["):
            return t[1:-1]
        if t == "X":
            return str(int(local.timestamp()))
        if t == "Z":
            offset = local.utcoffset() or dt.timedelta()
            minutes = int(offset.total_seconds()) // 60
            sign = "+" if minutes >= 0 else "-"
            return f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"
        return local.strftime(
            {"YYYY": "%Y", "MM": "%m", "DD": "%d", "HH": "%H", "mm": "%M", "ss": "%S"}[t]
        )

    return _TOKENS.sub(token, fmt)


def _split(text: str, sep: str) -> list[str]:
    parts, depth, quoted, start = [], 0, False, 0
    for i, ch in enumerate(text):
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        elif not quoted and depth == 0 and ch == sep:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _unquote(arg: str) -> str:
    arg = arg.strip()
    assert arg.startswith('"') and arg.endswith('"'), f"参数要加引号：{arg}"
    return arg[1:-1]


def _apply(value: str, name: str, arg: str, tz: dt.tzinfo) -> str:
    if name == "date":
        return dayjs_format(dt.datetime.fromisoformat(value).astimezone(tz), _unquote(arg))
    if name == "date_modify":
        match = _MODIFY.match(_unquote(arg).strip())
        assert match, arg
        sign, amount, unit = match.groups()
        delta = dt.timedelta(**{f"{unit}s": int(amount) * (1 if sign == "+" else -1)})
        return (dt.datetime.fromisoformat(value) + delta).astimezone(tz).strftime("%Y-%m-%d")
    if name == "replace":
        pairs = _PAIR.findall(arg)
        assert pairs, arg
        for search, replacement in pairs:  # 按顺序执行，后面的组看得到前面的结果
            value = value.replace(search, replacement)
        return value
    if name == "slice":
        bounds = [int(x) if x.strip() else None for x in arg.split(",")]
        return value[slice(*bounds)] if len(bounds) == 2 else value[bounds[0] :]
    if name == "safe_name":
        return _FORBIDDEN.sub("", value).strip()
    raise AssertionError(f"模拟器不认识过滤器 {name}")


def render(text: str, variables: dict[str, str], tz: dt.tzinfo) -> str:
    def expression(match: re.Match[str]) -> str:
        head, *filters = _split(match.group(1), "|")
        value = variables[head.strip()]
        for part in filters:
            name, _, arg = part.partition(":")
            value = _apply(value, name.strip(), arg, tz)
        return value

    return _EXPR.sub(expression, text)


def frontmatter(properties: list[dict[str, Any]], variables: dict[str, str], tz: dt.tzinfo) -> str:
    """generateFrontmatter：text 加双引号，multitext 写成列表，number / datetime 原样。"""
    lines = ["---"]
    for prop in properties:
        value = render(prop["value"], variables, tz)
        kind = prop["type"]
        if kind == "multitext":
            items = [item.strip() for item in value.split(",") if item.strip()]
            lines.append(f"{prop['name']}:")
            lines += [f'  - "{item}"' for item in items]
        elif kind == "number":
            lines.append(f"{prop['name']}: {float(value):g}")
        elif kind in ("date", "datetime"):
            lines.append(f"{prop['name']}: {value}")
        elif value:
            escaped = value.replace('"', '\\"')
            lines.append(f'{prop["name"]}: "{escaped}"')
        else:
            lines.append(f"{prop['name']}: ")
    lines.append("---")
    return "\n".join(lines) + "\n"


def clip(
    template: dict[str, Any], when: dt.datetime, tz: dt.tzinfo, page: dict[str, str]
) -> tuple[str, str]:
    """模拟剪一篇：返回（相对路径，文件内容）。"""
    variables = {"time": dayjs_time(when, tz), **page}
    name = render(template["noteNameFormat"], variables, tz)
    body = render(template["noteContentFormat"], variables, tz)
    text = frontmatter(template["properties"], variables, tz) + "\n" + body + "\n"
    return f"{template['path']}/{name}.md", text
