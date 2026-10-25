"""tests/unit/test_alert_webhook.py

P0-B: alerts.py:97 动态 import 接缝 — _push 模块路径搜索顺序 + 失败回退链。
告警失效 4 天的根因是 notify 模块从 services 移入 algorithms，
但旧路径优先尝试触发 ImportError → except ImportError 静默吞掉 → webhook 永远不发。
修复后：services 优先、algorithms 兜底，任一缺失仍降级为 False。

另覆盖 `_days_active`（2026-10-19 补）：原 `test_items_trigger_push` 把「第 1 天/第 2 天」
写死成 2026-10-17 当日快照，靠的是 `_days_active` 取墙上时钟，隔天必红（时间炸弹）。
现改为相对「今天」构造 created_at + 独立钉住 _days_active 的日历日差语义。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from emotion_core.algorithms import alerts

# ── 1. _push: 优先找到 services.notify ────────────────────────────────


class TestPushPriority:
    """_push: services.notify 优先，algorithms.notify 兜底。"""

    def test_services_notify_used_first(self):
        """services.notify 存在时直接调用其 push。"""
        mock_notify = MagicMock()
        mock_notify.push.return_value = True

        def fake_import_module(path):
            if path == "emotion_core.services.notify":
                return mock_notify
            raise ImportError(f"No module named {path!r}")

        with patch(
            "emotion_core.algorithms.alerts.importlib.import_module", side_effect=fake_import_module
        ):
            result = alerts._push("test message")

        assert result is True
        mock_notify.push.assert_called_once_with("test message", None)

    def test_falls_back_to_algorithms_notify(self):
        """services.notify 不存在时回退到 algorithms.notify。"""
        mock_notify = MagicMock()
        mock_notify.push.return_value = True

        def fake_import_module(path):
            if path == "emotion_core.algorithms.notify":
                return mock_notify
            raise ImportError(f"No module named {path!r}")

        with patch(
            "emotion_core.algorithms.alerts.importlib.import_module", side_effect=fake_import_module
        ):
            result = alerts._push("test message")

        assert result is True
        mock_notify.push.assert_called_once_with("test message", None)

    def test_no_notify_module_returns_false_and_logs_warning(self):
        """两条路径都不存在 → 返回 False + 记录 warning。"""
        mock_log = MagicMock()
        import emotion_core.algorithms.alerts as alerts_mod

        with patch.object(alerts_mod.importlib, "import_module", side_effect=ImportError):
            with patch.object(alerts_mod, "log", new=mock_log):
                result = alerts_mod._push("test message")

        assert result is False
        assert mock_log.warning.called
        assert "notify" in str(mock_log.warning.call_args)


# ── 2. _push: 失败静默降级（不抛异常）──────────────────────────────────


class TestPushFailureModes:
    """_push: 各种失败模式均不抛异常，返回 False。"""

    def test_push_returns_false(self):
        """notify.push 返回 False → _push 传播 False。"""
        mock_notify = MagicMock()
        mock_notify.push.return_value = False

        with patch(
            "emotion_core.algorithms.alerts.importlib.import_module", return_value=mock_notify
        ):
            result = alerts._push("test message")

        assert result is False

    def test_push_raises_exception_caught(self):
        """notify.push 抛异常 → _push 捕获 + 返回 False（不向上传播）。"""
        mock_notify = MagicMock()
        mock_notify.push.side_effect = RuntimeError("webhook unreachable")

        with patch(
            "emotion_core.algorithms.alerts.importlib.import_module", return_value=mock_notify
        ):
            result = alerts._push("test message")

        assert result is False

    def test_trade_date_passed_through(self):
        """trade_date 参数透传给 notify.push。"""
        mock_notify = MagicMock()
        mock_notify.push.return_value = True

        with patch(
            "emotion_core.algorithms.alerts.importlib.import_module", return_value=mock_notify
        ):
            alerts._push("test message", trade_date="2026-09-29")

        mock_notify.push.assert_called_once_with("test message", "2026-09-29")


# ── 3. push_pending_webhook: 完整流程 ──────────────────────────────────


class TestPushPendingWebhook:
    """push_pending_webhook: 未确认 WARN/ERROR 触发 webhook。"""

    def test_no_items_no_push(self):
        """无未确认告警 → 不调 push。"""
        with patch("emotion_core.algorithms.alerts.pending", return_value=[]):
            with patch("emotion_core.algorithms.alerts._push") as mock_push:
                alerts.push_pending_webhook()
                assert not mock_push.called

    def test_items_trigger_push(self):
        """有未确认 WARN/ERROR → 调 push 发送汇总。

        created_at 相对「今天」构造，天数期望仍写死为 1/2 天：
        这样断言仍能钉住「每个 item 按自己的 created_at 算天数」，
        又不会随真实日历漂移。
        """
        today_utc = datetime.now(UTC)
        recent = (today_utc - timedelta(days=0)).isoformat()
        older = (today_utc - timedelta(days=1)).isoformat()
        fake_items = [
            {"level": "ERROR", "source": "daily", "detail": "test error 1", "created_at": recent},
            {"level": "WARN", "source": "pool", "detail": "test warn 1", "created_at": older},
        ]
        with patch("emotion_core.algorithms.alerts.pending", return_value=fake_items):
            with patch("emotion_core.algorithms.alerts._push") as mock_push:
                mock_push.return_value = True
                alerts.push_pending_webhook()
                assert mock_push.called
                args, kwargs = mock_push.call_args
                body = args[0]
                assert "未确认告警" in body
                assert "ERROR" in body
                assert f"创建于 {recent[:10]}" in body  # _fmt_date 字段
                assert "第 1 天" in body  # 今日创建 → 第 1 天
                assert "第 2 天" in body  # 昨日创建 → 第 2 天


# ── 4. _days_active: 日历日差口径 ──────────────────────────────────────


class TestDaysActive:
    """_days_active: (today - created_date).days + 1，下限 1，异常归 1。"""

    def test_today_is_day_one(self):
        assert alerts._days_active(datetime.now(UTC).isoformat()) == 1

    def test_yesterday_is_day_two(self):
        ts = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        assert alerts._days_active(ts) == 2

    def test_ten_days_ago_is_day_eleven(self):
        ts = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        assert alerts._days_active(ts) == 11

    def test_naive_timestamp_treated_as_utc(self):
        ts = (datetime.now(UTC) - timedelta(days=3)).replace(tzinfo=None).isoformat()
        assert alerts._days_active(ts) == 4

    def test_future_timestamp_clamps_to_one(self):
        ts = (datetime.now(UTC) + timedelta(days=2)).isoformat()
        assert alerts._days_active(ts) == 1

    def test_invalid_returns_one(self):
        assert alerts._days_active("not-a-date") == 1

    def test_none_returns_one(self):
        assert alerts._days_active(None) == 1
