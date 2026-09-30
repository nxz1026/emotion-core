"""个股诊断服务层：只读组装 + LLM 可选降级（全程 monkeypatch，不连库、不出网）。

守的行为：
- 主接口**不带** LLM（规则层结论必须秒回，AI 解读走异步 `/api/stock/llm`）；
- LLM 未启用/密钥缺失/调用异常 → 规则层结论照常返回，错误如实上报，不抛异常；
- 昂贵的全市场统计按目标日 + TTL 缓存，不重复聚合；
- 非法输入/无数据 → ok=False + 可读原因，不 500。
"""
from __future__ import annotations

import dataclasses
import types
from datetime import date

import pandas as pd
import pytest

from emotion_core.services import stock_service as svc
from emotion_core.utils.config import CONFIG

D = date(2026, 9, 24)


@pytest.fixture(autouse=True)
def _clear_cache():
    svc.clear_cache()
    yield
    svc.clear_cache()


@pytest.fixture(autouse=True)
def _db_free_diagnose(monkeypatch):
    """三段式诊断段（services/diagnose_service）自持 query_df：单测一律空表。

    与仓库基线（单测 DB-free）一致：analyze 新增的 diagnose 段只走降级分支。
    """
    from emotion_core.services import diagnose_service
    monkeypatch.setattr(diagnose_service, "query_df",
                        lambda sql, params=(), conn=None: pd.DataFrame())


def _fake_data(monkeypatch, *, calls=None, promo=None):
    """给 service 装一套假的数据层 + 假 series。"""
    series = [{"date": D, "close": 11.0, "high": 11.0, "low": 10.0, "open": 10.0,
               "pre_close": 10.0, "volume": 300.0, "amount": 1.0,
               "turnover_rate": 3.0, "pct_chg": 10.0, "is_limit_up": True,
               "is_limit_down": False, "is_one_word": False, "is_exchange": True,
               "is_bomb": False, "touched_limit": True, "cont_days": 2,
               "amplitude": 9.0}] * 30
    calls = calls if calls is not None else []

    def count(name):
        calls.append(name)

    monkeypatch.setattr(svc.q, "latest_trade_date", lambda: D)
    monkeypatch.setattr(svc.q, "resolve_code",
                        lambda text, limit=10: ([{"code": "601811", "name": "新华文轩"}]
                                                if "60" in text or "新华" in text else []))
    monkeypatch.setattr(svc.q, "basic_row", lambda code: {
        "code": code, "name": "新华文轩", "is_st": False, "industry": None,
        "market_cap": None, "list_date": None, "first_bar_date": date(2024, 1, 2),
        "in_market": True})
    monkeypatch.setattr(svc.q, "recent_series", lambda c, e, n: series)
    monkeypatch.setattr(svc.q, "structure_window", lambda c, e, n: {
        "limit_up_n": 2, "bomb_n": 0, "one_word_n": 0, "exchange_n": 2,
        "limit_down_n": 0, "max_cont": 2, "sample_n": 60})
    monkeypatch.setattr(svc.q, "market_env", lambda d: {
        "phase": "发酵", "buy_window": "STANDARD", "limit_up_count": 40,
        "limit_down_count": 5, "max_limit_days": 4, "bomb_rate": 0.2,
        "oneword_ratio": 0.1, "force_liquidate": False})
    monkeypatch.setattr(svc.q, "market_top", lambda d: [])
    monkeypatch.setattr(svc.q, "themes_of", lambda c, d: [])
    promo = promo if promo is not None else [
        {"layer": "1->2", "total": 100, "promoted": 20, "rate": 20.0,
         "rate_exchange": 15.0, "divergence": 5.0, "perf_median": 1.1,
         "win_rate": 55.0, "perf_n": 100, "fail_perf": -2.0}]

    def promo_table(end, min_denom=None, pair_date=None):
        count("promotion_table")
        return promo

    monkeypatch.setattr(svc.q, "promotion_table", promo_table)
    monkeypatch.setattr(svc.q, "layer_forward",
                        lambda e, layer, h, sample_cap=600: (
                            count("layer_forward") or
                            {"layer": layer, "horizon": h, "n": 300,
                             "median": 1.0, "win_rate": 52.0, "avg": 1.5}))
    return calls


