"""持仓管理（services.position）：open/close/current，不连库。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from emotion_core.domain.position import Position
from emotion_core.services import position


class TestOpenPos:
    def test_inserts_and_returns_id(self, monkeypatch):
        captured: dict = {}

        def fake_query(sql, params=(), conn=None):
            captured["sql"] = sql
            captured["params"] = tuple(params)
            return pd.DataFrame({"id": [42]})

        monkeypatch.setattr(position, "query_df", fake_query)
        rid = position.open_pos("000001", date(2026, 9, 25), 10.5, 1000, "首次建仓")
        assert rid == 42
        assert "INSERT INTO position" in captured["sql"]
        assert captured["params"] == ("000001", date(2026, 9, 25), 10.5, 1000, "首次建仓")

    def test_default_note_empty(self, monkeypatch):
        captured: dict = {}

        def fake_query(sql, params=(), conn=None):
            captured["params"] = tuple(params)
            return pd.DataFrame({"id": [1]})

        monkeypatch.setattr(position, "query_df", fake_query)
        position.open_pos("000001", date(2026, 9, 25), 10.5, 1000)
        assert captured["params"][4] == ""


class TestClosePos:
    def test_updates_status_and_price(self, monkeypatch):
        captured: dict = {}

        def fake_execute(sql, params=(), conn=None):
            captured["sql"] = sql
            captured["params"] = tuple(params)

        monkeypatch.setattr(position, "execute", fake_execute)
        position.close_pos(42, date(2026, 9, 30), 11.2)
        assert "UPDATE position" in captured["sql"]
        assert "status='CLOSED'" in captured["sql"]
        assert captured["params"] == (date(2026, 9, 30), 11.2, 42)

    def test_only_updates_open(self, monkeypatch):
        captured: dict = {}

        def fake_execute(sql, params=(), conn=None):
            captured["sql"] = sql

        monkeypatch.setattr(position, "execute", fake_execute)
        position.close_pos(1, date(2026, 9, 30), 10.0)
        assert "status='OPEN'" in captured["sql"]  # WHERE 条件


class TestCurrent:
    def test_empty_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(position, "query_df",
                            lambda sql, params=(), conn=None: pd.DataFrame())
        assert position.current() == []

    def test_maps_rows_to_position_objects(self, monkeypatch):
        df = pd.DataFrame({
            "id": [1, 2],
            "code": ["000001", "600000"],
            "entry_date": [date(2026, 9, 20), date(2026, 9, 22)],
            "entry_price": [10.0, 20.0],
            "shares": [1000, 500],
            "status": ["OPEN", "OPEN"],
            "note": ["", "加仓"],
        })
        monkeypatch.setattr(position, "query_df",
                            lambda sql, params=(), conn=None: df)
        result = position.current()
        assert len(result) == 2
        assert all(isinstance(p, Position) for p in result)
        assert result[0] == Position(
            code="000001", entry_date=date(2026, 9, 20),
            entry_price=10.0, shares=1000, status="OPEN", note="", id=1)
        assert result[1] == Position(
            code="600000", entry_date=date(2026, 9, 22),
            entry_price=20.0, shares=500, status="OPEN", note="加仓", id=2)

    def test_note_none_becomes_empty_string(self, monkeypatch):
        df = pd.DataFrame({
            "id": [1],
            "code": ["000001"],
            "entry_date": [date(2026, 9, 20)],
            "entry_price": [10.0],
            "shares": [1000],
            "status": ["OPEN"],
            "note": [None],
        })
        monkeypatch.setattr(position, "query_df",
                            lambda sql, params=(), conn=None: df)
        result = position.current()
        assert result[0].note == ""

    def test_ordered_by_entry_date(self, monkeypatch):
        df = pd.DataFrame({
            "id": [1, 2],
            "code": ["600000", "000001"],
            "entry_date": [date(2026, 9, 25), date(2026, 9, 20)],
            "entry_price": [20.0, 10.0],
            "shares": [500, 1000],
            "status": ["OPEN", "OPEN"],
            "note": ["", ""],
        })
        monkeypatch.setattr(position, "query_df",
                            lambda sql, params=(), conn=None: df)
        result = position.current()
        # 返回顺序与 SQL ORDER BY entry_date 一致（600000 在前）
        assert result[0].code == "600000"
        assert result[1].code == "000001"
