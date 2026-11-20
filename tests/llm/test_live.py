"""真的调用 AI 服务、会花一点钱。默认不运行。

手动运行：先把 EIA_LIVE_VAULT 设成配好 llm 段的 vault 目录，再
    pytest -m live tests/llm/test_live.py -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from eia import llm

pytestmark = pytest.mark.live


@pytest.fixture
def vault() -> Path:
    path = os.environ.get("EIA_LIVE_VAULT")
    if not path:
        pytest.skip("没有设置 EIA_LIVE_VAULT")
    return Path(path)


class Capital(BaseModel):
    country: str
    capital: str


def test_live_complete_and_json(vault: Path) -> None:
    client = llm.get_client(vault)
    assert client.complete("只回复 OK 两个字母。").strip()
    result = client.complete_json("法国的首都是哪里？", Capital)
    assert "巴黎" in result.capital or "Paris" in result.capital


def test_live_embed(vault: Path) -> None:
    client = llm.get_client(vault)
    if client.config.embed is None:
        pytest.skip("没有配置 llm.embed")
    vectors = client.embed(["你好", "世界"])
    assert len(vectors) == 2 and len(vectors[0]) == len(vectors[1]) > 0
