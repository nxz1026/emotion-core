"""ref_dividend 分红除权回填服务测试。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from emotion_core.services import ref_dividend


class TestParseDividendEvents:
    def test_empty_data(self):
        assert ref_dividend._parse_dividend_events({}) == []

    def test_no_tables(self):
        data = {"data": {"data": []}}
        assert ref_dividend._parse_dividend_events(data) == []

    def test_dividend_event(self):
        data = {
            "data": {
                "data": [{
                    "columns": [{"name": "事件类型"}, {"name": "事件日期"}, {"name": "事件描述"}],
                    "rows": [["分红派息", "2024-06-15", "每股派0.5"]],
                }]
            }
        }
        events = ref_dividend._parse_dividend_events(data)
        assert len(events) == 1
        assert events[0]["事件类型"] == "分红派息"

    def test_non_dividend_event_filtered(self):
        data = {
            "data": {
                "data": [{
                    "columns": [{"name": "事件类型"}, {"name": "事件日期"}],
                    "rows": [["大宗交易", "2024-06-15"]],
                }]
            }
        }
        events = ref_dividend._parse_dividend_events(data)
        assert events == []


class TestToWindCode:
    def test_sh(self):
        assert ref_dividend._to_wind_code("600519") == "600519.SH"

    def test_sz(self):
        assert ref_dividend._to_wind_code("000001") == "000001.SZ"

    def test_bj(self):
        assert ref_dividend._to_wind_code("830000") == "830000.BJ"

    def test_invalid(self):
        assert ref_dividend._to_wind_code("abc") is None


class TestExtractDigit:
    def test_dividend(self):
        assert ref_dividend._extract_digit("每股派0.5", "每股派") == 0.5

    def test_bonus(self):
        assert ref_dividend._extract_digit("10送股3", "送股") == 3.0

    def test_no_match(self):
        assert ref_dividend._extract_digit("no match", "每股派") is None


class TestBackfillDividend:
    def test_no_client_returns_zero(self, monkeypatch):
        monkeypatch.setattr(ref_dividend, "_wind_client_or_none", lambda: None)
        result = ref_dividend.backfill_dividend()
        assert result == (0, 0)

    def test_with_codes(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.availability.return_value = (True, "ok")
        mock_call = MagicMock()
        mock_call.data = {"data": {"data": [{
            "columns": [{"name": "事件类型"}, {"name": "事件日期"}, {"name": "事件描述"}],
            "rows": [["分红派息", "2024-06-15", "每股派0.5"]],
        }]}}
        mock_client.call.return_value = mock_call

        monkeypatch.setattr(ref_dividend, "_wind_client_or_none", lambda: mock_client)
        monkeypatch.setattr(ref_dividend, "record_call", lambda c: None)

        mock_conn = MagicMock()
        monkeypatch.setattr(ref_dividend, "db_connect", lambda: mock_conn)

        inserted, skipped = ref_dividend.backfill_dividend(codes=["600519"], sleep_sec=0)
        assert inserted == 1
