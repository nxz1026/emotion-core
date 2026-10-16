"""`/api/holding` 与 `/api/holdings` 的 HTTP 投影（docs/17 §8.1）。

只测「参数解析 → 服务层 → (payload, status)」这一段；服务层自身在
`tests/unit/test_holding_service.py`。本文件守的核心契约：
**HTTP 路径恒只读**（`persist=False`，不写 `holding_decision`）与
**参数非法 → 400 / 标的不可用 → 404**。
"""

from __future__ import annotations

import inspect
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pytest

from emotion_core.domain.holding import Decision, Levels, Sizing
from emotion_core.presentation import server
from emotion_core.services import holding_service as hs


def _levels() -> Levels:
    return Levels(
        buy_reference=15.029,
        stop_price=14.278,
        target_price=27.205,
        basis="cpt_structure",
        detail="结构位",
    )


def _sizing() -> Sizing:
    return Sizing(
        suggested_shares=13300,
        position_pct=0.1999,
        suggested_amount=199_900.0,
        method="risk+liq+maxpos",
        detail="取小",
    )


def _decision(ok: bool = True, **kw) -> Decision:
    base = dict(
        code="601811",
        trade_date=date(2026, 10, 13),
        action="HOLD",
        action_label="持有观察",
        market_price=21.14,
        pnl_pct=1.6,
        levels=_levels(),
        sizing=_sizing(),
        ladder=(),
        reasons=("r1",),
        facts={"bar_count": 119},
        inputs={"cost": 21.5, "shares": 1000},
        cpt_status="invalidated",
        cpt_action="hold",
        cpt_divergence="none",
        cpt_price_ratio=1.061,
        name="新华文轩",
        board="main",
        ok=ok,
        error=None if ok else "no_bar",
    )
    base.update(kw)
    return Decision(**base)


class TestParamHelpers:
    def test_opt_float(self):
        assert server._opt_float(urlparse("/api/holding?cost=21.5"), "cost") == 21.5
        assert server._opt_float(urlparse("/api/holding"), "cost") is None
        assert server._opt_float(urlparse("/api/holding?cost="), "cost") is None
        assert server._opt_float(urlparse("/api/holding?cost=%2021.5"), "cost") == 21.5

    def test_opt_float_rejects_garbage(self):
        with pytest.raises(ValueError, match="cost"):
            server._opt_float(urlparse("/api/holding?cost=abc"), "cost")

    def test_opt_float_rejects_non_positive(self):
        """`cost` / `capital` 没有 ≤0 的语义；与 `_opt_int` 对 shares 的口径一致。"""
        for bad in ("0", "-1", "-0.001", "nan"):
            with pytest.raises(ValueError, match="cost"):
                server._opt_float(urlparse(f"/api/holding?cost={bad}"), "cost")

    def test_opt_int(self):
        assert server._opt_int(urlparse("/api/holding?shares=1000"), "shares") == 1000
        assert server._opt_int(urlparse("/api/holding"), "shares") is None

    def test_opt_int_rejects_float(self):
        with pytest.raises(ValueError, match="shares"):
            server._opt_int(urlparse("/api/holding?shares=10.5"), "shares")

    @pytest.mark.parametrize("raw", ["0", "false", "False", "no", "off", "OFF"])
    def test_query_flag_falsey(self, raw):
        parsed = urlparse(f"/api/holding?cpt={raw}")
        assert server._query_flag(parsed, "cpt") is False

    def test_query_flag_default_true_and_explicit_true(self):
        assert server._query_flag(urlparse("/api/holding"), "cpt") is True
        assert server._query_flag(urlparse("/api/holding?cpt=1"), "cpt") is True

    def test_opt_date(self):
        parsed = urlparse("/api/holding?as_of=2026-10-12")
        assert server._opt_date(parsed, "as_of") == date(2026, 10, 12)
        assert server._opt_date(urlparse("/api/holding"), "as_of") is None

    def test_opt_date_rejects_garbage(self):
        # 注意 py3.11+ 的 fromisoformat 接受紧凑式 "20261012"，故用真正的非法值
        with pytest.raises(ValueError, match="as_of"):
            server._opt_date(urlparse("/api/holding?as_of=2026/10/12"), "as_of")

    def test_opt_date_accepts_compact_iso(self):
        assert server._opt_date(urlparse("/api/holding?as_of=20261012"), "as_of") == date(
            2026, 10, 12
        )


