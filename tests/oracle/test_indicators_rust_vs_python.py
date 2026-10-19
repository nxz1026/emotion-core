"""对账测试：indicators（判据 + 连板）Rust vs Python 同输入同输出。

覆盖 `compute_derived` 全部 10 个输出字段 + 输出顺序（首次出现序 × 组内日期升序）：
is_limit_up / is_limit_down / is_one_word / is_exchange / is_bomb / touched_limit /
cont_days / amplitude / quality。

Python 参考实现：`src/emotion_core/algorithms/indicators.py`（两侧共用唯一涨停价实现
`utils/price.py`，价格一律整数分，消灭浮点）。

钉子（历史漂移，勿删）：
- `test_fold_suspended_row_does_not_break_streak`：停牌顺延行（volume == 0）既不加
  连板也不打断连板。2026-09-30 编出的 .so 与当时源码在这一点上分开过（源码 2026-10-17
  才补「零成交顺延行不打断」，产物没重编），实测同组输入 Rust cont_days=1 / Python=2。
- `test_board_branches`：北交所 92 号段（2026-10-07 修过「漏抄 92 → 拿主板 10%」）。
"""
from __future__ import annotations

import sys
from datetime import date

import pytest

sys.path.insert(0, "src")
from emotion_core.algorithms.indicators import compute_derived  # noqa: E402
from emotion_core.core import emotion_core_rust as rust  # noqa: E402
from emotion_core.domain.bar import Bar  # noqa: E402

# raw 行 = (code, date, open, high, low, close, pre_close, volume, turnover_rate)，价格单位分
Raw = tuple

FLAGS = ("is_limit_up", "is_limit_down", "is_one_word", "is_exchange",
         "is_bomb", "touched_limit", "cont_days")


def _py_bars(raw: list[Raw]) -> list[Bar]:
    return [Bar(code=c, date=d, open_cents=o, high_cents=h, low_cents=lo,
                close_cents=cl, pre_close_cents=pc, volume=v, turnover_rate=t)
            for c, d, o, h, lo, cl, pc, v, t in raw]


def _rs_bars(raw: list[Raw]) -> list:
    return [rust.Bar(c, d, o, h, lo, cl, pc, v, t) for c, d, o, h, lo, cl, pc, v, t in raw]


def _assert_same(raw: list[Raw]):
    """逐行逐字段对账，返回 (Python 结果, Rust 结果)。"""
    py = compute_derived(_py_bars(raw))
    rs = rust.compute_derived(_rs_bars(raw))
    assert len(rs) == len(py), f"行数不同：Rust {len(rs)} vs Python {len(py)}"
    for i, (p, r) in enumerate(zip(py, rs, strict=True)):
        where = f"第 {i} 行 {r.code}@{r.date}"
        assert r.code == p.code, f"{where}: code"
        assert r.date == str(p.date), f"{where}: date Rust {r.date} vs Python {p.date}"
        for f in FLAGS:
            assert getattr(r, f) == getattr(p, f), (
                f"{where}: {f} Rust {getattr(r, f)} vs Python {getattr(p, f)}")
        assert r.amplitude == p.amplitude, (
            f"{where}: amplitude Rust {r.amplitude} vs Python {p.amplitude}")
        assert r.quality == p.quality, f"{where}: quality"
    return py, rs


# ---------- 判据与连板基本盘 ----------

D1, D2, D3, D4, D5 = (date(2026, 3, day) for day in (2, 3, 4, 5, 6))

# 主板三连板：day1/day2 换手板，day3 一字板（low == 涨停价）
STREAK_MAIN = [
    ("600825", D1, 1050, 1100, 1050, 1100, 1000, 100, 3.5),
    ("600825", D2, 1150, 1210, 1150, 1210, 1100, 120, 4.1),
    ("600825", D3, 1331, 1331, 1331, 1331, 1210, 5, 0.2),
]


def test_streak_main_board():
    py, rs = _assert_same(STREAK_MAIN)
    assert [p.cont_days for p in py] == [1, 2, 3]
    assert [p.is_exchange for p in py] == [True, True, False]
    assert [p.is_one_word for p in py] == [False, False, True]
    assert [r.cont_days for r in rs] == [1, 2, 3], "Rust 连板链断在中途"


def test_fold_suspended_row_does_not_break_streak():
    """停牌顺延行（close == pre_close、volume == 0）不打断连板。

    600825 口径：连板两天 → 停牌一天 → 复牌继续涨停，cont_days 应为 3 而非 1。
    """
    raw = [
        ("600825", D1, 1050, 1100, 1050, 1100, 1000, 100, 3.5),
        ("600825", D2, 1150, 1210, 1150, 1210, 1100, 120, 4.1),
        ("600825", D3, 1210, 1210, 1210, 1210, 1210, 0, 0.0),      # 顺延行
        ("600825", D4, 1250, 1331, 1250, 1331, 1210, 130, 4.4),
    ]
    py, _ = _assert_same(raw)
    assert [p.cont_days for p in py] == [1, 2, 0, 3], "顺延行没被识别为「不累加不打断」"
    # 顺延行（close == pre_close）本身非涨停、cont_days 显式记 0，判据仍按当日价算
    assert py[2].is_limit_up is False and py[2].is_one_word is False
    assert py[3].cont_days == 3, "复牌首日应接在停牌前的高点上（3 而非 1）"


