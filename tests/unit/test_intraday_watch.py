"""`services.intraday_watch` 与 `loader` 的盘中台账 I/O 单测（ADR 0003 §D6）。

结算口径逐条钉住：
- 卖点 T+1 开盘；T+1 跌停封死顺延 T+2（`rolled=True`）；两天都封死 → 不结算（留 NULL）
- `closed_sealed` / `broke` 直接取 `derived_bar.is_limit_up` / `is_bomb`
- `ret_pct` 扣双边费 `FEE_PCT`；缺数一律不猜
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from emotion_core.data import loader
from emotion_core.services import intraday_watch as w


def _trig(**over):
    base = {"date": "2026-10-22", "code": "000530", "entry_px": 9.49,
            "trigger_price": 9.49}
    base.update(over)
    return base


_DERIVED = {"is_limit_up": True, "is_bomb": False}


class TestSettleOne:
    def test_normal_t1_open(self):
        row = w.settle_one(_trig(), _DERIVED, 10.44, None)
        assert row is not None
        assert row["exit_px"] == 10.44
        assert row["rolled"] is False
        assert row["closed_sealed"] is True
        assert row["broke"] is False
        # (10.44/9.49 - 1) * 100 - 0.15 = 9.858... - 0.15
        assert row["ret_pct"] == round((10.44 / 9.49 - 1) * 100 - 0.15, 3)

    def test_t1_locked_rolls_to_t2(self):
        row = w.settle_one(_trig(), _DERIVED, None, 9.00)
        assert row is not None
        assert row["rolled"] is True
        assert row["exit_px"] == 9.00
        assert row["ret_pct"] < 0

    def test_both_days_locked_stays_pending(self):
        assert w.settle_one(_trig(), _DERIVED, None, None) is None

    def test_missing_derived_stays_pending(self):
        """判据行缺数 = 不知道封没封住，不结算（缺数≠开板）。"""
        assert w.settle_one(_trig(), None, 10.0, None) is None

    def test_missing_entry_stays_pending(self):
        assert w.settle_one(_trig(entry_px=None, trigger_price=None), _DERIVED, 10.0, None) is None

    def test_entry_falls_back_to_trigger_price(self):
        row = w.settle_one(_trig(entry_px=None, trigger_price=9.49), _DERIVED, 10.44, None)
        assert row is not None and row["exit_px"] == 10.44

    def test_broke_flag_from_derived(self):
        row = w.settle_one(_trig(), {"is_limit_up": False, "is_bomb": True}, 9.60, None)
        assert row is not None
        assert row["closed_sealed"] is False
        assert row["broke"] is True


class TestSettle:
    def test_no_triggers(self, monkeypatch):
        monkeypatch.setattr(w.loader, "load_triggers", lambda d, unsettled_only=False: [])
        out = w.settle(__import__("datetime").date(2026, 10, 22))
        assert out["triggers"] == 0 and out["settled"] == 0

    def test_settles_and_writes(self, monkeypatch):
        import datetime as dt
        written = {}
        monkeypatch.setattr(w.loader, "load_triggers",
                            lambda d, unsettled_only=False: [_trig()])
        monkeypatch.setattr(w.loader, "save_settlements",
                            lambda rows: written.setdefault("rows", rows) or len(rows))
        monkeypatch.setattr(w, "_derived", lambda d, codes: {"000530": _DERIVED})
        monkeypatch.setattr(w, "next_trading_day",
                            lambda d: dt.date(2099, 1, 1) if d < dt.date(2099, 1, 1) else dt.date(2099, 1, 2))
        monkeypatch.setattr(w.exit_svc, "open_locked_limit_down", lambda d, code: False)
        monkeypatch.setattr(w, "_open_px", lambda d, code: 10.44 if d == dt.date(2099, 1, 1) else 9.0)

        out = w.settle(dt.date(2026, 10, 22))
        assert out["settled"] == 1 and out["pending"] == 0
        assert written["rows"][0]["exit_px"] == 10.44

    def test_dry_run_does_not_write(self, monkeypatch):
        import datetime as dt
        calls = []
        monkeypatch.setattr(w.loader, "load_triggers", lambda d, unsettled_only=False: [_trig()])
        monkeypatch.setattr(w.loader, "save_settlements", lambda rows: calls.append(rows))
        monkeypatch.setattr(w, "_derived", lambda d, codes: {"000530": _DERIVED})
        monkeypatch.setattr(w, "next_trading_day", lambda d: dt.date(2099, 1, 1))
        monkeypatch.setattr(w.exit_svc, "open_locked_limit_down", lambda d, code: False)
        monkeypatch.setattr(w, "_open_px", lambda d, code: 10.44)

        out = w.settle(dt.date(2026, 10, 22), dry_run=True)
        assert out["settled"] == 1
        assert calls == []

    def test_locked_t1_falls_through_to_t2(self, monkeypatch):
        import datetime as dt
        monkeypatch.setattr(w.loader, "load_triggers", lambda d, unsettled_only=False: [_trig()])
        monkeypatch.setattr(w.loader, "save_settlements", lambda rows: len(rows))
        monkeypatch.setattr(w, "_derived", lambda d, codes: {"000530": _DERIVED})
        monkeypatch.setattr(w, "next_trading_day",
                            lambda d: dt.date(2099, 1, 1) if d < dt.date(2099, 1, 1) else dt.date(2099, 1, 2))
        monkeypatch.setattr(w.exit_svc, "open_locked_limit_down",
                            lambda d, code: d == dt.date(2099, 1, 1))
        monkeypatch.setattr(w, "_open_px", lambda d, code: 9.0)

        out = w.settle(dt.date(2026, 10, 22))
        assert out["settled"] == 1
        assert out["rows"][0]["rolled"] is True


class TestCli:
    def test_requires_settle_flag(self):
        with pytest.raises(SystemExit):
            w.main([])

    def test_settle_writes_nothing_without_rows(self, monkeypatch, capsys):
        monkeypatch.setattr(w.loader, "load_triggers", lambda d, unsettled_only=False: [])
        assert w.main(["--settle", "--date", "2026-10-22"]) == 0
        assert "触发 0 条" in capsys.readouterr().out


class TestLoaderSql:
    def test_insert_is_first_trigger_wins(self):
        sql = loader._TRIGGER_INSERT_SQL
        assert "ON CONFLICT (date, code) DO NOTHING" in sql

    def test_settle_sql_updates_settled_at(self):
        flat = " ".join(loader._TRIGGER_SETTLE_SQL.split())
        assert flat.upper().startswith("UPDATE TRIGGER_LOG SET")
        assert "settled_at=now()" in flat
        assert flat.endswith("WHERE date=%s AND code=%s")

    def test_load_triggers_unsettled_filter(self, monkeypatch):
        captured = {}

        class _Cur:
            description: list = []

            def execute(self, sql, params):
                captured["sql"] = sql

            def fetchall(self):
                return []

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        class _Conn:
            def cursor(self):
                return _Cur()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(loader, "connect", lambda: _Conn())
        assert loader.load_triggers(__import__("datetime").date(2026, 10, 22),
                                    unsettled_only=True) == []
        assert "settled_at IS NULL" in captured["sql"]


# ---- --round：判一轮（CPT R93 九列 → trigger_log 行） ----

def _rec(**over):
    """CPT `intraday_seal_snapshot` 的一行（R93 九列）。"""
    rec = {"at": "2026-10-23T01:35:00+00:00", "code": "600001", "sealed": True,
           "bid1_lots": 400000.0, "limit_price": 11.0, "price": 11.0,
           "prev_close": 10.0, "distance_pct": 0.0, "is_st": False}
    rec.update(over)
    return rec


class TestScanRound:
    def test_hits_early_first_board(self):
        rows = w.scan_round({"600001": _rec()}, d=date(2026, 10, 23),
                            now=time(9, 35), prev_days={"600001": 0},
                            names={"600001": "测试一号"})
        assert len(rows) == 1
        row = rows[0]
        assert row["code"] == "600001"
        assert row["name"] == "测试一号"
        assert row["trigger_price"] == 11.0
        assert row["entry_px"] == 11.0
        assert row["seal_bid1_vol"] == 40000000  # 手 → 股
        assert row["prev_days"] == 0
        assert row["first_seal_ts"] == time(9, 35)

    def test_not_first_board(self):
        rows = w.scan_round({"600001": _rec()}, d=date(2026, 10, 23),
                            now=time(9, 35), prev_days={"600001": 1})
        assert rows == []

    def test_prev_days_unknown_is_not_first_board(self):
        rows = w.scan_round({"600001": _rec()}, d=date(2026, 10, 23),
                            now=time(9, 35), prev_days={})
        assert rows == []

    def test_late_seal_is_skipped(self):
        rows = w.scan_round({"600001": _rec()}, d=date(2026, 10, 23),
                            now=time(10, 5), prev_days={"600001": 0})
        assert rows == []

    def test_thin_seal_is_skipped(self):
        rows = w.scan_round({"600001": _rec(bid1_lots=100.0)}, d=date(2026, 10, 23),
                            now=time(9, 35), prev_days={"600001": 0})
        assert rows == []

    def test_below_limit_is_skipped(self):
        rows = w.scan_round({"600001": _rec(price=10.95)}, d=date(2026, 10, 23),
                            now=time(9, 35), prev_days={"600001": 0})
        assert rows == []

    def test_missing_price_is_skipped(self):
        rows = w.scan_round({"600001": _rec(price=None)}, d=date(2026, 10, 23),
                            now=time(9, 35), prev_days={"600001": 0})
        assert rows == []

    def test_entry_falls_back_to_prev_close(self):
        rows = w.scan_round({"600001": _rec(limit_price=None)}, d=date(2026, 10, 23),
                            now=time(9, 35), prev_days={"600001": 0})
        assert len(rows) == 1
        assert rows[0]["entry_px"] == 11.0  # 10.0 × 1.1

    def test_name_absent_when_unknown(self):
        rows = w.scan_round({"600001": _rec()}, d=date(2026, 10, 23),
                            now=time(9, 35), prev_days={"600001": 0}, names={})
        assert rows[0]["name"] is None


def _two_rounds(monkeypatch, *, now_rec=None, prev_rec=None, count=None):
    """桩掉跨轮读取：返回「最新一轮 + 上一轮」（都能封住，除非显式给空）。"""
    at_now = datetime(2026, 10, 23, 1, 35, tzinfo=UTC)   # 北京 09:35
    at_prev = datetime(2026, 10, 23, 1, 33, tzinfo=UTC)  # 北京 09:33
    calls = {"count": count}
    cur = {"600001": _rec()} if now_rec is None else now_rec
    prev = {"600001": _rec()} if prev_rec is None else prev_rec

    def _fetch(want=2, codes=None):
        calls["count"] = want
        return [(at_now, cur), (at_prev, prev)]

    monkeypatch.setattr(w.cpt_intraday, "fetch_rounds_from_cpt", _fetch)
    return calls


class TestRunRound:
    def test_missing_table(self, monkeypatch):
        monkeypatch.setattr(w.cpt_intraday, "fetch_rounds_from_cpt", lambda *a, **k: None)
        out = w.run_round(dry_run=True)
        assert out["round_at"] is None
        assert out["written"] == 0

    def test_dry_run_does_not_write(self, monkeypatch):
        calls = _two_rounds(monkeypatch)
        monkeypatch.setattr(w, "prev_days_of", lambda d, codes: {"600001": 0})
        monkeypatch.setattr(w, "_names", lambda codes: {"600001": "测试一号"})
        monkeypatch.setattr(w.loader, "insert_triggers",
                            lambda rows: pytest.fail("dry-run 不许写库"))
        out = w.run_round(dry_run=True)
        assert calls["count"] >= 2, "默认应取两轮做确认"
        assert out["triggers"] == 1
        assert out["written"] == 0
        # UTC 01:35 = 北京 09:35，日期按北京日切
        assert out["date"] == date(2026, 10, 23)
        assert out["now"] == time(9, 35)

    def test_writes_trigger(self, monkeypatch):
        _two_rounds(monkeypatch)
        seen = {}
        monkeypatch.setattr(w, "prev_days_of", lambda d, codes: {"600001": 0})
        monkeypatch.setattr(w, "_names", lambda codes: {"600001": "测试一号"})

        def _insert(rows):
            seen["rows"] = rows
            return len(rows)

        monkeypatch.setattr(w.loader, "insert_triggers", _insert)
        out = w.run_round(dry_run=False)
        assert out["written"] == 1
        assert seen["rows"][0]["code"] == "600001"

    def test_single_round_cannot_confirm(self, monkeypatch):
        """只有一轮时无法确认：宁可漏，不可假触发（首轮 09:32 就属于这种）。"""
        at = datetime(2026, 10, 23, 1, 35, tzinfo=UTC)
        monkeypatch.setattr(w.cpt_intraday, "fetch_rounds_from_cpt",
                            lambda want=2, codes=None: [(at, {"600001": _rec()})])
        monkeypatch.setattr(w, "prev_days_of", lambda d, codes: {"600001": 0})
        monkeypatch.setattr(w, "_names", lambda codes: {})
        out = w.run_round(dry_run=True)
        assert out["triggers"] == 0
        assert out["round_at"] is not None

    def test_prev_round_missed_blocks_trigger(self, monkeypatch):
        """上一轮没封住 → 这一轮才封，不算「连续两轮」，不触发。"""
        _two_rounds(monkeypatch, prev_rec={})
        monkeypatch.setattr(w, "prev_days_of", lambda d, codes: {"600001": 0})
        monkeypatch.setattr(w, "_names", lambda codes: {})
        out = w.run_round(dry_run=True)
        assert out["triggers"] == 0

    def test_confirm_ticks_one_needs_no_history(self, monkeypatch):
        at = datetime(2026, 10, 23, 1, 35, tzinfo=UTC)
        monkeypatch.setattr(w.cpt_intraday, "fetch_rounds_from_cpt",
                            lambda *a, **k: pytest.fail("单轮模式不该查多轮"))
        monkeypatch.setattr(w.cpt_intraday, "fetch_round_from_cpt",
                            lambda: (at, {"600001": _rec()}))
        monkeypatch.setattr(w, "prev_days_of", lambda d, codes: {"600001": 0})
        monkeypatch.setattr(w, "_names", lambda codes: {})
        out = w.run_round(dry_run=True, confirm_ticks=1)
        assert out["triggers"] == 1


class TestCliRound:
    def test_round_flag_runs(self, monkeypatch, capsys):
        _two_rounds(monkeypatch)
        monkeypatch.setattr(w, "prev_days_of", lambda d, codes: {"600001": 0})
        monkeypatch.setattr(w, "_names", lambda codes: {})
        assert w.main(["--round", "--dry-run"]) == 0
        assert "触发 1 条" in capsys.readouterr().out

    def test_requires_one_of_round_or_settle(self):
        with pytest.raises(SystemExit):
            w.main([])


class TestConfirmTicksPure:
    """`scan_round` 的跨轮确认（纯函数，不碰库）。"""

    D = date(2026, 10, 23)

    def _rows(self, *, prev_round, ticks=2, rec=None):
        return w.scan_round(rec if rec is not None else {"600001": _rec()},
                            d=self.D, now=time(9, 35), prev_days={"600001": 0},
                            names={}, prev_round=prev_round, confirm_ticks=ticks)

    def test_two_rounds_confirm(self):
        rows = self._rows(prev_round=(time(9, 33), {"600001": _rec()}))
        assert [r["code"] for r in rows] == ["600001"]

    def test_prev_round_without_hit_blocks(self):
        assert self._rows(prev_round=(time(9, 33), {})) == []

    def test_no_prev_round_blocks(self):
        assert self._rows(prev_round=None) == []

    def test_ticks_one_ignores_prev(self):
        assert len(self._rows(prev_round=None, ticks=1)) == 1

    def test_prev_round_datetime_is_converted_to_beijing(self):
        # 回归：`fetch_rounds_from_cpt` 给的是 UTC datetime，直接当成 `time` 用会
        # 在「本轮有命中」时炸 TypeError（10:00 之后命中恒空，把 bug 藏了一整天）。
        utc = datetime(2026, 10, 23, 1, 33, tzinfo=ZoneInfo("UTC"))  # 北京 09:33
        rows = self._rows(prev_round=(utc, {"600001": _rec()}))
        assert [r["code"] for r in rows] == ["600001"]

    def test_prev_round_datetime_after_deadline_blocks(self):
        late = datetime(2026, 10, 23, 2, 35, tzinfo=ZoneInfo("UTC"))  # 北京 10:35
        assert self._rows(prev_round=(late, {"600001": _rec()})) == []


class TestSession:
    def test_weekend_never(self):
        sat = datetime(2026, 10, 24, 10, 0, tzinfo=w._BJ)  # 周六
        assert w.in_session(sat, trading_day=True) is False

    def test_before_open_and_after_close(self):
        early = datetime(2026, 10, 23, 9, 24, tzinfo=w._BJ)
        late = datetime(2026, 10, 23, 15, 6, tzinfo=w._BJ)
        assert w.in_session(early, trading_day=True) is False
        assert w.in_session(late, trading_day=True) is False

    def test_edges_are_inclusive(self):
        assert w.in_session(datetime(2026, 10, 23, 9, 25, tzinfo=w._BJ), trading_day=True)
        assert w.in_session(datetime(2026, 10, 23, 15, 5, tzinfo=w._BJ), trading_day=True)

    def test_non_trading_day(self):
        mid = datetime(2026, 10, 23, 10, 0, tzinfo=w._BJ)
        assert w.in_session(mid, trading_day=False) is False
        assert w.in_session(mid, trading_day=True) is True

    def test_lunch_break_is_out_of_session(self):
        """午休不扫：CPT 11:30–13:00 本来就不落轮次，扫了只会刷假告警。"""
        lunch = [datetime(2026, 10, 23, h, m, tzinfo=w._BJ)
                 for h, m in ((11, 33), (12, 0), (12, 57))]
        assert [w.in_session(t, trading_day=True) for t in lunch] == [False] * 3
        assert w.in_session(datetime(2026, 10, 23, 11, 32, tzinfo=w._BJ), trading_day=True)
        assert w.in_session(datetime(2026, 10, 23, 12, 58, tzinfo=w._BJ), trading_day=True)


class TestLoop:
    def test_skips_outside_session(self, monkeypatch):
        calls = []
        monkeypatch.setattr(w, "in_session", lambda now: False)
        monkeypatch.setattr(w, "run_round", lambda **k: calls.append(1))
        w.loop(max_rounds=2, sleep=lambda s: None)
        assert calls == []

    def test_runs_inside_session(self, monkeypatch):
        calls = []
        monkeypatch.setattr(w, "in_session", lambda now: True)
        monkeypatch.setattr(w, "run_round",
                            lambda **k: calls.append(k.get("dry_run")))
        w.loop(dry_run=True, max_rounds=2, sleep=lambda s: None)
        assert calls == [True, True]

    def test_single_round_failure_does_not_stop_loop(self, monkeypatch):
        calls = []

        def _boom(**k):
            calls.append(1)
            raise RuntimeError("一轮 502")

        monkeypatch.setattr(w, "in_session", lambda now: True)
        monkeypatch.setattr(w, "run_round", _boom)
        assert w.loop(max_rounds=3, sleep=lambda s: None) == 0
        assert len(calls) == 3



class TestRoundAge:
    """`_round_age_min`：UTC datetime → 距今分钟数。"""

    def test_utc_datetime(self):
        now = datetime(2026, 10, 23, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        at = datetime(2026, 10, 23, 1, 50, tzinfo=ZoneInfo("UTC"))  # 北京 09:50
        assert w._round_age_min(at, now) == pytest.approx(10.0)

    def test_naive_datetime_treated_as_utc(self):
        now = datetime(2026, 10, 23, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert w._round_age_min(datetime(2026, 10, 23, 1, 54), now) == pytest.approx(6.0)

    def test_missing_round_gives_none(self):
        now = datetime(2026, 10, 23, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert w._round_age_min(None, now) is None


class TestLoopAlarm:
    """卡死告警：最新一轮太旧或读不到时推一条，同类告警有间隔上限。"""

    def _clock(self, *times):
        seq = iter(times)
        return lambda: next(seq)

    def test_alarm_when_round_is_stale(self, monkeypatch):
        monkeypatch.setattr(w, "is_trading_day", lambda d: True)
        monkeypatch.setattr(w, "run_round", lambda **kw: {
            "round_at": datetime(2026, 10, 23, 1, 20, tzinfo=ZoneInfo("UTC")),  # 北京 09:20
        })
        alarms: list[str] = []
        monkeypatch.setattr(w, "_alarm", alarms.append)
        w.loop(
            max_rounds=1, sleep=lambda s: None,
            now_fn=self._clock(datetime(2026, 10, 23, 9, 40, tzinfo=ZoneInfo("Asia/Shanghai"))),
        )
        assert len(alarms) == 1
        assert "盘中触发停机" in alarms[0] and "阈值 8 分钟" in alarms[0]
        assert "09:20" in alarms[0]

    def test_no_alarm_when_round_is_fresh(self, monkeypatch):
        monkeypatch.setattr(w, "is_trading_day", lambda d: True)
        monkeypatch.setattr(w, "run_round", lambda **kw: {
            "round_at": datetime(2026, 10, 23, 1, 38, tzinfo=ZoneInfo("UTC")),  # 北京 09:38
        })
        alarms: list[str] = []
        monkeypatch.setattr(w, "_alarm", alarms.append)
        w.loop(
            max_rounds=1, sleep=lambda s: None,
            now_fn=self._clock(datetime(2026, 10, 23, 9, 40, tzinfo=ZoneInfo("Asia/Shanghai"))),
        )
        assert alarms == []

    def test_alarm_when_round_missing(self, monkeypatch):
        monkeypatch.setattr(w, "is_trading_day", lambda d: True)
        monkeypatch.setattr(w, "run_round", lambda **kw: {"round_at": None})
        alarms: list[str] = []
        monkeypatch.setattr(w, "_alarm", alarms.append)
        w.loop(
            max_rounds=1, sleep=lambda s: None,
            now_fn=self._clock(datetime(2026, 10, 23, 9, 40, tzinfo=ZoneInfo("Asia/Shanghai"))),
        )
        assert len(alarms) == 1
        assert "读不到 CPT 封板表" in alarms[0]

    def test_alarm_throttled_within_gap(self, monkeypatch):
        monkeypatch.setattr(w, "is_trading_day", lambda d: True)
        monkeypatch.setattr(w, "run_round", lambda **kw: {"round_at": None})
        alarms: list[str] = []
        monkeypatch.setattr(w, "_alarm", alarms.append)
        tz = ZoneInfo("Asia/Shanghai")
        w.loop(
            max_rounds=3, sleep=lambda s: None,
            now_fn=self._clock(
                datetime(2026, 10, 23, 9, 40, tzinfo=tz),
                datetime(2026, 10, 23, 9, 45, tzinfo=tz),   # 5 分钟后：压住
                datetime(2026, 10, 23, 10, 15, tzinfo=tz),  # 35 分钟后：再报
            ),
        )
        assert len(alarms) == 2


class TestAlarmGrace:
    """早盘首轮宽限：09:35 之前「没有轮次」是正常的，不该告警。"""

    def _clock(self, *times):
        seq = iter(times)
        return lambda: next(seq)

    def test_no_alarm_before_grace(self, monkeypatch):
        monkeypatch.setattr(w, "is_trading_day", lambda d: True)
        monkeypatch.setattr(w, "run_round", lambda **kw: {"round_at": None, "triggers": 0, "written": 0})
        alarms: list[str] = []
        monkeypatch.setattr(w, "_alarm", alarms.append)
        w.loop(
            max_rounds=2, sleep=lambda s: None,
            now_fn=self._clock(datetime(2026, 10, 23, 9, 26, tzinfo=w._BJ),
                               datetime(2026, 10, 23, 9, 30, tzinfo=w._BJ)),
        )
        assert alarms == []

    def test_alarm_after_grace(self, monkeypatch):
        monkeypatch.setattr(w, "is_trading_day", lambda d: True)
        monkeypatch.setattr(w, "run_round", lambda **kw: {"round_at": None, "triggers": 0, "written": 0})
        alarms: list[str] = []
        monkeypatch.setattr(w, "_alarm", alarms.append)
        w.loop(
            max_rounds=1, sleep=lambda s: None,
            now_fn=self._clock(datetime(2026, 10, 23, 9, 36, tzinfo=w._BJ)),
        )
        assert len(alarms) == 1

    def test_alarm_result_is_logged(self, monkeypatch, caplog):
        monkeypatch.setattr(w, "is_trading_day", lambda d: True)
        monkeypatch.setattr(w, "run_round", lambda **kw: {"round_at": None, "triggers": 0, "written": 0})
        monkeypatch.setattr(w, "_alarm", lambda text: False)
        with caplog.at_level("WARNING"):
            w.loop(max_rounds=1, sleep=lambda s: None,
                   now_fn=self._clock(datetime(2026, 10, 23, 9, 40, tzinfo=w._BJ)))
        assert "卡死告警已触发" in caplog.text
        assert "推送未配置/失败" in caplog.text


class TestLoopSettle:
    """收盘自动结算：只在过了 SETTLE_AFTER 且当天没结过时跑一次。"""

    def test_settles_once_after_close(self, monkeypatch):
        monkeypatch.setattr(w, "is_trading_day", lambda d: True)
        calls: list[date] = []

        def fake_settle(d, *, dry_run=False):
            calls.append(d)
            return {"date": d, "triggers": 2, "settled": 2, "pending": 0}

        monkeypatch.setattr(w, "settle", fake_settle)
        alarms: list[str] = []
        monkeypatch.setattr(w, "_alarm", alarms.append)
        tz = ZoneInfo("Asia/Shanghai")
        times = iter([
            datetime(2026, 10, 23, 15, 30, tzinfo=tz),
            datetime(2026, 10, 23, 16, 0, tzinfo=tz),
        ])
        w.loop(max_rounds=2, sleep=lambda s: None, now_fn=lambda: next(times))
        assert calls == [date(2026, 10, 23)]
        assert len(alarms) == 1 and "已结算 2026-10-23" in alarms[0]

    def test_no_settle_before_close(self, monkeypatch):
        monkeypatch.setattr(w, "is_trading_day", lambda d: False)
        calls: list[date] = []
        monkeypatch.setattr(w, "settle", lambda d, **kw: calls.append(d))
        tz = ZoneInfo("Asia/Shanghai")
        w.loop(max_rounds=1, sleep=lambda s: None,
               now_fn=lambda: datetime(2026, 10, 23, 15, 0, tzinfo=tz))
        assert calls == []

    def test_no_settle_when_nothing_triggered(self, monkeypatch):
        monkeypatch.setattr(w, "is_trading_day", lambda d: False)
        monkeypatch.setattr(w, "settle",
                            lambda d, **kw: {"date": d, "triggers": 0, "settled": 0, "pending": 0})
        alarms: list[str] = []
        monkeypatch.setattr(w, "_alarm", alarms.append)
        tz = ZoneInfo("Asia/Shanghai")
        w.loop(max_rounds=1, sleep=lambda s: None,
               now_fn=lambda: datetime(2026, 10, 23, 16, 0, tzinfo=tz))
        assert alarms == []
if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
