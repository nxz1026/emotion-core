"""链路健康推送（_trading_gap / check_report_gap / check_data_gap /
check_outcome_stale / run / record_once / push）：不连库。

语义来源 lkl/services/health.py（无同名 lkl 测试，用例按源文件分支重写）；
差异仅限 IO 适配（见 health.py 模块文档）：读走 emotion_core.utils.db.query_df、
自持 _recent_trading_days（lkl dates.recent_trading_days 同 SQL）、dates.today_sh()、
alerts 走 emotion_core.algorithms.alerts。

IO 全部桩掉：health.query_df 按 SQL 特征分派并记录 (sql, params)；断档用例内
health._recent_trading_days 换为记录调用参数的空表；health.alerts 的 pending /
record / push_pending_webhook 三入口；health.dates.today_sh（缺省交易日用例）。
真实库对账另跑（编排者阶段）。
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd
import pytest

from emotion_core.algorithms import health

D0 = date(2026, 9, 25)


class FakeQuery:
    """health.query_df 替身：按 SQL 特征返回预置帧，记录全部 (sql, params)。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.report = None          # review_report max(date)
        self.data = None            # derived_bar max(date)
        self.outcome = None         # signal LEFT JOIN signal_outcome
        self.trading = None         # daily_bar（_recent_trading_days）

    @staticmethod
    def _frame(vals, col: str) -> pd.DataFrame:
        if isinstance(vals, pd.DataFrame):
            return vals
        if vals is None:
            return pd.DataFrame(columns=[col])
        return pd.DataFrame({col: list(vals)})

    def __call__(self, sql: str, params=(), conn=None) -> pd.DataFrame:
        self.calls.append((sql, tuple(params)))
        if "FROM review_report" in sql:
            return self._frame(self.report, "d")
        if "FROM derived_bar" in sql:
            return self._frame(self.data, "d")
        if "FROM signal s" in sql:
            return self.outcome if self.outcome is not None else pd.DataFrame(
                columns=["confirm_date", "code"])
        if "FROM daily_bar" in sql:
            return self._frame(self.trading, "date")
        raise AssertionError(f"未预期的 SQL: {sql}")


class GapStub:
    """health._recent_trading_days 替身：返回预置交易日并记录 (d, n)。"""

    def __init__(self) -> None:
        self.days: list[date] = []
        self.calls: list[tuple[date, int]] = []

    def __call__(self, d: date, n: int) -> list[date]:
        self.calls.append((d, n))
        return self.days


class AlertsStub:
    """health.alerts 三入口替身（pending / record / push_pending_webhook）。"""

    def __init__(self, pending_sources=(), raise_record=False,
                 raise_webhook=False) -> None:
        self.pending_sources = list(pending_sources)
        self.raise_record = raise_record
        self.raise_webhook = raise_webhook
        self.pending_limits: list[int] = []
        self.recorded: list[tuple[str, str, str]] = []
        self.webhooks = 0

    def pending(self, limit: int = 100) -> list[dict]:
        self.pending_limits.append(limit)
        return [{"source": s} for s in self.pending_sources]

    def record(self, level: str, source: str, detail: str) -> int:
        if self.raise_record:
            raise RuntimeError("alert db down")
        self.recorded.append((level, source, detail))
        return 1

    def push_pending_webhook(self) -> None:
        self.webhooks += 1
        if self.raise_webhook:
            raise RuntimeError("webhook down")


@pytest.fixture
def db(monkeypatch) -> FakeQuery:
    q = FakeQuery()
    monkeypatch.setattr(health, "query_df", q)
    return q


@pytest.fixture
def gap(monkeypatch) -> GapStub:
    stub = GapStub()
    monkeypatch.setattr(health, "_recent_trading_days", stub)
    return stub


@pytest.fixture
def alerts_stub(monkeypatch) -> AlertsStub:
    stub = AlertsStub()
    monkeypatch.setattr(health, "alerts", stub)
    return stub


# ────────────────────────── 阈值 / CHECKS ──────────────────────────

def test_thresholds_and_checks_order_locked():
    assert (health.REPORT_GAP_DAYS, health.DATA_GAP_DAYS,
            health.OUTCOME_STALE_DAYS) == (2, 2, 6)
    assert [name for name, _ in health.CHECKS] == ["report", "data", "outcome"]
    assert [fn for _, fn in health.CHECKS] == [
        health.check_report_gap, health.check_data_gap, health.check_outcome_stale]


# ────────────────────────── _trading_gap ──────────────────────────

