"""akshare 新浪历史日线备源：Provider 行为。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from emotion_core.data.providers import sina
from emotion_core.data.providers.base import ProviderError


class TestSinaProvider:
    def test_name(self):
        assert sina.SinaProvider.name == "sina"

    def test_market_prefix(self):
        provider = sina.SinaProvider()
        # Just verify the provider exists and has the right name
        assert provider.name == "sina"

    def test_fetch_with_mocked_akshare(self, monkeypatch):
        provider = sina.SinaProvider()

        # Mock akshare DataFrame
        mock_df = pd.DataFrame({
            "date": [date(2026, 9, 25)],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
            "volume": [1000.0],
            "amount": [10500.0],
            "turnover_rate": [1.5],
        })

        monkeypatch.setattr(sina.ak, "stock_zh_a_daily", lambda **kw: mock_df)

        result = provider.fetch_daily_bars("000001", date(2026, 9, 25), date(2026, 9, 25))
        assert len(result) > 0
        assert "close" in result.columns

    def test_fetch_chinese_columns(self, monkeypatch):
        provider = sina.SinaProvider()

        # Mock akshare DataFrame with Chinese column names
        mock_df = pd.DataFrame({
            "日期": [date(2026, 9, 25)],
            "开盘": [10.0],
            "最高": [11.0],
            "最低": [9.5],
            "收盘": [10.5],
            "成交量": [1000.0],
            "成交额": [10500.0],
            "换手率": [1.5],
        })

        monkeypatch.setattr(sina.ak, "stock_zh_a_daily", lambda **kw: mock_df)

        result = provider.fetch_daily_bars("600000", date(2026, 9, 25), date(2026, 9, 25))
        assert len(result) > 0
        assert "close" in result.columns

    def test_fetch_wraps_exception(self, monkeypatch):
        provider = sina.SinaProvider()
        monkeypatch.setattr(sina.ak, "stock_zh_a_daily", lambda **kw: (_ for _ in ()).throw(RuntimeError("akshare error")))

        with pytest.raises(ProviderError, match="sina"):
            provider.fetch_daily_bars("000001", date(2026, 9, 25), date(2026, 9, 25))
