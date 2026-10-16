"""数量计算（R3/R4 持仓决策，docs/17 §6.3 + docs/18 §7.3；决策 **D1**）：
固定风险 + 流动性上限 + 单票上限，取小。

三条约束（任一条的输入无效就**退出该条**，并在 `Sizing.method` / `detail` 里写明）：

| 约束 | 算式 | 含义 |
|---|---|---|
| 风险 `risk` | `capital × risk_pct ÷ (entry − stop)` | 打到止损时亏损 ≈ 总资金的 `risk_pct` |
| 流动性 `liq` | `amount × liq_pct ÷ entry` | 不超过当日成交额的 `liq_pct` |
| 单票上限 `maxpos` | `capital × max_position_pct ÷ entry` | 单票市值占总资金上限 |

`method` = 实际参与取小的约束名用 `+` 连接（如 `risk+liq+maxpos`）。

**D2**：`capital` 未传时不给绝对股数，只给风险反推的仓位比例上限
`risk_pct ÷ ((entry − stop) ÷ entry)`（再与 `max_position_pct` 取小），**不阻塞**。
**D9**：科创板按 `lot=200` 向下取整（真实规则是「200 起 + 1 股递增」，此处取保守近似）。
守 F6：`entry` / `stop` / `amount` 无效时不猜，对应约束直接不参与。**全部约束的输入
都无效时不给股数**：有总资金但只剩 `maxpos` → `maxpos_only`（只把上限写进 `detail`）；
没给总资金 → `risk_only_pct`（按风险反推比例）或 `liquidity_only`；`entry` 无效 → `none`。

`0` 与 `None` 的区别：`0` = 约束算出来确实是 0（例如成交额太小）；`None` = 缺输入、算不出。

R4 参数化（docs/18 §7.3）：三参数（`risk_pct` / `liq_pct` / `max_position_pct`）可由调用方
经 ``sizing_params`` 传入；越界时 ``holding_service`` 回落默认值并在 ``reasons`` 记
``sizing_param_ignored``，绝不抛。
"""

from __future__ import annotations

from typing import Any, Final

from emotion_core.domain.holding import Sizing

# ── 三约束常数（模块级 Final；**绝不进 `utils/config.py`**，见 docs/17 §1.3） ──
RISK_PCT: Final[float] = 0.01  # 单笔最大亏损占总资金
LIQ_PCT: Final[float] = 0.01  # 不超过当日成交额的 1%
MAX_POSITION_PCT: Final[float] = 0.30  # 单票占总资金上限

# ── R4 参数化合法区间（docs/18 §7.3；越界由 holding_service 回落默认） ──
SIZING_BOUNDS: Final[dict[str, tuple[float, float]]] = {
    "risk_pct": (0.001, 0.05),
    "liq_pct": (0.001, 0.05),
    "max_position_pct": (0.05, 1.0),
}

# ── 每手股数（D9） ──────────────────────────────────────────────────────────
LOT_MAIN: Final[int] = 100
LOT_STAR: Final[int] = 200  # 科创板起购 200 股（近似，见 D9）
STAR_BOARDS: Final[tuple[str, ...]] = ("star", "kcb", "688")


def _num(v: Any) -> float | None:
    """可解析的**正**数 → float；None / NaN / 非数 / <=0 → None。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f <= 0:
        return None
    return f


def lot_size(board: str) -> int:
    """该板块的每手股数。未知板块按主板 100（保守取小）。"""
    return LOT_STAR if board in STAR_BOARDS else LOT_MAIN


def _floor_lot(shares: float, lot: int) -> int:
    """向下取整到手数整数倍（负数/NaN 归 0）。"""
    if shares != shares or shares <= 0:
        return 0
    return int(shares // lot) * lot


def plan_shares(
    *,
    capital: float | None,
    entry: float | None,
    stop: float | None,
    amount: float | None,
    board: str = "main",
    risk_pct: float = RISK_PCT,
    liq_pct: float = LIQ_PCT,
    max_position_pct: float = MAX_POSITION_PCT,
    lot: int | None = None,
) -> Sizing:
    """按 D1 三条约束取小，给出建议股数 / 仓位比例 / 金额。

    `entry` 建议传 `Levels.buy_reference`（到价才买），缺则传当日 `close`。
    `stop` 传 `Levels.stop_price`。`amount` 是当日成交额（**元**）。
    `risk_pct` / `liq_pct` / `max_position_pct` 由调用方（holding_service）校验后传入；
    `lot` 缺省时按 board 自动取值。
    """
    resolved_lot = lot if lot is not None else lot_size(board)
    e = _num(entry)
    if e is None:
        return Sizing(
            suggested_shares=None,
            position_pct=None,
            suggested_amount=None,
            method="none",
            detail="无有效参考价 entry → 不给股数（F6 不猜）",
        )

    c = _num(capital)
    s = _num(stop)
    a = _num(amount)

    used: list[str] = []
    cand: dict[str, int] = {}

    if c is not None and s is not None and s < e:
        cand["risk"] = _floor_lot(c * risk_pct / (e - s), resolved_lot)
        used.append("risk")
    if a is not None:
        cand["liq"] = _floor_lot(a * liq_pct / e, resolved_lot)
        used.append("liq")
    if c is not None:
        cand["maxpos"] = _floor_lot(c * max_position_pct / e, resolved_lot)
        used.append("maxpos")

    shown = " ".join(f"{k}={v}" for k, v in cand.items())

    # ── D2：没给总资金 → 不给绝对股数，只给风险反推的比例上限 ──────────────
    if c is None:
        pct: float | None = None
        if s is not None and s < e:
            pct = min(RISK_PCT / ((e - s) / e), MAX_POSITION_PCT)
        if pct is not None:
            return Sizing(
                suggested_shares=None,
                position_pct=round(pct, 4),
                suggested_amount=None,
                method="risk_only_pct",
                detail=(
                    f"未给总资金 → 只给比例（风险 {RISK_PCT:.0%} ÷ 止损距离 "
                    f"{(e - s) / e:.2%}，与单票上限 {MAX_POSITION_PCT:.0%} 取小）"
                ),
            )
        return Sizing(
            suggested_shares=None,
            position_pct=None,
            suggested_amount=None,
            method="liquidity_only",
            detail=(
                "未给总资金且无有效止损（stop 缺或 ≥ entry）→ 无法按风险定仓；"
                "只给流动性口径" + (f"（{shown}）" if shown else "")
            ),
        )

    # ── 有总资金 ─────────────────────────────────────────────────────────
    # 走到这里 `c` 必有效 ⇒ `cand` 里必有 `maxpos`（所以没有「三条全无」的分支）。
    # 只剩单票上限：它是「上限」而非「建议」。没有止损或成交额支撑时不给股数，
    # 否则等于凭总资金拍一个 30% 仓位，与 D1「风险 + 流动性取小」的口径不符。
    if "risk" not in cand and "liq" not in cand:
        return Sizing(
            suggested_shares=None,
            position_pct=None,
            suggested_amount=None,
            method="maxpos_only",
            detail=(
                "只有单票上限可用（无有效止损、无成交额）→ 不给建议股数；"
                f"上限 = 总资金 ×{MAX_POSITION_PCT:.0%} ≈ {cand['maxpos']} 股"
            ),
        )
    n = min(cand.values())
    return Sizing(
        suggested_shares=n,
        position_pct=round(n * e / c, 4),
        suggested_amount=round(n * e, 2),
        method="+".join(used),
        detail=f"{shown} → 取小 {n}（每手 {resolved_lot} 股向下取整）",
    )
