"""F13：兴趣草稿、按兴趣过滤、评价与提炼。"""

from __future__ import annotations

import datetime as dt
import functools
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner, Result

from eia.cli import main
from eia.headlines.urls import url_key
from eia.interests import digest as digest_mod
from eia.interests import draft as draft_mod
from eia.interests import learn as learn_mod
from eia.interests.config import InterestsError, load_digest_config, load_interests
from eia.interests.feedback import rate, read_feedback
from eia.schema.io import read_document
from eia.schema.validate import validate_vault
from eia.schema.vault import CONFIG_NAME

from .conftest import (
    AI_MARKER,
    LLM,
    NOW,
    THINKING_MARKER,
    FakeModel,
    item,
    set_interests,
    stock_items,
)


def _cli(*args: str) -> Result:
    return CliRunner().invoke(main, list(args))


def _draft(vault: Path) -> list[draft_mod.DraftItem]:
    return draft_mod.parse_draft((vault / draft_mod.DRAFT).read_text(encoding="utf-8"))


# ---------- F13-01 只用我说的话 ----------


def test_F13_01_only_my_words_are_grouped(vault: Path, model: FakeModel) -> None:
    plan = draft_mod.plan_draft(vault)
    joined = "\n".join(model.embedded)
    assert AI_MARKER not in joined and THINKING_MARKER not in joined
    assert "好的谢谢" not in joined  # 少于 15 个字的消息跳过
    assert "驾照" in joined and "上帝" in joined
    excerpts = "\n".join(t for g in plan.groups for t in g.excerpts)
    assert AI_MARKER not in excerpts and THINKING_MARKER not in excerpts
    assert sorted(len(g.docs) for g in plan.groups) == [4, 4, 4]


def test_F13_01_notes_count_as_my_words(vault: Path, model: FakeModel) -> None:
    path = next((vault / "chats" / "claude").glob("*买车问题0*.md"))
    path.write_bytes(path.read_bytes() + "\n我自己的笔记：想换一辆省油的汽车\n".encode())
    draft_mod.plan_draft(vault)
    assert any("想换一辆省油的汽车" in text for text in model.embedded)


def test_F13_01_remote_embedding_is_refused(vault: Path, model: FakeModel) -> None:
    data = yaml.safe_load((vault / CONFIG_NAME).read_text(encoding="utf-8"))
    data["llm"]["embed"]["api_base"] = "https://api.example.com/v1"
    (vault / CONFIG_NAME).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(InterestsError, match="向量模型不在本机"):
        draft_mod.plan_draft(vault)
    assert model.embedded == []


def test_F13_01_too_few_conversations(
    tmp_path: Path, use_model: Callable[[FakeModel], None]
) -> None:
    from eia.schema.vault import init_vault

    root = tmp_path / "empty"
    init_vault(root)
    text = (root / CONFIG_NAME).read_text(encoding="utf-8") + yaml.safe_dump({"llm": LLM})
    (root / CONFIG_NAME).write_text(text, encoding="utf-8")
    use_model(FakeModel())
    with pytest.raises(InterestsError, match="太少了"):
        draft_mod.plan_draft(root)


# ---------- F13-02 确认后才发，发的就是说好的那些 ----------


def test_F13_02_nothing_is_sent_without_yes(vault: Path, model: FakeModel) -> None:
    result = _cli("interests", "draft", "--vault", str(vault))
    assert result.exit_code == 0, result.output
    assert "没有发出任何内容" in result.output and "加上 --yes" in result.output
    assert model.prompts == [] and not (vault / draft_mod.DRAFT).exists()


def test_F13_02_what_is_sent_matches_the_announcement(vault: Path, model: FakeModel) -> None:
    plan = draft_mod.plan_draft(vault)
    assert plan.excerpt_count == 12 and all(
        len(t) <= draft_mod.EXCERPT_CHARS for g in plan.groups for t in g.excerpts
    )
    draft_mod.name_groups(vault, plan)
    [prompt] = model.prompts
    sent = [line[2:] for line in prompt.splitlines() if line.startswith("- ")]
    assert len(sent) == plan.excerpt_count
    assert sum(len(s) for s in sent) == plan.excerpt_chars
    result = _cli("interests", "draft", "--vault", str(vault), "--yes")
    assert "已写草稿" in result.output
    assert f"{plan.excerpt_count} 段摘录、共 {plan.excerpt_chars} 字" in result.output


