"""orchestration/watchdog.py 链路看门狗 CLI 测试。"""
from __future__ import annotations

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
            mock_pending.return_value = [
                {"id": 1, "level": "WARN", "source": "health", "detail": "d1"},
                {"id": 2, "level": "WARN", "source": "health", "detail": "d2"},
            ]
            with pytest.raises(SystemExit) as exc_info:
                watchdog.main()
            assert exc_info.value.code == 1

    def test_unacked_gap_still_exits_nonzero_even_if_nothing_new(self):
        """★2026-10-07 回归：断档已入队但未 ack 时，下一轮 push 因去重返回 0。

        旧判据只看「新入队条数」，于是每天复检都退 0——故障持续期间 systemd 一路绿灯。
        修好后判据改为「仍有未确认的 health 断档」。
        """
        with patch("sys.argv", ["watchdog"]), \
             patch("emotion_core.algorithms.health.push") as mock_push, \
             patch("emotion_core.algorithms.alerts.pending") as mock_pending:
            mock_push.return_value = 0                       # 去重命中，本轮没新发现
            mock_pending.return_value = [
                {"id": 7, "level": "WARN", "source": "health",
                 "detail": "报告断档：最近成功报告 2026-09-30 距今 3 个交易日"},
            ]
            with pytest.raises(SystemExit) as exc_info:
                watchdog.main()
            assert exc_info.value.code == 1

    def test_other_sources_do_not_fail_the_run(self):
        """非 health 来源的未确认告警（sync/review…）不是断档，不该让 unit 变红。"""
        with patch("sys.argv", ["watchdog"]), \
             patch("emotion_core.algorithms.health.push") as mock_push, \
             patch("emotion_core.algorithms.alerts.pending") as mock_pending:
            mock_push.return_value = 0
            mock_pending.return_value = [
                {"id": 3, "level": "ERROR", "source": "sync", "detail": "boom"},
                {"id": 4, "level": "WARN", "source": "review", "detail": "东财池缺失"},
            ]
            watchdog.main()                              # 不抛 SystemExit 即为绿

    def test_pending_window_matches_dedup_window(self):
        """判未确认断档的取数窗口必须与 record_once 的判重视窗同为 200。

        两处窗口不一致时，watchdog 会把「record_once 早已见过因而不入队」的断档
        判成「没有断档」——正是本条修复要堵的口子。
        """
        with patch("sys.argv", ["watchdog"]), \
             patch("emotion_core.algorithms.health.push") as mock_push, \
             patch("emotion_core.algorithms.alerts.pending") as mock_pending:
            mock_push.return_value = 0
            mock_pending.return_value = []
            watchdog.main()
            assert mock_pending.call_args.args == (200,)

    def test_list_pending(self):
        with patch("sys.argv", ["watchdog", "--list"]), \
             patch("emotion_core.algorithms.alerts.pending") as mock_pending:
            mock_pending.return_value = [{"id": 1, "level": "ERROR",
                                         "source": "sync", "detail": "gap"}]
            watchdog.main()

    def test_ack(self):
        with patch("sys.argv", ["watchdog", "--ack", "1,2,3"]), \
             patch("emotion_core.algorithms.alerts.ack") as mock_ack:
            mock_ack.return_value = 3
            watchdog.main()
            mock_ack.assert_called_once_with([1, 2, 3])
