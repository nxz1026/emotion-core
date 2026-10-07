"""通达信 pytdx 备源：主机解析、分页逻辑、Provider 行为。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

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


# 降序帧：TDX 的真实返回顺序（_tdx_all_bars 的 docstring 明写「最新在前」）。
# 收盘价刻意用 30/20/10，任何 shift 方向搞反都会立刻显形。
_DESC = [
    {"datetime": "2026-09-25 15:00", "open": 30.0, "high": 31.0, "low": 29.0,
     "close": 30.0, "vol": 1000.0, "amount": 30000.0},
    {"datetime": "2026-09-24 15:00", "open": 20.0, "high": 21.0, "low": 19.0,
     "close": 20.0, "vol": 2000.0, "amount": 40000.0},
    {"datetime": "2026-09-23 15:00", "open": 10.0, "high": 11.0, "low": 9.0,
     "close": 10.0, "vol": 3000.0, "amount": 30000.0},
]


class TestPreCloseDirection:
    """★2026-10-07 回归：pre_close 必须取**前一**交易日收盘，不能取次日。

    原实现在降序帧上直接 `shift(1)`，每行拿到的是**未来价**。
    pre_close 是涨停判定基准（caliber C2），取未来价会让涨跌幅/涨停整体反向，
    而库里不报错——只能靠这个测试钉住。
    """

    def test_provider_pre_close_is_previous_day_close(self, monkeypatch):
        provider = pytdx_provider.PytdxProvider()
        monkeypatch.setattr(pytdx_provider, "_tdx_all_bars", lambda m, c, u: list(_DESC))

        frame = provider.fetch_daily_bars("000001", date(2026, 9, 20), date(2026, 9, 25))
        got = dict(zip(frame["date"], frame["pre_close"], strict=True))

        # 09-24 的昨收 = 09-23 的 10.0；09-25 的昨收 = 09-24 的 20.0。
        # 「取到次日」这个错法会让 09-24 拿到 20.0，两条断言一起把它排除。
        assert got[date(2026, 9, 24)] == 10.0
        assert got[date(2026, 9, 25)] == 20.0

    def test_provider_seeds_first_in_range_row_from_before_start(self, monkeypatch):
        """区间首行的 pre_close 要由 start 之前那根播种，不能恒为 NaN。"""
        provider = pytdx_provider.PytdxProvider()
        monkeypatch.setattr(pytdx_provider, "_tdx_all_bars", lambda m, c, u: list(_DESC))

        frame = provider.fetch_daily_bars("000001", date(2026, 9, 24), date(2026, 9, 25))
        first = frame[frame["date"] == date(2026, 9, 24)].iloc[0]
        assert pd.notna(first["pre_close"]), "区间首行 pre_close 为空"
        assert first["pre_close"] == 10.0

    def test_provider_output_is_ascending(self, monkeypatch):
        provider = pytdx_provider.PytdxProvider()
        monkeypatch.setattr(pytdx_provider, "_tdx_all_bars", lambda m, c, u: list(_DESC))

        frame = provider.fetch_daily_bars("000001", date(2026, 9, 20), date(2026, 9, 25))
        assert list(frame["date"]) == sorted(frame["date"])


class TestBackfillTdxPreClose:
    """回填脚本与 provider 是两份独立实现（都手写 shift），各自都要有守卫。"""

    _BACKFILL_DESC = [
        {"datetime": "2024-01-05 15:00", "open": 30.0, "high": 31.0, "low": 29.0,
         "close": 30.0, "vol": 1000.0, "amount": 30000.0},
        {"datetime": "2024-01-04 15:00", "open": 20.0, "high": 21.0, "low": 19.0,
         "close": 20.0, "vol": 2000.0, "amount": 40000.0},
        {"datetime": "2024-01-03 15:00", "open": 10.0, "high": 11.0, "low": 9.0,
         "close": 10.0, "vol": 3000.0, "amount": 30000.0},
    ]

    def test_fetch_code_pre_close_is_previous_day_close(self, monkeypatch):
        from emotion_core.data import backfill_tdx

        monkeypatch.setattr(backfill_tdx, "_tdx_all_bars",
                            lambda m, c, u: list(self._BACKFILL_DESC))

        rows = backfill_tdx.fetch_code("000001")
        # 回填行序：(code, date, open, high, low, close, pre_close, volume, amount, turnover)
        got = {r[1]: r[6] for r in rows}
        assert got[date(2024, 1, 4)] == 10.0
        assert got[date(2024, 1, 5)] == 20.0

    def test_fetch_code_respects_range_and_seeds_first_row(self, monkeypatch):
        """区间首行要用区间**外**那根播种，pre_close 不能是 None。"""
        from emotion_core.data import backfill_tdx

        monkeypatch.setattr(backfill_tdx, "_tdx_all_bars",
                            lambda m, c, u: list(self._BACKFILL_DESC))
        # 把区间起点推到 01-04，让 01-03 那根落在区间外，专门验证「区间外播种」
        monkeypatch.setattr(backfill_tdx, "START", date(2024, 1, 4))

        rows = backfill_tdx.fetch_code("000001")
        assert {r[1] for r in rows} == {date(2024, 1, 4), date(2024, 1, 5)}
        got = {r[1]: r[6] for r in rows}
        assert got[date(2024, 1, 4)] == 10.0, "区间首行没拿到区间外的播种值"
