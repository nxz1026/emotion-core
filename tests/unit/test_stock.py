"""algorithms/stock.py 个股诊断判定层测试。"""
from __future__ import annotations

from datetime import date
from emotion_core.algorithms.stock import (
    STANCE_BUY,
    STANCE_AVOID,
    STANCE_WATCH,
    STANCE_LOW_ABSORB,
    HIGH_CONT,
    NEAR_HIGH_PCT,
    BOMB_MANY,
    _pct,
    _ok,
    layer_label,
    mean,
    ma,
    trend_metrics,
    attach_volume_ratio,
    is_st_name,
    is_st,
    is_new_issuer,
    risk_tags,
    verdict,
    _one_liner,
)


class TestLayerLabel:
    def test_single_digit(self):
        assert layer_label(1) == "1->2"
        assert layer_label(3) == "3->4"

    def test_five_plus(self):
        assert layer_label(5) == "5+->6+"
        assert layer_label(8) == "5+->6+"

    def test_zero(self):
        assert layer_label(0) == "0->1"

    def test_none_as_zero(self):
        assert layer_label(None) == "0->1"


class TestMean:
    def test_basic(self):
        assert mean([1.0, 2.0, 3.0]) == 2.0

    def test_ignores_none(self):
        assert mean([1.0, None, 3.0]) == 2.0

    def test_all_none(self):
        assert mean([None, None]) is None

    def test_empty(self):
        assert mean([]) is None

    def test_ignores_nan(self):
        assert mean([1.0, float("nan"), 3.0]) == 2.0


class TestOk:
    def test_normal_number(self):
        assert _ok(1.0) is True
        assert _ok(0) is True

    def test_none(self):
        assert _ok(None) is False

    def test_nan(self):
        assert _ok(float("nan")) is False

    def test_string(self):
        assert _ok("abc") is False


class TestMa:
    def test_basic(self):
        assert ma([1.0, 2.0, 3.0, 4.0, 5.0], 5) == 3.0

    def test_window_smaller(self):
        assert ma([1.0, 2.0, 3.0, 4.0, 5.0], 3) == 4.0

    def test_insufficient(self):
        assert ma([1.0, 2.0], 5) is None


class TestTrendMetrics:
    def _make_series(self, n=25):
        series = []
        for i in range(n):
            series.append({
                "close": 10.0 + i * 0.1,
                "high": 10.5 + i * 0.1,
                "low": 9.5 + i * 0.1,
                "volume": 1000.0 + i * 100,
                "pct_chg": 0.01,
                "turnover_rate": 0.05,
                "amplitude": 0.02,
                "is_limit_up": i == n - 1,
                "is_one_word": False,
                "is_exchange": True,
                "is_limit_down": False,
                "cont_days": 3,
            })
        return series

    def test_basic(self):
        result = trend_metrics(self._make_series())
        assert "price" in result
        assert "ma5" in result
        assert "ma10" in result
        assert "ma20" in result
        assert "high20" in result
        assert "vol_ratio" in result
        assert result["cont_days"] == 3
        assert result["is_limit_up"] is True

    def test_empty_series(self):
        result = trend_metrics([])
        assert result["price"] is None

    def test_off_high_pct(self):
        series = self._make_series()
        result = trend_metrics(series)
        # Price is near the 20-day high
        assert result["off_high_pct"] is not None

    def test_above_ma20(self):
        series = self._make_series()
        result = trend_metrics(series)
        assert result["above_ma20"] is not None


class TestAttachVolumeRatio:
    def test_basic(self):
        series = [{"volume": 1000}, {"volume": 2000}, {"volume": 3000}, {"volume": 4000}, {"volume": 5000}]
        result = attach_volume_ratio(series, window=2)
        assert len(result) == 5
        assert all("vol_ratio5" in r for r in result)
        # First entry has no previous data → None
        assert result[0]["vol_ratio5"] is None
        # Second entry has 1 previous value → 2000/1000 = 2.0
        assert result[1]["vol_ratio5"] == 2.0

    def test_preserves_original(self):
        series = [{"volume": 1000, "code": "600000"}]
        result = attach_volume_ratio(series, window=2)
        assert result[0]["code"] == "600000"


class TestIsStName:
    def test_st_prefix(self):
        assert is_st_name("ST华海") is True
        assert is_st_name("*ST华海") is True

    def test_non_st(self):
        assert is_st_name("贵州茅台") is False

    def test_none(self):
        assert is_st_name(None) is False

    def test_case_insensitive(self):
        assert is_st_name("st华海") is True


class TestIsSt:
    def test_flag_true(self):
        assert is_st({"is_st": True, "name": "贵州茅台"}) is True

    def test_name_st(self):
        assert is_st({"is_st": False, "name": "ST华海"}) is True

    def test_non_st(self):
        assert is_st({"is_st": False, "name": "贵州茅台"}) is False


class TestIsNewIssuer:
    def test_recent(self):
        first = date(2024, 6, 1)
        trade = date(2024, 6, 30)
        assert is_new_issuer(first, trade, days=90) is True

    def test_old(self):
        first = date(2024, 1, 1)
        trade = date(2024, 6, 30)
        assert is_new_issuer(first, trade, days=90) is False

    def test_none_dates(self):
        assert is_new_issuer(None, date(2024, 6, 30)) is False
        assert is_new_issuer(date(2024, 1, 1), None) is False


