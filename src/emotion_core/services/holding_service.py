"""R3 持仓决策编排（docs/17 §5.3）—— 唯一对外入口。

数据流（一步一函数，便于单测逐段打桩）：

    stock_basic + daily_bar   → 名称 / 板块 / ST / 现价 / 当日成交额
    derived_bar + market_stat → 涨停 / 断板 / 退潮 / 环境
    CPT HTTP (D4)             → overlays(adj 域) + price_ratio（§4）
    levels.structure_levels   → 结构位 → raw 域；不可用则百分比兜底（§6.2）
    sizing.plan_shares        → 风险 / 流动性 / 单票上限取小（§6.3）
    §7.2 优先级表             → action（纯确定性，第一个命中即返回）
    §7.3 分档                 → ladder
    holding_store.upsert      → 可选落库（幂等）

纪律（docs/17 §1.3 / §13，改代码前先读）：

- **全部常数都在本模块（或 `algorithms/`）的模块级 `Final`，绝不进 `utils/config.py`**：
  `CONFIG` 是 `frozen dataclass` 且参与 `config_hash()`，加字段会改掉所有流水线的 hash。
- **默认只读**：`decide(persist=True)` 才写库，CLI 必须显式 `--persist`
  （2026-10-13 `python -m emotion_core.data.fill_stock_basic --help` 真跑全量写库的教训）。
- 缺数一律 `None` + `reasons[kind="degrade"]`，**不猜**（F6）。停牌 `close<=0` 视作 `no_bar`。
- CPT 结论只**并排展示**（`facts.cpt_*` / payload `cpt`），**不参与**方向判定（D7）。
- 本层**不产出交易决策**：不改 `presentation/trade_api.py` 的 `volume: 100`（另一个工单）。
- **A 止血（2026-10-21）**：观察视角（无成本）不再给买点/数量——口径B 打板的成交
  假设被证伪（触及即成交 → 每信号 −1.26%）；持仓视角只保留「建仓后第 N 个交易日
  开盘卖」的时间退出。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import date
from typing import Any, Final

from emotion_core.algorithms import exit as exit_svc
from emotion_core.algorithms.levels import (
    BI_LOOKBACK,
    BUY_BUFFER,
    FALLBACK_BUY_PCT,
    FALLBACK_STOP_PCT,
    FALLBACK_TARGET_PCT,
    SELL_BUFFER,
    STOP_LOSS_RATIO,
    ZHONGSHU_LOOKBACK,
    structure_levels,
)
from emotion_core.algorithms.sizing import (
    LIQ_PCT,
    MAX_POSITION_PCT,
    RISK_PCT,
    SIZING_BOUNDS,
    lot_size,
    plan_shares,
)
from emotion_core.data import holding_store
from emotion_core.domain.holding import ACTION_LABELS, Decision, Ladder, Levels, Sizing
from emotion_core.services import cpt_client
from emotion_core.services.cpt_client import CPT_ENSURE_TIMEOUT, DEFAULT_USER
from emotion_core.utils.db import connect_ro

log = logging.getLogger("emotion_core.services.holding_service")

#: 价位/数量常数住在 `algorithms/` 里；这里**只做再导出**，好让「所有阈值都在本模块或
#: algorithms 的模块级 Final」这条纪律有一处可查（docs/17 §8.2）。绝不复制成第二份。
__all__ = [
    "BUY_BUFFER",
    "BI_LOOKBACK",
    "FALLBACK_BUY_PCT",
    "FALLBACK_STOP_PCT",
    "FALLBACK_TARGET_PCT",
    "SELL_BUFFER",
    "STOP_LOSS_RATIO",
    "ZHONGSHU_LOOKBACK",
    "LIQ_PCT",
    "MAX_POSITION_PCT",
    "RISK_PCT",
    "BUY_LADDER",
    "SELL_LADDER",
    "DISCLAIMER",
    "decide",
    "analyze_holdings",
    "to_payload",
    "main",
]

DISCLAIMER: Final[str] = "结构状态与仓位的机械换算，不构成投资建议；不自动下单。"

#: 分档：`(价格系数, 资金占比)`，价格系数乘在买参考 / 目标价上（docs/17 §7.3）。
BUY_LADDER: Final[tuple[tuple[float, float], ...]] = ((1.00, 0.40), (0.97, 0.35), (0.94, 0.25))
SELL_LADDER: Final[tuple[tuple[float, float], ...]] = ((1.00, 0.50), (1.05, 0.50))

#: `no_bar` 时的占位价位（不给任何数，只说明原因）。
_NO_LEVELS: Final[Levels] = Levels(
    buy_reference=None,
    stop_price=None,
    target_price=None,
    basis="pct_fallback",
    detail="无当日行情（未上市 / 停牌超窗 / 尚未回补）→ 不给价位（F6 不猜）",
)
_NO_SIZING: Final[Sizing] = Sizing(
    suggested_shares=None,
    position_pct=None,
    suggested_amount=None,
    method="none",
    detail="无有效参考价 → 不给股数（F6 不猜）",
)


# ── R4 sizing 参数化校验（docs/18 §7.3/§7.4） ───────────────────────────
def _resolve_sizing_params(
    sizing_params: dict[str, Any] | None,
) -> tuple[dict[str, float], list[str]]:
    """校验 `sizing_params` 三字段（risk_pct / liq_pct / max_position_pct）。

    越界字段回落默认值并在 ``reasons`` 里用 ``sizing_param_ignored`` 提示。
    返回 ``(实际用的 dict, 需要追加的 degrade 文本列表)``。
    """
    if not isinstance(sizing_params, dict) or not sizing_params:
        return {}, []
    resolved: dict[str, float] = {}
    problems: list[str] = []
    for key, (lo, hi) in SIZING_BOUNDS.items():
        raw = sizing_params.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            problems.append(f"{key}={raw!r} 不是数字，已忽略")
            continue
        if not lo <= val <= hi:
            problems.append(f"{key}={val} 超出合法范围 [{lo}, {hi}]，已回落默认值")
            continue
        resolved[key] = val
    return resolved, problems


# ── SQL（只读；全部 %s 占位符） ──────────────────────────────────────
_BASIC_SQL = "SELECT name, is_st, float_shares, in_market FROM stock_basic WHERE code = %s"
_BAR_SQL = "SELECT close, amount FROM daily_bar WHERE code = %s AND date = %s"
_LATEST_SQL = "SELECT max(date) AS d FROM daily_bar"
_DERIVED_SQL = (
    "SELECT is_limit_up, cont_days, is_one_word, touched_limit, is_bomb"
    " FROM derived_bar WHERE code = %s AND date = %s"
)
_MARKET_SQL = (
    "SELECT phase, buy_window, force_liquidate, dragon_env, accel_reason"
    " FROM market_stat WHERE date = %s"
)


# ── 小工具 ──────────────────────────────────────────────────────────
def _norm_code(code: str) -> str:
    """归一化成 6 位数字串；非法抛 `ValueError`（HTTP 侧翻 400）。"""
    s = str(code or "").strip()
    if s.isdigit() and len(s) <= 6:
        s = s.zfill(6)
    if len(s) != 6 or not s.isdigit():
        raise ValueError(f"非法 code：{code!r}（需 6 位数字）")
    return s


def _board_label(code: str) -> str:
    """展示用板块标签（与 CPT `market.board` 同名口径）。"""
    if code.startswith("688"):
        return "star"
    if code.startswith("30"):
        return "chinext"
    if code.startswith("6"):
        return "sh_main"
    if code.startswith(("4", "8", "9")):
        return "bj"
    return "sz_main"


def _sizing_board(code: str) -> str:
    """给 `sizing.lot_size` 用的板块：科创板 200 股起，其余 100。"""
    return "star" if code.startswith("688") else "main"


def _f(v: Any) -> float | None:
    """DB 数值 → float；None / NaN / 不可解析 → None。0 是合法值（成交额可能为 0）。"""
    if v is None:
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return None if out != out else out  # NaN != NaN


def _latest_trade_date() -> date:
    """最新有 `daily_bar` 的交易日。

    **不用 `date.today()`**：非交易日 / 盘前拿不到当日 bar，用了就会一路 `no_bar`。
    """
    with connect_ro() as conn:
        row = conn.execute(_LATEST_SQL).fetchone()
    if row is None or row[0] is None:
        raise ValueError("daily_bar 为空，无法确定交易日")
    return row[0]


def _basic(code: str) -> dict[str, Any] | None:
    with connect_ro() as conn:
        row = conn.execute(_BASIC_SQL, (code,)).fetchone()
    if row is None:
        return None
    return {
        "name": row[0] or "",
        "is_st": bool(row[1]),
        "float_shares": _f(row[2]),
        "in_market": bool(row[3]),
    }


def _bar(code: str, trade_date: date) -> dict[str, float | None] | None:
    with connect_ro() as conn:
        row = conn.execute(_BAR_SQL, (code, trade_date)).fetchone()
    if row is None:
        return None
    close = _f(row[0])
    if close is None or close <= 0:
        # §6.5：停牌 close=0 视作 no_bar（不拿 0 去比价位）。
        return None
    return {"close": close, "amount": _f(row[1])}


def _derived(code: str, trade_date: date) -> dict[str, Any] | None:
    with connect_ro() as conn:
        row = conn.execute(_DERIVED_SQL, (code, trade_date)).fetchone()
    if row is None:
        return None
    return {
        "is_limit_up": bool(row[0]),
        "cont_days": int(row[1] or 0),
        "is_one_word": bool(row[2]),
        "touched_limit": bool(row[3]),
        "is_bomb": bool(row[4]),
    }


def _market(trade_date: date) -> dict[str, Any] | None:
    with connect_ro() as conn:
        row = conn.execute(_MARKET_SQL, (trade_date,)).fetchone()
    if row is None:
        return None
    return {
        "phase": row[0],
        "buy_window": row[1],
        "force_liquidate": bool(row[2]),
        "dragon_env": row[3],
        "accel_reason": row[4],
    }


# ── 方向判定（docs/17 §7.2，纯确定性） ───────────────────────────────
def _decide_action(
    *,
    stat: dict[str, Any] | None,
    drv: dict[str, Any] | None,
    cost: float | None,
    close: float,
    levels: Levels,
    held: int | None = None,
    locked: bool = False,
) -> tuple[str, dict[str, Any]]:
    """两视角分离：有成本＝持仓视角（时间退出），无成本＝观察视角（只判断在不在名单）。

    旧规则「退潮清仓 → 止损 → 断板减仓 → 目标减仓 → 到买点加仓」已废止：它把
    「有没有持仓」混进了方向语义（同一只票，填了成本就叫清仓、不填就叫加仓），
    且三条触发都建在被证伪的前提上（退潮日次日 +3.53%/73%；持有到断板 −1.90%/38%；
    结构位×0.95 的「止损」在涨停股 T+1 根本对应不上可成交价）。

    现在：
    - 持仓视角：只按**建仓后经过的交易日数**退出（见 algorithms/exit.py）；
    - 观察视角（A 止血 2026-10-21）：**不再给买点**。口径B 打板的成交假设被证伪
      （触及即成交 → 每信号 −1.26%，炸板占成交 30% 且均亏 15%），这个视角只回答
      「今天它在不在观察名单里」，永远返回 HOLD，不返回 ADD、不给价格。
    """
    if cost is not None:
        if held is None:
            # 有成本但查不到建仓日（position 无 OPEN 记录且调用方没给）→ 算不出
            # 「建仓后第几个交易日」，时间退出就无从判定。不许猜。
            return "HOLD", {
                "kind": "degrade",
                "text": (
                    "有成本但查不到建仓日（position 无 OPEN 记录）→ 算不出持有"
                    "交易日数，不给卖出指令；请补 entry_date"
                ),
                "src": "caliber_b / exit.held_days",
            }
        n = held
        if n <= 0:
            return "HOLD", {
                "kind": "verdict",
                "text": "今天刚建仓（口径B：建仓当日不卖，明天开盘再卖）",
                "src": "caliber_b / exit.py",
            }
        if n >= exit_svc.FORCE_AFTER_DAYS:
            return "EXIT", {
                "kind": "verdict",
                "text": f"强制了结（口径B：已持有 {n} 个交易日，超过 T+3）",
                "src": "caliber_b / exit.py",
            }
        if locked:
            return "HOLD", {
                "kind": "verdict",
                "text": "开盘跌停封死，今天卖不掉 → 顺延到下一交易日（届时强制了结）",
                "src": "caliber_b / exit.py",
            }
        return "EXIT", {
            "kind": "verdict",
            "text": "T+2 开盘卖出（口径B：alpha 全在隔夜，持有越久越亏）",
            "src": "caliber_b / exit.py",
        }
    # ── 观察视角（无成本）：只回答「在不在观察名单」，不给买点 ──
    if drv is None:
        return "HOLD", {
            "kind": "verdict",
            "text": "当日无判据行（缺数）→ 不评估",
            "src": "F6：缺数不当断板",
        }
    if drv["is_limit_up"]:
        return "HOLD", {
            "kind": "verdict",
            "text": (
                f"已封板 {close:.2f}（{drv['cont_days']} 板）→ 进观察名单，"
                "但本系统不出买入建议（口径B 打板实测 −1.26%/信号）"
            ),
            "src": "A 止血 / ADR 0002 复核",
        }
    return "HOLD", {
        "kind": "verdict",
        "text": "今天没封板 → 不在观察名单",
        "src": "A 止血 / ADR 0002 复核",
    }


# ── 分档（docs/17 §7.3 + §6.5） ─────────────────────────────────────
def _alloc(shares: int | None, pcts: list[float], lot: int) -> list[int | None]:
    """按 pct 分配股数，**逐档向 lot 取整**，差额全部补到最后一档。

    §6.5 要求「Σ各档 == suggested_shares」。取整损失集中到末档后，
    `suggested_shares` 本身是 lot 的整数倍，故示例 `1400 → 500/400/500` 与 docs/17 §3.3 一致。
    """
    if shares is None:
        return [None] * len(pcts)
    out = [int(shares * p / lot) * lot for p in pcts]
    tail = shares - sum(out[:-1]) if len(out) > 1 else shares
    if tail < 0:  # 取整把前面凑多了（极端小单）→ 退回不取整的按比例分
        out = [int(shares * p) for p in pcts]
        tail = shares - sum(out[:-1]) if len(out) > 1 else shares
    if out:
        out[-1] = max(tail, 0)
    return out


def _build_ladder(levels: Levels, sizing: Sizing, board: str) -> tuple[Ladder, ...]:
    """买档（买参考 × 系数）+ 卖档（目标 × 系数）；价格升序输出由调用方保证。"""
    lot = lot_size(board)
    out: list[Ladder] = []
    buy_ref = levels.buy_reference
    target = levels.target_price
    buys = list(BUY_LADDER) if buy_ref is not None else []
    sells = list(SELL_LADDER) if target is not None else []
    if buy_ref is not None and buys:
        bp = [p for _, p in buys]
        tot = sum(bp)
        shares = _alloc(sizing.suggested_shares, [p / tot for p in bp], lot)
        for (f, p), sh in zip(buys, shares, strict=True):
            out.append(
                Ladder(
                    side="BUY",
                    price=round(buy_ref * f, 3),
                    pct=round(p / tot, 4),
                    shares=sh,
                    why=f"买参考 {buy_ref:.2f} ×{f:.2f}",
                )
            )
    if target is not None and sells:
        sp = [p for _, p in sells]
        tot = sum(sp)
        shares = _alloc(sizing.suggested_shares, [p / tot for p in sp], lot)
        for (f, p), sh in zip(sells, shares, strict=True):
            out.append(
                Ladder(
                    side="SELL",
                    price=round(target * f, 3),
                    pct=round(p / tot, 4),
                    shares=sh,
                    why=f"目标 {target:.2f} ×{f:.2f}",
                )
            )
    return tuple(out)


def _inputs(
    *, cost: float | None, shares: int | None, capital: float | None, trade_date: date
) -> dict[str, Any]:
    return {
        "cost": cost,
        "shares": shares,
        "capital": capital,
        "risk_pct": RISK_PCT,
        "liq_pct": LIQ_PCT,
        "max_position_pct": MAX_POSITION_PCT,
        "as_of": trade_date.isoformat(),
    }


def _ladder_scan(ladder: tuple[Ladder, ...]) -> list[dict[str, Any]]:
    return [
        {"side": x.side, "price": x.price, "pct": x.pct, "shares": x.shares, "why": x.why}
        for x in ladder
    ]


# ── 主流程 ──────────────────────────────────────────────────────────
def decide(
    code: str,
    *,
    cost: float | None = None,
    shares: int | None = None,
    capital: float | None = None,
    as_of: date | None = None,
    use_cpt: bool = True,
    persist: bool = True,
    # ── R4 扩展（docs/18 §7.4） ─────────────────────────────────────────
    sizing_params: dict[str, Any] | None = None,
    ensure_factors: bool = False,
    cpt_user: str = DEFAULT_USER,
) -> Decision:
    """单标的持仓决策（docs/17 §5.3 的 10 步 + docs/18 §7.4 扩展，顺序不得调换）。

    `sizing_params` 可传 ``{"risk_pct", "liq_pct", "max_position_pct"}`` 任意子集；
    越界字段回落默认值并在 ``reasons`` 记 ``sizing_param_ignored``。
    `ensure_factors=True`` 会调 ``cpt_client.fetch_snapshot(..., ensure_factors=True)``
    （仅 CPT 显式开启 factor 按需刷新；UI 配合「运行提示」展示，见 docs/18 §7.5）。
    ``cpt_user`` 是 CPT 关注列表的 user_id（默认 ``"default"``）。
    """
    norm = _norm_code(code)
    trade_date = as_of or _latest_trade_date()
    inputs = _inputs(cost=cost, shares=shares, capital=capital, trade_date=trade_date)
    board = _board_label(norm)
    reasons: list[dict[str, Any]] = []

    basic = _basic(norm)
    name = (basic or {}).get("name") or ""
    bar = _bar(norm, trade_date)
    if bar is None:
        # 丙档：不猜价、不落库。
        return Decision(
            code=norm,
            trade_date=trade_date,
            action="HOLD",
            action_label=ACTION_LABELS["HOLD"],
            market_price=None,
            pnl_pct=None,
            levels=_NO_LEVELS,
            sizing=_NO_SIZING,
            ladder=(),
            reasons=(
                {
                    "kind": "degrade",
                    "text": "当日无日线（未上市 / 停牌超窗 / 尚未回补）→ 不给决策",
                    "src": "docs/17 §5.4 丙档",
                },
            ),
            facts={"board": board, "is_st": bool((basic or {}).get("is_st"))},
            inputs=inputs,
            name=name,
            board=board,
            ok=False,
            error="no_bar",
        )

    close = bar["close"]
    amount = bar["amount"]
    assert close is not None  # _bar 已保证 > 0

    # 4) 成本/数量缺省时回落到 position 表（analyze_holdings 场景由调用方先填）。
    #    W10′：无论填不填成本都取一次在仓快照——持仓视角要靠 entry_date 数持有天数。
    pos = holding_store.holding_for(norm)
    if pos is not None and (cost is None or shares is None):
        if cost is None:
            cost = pos.cost
        if shares is None:
            shares = pos.shares
        inputs["cost"], inputs["shares"] = cost, shares
        reasons.append(
            {
                "kind": "input",
                "text": f"成本/数量取自 position 在仓快照（{pos.entry_date}）",
                "src": "position.status='OPEN'",
            }
        )

    # 4.5) R4 sizing 参数化校验（docs/18 §7.3/§7.4）。
    resolved_sizing, sizing_problems = _resolve_sizing_params(sizing_params)
    inputs["sizing_params"] = dict(resolved_sizing)
    for p in sizing_problems:
        reasons.append(
            {"kind": "sizing_param_ignored", "text": p, "src": "docs/18 §7.3 SIZING_BOUNDS"}
        )

    drv = _derived(norm, trade_date)
    stat = _market(trade_date)

    # 5) CPT（D4）：任一失败只记 degrade，绝不中断。
    overlays = None
    price_ratio = None
    cpt_view: dict[str, Any] | None = None
    cpt_ensure_used = False
    if use_cpt and cpt_client.cpt_enabled():
        snap_timeout = CPT_ENSURE_TIMEOUT if ensure_factors else cpt_client.CPT_TIMEOUT
        snap = cpt_client.fetch_snapshot(
            norm,
            timeout=snap_timeout,
            ensure_factors=True if ensure_factors else None,
            user=cpt_user,
        )
        cpt_ensure_used = ensure_factors
        if snap is None:
            reasons.append(
                {"kind": "degrade", "text": "CPT snapshot 取数失败 → 价位走兜底", "src": "cpt:8010"}
            )
        else:
            note = cpt_client.data_quality_note(snap)
            if note:
                reasons.append({"kind": "degrade", "text": note, "src": "cpt:8010 data_quality"})
            overlays = cpt_client.snapshot_overlays(snap)
        if cpt_client.recommendation_enabled():
            rec = cpt_client.fetch_recommendation(norm, user=cpt_user)
            if rec is None:
                reasons.append(
                    {
                        "kind": "degrade",
                        "text": "CPT recommendation 取数失败 → 结构位不可换算",
                        "src": "cpt:8010",
                    }
                )
            else:
                price_ratio = cpt_client.price_ratio_of(rec)
                cpt_view = cpt_client.recommendation_view(rec)
                if price_ratio is None:
                    reasons.append(
                        {
                            "kind": "degrade",
                            "text": "price_ratio=None（快照与 daily_bar 不在同一天）→ 结构位不可换算",
                            "src": "docs/17 §6.1",
                        }
                    )

    # 6) 价位 → 7) 数量（R4 sizing 参数化，docs/18 §7.4）。
    #    A 止血（2026-10-21）：**观察视角（无成本）不再算价位与数量**——口径B 打板
    #    的成交假设被证伪（触及即成交 → 每信号 −1.26%），给出「买点 + 数量」就是
    #    负期望的操作建议。观察视角只回档位与是否在观察名单，levels/sizing 留空。
    if cost is not None:
        levels = structure_levels(overlays, price_ratio, close=close)
        reasons.append(
            {"kind": "structure", "text": levels.detail, "src": f"levels.basis={levels.basis}"}
        )
        sizing = plan_shares(
            capital=capital,
            entry=levels.buy_reference or close,
            stop=levels.stop_price,
            amount=amount,
            board=_sizing_board(norm),
            risk_pct=resolved_sizing.get("risk_pct", RISK_PCT),
            liq_pct=resolved_sizing.get("liq_pct", LIQ_PCT),
            max_position_pct=resolved_sizing.get("max_position_pct", MAX_POSITION_PCT),
        )
        if sizing.detail:
            reasons.append(
                {"kind": "sizing", "text": sizing.detail, "src": f"method={sizing.method}"}
            )
        if capital is None:
            reasons.append(
                {
                    "kind": "degrade",
                    "text": "未给总资金 → 只给仓位比例，不给绝对股数",
                    "src": "docs/17 D2",
                }
            )
    else:
        levels = _NO_LEVELS
        sizing = _NO_SIZING
        reasons.append(
            {
                "kind": "degrade",
                "text": (
                    "观察视角不给买点/数量（A 止血：口径B 打板按触及即成交实测"
                    "每信号 −1.26%，成交后 −2.01%、胜率 50%）"
                ),
                "src": "ADR 0002 复核 / _audit/probe_fill.py",
            }
        )

    # 8) 方向（W10′：持仓视角看持有天数 + 开盘是否跌停封死；观察视角看当日是否封板）
    held = (
        exit_svc.held_days(pos.entry_date, trade_date)
        if pos is not None and getattr(pos, "entry_date", None) is not None
        else None
    )
    locked = exit_svc.open_locked_limit_down(trade_date, norm)
    action, verdict = _decide_action(
        stat=stat, drv=drv, cost=cost, close=close, levels=levels, held=held, locked=locked
    )
    reasons.insert(0, verdict)
    if stat:
        reasons.append(
            {
                "kind": "market",
                "text": (
                    f"市场 phase={stat['phase']}，环境={stat['dragon_env']}，"
                    f"buy_window={stat['buy_window']}"
                    + (f"（{stat['accel_reason']}）" if stat["accel_reason"] else "")
                ),
                "src": f"market_stat {trade_date}",
            }
        )
    if cpt_view:
        reasons.append(
            {
                "kind": "structure",
                "text": f"CPT 结论并排展示：action={cpt_view.get('action')} "
                f"status={cpt_view.get('status')}（D7：不参与本层判定）",
                "src": "cpt:8010",
            }
        )
    if (basic or {}).get("is_st"):
        reasons.append(
            {
                "kind": "degrade",
                "text": "is_st=true：日线涨停幅度 5% 的口径缺口未修"
                "（derive.py:39 只有板块口径，无 ST 分支）",
                "src": "docs/17 §6.5",
            }
        )

    # 9) 分档 + facts
    ladder = _build_ladder(levels, sizing, _sizing_board(norm))
    # 展示序（docs/17 §3.3 示例）：买档按价格**降序**（买参考 = 最高一档在前，
    # 越跌越靠后），卖档按价格升序（目标在前）。前端「买参考」= buys[0] 依赖此序。
    ladder = tuple(
        sorted(ladder, key=lambda x: (x.side != "BUY", -x.price if x.side == "BUY" else x.price))
    )
    pnl_pct = round(close / cost - 1, 6) if cost else None
    facts: dict[str, Any] = {
        "board": board,
        "is_st": bool((basic or {}).get("is_st")),
        "float_shares": (basic or {}).get("float_shares"),
        "amount_yi": round(amount / 1e8, 4) if amount is not None else None,
        "phase": (stat or {}).get("phase"),
        "force_liquidate": (stat or {}).get("force_liquidate"),
        "dragon_env": (stat or {}).get("dragon_env"),
        "buy_window": (stat or {}).get("buy_window"),
        "is_limit_up": (drv or {}).get("is_limit_up"),
        "cont_days": (drv or {}).get("cont_days"),
        "cpt_status": (cpt_view or {}).get("status"),
        "cpt_action": (cpt_view or {}).get("action"),
        "cpt_divergence": (cpt_view or {}).get("divergence_status"),
        "cpt_price_ratio": price_ratio,
        "levels_basis": levels.basis,
        "cpt_ensure_used": cpt_ensure_used,
        # 两视角可审计——持仓视角给持有天数/是否卖不掉；观察视角只给名单状态
        "view": "position" if cost is not None else "watch",
        "held_days": held,
        "open_locked": locked,
        "advice": cost is not None,  # A 止血：观察视角不发任何买卖指令
    }
    d = Decision(
        code=norm,
        trade_date=trade_date,
        action=action,
        action_label=(
            # 观察视角（无持仓）：动作恒为 HOLD，标签说明「不出建议」
            "只观察（不出建议）" if cost is None else ACTION_LABELS.get(action, action)
        ),
        market_price=close,
        pnl_pct=pnl_pct,
        levels=levels,
        sizing=sizing,
        ladder=ladder,
        reasons=tuple(reasons),
        facts=facts,
        inputs=inputs,
        cpt_status=(cpt_view or {}).get("status"),
        cpt_action=(cpt_view or {}).get("action"),
        cpt_divergence=(cpt_view or {}).get("divergence_status"),
        cpt_price_ratio=price_ratio,
        name=name,
        board=board,
    )
    # 10) 落库（默认 True；`ok=False` 时 upsert 自己会跳过）
    if persist:
        holding_store.upsert_decision(d)
    return d


def analyze_holdings(
    *,
    capital: float | None = None,
    as_of: date | None = None,
    use_cpt: bool = True,
    sizing_params: dict[str, Any] | None = None,
    cpt_user: str = DEFAULT_USER,
) -> list[Decision]:
    """全部在仓（`position.status='OPEN'`）逐个决策，不落库（只读报告）。"""
    return [
        decide(
            h.code,
            cost=h.cost,
            shares=h.shares,
            capital=capital,
            as_of=as_of,
            use_cpt=use_cpt,
            persist=False,
            sizing_params=sizing_params,
            cpt_user=cpt_user,
        )
        for h in holding_store.open_holdings()
    ]


# ── 输出 ────────────────────────────────────────────────────────────
def to_payload(d: Decision) -> dict[str, Any]:
    """`Decision` → HTTP/CLI 的 JSON（docs/17 §7.1）。"""
    payload: dict[str, Any] = {
        "ok": d.ok,
        "trade_date": d.trade_date.isoformat(),
        "code": d.code,
        "name": d.name,
        "board": d.board,
        "holding": {
            "cost": d.inputs.get("cost"),
            "shares": d.inputs.get("shares"),
            "market_price": d.market_price,
            "pnl_pct": d.pnl_pct,
        },
        "inputs": {
            "cost": d.inputs.get("cost"),
            "shares": d.inputs.get("shares"),
            "capital": d.inputs.get("capital"),
            "sizing_params": d.inputs.get("sizing_params"),
            "cpt_ensure_used": d.facts.get("cpt_ensure_used"),
        },
        "action": d.action,
        "action_label": d.action_label,
        "ladder": _ladder_scan(d.ladder),
        "stop": {"price": d.levels.stop_price, "basis": d.levels.basis, "detail": d.levels.detail},
        "target": {
            "price": d.levels.target_price,
            "basis": d.levels.basis,
            "detail": d.levels.detail,
        },
        "sizing": {
            "suggested_shares": d.sizing.suggested_shares,
            "suggested_amount": d.sizing.suggested_amount,
            "position_pct": d.sizing.position_pct,
            "method": d.sizing.method,
            "detail": d.sizing.detail,
        },
        "reasons": list(d.reasons),
        "cpt": {
            "available": d.cpt_status is not None or d.cpt_action is not None,
            "action": d.cpt_action,
            "status": d.cpt_status,
            "divergence_status": d.cpt_divergence,
            "price_ratio": d.cpt_price_ratio,
            "note": "CPT 结论原样并排展示，不参与本系统的方向判定（D7）",
        },
        "facts": d.facts,
        "disclaimer": DISCLAIMER,
    }
    if not d.ok:
        payload["error"] = d.error
    return payload


def _render(d: Decision) -> str:
    """人读输出（CLI 默认）。"""
    p = to_payload(d)
    if not d.ok:
        return f"{d.code} {d.name or ''} {p['trade_date']}：不可用（{d.error}）"
    h = p["holding"]
    lines = [
        f"{d.code} {d.name}（{d.board}） {p['trade_date']}",
        f"  现价 {d.market_price}  成本 {h['cost']}  浮盈 "
        + (f"{d.pnl_pct:.2%}" if d.pnl_pct is not None else "—")
        + f"  持仓 {h['shares']}",
        f"  结论：{d.action_label}（{d.action}）",
        f"  买参考 {d.levels.buy_reference}  止损 {d.levels.stop_price}"
        f"  目标 {d.levels.target_price}  [{d.levels.basis}]",
        f"  数量：{d.sizing.suggested_shares} 股 / 仓位 "
        + (f"{d.sizing.position_pct:.2%}" if d.sizing.position_pct is not None else "—")
        + f"  [{d.sizing.method}]",
    ]
    for x in d.ladder:
        lines.append(f"    {x.side} {x.price} {x.pct:.0%} {x.shares} 股 — {x.why}")
    for r in d.reasons:
        lines.append(f"    · [{r.get('kind')}] {r.get('text')}")
    lines.append(f"  {DISCLAIMER}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI（docs/17 §8.1）。**默认只读**：不带 `--persist` 不写任何表。"""
    ap = argparse.ArgumentParser(
        prog="python -m emotion_core.services.holding_service",
        description="R3 持仓决策：给定标的（+成本/数量/总资金）算买卖价位与股数。默认只读。",
    )
    ap.add_argument("--code", help="6 位标的代码（与 --all 二选一）")
    ap.add_argument("--all", action="store_true", help="对全部 OPEN 持仓逐个决策")
    ap.add_argument("--cost", type=float, help="持仓成本价")
    ap.add_argument("--shares", type=int, help="持仓数量（股）")
    ap.add_argument("--capital", type=float, help="总资金（元）；不传只给仓位比例")
    ap.add_argument("--as-of", dest="as_of", help="交易日 YYYY-MM-DD，默认最新有日线的交易日")
    ap.add_argument("--json", action="store_true", help="输出 JSON（默认人读）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要写入的内容，不写库")
    ap.add_argument("--no-cpt", dest="no_cpt", action="store_true", help="本次不调 CPT")
    ap.add_argument(
        "--no-persist",
        dest="no_persist",
        action="store_true",
        help="不写库（默认即不写；显式给 --persist 才写）",
    )
    ap.add_argument("--persist", action="store_true", help="写 holding_decision（幂等）")
    # R4 sizing 参数（不传则用默认 1%%/1%%/30%%；传了必须在 SIZING_BOUNDS 区间内）
    ap.add_argument(
        "--risk-pct",
        dest="risk_pct",
        type=float,
        help="单笔风险占总资金比例（如 0.02 = 2%%）；默认 0.01",
    )
    ap.add_argument("--liq-pct", dest="liq_pct", type=float, help="流动性折损比例；默认 0.01")
    ap.add_argument(
        "--max-position-pct",
        dest="max_position_pct",
        type=float,
        help="单票仓位上限占总资金比例（如 0.5 = 50%%）；默认 0.30",
    )
    ap.add_argument(
        "--ensure-factors",
        dest="ensure_factors",
        action="store_true",
        help="调 CPT 前先补因子（写 CPT 库；默认只读）",
    )
    ap.add_argument("--user", default=DEFAULT_USER, help="CPT 用户标识（默认 default）")
    args = ap.parse_args(argv)

    # R4 sizing 参数校验（与 SIZING_BOUNDS 同一口径；越界报错而非静默回落）
    sizing_params: dict[str, float] | None = None
    if args.risk_pct is not None or args.liq_pct is not None or args.max_position_pct is not None:
        sizing_params = {}
        if args.risk_pct is not None:
            lo, hi = SIZING_BOUNDS["risk_pct"]
            if not (lo <= args.risk_pct <= hi):
                ap.error(f"--risk-pct 需要在 {lo}-{hi} 之间")
            sizing_params["risk_pct"] = args.risk_pct
        if args.liq_pct is not None:
            lo, hi = SIZING_BOUNDS["liq_pct"]
            if not (lo <= args.liq_pct <= hi):
                ap.error(f"--liq-pct 需要在 {lo}-{hi} 之间")
            sizing_params["liq_pct"] = args.liq_pct
        if args.max_position_pct is not None:
            lo, hi = SIZING_BOUNDS["max_position_pct"]
            if not (lo <= args.max_position_pct <= hi):
                ap.error(f"--max-position-pct 需要在 {lo}-{hi} 之间")
            sizing_params["max_position_pct"] = args.max_position_pct

    if args.all and args.code:
        ap.error("--code 与 --all 不能同时给")
    if not args.all and not args.code:
        ap.error("必须给 --code 或 --all")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    persist = bool(args.persist) and not args.dry_run and not args.no_persist
    try:
        if args.all:
            out = analyze_holdings(
                capital=args.capital,
                as_of=as_of,
                use_cpt=not args.no_cpt,
                sizing_params=sizing_params,
                cpt_user=args.user,
            )
            if persist:
                # `analyze_holdings` 按契约恒 persist=False（只读报告）；要落库就按它算出的
                # 成本/数量重跑一遍 decide(persist=True)，同一交易日同 code 幂等。
                out = [
                    decide(
                        d.code,
                        cost=d.inputs.get("cost"),
                        shares=d.inputs.get("shares"),
                        capital=args.capital,
                        as_of=as_of,
                        use_cpt=not args.no_cpt,
                        persist=True,
                        sizing_params=sizing_params,
                        ensure_factors=args.ensure_factors,
                        cpt_user=args.user,
                    )
                    for d in out
                ]
        else:
            d = decide(
                args.code,
                cost=args.cost,
                shares=args.shares,
                capital=args.capital,
                as_of=as_of,
                use_cpt=not args.no_cpt,
                persist=persist,
                sizing_params=sizing_params,
                ensure_factors=args.ensure_factors,
                cpt_user=args.user,
            )
            out = [d]
    except ValueError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                [to_payload(d) for d in out] if args.all else to_payload(out[0]),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    else:
        for d in out:
            print(_render(d))
        if args.dry_run and persist:
            print("（--dry-run：未写库）")
    if persist:
        print(f"已写入 holding_decision：{len(out)} 行", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
