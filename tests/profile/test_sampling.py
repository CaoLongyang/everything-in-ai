"""F18-20：观察太多时按月均匀挑，主动补充的一律保留（docs/specs/profile.delta-sampling.md）。"""

from __future__ import annotations

import pytest

from eia.profile import synth as synth_mod
from eia.profile.extract import Observation, Quote


def _obs(day: str, n: int, kind: str = "own") -> Observation:
    quote = Quote(f"原话{n}", "self" if kind == "self" else "own", day)
    return Observation(f"c{n}", "cognition", "stated", f"观察{n}", (quote,))


def test_F18_20_over_the_limit_every_month_gets_a_share(monkeypatch: pytest.MonkeyPatch) -> None:
    old = [_obs(f"2024-0{m}-10", m) for m in (1, 2, 3)]  # 很早以前，每月一条
    recent = [_obs("2026-11-01", 100 + n) for n in range(40)]  # 最近一个月很多
    mine = [_obs("2026-11-20", 999, "self")]
    one = len(synth_mod._line("O0000", recent[0], {}))
    monkeypatch.setattr(synth_mod, "SYNTH_CHARS", one * 10)
    numbered, omitted = synth_mod._fit([*recent, *old, *mine], {})
    kept = [obs for obs, _ in numbered]
    assert all(o in kept for o in old)  # 以前的每个月都有份
    assert mine[0] in kept  # 主动补充的先放进去
    assert len(kept) <= 10 and omitted == 44 - len(kept)
    assert [max(o.days) for o in kept] == sorted(max(o.days) for o in kept)  # 按时间排


def test_F18_20_under_the_limit_keeps_everything() -> None:
    observations = [_obs("2026-11-01", n) for n in range(5)]
    numbered, omitted = synth_mod._fit(observations, {})
    assert omitted == 0 and len(numbered) == 5