class TestTradingGap:
    def test_since_none_returns_max_and_skips_calendar(self, gap):
        assert health._trading_gap(D0, None) == 10**9
        assert gap.calls == []

    def test_open_interval_excludes_endpoints(self, gap):
        gap.days = [date(2026, 9, 18), date(2026, 9, 19), date(2026, 9, 22),
                    date(2026, 9, 23), date(2026, 9, 24), D0]
        # (9-19, 9-25) 开区间：9-19（since）与 9-25（trade_date）均不计
        assert health._trading_gap(D0, date(2026, 9, 19)) == 3
        assert gap.calls == [(D0, 60)]

    def test_zero_when_no_day_in_between(self, gap):
        gap.days = [date(2026, 9, 24), D0]
        assert health._trading_gap(D0, date(2026, 9, 24)) == 0


# ────────────────────────── _recent_trading_days（自持 gap） ──────────────────────────

class TestRecentTradingDays:
    def test_sql_locked_and_sorted_ascending(self, db):
        db.trading = [D0, date(2026, 9, 24), date(2026, 9, 23)]
        assert health._recent_trading_days(D0, 3) == [
            date(2026, 9, 23), date(2026, 9, 24), D0]
        sql, params = db.calls[0]
        assert "SELECT DISTINCT date FROM daily_bar" in sql
        assert "WHERE date <= %s" in sql
        assert "ORDER BY date DESC LIMIT %s" in sql
        assert params == (D0, 3)


# ────────────────────────── check_report_gap ──────────────────────────

class TestCheckReportGap:
    def test_empty_df_never_produced(self, db, gap):
        assert health.check_report_gap(D0) == \
            f"从未产出报告（截至 {D0} 无 review_report 落盘）"
        sql, params = db.calls[0]
        assert sql == "SELECT max(date) AS d FROM review_report WHERE date < %s"
        assert params == (D0,)
        assert gap.calls == []

    def test_null_last_never_produced(self, db, gap):
        db.report = [None]
        assert health.check_report_gap(D0) == \
            f"从未产出报告（截至 {D0} 无 review_report 落盘）"

    def test_gap_at_threshold_warns(self, db, gap):
        last = date(2026, 9, 22)
        db.report = [last]
        gap.days = [last, date(2026, 9, 23), date(2026, 9, 24), D0]
        assert health.check_report_gap(D0) == (
            "报告断档：最近成功报告 2026-09-22 距今 2 个交易日（阈值 2），"
            "疑似 daily.sh 未跑/中断")
        assert gap.calls == [(D0, 60)]

    def test_gap_below_threshold_returns_none(self, db, gap):
        last = date(2026, 9, 22)
        db.report = [last]
        gap.days = [last, date(2026, 9, 23), D0]     # 开区间只 1 天 < 2
        assert health.check_report_gap(D0) is None


# ────────────────────────── check_data_gap ──────────────────────────

class TestCheckDataGap:
    def test_empty_df_no_history(self, db, gap):
        assert health.check_data_gap(D0) == f"derived_bar 无任何历史数据（截至 {D0}）"
        sql, params = db.calls[0]
        assert sql == "SELECT max(date) AS d FROM derived_bar WHERE date < %s"
        assert params == (D0,)

    def test_null_last_no_history(self, db, gap):
        db.data = [None]
        assert health.check_data_gap(D0) == f"derived_bar 无任何历史数据（截至 {D0}）"

    def test_gap_at_threshold_warns_collection_stalled(self, db, gap):
        last = date(2026, 9, 22)
        db.data = [last]
        gap.days = [last, date(2026, 9, 23), date(2026, 9, 24), D0]
        assert health.check_data_gap(D0) == (
            "derived_bar 最近数据日 2026-09-22 距今 2 个交易日（阈值 2），"
            "疑似 fetch/derive 采集停摆")
        assert "采集停摆" in health.check_data_gap(D0)

    def test_gap_below_threshold_returns_none(self, db, gap):
        last = date(2026, 9, 22)
        db.data = [last]
        gap.days = [last, date(2026, 9, 23), D0]
        assert health.check_data_gap(D0) is None


# ────────────────────────── check_outcome_stale ──────────────────────────

SEVEN = [date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18),
         date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23),
         date(2026, 9, 24)]