def test_analyze_main_path_has_no_llm_and_rich_payload(monkeypatch):
    _fake_data(monkeypatch)
    out = svc.analyze("601811")
    assert out["ok"] and out["code"] == "601811"
    assert out["llm"]["enabled"] is False          # 主接口不调 LLM
    for key in ("basic", "trend", "structure", "market_env", "market_top",
                "themes", "promotion_table", "series", "verdict", "trade_date",
                "buy_point"):
        assert key in out, key
    assert out["verdict"]["disclaimer"].startswith("统计参考")
    assert len(out["promotion_table"]) == 1
    assert isinstance(out["trade_date"], date)


def test_analyze_payload_gains_diagnose_segment(monkeypatch):
    """新增 `diagnose` 段（A/B/C）——既有键不动，统计从本服务已算好的缓存注入。"""
    _fake_data(monkeypatch)
    out = svc.analyze("601811")
    legacy = {"ok", "code", "trade_date", "basic", "is_new_issuer", "trend", "structure",
              "market_env", "market_top", "themes", "promotion_table", "series",
              "verdict", "buy_point", "candidates", "llm"}
    assert legacy <= set(out)
    dg = out["diagnose"]
    assert set(dg) >= {"A", "B", "C"}
    assert dg["A"]["applicable"] is False            # 空表 → 非候选，不编造 PASS/FAIL
    assert dg["A"]["conditions"] == []
    assert dg["C"]["layer"] == out["verdict"]["layer"]
    assert dg["C"]["forward5"]["n"] == 300           # 同层前瞻统计已注入（未重复聚合）


def test_analyze_uses_target_date_and_is_readonly(monkeypatch):
    writes: list[str] = []
    _fake_data(monkeypatch)
    for name in ("insert", "execute", "upsert", "delete", "update"):
        monkeypatch.setattr(svc.q, name, lambda *a, _n=name, **k: writes.append(_n),
                            raising=False)
    assert svc.analyze("601811")["ok"] is True
    assert writes == []


def test_analyze_empty_and_unknown_input(monkeypatch):
    _fake_data(monkeypatch)
    assert svc.analyze("")["ok"] is False
    out = svc.analyze("ZZZ999")
    assert out["ok"] is False and "未找到" in out["error"]


def test_analyze_no_data_in_db(monkeypatch):
    _fake_data(monkeypatch)
    monkeypatch.setattr(svc.q, "latest_trade_date", lambda: None)
    out = svc.analyze("601811")
    assert out["ok"] is False and "无日线数据" in out["error"]


def test_expensive_stats_cached_per_date(monkeypatch):
    calls = _fake_data(monkeypatch)
    svc.analyze("601811")
    svc.analyze("601811")
    assert calls.count("promotion_table") == 1     # TTL 内只聚合一次
    assert calls.count("layer_forward") == 1


def test_llm_disabled_by_profile(monkeypatch):
    _fake_data(monkeypatch)
    monkeypatch.setattr(svc, "CONFIG",
                        dataclasses.replace(CONFIG, STOCK_LLM_PROFILE="off"))
    payload = svc.analyze("601811")
    llm = svc.llm_comment(payload)
    assert llm["enabled"] is False and llm["ok"] is False
    assert "未启用" in llm["error"]


