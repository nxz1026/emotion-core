"""信号结果回填（_outcome_row / pending_signals / _upsert_rows / backfill）：不连库。

语义来源 lkl/services/outcome.py（无同名 lkl 测试，用例按源文件分支重写）；
差异仅限 IO 适配（见 outcome.py 模块文档）：写入口自持 _upsert_rows、
事务走 utils.db.transaction()、读走 query_df。

IO 全部桩掉（基准日收盘价、前向 bar、涨停止、出口族、待办信号集、写连接），
真实库对账另跑（见交付记录：同信号集与 lkl.services.outcome 逐字段一致）。
"""
from __future__ import annotations

import contextlib
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from emotion_core.algorithms import outcome

D0 = date(2026, 9, 8)
COLS = outcome._COLS


def val(row: tuple, name: str):
    """按库表列序取行内字段（断言用，列序由 _COLS 定义）。"""
    return row[COLS.index(name)]


def bars(opens, highs=None, lows=None, closes=None, d0: date = D0) -> pd.DataFrame:
    """前向 bar 帧：date 从信号日次日起逐自然日（只为排序/取值语义，不涉交易日历）。"""
    n = len(opens)
    highs = highs or [o + 1 for o in opens]
    lows = lows or [o - 1 for o in opens]
    closes = closes or list(opens)
    return pd.DataFrame({
        "date": [d0 + timedelta(days=i + 1) for i in range(n)],
        "open": opens, "high": highs, "low": lows, "close": closes})


class FakeCursor:
    def __init__(self, calls: list[tuple[str, list[tuple]]]) -> None:
        self.calls = calls

    def executemany(self, sql: str, rows) -> None:
        self.calls.append((sql, list(rows)))

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


