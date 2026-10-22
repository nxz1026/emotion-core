"""`services.intraday_watch` 与 `loader` 的盘中台账 I/O 单测（ADR 0003 §D6）。

结算口径逐条钉住：
- 卖点 T+1 开盘；T+1 跌停封死顺延 T+2（`rolled=True`）；两天都封死 → 不结算（留 NULL）
- `closed_sealed` / `broke` 直接取 `derived_bar.is_limit_up` / `is_bomb`
- `ret_pct` 扣双边费 `FEE_PCT`；缺数一律不猜
"""

from __future__ import annotations

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
