"""信号质量评估（replay / forward_stats / ebb_days）：断言逐列语义与出口族收益（不连库）。

语义来源 lkl/services/evaluate.py；差异仅限 emotion-core 契约（见 evaluate.py 模块文档）：
- name 来自 entry._candidate_view（LadderDay 无 name 字段）；
- 逐项行经 entry._evaluate + _split_rows（emotion-core 的 entry.split_checklist
  已改为 (Checklist, WarningOnly)，不暴露说明文本）；
- FEE_COMMISSION / FEE_STAMP 经 _cfg 取 lkl 原值默认。

IO 全部桩掉（交易日历、窗口、梯队、候选质量、行情/涨停止/退潮日），
真实库对账另跑（见交付记录：同一区间与 lkl 产物差分）。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from emotion_core.algorithms import entry, evaluate
from emotion_core.domain.ladder import LadderDay
from emotion_core.domain.signal import Checklist

D1 = date(2026, 9, 16)
D2 = date(2026, 9, 21)
D3 = date(2026, 9, 24)
AS_OF = date(2026, 9, 24)

COLS = ["date", "code", "name", "cont_days", "window", "passed", "fails",
        "unknown", "warn_notes", "c5_verifiable", "pit_drift"]


def cand(cont: int = 5, code: str = "000017", d: date = D3) -> LadderDay:
    return LadderDay(date=d, code=code, cont_days=cont, is_exchange=True,
                     is_top=True, is_sole_top=True, y_top_group_count=3,
                     y_top_survivor_count=1)


def rows_ok() -> list[entry.Row]:
    """entry._rows 形状：五条件 + W1 警告（标签/值/说明逐字同 entry）。"""
    return [("c1 唯一换手高标", True, "唯一换手高标: 测试龙(000017) 5板"),
            ("c2 换手板非一字", True, "换手板(非一字)"),
            ("c3 淘汰赛身份", True, "昨日最高板组3只→候选胜出（集合等值：幸存=候选本身）"),
            ("c4 最低板数门槛", True, "5板 >= 门槛4"),
            ("c5 强度与分歧补偿", True, "换手8.00%≥阈值5.0%"),
            ("W1 同身位扎堆", True, "无同身位扎堆")]


def with_row(rows: list[entry.Row], prefix: str,
             ok: bool | None, note: str) -> list[entry.Row]:
    """把标签以 prefix 开头的行替换为 (标签, ok, note)。"""
    return [(label, ok, note) if label.startswith(prefix) else (label, o, t)
            for label, o, t in rows]


def checklist(**over) -> Checklist:
    base = {"c1_uniqueness": True, "c2_exchange": True, "c3_elimination": True,
            "c4_min_days": True, "c5_strength_diverge": True, "w1_crowding": True}
    base.update(over)
    return Checklist(**base)


@pytest.fixture
def patched(monkeypatch):
    """replay 的 IO 面全部桩掉；返回可改写状态桶。"""
    state: dict = {"days": [D2], "windows": {D2: "STANDARD"},
                   "cands": {D2: cand()}, "rows": rows_ok(), "cl": checklist(),
                   "view": {"name": "测试龙"}, "pit": False,
                   "built": [], "cursor": None, "evaluate_kw": None}

    monkeypatch.setattr(evaluate, "trading_days", lambda s, e: state["days"])
    monkeypatch.setattr(entry, "current_window",
                        lambda d: state["windows"].get(d, "NONE"))

    def build(d):
        state["cursor"] = d
        state["built"].append(d)
        return []

    monkeypatch.setattr(evaluate.ladder, "build", build)
    monkeypatch.setattr(evaluate.ladder, "sole_top",
                        lambda rows, min_days=None: state["cands"].get(state["cursor"]))

    def fake_evaluate(c, window, *, min_days, diverge_min, **kw):
        state["evaluate_kw"] = {"cand": c, "window": window, "min_days": min_days,
                                "diverge_min": diverge_min, "as_of": kw.get("as_of")}
        return state["cl"], state["rows"]

    monkeypatch.setattr(entry, "_evaluate", fake_evaluate)
    monkeypatch.setattr(entry, "_candidate_view", lambda c: state["view"])
    monkeypatch.setattr(evaluate, "_pit_drift", lambda code, d: state["pit"])
    return state


class TestReplay:
    def test_empty_range_returns_empty_frame(self, patched):
        patched["days"] = []
        assert evaluate.replay(D1, D3).empty

    def test_column_contract(self, patched):
        df = evaluate.replay(D1, D3, as_of=AS_OF)
        assert list(df.columns) == COLS

    def test_row_semantics(self, patched):
        df = evaluate.replay(D1, D3, as_of=AS_OF)
        r = df.iloc[0]
        assert (r["date"], r["code"], r["name"], r["cont_days"], r["window"]) \
            == (D2, "000017", "测试龙", 5, "STANDARD")
        assert bool(r["passed"]) and r["fails"] == [] and r["unknown"] == []
        assert r["warn_notes"] == ["无同身位扎堆"]
        assert bool(r["c5_verifiable"]) and not bool(r["pit_drift"])

    def test_none_window_and_no_candidate_skipped(self, patched):
        patched["days"] = [D1, D2, D3]
        patched["windows"] = {D1: "NONE", D2: "STANDARD", D3: "STANDARD"}
        patched["cands"] = {D2: cand()}          # D3 无唯一高标
        df = evaluate.replay(D1, D3, as_of=AS_OF)
        assert list(df["date"]) == [D2]          # D1 禁买，D3 无候选
        assert patched["built"] == [D2, D3]      # 禁买日不建梯队

    def test_fails_and_unknown_split(self, patched):
        patched["rows"] = with_row(rows_ok(), "c3", False, "昨日最高板组1只→今日幸存1只")
        patched["rows"] = with_row(patched["rows"], "c5", None, "⚠ EM池窗口外：不可核验")
        patched["cl"] = checklist(c3_elimination=False, c5_strength_diverge=None)
        r = evaluate.replay(D1, D3, as_of=AS_OF).iloc[0]
        assert r["fails"] == ["c3 淘汰赛身份"]
        assert r["unknown"] == ["c5 强度与分歧补偿"]
        assert bool(r["passed"]) is False    # 唯一聚合器：False 否决

    def test_unknown_does_not_veto(self, patched):
        patched["rows"] = with_row(rows_ok(), "c5", None, "⚠ EM池窗口外：不可核验")
        patched["cl"] = checklist(c5_strength_diverge=None)
        r = evaluate.replay(D1, D3, as_of=AS_OF).iloc[0]
        assert bool(r["passed"]) and r["fails"] == []
        assert r["unknown"] == ["c5 强度与分歧补偿"]

    def test_warning_never_vetoes(self, patched):
        patched["rows"] = with_row(rows_ok(), "W1", False,
                                   "⚠ 同身位扎堆：绝对最高 5板 2 只")
        patched["cl"] = checklist(w1_crowding=False)
        r = evaluate.replay(D1, D3, as_of=AS_OF).iloc[0]
        assert bool(r["passed"])
        assert r["warn_notes"] == ["⚠ 同身位扎堆：绝对最高 5板 2 只"]

    def test_c5_verifiable_window(self, patched):
        oldest = AS_OF - timedelta(days=entry.POOL_RECENT_DAYS)
        for d, want in ((oldest, True), (oldest - timedelta(days=1), False)):
            patched["days"] = [d]
            patched["windows"] = {d: "STANDARD"}
            patched["cands"] = {d: cand(d=d)}
            df = evaluate.replay(D1, D3, as_of=AS_OF)
            assert bool(df.iloc[0]["c5_verifiable"]) is want, d

    def test_pit_drift_passthrough(self, patched):
        patched["pit"] = True
        assert bool(evaluate.replay(D1, D3, as_of=AS_OF).iloc[0]["pit_drift"]) is True

    def test_name_falls_back_to_empty(self, patched):
        patched["view"] = {}
        assert evaluate.replay(D1, D3, as_of=AS_OF).iloc[0]["name"] == ""

    def test_min_days_flows_to_sole_top_and_evaluate(self, patched):
        evaluate.replay(D1, D3, min_days=3, as_of=AS_OF)
        assert patched["evaluate_kw"]["min_days"] == 3
        assert patched["evaluate_kw"]["diverge_min"] == entry.DIVERGE_MIN_TURNOVER
        assert patched["evaluate_kw"]["as_of"] == AS_OF

    def test_min_days_defaults_to_config(self, patched):
        evaluate.replay(D1, D3, as_of=AS_OF)
        assert patched["evaluate_kw"]["min_days"] == entry.MIN_LEADER_DAYS


def _bars(opens, highs, lows, closes, start=D3):
    dates, d = [], start
    while len(dates) < len(opens):
        d += timedelta(days=1)
        if d.weekday() < 5:
            dates.append(d)
    return pd.DataFrame({"date": dates, "open": opens, "high": highs,
                         "low": lows, "close": closes})


SIG = {"date": D2, "code": "000017", "window": "STANDARD", "cont_days": 5}


class TestForwardStats:
    @pytest.fixture
    def stubbed(self, monkeypatch):
        state: dict = {"nb": pd.DataFrame(), "base": 10.0, "rule": 1.23}
        monkeypatch.setattr(evaluate, "ebb_days", lambda start: [])
        monkeypatch.setattr(evaluate, "query_df", lambda sql, params=(), conn=None: (
            pd.DataFrame({"close": [state["base"]]})
            if "SELECT close FROM daily_bar" in sql else pd.DataFrame()))
        monkeypatch.setattr(evaluate, "_next_bars", lambda code, d0, n=5: state["nb"])
        monkeypatch.setattr(evaluate, "_limit_up_on", lambda code, d: True)
        monkeypatch.setattr(evaluate, "_rule_ret", lambda code, d0, ebbs=None: state["rule"])
        return state

    def test_full_window_metrics(self, stubbed):
        stubbed["nb"] = _bars([10.5, 10.6, 11.0, 10.9, 10.7],
                              [11.0, 12.0, 11.5, 10.8, 10.6],
                              [9.8, 10.2, 10.0, 9.5, 9.9],
                              [10.8, 11.8, 10.5, 10.0, 10.2])
        r = evaluate.forward_stats(pd.DataFrame([SIG])).iloc[0]
        assert r["t1_gap"] == 5.0
        assert bool(r["t1_promote"]) is True
        assert (r["max_up3"], r["max_dd3"]) == (20.0, -2.0)
        assert (r["max_up5"], r["max_dd5"]) == (20.0, -5.0)
        assert r["rule_ret"] == 1.23

    def test_right_censored_window_yields_none(self, stubbed):
        stubbed["nb"] = _bars([10.5, 10.6, 11.0, 10.9], [11.0, 12.0, 11.5, 10.8],
                              [9.8, 10.2, 10.0, 9.5], [10.8, 11.8, 10.5, 10.0])
        r = evaluate.forward_stats(pd.DataFrame([SIG])).iloc[0]
        assert r["max_up3"] == 20.0
        assert pd.isna(r["max_up5"]) and pd.isna(r["max_dd5"])

    def test_no_forward_bar_only_gap_column(self, stubbed):
        r = evaluate.forward_stats(pd.DataFrame([SIG])).iloc[0]
        assert pd.isna(r["t1_gap"])
        assert "t1_promote" not in r.index

    def test_missing_base_bar_skips_signal(self, stubbed, monkeypatch):
        monkeypatch.setattr(evaluate, "query_df",
                            lambda sql, params=(), conn=None: pd.DataFrame(columns=["close"]))
        assert evaluate.forward_stats(pd.DataFrame([SIG])).empty

    def test_ebbs_injected_once_for_all_signals(self, stubbed, monkeypatch):
        calls: list[date] = []
        monkeypatch.setattr(evaluate, "ebb_days",
                            lambda start: calls.append(start) or [])
        stubbed["nb"] = _bars([10.5, 10.6, 11.0], [11.0, 12.0, 11.5],
                              [9.8, 10.2, 10.0], [10.8, 11.8, 10.5])
        evaluate.forward_stats(pd.DataFrame([SIG, {**SIG, "date": D3}]))
        assert calls == [D2]


class TestRuleRet:
    def test_prefers_ebb_exit_over_break(self, monkeypatch):
        monkeypatch.setattr(evaluate, "_exit_family",
                            lambda code, d0, ebbs=None: {"E_退潮清仓": -3.0,
                                                         "A_断板开盘": 1.0})
        assert evaluate._rule_ret("000017", D2) == -3.0

    def test_falls_back_to_a_when_ebb_pending(self, monkeypatch):
        monkeypatch.setattr(evaluate, "_exit_family",
                            lambda code, d0, ebbs=None: {"E_退潮清仓": None,
                                                         "A_断板开盘": 2.0})
        assert evaluate._rule_ret("000017", D2) == 2.0

    def test_empty_family_returns_none(self, monkeypatch):
        monkeypatch.setattr(evaluate, "_exit_family", lambda code, d0, ebbs=None: {})
        assert evaluate._rule_ret("000017", D2) is None


class TestExitFamily:
    FEE = 0.0002 * 2 + 0.0005

    @pytest.fixture
    def stubbed(self, monkeypatch):
        monkeypatch.setattr(evaluate, "ebb_days", lambda start: [])
        monkeypatch.setattr(evaluate, "_ebb_exit",
                            lambda nb, d0, ret, ebbs=None: ret(9.85))
        return monkeypatch

    @staticmethod
    def _nb():
        # 前 2 根 bar 涨停 → 断板发生在第 3 根（index 2）
        return _bars([10.0, 11.0, 12.0, 13.0], [10.0, 11.0, 12.0, 13.0],
                     [10.0, 11.0, 12.0, 13.0], [10.5, 11.5, 12.5, 13.5])

    def test_legs(self, stubbed):
        nb = self._nb()
        dates = list(nb["date"])
        stubbed.setattr(evaluate, "_next_bars", lambda code, d0, n=5: nb)
        stubbed.setattr(evaluate, "_limit_up_on",
                        lambda code, d: d in (dates[0], dates[1]))
        fam = evaluate._exit_family("000017", D2)

        def r(p: float) -> float:
            return round((p / 10.0 - 1 - self.FEE) * 100, 2)

        assert fam["A_断板开盘"] == r(13.0)       # 首个前日未涨停日的开盘（index 3）
        assert fam["B_断板收盘"] == r(12.5)       # 断板判定当日收盘（index 2）
        assert fam["C_两日收盘"] == r(11.5)       # 固定 T+2 收盘
        assert fam["D_半仓"] == round(0.5 * fam["C_两日收盘"] + 0.5 * fam["B_断板收盘"], 2)
        assert fam["E_退潮清仓"] == r(9.85)

    def test_still_limit_up_in_window_is_pending(self, stubbed):
        stubbed.setattr(evaluate, "_next_bars", lambda code, d0, n=5: self._nb())
        stubbed.setattr(evaluate, "_limit_up_on", lambda code, d: True)
        fam = evaluate._exit_family("000017", D2)
        assert fam["A_断板开盘"] is None          # 窗内未断板 → 不近似
        assert fam["B_断板收盘"] is None          # 窗内无断板日 → 不近似

    def test_single_bar_is_illegal_t1(self, stubbed):
        stubbed.setattr(evaluate, "_next_bars",
                        lambda code, d0, n=5: _bars([10.5], [11.0], [9.8], [10.8]))
        assert evaluate._exit_family("000017", D2) == {}

    def test_no_forward_bar(self, stubbed):
        stubbed.setattr(evaluate, "_next_bars", lambda code, d0, n=5: pd.DataFrame())
        assert evaluate._exit_family("000017", D2) == {}


class TestEbbExit:
    NB = _bars([10.0, 10.5, 10.8], [10.0, 10.5, 10.8], [10.0, 10.5, 10.8],
               [10.2, 10.6, 9.9])

    def test_liquidates_on_first_ebb_after_signal(self):
        ed = list(self.NB["date"])[1]
        assert evaluate._ebb_exit(self.NB, D2, lambda p: round(p, 2), [ed]) \
            == round(float(self.NB["close"].iloc[1]), 2)

    def test_ebb_outside_window_is_pending(self):
        before = list(self.NB["date"])[0] - timedelta(days=1)
        assert evaluate._ebb_exit(self.NB, D2, lambda p: p, [before]) is None

    def test_no_ebb_after_signal(self):
        assert evaluate._ebb_exit(self.NB, D2, lambda p: p, []) is None

    def test_falls_back_to_own_query(self, monkeypatch):
        ed = list(self.NB["date"])[0]
        monkeypatch.setattr(evaluate, "ebb_days", lambda start: [ed])
        assert evaluate._ebb_exit(self.NB, D2, lambda p: p) \
            == float(self.NB["close"].iloc[0])


class TestEbbDays:
    def test_reads_force_liquidate_days(self, monkeypatch):
        seen: dict = {}

        def q(sql, params=(), conn=None):
            seen["sql"], seen["params"] = sql, params
            return pd.DataFrame({"date": [D2, D3]})

        monkeypatch.setattr(evaluate, "query_df", q)
        assert evaluate.ebb_days(D1) == [D2, D3]
        assert "force_liquidate" in seen["sql"] and seen["params"] == (D1,)
