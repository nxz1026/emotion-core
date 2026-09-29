"""ref_security_status ST 回填服务测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from emotion_core.services import ref_security_status


class TestParseStEvents:
    def test_empty(self):
        assert ref_security_status._parse_st_events({}) == []

    def test_st_event(self):
        data = {
            "data": {
                "data": [{
                    "columns": [{"name": "事件类型"}, {"name": "事件日期"}],
                    "rows": [["ST", "2024-06-15"]],
                }]
            }
        }
        result = ref_security_status._parse_st_events(data)
        assert len(result) == 1
        assert result[0]["事件类型"] == "ST"

    def test_risk_warning(self):
        data = {
            "data": {
                "data": [{
                    "columns": [{"name": "事件类型"}, {"name": "事件日期"}],
                    "rows": [["风险警示", "2024-06-15"]],
                }]
            }
        }
        result = ref_security_status._parse_st_events(data)
        assert len(result) == 1

    def test_revocation(self):
        data = {
            "data": {
                "data": [{
                    "columns": [{"name": "事件类型"}, {"name": "事件日期"}],
                    "rows": [["撤销ST", "2024-06-15"]],
                }]
            }
        }
        result = ref_security_status._parse_st_events(data)
        assert len(result) == 1

    def test_non_st_filtered(self):
        data = {
            "data": {
                "data": [{
                    "columns": [{"name": "事件类型"}, {"name": "事件日期"}],
                    "rows": [["大宗交易", "2024-06-15"]],
                }]
            }
        }
        result = ref_security_status._parse_st_events(data)
        assert result == []


class TestToWindCode:
    def test_sh(self):
        assert ref_security_status._to_wind_code("600519") == "600519.SH"

    def test_sz(self):
        assert ref_security_status._to_wind_code("000001") == "000001.SZ"

    def test_bj(self):
        assert ref_security_status._to_wind_code("830000") == "830000.BJ"

    def test_invalid(self):
        assert ref_security_status._to_wind_code("abc") is None


class TestBackfillSecurityStatus:
    def test_no_client_returns_zero(self, monkeypatch):
        monkeypatch.setattr(ref_security_status, "_is_st_client", lambda: None)
        result = ref_security_status.backfill_security_status()
        assert result == (0, 0)

    def test_with_codes(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.availability.return_value = (True, "ok")
        mock_call = MagicMock()
        mock_call.data = {"data": {"data": [{
            "columns": [{"name": "事件类型"}, {"name": "事件日期"}],
            "rows": [["ST", "2024-06-15"]],
        }]}}
        mock_client.call.return_value = mock_call

        monkeypatch.setattr(ref_security_status, "_is_st_client", lambda: mock_client)
        monkeypatch.setattr(ref_security_status, "record_call", lambda c: None)

        mock_conn = MagicMock()
        monkeypatch.setattr(ref_security_status, "db_connect", lambda: mock_conn)

        inserted, skipped = ref_security_status.backfill_security_status(
            codes=["600519"], sleep_sec=0
        )
        assert inserted == 1
