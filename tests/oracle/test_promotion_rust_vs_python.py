"""对账测试：晋级矩阵（promotion）Rust vs Python 同输入同输出。

覆盖 promotion.rs 的两个纯函数：
- `layer_row(date, layer, lo, pairs, min_denom, hi)` — 单层统计（筛选/名义/换手/
  比率/背离/失败负反馈）
- `matrix(pairs)` — 五层全量（顺序与标签 = PROMOTION_LAYERS）

Python 参考：`src/emotion_core/algorithms/promotion.py` 的 `_layer_row` / `_matrix`
（SQL 与持久化 `persist` 不在对账范围；`_PAIRS_SQL` 的已知 close=0 偏差是独立议题）。

钉子（历史漂移，勿删）：
- `test_rate_round4_half_even`：Python `round(x, 4)` 是「五取偶」，Rust 旧实现
  `(x*1e4).round()/1e4` 是「五远离零」。分母 32 时 1/32 = 0.03125 分叉为
  0.0312 vs 0.0313（晋级层昨日只数到 32 只很常见），2026-10-19 改为显式 round4。
- `test_matrix_ignores_date`：Rust `matrix()` 不接受 date，行内 date 恒为空串
  （Python `_matrix` 回填 trade_date）。这是接口差异，逐字段比对时须排除 date。
"""
from __future__ import annotations

import sys
from datetime import date

import pytest

sys.path.insert(0, "src")
from emotion_core.algorithms import promotion as py_promotion  # noqa: E402
from emotion_core.core import emotion_core_rust as rust  # noqa: E402
from emotion_core.utils.config import CONFIG  # noqa: E402

D1 = date(2026, 3, 2)

FIELDS = ("date", "layer", "promote_from", "promote_nominal", "promote_exchange",
          "rate_nominal", "rate_exchange", "divergence", "fail_perf")

# 覆盖全部五层 + 昨日 0 板（任何层都不选）
# 字段 = (昨日板数, 今日板数, 今日是否换手, 今日涨幅%|None)
PAIRS = [
    (1, 2, True, 2.0),     # 1->2 名义+换手成功
    (1, 2, False, 4.0),    # 1->2 一字晋级（名义算、换手不算）
    (1, 1, True, -3.0),    # 1->2 失败
    (1, 1, True, -1.0),    # 1->2 失败
    (1, 1, False, -2.0),   # 1->2 失败（该层失败样本 3 只 → fail_perf 有值）
    (2, 3, True, 1.0),     # 2->3 成功
    (2, 2, False, -5.0),   # 2->3 失败
    (2, 4, False, 6.0),    # 2->3 跳板晋级（>= 昨日+1 即可）
    (3, 3, True, -1.5),    # 3->4 失败
    (3, 4, False, 0.5),    # 3->4 成功
    (3, 3, False, None),   # 3->4 失败但缺价格 → 不进 fail_perf 样本
    (4, 5, True, 3.0),     # 4->5 成功
    (4, 4, True, -2.0),    # 4->5 失败
    (4, 5, True, 1.0),     # 4->5 成功
    (5, 6, False, 7.0),    # 5+->6+ 成功
    (5, 5, True, -4.0),    # 5+->6+ 失败
    (6, 7, True, 2.0),     # 5+->6+ 成功
    (0, 1, True, 1.0),     # 昨日 0 板：五层都不选
]


def _py_row(row: dict) -> tuple:
    return (str(row["date"]), row["layer"], row["promote_from"], row["promote_nominal"],
            row["promote_exchange"], row["rate_nominal"], row["rate_exchange"],
            row["divergence"], row["fail_perf"])


def _rs_row(r) -> tuple:
    return (r.date, r.layer, r.promote_from, r.promote_nominal, r.promote_exchange,
            r.rate_nominal, r.rate_exchange, r.divergence, r.fail_perf)


def test_layers_constant_in_sync():
    """五层标签/上下限与 Python PROMOTION_LAYERS 同序同值；min_denom 常量为 3。"""
    expected = [("1->2", 1, 1), ("2->3", 2, 2), ("3->4", 3, 3),
                ("4->5", 4, 4), ("5+->6+", 5, None)]
    assert [tuple(x) for x in py_promotion.PROMOTION_LAYERS] == expected
    assert CONFIG.PROMOTION_MIN_DENOM == 3, "Rust promotion::PROMOTION_MIN_DENOM 硬编码 3，需同步"
    assert [r.layer for r in rust.matrix(PAIRS)] == [x[0] for x in expected]


@pytest.mark.parametrize("layer,lo,hi", [
    ("1->2", 1, 1), ("2->3", 2, 2), ("3->4", 3, 3), ("4->5", 4, 4), ("5+->6+", 5, None),
])
@pytest.mark.parametrize("min_denom", [1, 3, 6])
def test_layer_row(layer, lo, hi, min_denom):
    """逐层逐字段比对（含 None 门槛与 fail_perf 样本门槛）。"""
    py = py_promotion._layer_row(D1, layer, lo, hi, PAIRS, min_denom)
    rs = rust.layer_row(D1.isoformat(), layer, lo, PAIRS, min_denom, hi)
    assert _rs_row(rs)[1:] == _py_row(py)[1:], (
        f"{layer} min_denom={min_denom}\n  Rust   {_rs_row(rs)}\n  Python {_py_row(py)}")


