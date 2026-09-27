"""对账测试：evaluate Rust vs Python 同输入同输出（纯逻辑核心）。

覆盖 4 个纯逻辑函数（SQL 取数留在 Python 侧，不在对账范围）：
- split_rows(rows) — 条件行 / W- 警告行分离
- ebb_exit(nb, d0, ret, ebbs) — 退潮清仓腿（窗外 pending）
- exit_family(nb, d0, ebbs, lim, ret) — 出口规则族五腿
- rule_ret(fam) — 正式口径 E??A 合成（退潮优先）

Python 参考实现：emotion_core/algorithms/evaluate.py 的 _split_rows / _ebb_exit /
_exit_family / _rule_ret（其逐字照搬 lkl/services/evaluate.py）。_next_bars /
_limit_up_on 经 monkeypatch 换成内存 fixture，ebbs 显式传参避开 ebb_days SQL。
"""
from __future__ import annotations

import sys
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, "src")
from emotion_core.algorithms import evaluate  # noqa: E402
from emotion_core.algorithms.evaluate import (  # noqa: E402
    _cfg,
    _ebb_exit,
    _exit_family,
    _rule_ret,
    _split_rows,
)
from emotion_core.core import emotion_core_rust as rust  # noqa: E402

FAM_KEYS = ("A_断板开盘", "B_断板收盘", "C_两日收盘", "D_半仓", "E_退潮清仓")


def make_nb(rows):
    """(date, open, high, low, close) 行 → DataFrame（与 _next_bars 同列）。"""
    return pd.DataFrame(
        {
            "date": [r[0] for r in rows],
            "open": [r[1] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[3] for r in rows],
            "close": [r[4] for r in rows],
        }
    )


def make_ret(nb_df):
    """与 _exit_family 内部 ret 逐字一致：fee 取 _cfg 默认（CONFIG 未收录）。"""
    fee = _cfg("FEE_COMMISSION", 0.0002) * 2 + _cfg("FEE_STAMP", 0.0005)
    entry_p = float(nb_df["open"].iloc[0])

    def ret(p):
        return round((p / entry_p - 1 - fee) * 100, 2)

    return ret


def to_rust_bars(nb_df):
    """DataFrame → Vec[FwdBar]（Rust 侧入参）。"""
    return [
        rust.FwdBar(b.date, float(b.open), float(b.high), float(b.low), float(b.close))
        for b in nb_df.itertuples()
    ]


@pytest.fixture
def patched(monkeypatch):
    """把 _next_bars / _limit_up_on 换成内存 fixture；ebbs 显式传参避开 SQL。"""
    state = {}

    def _next_bars(code, d0, n=5):
        return state["nb"].head(n)

    def _limit_up_on(code, d):
        return state["lim"][d]

    monkeypatch.setattr(evaluate, "_next_bars", _next_bars)
    monkeypatch.setattr(evaluate, "_limit_up_on", _limit_up_on)
    return state


def run_exit_family(patched, bars, lim, d0, ebbs):
    """同输入调 Python 与 Rust 的出口规则族，返回 (py_fam, rust_fam, nb_used)。"""
    patched["nb"] = make_nb(bars)
    patched["lim"] = lim
    py_fam = _exit_family("600000", d0, ebbs)
    nb_used = patched["nb"].head(5)
    ret = make_ret(nb_used) if not nb_used.empty else None
    rust_fam = rust.exit_family(
        to_rust_bars(nb_used),
        str(d0),
        [str(d) for d in ebbs],
        [lim[b.date] for b in nb_used.itertuples()],
        ret,
    )
    return py_fam, rust_fam, nb_used


def assert_fam_equal(py_fam, rust_fam, tag):
    """逐字段比对五腿（缺键与 None 等价，与 Python dict.get 语义一致）。"""
    assert set(rust_fam) == set(py_fam), f"{tag}: 键集不一致 rust={set(rust_fam)} py={set(py_fam)}"
    for k in FAM_KEYS:
        assert rust_fam.get(k) == py_fam.get(k), (
            f"{tag}: {k} 不一致 rust={rust_fam.get(k)} py={py_fam.get(k)}"
        )


