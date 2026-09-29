"""执行层反馈（services.execution）：feedback 回执，不连库。"""
from __future__ import annotations

from datetime import date

from emotion_core.services import execution


class TestFeedback:
    def test_adopted_uses_correct_sql(self, monkeypatch):
        captured: dict = {}

        def fake_execute(sql, params=(), conn=None):
            captured["sql"] = sql
            captured["params"] = tuple(params)
            return 1

        monkeypatch.setattr(execution, "execute", fake_execute)
        n = execution.feedback(date(2026, 9, 25), "000001", "BUY",
                               "ADOPTED", 10.5)
        assert n == 1
        assert "UPDATE signal" in captured["sql"]
        assert captured["params"][0] == "ADOPTED"
        assert captured["params"][1] == 10.5  # feedback_price

    def test_dropped_without_price(self, monkeypatch):
        captured: dict = {}

        def fake_execute(sql, params=(), conn=None):
            captured["params"] = tuple(params)
            return 1

        monkeypatch.setattr(execution, "execute", fake_execute)
        execution.feedback(date(2026, 9, 25), "600000", "BUY",
                           "DROPPED")
        assert captured["params"] == ("DROPPED", None, date(2026, 9, 25),
                                      date(2026, 9, 25), "600000", "BUY")

    def test_invalid_status_raises(self, monkeypatch):
        monkeypatch.setattr(execution, "execute", lambda *a, **k: 1)
        try:
            execution.feedback(date(2026, 9, 25), "000001", "BUY", "PENDING")
        except ValueError as e:
            assert "ADOPTED/DROPPED" in str(e)
        else:
            raise AssertionError("应抛 ValueError")

    def test_returns_affected_rows(self, monkeypatch):
        monkeypatch.setattr(execution, "execute", lambda *a, **k: 0)
        assert execution.feedback(date(2026, 9, 25), "000001", "BUY", "ADOPTED") == 0
