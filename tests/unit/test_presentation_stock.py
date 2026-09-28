"""个股诊断展示层：请求参数还原、JSON 序列化、路由与模板渲染（不起服务）。

守的事：
- 中文输入两条路径都要能用：浏览器（百分号编码）与 curl 直发原始 UTF-8
  （请求行被 latin-1 解码成乱码，必须还原，否则用户看到莫名其妙的 404）；
- psycopg 取出的 Decimal/date 必须能序列化成 JSON（不然页面直接 500）；
- 个股页必须在导航里、能预填代码；直观层推荐要能点进个股页。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from urllib.parse import urlparse

from emotion_core.presentation import server


def test_recover_utf8_handles_raw_utf8_query():
    raw = "新华文轩".encode().decode("latin-1")   # curl 直发原始字节的形态
    assert server._recover_utf8(raw) == "新华文轩"


def test_recover_utf8_leaves_normal_and_ascii_untouched():
    assert server._recover_utf8("601811") == "601811"
    assert server._recover_utf8("新华文轩") == "新华文轩"
    assert server._recover_utf8("") == ""


def test_query_arg_decodes_percent_encoded_and_raw():
    assert server._query_arg(urlparse("/api/stock?code=601811"), "code") == "601811"
    assert server._query_arg(urlparse("/api/stock?code=%E6%96%B0%E5%8D%8E"),
                             "code") == "新华"
    raw = "/api/stock?code=" + "新华".encode().decode("latin-1")
    assert server._query_arg(urlparse(raw), "code") == "新华"
    assert server._query_arg(urlparse("/api/stock"), "code") == ""


def test_json_default_handles_psycopg_types():
    assert server._json_default(Decimal("1.2500")) == 1.25
    assert server._json_default(date(2026, 9, 24)) == "2026-09-24"
    assert server._json_default(object()).startswith("<")


def test_stock_route_registered_and_page_renders_with_prefill():
    assert server.ROUTES["stock"] == "stock"
    html = server.render_dashboard("stock", code="601811")
    assert 'id="stock-input"' in html and 'value="601811"' in html
    assert "个股诊断" in html
    assert f'src="{server.BASE_PATH}/static/stock.js"' in html


def test_nav_has_stock_entry_and_llm_card():
    html = server.render_dashboard("intuitive")
    for path in ("/logic", "/algorithm", "/strategy", "/stock"):
        assert f'{server.BASE_PATH}{path}"' in html


def test_stock_page_states_data_sources_and_pit():
    html = server.render_dashboard("stock")
    assert "PIT" in html


def test_intuitive_page_links_recommendations_to_stock(monkeypatch):
    monkeypatch.setattr(server, "_load_intuitive_data", lambda: {
        "phase": "发酵", "phase_desc": "x", "buy_window": "STANDARD",
        "force_liquidate": False, "limit_up_count": 40, "max_limit_days": 4,
        "limit_down_count": 3, "ladder_count": 0, "signal_count": 1,
        "dragon_env": "NEUTRAL", "dragon_desc": "环境一般", "accelerate": False,
        "accel_reason": "", "recommendation": {"code": "601811", "cont_days": 2},
        "recommendations": [{"code": "601811", "name": "新华文轩", "cont_days": 2}]})
    html = server.render_dashboard("intuitive")
    assert f'{server.BASE_PATH}/stock?code=601811' in html


def test_intuitive_page_offers_input_when_no_signal(monkeypatch):
    monkeypatch.setattr(server, "_load_intuitive_data", lambda: {
        "phase": "冰点", "phase_desc": "x", "buy_window": "NONE",
        "force_liquidate": False, "limit_up_count": 5, "max_limit_days": 1,
        "limit_down_count": 1, "ladder_count": 0, "signal_count": 0,
        "dragon_env": "", "dragon_desc": "暂无评级", "accelerate": False,
        "accel_reason": "", "recommendation": None, "recommendations": []})
    html = server.render_dashboard("intuitive")
    assert f'action="{server.BASE_PATH}/stock"' in html