def test_bomb_break_and_limit_down():
    """炸板（触板未封）与跌停：touched_limit 与 is_bomb 的极性。"""
    raw = [
        ("600825", D1, 1050, 1100, 1050, 1100, 1000, 100, 3.5),   # 第 1 板
        ("600825", D2, 1100, 1210, 1050, 1099, 1100, 300, 9.9),   # 炸板：high 触板、close 未封
        ("600825", D3, 1000, 1000, 989, 989, 1099, 80, 2.0),      # 跌停：989 = (1099*900+500)//1000
        ("600825", D4, 989, 989, 900, 900, 989, 60, 1.5),         # 继续跌：不等于跌停价
    ]
    py, _ = _assert_same(raw)
    assert py[1].touched_limit is True and py[1].is_bomb is True
    assert py[1].is_limit_up is False and py[1].cont_days == 0
    assert py[2].is_limit_down is True and py[3].is_limit_down is False


def test_limit_up_is_exact_equality_not_ge():
    """涨停 = 收盘价「精确等于」涨停价（不是 >=）——多一分钱也不算。"""
    raw = [
        ("600825", D1, 1101, 1101, 1101, 1101, 1000, 10, 0.1),    # 1101 > 1100：不算涨停
        ("600825", D2, 1100, 1100, 1100, 1100, 1000, 10, 0.1),    # 1100 == 1100：一字板
    ]
    py, _ = _assert_same(raw)
    assert py[0].is_limit_up is False and py[0].cont_days == 0
    assert py[1].is_limit_up is True and py[1].is_one_word is True


# ---------- 板块分支 ----------

@pytest.mark.parametrize("code", ["600825", "000001", "002415", "601398", "603259", "605100"])
def test_board_branches_main_10pct(code):
    """主板 10%：pre_close=1000 → 涨停价 1100。"""
    py, _ = _assert_same([(code, D1, 1100, 1100, 1100, 1100, 1000, 10, 0.1)])
    assert py[0].is_limit_up is True


@pytest.mark.parametrize("code", ["300750", "301236", "688981"])
def test_board_branches_growth_20pct(code):
    """创业板/科创板 20%：pre_close=1000 → 涨停价 1200（按主板 1100 判会漏）。

    板号检查用「1200 不算 10% 涨停、1100 不算 20% 涨停」双向钉住。
    """
    py, _ = _assert_same([
        (code, D1, 1200, 1200, 1200, 1200, 1000, 10, 0.1),        # 20% 口径涨停
        (code, D2, 1100, 1100, 1100, 1100, 1000, 10, 0.1),        # 10% 口径不算
    ])
    assert py[0].is_limit_up is True
    assert py[1].is_limit_up is False


@pytest.mark.parametrize("code", ["430047", "830799", "871981", "920002", "bj430047"])
def test_board_branches_bse_30pct(code):
    """北交所 30%（含 2026-10-07 补的 920 号段）：pre_close=1000 → 1300。

    920002 曾落到 else 拿主板 10% —— 涨停价直接算错三成。
    """
    py, _ = _assert_same([
        (code, D1, 1300, 1300, 1300, 1300, 1000, 10, 0.1),
        (code, D2, 1100, 1100, 1100, 1100, 1000, 10, 0.1),
    ])
    assert py[0].is_limit_up is True, f"{code} 没吃到 30% 分支"
    assert py[1].is_limit_up is False


# ---------- 顺序、分组、边界 ----------

def test_output_order_first_seen_then_date_asc():
    """输出顺序 = code 首次出现序（等价 Python dict 插入序）× 组内日期升序。"""
    raw = [
        ("000002", D2, 100, 100, 100, 100, 100, 10, 0.1),
        ("600825", D3, 1100, 1100, 1100, 1100, 1000, 10, 0.1),
        ("000002", D1, 100, 100, 100, 100, 100, 10, 0.1),
        ("600825", D1, 1100, 1100, 1100, 1100, 1000, 10, 0.1),
    ]
    py, rs = _assert_same(raw)
    assert [(p.code, p.date) for p in py] == [
        ("000002", D1), ("000002", D2), ("600825", D1), ("600825", D3)]
    assert [(r.code, r.date) for r in rs] == [(p.code, str(p.date)) for p in py]


def test_empty_input():
    py, rs = _assert_same([])
    assert py == [] and rs == []


def test_pre_close_zero_no_division_by_zero():
    """pre_close=0（新股首日无昨收）→ 振幅 0.0，不抛除零。"""
    py, _ = _assert_same([("600825", D1, 0, 0, 0, 0, 0, 10, 0.0)])
    assert py[0].amplitude == 0.0


@pytest.mark.parametrize("high,low,pre_close,expected", [
    (1100, 1100, 1000, 0.0),        # 一字：high == low
    (1101, 1100, 1000, 0.1),        # 1/1000
    (1107, 1100, 1100, 0.6364),     # 7/1100*100 = 0.636363… → 四舍五入 4 位
    (3000, 2967, 3300, 1.0),        # 33/3300*100 = 1.0
    (3001, 2967, 3300, 1.0303),     # 34/3300*100 = 1.030303… → 1.0303
])
def test_amplitude_rounding(high, low, pre_close, expected):
    """振幅 = (high - low) / pre_close * 100，四舍五入到 4 位小数。"""
    py, _ = _assert_same([("600825", D1, low, high, low, high, pre_close, 10, 0.1)])
    assert py[0].amplitude == expected