class TestHoldingPayload:
    def test_ok_path_status_and_params(self, monkeypatch):
        seen: dict = {}

        def fake_decide(code, **kw):
            seen["code"] = code
            seen.update(kw)
            return _decision()

        monkeypatch.setattr(hs, "decide", fake_decide)
        parsed = urlparse("/api/holding?code=601811&cost=21.5&shares=1000&capital=1000000")
        body, status = server.holding_payload(parsed)

        assert status == 200 and body["ok"] is True
        assert body["action"] == "HOLD"
        assert body["sizing"]["suggested_shares"] == 13300
        assert body["stop"]["price"] == 14.278
        assert body["disclaimer"]
        assert seen["code"] == "601811"
        assert seen["cost"] == 21.5 and seen["shares"] == 1000
        assert seen["capital"] == 1_000_000.0

    def test_http_is_always_read_only(self, monkeypatch):
        """HTTP 路径必须恒 `persist=False`（落库只走 CLI --persist）。"""
        seen: dict = {}
        monkeypatch.setattr(hs, "decide", lambda code, **kw: (seen.update(kw), _decision())[1])
        server.holding_payload(urlparse("/api/holding?code=601811&persist=1"))
        assert seen["persist"] is False

    def test_cpt_off_and_as_of_are_forwarded(self, monkeypatch):
        seen: dict = {}
        monkeypatch.setattr(hs, "decide", lambda code, **kw: (seen.update(kw), _decision())[1])
        parsed = urlparse("/api/holding?code=601811&cpt=0&as_of=2026-10-12")
        server.holding_payload(parsed)
        assert seen["use_cpt"] is False
        assert seen["as_of"] == date(2026, 10, 12)

    def test_invalid_param_is_400_without_calling_service(self, monkeypatch):
        monkeypatch.setattr(hs, "decide", lambda *a, **kw: pytest.fail("不应调用服务层"))
        body, status = server.holding_payload(urlparse("/api/holding?code=601811&cost=abc"))
        assert status == 400 and body["ok"] is False
        assert "cost" in body["error"]

    def test_unavailable_symbol_is_404(self, monkeypatch):
        monkeypatch.setattr(hs, "decide", lambda *a, **kw: _decision(ok=False))
        body, status = server.holding_payload(urlparse("/api/holding?code=001381"))
        assert status == 404 and body["ok"] is False and body["error"] == "no_bar"

    def test_missing_code_is_forwarded_not_guessed(self, monkeypatch):
        seen: dict = {}
        monkeypatch.setattr(
            hs, "decide", lambda code, **kw: (seen.update({"code": code}), _decision())[1]
        )
        server.holding_payload(urlparse("/api/holding"))
        assert seen["code"] == ""


class TestHoldingsPayload:
    def test_empty_is_200_not_404(self, monkeypatch):
        monkeypatch.setattr(hs, "analyze_holdings", lambda **kw: [])
        body, status = server.holdings_payload(urlparse("/api/holdings"))
        assert status == 200
        assert body["count"] == 0 and body["items"] == []
        assert body["trade_date"] is None and body["disclaimer"]

    def test_items_and_trade_date(self, monkeypatch):
        monkeypatch.setattr(
            hs, "analyze_holdings", lambda **kw: [_decision(), _decision(code="000001")]
        )
        body, status = server.holdings_payload(urlparse("/api/holdings?capital=300000"))
        assert status == 200 and body["count"] == 2
        assert body["trade_date"] == "2026-10-13"
        assert [i["code"] for i in body["items"]] == ["601811", "000001"]

    def test_capital_and_as_of_are_forwarded(self, monkeypatch):
        seen: dict = {}
        monkeypatch.setattr(hs, "analyze_holdings", lambda **kw: (seen.update(kw), [])[1])
        server.holdings_payload(urlparse("/api/holdings?capital=300000&cpt=0"))
        assert seen["capital"] == 300_000.0 and seen["use_cpt"] is False

    def test_invalid_param_is_400(self, monkeypatch):
        monkeypatch.setattr(hs, "analyze_holdings", lambda **kw: pytest.fail("不应调用服务层"))
        body, status = server.holdings_payload(urlparse("/api/holdings?capital=x"))
        assert status == 400 and "capital" in body["error"]


