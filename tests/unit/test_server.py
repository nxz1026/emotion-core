"""展示层 web 服务（presentation.server）：静态文件 / 模板渲染 / 路由解析，不连库。"""
from __future__ import annotations

import os
from types import SimpleNamespace
from urllib.parse import urlparse

from emotion_core.presentation import server


class TestRecoverUtf8:
    def test_ascii_passthrough(self):
        assert server._recover_utf8("hello") == "hello"

    def test_empty_string(self):
        assert server._recover_utf8("") == ""

    def test_none(self):
        assert server._recover_utf8(None) is None

    def test_mangled_utf8(self):
        # 模拟被 latin-1 展宽的 UTF-8: "新华文轩" 被错误解码
        original = "新华文轩"
        mangled = original.encode("utf-8").decode("latin-1")
        assert server._recover_utf8(mangled) == original

    def test_normal_chinese_not_affected(self):
        # 正常中文不应被改变（无法被 latin-1 编码）
        assert server._recover_utf8("正常中文") == "正常中文"


class TestDefaultPath:
    def test_intuitive(self):
        assert server._default_path("intuitive") == "/emotion/"

    def test_logic(self):
        assert server._default_path("logic") == "/emotion/logic"

    def test_strategy(self):
        assert server._default_path("strategy") == "/emotion/strategy"


class TestRequestPath:
    def test_adds_prefix_when_missing(self):
        result = server._request_path("/logic", "logic")
        assert result == "/emotion/logic"

    def test_keeps_prefix_when_present(self):
        result = server._request_path("/emotion/logic", "logic")
        assert result == "/emotion/logic"

    def test_none_uses_default(self):
        result = server._request_path(None, "intuitive")
        assert result == "/emotion/"

    def test_no_slash_prefix(self):
        result = server._request_path("logic", "logic")
        assert result == "/emotion/logic"