def test_layer_row_explicit_expectations():
    """手算基线：1->2 层（5 只，名义 2 / 换手 1，3 只失败）与 3->4 层（缺价格样本）。"""
    row = py_promotion._layer_row(D1, "1->2", 1, 1, PAIRS, 3)
    assert (row["promote_from"], row["promote_nominal"], row["promote_exchange"]) == (5, 2, 1)
    assert row["rate_nominal"] == 0.4 and row["rate_exchange"] == 0.2
    assert row["divergence"] == 0.2
    assert row["fail_perf"] == -2.0  # (-3 + -1 + -2) / 3
    assert _rs_row(rust.layer_row(D1.isoformat(), "1->2", 1, PAIRS, 3, 1))[2:] == (
        5, 2, 1, 0.4, 0.2, 0.2, -2.0)

    row = py_promotion._layer_row(D1, "3->4", 3, 3, PAIRS, 3)
    assert (row["promote_from"], row["promote_nominal"], row["promote_exchange"]) == (3, 1, 0)
    assert row["rate_nominal"] == 0.3333 and row["rate_exchange"] == 0.0
    assert row["divergence"] == 0.3333
    assert row["fail_perf"] is None  # 失败 2 只，其中 1 只缺价格 → 样本 1 < 3 门槛


@pytest.mark.parametrize("n,d,expected", [
    (1, 32, 0.0312),   # 0.03125：五取偶 → 0.0312（旧 Rust 给 0.0313）
    (3, 32, 0.0938),   # 0.09375：末位取偶 → 0.0938（两边同）
    (5, 32, 0.1562),   # 0.15625 → 0.1562
    (9, 32, 0.2812),   # 0.28125 → 0.2812
    (11, 32, 0.3438),  # 0.34375 → 0.3438
    (1, 64, 0.0156),   # 0.015625 → 0.0156
    (7, 64, 0.1094),   # 0.109375 → 0.1094
    (1, 3, 0.3333),
    (2, 3, 0.6667),
])
def test_rate_round4_half_even(n, d, expected):
    """晋级率取整与 Python `round(x, 4)` 逐值一致（含 2 的幂分母的 .5 钉子）。"""
    assert py_promotion._rate(n, d, 1) == expected
    pairs = [(1, 2, True, None)] * n + [(1, 1, True, None)] * (d - n)
    assert rust.layer_row("", "1->2", 1, pairs, 1, 1).rate_nominal == expected


def test_rate_denominator_gate():
    """分母（层内只数）< min_denom → None（F6 不猜），两侧同门槛。"""
    assert py_promotion._rate(2, 3, 3) == 0.6667
    assert py_promotion._rate(2, 3, 4) is None
    small = [(1, 2, True, 1.0)] * 2                       # 层内 2 只
    assert rust.layer_row("", "1->2", 1, small, 3, 1).rate_nominal is None
    assert rust.layer_row("", "1->2", 1, small, 2, 1).rate_nominal == 1.0
    assert rust.layer_row("", "1->2", 1, small, 3, 1).divergence is None  # 两个率都 None
    three = small + [(1, 2, True, 1.0)]
    assert rust.layer_row("", "1->2", 1, three, 3, 1).rate_nominal == 1.0
    assert py_promotion._layer_row(D1, "1->2", 1, 1, three, 3)["rate_nominal"] == 1.0


def test_fail_perf_sample_gate():
    """fail_perf 样本门槛与比率分母门槛同值（< min_denom → None）。"""
    two_failed = [(1, 1, True, -1.0), (1, 1, True, -3.0)]
    assert rust.layer_row("", "1->2", 1, two_failed, 3, 1).fail_perf is None
    assert rust.layer_row("", "1->2", 1, two_failed, 2, 1).fail_perf == -2.0
    three_failed = two_failed + [(1, 1, True, -5.0)]
    assert rust.layer_row("", "1->2", 1, three_failed, 3, 1).fail_perf == -3.0
    assert py_promotion._layer_row(D1, "1->2", 1, 1, three_failed, 3)["fail_perf"] == -3.0


def test_matrix_matches_python():
    """五层全量逐字段一致（date 除外，见下）。"""
    rs_rows = rust.matrix(PAIRS)
    py_rows = py_promotion._matrix(D1, PAIRS)
    assert len(rs_rows) == len(py_rows) == 5
    for rs, py in zip(rs_rows, py_rows, strict=True):
        assert _rs_row(rs)[1:] == _py_row(py)[1:], (
            f"{py['layer']}\n  Rust   {_rs_row(rs)}\n  Python {_py_row(py)}")


def test_matrix_ignores_date():
    """接口差异钉子：Rust matrix() 无 date 参数，行内 date 恒为 ""。"""
    assert all(r.date == "" for r in rust.matrix(PAIRS))
    assert [r["date"] for r in py_promotion._matrix(D1, PAIRS)] == [D1] * 5


def test_matrix_min_denom_hardcoded():
    """Rust matrix() 的分母门槛硬编码 3：昨日 2 只的层比率应为 None，3 只才有值。"""
    pairs = [(1, 2, True, 1.0), (1, 1, True, -1.0)]  # 1->2 层只有 2 只
    assert rust.matrix(pairs)[0].rate_nominal is None
    pairs3 = pairs + [(1, 1, True, -2.0)]
    assert rust.matrix(pairs3)[0].rate_nominal == 0.3333
    assert py_promotion._matrix(D1, pairs3)[0]["rate_nominal"] == 0.3333
