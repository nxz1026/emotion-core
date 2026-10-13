"""R3 持仓决策类型（docs/17）：`Holding` / `Levels` / `Sizing` / `Ladder` / `Decision`。

全是**冻结 dataclass、无 IO**；价位与数量分别由 `algorithms/levels.py` 与
`algorithms/sizing.py` 算出后填进来（那两个模块只吃已取好的数，不连库、不发请求）。

口径（docs/17 §0）：
- 所有价位 `Levels` / `Ladder.price` 都在 **raw（不复权）域**，可直接下单；
- `None` 一律表示「缺数」，**不表示 0**（F6：缺数不补 0）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

#: 方向 4 档。与 `signal` 表的 BUY/SECONDARY/SELL/RECOMMEND 无对应关系，
#: 也和 CPT 的 buy/sell/watch/hold 无对应关系 —— 本层是「持仓动作」，D7 不裁决。
ACTIONS: tuple[str, ...] = ("ADD", "HOLD", "TRIM", "EXIT")


@dataclass(frozen=True)
class Holding:
    """一笔在仓：`position` 行（status='OPEN'）或用户当次传入的成本+数量。"""
    code: str
    cost: float
    shares: int
    entry_date: date | None = None
    note: str = ""


@dataclass(frozen=True)
class Levels:
    """价位（raw 域）。`basis` 必填，`detail` 供人复核来源。"""
    buy_reference: float | None
    stop_price: float | None
    target_price: float | None
    basis: str                 # "cpt_structure" | "pct_fallback"
    detail: str                # 人读来源说明，会进 Decision.reasons
    #: SELL 档要用的卖参考（结构位给的是「中枢上沿 ×0.97」）。
    #: 与 target_price 同源，分开是语义区分：target 是「到目标减仓」的判定阈值。
    sell_reference: float | None = None


@dataclass(frozen=True)
class Sizing:
    """数量。`method` 说明哪些约束参与了取小。"""
    suggested_shares: int | None
    position_pct: float | None
    suggested_amount: float | None
    #: 参与的约束按顺序用 `+` 连接（`risk` / `liq` / `maxpos`，如 `risk+liq+maxpos`）；
    #: 退化档另有四个定名值：`maxpos_only`（只剩单票上限 → 不给股数）、
    #: `risk_only_pct`（未给总资金）、`liquidity_only`、`none`。
    method: str
    detail: str


@dataclass(frozen=True)
class Ladder:
    """分档：`side` 为 BUY/SELL；Σshares 应等于 Sizing.suggested_shares。"""
    side: str
    price: float
    pct: float
    shares: int | None
    why: str


@dataclass(frozen=True)
class Decision:
    """一次决策的完整输出（= `holding_decision` 一行的内存形态）。"""
    code: str
    trade_date: date
    action: str
    action_label: str
    market_price: float | None
    pnl_pct: float | None
    levels: Levels
    sizing: Sizing
    ladder: tuple[Ladder, ...] = ()
    reasons: tuple[dict, ...] = ()
    facts: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)
    cpt_status: str | None = None
    cpt_action: str | None = None
    cpt_divergence: str | None = None
    cpt_price_ratio: float | None = None
    name: str = ""
    board: str = "main"
    ok: bool = True
    error: str | None = None


#: 方向 → 中文标签（前端与落库共用一份，避免两处各写一套）
ACTION_LABELS: dict[str, str] = {
    "ADD": "加仓",
    "HOLD": "持有观察",
    "TRIM": "减仓",
    "EXIT": "清仓",
}
