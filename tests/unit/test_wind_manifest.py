"""wind_manifest 配额台账测试。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from emotion_core.services.wind_manifest import _hash_params, record_call


class MockWindCall:
    def __init__(self):
        self.server_type = "stock_data"
        self.tool_name = "get_stock_basicinfo"
        self.params = {"question": "查询贵州茅台"}
        self.raw_response = '{"data": {"name": "贵州茅台"}}'
        self.elapsed_ms = 150
        self.ok = True
        self.code = None


class TestHashParams:
    def test_deterministic(self):
        params = {"b": 2, "a": 1}
        assert _hash_params(params) == _hash_params({"a": 1, "b": 2})

    def test_length(self):
        assert len(_hash_params({"any": "params"})) == 16

    def test_different_params(self):
        assert _hash_params({"a": 1}) != _hash_params({"a": 2})


class TestRecordCall:
    def test_inserts_to_both_tables(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("emotion_core.services.wind_manifest.connect", return_value=mock_conn):
            record_call(MockWindCall())

        # Should have executed 2 INSERTs
        assert mock_cur.execute.call_count == 2

    def test_first_insert_is_raw_manifest(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("emotion_core.services.wind_manifest.connect", return_value=mock_conn):
            record_call(MockWindCall())

        first_call = mock_cur.execute.call_args_list[0]
        assert "ops_raw_manifest" in first_call[0][0]

    def test_second_insert_is_quota_ledger(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("emotion_core.services.wind_manifest.connect", return_value=mock_conn):
            record_call(MockWindCall())

        second_call = mock_cur.execute.call_args_list[1]
        assert "ops_quota_ledger" in second_call[0][0]