# ---------- exit_family / rule_ret 对账 ----------

# 每例：(bars, lim, d0, ebbs)。bars 为 (date, open, high, low, close)，
# lim 为 {date: is_limit_up}，ebbs 为退潮日序列（显式注入）。
CASES = [
    # 空窗口 → 空字典
    ("空 nb", [], {}, date(2024, 3, 1), []),
    # 单 bar → V4② 制度非法（当日买当日卖）→ 空字典
    ("单 bar", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00)],
     {date(2024, 3, 4): True}, date(2024, 3, 1), []),
    # n=2 双涨停：窗内无断板 → A/B/D 全 None，只有 C
    ("双涨停", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00),
             (date(2024, 3, 5), 11.00, 12.10, 11.00, 12.10)],
     {date(2024, 3, 4): True, date(2024, 3, 5): True}, date(2024, 3, 1), []),
    # n=3 bar1 断板：brk=1 → B=close[1]；fb=2 → A=open[2]
    ("T+2 断板", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00),
              (date(2024, 3, 5), 11.00, 11.50, 10.20, 10.50),
              (date(2024, 3, 6), 10.60, 11.00, 10.40, 11.00)],
     {date(2024, 3, 4): True, date(2024, 3, 5): False, date(2024, 3, 6): True},
     date(2024, 3, 1), []),
    # brk==0（T+1 断板）：fb=1 → A=open[1]；si 顺延 1 → B=close[1]
    ("T+1 断板顺延", [(date(2024, 3, 4), 10.00, 10.50, 9.90, 10.30),
                (date(2024, 3, 5), 10.40, 10.90, 10.30, 10.80)],
     {date(2024, 3, 4): False, date(2024, 3, 5): True}, date(2024, 3, 1), []),
    # 断板在最后一根 bar：brk=2 → B=close[2]；fb=None → A=None
    ("断板在窗尾", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00),
               (date(2024, 3, 5), 11.00, 12.10, 11.00, 12.10),
               (date(2024, 3, 6), 12.10, 12.80, 11.90, 12.30)],
     {date(2024, 3, 4): True, date(2024, 3, 5): True, date(2024, 3, 6): False},
     date(2024, 3, 1), []),
    # 全涨停 5 根：右删失 → A/B/D None，C=close[1]
    ("全涨停右删失", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00),
                 (date(2024, 3, 5), 11.00, 12.10, 11.00, 12.10),
                 (date(2024, 3, 6), 12.10, 13.31, 12.10, 13.31),
                 (date(2024, 3, 7), 13.31, 14.64, 13.31, 14.64),
                 (date(2024, 3, 8), 14.64, 16.10, 14.64, 16.10)],
     {date(2024, 3, 4): True, date(2024, 3, 5): True, date(2024, 3, 6): True,
      date(2024, 3, 7): True, date(2024, 3, 8): True}, date(2024, 3, 1), []),
    # E 腿：退潮日在窗内（bar 2）→ E=close[2]
    ("退潮日在窗内", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00),
                 (date(2024, 3, 5), 11.00, 12.10, 11.00, 12.10),
                 (date(2024, 3, 6), 12.10, 12.60, 11.80, 12.20),
                 (date(2024, 3, 7), 12.20, 12.90, 12.00, 12.50)],
     {date(2024, 3, 4): True, date(2024, 3, 5): True, date(2024, 3, 6): False,
      date(2024, 3, 7): True},
     date(2024, 3, 1), [date(2024, 3, 6)]),
    # E 腿：退潮日在窗外 → None（pending，不按窗口末近似）
    ("退潮日在窗外", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00),
                 (date(2024, 3, 5), 11.00, 12.10, 11.00, 12.10)],
     {date(2024, 3, 4): True, date(2024, 3, 5): True},
     date(2024, 3, 1), [date(2024, 3, 15)]),
    # E 腿：ebb == d0 被 d>d0 过滤，取序列中下一个 ebb
    ("ebb 等于 d0 跳过", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00),
                   (date(2024, 3, 5), 11.00, 12.10, 11.00, 12.10)],
     {date(2024, 3, 4): True, date(2024, 3, 5): True},
     date(2024, 3, 1), [date(2024, 3, 1), date(2024, 3, 5)]),
    # E 腿：ebb 全部早于 d0 → None
    ("ebb 全早于 d0", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00),
                 (date(2024, 3, 5), 11.00, 12.10, 11.00, 12.10)],
     {date(2024, 3, 4): True, date(2024, 3, 5): True},
     date(2024, 3, 1), [date(2024, 2, 28)]),
    # E 腿：退潮日恰为信号后首根 bar（bar0）→ E=close[0]
    ("退潮日为首根 bar", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00),
                   (date(2024, 3, 5), 11.00, 12.10, 11.00, 12.10)],
     {date(2024, 3, 4): True, date(2024, 3, 5): True},
     date(2024, 3, 1), [date(2024, 3, 4)]),
    # 负收益：T+2 收盘跌破买入价
    ("负收益", [(date(2024, 3, 4), 10.00, 10.20, 9.80, 10.20),
             (date(2024, 3, 5), 10.10, 10.30, 9.70, 9.90)],
     {date(2024, 3, 4): True, date(2024, 3, 5): False}, date(2024, 3, 1), []),
    # n=4 多次断板：brk=1 → B=close[1]；fb=2 → A=open[2]
    ("多次断板", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00),
              (date(2024, 3, 5), 11.00, 11.30, 10.80, 11.10),
              (date(2024, 3, 6), 11.20, 11.80, 11.00, 11.50),
              (date(2024, 3, 7), 11.50, 11.90, 11.20, 11.60)],
     {date(2024, 3, 4): True, date(2024, 3, 5): False, date(2024, 3, 6): True,
      date(2024, 3, 7): False},
     date(2024, 3, 1), []),
]