class TestRouteWiring:
    """持仓 / CPT 点位 / 关注列表四条分支必须真的挂在 `DashboardHandler` 上。"""

    def test_do_get_dispatches_both_paths(self):
        src = inspect.getsource(server.DashboardHandler.do_GET)
        assert '"/api/holding"' in src and "holding_payload(parsed)" in src
        assert '"/api/holdings"' in src and "holdings_payload(parsed)" in src

    def test_do_get_dispatches_cpt_points(self):
        """R4：`/api/cpt/points` 必须挂在 do_GET 上。"""
        src = inspect.getsource(server.DashboardHandler.do_GET)
        assert '"/api/cpt/points"' in src and "cpt_points_payload(parsed)" in src

    def test_do_get_dispatches_watchlist(self):
        """R4：`/api/watchlist` GET 必须挂在 do_GET 上。"""
        src = inspect.getsource(server.DashboardHandler.do_GET)
        assert '"/api/watchlist"' in src and "watchlist_payload(parsed)" in src

    def test_do_post_dispatches_watchlist(self):
        """R4：`/api/watchlist` POST 必须挂在 do_POST 上。"""
        src = inspect.getsource(server.DashboardHandler.do_POST)
        assert '"/api/watchlist"' in src and "_handle_watchlist_post(self)" in src

    def test_branches_precede_the_api_fallback(self):
        src = inspect.getsource(server.DashboardHandler.do_GET)
        assert src.index('"/api/holding"') < src.index('parsed.path.startswith("/api/")')

    def test_unknown_api_path_is_404_not_status_fallback(self):
        """未识别的 `/api/*` 必须 404，不能再兜底成 status 载荷。

        2026-10-14 审计 A5：兜底让拼错的接口返回 200 + `ok:true`，前端把 status
        载荷当成功渲染出字面量 `undefined · undefined`，监控也看不出失败。
        兜底只留给 `/api/status`（门户「今日速览」依赖它）。
        """
        src = inspect.getsource(server.DashboardHandler.do_GET)
        assert src.index('"/api/status"') < src.index('parsed.path.startswith("/api/")')
        assert "未知 API 路径" in src
        # 兜底只剩一处调用点（`/api/status` 分支），catch-all 不再调它
        assert src.count("self._handle_status()") == 1


# ─────────────────────── R4 参数化 + 关注列表 + CPT 点位 ───────────────────────


class TestOptPct:
    """`_opt_pct`：闭区间校验（docs/18 §7.5）。"""

    def test_valid_in_bounds(self):
        assert server._opt_pct(urlparse("/x?risk_pct=0.02"), "risk_pct", 0.001, 0.05) == 0.02

    def test_boundary_values(self):
        assert server._opt_pct(urlparse("/x?risk_pct=0.001"), "risk_pct", 0.001, 0.05) == 0.001
        assert server._opt_pct(urlparse("/x?risk_pct=0.05"), "risk_pct", 0.001, 0.05) == 0.05

    def test_none_when_missing(self):
        assert server._opt_pct(urlparse("/x"), "risk_pct", 0.001, 0.05) is None
        assert server._opt_pct(urlparse("/x?risk_pct="), "risk_pct", 0.001, 0.05) is None

    def test_rejects_out_of_bounds(self):
        with pytest.raises(ValueError, match="risk_pct"):
            server._opt_pct(urlparse("/x?risk_pct=0.99"), "risk_pct", 0.001, 0.05)

    def test_rejects_non_numeric(self):
        with pytest.raises(ValueError, match="risk_pct"):
            server._opt_pct(urlparse("/x?risk_pct=abc"), "risk_pct", 0.001, 0.05)


