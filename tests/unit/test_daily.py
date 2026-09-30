"""orchestration/daily.py 编排层核心路径测试。

P1-D：orchestration/daily 零测试 — 核心生产路径无回归保护。
桩掉 pipeline.mark_running/mark_done/mark_mark_failed 与全部步骤实现，
验证：交易日守卫、步骤执行顺序、from_step 断点续跑、dry_run 模式、
CoverageBlocked 专用退出码、通用异常退出码、未知步骤拒绝。

打点回归（2026-09-30）：`mark_done` 必须带 `pipeline_state.detail`——
此前恒不带，生产上 signal 步天天 `status=OK` / `detail=''`，而 signal 表
`source='live'` 恒 0 行（`entry.check_signal` 只在全过时落库、其余只写日志），
库里无从区分「当天没信号」与「步骤没跑」。
"""

from __future__ import annotations

import logging
from datetime import date
from unittest.mock import MagicMock, call, patch

import pytest

from emotion_core.domain.signal import Action, Checklist, Signal, SignalSource
from emotion_core.orchestration import daily
from emotion_core.services.coverage import EXIT_COVERAGE_BLOCKED, CoverageBlocked


# ── fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def patch_pipeline(monkeypatch):
    """桩掉 pipeline 打点函数。"""
    monkeypatch.setattr(daily.pipeline, "mark_running", MagicMock())
    monkeypatch.setattr(daily.pipeline, "mark_done", MagicMock())
    monkeypatch.setattr(daily.pipeline, "mark_failed", MagicMock())


@pytest.fixture
def patch_trading_day(monkeypatch):
    """桩掉 trade_calendar.is_trading_day。"""
    monkeypatch.setattr(
        "emotion_core.data.trade_calendar.is_trading_day",
        lambda d: d.weekday() < 5,  # 简化：周一至周五为交易日
    )


@pytest.fixture
def patch_today(monkeypatch):
    """固定 today 为 2026-09-29（周二）。"""
    monkeypatch.setattr(daily, "today_sh", lambda: date(2026, 9, 29))


@pytest.fixture
def patch_all_steps(monkeypatch):
    """桩掉 13 步的实现（只留编排层），使 run_daily 走真实 ``_run_step`` 分派。

    返回值取真实量纲（行数 / 窗口），供 detail 断言；不依赖 DB 与网络。
    """
    monkeypatch.setattr("emotion_core.services.ingest.snapshot_daily", lambda d: 0)
    monkeypatch.setattr("emotion_core.data.providers.eastmoney.em_data_date",
                        lambda: date(2026, 9, 29))
    monkeypatch.setattr("emotion_core.services.ingest.fetch_hot_snapshot", lambda d: 0)
    monkeypatch.setattr("emotion_core.services.coverage.gate", lambda d: "coverage line")
    monkeypatch.setattr("emotion_core.services.derive_service.run", lambda d: 0)
    monkeypatch.setattr("emotion_core.services.market_service.run", lambda d: 0)
    monkeypatch.setattr("emotion_core.algorithms.ladder.persist", lambda d: 42)
    monkeypatch.setattr("emotion_core.services.signal_service.run", lambda d: None)
    monkeypatch.setattr("emotion_core.algorithms.entry.current_window", lambda d: "NONE")
    monkeypatch.setattr("emotion_core.services.promotion_service.run", lambda d: 0)
    monkeypatch.setattr("emotion_core.services.theme_service.run", lambda d: 0)
    monkeypatch.setattr("emotion_core.services.ecosystem_service.run", lambda d: "NEUTRAL")
    monkeypatch.setattr("emotion_core.services.strategy.run_for_date", lambda d: 0)
    monkeypatch.setattr("emotion_core.algorithms.outcome.backfill", lambda: 0)
    monkeypatch.setattr("emotion_core.algorithms.health.push", lambda d: 0)


# ── 1. _trading_day_guard ─────────────────────────────────────────────

