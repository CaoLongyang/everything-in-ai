"""起名时同时给出搜索关键词、判断是否适合挑新闻（headline-radar.delta-sources 第 7 节，F15-08）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from eia.cli import main
from eia.interests import draft as draft_mod
from eia.interests.config import load_interests
from eia.interests.prompts import NAMING_SYSTEM
from eia.llm.client import Reply

from .conftest import FakeModel

SEARCH = {"汽车生活": ["Malaysia car insurance", "Proton"], "神学与信仰": ["改革宗"]}
NOT_NEWS = {"Java 编程"}


class NewsModel(FakeModel):
    """在 FakeModel 的起名结果上加 search 和 news：Java 作业不会有新闻。"""

    def complete(self, prompt: str, system: str | None, json_schema: Any) -> Reply:
        reply = super().complete(prompt, system, json_schema)
        data = json.loads(reply.text)
        for item in data["interests"]:
            item["search"] = SEARCH.get(item["name"], [])
            item["news"] = item["name"] not in NOT_NEWS
        return Reply(json.dumps(data, ensure_ascii=False))


@pytest.fixture
def model() -> FakeModel:
    return NewsModel()


def test_F15_08_non_news_topics_go_last_and_unchecked(vault: Path) -> None:
    result = CliRunner().invoke(main, ["interests", "draft", "--vault", str(vault), "--yes"])
    assert result.exit_code == 0, result.output
    text = (vault / draft_mod.DRAFT).read_text(encoding="utf-8")
    heading = text.index(draft_mod.NOT_NEWS_HEADING)
    assert text.index("**Java 编程**") > heading > text.index("**汽车生活**")
    assert "- [ ] **Java 编程**" in text and "- [x] **汽车生活**" in text
    assert "  - 搜索：Malaysia car insurance、Proton" in text

    items = {i.interest.name: i for i in draft_mod.parse_draft(text)}
    assert not items["Java 编程"].checked
    assert items["神学与信仰"].interest.search == ("改革宗",)

    accepted = CliRunner().invoke(main, ["interests", "accept", "--vault", str(vault)])
    assert accepted.exit_code == 0, accepted.output
    saved = {i.name: i for i in load_interests(vault)}
    assert set(saved) == {"汽车生活", "神学与信仰"}
    assert saved["汽车生活"].search == ("Malaysia car insurance", "Proton")


def test_F15_08_prompt_asks_for_search_and_news() -> None:
    assert "search" in NAMING_SYSTEM and "news" in NAMING_SYSTEM
    assert "课程作业" in NAMING_SYSTEM
