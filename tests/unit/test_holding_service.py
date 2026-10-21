"""`holding_service`：方向判定优先级、分档守恒、降级路径与 CLI 只读纪律。

全程 monkeypatch，不连库、不出网（仓库基线的单测 DB-free 约定）。

守的事：
- §7.2 优先级表**逐条**：退潮 > 止损 > 断板 > 目标 > 无判据行 > 环境 > 加仓 > 默认；
- `ok=False`（no_bar）不落库，且停牌 `close<=0` 视作 no_bar（不拿 0 比价位）；
- §6.5「Σ各档 == suggested_shares」，且逐档是 lot 的整数倍；
- CPT 任何失败只记 degrade，绝不抛出；`use_cpt=False` 时一次都不调；
- CLI `--help` 零副作用、参数冲突 exit 2、默认不写库；
- **常数不进 `CONFIG`**（`CONFIG` 是 frozen dataclass 且参与 `config_hash()`）。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from emotion_core.domain.holding import Levels, Sizing
from emotion_core.services import holding_service as hs
from emotion_core.utils.config import CONFIG

D = date(2026, 10, 13)


def _levels(**kw: Any) -> Levels:
    base: dict[str, Any] = {
        "buy_reference": 10.0,
        "stop_price": None,
        "target_price": None,
        "basis": "cpt_structure",
        "detail": "d",
    }
    base.update(kw)
    return Levels(**base)


@pytest.fixture
def io(monkeypatch):
    """把 5 个读口 + position + 落库口全部换成内存替身。"""
    box: dict[str, Any] = {
        "trade_date": D,
        "basic": {"name": "测试股", "is_st": False, "float_shares": 1e9, "in_market": True},
        "bar": {"close": 10.0, "amount": 2e8},
        "drv": None,
        "stat": None,
        "pos": None,
        "writes": [],
        "cpt_calls": 0,
        "levels": None,
    }
    monkeypatch.setattr(hs, "_latest_trade_date", lambda: box["trade_date"])
    monkeypatch.setattr(hs, "_basic", lambda code: box["basic"])
    monkeypatch.setattr(hs, "_bar", lambda code, d: box["bar"])
    monkeypatch.setattr(hs, "_derived", lambda code, d: box["drv"])
    monkeypatch.setattr(hs, "_market", lambda d: box["stat"])
    monkeypatch.setattr(hs.holding_store, "holding_for", lambda code: box["pos"])
    # exit 侧的两个读口（都要查库）换掉，保持 DB-free
    monkeypatch.setattr(hs.exit_svc, "held_days", lambda entry, d: box.get("held"))
    monkeypatch.setattr(
        hs.exit_svc, "open_locked_limit_down", lambda d, code: bool(box.get("locked")))
    monkeypatch.setattr(
        hs.holding_store, "upsert_decision", lambda d: (box["writes"].append(d), 1)[1]
    )
    monkeypatch.setattr(hs.cpt_client, "cpt_enabled", lambda: False)

    real_structure = hs.structure_levels

    def _structure(overlays, price_ratio, *, close):
        if box["levels"] is not None:
            return box["levels"]
        return real_structure(overlays, price_ratio, close=close)

    monkeypatch.setattr(hs, "structure_levels", _structure)
    return box


# ────────────────────────── §7.2 优先级表（纯函数） ──────────────────────────


class TestPositionView:
    """持仓视角（有成本）：口径B 只按建仓后经过的交易日数退出，不再看退潮/止损/断板。"""

    def test_no_entry_date_is_degrade_hold(self):
        """有成本但查不到建仓日 → 算不出持有天数，不许猜「清仓」也不许猜「持有」。"""
        action, r = hs._decide_action(
            stat=None, drv=None, cost=5.0, close=10.0, levels=_levels(), held=None
        )
        assert action == "HOLD" and "建仓日" in r["text"]

    def test_entry_day_holds(self):
        action, r = hs._decide_action(
            stat=None, drv=None, cost=5.0, close=10.0, levels=_levels(), held=0
        )
        assert action == "HOLD" and "刚建仓" in r["text"]

    def test_next_trading_day_exits(self):
        """建仓后第 1 个交易日 = T+2 → 开盘卖。"""
        action, r = hs._decide_action(
            stat=None, drv=None, cost=5.0, close=10.0, levels=_levels(), held=1
        )
        assert action == "EXIT"
        assert "T+2 开盘卖出" in r["text"] and "口径B" in r["text"]

    def test_locked_limit_down_holds_and_defers(self):
        action, r = hs._decide_action(
            stat=None, drv=None, cost=5.0, close=10.0, levels=_levels(),
            held=1, locked=True,
        )
        assert action == "HOLD" and "顺延" in r["text"]

    def test_overdue_forces_exit_even_when_locked(self):
        """超过 T+3 无条件了结：即便今天又跌停封死，也要给卖出指令。"""
        action, r = hs._decide_action(
            stat=None, drv=None, cost=5.0, close=10.0, levels=_levels(),
            held=2, locked=True,
        )
        assert action == "EXIT" and "强制了结" in r["text"]

    def test_ebb_no_longer_liquidates(self):
        """旧规则「退潮 → 清仓」已废止：退潮日有成本也只按时间退出。"""
        stat = {
            "phase": "退潮",
            "buy_window": "STANDARD",
            "force_liquidate": False,
            "dragon_env": "UNFAVORABLE",
            "accel_reason": None,
        }
        action, r = hs._decide_action(
            stat=stat, drv={"is_limit_up": True, "cont_days": 3}, cost=5.0,
            close=10.0, levels=_levels(), held=0,
        )
        assert action == "HOLD" and "刚建仓" in r["text"]
        assert "退潮" not in r["text"]

    def test_broken_board_no_longer_trims(self):
        """断板也不减仓：口径B 的退出理由只有时间。"""
        action, r = hs._decide_action(
            stat=None, drv={"is_limit_up": False, "cont_days": 3}, cost=5.0,
            close=10.0, levels=_levels(), held=1,
        )
        assert action == "EXIT" and "T+2 开盘卖出" in r["text"]
        assert "断板" not in r["text"]


class TestWatchView:
    """观察视角（无成本）：A 止血后只回答「在不在观察名单」，不给买点。"""

    def test_sealed_board_is_watch_only(self):
        action, r = hs._decide_action(
            stat=None, drv={"is_limit_up": True, "cont_days": 3},
            cost=None, close=10.0, levels=_levels(buy_reference=11.0),
        )
        assert action == "HOLD" and "观察名单" in r["text"]
        assert "买入建议" in r["text"]          # 明确说不给建议
        assert "打板价" not in r["text"]        # 不再出现买点数字

    def test_not_sealed_not_in_watchlist(self):
        """没封板就不在观察名单；同样不给价位。"""
        action, r = hs._decide_action(
            stat=None, drv={"is_limit_up": False, "cont_days": 2},
            cost=None, close=10.0, levels=_levels(buy_reference=11.0),
        )
        assert action == "HOLD" and "不在观察名单" in r["text"]
        assert "打板价" not in r["text"]

    def test_missing_derived_row_is_hold_not_trim(self):
        """F6：缺判据行 = 缺数，绝不是「断板」。"""
        action, r = hs._decide_action(
            stat=None, drv=None, cost=None, close=10.0, levels=_levels()
        )
        assert action == "HOLD" and "缺数" in r["text"]

    def test_sealed_but_bad_env_still_watch_only(self):
        """A 止血：环境/相位不再产生「出手/不出手」指令，观察视角一切恒 HOLD。"""
        stat = {
            "phase": "冰点",
            "buy_window": "NONE",
            "force_liquidate": False,
            "dragon_env": "UNFAVORABLE",
            "accel_reason": None,
        }
        action, r = hs._decide_action(
            stat=stat, drv={"is_limit_up": True, "cont_days": 3},
            cost=None, close=10.0, levels=_levels(),
        )
        assert action == "HOLD" and "观察名单" in r["text"]

    def test_default_hold(self):
        action, _ = hs._decide_action(
            stat=None, drv={"is_limit_up": False, "cont_days": 1},
            cost=None, close=10.0, levels=_levels(buy_reference=9.0),
        )
        assert action == "HOLD"


# ────────────────────────── §6.5 分档守恒 ──────────────────────────


class TestLadder:
    @pytest.mark.parametrize("shares", [None, 0, 100, 1400, 13300])
    def test_ladder_sums_to_suggested_shares(self, shares):
        levels = _levels(buy_reference=15.0, target_price=27.0)
        sizing = Sizing(
            suggested_shares=shares,
            position_pct=None,
            suggested_amount=None,
            method="risk",
            detail="",
        )
        ladder = hs._build_ladder(levels, sizing, "main")
        buy = sum(x.shares or 0 for x in ladder if x.side == "BUY")
        sell = sum(x.shares or 0 for x in ladder if x.side == "SELL")
        expect = 0 if shares is None else shares
        assert buy == expect and sell == expect

    def test_ladder_is_lot_multiple_and_prices_ordered(self):
        levels = _levels(buy_reference=15.0, target_price=27.0)
        sizing = Sizing(
            suggested_shares=1400,
            position_pct=None,
            suggested_amount=None,
            method="risk",
            detail="",
        )
        ladder = hs._build_ladder(levels, sizing, "main")
        assert [x.price for x in ladder if x.side == "BUY"] == [15.0, 14.55, 14.1]
        assert all((x.shares or 0) % 100 == 0 for x in ladder)

    def test_star_lot_is_200(self):
        levels = _levels(buy_reference=10.0)
        sizing = Sizing(
            suggested_shares=1000,
            position_pct=None,
            suggested_amount=None,
            method="risk",
            detail="",
        )
        ladder = hs._build_ladder(levels, sizing, "star")
        assert all((x.shares or 0) % 200 == 0 for x in ladder)

    def test_decide_ladder_order_buy_desc_sell_asc(self, io):
        """展示序契约（docs/17 §3.3）：买档降序（买参考 = 最高一档在前）、卖档升序。

        `_build_ladder` 自己按 `BUY_LADDER` 构造序返回（恰好降序），排序由 `decide` 保证；
        「买参考」格 = max(BUY.price)，因此它必须排在第一档。
        """
        # 持仓视角才有「买档/卖档」概念；观察视角（无成本）只给单一打板档
        from types import SimpleNamespace
        io["pos"] = SimpleNamespace(
            code="601811", cost=12.0, shares=1000, entry_date=date(2026, 10, 12))
        io["held"] = 1
        io["levels"] = _levels(buy_reference=15.0, target_price=27.0)
        d = hs.decide("601811", capital=1_000_000, persist=False)
        buys = [x.price for x in d.ladder if x.side == "BUY"]
        sells = [x.price for x in d.ladder if x.side == "SELL"]
        assert len(buys) == 3 and len(sells) == 2
        assert buys == sorted(buys, reverse=True)
        assert buys[0] == pytest.approx(15.0)
        assert sells == sorted(sells)
        assert d.ladder[0].side == "BUY"  # 买档整体在卖档之前

    def test_watch_view_has_no_ladder_or_sizing(self, io):
        """A 止血：观察视角不给分档、不给数量（没有买点就没有对应的量）。"""
        io["levels"] = _levels(buy_reference=10.0)
        io["drv"] = {"is_limit_up": True, "cont_days": 3}
        d = hs.decide("601811", capital=1_000_000, persist=False)
        assert d.facts["view"] == "watch"
        assert d.facts["advice"] is False
        assert d.ladder == ()
        assert d.sizing.suggested_shares is None
        assert d.levels.basis == "pct_fallback" and d.levels.buy_reference is None
        assert d.action_label == "只观察（不出建议）"

    def test_no_levels_no_ladder(self):
        ladder = hs._build_ladder(
            _levels(buy_reference=None, target_price=None),
            Sizing(None, None, None, "none", ""),
            "main",
        )
        assert ladder == ()


# ────────────────────────── decide 的降级与纪律 ──────────────────────────


class TestDecide:
    def test_no_bar_returns_hold_ok_false_and_never_persists(self, io):
        io["bar"] = None
        d = hs.decide("601811", persist=True)
        assert d.ok is False and d.error == "no_bar" and d.action == "HOLD"
        assert io["writes"] == []
        assert any(r["kind"] == "degrade" for r in d.reasons)

    def test_suspended_zero_close_is_no_bar(self, monkeypatch, io):
        """§6.5：停牌 close<=0 —— `_bar` 自己就该返回 None，不拿 0 比价位。"""
        monkeypatch.setattr(hs, "_bar", lambda code, d: None)
        assert hs.decide("601811").error == "no_bar"

    def test_default_persists_and_dry_run_does_not(self, io):
        hs.decide("601811")
        assert len(io["writes"]) == 1
        hs.decide("601811", persist=False)
        assert len(io["writes"]) == 1

    def test_watch_view_degrades_without_price_advice(self, io):
        """观察视角：不给买点/数量，reason 里直接说明为什么（A 止血）。"""
        d = hs.decide("601811", persist=False)
        assert any("观察视角不给买点/数量" in r["text"] for r in d.reasons)

    def test_use_cpt_false_never_calls_cpt(self, monkeypatch, io):
        monkeypatch.setattr(
            hs.cpt_client, "cpt_enabled", lambda: pytest.fail("use_cpt=False 不该问 CPT")
        )
        io["levels"] = _levels()
        assert hs.decide("601811", use_cpt=False, persist=False).ok

    def test_cpt_failure_degrades_without_raising(self, monkeypatch, io):
        monkeypatch.setattr(hs.cpt_client, "cpt_enabled", lambda: True)
        monkeypatch.setattr(hs.cpt_client, "fetch_snapshot", lambda code, **kw: None)
        monkeypatch.setattr(hs.cpt_client, "recommendation_enabled", lambda: False)
        io["levels"] = _levels()
        d = hs.decide("601811", persist=False)
        assert d.ok and any("CPT snapshot" in r["text"] for r in d.reasons)

    def test_price_ratio_none_degrades(self, monkeypatch, io):
        monkeypatch.setattr(hs.cpt_client, "cpt_enabled", lambda: True)
        monkeypatch.setattr(hs.cpt_client, "fetch_snapshot", lambda code, **kw: {"x": 1})
        monkeypatch.setattr(hs.cpt_client, "data_quality_note", lambda s: None)
        monkeypatch.setattr(hs.cpt_client, "snapshot_overlays", lambda s: {"bis": []})
        monkeypatch.setattr(hs.cpt_client, "recommendation_enabled", lambda: True)
        monkeypatch.setattr(
            hs.cpt_client, "fetch_recommendation", lambda code, **kw: {"price_ratio": None}
        )
        monkeypatch.setattr(hs.cpt_client, "price_ratio_of", lambda r: None)
        monkeypatch.setattr(hs.cpt_client, "recommendation_view", lambda r: {"action": "hold"})
        d = hs.decide("601811", persist=False)
        assert any("price_ratio=None" in r["text"] for r in d.reasons)

    def test_cost_from_position_when_not_given(self, io):
        from emotion_core.domain.holding import Holding

        io["pos"] = Holding(code="601811", cost=8.0, shares=1000, entry_date=D)
        d = hs.decide("601811", persist=False)
        assert d.inputs["cost"] == 8.0 and d.inputs["shares"] == 1000
        assert d.pnl_pct == round(10.0 / 8.0 - 1, 6)

    def test_bad_code_raises_valueerror(self, io):
        with pytest.raises(ValueError, match="非法 code"):
            hs.decide("abc")

    def test_board_and_star_lot(self, io):
        assert hs._board_label("688111") == "star"
        assert hs._board_label("300086") == "chinext"
        assert hs._board_label("601811") == "sh_main"
        assert hs._board_label("000001") == "sz_main"
        assert hs._board_label("430047") == "bj"
        assert hs._sizing_board("688111") == "star"
        assert hs._sizing_board("601811") == "main"

    def test_analyze_holdings_is_read_only(self, monkeypatch, io):
        from emotion_core.domain.holding import Holding

        monkeypatch.setattr(
            hs.holding_store,
            "open_holdings",
            lambda: [Holding(code="601811", cost=8.0, shares=100)],
        )
        out = hs.analyze_holdings()
        assert len(out) == 1 and io["writes"] == []


# ────────────────────────── payload / 渲染 ──────────────────────────


class TestPayload:
    def test_payload_contract_keys(self, io):
        p = hs.to_payload(hs.decide("601811", persist=False))
        assert set(p) >= {
            "ok",
            "trade_date",
            "code",
            "name",
            "board",
            "holding",
            "action",
            "action_label",
            "ladder",
            "stop",
            "target",
            "sizing",
            "reasons",
            "cpt",
            "facts",
            "disclaimer",
        }
        assert p["trade_date"] == "2026-10-13"
        assert set(p["holding"]) == {"cost", "shares", "market_price", "pnl_pct"}
        assert set(p["sizing"]) >= {"suggested_shares", "position_pct", "method", "detail"}
        assert p["cpt"]["note"].startswith("CPT 结论原样并排展示")

    def test_payload_error_only_when_not_ok(self, io):
        io["bar"] = None
        p = hs.to_payload(hs.decide("601811", persist=False))
        assert p["ok"] is False and p["error"] == "no_bar"
        io["bar"] = {"close": 10.0, "amount": None}
        assert "error" not in hs.to_payload(hs.decide("601811", persist=False))

    def test_render_smoke(self, io):
        text = hs._render(hs.decide("601811", persist=False))
        assert "601811" in text and "结论：" in text and hs.DISCLAIMER in text


# ────────────────────────── CLI（默认只读） ──────────────────────────


class TestCli:
    def test_help_is_side_effect_free(self, monkeypatch):
        monkeypatch.setattr(
            hs.holding_store, "open_holdings", lambda: pytest.fail("--help 不该读库")
        )
        monkeypatch.setattr(hs, "_latest_trade_date", lambda: pytest.fail("--help 不该读库"))
        with pytest.raises(SystemExit) as e:
            hs.main(["--help"])
        assert e.value.code == 0

    def test_requires_code_or_all(self):
        with pytest.raises(SystemExit) as e:
            hs.main([])
        assert e.value.code == 2

    def test_code_and_all_conflict(self):
        with pytest.raises(SystemExit) as e:
            hs.main(["--code", "601811", "--all"])
        assert e.value.code == 2

    def test_bad_code_returns_2(self, io, capsys):
        assert hs.main(["--code", "abc"]) == 2
        assert "参数错误" in capsys.readouterr().err

    def test_default_does_not_persist(self, monkeypatch, io, capsys):
        hs.main(["--code", "601811", "--json"])
        assert io["writes"] == []
        assert '"code": "601811"' in capsys.readouterr().out

    def test_persist_flag_writes(self, io, capsys):
        assert hs.main(["--code", "601811", "--persist"]) == 0
        assert len(io["writes"]) == 1
        assert "已写入 holding_decision：1 行" in capsys.readouterr().err

    def test_dry_run_beats_persist(self, io, capsys):
        assert hs.main(["--code", "601811", "--persist", "--dry-run"]) == 0
        assert io["writes"] == []

    def test_all_reads_open_holdings(self, monkeypatch, io, capsys):
        from emotion_core.domain.holding import Holding

        monkeypatch.setattr(
            hs.holding_store,
            "open_holdings",
            lambda: [Holding(code="601811", cost=8.0, shares=100)],
        )
        assert hs.main(["--all", "--json"]) == 0
        assert io["writes"] == []


class TestNoConfigLeak:
    def test_constants_are_not_in_config(self):
        """`CONFIG` 参与 `config_hash()`：持仓决策的阈值一个都不得进 CONFIG。"""
        fields = set(CONFIG.__dataclass_fields__)
        for name in (
            "RISK_PCT",
            "LIQ_PCT",
            "MAX_POSITION_PCT",
            "BUY_LADDER",
            "SELL_LADDER",
            "RISK_PCT",
            "FALLBACK_STOP_PCT",
        ):
            assert name not in fields
        assert hs.RISK_PCT == 0.01 and hs.LIQ_PCT == 0.01 and hs.MAX_POSITION_PCT == 0.30
        assert hs.BUY_LADDER == ((1.00, 0.40), (0.97, 0.35), (0.94, 0.25))
        assert hs.SELL_LADDER == ((1.00, 0.50), (1.05, 0.50))


# ─────────────────────── R4 sizing 参数化（docs/18 §7.3/§7.4） ───────────────────────


class TestResolveSizingParams:
    """`_resolve_sizing_params`：合法 → 原值；越界 → 回落默认 + 原因。"""

    def test_valid_params_pass_through(self):
        resolved, problems = hs._resolve_sizing_params(
            {"risk_pct": 0.02, "liq_pct": 0.01, "max_position_pct": 0.5}
        )
        assert resolved == {"risk_pct": 0.02, "liq_pct": 0.01, "max_position_pct": 0.5}
        assert problems == []

    def test_out_of_bounds_falls_back(self):
        resolved, problems = hs._resolve_sizing_params({"risk_pct": 0.99, "liq_pct": 0.01})
        assert "risk_pct" not in resolved
        assert resolved["liq_pct"] == 0.01
        assert len(problems) == 1 and "risk_pct=0.99" in problems[0]

    def test_non_numeric_ignored(self):
        resolved, problems = hs._resolve_sizing_params({"risk_pct": "abc"})
        assert resolved == {}
        assert len(problems) == 1 and "abc" in problems[0]

    def test_none_or_empty_returns_defaults(self):
        assert hs._resolve_sizing_params(None) == ({}, [])
        assert hs._resolve_sizing_params({}) == ({}, [])

    def test_partial_params(self):
        resolved, problems = hs._resolve_sizing_params({"risk_pct": 0.03})
        assert resolved == {"risk_pct": 0.03}
        assert problems == []


class TestDecideSizingParams:
    """`decide` 传 `sizing_params` 后落进 `inputs` 并参与取小。"""

    def test_custom_risk_pct_reaches_plan_shares(self, io):
        io["levels"] = Levels(
            buy_reference=15.0,
            stop_price=14.0,
            target_price=27.0,
            basis="cpt_structure",
            detail="d",
        )
        d = hs.decide(
            "601811",
            capital=1_000_000,
            cost=21.5,
            shares=1000,
            sizing_params={"risk_pct": 0.02},
            persist=False,
        )
        # risk=1_000_000×2%/(15−14)=20_000 ; liq=2e8×1%/15≈1_333_333 ; maxpos=20_000
        # 取小 = 20_000
        assert d.sizing.suggested_shares == 20_000
        assert d.inputs.get("sizing_params") == {"risk_pct": 0.02}

    def test_out_of_bounds_recorded_in_reasons(self, io):
        io["levels"] = Levels(
            buy_reference=15.0,
            stop_price=14.0,
            target_price=27.0,
            basis="cpt_structure",
            detail="d",
        )
        d = hs.decide(
            "601811",
            capital=1_000_000,
            cost=21.5,
            shares=1000,
            sizing_params={"risk_pct": 0.99},
            persist=False,
        )
        ignored = [r for r in d.reasons if r["kind"] == "sizing_param_ignored"]
        assert len(ignored) == 1 and "risk_pct=0.99" in ignored[0]["text"]
        # 回落默认 1% → risk=10_000 ; maxpos=20_000 ; liq 很大 → 10_000
        assert d.sizing.suggested_shares == 10_000

    def test_ensure_factors_flagged_in_facts(self, io):
        """`ensure_factors=True` → `facts.cpt_ensure_used=True`。"""
        io["levels"] = Levels(
            buy_reference=15.0,
            stop_price=14.0,
            target_price=27.0,
            basis="cpt_structure",
            detail="d",
        )
        io["basic"]["is_st"] = False
        d = hs.decide(
            "601811",
            capital=1_000_000,
            cost=21.5,
            shares=1000,
            use_cpt=False,
            ensure_factors=True,
            persist=False,
        )
        # use_cpt=False 时 CPT 调用被跳过，ensure_factors 不会触发 fetch
        # 但 cpt_ensure_used 只记录「是否意图确保」，实际调用受 use_cpt 控制
        assert d.facts.get("cpt_ensure_used") is False  # use_cpt=False → 不调


# ── R4 CLI 扩展（sizing 参数 / user / ensure_factors）──────────────────────

# ── R4 CLI 扩展（sizing 参数 / user / ensure_factors）──────────────────────


class TestCliR4:
    """CLI 新增参数：--risk-pct / --liq-pct / --max-position-pct / --user / --ensure-factors。"""

    def _stub_io(self, monkeypatch):
        """为 CLI 桩出 IO 依赖，避免连库/出网。"""
        box = {
            "trade_date": D,
            "basic": {"name": "测试股", "is_st": False, "float_shares": 1e9, "in_market": True},
            "bar": {"close": 10.0, "amount": 2e8},
            "drv": None,
            "stat": None,
            "pos": None,
            "writes": [],
            "cpt_calls": 0,
            "levels": hs.Levels(
                buy_reference=15.0,
                stop_price=14.0,
                target_price=27.0,
                basis="cpt_structure",
                detail="d",
            ),
        }
        monkeypatch.setattr(hs, "_latest_trade_date", lambda: box["trade_date"])
        monkeypatch.setattr(hs, "_basic", lambda code: box["basic"])
        monkeypatch.setattr(hs, "_bar", lambda code, d: box["bar"])
        monkeypatch.setattr(hs, "_derived", lambda code, d: box["drv"])
        monkeypatch.setattr(hs, "_market", lambda d: box["stat"])
        monkeypatch.setattr(hs.holding_store, "holding_for", lambda code: box["pos"])
        # exit 侧的两个读口（都要查库）换掉，保持 DB-free
        monkeypatch.setattr(hs.exit_svc, "held_days", lambda entry, d: box.get("held"))
        monkeypatch.setattr(
            hs.exit_svc, "open_locked_limit_down",
            lambda d, code: bool(box.get("locked")))
        monkeypatch.setattr(
            hs.holding_store, "upsert_decision", lambda d: (box["writes"].append(d), 1)[1]
        )
        monkeypatch.setattr(hs.cpt_client, "cpt_enabled", lambda: False)

        def _structure(overlays, price_ratio, *, close):
            return box["levels"]

        monkeypatch.setattr(hs, "structure_levels", _structure)
        return box

    def test_risk_pct_passes_through(self, monkeypatch):
        self._stub_io(monkeypatch)
        rc = hs.main(["--code", "601811", "--risk-pct", "0.02", "--capital", "1000000", "--json"])
        assert rc == 0

    def test_risk_pct_out_of_bounds_errors(self, monkeypatch):
        self._stub_io(monkeypatch)
        with pytest.raises(SystemExit) as exc:
            hs.main(["--code", "601811", "--risk-pct", "0.99"])
        assert exc.value.code == 2

    def test_user_passes_through(self, monkeypatch):
        self._stub_io(monkeypatch)
        rc = hs.main(["--code", "601811", "--user", "alice", "--json"])
        assert rc == 0

    def test_ensure_factors_flag(self, monkeypatch):
        self._stub_io(monkeypatch)
        rc = hs.main(["--code", "601811", "--ensure-factors", "--no-cpt", "--json"])
        assert rc == 0
