"""对账测试：outcome Rust vs Python 同输入同输出。

覆盖 outcome.py 的纯逻辑：
- _pct(new, base) — 百分比变化（含 round-half-even 边界）
- _outcome_row — 单信号前瞻行组装（SQL 取数由 Python 侧桩注入）

SQL 取数（query_df / _next_bars / _limit_up_on / _exit_family）留在 Python
侧，Rust 只算纯行组装逻辑；对账时同输入分别调 Python _outcome_row 与 Rust
outcome_row，逐字段比对。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

import pandas as pd
import pytest

sys.path.insert(0, "src")
from emotion_core.algorithms import outcome  # noqa: E402
from emotion_core.core import emotion_core_rust as rust  # noqa: E402

D0 = date(2026, 9, 8)
COLS = outcome._COLS


def val(row: tuple, name: str):
    """按库表列序取行内字段（断言用，列序由 _COLS 定义）。"""
    return row[COLS.index(name)]


def bars(opens, highs=None, lows=None, closes=None, d0: date = D0) -> pd.DataFrame:
    """前向 bar 帧：date 从信号日次日起逐自然日（只为排序/取值语义）。"""
    n = len(opens)
    highs = highs or [o + 1 for o in opens]
    lows = lows or [o - 1 for o in opens]
    closes = closes or list(opens)
    return pd.DataFrame({
        "date": [d0 + timedelta(days=i + 1) for i in range(n)],
        "open": opens, "high": highs, "low": lows, "close": closes})


class Harness:
    """桩 outcome 的 SQL 依赖，提供 Python / Rust 双入口同输入对账。"""

    def __init__(self, monkeypatch):
        self.base = None          # None = 信号日无收盘价（缺数）
        self.nb = None            # None = 前向无 bar（缺数）
        self.fam = None           # None = 默认出口族；{} = 空族（缺键）
        self.limit_up_dates: set[date] = set()

        def query_df(sql, params=()):
            return pd.DataFrame({"close": []}) if self.base is None \
                else pd.DataFrame({"close": [self.base]})

        def next_bars(code, d0, n=5):
            return pd.DataFrame() if self.nb is None else self.nb

        def limit_up_on(code, d):
            return d in self.limit_up_dates

        def exit_family(code, d0):
            return {"A_断板开盘": -2.5, "D_半仓": 1.25} if self.fam is None \
                else self.fam

        monkeypatch.setattr(outcome, "query_df", query_df)
        monkeypatch.setattr(outcome, "_next_bars", next_bars)
        monkeypatch.setattr(outcome, "_limit_up_on", limit_up_on)
        monkeypatch.setattr(outcome, "_exit_family", exit_family)

    def py_row(self, d=D0, code="000017", action="BUY"):
        return outcome._outcome_row(d, code, action)

    def rust_row(self, d=D0, code="000017", action="BUY"):
        if self.nb is None:
            nb_open = nb_high = nb_low = nb_close = nb_lu = []
        else:
            nb_open = list(self.nb["open"])
            nb_high = list(self.nb["high"])
            nb_low = list(self.nb["low"])
            nb_close = list(self.nb["close"])
            nb_lu = [dt in self.limit_up_dates for dt in self.nb["date"]]
        fam = {"A_断板开盘": -2.5, "D_半仓": 1.25} if self.fam is None else self.fam
        return rust.outcome_row(
            d.isoformat(), code, action, self.base,
            nb_open, nb_high, nb_low, nb_close, nb_lu,
            fam.get("A_断板开盘"), fam.get("D_半仓"))


def assert_row_matches(py_row: tuple, r) -> None:
    """Python tuple 与 Rust OutcomeRow 逐字段精确比对。"""
    assert r is not None
    assert val(py_row, "confirm_date").isoformat() == r.confirm_date
    assert val(py_row, "code") == r.code
    assert val(py_row, "action") == r.action
    assert val(py_row, "entry_proxy") == r.entry_proxy
    assert val(py_row, "t1_gap") == r.t1_gap
    assert val(py_row, "t1_promote") == r.t1_promote
    assert val(py_row, "t1_close_ret") == r.t1_close_ret
    assert val(py_row, "max_up5") == r.max_up5
    assert val(py_row, "max_dd5") == r.max_dd5
    assert val(py_row, "t5_close_ret") == r.t5_close_ret
    assert val(py_row, "rule_ret_a") == r.rule_ret_a
    assert val(py_row, "rule_ret_d") == r.rule_ret_d
    assert val(py_row, "complete") == r.complete


# ────────────────────────── _pct ──────────────────────────

@pytest.mark.parametrize("new,base,expected", [
    (11.0, 10.0, 10.0),
    (9.0, 10.0, -10.0),
    (10.0, 10.0, 0.0),
    (10.505, 10.0, 5.05),
    (10.0, 3.0, 233.33),
    (1.0, 3.0, -66.67),
    (0.125, 1.0, -87.5),
    (100.0, 7.0, 1328.57),
])
def test_pct(new, base, expected):
    assert rust.pct(new, base) == outcome._pct(new, base) == expected


@pytest.mark.parametrize("new,base,expected", [
    (125.125, 100.0, 25.12),    # 精确 25.125，half-even 向下（末位 2 偶）
    (125.625, 100.0, 25.63),    # 浮点路径 > 25.625 → 向上
    (126.125, 100.0, 26.12),    # 精确 26.125，half-even 向下
    (126.625, 100.0, 26.63),    # 浮点路径 > 26.625 → 向上
    (1001.25, 1000.0, 0.12),    # 精确 0.125，half-even 向下
    (102.675, 100.0, 2.68),     # 浮点路径 > 2.675 → 向上
])
def test_pct_round_half_even(new, base, expected):
    """Python round 是十进制 half-even，Rust 侧 py_round 同语义。"""
    assert rust.pct(new, base) == outcome._pct(new, base) == expected


# ────────────────────────── _outcome_row：缺数短路 ──────────────────────────

def test_missing_base_close_returns_none(monkeypatch):
    """信号日无收盘价 → None（缺数不入库，不查前向 bar）。"""
    h = Harness(monkeypatch)
    h.base = None
    h.nb = bars([10.0])
    assert h.py_row() is None
    assert h.rust_row() is None


def test_no_forward_bars_returns_none(monkeypatch):
    """前向无 bar → None。"""
    h = Harness(monkeypatch)
    h.base = 10.0
    h.nb = None
    assert h.py_row() is None
    assert h.rust_row() is None


# ────────────────────────── _outcome_row：窗口完整性 ──────────────────────────

@pytest.mark.parametrize("n_bars", [1, 2, 3, 4])
def test_short_window_incomplete(monkeypatch, n_bars):
    """前向 <5 根：极值列 None、complete=False，其余列照算（右删失不近似）。"""
    h = Harness(monkeypatch)
    h.base = 10.0
    h.nb = bars([10.0 + i * 0.5 for i in range(n_bars)],
                highs=[10.5 + i * 0.5 for i in range(n_bars)],
                lows=[9.5 - i * 0.1 for i in range(n_bars)],
                closes=[10.2 + i * 0.3 for i in range(n_bars)])
    py_row = h.py_row()
    assert val(py_row, "max_up5") is None
    assert val(py_row, "max_dd5") is None
    assert val(py_row, "t5_close_ret") is None
    assert val(py_row, "complete") is False
    assert_row_matches(py_row, h.rust_row())


def test_full_window_complete(monkeypatch):
    """前向 5 根齐：极值/第 5 日收益按 entry 计，complete=True。"""
    h = Harness(monkeypatch)
    h.base = 9.0
    h.nb = bars([10.0, 11.0, 12.0, 13.0, 14.0],
                highs=[10.5, 12.0, 13.0, 15.0, 16.0],
                lows=[9.0, 10.0, 11.0, 12.0, 12.5],
                closes=[10.2, 11.5, 12.5, 14.0, 15.0])
    py_row = h.py_row()
    assert val(py_row, "entry_proxy") == 10.0
    assert val(py_row, "t1_gap") == 11.11
    assert val(py_row, "t1_close_ret") == 2.0
    assert val(py_row, "max_up5") == 60.0
    assert val(py_row, "max_dd5") == -10.0
    assert val(py_row, "t5_close_ret") == 50.0
    assert val(py_row, "complete") is True
    assert_row_matches(py_row, h.rust_row())


def test_window_capped_at_first_five_bars(monkeypatch):
    """桩多给 2 根（7 根）：第 6/7 根不得进入极值——窗口恒为前 5 根。"""
    h = Harness(monkeypatch)
    h.base = 10.0
    h.nb = bars([10.0] * 7, highs=[10.0] * 5 + [99.0, 99.0],
                lows=[10.0] * 5 + [-99.0, -99.0], closes=[10.0] * 7)
    py_row = h.py_row()
    assert val(py_row, "max_up5") == 0.0
    assert val(py_row, "max_dd5") == 0.0
    assert val(py_row, "t5_close_ret") == 0.0
    assert val(py_row, "complete") is True
    assert_row_matches(py_row, h.rust_row())


# ────────────────────────── _outcome_row：舍入口径 ──────────────────────────

def test_entry_proxy_rounds_to_3dp(monkeypatch):
    h = Harness(monkeypatch)
    h.base = 10.0
    h.nb = bars([10.1234])
    py_row = h.py_row()
    assert val(py_row, "entry_proxy") == 10.123
    assert_row_matches(py_row, h.rust_row())


@pytest.mark.parametrize("entry,base", [
    (10.0004, 9.0),     # pct(entry)=11.12 vs pct(round3)=11.11 —— 可区分
    (10.1234, 9.0),     # 12.48% —— 双重舍入恰好一致，防回归
    (3.3333, 3.0),      # 11.11% —— 恰好一致，防回归
])
def test_returns_use_unrounded_entry(monkeypatch, entry, base):
    """关键口径：t1_gap / t1_close_ret / 极值均相对未舍入 entry 计算，
    只有 entry_proxy 列 round 3 位。"""
    h = Harness(monkeypatch)
    h.base = base
    h.nb = bars([entry], closes=[entry * 1.02])
    py_row = h.py_row()
    assert val(py_row, "entry_proxy") == round(entry, 3)
    # Python 参考：_pct(entry, base) 用原始 entry
    assert val(py_row, "t1_gap") == outcome._pct(entry, base)
    assert val(py_row, "t1_close_ret") == outcome._pct(entry * 1.02, entry)
    assert_row_matches(py_row, h.rust_row())


def test_unrounded_entry_can_differ_from_rounded(monkeypatch):
    """entry=10.0004 时 t1_gap 必须用未舍入 entry（11.12），
    若错用 entry_proxy=10.0 则得 11.11 —— 本用例捕捉该回归。"""
    h = Harness(monkeypatch)
    h.base = 9.0
    h.nb = bars([10.0004])
    py_row = h.py_row()
    assert val(py_row, "t1_gap") == 11.12
    assert val(py_row, "t1_gap") != outcome._pct(round(10.0004, 3), 9.0)
    assert_row_matches(py_row, h.rust_row())


# ────────────────────────── _outcome_row：出口族 ──────────────────────────

def test_exit_family_empty_gives_none_legs(monkeypatch):
    """出口族为空（前向仅 1 根 / T+1 非法）→ A、D 记 None，行仍回填。"""
    h = Harness(monkeypatch)
    h.base = 10.0
    h.nb = bars([10.0])
    h.fam = {}
    py_row = h.py_row()
    assert val(py_row, "rule_ret_a") is None
    assert val(py_row, "rule_ret_d") is None
    assert val(py_row, "complete") is False
    assert_row_matches(py_row, h.rust_row())


def test_exit_family_custom_values(monkeypatch):
    h = Harness(monkeypatch)
    h.base = 10.0
    h.nb = bars([10.0])
    h.fam = {"A_断板开盘": -1.25, "D_半仓": 0.75}
    py_row = h.py_row()
    assert val(py_row, "rule_ret_a") == -1.25
    assert val(py_row, "rule_ret_d") == 0.75
    assert_row_matches(py_row, h.rust_row())


# ────────────────────────── _outcome_row：涨停止 ──────────────────────────

@pytest.mark.parametrize("promote", [True, False])
def test_t1_promote(monkeypatch, promote):
    h = Harness(monkeypatch)
    h.base = 10.0
    h.nb = bars([10.0])
    if promote:
        h.limit_up_dates = {h.nb["date"].iloc[0]}
    py_row = h.py_row()
    assert val(py_row, "t1_promote") is promote
    assert_row_matches(py_row, h.rust_row())


# ────────────────────────── _outcome_row：收益方向 ──────────────────────────

def test_negative_returns(monkeypatch):
    h = Harness(monkeypatch)
    h.base = 10.0
    h.nb = bars([9.0], highs=[9.5], lows=[8.0], closes=[8.5])
    py_row = h.py_row()
    assert val(py_row, "t1_gap") == -10.0
    assert val(py_row, "t1_close_ret") == -5.56
    assert_row_matches(py_row, h.rust_row())


def test_zero_return(monkeypatch):
    h = Harness(monkeypatch)
    h.base = 10.0
    h.nb = bars([10.0], closes=[10.0])
    py_row = h.py_row()
    assert val(py_row, "t1_gap") == 0.0
    assert val(py_row, "t1_close_ret") == 0.0
    assert_row_matches(py_row, h.rust_row())


# ────────────────────────── _outcome_row：透传字段 ──────────────────────────

@pytest.mark.parametrize("action", ["BUY", "SELL", "SECONDARY"])
def test_action_passthrough(monkeypatch, action):
    """pending_signals 不按 action 过滤——三种 action 一律回填。"""
    h = Harness(monkeypatch)
    h.base = 10.0
    h.nb = bars([10.0])
    py_row = h.py_row(action=action)
    assert val(py_row, "action") == action
    assert_row_matches(py_row, h.rust_row(action=action))


def test_confirm_date_passthrough(monkeypatch):
    d1 = date(2026, 1, 15)
    h = Harness(monkeypatch)
    h.base = 10.0
    h.nb = bars([10.0], d0=d1)
    py_row = h.py_row(d=d1)
    assert val(py_row, "confirm_date") == d1
    assert_row_matches(py_row, h.rust_row(d=d1))


def test_code_passthrough(monkeypatch):
    h = Harness(monkeypatch)
    h.base = 10.0
    h.nb = bars([10.0])
    py_row = h.py_row(code="600371")
    assert val(py_row, "code") == "600371"
    assert_row_matches(py_row, h.rust_row(code="600371"))


# ────────────────────────── _outcome_row：极值取值口径 ──────────────────────────

def test_max_dd5_uses_min_low_not_close(monkeypatch):
    """max_dd5 取窗口内最低价（low.min），不是收盘价。"""
    h = Harness(monkeypatch)
    h.base = 10.0
    h.nb = bars([10.0] * 5, highs=[11.0] * 5,
                lows=[10.0, 9.0, 10.0, 10.0, 10.0], closes=[10.5] * 5)
    py_row = h.py_row()
    assert val(py_row, "max_dd5") == -10.0
    assert val(py_row, "max_up5") == 10.0
    assert_row_matches(py_row, h.rust_row())


def test_t5_close_ret_uses_fifth_bar_close(monkeypatch):
    """t5_close_ret 取第 5 根 close，不是窗口末 high/low。"""
    h = Harness(monkeypatch)
    h.base = 10.0
    h.nb = bars([10.0] * 5, highs=[12.0] * 5, lows=[8.0] * 5,
                closes=[10.0, 10.0, 10.0, 10.0, 13.0])
    py_row = h.py_row()
    assert val(py_row, "t5_close_ret") == 30.0
    assert val(py_row, "max_up5") == 20.0
    assert val(py_row, "max_dd5") == -20.0
    assert_row_matches(py_row, h.rust_row())


# ────────────────────────── OutcomeRow 类 ──────────────────────────

def test_outcome_row_class_new_and_accessors():
    """OutcomeRow #[new] 构造 + 字段读写。"""
    r = rust.OutcomeRow(
        "2026-09-08", "000017", "BUY", 10.0, 0.0, False, 2.0,
        None, None, None, -2.5, 1.25, False)
    assert r.confirm_date == "2026-09-08"
    assert r.code == "000017"
    assert r.action == "BUY"
    assert r.entry_proxy == 10.0
    assert r.t1_gap == 0.0
    assert r.t1_promote is False
    assert r.t1_close_ret == 2.0
    assert r.max_up5 is None
    assert r.max_dd5 is None
    assert r.t5_close_ret is None
    assert r.rule_ret_a == -2.5
    assert r.rule_ret_d == 1.25
    assert r.complete is False
    # setter
    r.complete = True
    r.max_up5 = 60.0
    assert r.complete is True
    assert r.max_up5 == 60.0


def test_outcome_row_class_clone():
    r = rust.OutcomeRow(
        "2026-09-08", "000017", "BUY", 10.0, 0.0, False, 2.0,
        None, None, None, None, None, False)
    r2 = r.clone()
    assert r2.code == "000017"
    assert r2.complete is False
