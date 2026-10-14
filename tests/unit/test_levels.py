"""tests for `algorithms/levels.py`（R3 价位口径，docs/17 §6.2；决策 D3）。

重点守两件事：
1. **结构位算式与 CPT `track_points.py` 对齐**（回看窗口、方向过滤、三个 buffer）；
2. **`price_ratio` 无效时绝不用 adj 域的数当 raw**（把 1258 当 8886 是最贵的错）。
"""
from __future__ import annotations

import sys

import pytest

from emotion_core.algorithms import levels as lv


def _ov(
    zs: list[tuple[float, float]] | None = None,
    downs: list[float] | None = None,
    ups: list[float] | None = None,
) -> dict:
    """构造 CPT 形状的 overlays（`zhongshus` 用 (low, high)，`bis` 用方向 ±1）。"""
    return {
        "zhongshus": [{"low": lo, "high": hi} for lo, hi in (zs or [])],
        "bis": (
            [{"direction": -1, "low": x, "high": x} for x in (downs or [])]
            + [{"direction": 1, "low": x, "high": x} for x in (ups or [])]
        ),
    }


class TestStructureLevels:
    def test_basic_three_formulas(self):
        # lows = 中枢 last2 [90? ] —— 用 (100,120),(90,130) : min(90, 80) = 80
        ov = _ov(zs=[(100.0, 120.0), (90.0, 130.0)], downs=[80.0], ups=[140.0])
        lvl = lv.structure_levels(ov, 2.0, close=23.0)
        assert lvl.basis == "cpt_structure"
        # 买 = 80 ÷ 2 × 1.02 = 40.8
        assert lvl.buy_reference == pytest.approx(40.8)
        # 卖 = max(130, 140) ÷ 2 × 0.97 = 67.9
        assert lvl.sell_reference == pytest.approx(67.9)
        assert lvl.target_price == pytest.approx(67.9)
        # 止损 = 40.8 × 0.95 = 38.76
        assert lvl.stop_price == pytest.approx(38.76)
        assert "结构位" in lvl.detail

    def test_zhongshu_lookback_is_two(self):
        """只有最后 2 个中枢参与；更早的 0.5 若被算进来，买点会变成 0.51。"""
        ov = _ov(zs=[(0.5, 0.5), (0.5, 0.5), (100.0, 300.0), (200.0, 400.0)])
        lvl = lv.structure_levels(ov, 1.0, close=23.0)
        assert lvl.buy_reference == pytest.approx(102.0)     # min(100,200)×1.02
        assert lvl.sell_reference == pytest.approx(388.0)    # max(300,400)×0.97

    def test_bi_lookback_is_three(self):
        ov = _ov(downs=[1.0, 2.0, 3.0, 4.0])
        lvl = lv.structure_levels(ov, 1.0, close=23.0)
        assert lvl.buy_reference == pytest.approx(2.0 * 1.02)   # last 3 = [2,3,4]

    def test_bi_direction_filters_which_side(self):
        """`direction == +1` 的 high 只能进卖侧，不能污染买点。"""
        ov = _ov(downs=[10.0, 20.0], ups=[999.0])
        lvl = lv.structure_levels(ov, 1.0, close=23.0)
        assert lvl.buy_reference == pytest.approx(10.2)         # min(10,20)×1.02
        assert lvl.sell_reference == pytest.approx(999.0 * 0.97)

    def test_buffers_are_exact(self):
        ov = _ov(zs=[(100.0, 100.0)])
        lvl = lv.structure_levels(ov, 1.0, close=23.0)
        assert lvl.buy_reference == pytest.approx(102.0)        # ×1.02
        assert lvl.sell_reference == pytest.approx(97.0)        # ×0.97
        assert lvl.stop_price == pytest.approx(96.9)            # 102 ×0.95


class TestPercentFallback:
    def test_ratio_none_falls_back(self):
        lvl = lv.structure_levels(_ov(zs=[(100.0, 120.0)]), None, close=100.0)
        assert lvl.basis == "pct_fallback"
        assert lvl.buy_reference == pytest.approx(97.0)         # −3%
        assert lvl.stop_price == pytest.approx(92.0)            # −8%
        assert lvl.target_price == pytest.approx(105.0)         # +5%
        assert "price_ratio" in lvl.detail

    @pytest.mark.parametrize("bad", [None, 0, -1.0, float("nan"), "abc"])
    def test_invalid_ratio_falls_back(self, bad):
        lvl = lv.structure_levels(_ov(zs=[(100.0, 120.0)]), bad, close=100.0)
        assert lvl.basis == "pct_fallback"
        assert lvl.buy_reference == pytest.approx(97.0)

    def test_no_overlays_falls_back(self):
        lvl = lv.structure_levels(None, 2.0, close=100.0)
        assert lvl.basis == "pct_fallback"
        assert lvl.buy_reference == pytest.approx(97.0)

    def test_empty_overlays_falls_back_with_reason(self):
        lvl = lv.structure_levels(_ov(), 2.0, close=100.0)
        assert lvl.basis == "pct_fallback"
        assert "没有可用的中枢/笔" in lvl.detail

    def test_no_close_gives_no_prices(self):
        lvl = lv.structure_levels(_ov(zs=[(100.0, 120.0)]), None, close=None)
        assert lvl.basis == "pct_fallback"
        assert (lvl.buy_reference, lvl.stop_price, lvl.target_price) == (None, None, None)
        assert "不给价位" in lvl.detail


