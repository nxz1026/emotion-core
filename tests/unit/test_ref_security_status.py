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

class TestNormalizeStatus:
    """简称 → 风险警示档位。"""

    @pytest.mark.parametrize("name,expected", [
        ("ST东时", "ST"),
        ("*ST美谷", "*ST"),
        ("石药景峰", "NORMAL"),
        ("", "NORMAL"),
        (" st 东时 ", "ST"),
    ])
    def test_normalize(self, name, expected):
        assert ref_security_status._normalize_status(name) == expected


class TestParseBulkRows:
    """search_stocks 批量行的两种列形。"""

    def test_implement_shape(self):
        cols = ["Wind代码", "证券简称", "实施ST后简称", "实施ST前简称",
                "实施ST日期", "实施ST原因"]
        rows = [["603377.SH", "ST东时", "ST东时", "ST东时", "2025-04-30", "持续经营不确定"]]
        got = ref_security_status._parse_bulk_rows(cols, rows, "实施")
        assert got == [{
            "code": "603377", "date": date(2025, 4, 30), "kind": "实施",
            "status": "ST", "reason": "持续经营不确定",
        }]

    def test_revert_shape(self):
        cols = ["股票编号", "股票种类", "撤销ST后简称", "撤销ST前简称", "证券名称",
                "撤销日期", "2025年撤销前简称", "2025年撤销后简称"]
        rows = [["000908.SZ", "A股", "ST景峰", "*ST景峰", "石药景峰", "2025-06-16",
                 "*ST景峰", "ST景峰"]]
        got = ref_security_status._parse_bulk_rows(cols, rows, "撤销")
        assert got == [{
            "code": "000908", "date": date(2025, 6, 16), "kind": "撤销",
            "status": "ST", "reason": "撤销ST：*ST景峰→ST景峰",
        }]

    def test_revert_minimal_shape_status_unknown(self):
        """精简列形（无「撤销ST后简称」）：证券名称是当前简称，不可用来推断档位。"""
        cols = ["股票编号", "证券名称", "撤销ST日期"]
        rows = [["000820.SZ", "神雾节能", "2026-07-30"]]
        got = ref_security_status._parse_bulk_rows(cols, rows, "撤销")
        assert got[0]["code"] == "000820"
        assert got[0]["date"] == date(2026, 7, 30)
        assert got[0]["status"] is None

    def test_dynamic_date_column_name(self):
        """窗口边界非自然周期时，Wind 把日期列名写成「…到…撤销日期」（实测）。"""
        cols = ["股票编号", "证券名称", "2025年1月1日到2025年6月30日撤销日期", "证券简称"]
        rows = [["000908.SZ", "石药景峰", "2025-06-16", "石药景峰"]]
        got = ref_security_status._parse_bulk_rows(cols, rows, "撤销")
        assert got[0]["date"] == date(2025, 6, 16)

    def test_bad_rows_dropped(self):
        cols = ["Wind代码", "实施ST后简称", "实施ST日期"]
        rows = [["BAD", "ST某", "2025-01-01"], ["600000.SH", "ST某", ""]]
        assert ref_security_status._parse_bulk_rows(cols, rows, "实施") == []


class TestBuildIntervals:
    """事件序列 → ST 区间（左闭右开）。"""

    def _ev(self, day, kind, status, code="600000"):
        return {"code": code, "date": day, "kind": kind, "status": status, "reason": "r"}

    def test_empty(self):
        assert ref_security_status._build_intervals([]) == []

    def test_implement_then_revert(self):
        got = ref_security_status._build_intervals([
            self._ev(date(2025, 4, 30), "实施", "ST"),
            self._ev(date(2026, 7, 30), "撤销", "NORMAL"),
        ])
        assert len(got) == 1
        assert got[0]["status"] == "ST"
        assert got[0]["effective_from"] == date(2025, 4, 30)
        assert got[0]["effective_to"] == date(2026, 7, 30)

    def test_star_st_downgrade_keeps_interval(self):
        got = ref_security_status._build_intervals([
            self._ev(date(2025, 1, 2), "实施", "*ST"),
            self._ev(date(2025, 6, 16), "撤销", "ST"),
        ])
        assert [(i["status"], i["effective_from"], i["effective_to"]) for i in got] == [
            ("*ST", date(2025, 1, 2), date(2025, 6, 16)),
            ("ST", date(2025, 6, 16), None),
        ]

    def test_unknown_status_keeps_previous(self):
        """档位不可知时延续上一档位（保守：宁可多算 ST）。"""
        got = ref_security_status._build_intervals([
            self._ev(date(2025, 1, 2), "实施", "ST"),
            {"code": "600000", "date": date(2025, 6, 16), "kind": "撤销",
             "status": None, "reason": "r"},
            {"code": "600000", "date": date(2026, 7, 30), "kind": "撤销",
             "status": "NORMAL", "reason": "r"},
        ])
        assert [(i["status"], i["effective_to"]) for i in got] == [
            ("ST", date(2025, 6, 16)),
            ("ST", date(2026, 7, 30)),
        ]

    def test_same_day_events_drop_zero_length(self):
        got = ref_security_status._build_intervals([
            self._ev(date(2025, 1, 2), "实施", "ST"),
            self._ev(date(2025, 1, 2), "撤销", "NORMAL"),
        ])
        assert got == []

    def test_open_interval_when_still_st(self):
        got = ref_security_status._build_intervals([self._ev(date(2026, 4, 30), "实施", "ST")])
        assert got[0]["effective_to"] is None
