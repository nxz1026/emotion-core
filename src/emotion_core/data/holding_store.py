"""持仓决策的库访问（R3，docs/17 §5.1）—— `position` 读 + `holding_decision` 幂等写。

分层：本模块在 `data` 层，**只做 IO，不含任何判定**（价位/数量/方向都在
`algorithms/` 与 `services/holding_service.py`）。

两条硬约束：
- SQL 只用 `%s` 占位符。psycopg 不允许别的转义（写 `LIKE '%wind%'` 会抛
  `ProgrammingError: only '%s', '%b', '%t' are allowed as placeholders, got '%w'`）。
- 写 `holding_decision` 走 `ON CONFLICT (trade_date, code) DO UPDATE`：当日重算覆盖、
  跨日保留 = 决策历史（docs/17 §3.2）。`pub` 与 `created_at` 每次刷新。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from emotion_core.domain.holding import Decision, Holding
from emotion_core.utils.db import query_df, transaction

log = logging.getLogger("emotion_core.data.holding_store")

#: 在仓快照（与 `services/position.current()` 同口径的 `status='OPEN'`，只取需要的列）。
_POSITION_COLS = "SELECT code, entry_date, entry_price, shares, note FROM position"
_OPEN_SQL = f"{_POSITION_COLS} WHERE status = 'OPEN' ORDER BY entry_date, code"
_BY_CODE_SQL = (
    f"{_POSITION_COLS} WHERE status = 'OPEN' AND code = %s"
    " ORDER BY entry_date DESC LIMIT 1"
)

_INSERT_SQL = """
INSERT INTO holding_decision (
    trade_date, code, cost_price, shares, market_price, pnl_pct,
    action, action_label, sizing_method, suggested_shares, position_pct,
    suggested_amount, buy_reference, stop_price, target_price, price_basis,
    ladder, reasons, facts, inputs, cpt_status
) VALUES (
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s
)
ON CONFLICT (trade_date, code) DO UPDATE SET
    cost_price       = EXCLUDED.cost_price,
    shares           = EXCLUDED.shares,
    market_price     = EXCLUDED.market_price,
    pnl_pct          = EXCLUDED.pnl_pct,
    action           = EXCLUDED.action,
    action_label     = EXCLUDED.action_label,
    sizing_method    = EXCLUDED.sizing_method,
    suggested_shares = EXCLUDED.suggested_shares,
    position_pct     = EXCLUDED.position_pct,
    suggested_amount = EXCLUDED.suggested_amount,
    buy_reference    = EXCLUDED.buy_reference,
    stop_price       = EXCLUDED.stop_price,
    target_price     = EXCLUDED.target_price,
    price_basis      = EXCLUDED.price_basis,
    ladder           = EXCLUDED.ladder,
    reasons          = EXCLUDED.reasons,
    facts            = EXCLUDED.facts,
    inputs           = EXCLUDED.inputs,
    cpt_status       = EXCLUDED.cpt_status,
    created_at       = now()
RETURNING id
"""


def _rows_to_holdings(df: Any) -> list[Holding]:
    out: list[Holding] = []
    for r in df.itertuples():
        code = str(r.code).strip().zfill(6)
        if not code.isdigit():
            log.warning("position 里有非法 code=%r，跳过", r.code)
            continue
        out.append(Holding(
            code=code,
            cost=float(r.entry_price),
            shares=int(r.shares),
            entry_date=r.entry_date,
            note=r.note or "",
        ))
    return out


def open_holdings() -> list[Holding]:
    """全部在仓（`position.status='OPEN'`），按开仓日升序。"""
    return _rows_to_holdings(query_df(_OPEN_SQL))


def holding_for(code: str) -> Holding | None:
    """某标的的在仓快照；没有则 None（**不是**错误：未持仓评估是正常场景）。"""
    df = query_df(_BY_CODE_SQL, (code,))
    rows = _rows_to_holdings(df)
    return rows[0] if rows else None


def upsert_decision(d: Decision) -> int:
    """写一行决策（幂等）。返回 1（写了）/ 0（`ok=False`，不落库）。

    成本与数量从 `d.inputs` 取（docs/17 §3.3 的 `inputs` 契约），
    这样「只做未持仓评估」时两列为 NULL。
    """
    if not d.ok:
        # 丙档（no_bar）不落库：库里不该出现「当天没有行情」的伪决策。
        return 0
    cost = d.inputs.get("cost")
    shares = d.inputs.get("shares")
    params = (
        d.trade_date, d.code,
        cost, shares,
        d.market_price, d.pnl_pct,
        d.action, d.action_label, d.sizing.method, d.sizing.suggested_shares,
        d.sizing.position_pct, d.sizing.suggested_amount,
        d.levels.buy_reference, d.levels.stop_price, d.levels.target_price,
        d.levels.basis,
        json.dumps([_ladder_dict(x) for x in d.ladder], ensure_ascii=False),
        json.dumps(list(d.reasons), ensure_ascii=False),
        json.dumps(d.facts, ensure_ascii=False),
        json.dumps(d.inputs, ensure_ascii=False),
        d.cpt_status,
    )
    with transaction() as conn:
        row = conn.execute(_INSERT_SQL, params).fetchone()
    return 1 if row is not None else 0


def _ladder_dict(x: Any) -> dict:
    return {"side": x.side, "price": x.price, "pct": x.pct,
            "shares": x.shares, "why": x.why}
