"""价位计算（R3 持仓决策，docs/17 §6.2）：结构位优先，固定百分比兜底。

结构位算式**已于 2026-10-23 迁至 Emotion-CPT**（`emotion_cpt.snapshot.points`），
本模块与 CPT 的 `application/track_points.py` **共用同一份实现**：

- 此前这里是 CPT 的**手抄副本**（连行号引用都差 1 行），改一边不传播、无测试会红；
- 现在公式、常量、取整位数（2 位）**单一真源** ⇒
  同一只票在两个系统印出的买/卖点价**一致**（实测原先 36 个价里 29 个不一致）。

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

# ── 结构位常数与算式本体：单一真源在 Emotion-CPT（2026-10-23）──────────────
# 此前这里是**手抄** CPT track_points.py 的副本（连行号引用都已差 1 行），
# 改一边不传播、且没有任何测试会红。现改为 import 共享契约。
from emotion_cpt.contracts.pricing import (
    BI_LOOKBACK,  # noqa: F401 — re-export：holding_service 从这里取（勿删）
    BUY_BUFFER,
    PRICE_ROUND_DIGITS,
    SELL_BUFFER,
    STOP_LOSS_RATIO,
    ZHONGSHU_LOOKBACK,  # noqa: F401 — re-export：同上
)
from emotion_cpt.snapshot.points import structure_picks

from emotion_core.domain.holding import Levels

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
    if f != f or f <= 0:  # NaN 或非正
        return None
    return f


def _picks(overlays: dict, ratio: float) -> tuple[float | None, float | None]:
    """从 overlays 取 raw 域的 (买结构位, 卖结构位)；无候选返回 (None, None)。

    ⚠️ 算式本体在 Emotion-CPT（``emotion_cpt.snapshot.points``）——
    与 CPT 的 ``track_points`` 共用同一份实现，不再各写一份。
    """
    return structure_picks(overlays, ratio)


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
                buy_reference=buy,
                stop_price=round(stop, PRICE_ROUND_DIGITS) if stop is not None else None,
                target_price=sell,
                basis="cpt_structure",
                detail=(
                    f"结构位：中枢/笔 → raw（ratio={ratio}）"
                    f" × {BUY_BUFFER} / {SELL_BUFFER}，止损 × {STOP_LOSS_RATIO}"
                ),
                sell_reference=sell,
            )
        note = "CPT overlays 里没有可用的中枢/笔"
    elif ratio is None:
        note = "price_ratio 无效或未取到 → 结构位不可换算（不用 adj 域充数）"
    else:
        note = "CPT 不可用或无 overlays"

    if raw_close is None:
        return Levels(
            buy_reference=None,
            stop_price=None,
            target_price=None,
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
        sell_reference=round(raw_close * (1 + FALLBACK_TARGET_PCT), PRICE_ROUND_DIGITS),
    )