class TestLoadIntuitiveData:
    def test_basic_shape(self, monkeypatch):
        monkeypatch.setattr(server.loaders, "load_market_snapshot",
                            lambda d: {"phase": "发酵", "date": "2026-09-25"})
        monkeypatch.setattr(server.loaders, "load_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_signals", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_top_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_signal_counts",
                            lambda d: {"day": 0, "total": 0, "ladder_day": 0, "ladder_total": 0})
        data = server._load_intuitive_data()
        assert data["phase"] == "发酵"
        assert "phase_desc" in data
        assert "signal_counts" in data

    def test_phase_descriptions(self, monkeypatch):
        for phase, expected_word in [
            ("发酵", "上升"), ("高潮", "狂热"), ("退潮", "观望"), ("冰点", "低迷")
        ]:
            monkeypatch.setattr(server.loaders, "load_market_snapshot",
                                lambda d, p=phase: {"phase": p})
            monkeypatch.setattr(server.loaders, "load_ladder", lambda d: [])
            monkeypatch.setattr(server.loaders, "load_signals", lambda d: [])
            monkeypatch.setattr(server.loaders, "load_top_ladder", lambda d: [])
            monkeypatch.setattr(server.loaders, "load_signal_counts",
                                lambda d: {"day": 0, "total": 0, "ladder_day": 0, "ladder_total": 0})
            data = server._load_intuitive_data()
            assert expected_word in data["phase_desc"]

    def test_dragon_env_descriptions(self, monkeypatch):
        monkeypatch.setattr(server.loaders, "load_market_snapshot",
                            lambda d: {"dragon_env": "FAVORABLE"})
        monkeypatch.setattr(server.loaders, "load_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_signals", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_top_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_signal_counts",
                            lambda d: {"day": 0, "total": 0, "ladder_day": 0, "ladder_total": 0})
        data = server._load_intuitive_data()
        assert "积极" in data["dragon_desc"]

    def test_recommendation_filtering(self, monkeypatch):
        signals = [
            {"code": "000001", "action": "BUY"},
            {"code": "600000", "action": "WATCH"},
            {"code": "000002", "action": "BUY"},
        ]
        monkeypatch.setattr(server.loaders, "load_market_snapshot", lambda d: {})
        monkeypatch.setattr(server.loaders, "load_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_signals", lambda d: signals)
        monkeypatch.setattr(server.loaders, "load_top_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_signal_counts",
                            lambda d: {"day": 0, "total": 0, "ladder_day": 0, "ladder_total": 0})
        data = server._load_intuitive_data()
        assert data["recommendation"]["code"] == "000001"
        assert len(data["recommendations"]) == 2

    def test_observation_signals_filters_secondary_recommend(self, monkeypatch):
        """R58-5：observation_signals 仅保留 SECONDARY + RECOMMEND，BUY 不混入。"""
        signals = [
            {"code": "000001", "action": "BUY"},
            {"code": "000002", "action": "SECONDARY"},
            {"code": "000003", "action": "RECOMMEND"},
            {"code": "000004", "action": "WATCH"},
        ]
        monkeypatch.setattr(server.loaders, "load_market_snapshot", lambda d: {})
        monkeypatch.setattr(server.loaders, "load_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_signals", lambda d: signals)
        monkeypatch.setattr(server.loaders, "load_top_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_signal_counts",
                            lambda d: {"day": 0, "total": 0, "ladder_day": 0, "ladder_total": 0})
        data = server._load_intuitive_data()
        codes = [s["code"] for s in data["observation_signals"]]
        assert codes == ["000003", "000002"]   # RECOMMEND 在前，SECONDARY 在后
        assert "000001" not in codes   # BUY 不混入
        assert "000004" not in codes   # WATCH 排除

    def test_no_buy_signals(self, monkeypatch):
        signals = [{"code": "000001", "action": "WATCH"}]
        monkeypatch.setattr(server.loaders, "load_market_snapshot", lambda d: {})
        monkeypatch.setattr(server.loaders, "load_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_signals", lambda d: signals)
        monkeypatch.setattr(server.loaders, "load_top_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_signal_counts",
                            lambda d: {"day": 0, "total": 0, "ladder_day": 0, "ladder_total": 0})
        data = server._load_intuitive_data()
        assert data["recommendation"] is None


