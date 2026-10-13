"""价位计算（R3 持仓决策，docs/17 §6.2）：结构位优先，固定百分比兜底。

结构位算式**逐条移植**自 CPT（Chan_Pattern_Trader）的 `application/track_points.py`，
**不 import**（`docs/shared-tables-contract.md` §5 明令两侧零代码依赖）：

- 买参考 = min(最近 `ZHONGSHU_LOOKBACK` 个中枢 `.low` ∪ 最近 `BI_LOOKBACK` 笔中
  `direction == -1` 的 `.low`) ÷ ratio × `BUY_BUFFER`          （track_points.py:182-221）
- 卖参考 = max(最近 `ZHONGSHU_LOOKBACK` 个中枢 `.high` ∪ 最近 `BI_LOOKBACK` 笔中
  `direction == +1` 的 `.high`) ÷ ratio × `SELL_BUFFER`        （track_points.py:224-262）
- 止损   = 买参考 × `STOP_LOSS_RATIO`                           （track_points.py:151-158）

**价格域**（本模块最容易出错的地方）：CPT 的 `overlays` 在**后复权（adj）域**，
`daily_bar.close` 在**不复权（raw）域**。`ratio = adj ÷ raw` 由 CPT 现算（逐股逐日不同）。
`ratio` 无效时**绝不**拿 adj 域的数当 raw 用（会把 1258 当成 8886），一律走百分比兜底
并在 `Levels.detail` 里写明原因。

守 F6：缺数不补 0、不猜。`close` 缺失时三个价位全 `None`。
"""
from __future__ import annotations

from typing import Any, Final

from emotion_core.domain.holding import Levels

# ── 结构位常数：与 CPT track_points.py 同值（移植口径，勿单方面改） ──────────
STOP_LOSS_RATIO: Final[float] = 0.95       # track_points.py:49
BUY_BUFFER: Final[float] = 1.02            # track_points.py:44
SELL_BUFFER: Final[float] = 0.97           # track_points.py:47
BI_LOOKBACK: Final[int] = 3                # track_points.py:_BI_LOOKBACK
ZHONGSHU_LOOKBACK: Final[int] = 2          # track_points.py:_ZHONGSHU_LOOKBACK

# ── 百分比兜底（只在结构位不可用时启用，决策 D3） ────────────────────────────
FALLBACK_BUY_PCT: Final[float] = 0.03
FALLBACK_TARGET_PCT: Final[float] = 0.05
FALLBACK_STOP_PCT: Final[float] = 0.08

#: 明细里给「哪些 action/direction 常量」用，便于与 CPT 对齐核对
_DIR_DOWN: Final[int] = -1
_DIR_UP: Final[int] = 1


def _num(v: Any) -> float | None:
    """可解析的**正**数 → float；None / NaN / 非数 / <=0 → None。

    `bool` 显式排除（`True` 会变成 1.0，是价格域里明显的脏数据）。
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f <= 0:                    # NaN 或非正
        return None
    return f


def _picks(overlays: dict, ratio: float) -> tuple[float | None, float | None]:
    """从 overlays 取 raw 域的 (买结构位, 卖结构位)；无候选返回 (None, None)。"""
    zs = overlays.get("zhongshus")
    bis = overlays.get("bis")
    if not isinstance(zs, list) or not isinstance(bis, list):
        return None, None
    lows: list[float] = []
    highs: list[float] = []
    for z in zs[-ZHONGSHU_LOOKBACK:]:
        if isinstance(z, dict):
            lo, hi = _num(z.get("low")), _num(z.get("high"))
            if lo is not None:
                lows.append(lo)
            if hi is not None:
                highs.append(hi)
    for b in bis[-BI_LOOKBACK:]:
        if not isinstance(b, dict):
            continue
        if b.get("direction") == _DIR_DOWN:
            lo = _num(b.get("low"))
            if lo is not None:
                lows.append(lo)
        elif b.get("direction") == _DIR_UP:
            hi = _num(b.get("high"))
            if hi is not None:
                highs.append(hi)
    # adj → raw 必须除以 ratio（ratio 已由调用方校验为正）
    buy = min(lows) / ratio * BUY_BUFFER if lows else None
    sell = max(highs) / ratio * SELL_BUFFER if highs else None
    return buy, sell


def structure_levels(
    overlays: dict | None, price_ratio: float | None, *, close: float | None
) -> Levels:
    """算出 raw 域的三个价位：结构位可用则用结构位，否则百分比兜底。

    `overlays` —— CPT snapshot 的 `overlays`（含 `zhongshus` / `bis`），已在 adj 域。
    `price_ratio` —— CPT recommendation 的 `price_ratio`（`adj ÷ raw`）；**可能为 None**。
    `close` —— `daily_bar.close`（raw 域）。
    """
    ratio = _num(price_ratio)
    raw_close = _num(close)

    if overlays and ratio is not None:
        buy, sell = _picks(overlays, ratio)
        if buy is not None or sell is not None:
            stop = buy * STOP_LOSS_RATIO if buy is not None else None
            return Levels(
                buy_reference=round(buy, 3) if buy is not None else None,
                stop_price=round(stop, 3) if stop is not None else None,
                target_price=round(sell, 3) if sell is not None else None,
                basis="cpt_structure",
                detail=(
                    f"结构位：中枢/笔 → raw（ratio={ratio}）"
                    f" × {BUY_BUFFER} / {SELL_BUFFER}，止损 × {STOP_LOSS_RATIO}"
                ),
                sell_reference=round(sell, 3) if sell is not None else None,
            )
        note = "CPT overlays 里没有可用的中枢/笔"
    elif ratio is None:
        note = "price_ratio 无效或未取到 → 结构位不可换算（不用 adj 域充数）"
    else:
        note = "CPT 不可用或无 overlays"

    if raw_close is None:
        return Levels(
            buy_reference=None, stop_price=None, target_price=None,
            basis="pct_fallback",
            detail=f"{note}；且无当日收盘价 → 不给价位（F6 不猜）",
        )
    return Levels(
        buy_reference=round(raw_close * (1 - FALLBACK_BUY_PCT), 3),
        stop_price=round(raw_close * (1 - FALLBACK_STOP_PCT), 3),
        target_price=round(raw_close * (1 + FALLBACK_TARGET_PCT), 3),
        basis="pct_fallback",
        detail=(
            f"{note}；改用百分比兜底"
            f"（收盘 {raw_close} × −{FALLBACK_BUY_PCT:.0%} / −{FALLBACK_STOP_PCT:.0%}"
            f" / +{FALLBACK_TARGET_PCT:.0%}）"
        ),
        sell_reference=round(raw_close * (1 + FALLBACK_TARGET_PCT), 3),
    )