class TestAdjDomainGuard:
    def test_never_uses_adj_prices_as_raw_when_ratio_missing(self):
        """回归护栏：ratio=None 时，adj 域的大数（8000）绝不能冒出来当 raw 价。"""
        ov = _ov(zs=[(8000.0, 9000.0)], downs=[8000.0], ups=[9000.0])
        lvl = lv.structure_levels(ov, None, close=100.0)
        assert lvl.basis == "pct_fallback"
        assert lvl.buy_reference == pytest.approx(97.0)
        assert lvl.buy_reference < 200          # 绝不在 adj 量级

    def test_ratio_converts_adj_to_raw(self):
        """同一份 adj overlays，ratio 不同 → raw 结果随之缩放（证明确实除了）。"""
        ov = _ov(zs=[(8000.0, 9000.0)])
        a = lv.structure_levels(ov, 1.0, close=100.0)
        b = lv.structure_levels(ov, 80.0, close=100.0)
        assert a.buy_reference == pytest.approx(8000.0 * 1.02)
        assert b.buy_reference == pytest.approx(100.0 * 1.02)


class TestDirtyValues:
    def test_none_and_nan_entries_are_ignored(self):
        ov = {
            "zhongshus": [{"low": None, "high": float("nan")}, {"low": 50.0, "high": 60.0}],
            "bis": [{"direction": -1, "low": None, "high": None}],
        }
        lvl = lv.structure_levels(ov, 1.0, close=100.0)
        assert lvl.buy_reference == pytest.approx(50.0 * 1.02)
        assert lvl.sell_reference == pytest.approx(60.0 * 0.97)

    def test_all_dirty_falls_back(self):
        ov = {"zhongshus": [{"low": None, "high": None}], "bis": []}
        lvl = lv.structure_levels(ov, 1.0, close=100.0)
        assert lvl.basis == "pct_fallback"

    def test_bool_is_not_a_price(self):
        """`True` 会变成 1.0 —— 价格域里的脏数据，必须忽略。"""
        ov = {"zhongshus": [{"low": True, "high": True}], "bis": []}
        lvl = lv.structure_levels(ov, 1.0, close=100.0)
        assert lvl.basis == "pct_fallback"

    def test_non_dict_entries_are_skipped(self):
        ov = {"zhongshus": ["oops", 42], "bis": [None, {"direction": -1, "low": 30.0}]}
        lvl = lv.structure_levels(ov, 1.0, close=100.0)
        assert lvl.buy_reference == pytest.approx(30.6)

    def test_wrong_container_types_fall_back(self):
        lvl = lv.structure_levels({"zhongshus": "x", "bis": 3}, 1.0, close=100.0)
        assert lvl.basis == "pct_fallback"


class TestConstantsMatchCpt:
    def test_buffers(self):
        """与 CPT `track_points.py` 同值；改了必须同步两侧。"""
        assert (lv.BUY_BUFFER, lv.SELL_BUFFER, lv.STOP_LOSS_RATIO) == (1.02, 0.97, 0.95)
        assert (lv.BI_LOOKBACK, lv.ZHONGSHU_LOOKBACK) == (3, 2)


class TestConstantsMatchCptLive:
    """从 CPT 仓源码 import 真实常量，防两侧漂移（R4 S6）。"""
    @pytest.fixture(autouse=True)
    def _add_cpt_path(self):
        import os
        cpt_path = "/home/ubuntu/DSH/Chan_Pattern_Trader"
        if os.path.isdir(cpt_path) and cpt_path not in sys.path:
            sys.path.insert(0, cpt_path)
        yield

    def test_lookback_matches_cpt(self):
        try:
            from cpt.application.track_points import _BI_LOOKBACK, _ZHONGSHU_LOOKBACK
        except ImportError:
            pytest.skip("CPT 仓不可用（非同一机器）")
        assert lv.BI_LOOKBACK == _BI_LOOKBACK
        assert lv.ZHONGSHU_LOOKBACK == _ZHONGSHU_LOOKBACK

    def test_buffers_match_cpt(self):
        try:
            from cpt.application.track_points import BUY_BUFFER, SELL_BUFFER, STOP_LOSS_RATIO
        except ImportError:
            pytest.skip("CPT 仓不可用")
        assert lv.BUY_BUFFER == BUY_BUFFER
        assert lv.SELL_BUFFER == SELL_BUFFER
        assert lv.STOP_LOSS_RATIO == STOP_LOSS_RATIO
