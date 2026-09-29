"""indicators.py 判据计算测试。"""
from __future__ import annotations

from datetime import date

from emotion_core.algorithms import indicators
from emotion_core.domain.bar import Bar


def make_bar(code: str, d: date, pre_close: int, close: int, high: int, low: int) -> Bar:
    return Bar(
        code=code,
        date=d,
        open_cents=pre_close,
        high_cents=high,
        low_cents=low,
        close_cents=close,
        pre_close_cents=pre_close,
        volume=1000,
        turnover_rate=1.0,
    )


class TestComputeDerived:
    def test_single_limit_up(self):
        # pre_close=10000 (100.00), close=11000 (110.00) = limit up +10%
        bars = [make_bar("600519", date(2024, 6, 15), 10000, 11000, 11000, 10500)]
        result = indicators.compute_derived(bars)
        assert len(result) == 1
        assert result[0].is_limit_up is True
        assert result[0].cont_days == 1
        assert result[0].is_exchange is True  # low < limit_up

    def test_one_word(self):
        # low == limit_up_price
        bars = [make_bar("600519", date(2024, 6, 15), 10000, 11000, 11000, 11000)]
        result = indicators.compute_derived(bars)
        assert result[0].is_one_word is True
        assert result[0].is_exchange is False

    def test_bomb(self):
        # high >= limit_up but close < limit_up
        bars = [make_bar("600519", date(2024, 6, 15), 10000, 10500, 11000, 10000)]
        result = indicators.compute_derived(bars)
        assert result[0].is_bomb is True
        assert result[0].is_limit_up is False
        assert result[0].touched_limit is True

    def test_not_limit_up(self):
        bars = [make_bar("600519", date(2024, 6, 15), 10000, 10500, 10600, 9900)]
        result = indicators.compute_derived(bars)
        assert result[0].is_limit_up is False
        assert result[0].cont_days == 0

    def test_consecutive_streak(self):
        bars = [
            make_bar("600519", date(2024, 6, 13), 10000, 11000, 11000, 10500),
            make_bar("600519", date(2024, 6, 14), 11000, 12100, 12100, 11500),
            make_bar("600519", date(2024, 6, 15), 12100, 13310, 13310, 12600),
        ]
        result = indicators.compute_derived(bars)
        assert result[0].cont_days == 1
        assert result[1].cont_days == 2
        assert result[2].cont_days == 3

    def test_streak_reset(self):
        bars = [
            make_bar("600519", date(2024, 6, 13), 10000, 11000, 11000, 10500),
            make_bar("600519", date(2024, 6, 14), 11000, 11500, 11600, 10900),
            make_bar("600519", date(2024, 6, 15), 11500, 12650, 12650, 12000),
        ]
        result = indicators.compute_derived(bars)
        assert result[0].cont_days == 1
        assert result[1].cont_days == 0
        assert result[2].cont_days == 1

    def test_amplitude(self):
        bars = [make_bar("600519", date(2024, 6, 15), 10000, 10500, 11000, 9000)]
        result = indicators.compute_derived(bars)
        # amplitude = (11000 - 9000) / 10000 * 100 = 20.0
        assert result[0].amplitude == 20.0

    def test_multiple_codes(self):
        bars = [
            make_bar("600519", date(2024, 6, 15), 10000, 11000, 11000, 10500),
            make_bar("000001", date(2024, 6, 15), 5000, 5500, 5500, 5200),
        ]
        result = indicators.compute_derived(bars)
        assert len(result) == 2
        codes = {r.code for r in result}
        assert codes == {"600519", "000001"}

    def test_sorted_by_date(self):
        bars = [
            make_bar("600519", date(2024, 6, 15), 12100, 13310, 13310, 12600),
            make_bar("600519", date(2024, 6, 13), 10000, 11000, 11000, 10500),
            make_bar("600519", date(2024, 6, 14), 11000, 12100, 12100, 11500),
        ]
        result = indicators.compute_derived(bars)
        dates = [r.date for r in result]
        assert dates == [date(2024, 6, 13), date(2024, 6, 14), date(2024, 6, 15)]
        assert result[0].cont_days == 1
        assert result[1].cont_days == 2
        assert result[2].cont_days == 3