class FakeConn:
    """写连接桩：记录 executemany 的 (SQL, 批次)。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[tuple]]] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.calls)

    @property
    def rows(self) -> list[tuple]:
        return [r for _, batch in self.calls for r in batch]


@pytest.fixture
def writer(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(outcome, "transaction",
                        lambda: contextlib.nullcontext(conn))
    return conn


# ────────────────────────── _pct ──────────────────────────

class TestPct:
    def test_up_and_down(self):
        assert outcome._pct(11.0, 10.0) == 10.0
        assert outcome._pct(9.0, 10.0) == -10.0

    def test_rounded_to_two_decimals(self):
        assert outcome._pct(10.505, 10.0) == 5.05
        assert outcome._pct(10.0, 3.0) == 233.33


# ────────────────────────── _outcome_row ──────────────────────────

class TestOutcomeRow:
    def stub(self, monkeypatch, base, nb, fam=None, nb_dates=None):
        """桩：基准日收盘价 / 前向 bar / 出口族 / 涨停止；返回调用记录。"""
        seen = {"base_q": [], "nb": [], "lim": [], "fam": []}

        def query_df(sql, params=()):
            seen["base_q"].append((sql, params))
            return pd.DataFrame({"close": []}) if base is None \
                else pd.DataFrame({"close": [base]})

        def next_bars(code, d0, n=5):
            seen["nb"].append((code, d0, n))
            return pd.DataFrame() if nb is None else nb

        def limit_up_on(code, d):
            seen["lim"].append((code, d))
            return d in (nb_dates or [])

        def exit_family(code, d0):
            seen["fam"].append((code, d0))
            return {"A_断板开盘": -2.5, "D_半仓": 1.25} if fam is None else fam

        monkeypatch.setattr(outcome, "query_df", query_df)
        monkeypatch.setattr(outcome, "_next_bars", next_bars)
        monkeypatch.setattr(outcome, "_limit_up_on", limit_up_on)
        monkeypatch.setattr(outcome, "_exit_family", exit_family)
        return seen

    def test_missing_base_close_is_none(self, monkeypatch):
        seen = self.stub(monkeypatch, None, bars([10.0]))
        assert outcome._outcome_row(D0, "000017", "BUY") is None
        assert seen["nb"] == []          # 缺基准价即短路，不查前向 bar

    def test_no_forward_bars_is_none(self, monkeypatch):
        self.stub(monkeypatch, 10.0, None)
        assert outcome._outcome_row(D0, "000017", "BUY") is None

    def test_short_window_incomplete(self, monkeypatch):
        """前向 3 根 <5：极值列 None、complete=False，其余列照算（右删失不近似）。"""
        nb = bars([10.0, 10.5, 11.0], highs=[10.5, 11.0, 11.5],
                  lows=[9.5, 10.0, 10.2], closes=[10.2, 10.8, 10.9])
        self.stub(monkeypatch, 10.0, nb, nb_dates=[nb["date"].iloc[0]])
        row = outcome._outcome_row(D0, "000017", "BUY")
        assert len(row) == len(COLS)
        assert val(row, "confirm_date") == D0
        assert val(row, "code") == "000017"
        assert val(row, "action") == "BUY"
        assert val(row, "entry_proxy") == 10.0
        assert val(row, "t1_gap") == 0.0                     # 开盘 10.0 / 昨收 10.0
        assert val(row, "t1_promote") is True                # 次日涨停（桩）
        assert val(row, "t1_close_ret") == 2.0               # 10.2 / 10.0
        assert val(row, "max_up5") is None
        assert val(row, "max_dd5") is None
        assert val(row, "t5_close_ret") is None
        assert val(row, "rule_ret_a") == -2.5
        assert val(row, "rule_ret_d") == 1.25
        assert val(row, "complete") is False

    def test_full_window_complete(self, monkeypatch):
        """前向 5 根齐：极值/第 5 日收益按 entry_proxy 计，complete=True。"""
        nb = bars([10.0, 11.0, 12.0, 13.0, 14.0],
                  highs=[10.5, 12.0, 13.0, 15.0, 16.0],
                  lows=[9.0, 10.0, 11.0, 12.0, 12.5],
                  closes=[10.2, 11.5, 12.5, 14.0, 15.0])
        self.stub(monkeypatch, 9.0, nb)
        row = outcome._outcome_row(D0, "000017", "BUY")
        assert val(row, "entry_proxy") == 10.0
        assert val(row, "t1_gap") == 11.11               # 10.0 / 9.0
        assert val(row, "t1_promote") is False           # 无涨停日（桩）
        assert val(row, "t1_close_ret") == 2.0
        assert val(row, "max_up5") == 60.0               # 16.0 / 10.0
        assert val(row, "max_dd5") == -10.0              # 9.0 / 10.0
        assert val(row, "t5_close_ret") == 50.0          # 15.0 / 10.0
        assert val(row, "complete") is True

    def test_window_capped_at_first_five_bars(self, monkeypatch):
        """桩多给 2 根（7 根）：第 6/7 根不得进入极值——窗口恒为前 5 根。"""
        nb = bars([10.0] * 7, highs=[10.0] * 5 + [99.0, 99.0],
                  lows=[10.0] * 5 + [-99.0, -99.0], closes=[10.0] * 7)
        self.stub(monkeypatch, 10.0, nb)
        row = outcome._outcome_row(D0, "000017", "BUY")
        assert val(row, "max_up5") == 0.0
        assert val(row, "max_dd5") == 0.0
        assert val(row, "complete") is True

    def test_entry_proxy_rounded_3dp(self, monkeypatch):
        self.stub(monkeypatch, 10.0, bars([10.1234]))
        row = outcome._outcome_row(D0, "000017", "BUY")
        assert val(row, "entry_proxy") == 10.123

    def test_exit_family_empty_keeps_row_with_none_legs(self, monkeypatch):
        """出口族为空（前向仅 1 根 / T+1 非法）→ A、D 记 None，行仍回填。"""
        self.stub(monkeypatch, 10.0, bars([10.0]), fam={})
        row = outcome._outcome_row(D0, "000017", "BUY")
        assert val(row, "rule_ret_a") is None
        assert val(row, "rule_ret_d") is None
        assert val(row, "complete") is False

    def test_forwards_confirm_date_to_dependencies(self, monkeypatch):
        seen = self.stub(monkeypatch, 10.0, bars([10.0], d0=D0))
        outcome._outcome_row(D0, "000017", "SELL")
        assert seen["base_q"][0][1] == ("000017", D0)
        assert seen["nb"] == [("000017", D0, 5)]
        assert seen["fam"] == [("000017", D0)]


# ────────────────────────── pending_signals ──────────────────────────

class TestPendingSignals:
    def stub(self, monkeypatch, df):
        seen = {}

        def query_df(sql, params=()):
            seen["sql"], seen["params"] = sql, params
            return df

        monkeypatch.setattr(outcome, "query_df", query_df)
        return seen

    def test_incremental_filters_incomplete(self, monkeypatch):
        df = pd.DataFrame({"confirm_date": [D0], "code": ["000017"],
                           "action": ["BUY"]})
        seen = self.stub(monkeypatch, df)
        assert outcome.pending_signals() == [(D0, "000017", "BUY")]
        assert "WHERE o.confirm_date IS NULL OR NOT o.complete" in seen["sql"]
        assert "LEFT JOIN signal_outcome o USING (confirm_date, code, action)" \
            in seen["sql"]

    def test_force_scans_all_signals(self, monkeypatch):
        seen = self.stub(monkeypatch, pd.DataFrame(
            {"confirm_date": [], "code": [], "action": []}))
        assert outcome.pending_signals(force=True) == []
        assert "WHERE" not in seen["sql"]

    def test_empty_result_is_empty_list(self, monkeypatch):
        self.stub(monkeypatch, pd.DataFrame(
            {"confirm_date": [], "code": [], "action": []}))
        assert outcome.pending_signals() == []

    def test_rows_preserve_query_order(self, monkeypatch):
        df = pd.DataFrame({"confirm_date": [D0, D0 + timedelta(days=2)],
                           "code": ["000017", "600371"],
                           "action": ["BUY", "SELL"]})
        self.stub(monkeypatch, df)
        assert outcome.pending_signals(force=True) == [
            (D0, "000017", "BUY"), (D0 + timedelta(days=2), "600371", "SELL")]


# ────────────────────────── _upsert_rows ──────────────────────────

def row(**over) -> tuple:
    base = {"confirm_date": D0, "code": "000017", "action": "BUY",
            "entry_proxy": 10.0, "t1_gap": 0.0, "t1_promote": True,
            "t1_close_ret": 2.0, "max_up5": 1.0, "max_dd5": -1.0,
            "t5_close_ret": 3.0, "rule_ret_a": None, "rule_ret_d": None,
            "complete": False}
    base.update(over)
    return tuple(base[c] for c in COLS)


class TestUpsertRows:
    def test_empty_rows_writes_nothing(self, monkeypatch):
        def boom():
            raise AssertionError("空行不得开事务")

        monkeypatch.setattr(outcome, "transaction", boom)
        assert outcome._upsert_rows([]) == 0

    def test_row_width_mismatch_raises_before_write(self, writer):
        with pytest.raises(ValueError, match="行宽"):
            outcome._upsert_rows([row()[:-1]])
        assert writer.calls == []

    def test_conflict_and_update_columns_consistent(self):
        """冲突键必须都在列内，且存在非冲突列（否则 SET 为空 = 非法 SQL）。"""
        assert set(outcome._CONFLICT) <= set(COLS)
        for col in outcome._CONFLICT:
            assert f"{col}=EXCLUDED.{col}" not in outcome._UPSERT_SQL

    def test_nan_and_inf_sanitized_to_null(self, writer):
        outcome._upsert_rows([row(max_up5=float("nan"), max_dd5=float("inf"),
                                  t5_close_ret=float("-inf"))])
        out = writer.rows[0]
        assert val(out, "max_up5") is None
        assert val(out, "max_dd5") is None
        assert val(out, "t5_close_ret") is None
        assert val(out, "t1_close_ret") == 2.0          # 有限值不被动
        assert val(out, "complete") is False            # 非 float 不被动
        assert val(out, "confirm_date") == D0

    def test_returns_written_row_count(self, writer):
        assert outcome._upsert_rows([row(), row(code="600371")]) == 2
        assert len(writer.rows) == 2

    def test_batched_by_config_upsert_batch(self, writer, monkeypatch):
        monkeypatch.setattr(outcome, "CONFIG", SimpleNamespace(UPSERT_BATCH=2))
        outcome._upsert_rows([row(code=f"00000{i}") for i in range(5)])
        assert [len(batch) for _, batch in writer.calls] == [2, 2, 1]
        assert [val(r, "code") for r in writer.rows] == \
            ["000000", "000001", "000002", "000003", "000004"]


# ────────────────────────── backfill ──────────────────────────

class TestBackfill:
    def test_skips_missing_rows_and_counts_written(self, monkeypatch, writer):
        """两个候选：缺基准价的返回 None（不入库），入库数 = 1（非候选数）。"""
        pending = [(D0, "000017", "BUY"), (D0, "600371", "SELL")]

        def query_df(sql, params=()):
            code, d = params
            return pd.DataFrame({"close": [9.0]}) if code == "000017" \
                else pd.DataFrame({"close": []})

        monkeypatch.setattr(outcome, "query_df", query_df)
        monkeypatch.setattr(outcome, "_next_bars",
                            lambda code, d0, n=5: bars([10.0]))
        monkeypatch.setattr(outcome, "_limit_up_on", lambda code, d: False)
        monkeypatch.setattr(outcome, "_exit_family", lambda code, d0: {})
        monkeypatch.setattr(outcome, "pending_signals", lambda force=False: pending)
        assert outcome.backfill() == 1
        assert [val(r, "code") for r in writer.rows] == ["000017"]

    def test_force_passed_through_to_pending(self, monkeypatch, writer):
        seen = {}

        def pending(force=False):
            seen["force"] = force
            return []

        monkeypatch.setattr(outcome, "pending_signals", pending)
        assert outcome.backfill(force=True) == 0
        assert seen["force"] is True
        assert writer.calls == []                        # 无候选即不写

    def test_second_run_is_noop_after_complete(self, monkeypatch, writer):
        """幂等：第二轮 pending 为空 → 0 写入、不开事务。"""
        state = {"n": 0}

        def pending(force=False):
            state["n"] += 1
            return [(D0, "000017", "BUY")] if state["n"] == 1 else []

        monkeypatch.setattr(outcome, "pending_signals", pending)
        monkeypatch.setattr(outcome, "query_df",
                            lambda sql, params=(): pd.DataFrame({"close": [9.0]}))
        monkeypatch.setattr(outcome, "_next_bars",
                            lambda code, d0, n=5: bars([10.0]))
        monkeypatch.setattr(outcome, "_limit_up_on", lambda code, d: False)
        monkeypatch.setattr(outcome, "_exit_family", lambda code, d0: {})
        assert outcome.backfill() == 1
        assert outcome.backfill() == 0
        assert len(writer.rows) == 1

    def test_upsert_receives_full_width_rows(self, monkeypatch, writer):
        monkeypatch.setattr(outcome, "pending_signals",
                            lambda force=False: [(D0, "000017", "BUY")])
        monkeypatch.setattr(outcome, "query_df",
                            lambda sql, params=(): pd.DataFrame({"close": [9.0]}))
        monkeypatch.setattr(outcome, "_next_bars",
                            lambda code, d0, n=5: bars([10.0] * 5))
        monkeypatch.setattr(outcome, "_limit_up_on", lambda code, d: False)
        monkeypatch.setattr(outcome, "_exit_family",
                            lambda code, d0: {"A_断板开盘": -1.0, "D_半仓": 0.5})
        outcome.backfill(force=True)
        assert [len(r) for r in writer.rows] == [len(COLS)]
        assert "ON CONFLICT (confirm_date, code, action) DO UPDATE SET" \
            in writer.calls[0][0]
