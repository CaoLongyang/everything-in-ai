"""F16 网页剪藏：eia clip template 生成的插件模板（docs/specs/web-clip.md）。

插件本身不在测试里运行，用 clipper_sim 按插件源码的规则渲染；真实插件以手动验收 M-W1 为准。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from click.testing import CliRunner

from eia.cli import main
from eia.clip import template as clip_template
from eia.schema.io import read_document
from eia.schema.validate import validate_vault
from eia.schema.vault import CONFIG_NAME, init_vault

from .clipper_sim import clip

KL = ZoneInfo("Asia/Kuala_Lumpur")
PAGE = {
    "title": "一篇关于芯片出口的文章",
    "url": "https://news.example/chips?id=1",
    "domain": "news.example",
    "author": "张三",
    "published": "2026-11-20",
    "content": "正文第一段：光刻机出口管制。\n\n第二段。",
}


def make_vault(tmp_path: Path, timezone: str | None = "Asia/Kuala_Lumpur") -> Path:
    root = tmp_path / "vault"
    init_vault(root)
    if timezone:
        config = root / CONFIG_NAME
        text = config.read_text(encoding="utf-8").replace(
            "\ntimezone:\n", f"\ntimezone: {timezone}\n"
        )
        config.write_text(text, encoding="utf-8")
    return root


def template_for(root: Path, now: dt.datetime | None = None) -> dict[str, Any]:
    return clip_template.generate(root, now=now).template


def save_clip(
    root: Path, template: dict[str, Any], when: dt.datetime, tz: Any, **page: str
) -> Path:
    rel, text = clip(template, when, tz, {**PAGE, **page})
    path = root / rel
    path.write_text(text, encoding="utf-8")
    return path


# ---------- F16-01 模板 ----------


def test_F16_01_cli_writes_importable_template(tmp_path: Path) -> None:
    root = make_vault(tmp_path)
    result = CliRunner().invoke(main, ["clip", "template", "--vault", str(root)])
    assert result.exit_code == 0, result.output
    assert "时区 Asia/Kuala_Lumpur，UTC+8" in result.output and "夏令时" not in result.output
    data = json.loads((root / clip_template.DEFAULT_FILE).read_text(encoding="utf-8"))
    # 插件导入时要求的键（obsidian-clipper validateImportedTemplate）
    for key in ("name", "behavior", "properties", "noteContentFormat", "noteNameFormat", "path"):
        assert data[key]
    assert data["schemaVersion"] == "0.1.0" and data["behavior"] == "create"
    assert data["path"] == "inbox/clips"
    assert all(p["type"] in clip_template.PROPERTY_TYPES for p in data["properties"])
    names = [p["name"] for p in data["properties"]]
    assert names[:6] == ["schema", "id", "type", "title", "created", "status"]
    assert {"url", "source", "tags"} <= set(names)
    tags = next(p for p in data["properties"] if p["name"] == "tags")
    assert tags == {"name": "tags", "value": "clip", "type": "multitext"}
    created = next(p for p in data["properties"] if p["name"] == "created")
    assert (
        created["type"] == "datetime" and "|date:" in created["value"]
    )  # 插件才会原样写、不加引号


def test_F16_01_out_option_and_not_a_vault(tmp_path: Path) -> None:
    root = make_vault(tmp_path)
    out = tmp_path / "elsewhere.json"
    assert (
        CliRunner()
        .invoke(main, ["clip", "template", "--vault", str(root), "--out", str(out)])
        .exit_code
        == 0
    )
    assert json.loads(out.read_text(encoding="utf-8"))["name"] == clip_template.TEMPLATE_NAME
    assert not (root / clip_template.DEFAULT_FILE).exists()
    again = CliRunner().invoke(main, ["clip", "template", "--vault", str(root), "--out", str(out)])
    assert again.exit_code == 0  # 重新生成直接覆盖（模板是程序生成的）
    bad = CliRunner().invoke(main, ["clip", "template", "--vault", str(tmp_path / "none")])
    assert bad.exit_code == 1 and "不是一个 vault" in bad.output


# ---------- F16-02 渲染出的剪藏合规，时间正确 ----------


def _instants() -> list[dt.datetime]:
    day = [dt.datetime(2026, 11, 24, h, 7, 9, tzinfo=KL) for h in range(24)]
    edges = [
        dt.datetime(2027, 1, 1, 3, 0, 0, tzinfo=KL),  # 本地新年，UTC 还是去年
        dt.datetime(2026, 3, 1, 7, 59, 59, tzinfo=KL),  # 本地 3 月 1 日，UTC 是 2 月 28 日
        dt.datetime(2026, 3, 1, 8, 0, 0, tzinfo=KL),
    ]
    return day + edges


def test_F16_02_rendered_clips_validate_and_times_are_utc(tmp_path: Path) -> None:
    root = make_vault(tmp_path)
    template = template_for(root)
    for when in _instants():
        path = save_clip(root, template, when, KL)
        meta = read_document(path).meta
        utc = when.astimezone(dt.UTC)
        assert meta["created"] == utc, (when, meta["created"])
        assert meta["id"].startswith(f"clip-{utc:%Y%m%d}-"), meta["id"]
        assert path.name.startswith(f"{when:%Y-%m-%d}-")  # 文件名日期按本地
        assert meta["tags"] == ["clip"] and meta["type"] == "clip" and meta["schema"] == 1
    report = validate_vault(root)
    assert report.files_checked == len(_instants())
    assert not report.errors, report.errors


def test_F16_02_body_and_fields(tmp_path: Path) -> None:
    root = make_vault(tmp_path)
    path = save_clip(root, template_for(root), dt.datetime(2026, 11, 24, 9, 0, tzinfo=KL), KL)
    doc = read_document(path)
    assert doc.meta["title"] == PAGE["title"] and doc.meta["url"] == PAGE["url"]
    assert doc.meta["source"] == "news.example" and doc.meta["status"] == "inbox"
    assert "光刻机出口管制" in doc.body
    suffix = doc.meta["id"].rsplit("-", 1)[1]
    assert len(suffix) == 10 and set(suffix) <= set("abcdefghij")
    assert path.stem.endswith(
        "-" + suffix[:6]
    )  # 文件名后缀 = id 后缀前 6 位，与 eia 自己的规则一致


# ---------- F16-03 时区 ----------


def test_F16_03_negative_offset(tmp_path: Path) -> None:
    root = make_vault(tmp_path, "America/Bogota")
    tz = ZoneInfo("America/Bogota")
    result = clip_template.generate(root)
    assert result.offset_hours == -5 and not result.has_dst
    for hour in (0, 18, 19, 23):
        when = dt.datetime(2026, 12, 31, hour, 30, 0, tzinfo=tz)
        meta = read_document(save_clip(root, result.template, when, tz)).meta
        assert meta["created"] == when.astimezone(dt.UTC)
    assert not validate_vault(root).errors


def test_F16_03_utc_zone(tmp_path: Path) -> None:
    root = make_vault(tmp_path, "UTC")
    template = template_for(root)
    when = dt.datetime(2026, 11, 24, 23, 59, 59, tzinfo=dt.UTC)
    assert read_document(save_clip(root, template, when, dt.UTC)).meta["created"] == when


def test_F16_03_empty_timezone_uses_local(tmp_path: Path) -> None:
    root = make_vault(tmp_path, None)
    now = dt.datetime(2026, 11, 24, 6, 0, tzinfo=dt.UTC)
    local = now.astimezone().utcoffset()
    assert local is not None
    if local.total_seconds() % 3600:
        with pytest.raises(clip_template.ClipError):
            clip_template.generate(root, now=now)
        return
    result = clip_template.generate(root, now=now)
    assert result.offset_hours == int(local.total_seconds()) // 3600
    assert result.zone == "本机时区"


def test_F16_03_dst_warning_and_half_hour_error(tmp_path: Path) -> None:
    london = make_vault(tmp_path / "a", "Europe/London")
    result = CliRunner().invoke(main, ["clip", "template", "--vault", str(london)])
    assert result.exit_code == 0 and "夏令时" in result.output
    winter = clip_template.generate(london, now=dt.datetime(2026, 1, 10, tzinfo=dt.UTC))
    summer = clip_template.generate(london, now=dt.datetime(2026, 7, 10, tzinfo=dt.UTC))
    assert (winter.offset_hours, summer.offset_hours) == (0, 1)

    india = make_vault(tmp_path / "b", "Asia/Kolkata")
    bad = CliRunner().invoke(main, ["clip", "template", "--vault", str(india)])
    assert bad.exit_code == 1 and "不是整小时" in bad.output
    assert not (india / clip_template.DEFAULT_FILE).exists()


# ---------- F16-04 id ----------


def test_F16_04_same_second_duplicates_are_reported(tmp_path: Path) -> None:
    root = make_vault(tmp_path)
    template = template_for(root)
    when = dt.datetime(2026, 11, 24, 10, 0, 0, tzinfo=KL)
    first = save_clip(root, template, when, KL, title="第一篇")
    second = save_clip(root, template, when, KL, title="第二篇")
    third = save_clip(root, template, when + dt.timedelta(seconds=1), KL, title="第三篇")
    ids = [read_document(p).meta["id"] for p in (first, second, third)]
    assert ids[0] == ids[1] != ids[2]
    assert first.stem[-6:] != third.stem[-6:]  # 文件名后缀每秒都变
    errors = validate_vault(root).errors
    assert len(errors) == 2  # 重复的两个文件各报一条，第三篇不报
    assert all(e.field == "id" and "重复" in e.message for e in errors)
    assert {Path(e.path).name for e in errors} == {first.name, second.name}


# ---------- F16-05 额外字段、搜索 ----------


def test_F16_05_extra_fields_warn_and_search_finds_clip(tmp_path: Path) -> None:
    root = make_vault(tmp_path)
    save_clip(root, template_for(root), dt.datetime(2026, 11, 24, 9, 0, tzinfo=KL), KL)
    save_clip(
        root,
        template_for(root),
        dt.datetime(2026, 11, 24, 9, 0, 1, tzinfo=KL),
        KL,
        author="",
        published="",
        title='标题里有 "引号"',
    )
    report = validate_vault(root)
    assert not report.errors, report.errors
    assert {w.field for w in report.warnings} == {"author", "published"}
    result = CliRunner().invoke(main, ["search", "光刻机", "--vault", str(root), "--type", "clip"])
    assert result.exit_code == 0 and "inbox/clips/" in result.output.replace("\\", "/")