class TestOptUser:
    def test_default_when_missing(self):
        assert server._opt_user(urlparse("/x")) == "default"

    def test_explicit_user(self):
        assert server._opt_user(urlparse("/x?user=alice")) == "alice"

    def test_empty_string_defaults(self):
        assert server._opt_user(urlparse("/x?user=")) == "default"


class TestHoldingPayloadSizingParams:
    """`holding_payload` 透传 sizing_params / ensure_factors / user。"""

    def test_sizing_params_forwarded(self, monkeypatch):
        seen: dict = {}
        monkeypatch.setattr(hs, "decide", lambda code, **kw: (seen.update(kw), _decision())[1])
        parsed = urlparse(
            "/api/holding?code=601811&risk_pct=0.02&liq_pct=0.01&max_position_pct=0.5"
        )
        server.holding_payload(parsed)
        assert seen["sizing_params"] == {"risk_pct": 0.02, "liq_pct": 0.01, "max_position_pct": 0.5}

    def test_partial_sizing_params(self, monkeypatch):
        seen: dict = {}
        monkeypatch.setattr(hs, "decide", lambda code, **kw: (seen.update(kw), _decision())[1])
        server.holding_payload(urlparse("/api/holding?code=601811&risk_pct=0.03"))
        assert seen["sizing_params"] == {"risk_pct": 0.03}

    def test_no_sizing_params_when_absent(self, monkeypatch):
        seen: dict = {}
        monkeypatch.setattr(hs, "decide", lambda code, **kw: (seen.update(kw), _decision())[1])
        server.holding_payload(urlparse("/api/holding?code=601811"))
        assert seen["sizing_params"] is None

    def test_out_of_bounds_raises_400(self, monkeypatch):
        monkeypatch.setattr(hs, "decide", lambda *a, **kw: pytest.fail("不应调用"))
        body, status = server.holding_payload(urlparse("/api/holding?code=601811&risk_pct=0.99"))
        assert status == 400 and "risk_pct" in body["error"]

    def test_ensure_factors_forwarded(self, monkeypatch):
        seen: dict = {}
        monkeypatch.setattr(hs, "decide", lambda code, **kw: (seen.update(kw), _decision())[1])
        server.holding_payload(urlparse("/api/holding?code=601811&ensure_factors=1"))
        assert seen["ensure_factors"] is True

    def test_user_forwarded(self, monkeypatch):
        seen: dict = {}
        monkeypatch.setattr(hs, "decide", lambda code, **kw: (seen.update(kw), _decision())[1])
        server.holding_payload(urlparse("/api/holding?code=601811&user=alice"))
        assert seen["cpt_user"] == "alice"


