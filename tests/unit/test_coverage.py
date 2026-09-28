"""覆盖率硬门槛（asel A12）单测：判定纯函数 + 编排退出码 76。

门槛语义照 asel `scripts/asel-coverage-gate.py`：
`covered=count(DISTINCT code) FROM daily_bar WHERE date=目标日`、
`in_market=count(*) FROM stock_basic WHERE in_market`，`ratio<0.90 → 拒绝装配`。
emotion-core 的退出码取 docs/01 A12 规定的 **76**（asel 脚本当年用 1/2）。
"""
from __future__ import annotations

from datetime import date

import pytest

from emotion_core.orchestration import daily
from emotion_core.services import coverage
from emotion_core.utils.config import CONFIG

D = date(2026, 9, 25)


def test_exit_code_is_76():
    assert coverage.EXIT_COVERAGE_BLOCKED == 76
    assert CONFIG.MIN_COVERAGE == 0.90


@pytest.mark.parametrize("covered,in_market,ok", [
    (9978, 10000, True),    # asel 实测正常日 0.9978
    (9000, 10000, True),    # 恰好等于门槛
    (8999, 10000, False),   # 差 1 只就拦
    (180, 5569, False),     # asel 实测半截数据 0.032
    (0, 10000, False),
    (100, 0, False),        # 分母不可用同样拒绝
])
def test_decide_branches(covered, in_market, ok):
    got, line = coverage.decide(covered, in_market)
    assert got is ok
    assert f"covered={covered}" in line and f"in_market={in_market}" in line
    assert "min=0.9" in line
    assert ("拒绝装配" in line) is not ok


def test_ratio_reported_with_4_decimals():
    _ok, line = coverage.decide(9978, 10000)
    assert "ratio=0.9978" in line


def test_gate_passes_and_logs(monkeypatch):
    monkeypatch.setattr(coverage, "measure", lambda d: (9978, 10000))
    assert "ratio=0.9978" in coverage.gate(D)


def test_gate_blocks_and_raises(monkeypatch):
    monkeypatch.setattr(coverage, "measure", lambda d: (180, 5569))
    with pytest.raises(coverage.CoverageBlocked) as ei:
        coverage.gate(D)
    assert "拒绝装配" in str(ei.value)


def test_measure_sql_shape(monkeypatch):
    import pandas as pd

    seen: list[str] = []

    def fake_query(sql, params=()):
        seen.append(sql)
        return pd.DataFrame({"n": [7]})

    monkeypatch.setattr(coverage, "query_df", fake_query)
    assert coverage.measure(D) == (7, 7)
    assert "count(DISTINCT code)" in seen[0] and "daily_bar" in seen[0]
    assert "count(*)" in seen[1] and "WHERE in_market" in seen[1]


def test_coverage_step_sits_right_after_sync():
    steps = [s for s, _ in daily.STEPS]
    assert steps.index("coverage") == steps.index("sync") + 1


class _Pipeline:
    """orchestration/pipeline.py 门面替身：签名与门面一致 (step, for_date)。

    注意门面与 algorithms 实现的**实参位次不同**：
    门面 mark_failed(step, for_date, error) → 实现 mark_failed(step, error, for_date)。
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def mark_running(self, step, for_date=None):
        self.calls.append(("running", step, for_date))

    def mark_done(self, step, for_date=None, detail=""):
        self.calls.append(("done", step, for_date, detail))

    def mark_failed(self, step, for_date=None, error=""):
        self.calls.append(("failed", step, for_date, error))


def _blocked(trade_date):
    raise coverage.CoverageBlocked(
        "coverage covered=180 in_market=5569 ratio=0.0323 min=0.9 -> 拒绝装配")


def test_run_daily_returns_76_when_blocked(monkeypatch):
    pipe = _Pipeline()
    monkeypatch.setattr(daily, "_trading_day_guard", lambda d: True)
    monkeypatch.setattr(daily, "pipeline", pipe)
    monkeypatch.setattr(coverage, "gate", _blocked)

    assert daily.run_daily(D, from_step="coverage") == 76
    assert pipe.calls[0][:2] == ("running", "coverage")
    assert pipe.calls[1][:3] == ("failed", "coverage", D)
    assert "拒绝装配" in pipe.calls[1][3]


def test_run_daily_continues_past_a_passing_gate(monkeypatch):
    """达标不阻断：继续跑到下一步，下一步失败才是 1（不是 76）。"""
    from emotion_core.services import derive_service

    pipe = _Pipeline()
    monkeypatch.setattr(daily, "_trading_day_guard", lambda d: True)
    monkeypatch.setattr(daily, "pipeline", pipe)
    monkeypatch.setattr(coverage, "gate", lambda d: "coverage ratio=0.9978")
    monkeypatch.setattr(derive_service, "run",
                        lambda d: (_ for _ in ()).throw(RuntimeError("boom")))

    assert daily.run_daily(D, from_step="coverage") == 1
    assert pipe.calls[1][:2] == ("done", "coverage")
    failed = [c for c in pipe.calls if c[0] == "failed"]
    assert failed and failed[0][:3] == ("failed", "derive", D)
    assert "boom" in failed[0][3]


# ── 交易日守卫（weekday 判据，独立于库内数据）──────────────────
def test_guard_rejects_weekend_even_if_calendar_is_polluted(monkeypatch):
    """脏数据把周日写进 daily_bar 时，守卫仍必须拒绝。

    实测事故：2026-09-27（周日）在 daily_bar 有 5221 行、与 09-24 逐行相同，
    而交易日历从 daily_bar 派生 → 守卫被骗过 → sync 失败 → 日更链卡死。
    """
    monkeypatch.setattr(daily, "trading_days", lambda a, b: [date(2026, 9, 27)])
    assert daily._trading_day_guard(date(2026, 9, 27)) is False   # 周日


def test_guard_accepts_weekday_present_in_calendar(monkeypatch):
    monkeypatch.setattr(daily, "trading_days", lambda a, b: [date(2026, 9, 24)])
    assert daily._trading_day_guard(date(2026, 9, 24)) is True     # 周四


def test_guard_rejects_weekday_absent_from_calendar(monkeypatch):
    monkeypatch.setattr(daily, "trading_days", lambda a, b: [date(2026, 9, 24)])
    assert daily._trading_day_guard(date(2026, 9, 25)) is False    # 周五中秋休市


def test_guard_makes_weekend_run_a_noop(monkeypatch):
    """周日跑 daily：直接 0 退出，不碰 pipeline_state、不进 sync。"""
    monkeypatch.setattr(daily, "trading_days", lambda a, b: [date(2026, 9, 27)])
    called: list[str] = []
    monkeypatch.setattr(daily, "_run_step", lambda s, d: called.append(s))
    assert daily.run_daily(date(2026, 9, 27)) == 0
    assert called == []
