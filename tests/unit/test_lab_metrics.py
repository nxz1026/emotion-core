"""Lab 纯计算层单测：口径B 成交/卖出判据、统计量。

这里钉死的是「容易被写成事后视角」的三个点（A/B/C 三轮各踩过一次）：
1. 买限价挂在 T 日收盘之上 —— 成交看**最高价**，不是最低价；
2. 卖出是 T+2 开盘，不是 T+1；
3. 跌停封死 = 开盘与收盘都在跌停，只看开盘会把「砸下去又撬板」误判成卖不掉。
"""

from __future__ import annotations

import inspect

import pytest

from emotion_core.lab import metrics


class TestCaliberB:
    def test_unfilled_contributes_zero(self) -> None:
        # T 收 10.00 → T+1 涨停价 11.00；T+1 最高 10.50 够不到
        r = metrics.caliber_b_ret(
            code="600000",
            base_close=10.00,
            t1_high=10.50,
            t1_close=10.40,
            t1_volume=1000,
            t2_open=10.30,
        )
        assert r["filled"] is False
        assert r["ret_pct"] == 0.0
        assert metrics.per_signal([r]) == [0.0]
        assert metrics.per_signal([r], count_unfilled_as_zero=False) == []

    def test_fill_needs_high_not_low_pin(self) -> None:
        """反例守卫：成交判据只吃最高价，接口里根本没有最低价。

        若哪天有人改回「最低价 ≤ 涨停价」（卖出腿的写法），这条会红：
        参数表里出现 t1_low 就说明判据被写反了（B 轮踩过一次）。
        """
        params = set(inspect.signature(metrics.caliber_b_ret).parameters)
        assert "t1_high" in params
        assert "t1_low" not in params
        # T+1 最高 10.90 未够 11.00 的涨停价 → 不成交
        r = metrics.caliber_b_ret(
            code="600000",
            base_close=10.00,
            t1_high=10.90,
            t1_close=10.20,
            t1_volume=1000,
            t2_open=10.00,
        )
        assert r["filled"] is False

    def test_filled_at_limit_then_sell_t2_open(self) -> None:
        # T 收 10.00 → 买 11.00；T+1 封住；T+2 开 11.50
        r = metrics.caliber_b_ret(
            code="600000",
            base_close=10.00,
            t1_high=11.00,
            t1_close=11.00,
            t1_volume=1000,
            t2_open=11.50,
        )
        assert r["filled"] is True and r["rolled"] is False
        assert r["entry_px"] == pytest.approx(11.00)
        assert r["exit_px"] == pytest.approx(11.50)
        # (11.50/11.00 - 1 - 0.0015) * 100
        assert r["ret_pct"] == pytest.approx(4.395, abs=1e-3)

    def test_sell_is_t2_open_not_t1(self) -> None:
        """卖掉的是 T+2 开盘；T+1 收盘只是持有中的价。"""
        r = metrics.caliber_b_ret(
            code="600000",
            base_close=10.00,
            t1_high=11.00,
            t1_close=11.00,
            t1_volume=1000,
            t2_open=9.50,
        )
        assert r["exit_px"] == pytest.approx(9.50)

    def test_limit_down_locked_rolls_to_t3(self) -> None:
        # T+1 收 11.00 → T+2 跌停 9.90；T+2 开收都在跌停 → 顺延 T+3 开 9.20
        r = metrics.caliber_b_ret(
            code="600000",
            base_close=10.00,
            t1_high=11.00,
            t1_close=11.00,
            t1_volume=1000,
            t2_open=9.90,
            t2_close=9.90,
            t3_open=9.20,
        )
        assert r["rolled"] is True
        assert r["exit_px"] == pytest.approx(9.20)

    def test_open_limit_down_but_close_recovers_still_sells(self) -> None:
        """开盘砸跌停、收盘撬回来 → 卖得掉（只看开盘会误判）。"""
        r = metrics.caliber_b_ret(
            code="600000",
            base_close=10.00,
            t1_high=11.00,
            t1_close=11.00,
            t1_volume=1000,
            t2_open=9.90,
            t2_close=10.60,
            t3_open=10.00,
        )
        assert r["rolled"] is False
        assert r["exit_px"] == pytest.approx(9.90)

    def test_censored_when_t2_missing(self) -> None:
        r = metrics.caliber_b_ret(
            code="600000",
            base_close=10.00,
            t1_high=11.00,
            t1_close=11.00,
            t1_volume=1000,
            t2_open=None,
        )
        assert r["filled"] is True
        assert r["ret_pct"] is None, "右截断不猜，不能当 0 算"

    def test_chinext_20pct_board(self) -> None:
        # 创业板 20%：T 收 10.00 → 买 12.00
        r = metrics.caliber_b_ret(
            code="300001",
            base_close=10.00,
            t1_high=12.00,
            t1_close=12.00,
            t1_volume=1000,
            t2_open=12.00,
        )
        assert r["entry_px"] == pytest.approx(12.00)

    def test_missing_base_close_is_none(self) -> None:
        r = metrics.caliber_b_ret(
            code="600000",
            base_close=None,
            t1_high=11.00,
            t1_close=11.00,
            t1_volume=1000,
            t2_open=11.00,
        )
        assert r["filled"] is None and r["ret_pct"] is None

    def test_zero_volume_bar_cannot_fill(self) -> None:
        r = metrics.caliber_b_ret(
            code="600000",
            base_close=10.00,
            t1_high=11.00,
            t1_close=11.00,
            t1_volume=0,
            t2_open=11.00,
        )
        assert r["filled"] is False


class TestStats:
    def test_empty(self) -> None:
        s = metrics.stats([])
        assert s["n"] == 0 and s["mean"] is None and s["reliable"] is False

    def test_small_sample_hides_median(self) -> None:
        s = metrics.stats([1.0] * 10)
        assert s["n"] == 10 and s["median"] is None and s["reliable"] is False

    def test_median_and_lcb(self) -> None:
        rets = [float(i) for i in range(40)]
        s = metrics.stats(rets)
        assert s["median"] == pytest.approx(19.5)
        assert s["win_rate"] == pytest.approx(97.5)
        assert s["lcb"] < s["mean"]

    def test_none_values_are_dropped_not_zeroed(self) -> None:
        s = metrics.stats([1.0, None, 3.0])
        assert s["n"] == 2
        assert s["mean"] == pytest.approx(2.0)

    def test_worst_slice_ignores_small_samples(self) -> None:
        slices = {
            "a": {"n": 5, "mean": -50.0},
            "b": {"n": 80, "mean": -1.5},
            "c": {"n": 90, "mean": 0.4},
        }
        assert metrics.worst_slice(slices) == pytest.approx(-1.5)


class TestWilson:
    def test_bounds(self) -> None:
        assert metrics.wilson_lower(0, 0) is None
        lo = metrics.wilson_lower(50, 100)
        assert lo is not None and 0.40 < lo < 0.50

    def test_small_sample_is_penalised(self) -> None:
        small = metrics.wilson_lower(8, 10)
        big = metrics.wilson_lower(800, 1000)
        assert small is not None and big is not None and small < big