def test_llm_failure_degrades_without_raising(monkeypatch):
    _fake_data(monkeypatch)
    monkeypatch.setattr(svc, "CONFIG",
                        dataclasses.replace(CONFIG, STOCK_LLM_PROFILE="smart"))

    def boom(*a, **k):
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr("emotion_core.services.llm.get_client", boom)
    payload = svc.analyze("601811")
    llm = svc.llm_comment(payload)
    assert llm["enabled"] is True and llm["ok"] is False
    assert "401" in llm["error"] and llm["text"] == ""
    assert svc.llm_for("601811")["ok"] is False    # 异步接口同样降级


def test_llm_success_returns_text_and_model(monkeypatch):
    _fake_data(monkeypatch)
    monkeypatch.setattr(svc, "CONFIG",
                        dataclasses.replace(CONFIG, STOCK_LLM_PROFILE="smart"))
    captured: dict = {}

    class FakeClient:
        def __init__(self, **kw):
            captured["kw"] = kw

        def chat(self, messages, purpose="chat", json_mode=False):
            captured["messages"] = messages
            captured["purpose"] = purpose
            return types.SimpleNamespace(text="局面…建议…", model="sensenova-6.8-flash-lite")

    monkeypatch.setattr("emotion_core.services.llm.get_client",
                        lambda profile, **kw: FakeClient(**kw))
    llm = svc.llm_for("601811")
    assert llm["ok"] is True and llm["text"].startswith("局面")
    assert llm["model"] == "sensenova-6.8-flash-lite"
    assert captured["purpose"] == "stock-diagnosis"
    system, user = captured["messages"]
    assert system["role"] == "system"
    # 提示词必须带上规则层结论与统计依据，且不许写"编造"
    assert "只做低吸回踩" in user["content"] or "观察" in user["content"]
    assert "晋级率" in user["content"] or "样本不足" in user["content"]


class TestBuyPointReference:
    def test_limit_price_computed_from_pre_close(self):
        basic = {"code": "601811", "pre_close": 10.0}
        trend = {"cont_days": 2}
        promo = {"promote_rate": 20.0, "n": 100}
        fwd5 = {"median": 1.0, "win_rate": 52.0, "avg": 1.5, "n": 300}
        bp = svc._buy_point_reference(basic, trend, "1->2", promo, fwd5)
        assert bp["limit_price"] is not None
        assert bp["limit_price"] > 10.0  # 涨停价 > 昨收
        assert bp["odds"]["promote_rate"] == 20.0
        assert bp["odds"]["fwd_median"] == 1.0
        assert "打板价" in bp["disclaimer"]

    def test_non_stock_code_has_no_limit_price(self):
        basic = {"code": "000001", "pre_close": 5.0}
        trend = {"cont_days": 1}
        bp = svc._buy_point_reference(basic, trend, "1->2",
                                      {"promote_rate": 30.0, "n": 50},
                                      {"median": 0.5, "win_rate": 55.0, "avg": 1.0, "n": 100})
        # 000001 是深市主板，板比 10%
        assert bp["limit_price"] is not None
        assert bp["limit_price"] > 5.0

    def test_no_layer_no_cont_days_returns_none_odds(self):
        basic = {"code": "601811", "pre_close": 10.0}
        trend = {"cont_days": 0}
        bp = svc._buy_point_reference(basic, trend, None, None, {"n": 0})
        assert bp["odds"] is None

    def test_missing_pre_close_returns_none_limit_price(self):
        basic = {"code": "601811"}
        trend = {"cont_days": 2}
        bp = svc._buy_point_reference(basic, trend, "1->2", None, {"n": 0})
        assert bp["limit_price"] is None

    def test_odds_include_layer_info(self):
        basic = {"code": "601811", "pre_close": 10.0}
        trend = {"cont_days": 3}
        fwd5 = {"median": 2.0, "win_rate": 60.0, "avg": 2.5, "n": 200}
        bp = svc._buy_point_reference(basic, trend, "3->4",
                                      {"promote_rate": 15.0, "n": 80}, fwd5)
        assert bp["odds"]["layer"] == "3->4"
        assert bp["odds"]["cont_days"] == 3
