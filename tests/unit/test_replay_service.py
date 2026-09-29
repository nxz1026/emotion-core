"""信号历史回填服务（services.replay_service）：run 遍历交易日，不连库。"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.domain.signal import SignalSource
from emotion_core.services import replay_service


class TestRun:
    def test_empty_range_returns_zero(self, monkeypatch):
        monkeypatch.setattr(replay_service, "trading_days",
                            lambda s, e: [])
        assert replay_service.run(date(2026, 1, 1), date(2026, 1, 10)) == 0

    def test_counts_signals(self, monkeypatch):
        days = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]

        def fake_check(d, s):
            return {"code": "000001"} if d == date(2026, 1, 6) else None

        monkeypatch.setattr(replay_service, "trading_days", lambda s, e: days)
        monkeypatch.setattr(replay_service.entry, "check_signal", fake_check)
        assert replay_service.run(date(2026, 1, 5), date(2026, 1, 7)) == 1

    def test_uses_replay_source(self, monkeypatch):
        days = [date(2026, 1, 5)]
        captured: dict = {}

        def fake_check(d, s):
            captured["source"] = s
            return None

        monkeypatch.setattr(replay_service, "trading_days", lambda s, e: days)
        monkeypatch.setattr(replay_service.entry, "check_signal", fake_check)
        replay_service.run(date(2026, 1, 5), date(2026, 1, 5))
        assert captured["source"] == SignalSource.REPLAY

    def test_logs_summary(self, monkeypatch, caplog):
        days = [date(2026, 1, 5), date(2026, 1, 6)]
        monkeypatch.setattr(replay_service, "trading_days", lambda s, e: days)
        monkeypatch.setattr(replay_service.entry, "check_signal",
                            lambda d, s: {"code": "000001"})
        with caplog.at_level(logging.INFO, logger="emotion_core.replay_service"):
            replay_service.run(date(2026, 1, 5), date(2026, 1, 6))
        assert any("2/2" in r.getMessage() for r in caplog.records)
