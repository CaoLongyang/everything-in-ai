from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from eia.cli import main
from eia.schema.io import render_document
from eia.schema.vault import init_vault

CREATED = dt.datetime(2026, 11, 15, 10, 0, tzinfo=dt.UTC)


def base_meta(**overrides: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "schema": 1,
        "id": "note-20261115-abcdef",
        "type": "note",
        "title": "测试笔记",
        "created": CREATED,
        "status": "inbox",
    }
    meta.update(overrides)
    return meta


def chat_meta(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": "chat-20261115-abcdefabcdef",
        "type": "chat",
        "platform": "claude",
        "conversation_id": "conv-1",
    }
    fields.update(overrides)
    return base_meta(**fields)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    init_vault(root)
    return root


@pytest.fixture
def write() -> Callable[..., Path]:
    def _write(root: Path, rel: str, meta: dict[str, Any], body: str = "正文\n") -> Path:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(render_document(meta, body).encode("utf-8"))
        return path

    return _write


@pytest.fixture
def run() -> Callable[..., Result]:
    def _run(*args: str) -> Result:
        return CliRunner().invoke(main, list(args))

    return _run