# ---------- F13-03 草稿、证据、确认 ----------


def test_F13_03_draft_has_evidence_and_does_not_apply_itself(vault: Path) -> None:
    before = (vault / CONFIG_NAME).read_text(encoding="utf-8")
    assert _cli("interests", "draft", "--vault", str(vault), "--yes").exit_code == 0
    text = (vault / draft_mod.DRAFT).read_text(encoding="utf-8")
    assert "依据：4 个对话、8 条消息，2026-03 至 2026-03；例如 [[chats/claude/" in text
    assert sorted(i.interest.name for i in _draft(vault)) == sorted(
        ["汽车生活", "Java 编程", "神学与信仰"]
    )
    assert "杂项" not in text  # 不成主题的组被跳过
    assert (vault / CONFIG_NAME).read_text(encoding="utf-8") == before  # 草稿不会自动生效
    assert validate_vault(vault).ok


def test_F13_03_accept_writes_only_checked_items(vault: Path) -> None:
    _cli("interests", "draft", "--vault", str(vault), "--yes")
    path = vault / draft_mod.DRAFT
    text = path.read_text(encoding="utf-8")
    text = text.replace("- [x] **Java 编程**", "- [ ] **Java 编程**")
    text = text.replace("**汽车生活**：car 相关", "**汽车生活**：买车、驾照、保险")
    text = text.replace(
        "**汽车生活**：买车、驾照、保险\n  - 搜索：\n  - 不要：",
        "**汽车生活**：买车、驾照、保险\n  - 搜索：car insurance、驾照\n  - 不要：赛车",
    )
    path.write_text(text, encoding="utf-8")
    result = _cli("interests", "accept", "--vault", str(vault))
    assert result.exit_code == 0, result.output
    interests = {i.name: i for i in load_interests(vault)}
    assert set(interests) == {"汽车生活", "神学与信仰"}
    assert interests["汽车生活"].description == "买车、驾照、保险"
    assert interests["汽车生活"].exclude == "赛车"
    assert interests["汽车生活"].search == ("car insurance", "驾照")
    config = (vault / CONFIG_NAME).read_text(encoding="utf-8")
    assert "# 用户自己的注释" in config and "deepseek-v4-flash" in config
    assert (vault / ".eia" / "config.yaml.bak").is_file()

    # 再生成一次：现有兴趣列在最前面、勾着，旧草稿备份
    _cli("interests", "draft", "--vault", str(vault), "--yes")
    items = _draft(vault)
    assert [i.interest.name for i in items[:2]] == [i.name for i in load_interests(vault)]
    assert all(i.checked for i in items) and (vault / draft_mod.DRAFT_BACKUP).is_file()


def test_F13_03_accept_errors(vault: Path) -> None:
    assert "还没有兴趣草稿" in _cli("interests", "accept", "--vault", str(vault)).output
    _cli("interests", "draft", "--vault", str(vault), "--yes")
    path = vault / draft_mod.DRAFT
    path.write_text(path.read_text(encoding="utf-8").replace("- [x]", "- [ ]"), encoding="utf-8")
    assert "一项都没勾" in _cli("interests", "accept", "--vault", str(vault)).output


# ---------- F13-04 打分、分组、上限 ----------


def _stock(vault: Path) -> None:
    items = [item(n, f"马来西亚汽车保险新规 {n}") for n in range(7)]
    items += [item(10 + n, f"Java 编程语言新版本发布 {n}") for n in range(2)]
    items += [item(20 + n, f"足球比赛结果 {n}") for n in range(3)]
    items += [item(30, "很久以前的汽车新闻", hours_ago=100)]
    stock_items(vault, items)


def test_F13_04_needs_interests_and_items(vault: Path) -> None:
    result = _cli("digest", "run", "--vault", str(vault))
    assert result.exit_code == 1 and "先运行 eia interests draft" in result.output
    set_interests(vault, [{"name": "汽车生活"}])
    result = _cli("digest", "run", "--vault", str(vault))
    assert "先运行 eia headlines run" in result.output