class TestTradingDayGuard:
    """交易日守卫：非交易日跳过、未来日期跳过、交易日放行。"""

    def test_trading_day_passes(self, patch_today, patch_trading_day):
        assert daily._trading_day_guard(date(2026, 9, 29)) is True  # 周二

    def test_weekend_skipped(self, patch_today, patch_trading_day):
        assert daily._trading_day_guard(date(2026, 9, 27)) is False  # 周日

    def test_future_date_skipped(self, patch_today, patch_trading_day):
        assert daily._trading_day_guard(date(2026, 10, 15)) is False

    def test_today_passes(self, patch_today, patch_trading_day):
        assert daily._trading_day_guard(date(2026, 9, 29)) is True


# ── 2. run_daily: 步骤顺序与打点 ──────────────────────────────────────

class TestRunDailyStepOrder:
    """run_daily 步骤执行顺序、打点行为。"""

    def test_all_steps_execute_in_order(self, patch_pipeline, patch_today, patch_trading_day):
        """所有 13 步按 STEPS 顺序执行，每步都 mark_running + mark_done。"""
        step_calls = []

        def mock_run_step(step, trade_date):
            step_calls.append(step)

        with patch.object(daily, "_run_step", side_effect=mock_run_step):
            rc = daily.run_daily(date(2026, 9, 29))

        assert rc == 0
        assert step_calls == [s for s, _ in daily.STEPS]
        assert daily.pipeline.mark_running.call_count == 13
        assert daily.pipeline.mark_done.call_count == 13
        daily.pipeline.mark_failed.assert_not_called()

    def test_from_step_resumes(self, patch_pipeline, patch_today, patch_trading_day):
        """from_step 从指定步骤开始，跳过前面的步骤。"""
        step_calls = []

        def mock_run_step(step, trade_date):
            step_calls.append(step)

        with patch.object(daily, "_run_step", side_effect=mock_run_step):
            rc = daily.run_daily(date(2026, 9, 29), from_step="signal")

        assert rc == 0
        assert step_calls == ["signal", "promotion", "theme", "ecosystem", "strategy", "outcome", "health"]

    def test_dry_run_no_execution(self, patch_pipeline, patch_today, patch_trading_day):
        """dry_run 只打印步骤，不执行。"""
        with patch.object(daily, "_run_step") as mock_run:
            rc = daily.run_daily(date(2026, 9, 29), dry_run=True)

        assert rc == 0
        mock_run.assert_not_called()
        daily.pipeline.mark_running.assert_not_called()

    def test_step_detail_is_passed_to_mark_done(self, patch_pipeline, patch_today, patch_trading_day):
        """回归：每步产出摘要必须作为 detail 落 pipeline_state（此前恒不传）。"""
        with patch.object(daily, "_run_step", side_effect=lambda step, d: f"{step} 摘要"):
            rc = daily.run_daily(date(2026, 9, 29))

        assert rc == 0
        target = date(2026, 9, 29)
        assert daily.pipeline.mark_done.call_args_list == [
            call(step, target, f"{step} 摘要") for step, _ in daily.STEPS]

    def test_none_returning_step_marks_empty_detail(self, patch_pipeline, patch_today,
                                                    patch_trading_day):
        """桩函数/_run_step 返回 None 时，detail 归一化为 '' 而不是 None。"""
        with patch.object(daily, "_run_step", side_effect=lambda step, d: None):
            rc = daily.run_daily(date(2026, 9, 29))

        assert rc == 0
        target = date(2026, 9, 29)
        assert daily.pipeline.mark_done.call_args_list == [
            call(step, target, "") for step, _ in daily.STEPS]

    def test_signal_and_ladder_details_land_in_pipeline_state(
            self, patch_pipeline, patch_today, patch_trading_day, patch_all_steps):
        """真实分派下：signal / ladder 两步带摘要，其余步骤允许为空串。"""
        rc = daily.run_daily(date(2026, 9, 29))

        assert rc == 0
        by_step = {c.args[0]: c.args[2] for c in daily.pipeline.mark_done.call_args_list}
        assert list(by_step) == [s for s, _ in daily.STEPS]
        assert "signals=" in by_step["signal"]
        assert by_step["ladder"] == "候选=42"


# ── 3. run_daily: 守卫与异常处理 ──────────────────────────────────────

