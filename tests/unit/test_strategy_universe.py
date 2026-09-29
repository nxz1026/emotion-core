"""strategy.universe 候选池构建测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from emotion_core.services.strategy import universe


class TestCodes:
    def test_empty_frame(self):
        assert universe._codes(pd.DataFrame()) == []

    def test_with_codes(self):
        df = pd.DataFrame({"code": ["600519", "000001"]})
        result = universe._codes(df)
        assert result == ["600519", "000001"]

    def test_padding(self):
        df = pd.DataFrame({"code": [1, 2]})
        result = universe._codes(df)
        assert result == ["000001", "000002"]

    def test_dropna(self):
        df = pd.DataFrame({"code": ["600519", None, "000001"]})
        result = universe._codes(df)
        assert None not in result


class TestBuildUniverse:
    def test_empty(self, monkeypatch):
        monkeypatch.setattr(universe, "query_df", lambda sql, params: pd.DataFrame())
        result = universe.build_universe(date(2024, 6, 15))
        assert result == []

    def test_limit_pool_only(self, monkeypatch):
        def mock_query(sql, params):
            if "limit_pool_em" in sql:
                return pd.DataFrame({"code": ["600519", "000001"]})
            return pd.DataFrame()
        monkeypatch.setattr(universe, "query_df", mock_query)
        result = universe.build_universe(date(2024, 6, 15))
        assert "600519" in result

    def test_hot_rank_only(self, monkeypatch):
        def mock_query(sql, params):
            if "hot_rank" in sql:
                return pd.DataFrame({"code": ["000001"]})
            return pd.DataFrame()
        monkeypatch.setattr(universe, "query_df", mock_query)
        result = universe.build_universe(date(2024, 6, 15))
        assert "000001" in result

    def test_dedup(self, monkeypatch):
        def mock_query(sql, params):
            if "limit_pool_em" in sql:
                return pd.DataFrame({"code": ["600519"]})
            if "hot_rank" in sql:
                return pd.DataFrame({"code": ["600519"]})
            return pd.DataFrame()
        monkeypatch.setattr(universe, "query_df", mock_query)
        result = universe.build_universe(date(2024, 6, 15))
        assert result.count("600519") == 1

    def test_max_universe_cap(self, monkeypatch):
        """候选池不超过 STRATEGY_MAX_UNIVERSE。"""
        import dataclasses
        from emotion_core.utils.config import CONFIG as REAL_CONFIG

        codes = [f"{i:06d}" for i in range(100, 200)]
        df = pd.DataFrame({"code": codes})

        conf = dataclasses.replace(REAL_CONFIG, STRATEGY_MAX_UNIVERSE=10, BOARD_PREFIXES=("0", "3", "6"))
        monkeypatch.setattr(universe, "CONFIG", conf)
        monkeypatch.setattr(universe, "query_df", lambda sql, params: df)
        result = universe.build_universe(date(2024, 6, 15))
        assert len(result) <= 10

    def test_board_filter(self, monkeypatch):
        """只保留主板前缀。"""
        import dataclasses
        from emotion_core.utils.config import CONFIG as REAL_CONFIG

        def mock_query(sql, params):
            if "limit_pool_em" in sql:
                return pd.DataFrame({"code": ["600519", "830001"]})
            return pd.DataFrame()

        conf = dataclasses.replace(REAL_CONFIG, BOARD_PREFIXES=("0", "3", "6"))
        monkeypatch.setattr(universe, "CONFIG", conf)
        monkeypatch.setattr(universe, "query_df", mock_query)
        result = universe.build_universe(date(2024, 6, 15))
        assert "600519" in result
        assert "830001" not in result