class TestRiskTags:
    def test_st_tag(self):
        tags = risk_tags({"name": "ST华海"}, {}, {}, {}, None, date(2024, 6, 15))
        assert any(t["name"] == "ST" for t in tags)

    def test_new_issuer_tag(self):
        tags = risk_tags({"name": "贵州茅台"}, {}, {}, {}, date(2024, 6, 1), date(2024, 6, 15))
        assert any(t["name"] == "次新" for t in tags)

    def test_one_word_tag(self):
        tags = risk_tags({}, {"is_one_word": True}, {}, {}, None, date(2024, 6, 15))
        assert any(t["name"] == "一字板" for t in tags)

    def test_high_cont_tag(self):
        tags = risk_tags({}, {"cont_days": 5}, {}, {}, None, date(2024, 6, 15))
        assert any("高位" in t.get("name", "") for t in tags)

    def test_no_tags(self):
        tags = risk_tags({"name": "贵州茅台"}, {"cont_days": 2}, {"bomb_n": 0}, {}, None, date(2024, 6, 15))
        assert len(tags) == 0


class TestVerdict:
    def _basic_trend(self):
        return {
            "cont_days": 0,
            "is_limit_up": False,
            "is_one_word": False,
            "is_exchange": False,
            "is_limit_down": False,
            "price": 10.0,
            "ma20": 9.5,
            "above_ma20": True,
            "off_high_pct": -5.0,
            "vol_ratio": 1.5,
        }

    def test_st_avoid(self):
        result = verdict({"name": "ST华海"}, self._basic_trend(), {}, {}, None, None, None, None, date(2024, 6, 15))
        assert result["stance"] == STANCE_AVOID

    def test_limit_down_avoid(self):
        trend = self._basic_trend()
        trend["is_limit_down"] = True
        result = verdict({"name": "贵州茅台"}, trend, {}, {}, None, None, None, None, date(2024, 6, 15))
        assert result["stance"] == STANCE_AVOID

    def test_buy_window_none(self):
        trend = self._basic_trend()
        env = {"buy_window": "NONE", "phase": "冰点"}
        result = verdict({"name": "贵州茅台"}, trend, {}, env, None, None, None, None, date(2024, 6, 15))
        assert result["stance"] == STANCE_AVOID

    def test_force_liquidate(self):
        trend = self._basic_trend()
        env = {"buy_window": "STANDARD", "force_liquidate": True}
        result = verdict({"name": "贵州茅台"}, trend, {}, env, None, None, None, None, date(2024, 6, 15))
        assert result["stance"] == STANCE_AVOID

    def test_non_limit_up_above_ma20(self):
        trend = self._basic_trend()
        env = {"buy_window": "STANDARD", "phase": "发酵"}
        result = verdict({"name": "贵州茅台"}, trend, {}, env, None, None, None, None, date(2024, 6, 15))
        assert result["stance"] == STANCE_WATCH

    def test_exchange_board_buy(self):
        trend = self._basic_trend()
        trend["cont_days"] = 2
        trend["is_exchange"] = True
        trend["is_limit_up"] = True
        env = {"buy_window": "STANDARD", "phase": "发酵"}
        promo = {"rate": 50, "total": 100, "perf_median": 2.0}
        result = verdict({"name": "贵州茅台"}, trend, {}, env, promo, None, None, None, date(2024, 6, 15))
        assert result["stance"] == STANCE_BUY

    def test_one_word_low_absorb(self):
        trend = self._basic_trend()
        trend["cont_days"] = 2
        trend["is_one_word"] = True
        trend["is_limit_up"] = True
        env = {"buy_window": "STANDARD", "phase": "发酵"}
        promo = {"rate": 50, "total": 100, "perf_median": 2.0}
        result = verdict({"name": "贵州茅台"}, trend, {}, env, promo, None, None, None, date(2024, 6, 15))
        assert result["stance"] == STANCE_LOW_ABSORB

    def test_has_disclaimer(self):
        result = verdict({"name": "贵州茅台"}, self._basic_trend(), {}, {}, None, None, None, None, date(2024, 6, 15))
        assert "disclaimer" in result
        assert "非投资建议" in result["disclaimer"]

    def test_has_context(self):
        env = {"buy_window": "STANDARD", "phase": "发酵", "limit_up_count": 50}
        result = verdict({"name": "贵州茅台"}, self._basic_trend(), {}, env, None, None, None, None, date(2024, 6, 15))
        assert "context" in result


class TestOneLiner:
    def test_avoid_window_closed(self):
        assert "情绪窗口关闭" in _one_liner(STANCE_AVOID, 0, {}, {"buy_window": "NONE"})

    def test_avoid_general(self):
        assert "风险优先" in _one_liner(STANCE_AVOID, 0, {}, {})

    def test_buy(self):
        assert "换手板" in _one_liner(STANCE_BUY, 2, {}, {})

    def test_low_absorb(self):
        assert "回踩" in _one_liner(STANCE_LOW_ABSORB, 5, {}, {})

    def test_watch_no_cont(self):
        assert "等首板" in _one_liner(STANCE_WATCH, 0, {}, {})


class TestPct:
    def test_normal(self):
        assert _pct(0.5) == "50.0%"

    def test_none(self):
        assert _pct(None) == "—"

    def test_zero(self):
        assert _pct(0) == "0.0%"