class TestRunDailyGuardAndExceptions:
    """守卫拦截、CoverageBlocked、通用异常。"""

    def test_non_trading_day_returns_zero(self, patch_pipeline, patch_today, patch_trading_day):
        """非交易日直接返回 0，不执行任何步骤。"""
        with patch.object(daily, "_run_step") as mock_run:
            rc = daily.run_daily(date(2026, 9, 27))  # 周日

        assert rc == 0
        mock_run.assert_not_called()

    def test_coverage_blocked_returns_exit_code_76(self, patch_pipeline, patch_today, patch_trading_day):
        """CoverageBlocked 应返回 EXIT_COVERAGE_BLOCKED (76)，不返回 1。"""
        def raise_coverage_blocked(step, trade_date):
            if step == "coverage":
                raise CoverageBlocked("覆盖率不足 0.90")

        with patch.object(daily, "_run_step", side_effect=raise_coverage_blocked):
            rc = daily.run_daily(date(2026, 9, 29))

        assert rc == EXIT_COVERAGE_BLOCKED
        daily.pipeline.mark_failed.assert_called_once()

    def test_coverage_blocked_stops_chain(self, patch_pipeline, patch_today, patch_trading_day):
        """CoverageBlocked 后步骤链应停止。"""
        step_calls = []

        def fail_at_coverage(step, trade_date):
            step_calls.append(step)
            if step == "coverage":
                raise CoverageBlocked("mock")

        with patch.object(daily, "_run_step", side_effect=fail_at_coverage):
            daily.run_daily(date(2026, 9, 29))

        # coverage 之后不应有 derive
        assert "derive" not in step_calls

    def test_general_exception_returns_1(self, patch_pipeline, patch_today, patch_trading_day):
        """通用异常应返回 1，mark_failed 被调用。"""
        def raise_error(step, trade_date):
            if step == "sync":
                raise RuntimeError("东财 502")

        with patch.object(daily, "_run_step", side_effect=raise_error):
            rc = daily.run_daily(date(2026, 9, 29))

        assert rc == 1
        daily.pipeline.mark_failed.assert_called_once()

    def test_exception_stops_chain(self, patch_pipeline, patch_today, patch_trading_day):
        """异常后步骤链应停止。"""
        step_calls = []

        def fail_at_sync(step, trade_date):
            step_calls.append(step)
            if step == "sync":
                raise RuntimeError("mock")

        with patch.object(daily, "_run_step", side_effect=fail_at_sync):
            daily.run_daily(date(2026, 9, 29))

        assert step_calls == ["sync"]


# ── 4. _run_step: 步骤分派 ────────────────────────────────────────────

class TestRunStepDispatch:
    """_run_step 按步骤名分派到对应实现。"""

    def test_sync_dispatches_to_snapshot_daily(self):
        with patch("emotion_core.services.ingest.snapshot_daily") as mock:
            daily._run_step("sync", date(2026, 9, 29))
            mock.assert_called_once_with(date(2026, 9, 29))

    def test_coverage_dispatches_to_gate(self):
        with patch("emotion_core.services.coverage.gate") as mock:
            daily._run_step("coverage", date(2026, 9, 29))
            mock.assert_called_once_with(date(2026, 9, 29))

    def test_derive_dispatches(self):
        with patch("emotion_core.services.derive_service.run") as mock:
            daily._run_step("derive", date(2026, 9, 29))
            mock.assert_called_once_with(date(2026, 9, 29))

    def test_ladder_dispatches(self):
        with patch("emotion_core.algorithms.ladder.persist") as mock:
            daily._run_step("ladder", date(2026, 9, 29))
            mock.assert_called_once_with(date(2026, 9, 29))

    def test_unknown_step_raises(self):
        with pytest.raises(ValueError, match="未知步骤"):
            daily._run_step("nonexistent", date(2026, 9, 29))


# ── 5. signal 步产出摘要（pipeline_state.detail） ──────────────────────