class TestCptPointsPayload:
    """`cpt_points_payload`：CPT 参考买卖点（恒只读）。"""

    def test_cpt_available(self, monkeypatch):
        snap = {
            "overlays": {
                "bis": [{"direction": -1, "low": 16.0}],
                "zhongshus": [{"low": 15.5, "high": 27.0}],
                "fractals": [],
                "trend_types": [],
            },
            "data_quality": {"severity": "ok"},
            "signal": {},
            "runtime": {},
            "config": {},
        }
        rec = {
            "available": True,
            "action": "watch",
            "price_ratio": 1.061,
            "signal_type": "first_buy",
            "divergence_status": None,
            "headline": "结构位参考",
            "reason": "中枢下沿",
            "status": "ok",
            "action_label": "观望",
            "disclaimer": "test",
        }
        advice = {"suggested_points": {"buy_reference": 15.0}, "human_text": "近低关注"}
        import emotion_core.services.cpt_client as cpt_mod

        monkeypatch.setattr(cpt_mod, "fetch_snapshot", lambda code, **kw: snap)
        monkeypatch.setattr(cpt_mod, "fetch_recommendation", lambda code, **kw: rec)
        monkeypatch.setattr(cpt_mod, "price_ratio_of", lambda r: 1.061)
        monkeypatch.setattr(cpt_mod, "snapshot_overlays", lambda s: s["overlays"])
        monkeypatch.setattr(cpt_mod, "data_quality_note", lambda s: None)
        monkeypatch.setattr(cpt_mod, "track_advice", lambda code, **kw: advice)
        monkeypatch.setattr(cpt_mod, "points_view", lambda a: a.get("suggested_points"))
        monkeypatch.setattr(cpt_mod, "human_text", lambda a: a.get("human_text"))
        parsed = urlparse("/api/cpt/points?code=601811")
        body, status = server.cpt_points_payload(parsed)
        assert status == 200 and body["ok"] is True
        assert body["cpt_available"] is True
        assert body["levels"]["buy_reference"] == 14.901
        assert body["human_text"] == "近低关注"

    def test_cpt_unavailable(self, monkeypatch):
        import emotion_core.services.cpt_client as cpt_mod

        monkeypatch.setattr(cpt_mod, "fetch_snapshot", lambda code, **kw: None)
        body, status = server.cpt_points_payload(urlparse("/api/cpt/points?code=601811"))
        assert status == 200 and body["ok"] is True
        assert body["cpt_available"] is False

    def test_invalid_param_400(self, monkeypatch):
        import emotion_core.services.cpt_client as cpt_mod

        monkeypatch.setattr(cpt_mod, "fetch_snapshot", lambda code, **kw: None)
        body, status = server.cpt_points_payload(urlparse("/api/cpt/points?"))
        assert status == 400 and body["ok"] is False


class TestWatchlistPayload:
    """`watchlist_payload`：关注列表（恒只读）。"""

    def test_success(self, monkeypatch):
        items = [{"code": "601811", "name": "新华文轩", "note": ""}]
        import emotion_core.services.watchlist_service as wl_mod

        monkeypatch.setattr(wl_mod, "list_codes", lambda user: (items, True))
        body, status = server.watchlist_payload(urlparse("/api/watchlist"))
        assert status == 200 and body["ok"] is True
        assert body["count"] == 1 and body["cpt_available"] is True

    def test_cpt_unavailable(self, monkeypatch):
        import emotion_core.services.watchlist_service as wl_mod

        monkeypatch.setattr(wl_mod, "list_codes", lambda user: ([], False))
        body, status = server.watchlist_payload(urlparse("/api/watchlist"))
        assert status == 200 and body["ok"] is True
        assert body["count"] == 0 and body["cpt_available"] is False


class TestWatchlistPost:
    """`_handle_watchlist_post`：add / remove / restore（R4，docs/18 §7.5）。"""

    def _handler(self, path, body=b"", content_length=None):
        """构造一个伪 handler 供 `_handle_watchlist_post` 调用。"""
        from unittest.mock import MagicMock

        h = MagicMock()
        h.headers = {"Content-Length": content_length if content_length is not None else len(body)}
        h.rfile = MagicMock()
        h.rfile.read.return_value = body
        h.path = path
        return h

    def test_add(self, monkeypatch):
        import emotion_core.services.watchlist_service as wl_mod

        monkeypatch.setattr(wl_mod, "add", lambda code, user="default", note="": (True, "ok"))
        payload, status = server._handle_watchlist_post(
            self._handler("/api/watchlist", b'{"action":"add","code":"601811"}')
        )
        assert status == 200 and payload["ok"] is True

    def test_remove(self, monkeypatch):
        import emotion_core.services.watchlist_service as wl_mod

        monkeypatch.setattr(wl_mod, "remove", lambda code, user="default": (True, "ok"))
        payload, status = server._handle_watchlist_post(
            self._handler("/api/watchlist", b'{"action":"remove","code":"601811"}')
        )
        assert status == 200

    def test_restore(self, monkeypatch):
        import emotion_core.services.watchlist_service as wl_mod

        monkeypatch.setattr(wl_mod, "restore", lambda code, user="default": (True, "ok"))
        payload, status = server._handle_watchlist_post(
            self._handler("/api/watchlist", b'{"action":"restore","code":"601811"}')
        )
        assert status == 200

    def test_invalid_action_400(self):
        payload, status = server._handle_watchlist_post(
            self._handler("/api/watchlist", b'{"action":"frobnicate","code":"601811"}')
        )
        assert status == 400 and payload["ok"] is False

    def test_bad_json_400(self):
        payload, status = server._handle_watchlist_post(
            self._handler("/api/watchlist", b"not json")
        )
        assert status == 400

    def test_body_too_large_413(self):
        payload, status = server._handle_watchlist_post(
            self._handler("/api/watchlist", b"x" * 5000, content_length=5000)
        )
        assert status == 413


