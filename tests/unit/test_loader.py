"""data/loader.py 核心写入函数测试。

P1-F：data/loader.py 核心写入函数无测试 — replace_ladder_day/upsert_promotion_day。
桩掉 DB 连接（connect/transaction），验证：
- replace_ladder_day: DELETE + INSERT 同事务、空 rows → 仅 DELETE
- upsert_promotion_day: 列集与 promotion._INSERT_SQL 一致、空 rows → 0
- insert_signal: ON CONFLICT 用 (confirm_date, code, action)
- count_derived_rows: 返回值即上游完整性凭证
- update_market_stat_ecosystem: UPDATE 字段正确
- upsert_market_stat: INSERT ON CONFLICT DO UPDATE
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

import pytest

from emotion_core.data import loader
from emotion_core.domain.ladder import LadderDay
from emotion_core.domain.market import MarketStat, Phase
from emotion_core.domain.signal import Action, Checklist, Signal, SignalSource


# ── helpers ───────────────────────────────────────────────────────────

class FakeConn:
    """内存 DB 替身：记录 execute/executemany 调用，返回预设值。"""

    def __init__(self, *, fetchall_rows=None, fetchone_row=None, rowcount=1):
        self.execute_calls: list[tuple[str, tuple]] = []
        self.executemany_calls: list[tuple[str, list]] = []
        self._fetchall = fetchall_rows or []
        self._fetchone = fetchone_row
        self._rowcount = rowcount

    def execute(self, sql, params=()):
        self.execute_calls.append((sql, tuple(params) if params else ()))

        class _Result:
            def __init__(self, rc, fetchone_val):
                self.rowcount = rc
                self._fetchone = fetchone_val

            def fetchone(self):
                return self._fetchone

        return _Result(self._rowcount, self._fetchone)

    def fetchall(self):
        return self._fetchall

    def fetchone(self):
        return self._fetchone

    def cursor(self):
        return FakeCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class FakeCursor:
    """cursor 替身：记录 executemany 调用。"""

    def __init__(self, conn: FakeConn):
        self._conn = conn

    def executemany(self, sql, data):
        self._conn.executemany_calls.append((sql, data))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def _patch_db(conn: FakeConn):
    """返回 patch 上下文：connect 与 transaction 都指向 conn。"""

    @contextmanager
    def fake_connect():
        yield conn

    @contextmanager
    def fake_transaction():
        yield conn

    return (
        patch("emotion_core.data.loader.connect", fake_connect),
        patch("emotion_core.data.loader.transaction", fake_transaction),
    )


# ── 1. replace_ladder_day ─────────────────────────────────────────────

class TestReplaceLadderDay:
    """replace_ladder_day: DELETE + INSERT 同事务，空 rows → 仅 DELETE。"""

    def test_deletes_then_inserts(self):
        conn = FakeConn()
        patches = _patch_db(conn)
        for p in patches:
            p.start()
        try:
            rows = [
                LadderDay(date=date(2026, 9, 29), code="600519", cont_days=3,
                          is_exchange=False, is_top=True, is_sole_top=False,
                          y_top_group_count=5, y_top_survivor_count=3),
            ]
            n = loader.replace_ladder_day(date(2026, 9, 29), rows)

            assert n == 1
            assert any("DELETE FROM ladder_day" in sql for sql, _ in conn.execute_calls)
            assert len(conn.executemany_calls) == 1
            assert "INSERT INTO ladder_day" in conn.executemany_calls[0][0]
        finally:
            for p in patches:
                p.stop()

    def test_empty_rows_only_deletes(self):
        conn = FakeConn()
        patches = _patch_db(conn)
        for p in patches:
            p.start()
        try:
            n = loader.replace_ladder_day(date(2026, 9, 29), [])

            assert n == 0
            assert any("DELETE FROM ladder_day" in sql for sql, _ in conn.execute_calls)
            assert len(conn.executemany_calls) == 0
        finally:
            for p in patches:
                p.stop()

    def test_insert_uses_on_conflict_update(self):
        conn = FakeConn()
        patches = _patch_db(conn)
        for p in patches:
            p.start()
        try:
            rows = [
                LadderDay(date=date(2026, 9, 29), code="000001", cont_days=2,
                          is_exchange=True, is_top=False, is_sole_top=False,
                          y_top_group_count=0, y_top_survivor_count=0),
            ]
            loader.replace_ladder_day(date(2026, 9, 29), rows)

            sql = conn.executemany_calls[0][0]
            assert "ON CONFLICT (date, code)" in sql
            assert "DO UPDATE SET" in sql
        finally:
            for p in patches:
                p.stop()


# ── 2. upsert_promotion_day ───────────────────────────────────────────

class TestUpsertPromotionDay:
    """upsert_promotion_day: 列集与 promotion._INSERT_SQL 一致。"""

    def test_inserts_with_correct_columns(self):
        conn = FakeConn()
        patches = _patch_db(conn)
        for p in patches:
            p.start()
        try:
            rows = [
                {"date": date(2026, 9, 29), "layer": 1, "promote_from": "A",
                 "promote_nominal": 10, "promote_exchange": 5,
                 "rate_nominal": 0.5, "rate_exchange": 0.25,
                 "divergence": 0.1, "fail_perf": 0.0},
            ]
            n = loader.upsert_promotion_day(rows)

            assert n == 1
            assert len(conn.executemany_calls) == 1
            sql = conn.executemany_calls[0][0]
            assert "promote_from" in sql
            assert "ON CONFLICT (date, layer)" in sql
        finally:
            for p in patches:
                p.stop()

    def test_empty_rows_returns_zero(self):
        conn = FakeConn()
        patches = _patch_db(conn)
        for p in patches:
            p.start()
        try:
            n = loader.upsert_promotion_day([])
            assert n == 0
        finally:
            for p in patches:
                p.stop()

    def test_deletes_existing_date_before_insert(self):
        conn = FakeConn()
        patches = _patch_db(conn)
        for p in patches:
            p.start()
        try:
            rows = [
                {"date": date(2026, 9, 29), "layer": 1, "promote_from": "A",
                 "promote_nominal": 10, "promote_exchange": 5,
                 "rate_nominal": None, "rate_exchange": None,
                 "divergence": None, "fail_perf": None},
            ]
            loader.upsert_promotion_day(rows)

            delete_calls = [sql for sql, _ in conn.execute_calls if "DELETE FROM promotion_day" in sql]
            assert len(delete_calls) == 1
        finally:
            for p in patches:
                p.stop()


# ── 3. insert_signal ──────────────────────────────────────────────────

class TestInsertSignal:
    """insert_signal: ON CONFLICT 用 (confirm_date, code, action)。"""

    def _make_signal(self, action=Action.BUY, source=SignalSource.LIVE):
        return Signal(
            code="600519", date=date(2026, 9, 29),
            action=action,
            checklist=Checklist(c1_uniqueness=None, c2_exchange=None,
                                c3_elimination=None, c4_min_days=None,
                                c5_strength_diverge=None, w1_crowding=None),
            source=source,
        )

    def test_uses_correct_unique_constraint(self):
        conn = FakeConn(rowcount=1)
        patches = _patch_db(conn)
        for p in patches:
            p.start()
        try:
            rc = loader.insert_signal(self._make_signal())

            assert rc == 1
            conflict_calls = [sql for sql, _ in conn.execute_calls if "ON CONFLICT" in sql]
            assert len(conflict_calls) == 1
            assert "(confirm_date, code, action)" in conflict_calls[0]
        finally:
            for p in patches:
                p.stop()

    def test_includes_config_hash(self):
        conn = FakeConn(rowcount=1)
        patches = _patch_db(conn)
        for p in patches:
            p.start()
        try:
            loader.insert_signal(self._make_signal())

            sql = conn.execute_calls[0][0]
            assert "config_hash" in sql
        finally:
            for p in patches:
                p.stop()


# ── 4. count_derived_rows ─────────────────────────────────────────────

class TestCountDerivedRows:
    """count_derived_rows: 返回值即上游完整性凭证。"""

    def test_returns_count(self):
        conn = FakeConn(fetchone_row=(42,))
        patches = _patch_db(conn)
        for p in patches:
            p.start()
        try:
            n = loader.count_derived_rows(date(2026, 9, 29))
            assert n == 42
        finally:
            for p in patches:
                p.stop()

    def test_zero_means_missing_data(self):
        conn = FakeConn(fetchone_row=(0,))
        patches = _patch_db(conn)
        for p in patches:
            p.start()
        try:
            n = loader.count_derived_rows(date(2026, 9, 29))
            assert n == 0
        finally:
            for p in patches:
                p.stop()


# ── 5. update_market_stat_ecosystem ───────────────────────────────────

class TestUpdateMarketStatEcosystem:
    """update_market_stat_ecosystem: UPDATE 字段正确。"""

    def test_updates_ecosystem_fields(self):
        conn = FakeConn(rowcount=1)
        patches = _patch_db(conn)
        for p in patches:
            p.start()
        try:
            rc = loader.update_market_stat_ecosystem(
                date(2026, 9, 29), "bull", "reason text", "risk text"
            )
            assert rc == 1
            sql = conn.execute_calls[0][0]
            assert "UPDATE market_stat" in sql
            assert "dragon_env=" in sql
            assert "dragon_env_reasons=" in sql
            assert "dragon_env_risks=" in sql
        finally:
            for p in patches:
                p.stop()


# ── 6. upsert_market_stat ─────────────────────────────────────────────

class TestUpsertMarketStat:
    """upsert_market_stat: INSERT ON CONFLICT (date) DO UPDATE。"""

    def test_insert_with_all_fields(self):
        conn = FakeConn(rowcount=1)
        patches = _patch_db(conn)
        for p in patches:
            p.start()
        try:
            stat = MarketStat(
                date=date(2026, 9, 29), phase=Phase.ICE,
                buy_window=1, limit_up_count=20, bomb_count=3,
                limit_down_count=5, max_height=3,
                force_liquidate=False, bomb_threshold=0.1, has_candidate=True,
            )
            rc = loader.upsert_market_stat(stat)

            assert rc == 1
            sql = conn.execute_calls[0][0]
            assert "INSERT INTO market_stat" in sql
            assert "ON CONFLICT (date)" in sql
            assert "DO UPDATE SET" in sql
        finally:
            for p in patches:
                p.stop()