def test_F13_04_scoring_groups_and_limits(vault: Path) -> None:
    set_interests(
        vault,
        [
            {"name": "汽车生活", "description": "汽车、驾照"},
            {"name": "Java 编程", "description": "代码"},
        ],
        per_interest=5,
        max_items=6,
    )
    _stock(vault)
    result = digest_mod.run_digest(vault, now=NOW)
    groups = [p.interest for p in result.picks]
    assert result.candidates == 12  # 太旧的那篇不在窗口里
    # 总数上限 6；分数高的先选（两篇 Java 都入选），汽车的 7 篇只剩 4 个位置
    assert len(groups) == 6 and groups.count("Java 编程") == 2 and groups.count("汽车生活") == 4
    assert [p.score for p in result.picks] == sorted((p.score for p in result.picks), reverse=True)
    capped = digest_mod.run_digest(vault, now=NOW, dry_run=True)
    assert len(capped.picks) == 6
    set_interests(vault, [{"name": "汽车生活", "description": "汽车、驾照"}], per_interest=5)
    assert len(digest_mod.run_digest(vault, now=NOW, dry_run=True).picks) == 5  # 每组上限 5
    assert all("足球" not in p.item.title for p in result.picks)
    assert all(p.score >= load_digest_config(vault).min_score for p in result.picks)


@pytest.mark.parametrize(
    ("digest", "error"),
    [
        ({"min_score": 2}, "0 到 1"),
        ({"per_interest": 0}, "至少是 1"),
        ({"max_items": "x"}, "应该是数字"),
    ],
)
def test_F13_04_bad_digest_config(vault: Path, digest: dict[str, object], error: str) -> None:
    set_interests(vault, [{"name": "x"}], **digest)
    with pytest.raises(InterestsError, match=error):
        load_digest_config(vault)


def test_F13_04_bad_interests_config(vault: Path) -> None:
    set_interests(vault, [{"description": "没有名字"}])
    with pytest.raises(InterestsError, match="缺少 name"):
        load_interests(vault)
    set_interests(vault, ["只写名字也行"])
    assert load_interests(vault)[0].name == "只写名字也行"


# ---------- F13-05 每天一个文件，保留笔记 ----------


def test_F13_05_same_day_updates_same_file_and_keeps_notes(vault: Path) -> None:
    set_interests(vault, [{"name": "汽车生活", "description": "汽车、驾照"}])
    _stock(vault)
    first = digest_mod.run_digest(vault, now=NOW)
    assert first.created and first.path
    path = vault / first.path
    path.write_bytes(path.read_bytes() + "\n我的笔记：保险要比价\n".encode())
    second = digest_mod.run_digest(vault, now=NOW + dt.timedelta(hours=1))
    assert not second.created and second.path == first.path
    text = path.read_text(encoding="utf-8")
    assert "我的笔记：保险要比价" in text and "### 汽车生活" in text
    meta = read_document(path).meta
    assert meta["type"] == "digest" and str(meta["date"]) == "2026-11-22"
    report = validate_vault(vault)
    assert report.ok and not [w for w in report.warnings if "digest" in w.path]

    # 第二天：昨天已经给你看过的不再出现
    third = digest_mod.run_digest(vault, now=NOW + dt.timedelta(days=1))
    shown = {url_key(p.item.url) for p in first.picks}
    assert third.created and not {url_key(p.item.url) for p in third.picks} & shown


def test_F13_05_broken_markers_are_not_touched(vault: Path) -> None:
    set_interests(vault, [{"name": "汽车生活", "description": "汽车"}])
    _stock(vault)
    result = digest_mod.run_digest(vault, now=NOW)
    path = vault / str(result.path)
    broken = path.read_text(encoding="utf-8").replace(digest_mod.END, "")
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(InterestsError, match="标记缺失"):
        digest_mod.run_digest(vault, now=NOW)
    assert path.read_text(encoding="utf-8") == broken


def test_F13_05_dry_run_and_empty_day(vault: Path) -> None:
    set_interests(vault, [{"name": "神学与信仰", "description": "上帝"}])
    _stock(vault)
    result = digest_mod.run_digest(vault, now=NOW, dry_run=True)
    assert result.picks == [] and result.path is None
    assert list((vault / "inbox" / "digest").glob("*.md")) == []
    real = digest_mod.run_digest(vault, now=NOW)
    assert "今天没有和你的兴趣足够相关的报道" in (vault / str(real.path)).read_text(
        encoding="utf-8"
    )


# ---------- F13-06 复用雷达的向量 ----------


