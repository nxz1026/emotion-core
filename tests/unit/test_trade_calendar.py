"""独立交易日历（§sync 根因修复）：源 materialize、缓存查询、降级与守卫接线。

不碰真库、不联网：`_fetch_open_dates` / `lookup` / `refresh` 全部可替换。
"""
from __future__ import annotations

from datetime import date

import pytest

from emotion_core.data import trade_calendar as cal
from emotion_core.orchestration import daily

SUN, MON, THU, FRI = (date(2026, 9, 27), date(2026, 9, 28),
                      date(2026, 9, 24), date(2026, 9, 25))


# ── materialize：开市日列表 → 区间内每一自然日 ──────────────────
def test_rows_materialize_covers_closed_days_in_range():
    rows = cal.rows_from_open_dates([THU, MON])
    assert rows == [(THU, True), (FRI, False), (date(2026, 9, 26), False),
                    (SUN, False), (MON, True)]
    assert len(rows) == 5, "区间内每个自然日都要有行，否则分不清「休市」与「越界」"


def test_rows_materialize_empty_source_is_empty():
    assert cal.rows_from_open_dates([]) == []


def test_refresh_upserts_materialized_rows(monkeypatch):
    seen: list[tuple] = []

    class Cur:
        def executemany(self, sql, params):
            seen.extend(params)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Conn:
        def cursor(self):
            return Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cal, "_fetch_open_dates", lambda: [THU, MON])
    monkeypatch.setattr(cal, "transaction", lambda: Conn())
    info = cal.refresh()
    assert (info["open_n"], info["rows"]) == (2, 5)
    assert (THU, True, cal.SOURCE) in seen
    assert (SUN, False, cal.SOURCE) in seen, "周日必须显式落 is_open=false"


# ── 查询与降级 ────────────────────────────────────────────────
def test_lookup_open_closed_and_missing(monkeypatch):
    table = {THU: True, FRI: False}
    monkeypatch.setattr(cal, "connect_ro", lambda: _Conn(table))
    assert cal.lookup(THU) is True
    assert cal.lookup(FRI) is False
    assert cal.lookup(SUN) is None, "区间外/未知必须返回 None（区别于休市）"


def test_is_trading_day_uses_cache_without_refresh(monkeypatch):
    monkeypatch.setattr(cal, "connect_ro", lambda: _Conn({SUN: False, THU: True}))
    monkeypatch.setattr(cal, "refresh", lambda: pytest.fail("命中缓存不该刷新"))
    assert cal.is_trading_day(SUN) is False
    assert cal.is_trading_day(THU) is True


def test_is_trading_day_refreshes_when_out_of_range(monkeypatch):
    """目标日超出缓存区间 → 自动刷新一次再判（懒加载，免运维）。"""
    state = {"table": {THU: True}, "refreshed": 0}

    def fake_refresh():
        state["refreshed"] += 1
        state["table"][MON] = True
        return {}

    monkeypatch.setattr(cal, "connect_ro", lambda: _Conn(state["table"]))
    monkeypatch.setattr(cal, "refresh", fake_refresh)
    assert cal.is_trading_day(MON) is True
    assert state["refreshed"] == 1
    # 第二次命中缓存，不再刷新
    assert cal.is_trading_day(MON) is True
    assert state["refreshed"] == 1


def test_source_failure_falls_back_to_weekday(monkeypatch):
    """取源失败 → 工作日保守判据（周日必拒），绝不因网络问题放行周末。"""
    monkeypatch.setattr(cal, "connect_ro", lambda: _Conn({}))
    monkeypatch.setattr(cal, "refresh", _boom)
    assert cal.is_trading_day(SUN) is False
    assert cal.is_trading_day(date(2026, 9, 23)) is True   # 周三


def test_db_failure_falls_back_to_weekday(monkeypatch):
    monkeypatch.setattr(cal, "connect_ro", lambda: _boom())
    assert cal.is_trading_day(SUN) is False
    assert cal.is_trading_day(date(2026, 9, 23)) is True


# ── 守卫接线：判据必须来自独立日历，而非库内 daily_bar ──────────
def test_guard_asks_the_independent_calendar(monkeypatch):
    asked: list[date] = []

    def fake_is_trading_day(d, **kw):
        asked.append(d)
        return d == THU

    monkeypatch.setattr(cal, "is_trading_day", fake_is_trading_day)
    monkeypatch.setattr(daily, "today_sh", lambda: MON)
    assert daily._trading_day_guard(THU) is True
    assert daily._trading_day_guard(SUN) is False
    assert asked == [THU, SUN], "守卫必须逐次问独立日历"


def test_guard_rejects_holiday_and_weekend_even_with_dirty_data(monkeypatch):
    """2026-09-25（周五中秋）/09-27（周日）在库内都曾有行 → 守卫仍须拒绝。"""
    monkeypatch.setattr(cal, "is_trading_day", lambda d, **kw: d == THU)
    monkeypatch.setattr(daily, "today_sh", lambda: MON)
    assert daily._trading_day_guard(FRI) is False
    assert daily._trading_day_guard(SUN) is False
    assert daily._trading_day_guard(THU) is True


def test_guard_skips_future_date(monkeypatch):
    monkeypatch.setattr(cal, "is_trading_day", lambda d, **kw: True)
    monkeypatch.setattr(daily, "today_sh", lambda: THU)
    assert daily._trading_day_guard(MON) is False, "未来日不可能有数据"


def test_guard_lets_a_real_trading_day_without_data_through(monkeypatch):
    """交易日但行情未发布 → 不静默跳过，走到 sync 响亮失败（数据延迟必须可见）。"""
    monkeypatch.setattr(cal, "is_trading_day", lambda d, **kw: d == MON)
    monkeypatch.setattr(daily, "today_sh", lambda: MON)
    assert daily._trading_day_guard(MON) is True


def test_weekend_run_is_a_noop(monkeypatch):
    """周日跑 daily：直接 0 退出，不碰 pipeline_state、不进任何步骤。"""
    monkeypatch.setattr(cal, "is_trading_day", lambda d, **kw: False)
    called: list[str] = []
    monkeypatch.setattr(daily, "_run_step", lambda s, d: called.append(s))
    assert daily.run_daily(SUN) == 0
    assert called == []


# ── 工具 ─────────────────────────────────────────────────────
def _boom(*_a, **_kw):
    raise RuntimeError("源不可用")


class _Row(list):
    pass


class _Cur:
    def __init__(self, table):
        self._table = table
        self._row = None

    def execute(self, sql, params=()):
        if "min(date), max(date)" in sql:
            keys = sorted(self._table)
            self._row = (keys[0], keys[-1]) if keys else (None, None)
        else:
            d = params[0]
            self._row = None if d not in self._table else (self._table[d],)
        return self

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, table):
        self._table = table

    def cursor(self):
        return _Cur(self._table)

    def execute(self, sql, params=()):
        return _Cur(self._table).execute(sql, params)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
