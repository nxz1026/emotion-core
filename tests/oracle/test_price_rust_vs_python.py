"""对账测试：涨停/跌停价与判据 Rust vs Python 逐板块对等。

`src/emotion_core/core/src/indicators.rs` 的注释声称「现由
tests/oracle/test_price_rust_vs_python.py 逐板块对等钉住」—— 本文件就是它
（2026-10-19 之前该文件并不存在，注释指向空气）。

Rust 侧价格函数是私有 fn，只能经 `compute_derived` 的判据间接验证；每个板块
分支都用「按本板块口径算出的价 → 判涨停」和「按邻板块口径算出的价 → 不判涨停」
双向钉死。Python 参考实现：`src/emotion_core/utils/price.py`（唯一涨停价实现，
整数分运算，四舍五入 = SQL 整数式 `(x*pct + 50)/100`）。
"""

from __future__ import annotations

import sys
from datetime import date

import pytest

sys.path.insert(0, "src")
from emotion_core.core import emotion_core_rust as rust  # noqa: E402
from emotion_core.utils.price import (  # noqa: E402
    board_pct_milli,
    limit_down_price_cents,
    limit_up_price_cents,
)

D1 = date(2026, 3, 2)

# (代码, 期望千分比)：主板 100 / 创业科创 200 / 北交所 300
BOARDS = [
    ("600825", 100),
    ("601398", 100),
    ("603259", 100),
    ("605100", 100),
    ("000001", 100),
    ("001979", 100),
    ("002415", 100),
    ("300750", 200),
    ("301236", 200),
    ("688981", 200),
    ("430047", 300),
    ("830799", 300),
    ("871981", 300),
    ("920002", 300),
    ("bj430047", 300),
]


def _rust_flags(code: str, o: int, h: int, lo: int, cl: int, pc: int) -> tuple:
    """Rust 侧判据元组 (lu, ld, one_word, exchange, bomb, touched)。"""
    bar = rust.Bar(code, D1, o, h, lo, cl, pc, 10, 0.1)
    r = rust.compute_derived([bar])[0]
    return (
        r.is_limit_up,
        r.is_limit_down,
        r.is_one_word,
        r.is_exchange,
        r.is_bomb,
        r.touched_limit,
    )


@pytest.mark.parametrize("code,pct", BOARDS)
def test_board_pct_milli(code, pct):
    """Rust 的分支表与 Python 唯一实现逐板块一致（含 bj 前缀与 92 号段）。"""
    assert board_pct_milli(code) == pct
    # 间接验证：该板块涨停价判涨停，主板 10% 价不判（除主板本身）
    lim = limit_up_price_cents(1000, code)
    assert _rust_flags(code, lim, lim, lim, lim, 1000)[0] is True
    if pct != 100:
        assert _rust_flags(code, 1100, 1100, 1100, 1100, 1000)[0] is False


@pytest.mark.parametrize("code,pct", BOARDS)
def test_limit_price_flags(code, pct):
    """同一 pre_close 下 6 个判据位在两实现间逐值一致，且语义符合口径。"""
    pre = 1001  # 带零头，能区分四舍五入与截断
    lu = limit_up_price_cents(pre, code)
    ld = limit_down_price_cents(pre, code)
    cases = [
        # (open, high, low, close, 期望 (lu, ld, ow, ex, bomb, tl))
        (lu, lu, lu, lu, (True, False, True, False, False, True)),  # 一字板
        (lu - 5, lu, lu - 1, lu, (True, False, False, True, False, True)),  # 换手板
        (lu, lu + 1, lu, lu + 1, (False, False, True, False, True, True)),  # 多一分：炸板
        (ld, ld, ld, ld, (False, True, False, False, False, False)),  # 跌停
        (ld - 1, ld - 1, ld - 1, ld - 1, (False, False, False, False, False, False)),
    ]
    for o, h, lo, cl, expected in cases:
        got = _rust_flags(code, o, h, lo, cl, pre)
        assert got == expected, f"{code} pre={pre} close={cl}：Rust {got} vs 期望 {expected}"


@pytest.mark.parametrize(
    "code,pre,rounded",
    [
        ("600825", 1005, 1106),  # 1005*1100 = 1105500，+500 → 1106（截断会得 1105）
        ("300750", 1003, 1204),  # 1003*1200 = 1203600，+500 → 1204
        ("430047", 1002, 1303),  # 1002*1300 = 1302600，+500 → 1303
        ("920002", 1002, 1303),  # 920 号段同 30%（2026-10-07 补的分支）
    ],
)
def test_limit_price_rounds_half_up_not_truncates(code, pre, rounded):
    """涨停价 = 四舍五入（SQL 整数式 `(x*pct + 50)/100`），不是直接截断。"""
    pct = board_pct_milli(code)
    assert limit_up_price_cents(pre, code) == rounded
    assert (pre * (1000 + pct)) // 1000 == rounded - 1, "这组数据不再能区分舍入与截断"
    # Rust 侧同值：该价判涨停，少一分不判
    assert _rust_flags(code, rounded, rounded, rounded, rounded, pre)[0] is True
    assert _rust_flags(code, rounded - 1, rounded - 1, rounded - 1, rounded - 1, pre)[0] is False


def test_limit_down_price_exact_equality():
    """跌停同为精确等于：002931 口径（close 低于跌停价 → 不算跌停）。"""
    code, pre = "002931", 7164
    ld = limit_down_price_cents(pre, code)
    assert _rust_flags(code, ld, ld, ld, ld, pre)[1] is True
    assert _rust_flags(code, ld - 1, ld, ld - 1, ld - 1, pre)[1] is False
    assert _rust_flags(code, ld + 1, ld + 1, ld + 1, ld + 1, pre)[1] is False