def test_F13_06_reuses_radar_vectors_and_resets_on_model_change(
    vault: Path, model: FakeModel
) -> None:
    from eia.headlines.store import save_vectors
    from eia.llm.config import load_llm_config

    set_interests(vault, [{"name": "汽车生活", "description": "汽车"}])
    _stock(vault)
    from eia.headlines.store import load_items

    items = load_items(vault)
    endpoint = load_llm_config(vault, environ={}).embed
    assert endpoint is not None
    save_vectors(vault, endpoint.cache_key, {i.url: [1.0, 0, 0, 0, 0.15] for i in items})
    digest_mod.run_digest(vault, now=NOW)
    assert not any("新闻" in t or "足球" in t for t in model.embedded)

    data = yaml.safe_load((vault / CONFIG_NAME).read_text(encoding="utf-8"))
    data["llm"]["embed"]["model"] = "other"
    (vault / CONFIG_NAME).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    digest_mod.run_digest(vault, now=NOW, dry_run=True)
    assert any("足球" in t for t in model.embedded)


# ---------- F13-07 评价 ----------


def test_F13_07_ratings_combine_and_overwrite(vault: Path) -> None:
    rate(vault, "https://a.example/1?utm_source=x", title="A", relevant=True, now=NOW)
    rate(vault, "https://a.example/2", title="B", stars=2, now=NOW)
    rate(vault, "https://a.example/3", title="C", why="只关心芯片", now=NOW)
    rate(
        vault, "https://a.example/1", title="A", relevant=False, stars=1, why="其实不想看", now=NOW
    )
    records = {r.title: r for r in read_feedback(vault)}
    assert set(records) == {"A", "B", "C"}
    assert records["A"].relevant is False and records["A"].stars == 1 and records["A"].disliked
    assert records["B"].disliked and not records["C"].liked and not records["C"].disliked
    assert (vault / "interests" / "feedback.jsonl").is_file()
    with pytest.raises(InterestsError, match="至少要选"):
        rate(vault, "https://a.example/9")
    with pytest.raises(InterestsError, match="1 到 5"):
        rate(vault, "https://a.example/9", stars=6)


def test_F13_07_cli_rate_looks_up_the_title(vault: Path) -> None:
    _stock(vault)
    result = _cli(
        "digest",
        "rate",
        "https://news.example/3",
        "--vault",
        str(vault),
        "--irrelevant",
        "--stars",
        "2",
        "--why",
        "不关心保险",
    )
    assert result.exit_code == 0, result.output
    [record] = read_feedback(vault)
    assert record.title == "马来西亚汽车保险新规 3" and record.publisher == "媒体3"
    assert record.relevant is False and record.why == "不关心保险"
    nothing = _cli("digest", "rate", "https://x", "--vault", str(vault))
    assert nothing.exit_code == 1


# ---------- F13-08 评价和「不要」影响打分 ----------


def test_F13_08_feedback_and_exclude_change_scores(vault: Path) -> None:
    set_interests(
        vault,
        [
            {"name": "汽车生活", "description": "汽车"},
            {"name": "Java 编程", "description": "代码", "exclude": "手机"},
        ],
        min_score=0.3,
    )
    items = [
        item(1, "汽车保险新规"),
        item(2, "汽车展览开幕"),
        item(3, "Java 新版本"),
        item(4, "Java 手机 iphone 应用"),
    ]
    stock_items(vault, items)
    before = {
        p.item.title: p.score for p in digest_mod.run_digest(vault, now=NOW, dry_run=True).picks
    }
    assert "Java 手机 iphone 应用" not in before  # 更像「不要」

    rate(vault, items[0].url, title=items[0].title, relevant=False, now=NOW)
    rate(vault, items[2].url, title=items[2].title, stars=5, now=NOW)
    result = digest_mod.run_digest(vault, now=NOW, dry_run=True)
    after = {p.item.title: p.score for p in result.picks}
    assert (
        "汽车保险新规" not in after and "汽车展览开幕" not in after
    )  # 标了不相关的，和它很像的也不要了
    assert after["Java 新版本"] == pytest.approx(
        before["Java 新版本"] + digest_mod.LIKE_BONUS, abs=1e-3
    )
    assert result.dropped_by_feedback == 2 and result.dropped_by_exclude == 1


# ---------- F13-09 提炼 ----------


LEARN_REPLY = {
    "interests": [
        {"name": "汽车生活", "description": "买车和保险", "exclude": "赛车", "change": "修改"},
        {"name": "Java 编程", "description": "代码", "change": "删除"},
        {"name": "芯片", "description": "半导体", "change": "新增"},
        {"name": "汽车生活", "description": "重复的会被去掉", "change": "保留"},
    ]
}


