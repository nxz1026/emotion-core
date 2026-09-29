"""strategy.context 个股上下文构建测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from emotion_core.services.strategy import context


class TestValue:
    def test_normal(self):
        row = {"a": 1, "b": "hello"}
        assert context._value(row, "a") == 1

    def test_missing_key(self):
        row = {"a": 1}
        assert context._value(row, "missing") == "—"

    def test_none_value(self):
        row = {"a": None}
        assert context._value(row, "a") == "—"

    def test_nan_value(self):
        row = {"a": float("nan")}
        assert context._value(row, "a") == "—"

    def test_custom_default(self):
        row = {}
        assert context._value(row, "missing", default="N/A") == "N/A"


class TestDailyText:
    def test_empty_frame(self, monkeypatch):
        mock_query = MagicMock(return_value=pd.DataFrame())
        monkeypatch.setattr(context, "query_df", mock_query)
        result = context._daily_text(date(2024, 6, 15), "600519")
        assert result == []

    def test_with_rows(self, monkeypatch):
        df = pd.DataFrame([{
            "date": date(2024, 6, 15),
            "close": 100.0,
            "change_pct": 5.0,
            "turnover_rate": 3.5,
            "cont_days": 2,
            "is_one_word": True,
        }])
        mock_query = MagicMock(return_value=df)
        monkeypatch.setattr(context, "query_df", mock_query)
        result = context._daily_text(date(2024, 6, 15), "600519")
        assert len(result) == 1
        assert "100.0" in result[0]
        assert "5.0%" in result[0]


class TestBuildStockContext:
    def test_empty_data(self, monkeypatch):
        mock_query = MagicMock(return_value=pd.DataFrame())
        monkeypatch.setattr(context, "query_df", mock_query)
        result = context.build_stock_context(date(2024, 6, 15), "600519")
        assert "600519" in result
        assert "—" in result

    def test_with_ladder(self, monkeypatch):
        def mock_query(sql, params):
            if "ladder_day" in sql:
                return pd.DataFrame([{"cont_days": 3, "is_top": True}])
            return pd.DataFrame()
        monkeypatch.setattr(context, "query_df", mock_query)
        result = context.build_stock_context(date(2024, 6, 15), "600519")
        assert "3" in result
        assert "True" in result

    def test_with_theme(self, monkeypatch):
        def mock_query(sql, params):
            if "theme_tag" in sql:
                return pd.DataFrame([{"primary_theme": "AI"}])
            return pd.DataFrame()
        monkeypatch.setattr(context, "query_df", mock_query)
        result = context.build_stock_context(date(2024, 6, 15), "600519")
        assert "AI" in result

    def test_with_market_stat(self, monkeypatch):
        def mock_query(sql, params):
            if "market_stat" in sql:
                return pd.DataFrame([{
                    "phase": "上升",
                    "buy_window": True,
                    "limit_up_count": 50,
                    "bomb_rate": 0.3,
                    "zt_performance": 0.8,
                    "max_limit_days": 5,
                }])
            return pd.DataFrame()
        monkeypatch.setattr(context, "query_df", mock_query)
        result = context.build_stock_context(date(2024, 6, 15), "600519")
        assert "上升" in result
        assert "50" in result

    def test_max_length(self, monkeypatch):
        """上下文不超过 1500 字。"""
        def mock_query(sql, params):
            if "daily_bar" in sql:
                rows = []
                for i in range(20):
                    rows.append({
                        "date": date(2024, 5, 1 + i),
                        "close": 100.0 + i,
                        "change_pct": 5.0,
                        "turnover_rate": 3.5,
                        "cont_days": 2,
                        "is_one_word": True,
                    })
                return pd.DataFrame(rows)
            return pd.DataFrame()
        monkeypatch.setattr(context, "query_df", mock_query)
        result = context.build_stock_context(date(2024, 6, 15), "600519")
        assert len(result) <= 1500
