"""`lab.cpt_watch` 的单测：CPT 观察期闸门的判定逻辑。

纯逻辑 + 文件解析，不连库（`trading_days` 用假连接替身）。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from emotion_core.lab import cpt_watch as w

BJ = ZoneInfo("Asia/Shanghai")


def _round(day: str, outcome: str, hh: int = 10, mm: int = 0) -> w.Round:
    return w.Round(
        ts=datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}:00+08:00"),
        outcome=outcome,
    )


class TestParseLog:
    def test_parses_json_lines(self, tmp_path):
        p = tmp_path / "c.log"
        p.write_text(
            json.dumps({"at": "2026-10-22T10:00:00+08:00", "outcome": "ok"})
            + "\n"
            + json.dumps({"at": "2026-10-22T10:02:00+08:00", "outcome": "error", "error": "boom"})
            + "\n",
            encoding="utf-8",
        )
        rounds = w.parse_log(p)
        assert [r.outcome for r in rounds] == ["ok", "error"]
        assert rounds[1].detail == "boom"

    def test_skips_garbage_and_non_json(self, tmp_path):
        p = tmp_path / "c.log"
        p.write_text(
            "跑了 1 轮：ok 1 · empty 0 · error 0\n"
            "not json {\n"
            + json.dumps({"at": "2026-10-22T10:00:00+08:00", "outcome": "ok"})
            + "\n",
            encoding="utf-8",
        )
        rounds = w.parse_log(p)
        assert len(rounds) == 1

    def test_missing_file_returns_empty(self, tmp_path):
        assert w.parse_log(tmp_path / "nope.log") == []

    def test_naive_timestamp_treated_as_beijing(self, tmp_path):
        p = tmp_path / "c.log"
        p.write_text(json.dumps({"at": "2026-10-22T10:00:00", "outcome": "ok"}) + "\n", encoding="utf-8")
        r = w.parse_log(p)[0]
        assert r.ts.date() == date(2026, 10, 22)
        assert r.ts.utcoffset().total_seconds() == 8 * 3600


class TestDayStat:
    def test_clean_requires_enough_ok_rounds(self):
        assert w.DayStat(date(2026, 10, 22), ok=10, empty=0, error=0, skipped=0).is_clean()
        assert not w.DayStat(date(2026, 10, 22), ok=9, empty=0, error=0, skipped=0).is_clean()

    def test_any_error_makes_day_dirty(self):
        assert not w.DayStat(date(2026, 10, 22), ok=200, empty=0, error=1, skipped=0).is_clean()

    def test_skipped_does_not_veto_but_counts_toward_nothing(self):
        d = w.DayStat(date(2026, 10, 22), ok=12, empty=0, error=0, skipped=5)
        assert d.is_clean()
        assert d.rounds == 17

    def test_empty_is_not_failure(self):
        assert w.DayStat(date(2026, 10, 22), ok=10, empty=50, error=0, skipped=0).is_clean()

    def test_min_ok_rounds_is_parameterisable(self):
        d = w.DayStat(date(2026, 10, 22), ok=3, empty=0, error=0, skipped=0)
        assert not d.is_clean()
        assert d.is_clean(min_ok_rounds=3)


class TestGroupByDay:
    def test_groups_by_beijing_date(self):
        rounds = [
            _round("2026-10-22", "ok"),
            _round("2026-10-22", "ok"),
            _round("2026-10-22", "skipped"),
            _round("2026-10-21", "error"),
        ]
        g = w.group_by_day(rounds)
        assert g[date(2026, 10, 22)].ok == 2
        assert g[date(2026, 10, 22)].skipped == 1
        assert g[date(2026, 10, 21)].error == 1


class _FakeConn:
    """`trading_days` 的替身：只认预置的日期。"""

    def __init__(self, days):
        self.days = days


@pytest.fixture
def fake_db(monkeypatch):
    days = [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)]
    monkeypatch.setattr(w, "trading_days", lambda conn, s, e: days)
    return days


class TestEvaluate:
    def test_streak_counts_consecutive_clean_days_from_latest(self, monkeypatch, fake_db, tmp_path):
        log = tmp_path / "c.log"
        lines = []
        for day, ok, err in (
            ("2026-10-19", 20, 0),
            ("2026-10-20", 20, 0),
            ("2026-10-21", 20, 1),  # 脏
            ("2026-10-22", 20, 0),
        ):
            for i in range(ok):
                lines.append(json.dumps({"at": f"{day}T10:{i:02d}:00+08:00", "outcome": "ok"}))
            for i in range(err):
                lines.append(json.dumps({"at": f"{day}T11:{i:02d}:00+08:00", "outcome": "error"}))
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")

        monkeypatch.setattr(w, "dashboard_errors", lambda *a, **k: 0)
        res = w.evaluate(_FakeConn(None), now=datetime(2026, 10, 22, 16, 0, tzinfo=BJ), log_path=log)
        assert res.streak == 1
        assert not res.ready
        assert res.dashboard_errors == 0

    def test_today_excluded_before_close(self, monkeypatch, fake_db, tmp_path):
        log = tmp_path / "c.log"
        log.write_text(
            json.dumps({"at": "2026-10-22T10:00:00+08:00", "outcome": "ok"}) + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(w, "dashboard_errors", lambda *a, **k: 0)
        res = w.evaluate(_FakeConn(None), now=datetime(2026, 10, 22, 11, 0, tzinfo=BJ), log_path=log)
        assert res.pending_day == date(2026, 10, 22)
        assert date(2026, 10, 22) not in [d.day for d in res.days]

    def test_missing_log_is_not_clean(self, monkeypatch, fake_db, tmp_path):
        monkeypatch.setattr(w, "dashboard_errors", lambda *a, **k: 0)
        res = w.evaluate(_FakeConn(None), now=datetime(2026, 10, 22, 16, 0, tzinfo=BJ), log_path=tmp_path / "nope.log")
        assert res.log_missing is True
        assert res.streak == 0
        assert not res.ready

    def test_ready_when_five_clean_days(self, monkeypatch, fake_db, tmp_path):
        log = tmp_path / "c.log"
        lines = []
        for day in ("2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"):
            for i in range(12):
                lines.append(json.dumps({"at": f"{day}T10:{i:02d}:00+08:00", "outcome": "ok"}))
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        monkeypatch.setattr(w, "dashboard_errors", lambda *a, **k: 0)
        res = w.evaluate(
            _FakeConn(None),
            now=datetime(2026, 10, 22, 16, 0, tzinfo=BJ),
            log_path=log,
            min_ok_rounds=10,
        )
        assert res.streak == 4
        assert not res.ready
        res2 = w.evaluate(
            _FakeConn(None),
            now=datetime(2026, 10, 22, 16, 0, tzinfo=BJ),
            log_path=log,
            min_ok_rounds=10,
            required=4,
        )
        assert res2.ready is True

    def test_dashboard_error_none_is_not_zero(self, monkeypatch, fake_db, tmp_path):
        log = tmp_path / "c.log"
        log.write_text("", encoding="utf-8")
        monkeypatch.setattr(w, "dashboard_errors", lambda *a, **k: None)
        res = w.evaluate(_FakeConn(None), now=datetime(2026, 10, 22, 16, 0, tzinfo=BJ), log_path=log)
        assert res.dashboard_errors is None


class TestRenderText:
    def test_text_mentions_gate_and_not_ready(self, monkeypatch, fake_db, tmp_path):
        log = tmp_path / "c.log"
        log.write_text("", encoding="utf-8")
        monkeypatch.setattr(w, "dashboard_errors", lambda *a, **k: 0)
        res = w.evaluate(_FakeConn(None), now=datetime(2026, 10, 22, 16, 0, tzinfo=BJ), log_path=log)
        text = w.render_text(res)
        assert "观察期闸门" in text
        assert "还不能开工" in text
        assert "干净" in text
