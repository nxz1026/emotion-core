"""生态评级服务（services.ecosystem_service）：run 封装，不连库。"""
from __future__ import annotations

import json
import logging
from datetime import date

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
    def test_writes_rating_reasons_and_risks(self, monkeypatch):
        """回归：原实现只写 dragon_env，把 rate() 的 goods/bads 丢掉，
        dragon_env_reasons / dragon_env_risks 恒为 NULL。"""
        captured: dict = {}

        monkeypatch.setattr(ecosystem_service.accelerate, "detect",
                            lambda d: (True, "加速", 0.5))
        monkeypatch.setattr(ecosystem_service.dragon_env, "rate",
                            lambda d, a: {"rating": "FAVORABLE",
                                          "goods": [("G1 可交易高度扩张", True, "说明")],
                                          "bads": [("B1 无量", False, "无成交")]})

        def fake_update(d, rating, reasons, risks):
            captured.update(date=d, rating=rating, reasons=reasons, risks=risks)
            return 1

        monkeypatch.setattr(ecosystem_service, "_update_market_stat_ecosystem", fake_update)
        rating = ecosystem_service.run(date(2026, 9, 25))
        assert rating == "FAVORABLE"
        assert captured["date"] == date(2026, 9, 25)
        assert captured["rating"] == "FAVORABLE"
        assert json.loads(captured["reasons"]) == [
            {"cond": "G1 可交易高度扩张", "ok": True, "note": "说明"}]
        assert json.loads(captured["risks"]) == [
            {"cond": "B1 无量", "ok": False, "note": "无成交"}]

    def test_missing_goods_bads_does_not_raise(self, monkeypatch):
        """rate() 只回 rating（缺 goods/bads）时照样落库，不 KeyError。"""
        captured: dict = {}

        monkeypatch.setattr(ecosystem_service.accelerate, "detect", lambda d: None)
        monkeypatch.setattr(ecosystem_service.dragon_env, "rate",
                            lambda d, a: {"rating": "UNFAVORABLE"})

        def fake_update(d, rating, reasons, risks):
            captured.update(rating=rating, reasons=reasons, risks=risks)
            return 1

        monkeypatch.setattr(ecosystem_service, "_update_market_stat_ecosystem", fake_update)
        assert ecosystem_service.run(date(2026, 9, 25)) == "UNFAVORABLE"
        assert json.loads(captured["reasons"]) == []
        assert json.loads(captured["risks"]) == []

    def test_logs_result(self, monkeypatch, caplog):
        monkeypatch.setattr(ecosystem_service.accelerate, "detect",
                            lambda d: None)
        monkeypatch.setattr(ecosystem_service.dragon_env, "rate",
                            lambda d, a: {"rating": "UNFAVORABLE"})
        monkeypatch.setattr(ecosystem_service, "_update_market_stat_ecosystem",
                            lambda *a: 1)

        with caplog.at_level(logging.INFO, logger="emotion_core.ecosystem_service"):
            ecosystem_service.run(date(2026, 9, 25))
        assert any("UNFAVORABLE" in r.getMessage() for r in caplog.records)
