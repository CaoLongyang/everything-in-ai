"""F17-05：兴趣草稿里每条消息分量一样，几万字的粘贴不会把其他消息挤出去。"""

from __future__ import annotations

from pathlib import Path

from eia.chat.importer import import_conversations
from eia.interests import draft as draft_mod

from .conftest import FakeModel, _chat


def test_F17_05_long_paste_does_not_crowd_out_short_messages(vault: Path, model: FakeModel) -> None:
    paste = "这里贴了一大段作业代码 " + "java 代码 " * 2000
    import_conversations(
        vault, [_chat("long", "长粘贴", [paste, "我其实最想知道马来西亚的汽车保险怎么买"], 28)]
    )
    draft_mod.plan_draft(vault)
    [text] = [t for t in model.embedded if t.startswith("长粘贴\n")]
    assert "我其实最想知道马来西亚的汽车保险怎么买" in text
    assert len(text) <= draft_mod.EMBED_CHARS


def test_F17_05_fair_share() -> None:
    texts = ["短" * 10, "长" * 5000, "中" * 300]
    shared = draft_mod.fair_share(texts, 1000)
    assert shared[0] == texts[0] and shared[2] == texts[2]  # 没超过上限的全留
    assert len(shared[1]) == 1000 - 3 - 10 - 300  # 只有最长的被截，用掉剩下的全部额度
    assert draft_mod.fair_share(["一句", "两句"], 1000) == ["一句", "两句"]  # 没超就不动