def _referenced_assets() -> set[str]:
    """模板里 `{{ base_path }}/static/<name>` 引用到的静态资产名。"""
    root = Path(server.__file__).parent
    names: set[str] = set()
    for tpl in (root / "templates").glob("*.html"):
        names |= set(re.findall(r"/static/([\w.-]+)", tpl.read_text(encoding="utf-8")))
    return names


class TestStaticAssetWiring:
    """静态资产接线：模板引用的必须存在，且 `static/` 里不留孤儿文件。

    2026-10-14 审计 A1：R3 持仓卡片的 `.holding-*` 样式**全写在孤儿文件
    `static/style.css`**，而模板只加载 `static/dashboard.css` ⇒ 卡片功能在、
    样式全无（实测 `.holding-form` 的 `padding/border-radius/background` 全是浏览器
    默认值，按钮是个灰疙瘩）。本测试把「引用 → 文件存在 → 样式真的写在被加载的那份
    CSS 里」钉死，防止同样的写法再次出现。
    """

    def test_assets_referenced_by_templates_exist(self):
        root = Path(server.__file__).parent
        assert _referenced_assets() == {"dashboard.css", "stock.js"}
        missing = sorted(n for n in _referenced_assets() if not (root / "static" / n).is_file())
        assert missing == []

    def test_no_orphan_static_file(self):
        root = Path(server.__file__).parent
        present = {p.name for p in (root / "static").iterdir() if p.is_file()}
        assert present - _referenced_assets() == set()

    def test_card_and_calendar_rules_live_in_the_loaded_stylesheet(self):
        path = Path(server.__file__).parent / "static" / "dashboard.css"
        css = path.read_text(encoding="utf-8")
        for sel in (
            ".holding-form",
            ".holding-action",
            ".holding-add",
            ".holding-ladder-row.buy .side",
            ".holding-card details",
            ".cal-day.no-data",
        ):
            assert sel in css, f"{sel} 必须在被模板加载的 dashboard.css 里"


class TestHoldingEmptyStateHint:
    """空态引导：卡片首次出现时只有一排输入框 + 一个「—」。

    2026-10-14 无头审计：卡片在 `bindHoldingForm` 里就被 `display:block` 显出来，而
    `holding-body` 要等点「计算」才渲染 ⇒ 首屏是一片空白输入框，用户看不出下一步。
    本测试钉住「空态给引导 / 已回填给提示 / 成功渲染清掉」三条接线。
    """

    def test_hint_wired_for_empty_ready_and_cleared_states(self):
        js = (Path(server.__file__).parent / "static" / "stock.js").read_text(encoding="utf-8")
        assert 'const HOLD_EMPTY_HINT = "填成本价与数量后点「计算」' in js
        assert 'const HOLD_READY_HINT = "点「计算」查看决策";' in js
        # 空态与「已回填」各挂一次；仓位预填成功后再挂一次 ready
        assert js.count("= saved.cost ? HOLD_READY_HINT : HOLD_EMPTY_HINT;") == 1
        assert js.count('$("holding-hint").textContent = HOLD_READY_HINT;') == 1
        # 成功渲染必须把引导清空，否则引导会与结果同屏
        assert js.count('$("holding-hint").textContent = "";') == 1
        # 失败路径仍然只写错误原因，不被引导覆盖
        assert '"无法计算："' in js
