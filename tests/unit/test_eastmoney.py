"""东财数据源 Provider：行情守卫逻辑、收盘判定、回退路径。"""
from __future__ import annotations

from datetime import date

import pytest

from emotion_core.data.providers import eastmoney
from emotion_core.data.providers.base import DataError


class TestRejectZeroPrices:
    def test_empty_rows(self):
        eastmoney._reject_zero_prices([])

    def test_all_priced(self):
        rows = [{"f2": 10.0}, {"f2": 20.0}, {"f2": 30.0}]
        eastmoney._reject_zero_prices(rows)

    def test_all_zero_raises(self):
        rows = [{"f2": 0}, {"f2": 0}, {"f2": 0}]
        with pytest.raises(DataError, match="价格异常"):
            eastmoney._reject_zero_prices(rows)

    def test_mostly_zero_raises(self):
        rows = [{"f2": 0}, {"f2": 0}, {"f2": 10.0}]
        with pytest.raises(DataError, match="价格异常"):
            eastmoney._reject_zero_prices(rows)

    def test_exactly_half_not_raises(self):
        # 3/5 有价 → 3*2=6 >= 5，不触发
        rows = [{"f2": 0}, {"f2": 0}, {"f2": 10.0}, {"f2": 20.0}, {"f2": 30.0}]
        eastmoney._reject_zero_prices(rows)


class TestSinaSessionClosed:
    def test_none_none_returns_none(self):
        assert eastmoney._sina_session_closed(None, None) is None

    def test_zero_volume_returns_false(self):
        assert eastmoney._sina_session_closed(0, None) is False

    def test_before_close_returns_false(self):
        # 14:59 < 15:00
        assert eastmoney._sina_session_closed(1000, 14 * 60 + 59) is False

    def test_at_close_returns_true(self):
        # 15:00 >= 15:00
        assert eastmoney._sina_session_closed(1000, 15 * 60) is True

    def test_after_close_returns_true(self):
        assert eastmoney._sina_session_closed(1000, 16 * 60) is True

    def test_zero_volume_with_time_returns_false(self):
        # 成交量 0 优先于时间判断
        assert eastmoney._sina_session_closed(0, 15 * 60) is False


class TestSinaQuotePayload:
    def test_normal_payload(self):
        text = 'var hq_str_sh000001="指数,100,101,102,..."'
        result = eastmoney._sina_quote_payload(text)
        assert result == ["指数", "100", "101", "102", "..."]

    def test_empty_payload(self):
        text = 'var hq_str_sh000001=""'
        result = eastmoney._sina_quote_payload(text)
        assert result == [""]

    def test_no_match(self):
        assert eastmoney._sina_quote_payload("no match here") is None


class TestSinaIndexVolume:
    def test_normal(self):
        # 模拟新浪指数回报，成交量在下标 8（第 9 个字段）
        parts = ["指数"] * 8 + ["12345", "extra"]
        text = '="' + ",".join(parts) + '"'
        result = eastmoney._sina_index_volume(text)
        assert result == 12345.0

    def test_too_short(self):
        text = '="a,b,c"'
        assert eastmoney._sina_index_volume(text) is None

    def test_zero_volume(self):
        parts = ["指数"] * 8 + ["0"]
        text = '="' + ",".join(parts) + '"'
        assert eastmoney._sina_index_volume(text) == 0.0


class TestSinaQuoteMinutes:
    def test_normal(self):
        # 时间在下标 31
        parts = ["field"] * 31 + ["14:30:25"]
        text = '="' + ",".join(parts) + '"'
        result = eastmoney._sina_quote_minutes(text)
        assert result == 14 * 60 + 30

    def test_too_short(self):
        text = '="a,b,c"'
        assert eastmoney._sina_quote_minutes(text) is None


class TestDowngradeOpenSession:
    def test_session_closed_downgrades(self, monkeypatch):
        # 模拟新浪返回：成交量 0 → 未收完
        monkeypatch.setattr(eastmoney, "_sina_index_volume", lambda t: 0)
        monkeypatch.setattr(eastmoney, "_sina_quote_minutes", lambda t: None)
        monkeypatch.setattr(eastmoney, "_sina_last_bar_date", lambda: date(2026, 9, 24))

        class MockResp:
            content = b'=""'
            def raise_for_status(self): pass

        monkeypatch.setattr(eastmoney.requests, "get", lambda *a, **kw: MockResp())
        result = eastmoney._downgrade_open_session(date(2026, 9, 25))
        assert result == date(2026, 9, 24)

    def test_session_open_keeps_date(self, monkeypatch):
        # 模拟新浪返回：成交量 > 0，时间 >= 15:00 → 已收完
        monkeypatch.setattr(eastmoney, "_sina_index_volume", lambda t: 99999)
        monkeypatch.setattr(eastmoney, "_sina_quote_minutes", lambda t: 15 * 60)

        class MockResp:
            content = b'=""'
            def raise_for_status(self): pass

        monkeypatch.setattr(eastmoney.requests, "get", lambda *a, **kw: MockResp())
        result = eastmoney._downgrade_open_session(date(2026, 9, 25))
        assert result == date(2026, 9, 25)


class TestEastmoneyProvider:
    def test_name(self):
        assert eastmoney.EastmoneyProvider.name == "eastmoney"

    def test_fetch_with_non_matching_date(self, monkeypatch):
        provider = eastmoney.EastmoneyProvider()
        monkeypatch.setattr(eastmoney, "em_data_date", lambda: date(2026, 9, 24))
        result = provider.fetch_daily_bars("000001", date(2026, 9, 25), date(2026, 9, 25))
        assert len(result) == 0
