"""`cpt_intraday` provider 单测：读 CPT 的封板结论表（ADR 0003 §H）。

表是 `public.intraday_seal_snapshot`（5 列：at / code / sealed / bid1_lots /
limit_price），不是 §G 里的 13 列 `intraday_quote`。
"""

from __future__ import annotations

import pytest

from emotion_core.data.providers import cpt_intraday as ci


class _Col:
    """psycopg `cur.description` 的一列替身（只要 `.name`）。"""

    def __init__(self, name):
        self.name = name


class _FakeCur:
    def __init__(self, rows, names):
        self._rows = rows
        self.description = [_Col(n) for n in names]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        return None

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows, names):
        self._rows = rows
        self._names = names

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCur(self._rows, self._names)


def _fake_conn(rows, names):
    def factory(*a, **k):
        return _FakeConn(rows, names)

    return factory


class TestTableMissing:
    def test_returns_none_when_table_absent(self, monkeypatch):
        """表还没建是**正常状态**，不是故障。"""
        import psycopg

        def boom(*a, **k):
            raise psycopg.errors.UndefinedTable(
                'relation "intraday_seal_snapshot" does not exist'
            )

        monkeypatch.setattr(ci, "connect_ro", boom)
        assert ci.fetch_quotes_from_cpt(["600519"]) is None
        assert ci.fetch_round_from_cpt(["600519"]) is None

    def test_other_error_returns_none(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(ci, "connect_ro", boom)
        assert ci.fetch_quotes_from_cpt() is None


class TestRows:
    def test_empty_table_returns_empty_dict(self, monkeypatch):
        """表在、但一轮都没有 → `{}`，不是 None（区分「没建表」与「还没采」）。"""
        monkeypatch.setattr(ci, "connect_ro", _fake_conn([], list(ci._COLUMNS)))
        assert ci.fetch_quotes_from_cpt() == {}

    def test_records_and_round_at(self, monkeypatch):
        at = "2026-10-22 09:20:36+00:00"
        rows = [
            (at, "600293", True, 847522.54, 10.19),
            (at, "002724", False, None, 10.19),
        ]
        monkeypatch.setattr(ci, "connect_ro", _fake_conn(rows, list(ci._COLUMNS)))
        got = ci.fetch_round_from_cpt()
        assert got is not None
        round_at, out = got
        assert round_at == at
        assert set(out) == {"600293", "002724"}
        assert out["600293"]["sealed"] is True
        # 单位是**手**，不是股 —— 用的时候别直接拿去比 SEAL_BID1_VOL_MIN（股）
        assert out["600293"]["bid1_lots"] == 847522.54
        assert out["002724"]["bid1_lots"] is None

    def test_filter_by_codes(self, monkeypatch):
        at = "2026-10-22 09:20:36+00:00"
        rows = [
            (at, "600293", True, 100.0, 10.19),
            (at, "000002", False, 5.0, 3.51),
        ]
        monkeypatch.setattr(ci, "connect_ro", _fake_conn(rows, list(ci._COLUMNS)))
        out = ci.fetch_quotes_from_cpt(["000002"])
        assert out is not None
        assert set(out) == {"000002"}


def test_select_sql_only_takes_latest_round():
    """SQL 必须按 `at = max(at)` 取整轮 —— 不能按 code 单点查最新（§H.3 第 3 条）。"""
    sql = " ".join(ci._SELECT_SQL.split()).lower()
    assert "at = (select max(at)" in sql
    assert "order by code" in sql


def test_columns_match_h2_ddl():
    """列清单必须与 ADR §H.2 的表结构逐字对齐。"""
    assert ci._COLUMNS == ("at", "code", "sealed", "bid1_lots", "limit_price")
    assert ci.CPT_INTRADAY_TABLE == "intraday_seal_snapshot"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
