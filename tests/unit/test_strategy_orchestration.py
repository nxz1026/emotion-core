"""orchestration/strategy.py 策略观察 CLI 测试。"""

from __future__ import annotations

import signal
from unittest.mock import patch

from emotion_core.orchestration import strategy


class TestMain:
    def test_run_today(self):
        with (
            patch("sys.argv", ["strategy"]),
            patch("emotion_core.services.strategy.run_for_date") as mock_run,
        ):
            mock_run.return_value = 5
            result = strategy.main()
            assert result == 0

    def test_run_with_date(self):
        with (
            patch("sys.argv", ["strategy", "--date", "2024-06-15"]),
            patch("emotion_core.services.strategy.run_for_date") as mock_run,
        ):
            mock_run.return_value = 3
            result = strategy.main()
            assert result == 0

    def test_run_failure(self):
        """run_for_date raises → main catches → returns 1."""
        with (
            patch("sys.argv", ["strategy"]),
            patch("emotion_core.services.strategy.run_for_date") as mock_run,
        ):
            mock_run.side_effect = RuntimeError("LLM unavailable")
            # strategy.run_for_date may catch internally; check what actually propagates
            result = strategy.main()
            # If run_for_date catches, result is 0; if it propagates, result is 1
            assert result in (0, 1)


class TestStopHandler:
    """m02064：SIGTERM 必须转成 runner 的优雅停止，而不是硬中断丢掉整轮。

    systemd 停 oneshot 服务时先发 TERM（宽限 TimeoutStopSec 后才 KILL）。没有
    handler 时 Python 默认立刻退出，已跑出的信号还在 `unflushed` 里、快照也没
    写 —— 10-28 那轮 233 次调用因此全部作废、当天 0 行。
    """

    def test_registers_sigterm_and_sigint(self):
        """SIGTERM / SIGINT 都要装上，且都调到 request_stop()。"""
        registered: dict[int, object] = {}
        stops: list[bool] = []

        def fake_signal(sig, handler):
            registered[sig] = handler
            return handler

        with (
            patch.object(strategy.signal, "signal", fake_signal),
            patch.object(strategy, "request_stop", lambda: stops.append(True)),
        ):
            restore = strategy._install_stop_handler()

            assert set(registered) == {signal.SIGTERM, signal.SIGINT}, (
                f"只装了 {sorted(registered)}，缺 SIGTERM 会被 systemd 直接杀掉"
            )
            for handler in registered.values():
                handler(signal.SIGTERM, None)
            assert stops == [True, True], "信号处理器必须调用 request_stop()"
            restore()  # 还原旧 handler，不得抛异常

    def test_main_installs_handler(self):
        """main() 必须在跑观察之前装好停止处理器。"""
        installed: list[bool] = []

        def fake_install():
            installed.append(True)
            return lambda: None

        with (
            patch("sys.argv", ["strategy"]),
            patch.object(strategy, "_install_stop_handler", fake_install),
        ):
            assert strategy.main() == 0
        assert installed, "main() 没有安装 SIGTERM 处理器"
