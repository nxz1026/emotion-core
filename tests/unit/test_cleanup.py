"""data/cleanup.py 历史数据清理测试。"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from emotion_core.data import cleanup


class TestCleanupDryRun:
    def _mock_conn(self, counts: dict):
        """构造 mock transaction cursor。"""
        mock_cursor = MagicMock()
        # 模拟各表 count 查询返回
        results = []
        for table, (col, days) in cleanup.RETENTION.items():
            if table in cleanup.PROTECTED:
                continue
            if days is None and table not in cleanup.KEEP_LATEST:
                results.append([counts.get(table, 0)])
            elif days is not None:
                results.append([counts.get(table, 0)])
            elif table in cleanup.KEEP_LATEST:
                # 两次查询：total 和 keep_cnt
                total = counts.get(table, 10)
                keep = min(total, cleanup.KEEP_LATEST[table])
                results.append([total])
                results.append([keep])
        mock_cursor.fetchone.side_effect = results
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return mock_conn

    def test_contains_limit_pool(self):
        assert "limit_pool_em" in cleanup.RETENTION
        assert cleanup.RETENTION["limit_pool_em"] == ("date", 30)

    def test_contains_alert(self):
        assert "alert" in cleanup.RETENTION
        assert cleanup.RETENTION["alert"] == ("created_at", 90)

    def test_protected_tables(self):
        assert "daily_bar" in cleanup.PROTECTED
        assert "signal" in cleanup.PROTECTED
        assert "data_revision" in cleanup.PROTECTED

    def test_keep_latest(self):
        assert cleanup.KEEP_LATEST["llm_call_log"] == 1000
        assert cleanup.KEEP_LATEST["eval_result"] == 50


class TestCleanup:
    def test_returns_dict(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 5
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        with patch("emotion_core.data.cleanup.transaction", return_value=MagicMock(
            __enter__=MagicMock(return_value=mock_conn),
            __exit__=MagicMock(return_value=False),
        )):
            result = cleanup.cleanup()
            assert isinstance(result, dict)
