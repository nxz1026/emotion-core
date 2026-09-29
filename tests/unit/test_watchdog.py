"""orchestration/watchdog.py 链路看门狗 CLI 测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from emotion_core.orchestration import watchdog


class TestFmt:
    def test_empty(self):
        result = watchdog._fmt([])
        assert "（无未确认告警）" in result

    def test_with_items(self):
        items = [
            {"id": 1, "level": "ERROR", "source": "sync", "detail": "data gap"},
            {"id": 2, "level": "WARN", "source": "health", "detail": "stale"},
        ]
        result = watchdog._fmt(items)
        assert "[1] ERROR/sync: data gap" in result
        assert "[2] WARN/health: stale" in result


class TestMain:
    def test_all_clear(self):
        with patch("sys.argv", ["watchdog"]), \
             patch("emotion_core.algorithms.health.push") as mock_push, \
             patch("emotion_core.algorithms.alerts.pending") as mock_pending:
            mock_push.return_value = 0
            mock_pending.return_value = []
            watchdog.main()

    def test_with_gaps(self):
        with patch("sys.argv", ["watchdog"]), \
             patch("emotion_core.algorithms.health.push") as mock_push, \
             patch("emotion_core.algorithms.alerts.pending") as mock_pending:
            mock_push.return_value = 2
            mock_pending.return_value = [{"id": 1}, {"id": 2}]
            with pytest.raises(SystemExit) as exc_info:
                watchdog.main()
            assert exc_info.value.code == 1

    def test_list_pending(self):
        with patch("sys.argv", ["watchdog", "--list"]), \
             patch("emotion_core.algorithms.alerts.pending") as mock_pending:
            mock_pending.return_value = [{"id": 1, "level": "ERROR", "source": "sync", "detail": "gap"}]
            watchdog.main()

    def test_ack(self):
        with patch("sys.argv", ["watchdog", "--ack", "1,2,3"]), \
             patch("emotion_core.algorithms.alerts.ack") as mock_ack:
            mock_ack.return_value = 3
            watchdog.main()
            mock_ack.assert_called_once_with([1, 2, 3])
