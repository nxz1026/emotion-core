"""Lab 的纯计算层：收益口径、统计量、置信下界。

全部无 IO、无 DB、无网络 —— 可单测、可在回放里跑百万次。

口径（与 ADR 0002 B 轮 / ADR 0003 C3 一致）
- 入场：T+1 涨停价 = **T 日收盘 × 板比**（`utils.price.limit_up_price_cents`），
    「触及即成交」；
- 成交判据：T+1 `high >= 涨停价` 且当根 K 线 `volume > 0`（零量行不可成交）；
- 未成交记 `filled=False`，**在「每信号期望」里算 0**，另单列成交率；
- 卖出：T+2 开盘无条件卖；若 T+2 开盘即跌停封死，顺延 T+3 开盘，T+3 强制了结；
- 费：双边 0.15%（佣金 2×0.02% + 印花 0.05%）。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

from emotion_core.utils.price import limit_down_price_cents, limit_up_price_cents

FEE = 0.0015
Z_LCB = 1.645  # 单侧 95%


def wilson_lower(hits: int, n: int, z: float = Z_LCB) -> float | None:
    """胜率的 Wilson 下界（小样本惩罚）。n=0 返回 None。"""
    if n <= 0:
        return None
    p = hits / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def stats(rets: Sequence[float | None]) -> dict[str, Any]:
    """一组收益率（百分点，费后）→ n / 均值 / 中位 / 胜率 / 置信下界 / 最差。

    缺失（None/NaN）不计入 n，绝不补 0。n<30 时 `reliable=False`（不给分位数）。
    """
    vals = [float(v) for v in rets if v is not None and not _isnan(v)]
    n = len(vals)
    if n == 0:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "win_rate": None,
            "lcb": None,
            "worst": None,
            "reliable": False,
        }
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1)) if n > 1 else 0.0
    lcb = mean - Z_LCB * sd / math.sqrt(n) if n > 1 else mean
    wins = sum(1 for v in vals if v > 0)
    return {
        "n": n,
        "mean": round(mean, 3),
        "median": round(_median(vals), 3) if n >= 30 else None,
        "win_rate": round(100.0 * wins / n, 1),
        "lcb": round(lcb, 3),
        "worst": round(min(vals), 3),
        "reliable": n >= 30,
    }


def caliber_b_ret(
    *,
    code: str,
    base_close: float | None,
    t1_high: float | None,
    t1_close: float | None,
    t1_volume: float | None,
    t2_open: float | None,
    t2_close: float | None = None,
    t3_open: float | None = None,
) -> dict[str, Any]:
    """单条信号的「口径B 触及即成交」收益。

    买：T+1 挂 T 日收盘 × 板比 的限价，当日最高价够到即成交（成交价=限价）。
    卖：T+2 开盘无条件卖；若 T+2 **开盘与收盘都在跌停**（全天封死卖不掉）→
    顺延 T+3 开盘（`rolled=True`）。只看开盘不看收盘会把「开盘砸跌停又撬板」误判成卖不掉。

    Returns:
        {entry_px, exit_px, ret_pct, filled, rolled}；未成交 → ret_pct=0.0；
        成交但还不知道卖出价（T+2 尚未发生）→ 全部 None（右截断，不猜）。
    """
    empty = {"entry_px": None, "exit_px": None, "ret_pct": None, "filled": None, "rolled": None}
    if not base_close or base_close <= 0 or not t1_high:
        return empty
    # 涨停基数是 **T 日收盘**（T+1 的涨停价 = T 收盘 × 板比），不是 T−1 收盘
    pre_cents = int(round(base_close * 100))
    limit = limit_up_price_cents(pre_cents, code) / 100.0
    # 买限价挂在 T 日收盘之上：只有当日**最高价**够到才成交（不是最低价）
    filled = t1_high >= limit - 1e-9 and (t1_volume or 0) > 0
    if not filled:
        return {
            "entry_px": limit,
            "exit_px": None,
            "ret_pct": 0.0,
            "filled": False,
            "rolled": False,
        }

    if not t2_open or t2_open <= 0:
        return {**empty, "filled": True}
    exit_px, rolled = t2_open, False
    if t1_close and t1_close > 0 and t2_close:
        ld = limit_down_price_cents(int(round(t1_close * 100)), code) / 100.0
        if t2_open <= ld + 1e-9 and t2_close <= ld + 1e-9:
            if not t3_open or t3_open <= 0:
                return {**empty, "filled": True}
            exit_px, rolled = t3_open, True
    ret = (exit_px / limit - 1.0 - FEE) * 100.0
    return {
        "entry_px": round(limit, 3),
        "exit_px": round(exit_px, 3),
        "ret_pct": round(ret, 3),
        "filled": True,
        "rolled": rolled,
    }


def per_signal(
    rows: Iterable[dict[str, Any]], *, count_unfilled_as_zero: bool = True
) -> list[float]:
    """把逐条结果压成「每信号收益」序列：未成交按 0 计（ADR 0002 口径）。"""
    out: list[float] = []
    for r in rows:
        ret = r.get("ret_pct")
        if ret is None:
            continue
        if r.get("filled") is False and not count_unfilled_as_zero:
            continue
        out.append(float(ret))
    return out


def worst_slice(slices: dict[str, dict[str, Any]]) -> float | None:
    """各子样本均值里最差的那个（稳定性指标）。忽略 n<30 的子样本。"""
    vals = [
        v["mean"] for v in slices.values() if v.get("mean") is not None and (v.get("n") or 0) >= 30
    ]
    return round(min(vals), 3) if vals else None


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _isnan(v: Any) -> bool:
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True