def test_F13_09_learn_needs_feedback_and_confirmation(vault: Path, model: FakeModel) -> None:
    set_interests(vault, [{"name": "汽车生活"}, {"name": "Java 编程"}])
    assert "还没有任何评价" in _cli("interests", "learn", "--vault", str(vault)).output
    rate(vault, "https://a.example/1", title="赛车比赛", relevant=False, why="不看赛车", now=NOW)
    result = _cli("interests", "learn", "--vault", str(vault))
    assert "加上 --yes" in result.output and model.prompts == []
    plan = learn_mod.plan_learn(vault)
    assert "赛车比赛" in plan.prompt and "不看赛车" in plan.prompt and plan.feedback == 1


def test_F13_09_suggestions_go_through_the_draft(
    vault: Path, use_model: Callable[[FakeModel], None]
) -> None:
    fake = FakeModel(learn_reply=LEARN_REPLY)
    use_model(fake)
    set_interests(vault, [{"name": "汽车生活"}, {"name": "Java 编程"}])
    rate(vault, "https://a.example/1", title="赛车比赛", relevant=False, now=NOW)
    before = load_interests(vault)
    result = _cli("interests", "learn", "--vault", str(vault), "--yes")
    assert result.exit_code == 0, result.output
    assert "3 处建议修改" in result.output  # 修改、删除、新增；重复的那项被去掉
    assert load_interests(vault) == before  # 确认前不改配置
    items = {i.interest.name: i for i in _draft(vault)}
    assert not items["Java 编程"].checked and items["芯片"].checked
    _cli("interests", "accept", "--vault", str(vault))
    after = {i.name: i for i in load_interests(vault)}
    assert set(after) == {"汽车生活", "芯片"} and after["汽车生活"].exclude == "赛车"


def test_F13_09_empty_suggestions(vault: Path, use_model: Callable[[FakeModel], None]) -> None:
    use_model(FakeModel(learn_reply={"interests": []}))
    set_interests(vault, [{"name": "汽车生活"}])
    rate(vault, "https://a.example/1", title="x", relevant=True, now=NOW)
    with pytest.raises(InterestsError, match="没有给出任何建议"):
        learn_mod.learn(vault, learn_mod.plan_learn(vault))


# ---------- 定时任务里接着跑 ----------


def test_F13_04_scheduled_job_runs_digest_after_radar(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from eia.schedule import runs

    from ..schedule.test_schedule import _radar_result

    monkeypatch.setattr("eia.headlines.run.run", lambda root: _radar_result(13, 13))
    # 定时任务不传 now，固定成 NOW，否则样例报道过几天就出了时间窗口
    monkeypatch.setattr(digest_mod, "run_digest", functools.partial(digest_mod.run_digest, now=NOW))
    assert "今日兴趣" not in runs.run_job(vault, "headlines").summary  # 没有兴趣清单就跳过
    set_interests(vault, [{"name": "汽车生活", "description": "汽车"}])
    _stock(vault)
    record = runs.run_job(vault, "headlines")
    assert record.ok and "今日兴趣挑出 5 篇" in record.summary

    def broken(root: Path, **kwargs: object) -> object:
        raise InterestsError("坏了")

    monkeypatch.setattr("eia.interests.digest.run_digest", broken)
    failed = runs.run_job(vault, "headlines")
    assert not failed.ok and "新闻雷达成功，但兴趣过滤失败：坏了" in str(failed.error)


def test_F13_04_shown_records_expire_after_a_week(vault: Path) -> None:
    digest_mod._save_shown(vault, {"old": "2026-11-01", "new": "2026-11-21"}, NOW)
    assert digest_mod._load_shown(vault, NOW) == {"new": "2026-11-21"}


def test_F13_03_config_edit_handles_unindented_lists(vault: Path) -> None:
    """2026-11-21 发现：`interests:` 下面顶格写的 `- name:` 没被当成这一段，替换后旧项残留。"""
    from eia.schema.configedit import replace_section

    text = "schema: 1\ninterests:\n- name: 旧的\n- name: 也是旧的\n# 下一段的注释\ntimezone:\n"
    new = replace_section(text, "interests", [{"name": "新的"}])
    assert new == "schema: 1\ninterests:\n- name: 新的\n# 下一段的注释\ntimezone:\n"
