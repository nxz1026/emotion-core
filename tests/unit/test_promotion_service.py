"""晋级率服务（services.promotion_service）：run 封装，不连库。"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.services import promotion_service


class TestRun:
    def test_calls_persist_and_returns_layers(self, monkeypatch):
        captured: dict = {}

        def fake_persist(d):
            captured["date"] = d
            return 5

        monkeypatch.setattr(promotion_service.promotion, "persist", fake_persist)
        n = promotion_service.run(date(2026, 9, 25))
        assert n == 5
        assert captured["date"] == date(2026, 9, 25)

    def test_logs_info(self, monkeypatch, caplog):
        monkeypatch.setattr(promotion_service.promotion, "persist", lambda d: 3)
        with caplog.at_level(logging.INFO, logger="emotion_core.promotion_service"):
            promotion_service.run(date(2026, 9, 25))
        assert any("3 层" in r.getMessage() for r in caplog.records)

    def test_zero_layers(self, monkeypatch):
        monkeypatch.setattr(promotion_service.promotion, "persist", lambda d: 0)
        assert promotion_service.run(date(2026, 9, 25)) == 0
