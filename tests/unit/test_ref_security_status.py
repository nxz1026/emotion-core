"""ref_security_status ST 回填服务测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from emotion_core.services import ref_security_status


class TestParseEvents:
    """测试 _parse_events（兼容 ST 变动 + 违规处分两种格式）。"""

    def test_empty(self):
        assert ref_security_status._parse_events({}) == []

    def test_no_data_text(self):
        """Wind 返回 '没找到数据' 文本时应返回空列表。"""
        data = {"data": "没找到数据"}
        assert ref_security_status._parse_events(data) == []

    def test_st_event_format1(self):
        """ST 变动格式：实施ST日期 + 实施ST后简称 + 实施ST原因。"""
        data = {
            "data": {
                "data": [{
                    "columns": [
                        {"name": "Wind代码"},
                        {"name": "证券简称"},
                        {"name": "实施ST后简称"},
                        {"name": "实施ST前简称"},
                        {"name": "实施ST日期"},
                        {"name": "实施ST原因"},
                    ],
                    "rows": [[
                        "603729.SH", "ST龙韵", "ST龙韵", "龙韵股份",
                        "2021-05-06", "被注册会计师出具无法表示意见的审计报告",
                    ]],
                }]
            }
        }
        result = ref_security_status._parse_events(data)
        assert len(result) == 1
        assert result[0]["code"] == "603729"
        assert result[0]["status"] == "ST龙韵"
        assert result[0]["effective_from"] == date(2021, 5, 6)
        assert "无法表示意见" in result[0]["reason"]

    def test_multiple_st_events(self):
        """多条 ST 变动记录。"""
        data = {
            "data": {
                "data": [{
                    "columns": [
                        {"name": "Wind代码"},
                        {"name": "证券简称"},
                        {"name": "实施ST后简称"},
                        {"name": "实施ST前简称"},
                        {"name": "实施ST日期"},
                        {"name": "实施ST原因"},
                    ],
                    "rows": [
                        ["603729.SH", "ST龙韵", "ST龙韵", "龙韵股份", "2021-05-06", "原因A"],
                        ["603729.SH", "ST龙韵", "ST龙韵", "龙韵股份", "2026-04-30", "原因B"],
                    ],
                }]
            }
        }
        result = ref_security_status._parse_events(data)
        assert len(result) == 2
        assert result[0]["effective_from"] == date(2021, 5, 6)
        assert result[1]["effective_from"] == date(2026, 4, 30)

    def test_violation_format_st_related(self):
        """违规处分格式：ST 相关处分应保留。"""
        data = {
            "data": {
                "data": [{
                    "columns": [
                        {"name": "Wind代码"},
                        {"name": "证券简称"},
                        {"name": "违规处分类型"},
                        {"name": "违规公告日期"},
                        {"name": "违规行为"},
                    ],
                    "rows": [[
                        "600519.SH", "贵州茅台", "其他", "2026-03-13", "未公布",
                    ]],
                }]
            }
        }
        result = ref_security_status._parse_events(data)
        # "其他" 不含 ST/风险警示/撤销 → 过滤掉
        assert result == []

    def test_violation_format_risk_warning(self):
        """违规处分格式：风险警示处分应保留。"""
        data = {
            "data": {
                "data": [{
                    "columns": [
                        {"name": "Wind代码"},
                        {"name": "证券简称"},
                        {"name": "违规处分类型"},
                        {"name": "违规公告日期"},
                        {"name": "违规行为"},
                    ],
                    "rows": [[
                        "600000.SH", "浦发银行", "风险警示", "2025-01-01", "财务问题",
                    ]],
                }]
            }
        }
        result = ref_security_status._parse_events(data)
        assert len(result) == 1
        assert result[0]["code"] == "600000"
        assert result[0]["status"] == "风险警示"
        assert result[0]["effective_from"] == date(2025, 1, 1)


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
        # 使用真实 ST 变动格式
        mock_call.data = {
            "data": {
                "data": [{
                    "columns": [
                        {"name": "Wind代码"},
                        {"name": "证券简称"},
                        {"name": "实施ST后简称"},
                        {"name": "实施ST前简称"},
                        {"name": "实施ST日期"},
                        {"name": "实施ST原因"},
                    ],
                    "rows": [[
                        "603729.SH", "ST龙韵", "ST龙韵", "龙韵股份",
                        "2021-05-06", "被注册会计师出具无法表示意见的审计报告",
                    ]],
                }]
            }
        }
        mock_client.call.return_value = mock_call

        monkeypatch.setattr(ref_security_status, "_is_st_client", lambda: mock_client)
        monkeypatch.setattr(ref_security_status, "record_call", lambda c: None)

        mock_conn = MagicMock()
        monkeypatch.setattr(ref_security_status, "db_connect", lambda: mock_conn)

        inserted, skipped = ref_security_status.backfill_security_status(
            codes=["603729"], sleep_sec=0
        )
        assert inserted == 1

    def test_no_data_skipped(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.availability.return_value = (True, "ok")
        mock_call = MagicMock()
        mock_call.data = {"data": "没找到数据"}
        mock_client.call.return_value = mock_call

        monkeypatch.setattr(ref_security_status, "_is_st_client", lambda: mock_client)
        monkeypatch.setattr(ref_security_status, "record_call", lambda c: None)

        inserted, skipped = ref_security_status.backfill_security_status(
            codes=["600519"], sleep_sec=0
        )
        assert skipped == 1