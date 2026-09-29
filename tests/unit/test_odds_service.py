"""买点口径统计服务（services.odds_service）：run 统计，不连库。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from emotion_core.services import odds_service


class TestRun:
    def test_empty_returns_zero(self, monkeypatch):
        monkeypatch.setattr(odds_service, "query_df",
                            lambda sql, params=(), conn=None: pd.DataFrame())
        result = odds_service.run(date(2026, 9, 25))
        assert result == {"trade_date": date(2026, 9, 25), "n": 0}

    def test_calculates_avg_next_open_pct(self, monkeypatch):
        df = pd.DataFrame({
            "code": ["000001", "600000"],
            "confirm_date": [date(2026, 9, 25)] * 2,
            "signal_close": [10.0, 20.0],
            "next_open": [10.5, 19.0],
        })
        monkeypatch.setattr(odds_service, "query_df",
                            lambda sql, params=(), conn=None: df)
        result = odds_service.run(date(2026, 9, 25))
        assert result["n"] == 2
        # (10.5/10 - 1) = 5%, (19/20 - 1) = -5%, mean = 0%
        assert result["avg_next_open_pct"] == 0.0

    def test_single_stock(self, monkeypatch):
        df = pd.DataFrame({
            "code": ["000001"],
            "confirm_date": [date(2026, 9, 25)],
            "signal_close": [10.0],
            "next_open": [11.0],
        })
        monkeypatch.setattr(odds_service, "query_df",
                            lambda sql, params=(), conn=None: df)
        result = odds_service.run(date(2026, 9, 25))
        assert result["n"] == 1
        assert result["avg_next_open_pct"] == 10.0

    def test_sql_uses_correct_date(self, monkeypatch):
        captured: dict = {}

        def fake_query(sql, params=(), conn=None):
            captured["sql"] = sql
            captured["params"] = tuple(params)
            return pd.DataFrame()

        monkeypatch.setattr(odds_service, "query_df", fake_query)
        odds_service.run(date(2026, 9, 25))
        assert "signal s" in captured["sql"]
        assert "daily_bar" in captured["sql"]
        assert captured["params"] == (date(2026, 9, 25),)
