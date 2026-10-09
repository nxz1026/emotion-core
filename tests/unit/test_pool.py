"""orchestration/pool.py 东财三池同步 CLI 测试。"""
from __future__ import annotations

import socket
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from emotion_core.orchestration import pool


class TestTradingDays:
    def test_returns_last_n(self):
        mock_dates = [date(2024, 6, 10), date(2024, 6, 11), date(2024, 6, 12)]
        with patch("emotion_core.utils.dates.trading_days", return_value=mock_dates):
            result = pool._trading_days(date(2024, 6, 15), 2)
            assert result == [date(2024, 6, 11), date(2024, 6, 12)]

    def test_n_larger_than_available(self):
        mock_dates = [date(2024, 6, 10)]
        with patch("emotion_core.utils.dates.trading_days", return_value=mock_dates):
            result = pool._trading_days(date(2024, 6, 15), 5)
            assert result == [date(2024, 6, 10)]


class TestMain:
    def test_dry_run_success(self):
        mock_pools = {"limit_pool": (MagicMock(return_value=MagicMock()), "mock")}
        with patch("emotion_core.orchestration.pool._trading_days", return_value=[date(2024, 6, 15)]), \
             patch("emotion_core.services.ingest._POOLS", mock_pools), \
             patch("emotion_core.utils.fetch.retry_fetch", side_effect=RuntimeError("network error")):
            result = pool.main(["--date", "2024-06-15", "--dry-run"])
            # dry-run catches exceptions, prints them, returns 0
            assert result == 0

    def test_sync_range_success(self):
        with patch("emotion_core.orchestration.pool._trading_days", return_value=[date(2024, 6, 15)]), \
             patch("emotion_core.services.ingest.sync_range") as mock_sync:
            mock_sync.return_value = []
            result = pool.main(["--date", "2024-06-15"])
            assert result == 0
            mock_sync.assert_called_once()

    def test_sync_range_runtime_error(self):
        with patch("emotion_core.orchestration.pool._trading_days", return_value=[date(2024, 6, 15)]), \
             patch("emotion_core.services.ingest.sync_range") as mock_sync:
            mock_sync.side_effect = RuntimeError("all pools failed")
            result = pool.main(["--date", "2024-06-15"])
            assert result == 1

    def test_sync_range_returns_failed(self):
        with patch("emotion_core.orchestration.pool._trading_days", return_value=[date(2024, 6, 15)]), \
             patch("emotion_core.services.ingest.sync_range") as mock_sync:
            mock_sync.return_value = [date(2024, 6, 14)]
            result = pool.main(["--date", "2024-06-15"])
            assert result == 1

    def test_date_format_error(self):
        result = pool.main(["--date", "bad-date"])
        assert result == 2

    def test_no_trading_days(self):
        with patch("emotion_core.orchestration.pool._trading_days", return_value=[]):
            result = pool.main(["--latest"])
            assert result == 1


class TestSocketTimeoutGuard:
    """2026-10-09：EM 接口挂死（akshare 不传 timeout → 进程无限等）→ pool.service failed。
    pool.py 入口 setdefaulttimeout(15) 给任何外网 socket 一个挂死上限。
    测试钉死：import 即生效。
    """

    def test_module_import_sets_socket_default(self) -> None:
        # pool 模块已经在 conftest 加载 emotion_core 时被 import 过。
        # 现在的 socket 默认值应当是 pool 设的 15s（不是 None）。
        assert socket.getdefaulttimeout() == 15, (
            f"expected 15s after pool module import, got {socket.getdefaulttimeout()!r}"
        )

    def test_socket_timeout_within_normal_calls(self) -> None:
        """socket timeout 不能让普通 DB / 业务调用立刻失败——15s 已够慢查询余量。"""
        current = socket.getdefaulttimeout()
        assert current is not None, "pool module 没设 socket default — 回归！"
        assert current >= 5, f"timeout 太严，已可能被正常慢请求打死: {current}s"