@pytest.mark.parametrize("tag,bars,lim,d0,ebbs", CASES, ids=[c[0] for c in CASES])
def test_exit_family(patched, tag, bars, lim, d0, ebbs):
    py_fam, rust_fam, _ = run_exit_family(patched, bars, lim, d0, ebbs)
    assert_fam_equal(py_fam, rust_fam, tag)


@pytest.mark.parametrize("tag,bars,lim,d0,ebbs", CASES, ids=[c[0] for c in CASES])
def test_rule_ret_end_to_end(patched, tag, bars, lim, d0, ebbs):
    """rule_ret 正式口径：Python _rule_ret（mock SQL）vs Rust 组合 exit_family+rule_ret。"""
    py_fam, rust_fam, _ = run_exit_family(patched, bars, lim, d0, ebbs)
    py_rule = _rule_ret("600000", d0, ebbs)  # state 已由 run_exit_family 填充
    rust_rule = rust.rule_ret(rust_fam)
    expected = (py_fam.get("E_退潮清仓")
                if py_fam.get("E_退潮清仓") is not None
                else py_fam.get("A_断板开盘"))
    assert rust_rule == py_rule == expected, tag


# ---------- ebb_exit 直接对账 ----------

EBB_CASES = [
    # (tag, bars, d0, ebbs)
    ("ebb 在窗内", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00),
                 (date(2024, 3, 5), 11.00, 12.10, 11.00, 12.10),
                 (date(2024, 3, 6), 12.10, 12.60, 11.80, 12.20)],
     date(2024, 3, 1), [date(2024, 3, 5)]),
    ("ebb 在窗外", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00)],
     date(2024, 3, 1), [date(2024, 3, 11)]),
    ("ebb 等于 d0", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00)],
     date(2024, 3, 1), [date(2024, 3, 1)]),
    ("ebb 为空", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00)],
     date(2024, 3, 1), []),
    ("ebb 取首个晚于 d0", [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00),
                    (date(2024, 3, 5), 11.00, 12.10, 11.00, 12.10)],
     date(2024, 3, 1), [date(2024, 2, 29), date(2024, 3, 4), date(2024, 3, 5)]),
    ("空 nb 有 ebb", [], date(2024, 3, 1), [date(2024, 3, 5)]),
]