class TestSignalStepDetail:
    """signal 步 detail：``signals=1 code=… action=… buy_window=… source=…``
    / ``signals=0 buy_window=…``。

    这是 docs/05 §P4 ①（signal 表 `source='live'` 恒 0 行、signal 步却天天
    `status=OK` / `detail=''`）留下的可观测缺口：``entry.check_signal`` 只在
    五条件全过时 ``_persist``、其余只写日志，库里分不出「当天没信号」与
    「步骤没跑」。``Signal`` 无 buy_window 属性（domain/signal.py:39-47），
    故该字段取 ``entry.current_window()`` 的当日窗口。
    """

    @staticmethod
    def _signal() -> Signal:
        """按 domain/signal.py 真实定义构造（不编造属性、不用 SimpleNamespace）。"""
        return Signal(
            code="600519", date=date(2026, 9, 29), action=Action.BUY,
            checklist=Checklist(c1_uniqueness=True, c2_exchange=True,
                                c3_elimination=True, c4_min_days=True,
                                c5_strength_diverge=True, w1_crowding=True),
            source=SignalSource.LIVE,
        )

    def test_written_signal_is_recorded(self):
        with patch("emotion_core.services.signal_service.run", return_value=self._signal()), \
                patch("emotion_core.algorithms.entry.current_window",
                      return_value="ENHANCED"):
            assert (daily._run_step("signal", date(2026, 9, 29))
                    == "signals=1 code=600519 action=BUY"
                       " buy_window=ENHANCED source=live")

    def test_no_signal_reports_zero_and_window(self):
        with patch("emotion_core.services.signal_service.run", return_value=None), \
                patch("emotion_core.algorithms.entry.current_window",
                      return_value="STANDARD"):
            assert (daily._run_step("signal", date(2026, 9, 29))
                    == "signals=0 buy_window=STANDARD")

    def test_none_window_reported_as_is(self):
        with patch("emotion_core.services.signal_service.run", return_value=None), \
                patch("emotion_core.algorithms.entry.current_window", return_value="NONE"):
            assert (daily._run_step("signal", date(2026, 9, 29))
                    == "signals=0 buy_window=NONE")

    def test_window_read_failure_degrades_without_raising(self):
        """打点是旁路：读窗口失败不得把 signal 步变成 FAILED。"""
        with patch("emotion_core.services.signal_service.run", return_value=None), \
                patch("emotion_core.algorithms.entry.current_window",
                      side_effect=RuntimeError("db down")):
            assert (daily._run_step("signal", date(2026, 9, 29))
                    == "signals=0 buy_window=unknown")

    def test_detail_reaches_pipeline_state(self, patch_pipeline, patch_today,
                                           patch_trading_day, patch_all_steps, monkeypatch):
        """端到端（编排层）：有信号时摘要经 mark_done 落 pipeline_state。"""
        monkeypatch.setattr("emotion_core.services.signal_service.run",
                            lambda d: self._signal())
        monkeypatch.setattr("emotion_core.algorithms.entry.current_window",
                            lambda d: "ENHANCED")

        assert daily.run_daily(date(2026, 9, 29)) == 0

        by_step = {c.args[0]: c.args[2] for c in daily.pipeline.mark_done.call_args_list}
        assert by_step["signal"] == ("signals=1 code=600519 action=BUY"
                                     " buy_window=ENHANCED source=live")


# ── 6. 默认参数 ───────────────────────────────────────────────────────

class TestDefaultArgs:
    """默认参数行为。"""

    def test_default_date_is_today(self, patch_pipeline, patch_today, patch_trading_day):
        """不传 date 时默认用 today_sh()。"""
        with patch.object(daily, "_run_step") as mock_run:
            daily.run_daily()

        # 验证至少执行了一个步骤（今天是交易日）
        mock_run.assert_called()

    def test_steps_list_has_13_entries(self):
        """STEPS 注册表必须有 13 步（R20 起含 hot）。"""
        assert len(daily.STEPS) == 13
        step_names = [s for s, _ in daily.STEPS]
        expected = ["sync", "coverage", "hot", "derive", "market", "ladder", "signal",
                    "promotion", "theme", "ecosystem", "strategy", "outcome", "health"]
        assert step_names == expected
