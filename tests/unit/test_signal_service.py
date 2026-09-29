"""买入信号服务（services.signal_service）：run 转发，不连库。"""
from __future__ import annotations

import logging
from datetime import date
from types import SimpleNamespace

from emotion_core.domain.signal import SignalSource
from emotion_core.services import signal_service


class TestRun:
    def test_returns_signal_when_present(self, monkeypatch):
        fake_signal = SimpleNamespace(action=SimpleNamespace(value="BUY"), code="000001")
        monkeypatch.setattr(signal_service.entry, "check_signal",
                            lambda d, s: fake_signal)
        result = signal_service.run(date(2026, 9, 25))
        assert result is fake_signal

    def test_returns_none_when_no_signal(self, monkeypatch):
        monkeypatch.setattr(signal_service.entry, "check_signal",
                            lambda d, s: None)
        assert signal_service.run(date(2026, 9, 25)) is None

    def test_default_source_is_live(self, monkeypatch):
        captured: dict = {}

        def fake_check(d, s):
            captured["source"] = s
            return None

        monkeypatch.setattr(signal_service.entry, "check_signal", fake_check)
        signal_service.run(date(2026, 9, 25))
        assert captured["source"] == SignalSource.LIVE

    def test_replay_source(self, monkeypatch):
        captured: dict = {}

        def fake_check(d, s):
            captured["source"] = s
            return None

        monkeypatch.setattr(signal_service.entry, "check_signal", fake_check)
        signal_service.run(date(2026, 9, 25), SignalSource.REPLAY)
        assert captured["source"] == SignalSource.REPLAY

    def test_logs_signal_found(self, monkeypatch, caplog):
        fake_signal = SimpleNamespace(action=SimpleNamespace(value="BUY"), code="000001")
        monkeypatch.setattr(signal_service.entry, "check_signal",
                            lambda d, s: fake_signal)
        with caplog.at_level(logging.INFO, logger="emotion_core.signal_service"):
            signal_service.run(date(2026, 9, 25))
        assert any("BUY 000001" in r.getMessage() for r in caplog.records)

    def test_logs_no_signal(self, monkeypatch, caplog):
        monkeypatch.setattr(signal_service.entry, "check_signal",
                            lambda d, s: None)
        with caplog.at_level(logging.INFO, logger="emotion_core.signal_service"):
            signal_service.run(date(2026, 9, 25))
        assert any("无信号" in r.getMessage() for r in caplog.records)
