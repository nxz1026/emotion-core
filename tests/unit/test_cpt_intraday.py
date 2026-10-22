"""`cpt_intraday` provider 单测：表不存在不炸、缺数给 None。"""

from __future__ import annotations

import pytest

from emotion_core.data.providers import cpt_intraday as ci


class TestTableMissing:
    def test_returns_none_when_table_absent(self, monkeypatch):
        """表还没建是**正常状态**（CPT 采集器未上线），不是故障。"""
        import psycopg

        def boom(*a, **k):
            raise psycopg.errors.UndefinedTable("relation \"intraday_quote\" does not exist")

        monkeypatch.setattr(ci, "connect_ro", boom)
        assert ci.fetch_quotes_from_cpt(["600519"]) is None

    def test_other_error_returns_none(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(ci, "connect_ro", boom)
        assert ci.fetch_quotes_from_cpt() is None


class TestRows:
    def test_latest_frame_wins(self, monkeypatch):
        rows = [
            ("600519", "2026-10-21 10:00:00", 1300.0, 1301.0, 1299.0, 100.0, 1301.0, 50.0, "茅台"),
            ("600519", "2026-10-21 09:59:00", 1290.0, 1291.0, 1289.0, 200.0, 1291.0, 60.0, "茅台"),
        ]
        names = [d.name for d in _cols()]
        monkeypatch.setattr(ci, "connect_ro", _fake_conn(rows, names))
        out = ci.fetch_quotes_from_cpt(["600519"])
        assert out is not None
        assert out["600519"]["last"] == 1300.0

    def test_filter_by_codes(self, monkeypatch):
        rows = [
            ("600519", "2026-10-21 10:00:00", 1300.0, 1301.0, 1299.0, 100.0, 1301.0, 50.0, "茅台"),
            ("000002", "2026-10-21 10:00:00", 3.4, 3.5, 3.4, 10.0, 3.5, 20.0, "万科"),
        ]
        names = [d.name for d in _cols()]
        monkeypatch.setattr(ci, "connect_ro", _fake_conn(rows, names))
        out = ci.fetch_quotes_from_cpt(["000002"])
        assert out is not None
        assert set(out) == {"000002"}


class _Col:
    """psycopg `cur.description` 的一列替身（只要 `.name`）。"""

    def __init__(self, name):
        self.name = name


def _cols():
    return [_Col(name) for name in ci._COLUMNS]


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
    def factory():
        return _FakeConn(rows, names)

    return factory
