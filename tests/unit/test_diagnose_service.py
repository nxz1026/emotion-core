"""个股诊断服务（services.diagnose_service）：diagnose 三段式，不连库。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from emotion_core.services import diagnose_service


class TestDiagnose:
    def test_basic_shape(self, monkeypatch):
        monkeypatch.setattr(diagnose_service, "query_df",
                            lambda sql, params=(), conn=None: pd.DataFrame())
        monkeypatch.setattr(diagnose_service, "prev_trading_day",
                            lambda d: date(2026, 9, 25))
        result = diagnose_service.diagnose("000001")
        assert result["code"] == "000001"
        assert "technical" in result
        assert "fundamental" in result
        assert "market" in result

    def test_with_as_of_date(self, monkeypatch):
        monkeypatch.setattr(diagnose_service, "query_df",
                            lambda sql, params=(), conn=None: pd.DataFrame())
        result = diagnose_service.diagnose("000001", date(2026, 9, 20))
        assert result["as_of"] == date(2026, 9, 20)

    def _make_query(self, technical_df, fundamental_df, market_df):
        """Return a query_df stub that dispatches on SQL content."""
        def fake_query(sql, params=(), conn=None):
            if "derived_bar" in sql:
                return technical_df
            if "stock_basic" in sql:
                return fundamental_df
            if "market_stat" in sql:
                return market_df
            return pd.DataFrame()
        return fake_query

    def test_technical_data_present(self, monkeypatch):
        tech_df = pd.DataFrame({
            "is_limit_up": [True],
            "is_exchange": [False],
            "cont_days": [3],
            "amplitude": [10.5],
        })
        monkeypatch.setattr(diagnose_service, "query_df",
                            self._make_query(tech_df, pd.DataFrame(), pd.DataFrame()))
        result = diagnose_service.diagnose("000001", date(2026, 9, 25))
        assert result["technical"]["is_limit_up"] is True
        assert result["technical"]["cont_days"] == 3
        assert result["technical"]["amplitude"] == 10.5

    def test_fundamental_data_present(self, monkeypatch):
        fund_df = pd.DataFrame({
            "name": ["平安银行"],
            "industry": ["银行"],
            "market_cap": [1000.5],
        })
        monkeypatch.setattr(diagnose_service, "query_df",
                            self._make_query(pd.DataFrame(), fund_df, pd.DataFrame()))
        result = diagnose_service.diagnose("000001", date(2026, 9, 25))
        assert result["fundamental"]["name"] == "平安银行"
        assert result["fundamental"]["industry"] == "银行"

    def test_market_data_present(self, monkeypatch):
        mkt_df = pd.DataFrame({
            "phase": ["UPTREND"],
            "buy_window": ["OPEN"],
        })
        monkeypatch.setattr(diagnose_service, "query_df",
                            self._make_query(pd.DataFrame(), pd.DataFrame(), mkt_df))
        result = diagnose_service.diagnose("000001", date(2026, 9, 25))
        assert result["market"]["phase"] == "UPTREND"
        assert result["market"]["buy_window"] == "OPEN"
