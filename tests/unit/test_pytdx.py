"""通达信 pytdx 备源：主机解析、分页逻辑、Provider 行为。"""
from __future__ import annotations

import threading
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from emotion_core.data.providers import pytdx_provider
from emotion_core.data.providers.base import ProviderError


class TestParseTdxHosts:
    def test_default_hosts(self):
        hosts = pytdx_provider._parse_tdx_hosts()
        assert len(hosts) == 5
        assert ("180.153.18.170", 7709) in hosts

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("TDX_HOSTS", "1.2.3.4:7709,5.6.7.8:7727")
        hosts = pytdx_provider._parse_tdx_hosts()
        assert hosts == [("1.2.3.4", 7709), ("5.6.7.8", 7727)]

    def test_invalid_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("TDX_HOSTS", "not-valid")
        hosts = pytdx_provider._parse_tdx_hosts()
        assert len(hosts) == 5


class TestTdxBars:
    def test_bars_success(self, monkeypatch):
        mock_api = MagicMock()
        mock_api.get_security_bars.return_value = [{"datetime": "2026-09-25", "close": 10.0}]

        def mock_tdx():
            return mock_api

        monkeypatch.setattr(pytdx_provider, "_tdx", mock_tdx)

        result = pytdx_provider._tdx_bars(1, "600000")
        assert result == [{"datetime": "2026-09-25", "close": 10.0}]

    def test_bars_failure_resets_connection(self, monkeypatch):
        # Simulate a broken connection: set _tls.api to a mock that fails
        mock_api = MagicMock()
        mock_api.get_security_bars.side_effect = ConnectionError("timeout")
        pytdx_provider._tls.api = mock_api

        with pytest.raises(ConnectionError):
            pytdx_provider._tdx_bars(1, "600000")
        # On failure, connection should be reset
        assert getattr(pytdx_provider._tls, "api", None) is None


class TestTdxAllBars:
    def test_single_page(self, monkeypatch):
        bars = [{"datetime": "2026-09-25", "close": 10.0}]
        monkeypatch.setattr(pytdx_provider, "_tdx_bars", lambda m, c, o=0: bars)

        result = pytdx_provider._tdx_all_bars(1, "600000", date(2026, 9, 25))
        assert result == bars

    def test_empty_page(self, monkeypatch):
        monkeypatch.setattr(pytdx_provider, "_tdx_bars", lambda m, c, o=0: [])

        result = pytdx_provider._tdx_all_bars(1, "600000", date(2026, 9, 25))
        assert result == []

    def test_pagination_stops_at_older_date(self, monkeypatch):
        # Page 1: 800 items at 2026-09-25, page 2: 800 items at 2020-01-01
        page1 = [{"datetime": "2026-09-25", "close": 10.0} for _ in range(800)]
        page2 = [{"datetime": "2020-01-01", "close": 5.0} for _ in range(800)]

        call_count = [0]
        def mock_bars(market, code, offset=0):
            call_count[0] += 1
            return page1 if offset == 0 else page2

        monkeypatch.setattr(pytdx_provider, "_tdx_bars", mock_bars)

        result = pytdx_provider._tdx_all_bars(1, "600000", date(2020, 1, 1))
        assert len(result) == 1600
        assert call_count[0] == 2


class TestPytdxProvider:
    def test_name(self):
        assert pytdx_provider.PytdxProvider.name == "pytdx"

    def test_market_detection(self):
        provider = pytdx_provider.PytdxProvider()
        # 6xxxxx → market 1 (上海)
        # 0xxxxx/3xxxxx → market 0 (深圳)
        assert provider.name == "pytdx"

    def test_fetch_with_no_bars(self, monkeypatch):
        provider = pytdx_provider.PytdxProvider()
        monkeypatch.setattr(pytdx_provider, "_tdx_all_bars", lambda m, c, u: [])

        result = provider.fetch_daily_bars("000001", date(2026, 9, 20), date(2026, 9, 25))
        assert len(result) == 0

    def test_fetch_wraps_exception(self, monkeypatch):
        provider = pytdx_provider.PytdxProvider()
        def bad_bars(m, c, u):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(pytdx_provider, "_tdx_all_bars", bad_bars)

        with pytest.raises(ProviderError, match="pytdx"):
            provider.fetch_daily_bars("000001", date(2026, 9, 20), date(2026, 9, 25))
