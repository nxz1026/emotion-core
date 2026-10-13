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
                            lambda d, a, m: {"rating": "FAVORABLE",
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
                            lambda d, a, m: {"rating": "UNFAVORABLE"})

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
                            lambda d, a, m: {"rating": "UNFAVORABLE"})
        monkeypatch.setattr(ecosystem_service, "_update_market_stat_ecosystem",
                            lambda *a: 1)

        with caplog.at_level(logging.INFO, logger="emotion_core.ecosystem_service"):
            ecosystem_service.run(date(2026, 9, 25))
        assert any("UNFAVORABLE" in r.getMessage() for r in caplog.records)


class TestRunMode:
    """daily 走 live（看不到未来），历史回填走 replay（B3 要看得见次日）。"""

    def _patch(self, monkeypatch, seen):
        monkeypatch.setattr(ecosystem_service.accelerate, "detect", lambda d: None)
        monkeypatch.setattr(ecosystem_service.dragon_env, "rate",
                            lambda d, a, m: seen.update(mode=m) or {"rating": "NEUTRAL"})
        monkeypatch.setattr(ecosystem_service, "_update_market_stat_ecosystem",
                            lambda *a: 1)

    def test_live_is_the_default(self, monkeypatch):
        seen: dict = {}
        self._patch(monkeypatch, seen)
        ecosystem_service.run(date(2026, 9, 25))
        assert seen["mode"] == "live"

    def test_mode_is_forwarded_to_rate(self, monkeypatch):
        seen: dict = {}
        self._patch(monkeypatch, seen)
        ecosystem_service.run(date(2024, 3, 8), "replay")
        assert seen["mode"] == "replay"


class TestRunRange:
    """区间回填：注入落库口 + CLI。"""

    def test_injects_loader_as_persist_fn(self, monkeypatch):
        seen: dict = {}

        def fake_run_range(start, end, *, persist_fn):
            seen.update(start=start, end=end, persist_fn=persist_fn)
            return 659

        monkeypatch.setattr(ecosystem_service.dragon_env, "run_range", fake_run_range)
        assert ecosystem_service.run_range(date(2024, 1, 2), date(2026, 10, 12)) == 659
        assert seen["start"] == date(2024, 1, 2)
        assert seen["end"] == date(2026, 10, 12)
        # 落库口必须与 loader 是同一个对象：algorithms 层自己不碰 data
        assert seen["persist_fn"] is ecosystem_service.update_market_stat_ecosystem

    def test_cli_prints_day_count(self, monkeypatch, capsys):
        monkeypatch.setattr(ecosystem_service, "run_range", lambda s, e: 659)
        rc = ecosystem_service.main(["--start", "2024-01-02", "--end", "2026-10-12"])
        assert rc == 0
        assert "659" in capsys.readouterr().out