@pytest.mark.parametrize("tag,bars,d0,ebbs", EBB_CASES, ids=[c[0] for c in EBB_CASES])
def test_ebb_exit(tag, bars, d0, ebbs):
    nb_df = make_nb(bars)
    ret = make_ret(nb_df) if not nb_df.empty else None
    py_e = _ebb_exit(nb_df, d0, ret, ebbs)
    rust_e = rust.ebb_exit(to_rust_bars(nb_df), str(d0), ret, [str(d) for d in ebbs])
    assert rust_e == py_e, f"{tag}: rust={rust_e} py={py_e}"


# ---------- rule_ret 显式 fam 字典对账 ----------

@pytest.mark.parametrize("fam,expected", [
    ({"E_退潮清仓": None, "A_断板开盘": 1.5}, 1.5),     # E=None → 回落 A
    ({"E_退潮清仓": 2.0, "A_断板开盘": 1.5}, 2.0),     # E 非 None → 取 E
    ({"A_断板开盘": 1.0}, 1.0),                          # 无 E 键 → A
    ({"E_退潮清仓": 2.0}, 2.0),                          # 只有 E
    ({"E_退潮清仓": None}, None),                        # E=None 且无 A → None
    ({"A_断板开盘": None}, None),                        # A=None → None
    ({"A_断板开盘": -1.25}, -1.25),                      # 负收益
    ({}, None),                                          # 空 fam（n<2/空窗口）
    ({"E_退潮清仓": 0.0, "A_断板开盘": 3.0}, 0.0),     # E=0.0 非 None → 取 0.0
])
def test_rule_ret(fam, expected):
    assert rust.rule_ret(fam) == expected


# ---------- split_rows 对账 ----------

@pytest.mark.parametrize("rows", [
    [],
    [("C1", True, "唯一换手高标")],
    [("W1", True, "⚠ 扎堆")],
    [("C1", False, "否"), ("C2", None, "未知"), ("W2", None, "⚠ 警告")],
    [("W1", False, "w1"), ("W2", None, "w2"), ("C1", True, "c1")],
    [("C5", None, "不可核验")],
    [("W3", True, "w"), ("W4", True, "w"), ("W5", False, "w")],
])
def test_split_rows(rows):
    r_conds, r_warns = rust.split_rows(rows)
    p_conds, p_warns = _split_rows(rows)
    assert r_conds == [tuple(r) for r in p_conds]
    assert r_warns == [tuple(r) for r in p_warns]


# ---------- 费率口径 ----------

def test_fee_defaults_match_lkl():
    """_cfg 对 CONFIG 未收录键回落 lkl config.py 原值（0.0002 / 0.0005）。"""
    assert _cfg("FEE_COMMISSION", 0.0002) == 0.0002
    assert _cfg("FEE_STAMP", 0.0005) == 0.0005


def test_fee_used_by_both_sides(patched):
    """Rust 侧 ret 闭包与 Python _exit_family 内部 fee 逐位一致（C 腿即校验）。"""
    bars = [(date(2024, 3, 4), 10.00, 11.00, 10.00, 11.00),
            (date(2024, 3, 5), 11.00, 12.10, 11.00, 12.10)]
    py_fam, rust_fam, _ = run_exit_family(
        patched, bars,
        {date(2024, 3, 4): True, date(2024, 3, 5): True},
        date(2024, 3, 1), [])
    assert rust_fam["C_两日收盘"] == py_fam["C_两日收盘"] == 20.91
