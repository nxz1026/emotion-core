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


class TestR58_4BuyWebhookHook:
    """R58-4：BUY 信号触发 push_buy_signal；SECONDARY/RECOMMEND 不触发。"""

    def _make_sig(self, action_value: str):
        from emotion_core.domain.signal import Action
        act = Action(action_value)
        cl = SimpleNamespace(c1_uniqueness=True, c2_exchange=True,
                             c3_elimination=True, c4_min_days=True,
                             c5_strength_diverge=True, w1_crowding=True)
        return SimpleNamespace(action=act, code="000017", date=date(2026, 9, 25),
                               checklist=cl, name="测试龙", cont_days=5)

    def test_buy_signal_triggers_push(self, monkeypatch):
        """R58-4：action=BUY → notify.push_buy_signal 被调一次。"""
        sig = self._make_sig("BUY")
        monkeypatch.setattr(signal_service.entry, "check_signal", lambda d, s: sig)
        captured: dict = {}
        monkeypatch.setattr(signal_service.notify, "push_buy_signal",
                            lambda **kw: captured.update(kw) or True)
        signal_service.run(date(2026, 9, 25))
        assert captured.get("code") == "000017"
        assert captured.get("cont_days") == 5
        assert captured.get("name") == "测试龙"

    def test_secondary_signal_does_not_trigger_push(self, monkeypatch):
        """R58-4：action=SECONDARY → 不调 push_buy_signal。"""
        sig = self._make_sig("SECONDARY")
        monkeypatch.setattr(signal_service.entry, "check_signal", lambda d, s: sig)
        called: list = []
        monkeypatch.setattr(signal_service.notify, "push_buy_signal",
                            lambda **kw: called.append(kw) or True)
        signal_service.run(date(2026, 9, 25))
        assert called == []

    def test_recommend_signal_does_not_trigger_push(self, monkeypatch):
        """R58-4：action=RECOMMEND → 不调 push_buy_signal（仅 BUY 给独立时点）。"""
        sig = self._make_sig("RECOMMEND")
        monkeypatch.setattr(signal_service.entry, "check_signal", lambda d, s: sig)
        called: list = []
        monkeypatch.setattr(signal_service.notify, "push_buy_signal",
                            lambda **kw: called.append(kw) or True)
        signal_service.run(date(2026, 9, 25))
        assert called == []

    def test_push_failure_does_not_break_run(self, monkeypatch):
        """R58-4：push_buy_signal 抛异常 → run 仍正常返回 sig。"""
        sig = self._make_sig("BUY")
        monkeypatch.setattr(signal_service.entry, "check_signal", lambda d, s: sig)

        def boom(**kw):
            raise RuntimeError("webhook 下游挂了")
        monkeypatch.setattr(signal_service.notify, "push_buy_signal", boom)
        # 不应抛
        result = signal_service.run(date(2026, 9, 25))
        assert result is sig

    def test_no_signal_does_not_trigger_push(self, monkeypatch):
        monkeypatch.setattr(signal_service.entry, "check_signal", lambda d, s: None)
        called: list = []
        monkeypatch.setattr(signal_service.notify, "push_buy_signal",
                            lambda **kw: called.append(kw) or True)
        signal_service.run(date(2026, 9, 25))
        assert called == []
