"""`holding_store`：SQL 形状（幂等 upsert + jsonb 转型）与不写库路径。

守的事：
- `ok=False`（丙档 no_bar）**一行都不写** —— 库里不该出现「当天没有行情」的伪决策；
- `ON CONFLICT (trade_date, code)` 是决策历史的唯一守卫：当日重算覆盖、跨日保留；
- 四个 jsonb 列必须带 `::jsonb` 转型，否则 psycopg 把 Python 字符串当 text 塞进去直接报错；
- 参数顺序与列清单一一对应（21 列 / 21 参数），插错位不会报错但会静默脏数据；
- `holding_for` 无行返回 `None`（未持仓评估是正常场景，不是错误）。
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

import pandas as pd
import pytest

from emotion_core.data import holding_store as hs
from emotion_core.domain.holding import Decision, Ladder, Levels, Sizing

D = date(2026, 10, 13)


def _decision(*, ok: bool = True, cost: float | None = 8.0,
              shares: int | None = 1000) -> Decision:
    return Decision(
        code="601811", trade_date=D, action="HOLD", action_label="持有观察",
        market_price=21.14, pnl_pct=1.6,
        levels=Levels(buy_reference=15.029, stop_price=14.278,
                      target_price=27.205, basis="cpt_structure", detail="结构位"),
        sizing=Sizing(suggested_shares=13300, position_pct=0.1999,
                      suggested_amount=199900.0, method="risk+liq+maxpos", detail="取小"),
        ladder=(Ladder(side="BUY", price=15.029, pct=0.4, shares=5300, why="买参考"),),
        reasons=({"kind": "verdict", "text": "无触发条件"},),
        facts={"phase": "发酵"}, inputs={"cost": cost, "shares": shares, "capital": 1e6},
        cpt_status="structure_ready", name="新华文轩", board="sh_main", ok=ok,
    )


class _Rec:
    """`transaction()` 的最小替身：记录 (sql, params)，返回预置行。"""

    def __init__(self, row: tuple | None = (1,)) -> None:
        self._row = row
        self.calls: list[tuple[Any, Any]] = []

    def __enter__(self) -> _Rec:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: Any, params: Any = None) -> _Rec:
        self.calls.append((sql, params))
        return self

    def fetchone(self) -> tuple | None:
        return self._row


class TestUpsert:
    def test_ok_false_writes_nothing(self, monkeypatch):
        monkeypatch.setattr(hs, "transaction",
                            lambda: pytest.fail("丙档（ok=False）不该开事务"))
        assert hs.upsert_decision(_decision(ok=False)) == 0

    def test_sql_is_idempotent_and_jsonb_cast(self, monkeypatch):
        rec = _Rec()
        monkeypatch.setattr(hs, "transaction", lambda: rec)
        assert hs.upsert_decision(_decision()) == 1
        sql, params = rec.calls[0]
        assert "ON CONFLICT (trade_date, code) DO UPDATE" in sql
        assert "created_at       = now()" in sql        # 每次刷新
        assert sql.count("%s::jsonb") == 4              # ladder/reasons/facts/inputs
        assert len(params) == 21
        # 列顺序：trade_date, code, cost_price, shares, market_price, pnl_pct, ...
        assert params[:6] == (D, "601811", 8.0, 1000, 21.14, 1.6)
        assert params[6:11] == ("HOLD", "持有观察", "risk+liq+maxpos", 13300, 0.1999)
        assert params[11:16] == (199900.0, 15.029, 14.278, 27.205, "cpt_structure")
        assert json.loads(params[16]) == [
            {"side": "BUY", "price": 15.029, "pct": 0.4, "shares": 5300, "why": "买参考"}]
        assert json.loads(params[19]) == {"cost": 8.0, "shares": 1000, "capital": 1e6}
        assert params[20] == "structure_ready"

    def test_cost_and_shares_come_from_inputs_so_unheld_is_null(self, monkeypatch):
        rec = _Rec()
        monkeypatch.setattr(hs, "transaction", lambda: rec)
        hs.upsert_decision(_decision(cost=None, shares=None))
        _, params = rec.calls[0]
        assert params[2] is None and params[3] is None

    def test_no_row_returned_is_zero(self, monkeypatch):
        monkeypatch.setattr(hs, "transaction", lambda: _Rec(row=None))
        assert hs.upsert_decision(_decision()) == 0


class TestReads:
    def test_open_holdings_maps_rows(self, monkeypatch):
        df = pd.DataFrame([{"code": "1811", "entry_date": D, "entry_price": 8.0,
                            "shares": 1000, "note": None}])
        monkeypatch.setattr(hs, "query_df", lambda sql, params=(): df)
        out = hs.open_holdings()
        assert len(out) == 1
        assert out[0].code == "001811"          # zfill(6)
        assert out[0].cost == 8.0 and out[0].shares == 1000 and out[0].note == ""

    def test_open_holdings_skips_non_numeric_code(self, monkeypatch):
        df = pd.DataFrame([{"code": "BAD", "entry_date": D, "entry_price": 1.0,
                            "shares": 1, "note": ""}])
        monkeypatch.setattr(hs, "query_df", lambda sql, params=(): df)
        assert hs.open_holdings() == []

    def test_holding_for_none_when_empty(self, monkeypatch):
        monkeypatch.setattr(hs, "query_df", lambda sql, params=(): pd.DataFrame())
        assert hs.holding_for("601811") is None

    def test_holding_for_uses_code_param_and_limits_1(self, monkeypatch):
        seen: dict[str, Any] = {}

        def _q(sql: str, params: Any = ()) -> pd.DataFrame:
            seen["sql"], seen["params"] = sql, params
            return pd.DataFrame([{"code": "601811", "entry_date": D,
                                  "entry_price": 8.0, "shares": 1000, "note": ""}])

        monkeypatch.setattr(hs, "query_df", _q)
        h = hs.holding_for("601811")
        assert h is not None and h.code == "601811"
        assert seen["params"] == ("601811",)
        assert "status = 'OPEN'" in seen["sql"] and "LIMIT 1" in seen["sql"]
