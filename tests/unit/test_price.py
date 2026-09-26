"""涨停价测试。与 lkl 的 SQL 整数式逐分对账。

公式验证（见 utils/price.py docstring）：
    SQL: (round(pre_close*100)::bigint * pct + 50) / 100
    Python: (pre_close_cents * (1000 + pct_milli) + 500) // 1000
    等价：pre_close=10.00 元=1000 分, 主板 → 1100 分 ✓
"""
from __future__ import annotations

import pytest

from emotion_core.utils.price import (
    board_pct_milli,
    is_limit_up,
    limit_down_price_cents,
    limit_up_price_cents,
)


class TestBoardPctMilli:
    """板块涨跌幅千分比。"""

    def test_main_board_10pct(self):
        assert board_pct_milli("600519") == 100  # 沪主板
        assert board_pct_milli("000001") == 100  # 深主板
        assert board_pct_milli("601398") == 100  # 沪主板

    def test_chinext_star_20pct(self):
        assert board_pct_milli("300750") == 200  # 创业板
        assert board_pct_milli("688981") == 200  # 科创板

    def test_bse_30pct(self):
        assert board_pct_milli("bj92002") == 300  # 北交所
        assert board_pct_milli("830799") == 300  # 北交所


class TestLimitUpPrice:
    """涨停价（分）。"""

    def test_main_board_exact(self):
        # 昨收 10.00 元 = 1000 分，涨停 11.00 元 = 1100 分
        assert limit_up_price_cents(1000, "600519") == 1100

    def test_main_board_rounding(self):
        # 昨收 10.01 元 = 1001 分，涨停 11.011 → 四舍五入 1101 分
        # SQL: (1001 * 110 + 50) / 100 = 110160 / 100 = 1101
        # Python: (1001 * 1100 + 500) // 1000 = 1101600 // 1000 = 1101 ✓
        assert limit_up_price_cents(1001, "600519") == 1101

    def test_main_board_rounding_up(self):
        # 昨收 10.05 元 = 1005 分，涨停 11.055 → 四舍五入 1106 分
        # SQL: (1005 * 110 + 50) / 100 = 110600 / 100 = 1106
        # Python: (1005 * 1100 + 500) // 1000 = 1106000 // 1000 = 1106 ✓
        assert limit_up_price_cents(1005, "600519") == 1106

    def test_chinext_star(self):
        # 昨收 10.00 元 = 1000 分，涨停 12.00 元 = 1200 分
        assert limit_up_price_cents(1000, "300750") == 1200

    def test_bse(self):
        # 昨收 10.00 元 = 1000 分，涨停 13.00 元 = 1300 分
        assert limit_up_price_cents(1000, "bj92002") == 1300


class TestLimitDownPrice:
    """跌停价（分）。"""

    def test_main_board(self):
        assert limit_down_price_cents(1000, "600519") == 900

    def test_chinext_star(self):
        assert limit_down_price_cents(1000, "300750") == 800


class TestIsLimitUp:
    """涨停判定。"""

    def test_exact_limit_up(self):
        assert is_limit_up(1100, 1000, "600519") is True

    def test_below_limit(self):
        assert is_limit_up(1099, 1000, "600519") is False

    def test_above_limit(self):
        assert is_limit_up(1101, 1000, "600519") is True
