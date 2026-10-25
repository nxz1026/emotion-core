"""Lab 变体回放（P2）：DB-free 单测。

真库等价性（默认变体 == `signal` 表 replay BUY 集合）在
`tests/e2e/test_lab_replay_live.py`（`EC_LIVE_DB=1` 才跑）。这里守三件事：

1. `LabRules` 默认值逐项等于主线的 `CONFIG` / `entry` 常量，且 `is_open()` 与
   `entry.position_tier(...).weight > 0` 逐格一致（否则 Lab 搜的是另一个策略）；
2. `prepare()` 的最高组/换手板集合口径与 `ladder.top_group` 一致（停牌与一字剔除）；
3. `signals()` 的行为契约：窗口 NONE（冰点）不产记录；档位倍率 0 不产记录；
   门槛抬高后不产记录；正常日产出 1 条且身份档正确。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from emotion_core.algorithms import entry, state
from emotion_core.lab import replay
from emotion_core.lab.rules import BASELINE, GRID, LabRules, variants
from emotion_core.utils.config import CONFIG

D1 = date(2026, 3, 2)
D2 = date(2026, 3, 3)


def _row(
    d: date,
    code: str,
    cont: int,
    *,
    is_exchange: bool = True,
    is_suspended: bool = False,
    turnover: float | None = 8.0,
    bomb_times: float | None = None,
    close: float = 10.0,
    market: dict | None = None,
) -> dict:
    """一行面板记录（只填 prepare/signals 会用到的列，其余按 None 补齐）。"""
    base = {
        "d0": d,
        "code": code,
        "cont_days": cont,
        "is_exchange": is_exchange,
        "is_top": False,
        "is_sole_top": False,
        "y_top_group_count": 0,
        "y_top_survivor_count": 0,
        "is_suspended": is_suspended,
        "phase": "退潮",
        "buy_window": "STANDARD",
        "diverge": False,
        "limit_up_count": 50,
        "bomb_rate": 0.30,
        "zt_performance": 1.0,
        "zt_performance_median": 0.5,
        "max_limit_days": 4,
        "limit_down_count": 3,
        "top_amplitude": 8.0,
        "top_broke": False,
        "bomb_threshold": 0.42,
        "has_candidate": True,
        "tradable_max_days": 4,
        "oneword_ratio": 0.10,
        "is_one_word": not is_exchange,
        "is_limit_up": True,
        "is_bomb": False,
        "touched_limit": True,
        "bomb_times": bomb_times,
        "stock_name": f"票{code[-2:]}",
        "turnover_rate": turnover,
        "pre_close": close,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1e6,
        "n1_date": None,
        "n1_open": None,
        "n1_high": None,
        "n1_low": None,
        "n1_close": None,
        "n1_pre_close": None,
        "n1_volume": None,
        "n2_open": None,
        "n2_close": None,
        "n3_open": None,
    }
    if market:
        base.update(market)
    return base


def _panel() -> pd.DataFrame:
    """两天：D1 最高组={600001}；D2 唯一最高板仍是 600001（身位延续）。"""
    # zt_performance=-3.0 → 两天都命中「退潮」→ buy_window=STANDARD（可产记录）
    m = {"zt_performance": -3.0}
    rows = [
        _row(D1, "600001", 4, market=m),
        _row(D1, "600002", 3, market=m),
        _row(D2, "600001", 5, market=m),
        _row(D2, "600002", 3, market=m),
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 参数一致性


def test_defaults_match_mainline_constants():
    assert BASELINE.min_leader_days == entry.MIN_LEADER_DAYS
    assert BASELINE.diverge_min_turnover == entry.DIVERGE_MIN_TURNOVER
    assert BASELINE.climax_zt == CONFIG.CLIMAX_ZT
    assert BASELINE.ebb_zt_perf == CONFIG.EBB_ZT_PERF
    assert BASELINE.ferment_zt_perf == CONFIG.FERMENT_ZT_PERF
    assert BASELINE.ice_zt_max == CONFIG.ICE_ZT_MAX
    assert BASELINE.ferment_min_days == CONFIG.MIN_LEADER_DAYS
    assert (BASELINE.tier_mult_strict, BASELINE.tier_mult_inherit, BASELINE.tier_mult_newcomer) == (
        1.0,
        1.0,
        1.0,
    )


def test_state_rules_projection_matches_fields():
    sr = BASELINE.state_rules()
    assert sr == state.DEFAULT_RULES
    assert sr == LabRules().state_rules()


def test_is_open_matches_position_tier_and_zero_mult_closes():
    tiers = (entry.TIER_STRICT, entry.TIER_INHERIT, entry.TIER_NEWCOMER)
    for phase in ("退潮", "高潮", "发酵", "冰点"):
        for diverge in (False, True):
            for tier in tiers:
                expected = entry.position_tier(phase, diverge, tier).weight > 0
                assert BASELINE.is_open(phase, diverge, tier) is expected, (phase, diverge, tier)
    closed = BASELINE.replace(tier_mult_newcomer=0.0)
    assert closed.is_open("退潮", False, entry.TIER_NEWCOMER) is False
    assert closed.is_open("退潮", False, entry.TIER_INHERIT) is True


# ---------------------------------------------------------------- prepare 口径


def test_prepare_top_group_and_exchange_sets():
    panel = pd.DataFrame(
        [
            _row(D1, "600001", 4),
            _row(D1, "600002", 4),
            _row(D1, "600003", 2),
            _row(D1, "600004", 9, is_exchange=False),  # 一字板：不进最高组
            _row(D1, "600005", 9, is_suspended=True),  # 停牌：不进最高组
        ]
    )
    prep = replay.prepare(panel)
    assert prep.days == [D1]
    assert prep.tops[D1] == {"600001", "600002"}  # 平顶 → 最高组 2 只
    assert prep.exchange[D1] == {"600001", "600002", "600003"}
    assert prep.metrics[0].limit_up_count == 50


def test_prepare_keeps_missing_as_none():
    panel = pd.DataFrame(
        [
            _row(
                D1,
                "600001",
                4,
                turnover=None,
                bomb_times=None,
                market={"top_broke": None, "bomb_rate": None},
            )
        ]
    )
    prep = replay.prepare(panel)
    m = prep.metrics[0]
    assert m.bomb_rate is None and m.top_broke is None
    assert pd.isna(prep.rows[D1][0]["turnover_rate"]) or prep.rows[D1][0]["turnover_rate"] is None


# ---------------------------------------------------------------- signals 契约


def test_signal_emitted_and_tier_inherit():
    prep = replay.prepare(_panel())
    sig = replay.signals(prep, BASELINE, as_of=D2)
    assert len(sig) == 1
    row = sig.iloc[0]
    assert row["d0"] == D2 and row["code"] == "600001"
    assert row["tier"] == entry.TIER_INHERIT  # 昨在最高组但昨日无竞争
    assert row["buy_window"] == "STANDARD"


def test_ice_window_produces_nothing():
    """冰点 → buy_window NONE → 不产记录（唯一禁买相位）。"""
    rows = []
    for d, cont in ((D1, 4), (D2, 5)):
        rows.append(
            _row(
                d,
                "600001",
                cont,
                market={"limit_up_count": 10, "max_limit_days": 2, "has_candidate": False},
            )
        )
    prep = replay.prepare(pd.DataFrame(rows))
    assert replay.signals(prep, BASELINE, as_of=D2).empty
    assert prep and state.classify_series(prep.metrics)[1].buy_window == "NONE"


def test_tier_multiplier_zero_filters_signal():
    prep = replay.prepare(_panel())
    sig = replay.signals(prep, BASELINE.replace(tier_mult_inherit=0.0), as_of=D2)
    assert sig.empty


def test_min_leader_days_gate():
    prep = replay.prepare(_panel())
    assert replay.signals(prep, BASELINE.replace(min_leader_days=6), as_of=D2).empty


def test_flat_top_no_signal():
    """昨日/今日最高组不止一只 → 不是唯一高标 → 不产记录。"""
    m = {"zt_performance": -3.0}
    rows = [
        _row(D1, "600001", 4, market=m),
        _row(D1, "600002", 4, market=m),
        _row(D2, "600001", 5, market=m),
        _row(D2, "600002", 5, market=m),
    ]
    prep = replay.prepare(pd.DataFrame(rows))
    assert replay.signals(prep, BASELINE, as_of=D2).empty


def test_start_end_filter():
    prep = replay.prepare(_panel())
    assert replay.signals(prep, BASELINE, as_of=D2, start=D2 + timedelta(days=1)).empty
    assert len(replay.signals(prep, BASELINE, as_of=D2, end=D1)) == 0


# ---------------------------------------------------------------- 搜索空间


def test_variants_are_single_knob_changes():
    vs = variants()
    assert vs[0] == BASELINE
    keys = set(GRID)
    for v in vs[1:]:
        diff = {k for k in v.as_dict() if getattr(v, k) != getattr(BASELINE, k)}
        assert len(diff) == 1 and diff <= keys, diff
    assert len({v.key() for v in vs}) == len(vs)  # 无重复变体