class TestLoadLogicData:
    def test_basic_shape(self, monkeypatch):
        monkeypatch.setattr(server.loaders, "load_market_snapshot",
                            lambda d: {"date": "2026-09-25"})
        monkeypatch.setattr(server.loaders, "load_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_top_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_promotion", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_top_themes", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_hot_rank", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_market_trend", lambda: [])
        data = server._load_logic_data()
        assert "stat" in data
        assert "bomb_count" in data
        assert "oneword_count" in data

    def test_bomb_count_calculation(self, monkeypatch):
        market = {"bomb_rate": 0.3, "limit_up_count": 10}
        monkeypatch.setattr(server.loaders, "load_market_snapshot", lambda d: market)
        monkeypatch.setattr(server.loaders, "load_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_top_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_promotion", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_top_themes", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_hot_rank", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_market_trend", lambda: [])
        data = server._load_logic_data()
        assert data["bomb_count"] == 3

    def test_bomb_count_none_without_rate(self, monkeypatch):
        monkeypatch.setattr(server.loaders, "load_market_snapshot", lambda d: {})
        monkeypatch.setattr(server.loaders, "load_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_top_ladder", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_promotion", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_top_themes", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_hot_rank", lambda d: [])
        monkeypatch.setattr(server.loaders, "load_market_trend", lambda: [])
        data = server._load_logic_data()
        assert data["bomb_count"] is None


class TestLoadAlgorithmData:
    def test_basic_shape(self, monkeypatch):
        monkeypatch.setattr(server.loaders, "load_market_snapshot",
                            lambda d: {"date": "2026-09-25"})
        data = server._load_algorithm_data()
        assert "formulas" in data
        assert "thresholds" in data
        assert "涨停价" in data["formulas"]
        assert "is_limit_up" in data["formulas"]

    def test_snapshot_failure_handled(self, monkeypatch):
        def bad_load(d):
            raise RuntimeError("DB error")
        monkeypatch.setattr(server.loaders, "load_market_snapshot", bad_load)
        data = server._load_algorithm_data()
        assert data["market"] == {}


class TestRenderDashboard:
    def test_intuitive_layer(self, monkeypatch):
        monkeypatch.setattr(server, "_load_intuitive_data",
                            lambda d: {"phase": "发酵"})
        monkeypatch.setattr(server.snapshot, "resolve_date", lambda d: None)
        monkeypatch.setattr(server.snapshot, "banner_ctx", lambda d: {})
        monkeypatch.setattr(server.snapshot, "calendar_ctx", lambda d, m: {})
        monkeypatch.setattr(server.snapshot, "date_links", lambda d: {"date_query": ""})
        monkeypatch.setattr(server.snapshot, "available_dates", lambda: [])
        monkeypatch.setattr(server.snapshot, "recent_snapshots_ctx", lambda d: [])
        monkeypatch.setattr(server, "_render_template",
                            lambda name, **ctx: f"rendered:{name}")
        result = server.render_dashboard("intuitive")
        assert "rendered:intuitive.html" == result

    def test_strategy_layer(self, monkeypatch):
        monkeypatch.setattr(server.strategy_view, "strategy_dates",
                            lambda: ["2026-09-25"])
        monkeypatch.setattr(server.snapshot, "resolve_date", lambda d: None)
        monkeypatch.setattr(server.snapshot, "banner_ctx", lambda d: {})
        monkeypatch.setattr(server.snapshot, "calendar_ctx", lambda d, m: {})
        monkeypatch.setattr(server.snapshot, "date_links", lambda d: {"date_query": ""})
        monkeypatch.setattr(server.snapshot, "available_dates", lambda: [])
        monkeypatch.setattr(server.snapshot, "recent_snapshots_ctx", lambda d: [])
        monkeypatch.setattr(server, "_render_template",
                            lambda name, **ctx: f"rendered:{name}")
        result = server.render_dashboard("strategy")
        assert "rendered:strategy.html" == result

    def test_unknown_layer_returns_empty(self, monkeypatch):
        monkeypatch.setattr(server.snapshot, "resolve_date", lambda d: None)
        monkeypatch.setattr(server.snapshot, "banner_ctx", lambda d: {})
        monkeypatch.setattr(server.snapshot, "calendar_ctx", lambda d, m: {})
        monkeypatch.setattr(server.snapshot, "date_links", lambda d: {"date_query": ""})
        monkeypatch.setattr(server.snapshot, "available_dates", lambda: [])
        monkeypatch.setattr(server.snapshot, "recent_snapshots_ctx", lambda d: [])
        monkeypatch.setattr(server, "_render_template",
                            lambda name, **ctx: f"rendered:{name}")
        result = server.render_dashboard("unknown")
        assert "rendered:unknown.html" == result


class TestGetStatic:
    def test_missing_file_returns_empty(self):
        content, ct = server.get_static("nonexistent.css")
        assert content == b""
        assert ct == ""

    def test_css_content_type(self, monkeypatch, tmp_path):
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        (static_dir / "style.css").write_text("body {}", encoding="utf-8")
        monkeypatch.setattr(server, "STATIC_DIR", static_dir)
        content, ct = server.get_static("style.css")
        assert b"body {}" in content
        assert ct == "text/css"


class TestJsonDefault:
    def test_decimal(self):
        from decimal import Decimal
        assert server._json_default(Decimal("10.5")) == 10.5

    def test_date(self):
        from datetime import date
        assert server._json_default(date(2026, 9, 25)) == "2026-09-25"

    def test_other(self):
        assert server._json_default({1, 2, 3}) == str({1, 2, 3})
