"""生态评级服务（services.ecosystem_service）：run 封装，不连库。"""
from __future__ import annotations

import logging
from datetime import date
from types import SimpleNamespace

from emotion_core.services import ecosystem_service


class TestUpdateMarketStatEcosystem:
    def test_delegates_to_loader(self, monkeypatch):
        captured: dict = {}

        def fake_update(d, rating, reasons, risks):
            captured.update(date=d, rating=rating, reasons=reasons, risks=risks)
            return 1

        monkeypatch.setattr(ecosystem_service, "_update_market_stat_ecosystem", fake_update)
        n = ecosystem_service.update_market_stat_ecosystem(
            date(2026, 9, 25), "NEUTRAL", "[]", "[]")
        assert n == 1
        assert captured["date"] == date(2026, 9, 25)
        assert captured["rating"] == "NEUTRAL"


class TestRun:
    def test_calls_rate_and_writes_to_db(self, monkeypatch):
        captured: dict = {}

        monkeypatch.setattr(ecosystem_service.accelerate, "detect",
                            lambda d: (True, "加速", 0.5))
        monkeypatch.setattr(ecosystem_service.dragon_env, "rate",
                            lambda d, a: {"rating": "FAVORABLE",
                                         "goods": [], "bads": []})

        class FakeConn:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params):
                self.calls.append((sql, params))

        fake_conn = FakeConn()

        class FakeTransaction:
            def __enter__(self):
                return fake_conn

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(ecosystem_service, "transaction",
                            lambda: FakeTransaction())
        rating = ecosystem_service.run(date(2026, 9, 25))
        assert rating == "FAVORABLE"
        assert any("UPDATE market_stat" in sql for sql, _ in fake_conn.calls)
        assert any("FAVORABLE" in params for _, params in fake_conn.calls)

    def test_logs_result(self, monkeypatch, caplog):
        monkeypatch.setattr(ecosystem_service.accelerate, "detect",
                            lambda d: None)
        monkeypatch.setattr(ecosystem_service.dragon_env, "rate",
                            lambda d, a: {"rating": "UNFAVORABLE"})

        class FakeTransaction:
            def __enter__(self):
                return SimpleNamespace(execute=lambda *a: None)

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(ecosystem_service, "transaction",
                            lambda: FakeTransaction())
        with caplog.at_level(logging.INFO, logger="emotion_core.ecosystem_service"):
            ecosystem_service.run(date(2026, 9, 25))
        assert any("UNFAVORABLE" in r.getMessage() for r in caplog.records)