class TestCheckOutcomeStale:
    def test_insufficient_history_returns_none_without_signal_query(self, db, gap):
        gap.days = [date(2026, 9, 24), D0]           # 2 < 6+1
        assert health.check_outcome_stale(D0) is None
        assert gap.calls == [(D0, health.OUTCOME_STALE_DAYS + 1)]
        assert db.calls == []                        # 历史不足不查 signal

    def test_empty_result_returns_none_but_predicates_locked(self, db, gap):
        gap.days = SEVEN
        assert health.check_outcome_stale(D0) is None
        sql, params = db.calls[0]
        assert "SELECT s.confirm_date, s.code FROM signal s" in sql
        assert "LEFT JOIN signal_outcome o USING (confirm_date, code, action)" in sql
        assert "s.action = 'BUY'" in sql
        assert "s.confirm_date <= %s" in sql
        assert "(o.confirm_date IS NULL OR NOT o.complete)" in sql
        assert "ORDER BY s.confirm_date DESC LIMIT 5" in sql
        assert params == (SEVEN[0],)                 # stale = cutoff[0]

    def test_rows_render_names_joined_by_dunhao(self, db, gap):
        gap.days = SEVEN
        db.outcome = pd.DataFrame({
            "confirm_date": [date(2026, 9, 16), date(2026, 9, 17)],
            "code": ["000017", "600371"]})
        assert health.check_outcome_stale(D0) == (
            "2 条 ≥6 交易日前 BUY 信号无 complete outcome（L3 未回填）："
            "2026-09-16 000017、2026-09-17 600371")
        assert db.calls[0][1] == (SEVEN[0],)


# ────────────────────────── run ──────────────────────────

class TestRun:
    def test_collects_in_checks_order_filtering_none(self, db, gap):
        gap.days = [date(2026, 9, 11), date(2026, 9, 14), date(2026, 9, 15),
                    date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18),
                    date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23),
                    date(2026, 9, 24), D0]
        db.report = [date(2026, 9, 11)]              # 断档（gap 10 ≥ 2）
        db.data = [date(2026, 9, 24)]                # 未断档（gap 0 < 2）
        db.outcome = pd.DataFrame({"confirm_date": [date(2026, 9, 11)],
                                   "code": ["000017"]})
        out = health.run(D0)
        assert len(out) == 2
        assert out[0].startswith("报告断档")
        assert "L3 未回填" in out[1]
        assert not any("derived_bar" in d for d in out)

    def test_default_trade_date_uses_today_sh(self, monkeypatch):
        sentinel = date(2026, 1, 2)
        monkeypatch.setattr(health.dates, "today_sh", lambda: sentinel)
        seen: list[date] = []
        monkeypatch.setattr(health, "CHECKS", (("x", seen.append),))
        assert health.run() == []
        assert seen == [sentinel]

    def test_explicit_trade_date_skips_today_sh(self, monkeypatch):
        def boom():
            raise AssertionError("显式 trade_date 不应走 today_sh")
        monkeypatch.setattr(health.dates, "today_sh", boom)
        seen: list[date] = []
        monkeypatch.setattr(health, "CHECKS", (("x", lambda td: seen.append(td) or "d"),))
        assert health.run(D0) == ["d"]
        assert seen == [D0]


# ────────────────────────── record_once ──────────────────────────

class TestRecordOnce:
    def test_pending_health_source_skips_enqueue(self, alerts_stub):
        alerts_stub.pending_sources = ["health"]
        assert health.record_once(["a", "b"]) == 0
        assert alerts_stub.pending_limits == [200]
        assert alerts_stub.recorded == []

    def test_first_detail_enqueued_then_health_dedup_same_round(self, alerts_stub):
        assert health.record_once(["a", "b"]) == 1
        assert alerts_stub.pending_limits == [200]
        # lkl 逐字行为：首条入队后 "health" 进入 pending 集合，同轮后续 detail 跳过
        assert alerts_stub.recorded == [("WARN", "health", "a")]

    def test_record_failure_warns_and_not_counted(self, alerts_stub, caplog):
        alerts_stub.raise_record = True
        with caplog.at_level(logging.WARNING, logger="emotion_core.health"):
            assert health.record_once(["boom"]) == 0
        assert "health 告警入队失败：boom" in caplog.text


# ────────────────────────── push ──────────────────────────

class TestPush:
    def test_enqueue_before_webhook_and_returns_n(self, monkeypatch, alerts_stub):
        events: list[tuple] = []
        monkeypatch.setattr(health, "run",
                            lambda td: events.append(("run", td)) or ["d1"])
        monkeypatch.setattr(health, "record_once",
                            lambda details, td: events.append(("record", details, td)) or 7)
        monkeypatch.setattr(alerts_stub, "push_pending_webhook",
                            lambda: events.append(("webhook",)))
        assert health.push(D0) == 7
        assert events == [("run", D0), ("record", ["d1"], D0), ("webhook",)]

    def test_webhook_error_swallowed(self, monkeypatch, alerts_stub, caplog):
        monkeypatch.setattr(health, "run", lambda td: ["d1"])
        monkeypatch.setattr(health, "record_once", lambda details, td: 3)
        alerts_stub.raise_webhook = True
        with caplog.at_level(logging.WARNING, logger="emotion_core.health"):
            assert health.push() == 3
        assert "health webhook 推送失败（不影响告警入队）" in caplog.text
