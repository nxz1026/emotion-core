"""个股诊断的判定层（纯计算：零 SQL、零 LLM、零 IO）。

设计约束：
- 所有输入都是 `data/stock_query.py` 取回的普通 dict/list，便于单测用假数据；
- 结论**每条都挂数据依据**（统计样本数 N、晋级率、收益中位数），不写空话；
- 建议强度按用户拍板：给出明确动作倾向 + 风险量化，但页面保留
  「统计参考，非投资建议」脚注，输出里也带 `disclaimer` 字段；
- 与本仓库状态机同源：市场 `buy_window` 是硬门（NONE = 不参与），
  连板层级走 `PROMOTION_LAYERS` 的标签（`1->2` … `5+->6+`）。
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# 建议档位（从强到弱）。UI 按 stance_code 上色。
STANCE_BUY = "BUY"            # 可参与（低吸/接力换手板）
STANCE_LOW_ABSORB = "LOW"     # 只做低吸/回踩，不追高
STANCE_WATCH = "WATCH"        # 观察等信号
STANCE_AVOID = "AVOID"        # 不建议参与

_STANCE_TEXT = {
    STANCE_BUY: "可参与（低吸/接力换手板）",
    STANCE_LOW_ABSORB: "只做低吸回踩，不追高",
    STANCE_WATCH: "观察等信号",
    STANCE_AVOID: "不建议参与",
}

NEW_ISSUER_DAYS = 90  # 与 CONFIG.NEW_ISSUER_MIN_DAYS 同义（判定层不读配置，由调用方传）
HIGH_CONT = 4         # 连板高度阈值：>= 视为高位（打板风险陡增）
NEAR_HIGH_PCT = 3.0   # 距 20 日高点 3% 以内视为"贴着高点"
BOMB_MANY = 3         # 近 60 日炸板 >= 3 次视为"炸板体质"


def layer_label(cont_days: int) -> str:
    """连板层级标签，与 `algorithms/promotion.py: PROMOTION_LAYERS` 一致。"""
    n = int(cont_days or 0)
    if n >= 5:
        return "5+->6+"
    return f"{n}->{n + 1}"


def mean(values: Sequence[float | None]) -> float | None:
    """均值；忽略 None/NaN，无有效值返回 None。"""
    nums = [float(v) for v in values if _ok(v)]
    return None if not nums else round(sum(nums) / len(nums), 3)


def _ok(v: Any) -> bool:
    try:
        return v is not None and float(v) == float(v)  # NaN != NaN
    except (TypeError, ValueError):
        return False


def ma(closes: Sequence[float | None], n: int) -> float | None:
    """最近 n 根收盘均值（不足 n 根返回 None）。"""
    nums = [float(c) for c in closes if _ok(c)]
    if len(nums) < n:
        return None
    window = nums[-n:]
    return round(sum(window) / len(window), 3)


def trend_metrics(series: Sequence[dict]) -> dict:
    """趋势指标：均线、距 20 日高点、量比、连板现状。"""
    closes = [r.get("close") for r in series]
    vols = [r.get("volume") for r in series]
    last = series[-1] if series else {}
    close = last.get("close")
    price = float(close) if _ok(close) else None
    highs = [float(r["high"]) for r in series[-20:] if _ok(r.get("high"))]
    high20 = round(max(highs), 3) if highs else None   # 与 ma() 同精度
    vol_ma20 = mean([v for v in vols[-21:-1]]) if len(vols) > 21 else mean(vols[:-1])
    vol_last = vols[-1] if vols else None
    vol_ratio = (round(float(vol_last) / vol_ma20, 2)
                 if _ok(vol_last) and vol_ma20 else None)
    return {
        "price": price,
        "pct_chg": _f(last.get("pct_chg")),
        "ma5": ma(closes, 5), "ma10": ma(closes, 10), "ma20": ma(closes, 20),
        "high20": high20,
        "off_high_pct": (round((price / high20 - 1) * 100, 2)
                         if price and high20 else None),
        "vol_ratio": vol_ratio,
        "turnover_rate": _f(last.get("turnover_rate")),
        "amplitude": _f(last.get("amplitude")),
        "close": price,
        "is_limit_up": bool(last.get("is_limit_up")),
        "is_one_word": bool(last.get("is_one_word")),
        "is_exchange": bool(last.get("is_exchange")),
        "is_limit_down": bool(last.get("is_limit_down")),
        "cont_days": int(last.get("cont_days") or 0),
        "above_ma20": (price > ma(closes, 20)) if price and ma(closes, 20) else None,
        "above_ma5": (price > ma(closes, 5)) if price and ma(closes, 5) else None,
    }


def _f(v: Any) -> float | None:
    return float(v) if _ok(v) else None


def attach_volume_ratio(series: list[dict], window: int = 5) -> list[dict]:
    """给每根 bar 补"相对前 window 日均量"的量比（展示用；纯计算、不改库）。"""
    vols = [r.get("volume") for r in series]
    out: list[dict] = []
    for i, row in enumerate(series):
        prev = [float(v) for v in vols[max(0, i - window):i] if _ok(v)]
        item = dict(row)
        item["vol_ratio5"] = (round(float(vols[i]) / (sum(prev) / len(prev)), 2)
                              if prev and _ok(vols[i]) else None)
        out.append(item)
    return out


def is_st_name(name: str | None) -> bool:
    """名称兜底的 ST 判定。

    实测（2026-09-28 真库）：`stock_basic.is_st` **5221 行全为 false**，而名字带
    ST/*ST 的有 201 只 —— 该标志位来自 EM clist（本机 502，从未填充）。本体系把
    ST 排除在标的池外，所以这里以名称为准（标志位为真也认）。
    """
    return (name or "").strip().upper().startswith(("ST", "*ST"))


def is_st(basic: dict) -> bool:
    """ST 判定：标志位 or 名称（见 is_st_name 的实测说明）。"""
    return bool(basic.get("is_st")) or is_st_name(basic.get("name"))


def is_new_issuer(first_bar_date, trade_date, days: int = NEW_ISSUER_DAYS) -> bool:
    """次新判定：首个 bar 距目标日不足 days 自然日（与次新过滤同口径）。"""
    if first_bar_date is None or trade_date is None:
        return False
    return (trade_date - first_bar_date).days < days


def risk_tags(basic: dict, trend: dict, structure: dict, env: dict,
              first_bar_date, trade_date) -> list[dict]:
    """风险标签（带一句话说明，UI 直接渲染 chips）。"""
    tags: list[dict] = []
    if is_st(basic):
        tags.append({"level": "high", "name": "ST",
                     "why": "ST 股不在本体系标的池内（主板非 ST 口径）"})
    if is_new_issuer(first_bar_date, trade_date):
        tags.append({"level": "mid", "name": "次新",
                     "why": "上市不足 90 自然日，历史样本少、波动大"})
    if trend.get("is_one_word"):
        tags.append({"level": "mid", "name": "一字板",
                     "why": "一字板上不了车；能买到时往往是开板分歧（样本收益显著变差）"})
    elif trend.get("is_exchange"):
        tags.append({"level": "low", "name": "换手板",
                     "why": "换手板有量、接力意愿真实，比一字板可参与"})
    if int(trend.get("cont_days") or 0) >= HIGH_CONT:
        tags.append({"level": "high", "name": f'{trend["cont_days"]}连板高位',
                     "why": f"连板高度 ≥{HIGH_CONT} 板，越往上打错一次回撤越大"})
    if (trend.get("off_high_pct") is not None
            and trend["off_high_pct"] > -NEAR_HIGH_PCT):
        tags.append({"level": "mid", "name": "贴着 20 日高点",
                     "why": f'距 20 日高点仅 {trend["off_high_pct"]}%，追高止损位难设'})
    if int(structure.get("bomb_n") or 0) >= BOMB_MANY:
        tags.append({"level": "mid", "name": "炸板体质",
                     "why": f'近 {structure.get("sample_n")} 个交易日炸板 '
                            f'{structure["bomb_n"]} 次'})
    if trend.get("is_limit_down"):
        tags.append({"level": "high", "name": "跌停", "why": "当日跌停，情绪负反馈"})
    if env.get("buy_window") == "NONE":
        tags.append({"level": "high", "name": "情绪窗口关闭",
                     "why": f'当日 buy_window=NONE（{env.get("phase", "?")} 阶段）'})
    if env.get("force_liquidate"):
        tags.append({"level": "high", "name": "强制离场信号",
                     "why": "市场状态机给出 force_liquidate"})
    if env.get("phase") in ("退潮", "冰点"):
        tags.append({"level": "mid", "name": f'市场{env.get("phase")}',
                     "why": "退潮/冰点阶段接力胜率整体下降"})
    if trend.get("above_ma20") is False:
        tags.append({"level": "mid", "name": "跌破 20 日线",
                     "why": "趋势转弱，短线资金流出"})
    if trend.get("vol_ratio") is not None and trend["vol_ratio"] >= 3:
        tags.append({"level": "mid", "name": "放量异常",
                     "why": f'量比 {trend["vol_ratio"]}（相对 20 日均量）'})
    return tags


def verdict(basic: dict, trend: dict, structure: dict, env: dict,
            promo_row: dict | None, fwd1: dict | None, fwd5: dict | None,
            first_bar_date, trade_date) -> dict:
    """规则判定：给出档位 + 逐条依据（每条都带数据）。

    判定顺序（先硬否决，再按情绪门控与个股位置给档）：
    1. ST / 跌停 → AVOID（体系外或负反馈）
    2. 市场 buy_window=NONE 或 force_liquidate → AVOID（情绪窗前，个股再好也不做）
    3. 当日未涨停：看趋势 —— 站上 20 日线 → WATCH（等启动信号），否则 WATCH + 风险提示
    4. 高位（cont ≥ 4）或一字板 → LOW（只低吸/等开板，不追）
    5. 低位（cont ≤ 3）且换手板 且 晋级率不低于同层历史 → BUY
    6. 其余 → LOW / WATCH
    """
    cont = int(trend.get("cont_days") or 0)
    layer = layer_label(cont) if cont >= 1 else None
    reasons: list[str] = []
    risks: list[str] = []
    stance = STANCE_WATCH

    rate = None if not promo_row else promo_row.get("rate")
    total = None if not promo_row else promo_row.get("total")
    fail_perf = None if not promo_row else promo_row.get("fail_perf")
    perf_med = None if not promo_row else promo_row.get("perf_median")
    win_rate = None if not promo_row else promo_row.get("win_rate")

    if is_st(basic):
        stance = STANCE_AVOID
        reasons.append("ST 股：本体系标的池为主板非 ST，历史统计不覆盖该样本")
    elif trend.get("is_limit_down"):
        stance = STANCE_AVOID
        reasons.append("当日跌停：情绪负反馈，不接下跌中的票")
    elif env.get("buy_window") == "NONE" or env.get("force_liquidate"):
        stance = STANCE_AVOID
        reasons.append(
            f'市场情绪窗口关闭（{env.get("phase", "?")} 阶段 / buy_window=NONE）：'
            f'当日涨停 {env.get("limit_up_count", "—")} 家、炸板率 {_pct(env.get("bomb_rate"))}，'
            "个股信号在这种环境里失效")
    elif cont == 0:
        stance = STANCE_WATCH
        if trend.get("above_ma20"):
            reasons.append(
                f'未涨停但站上 20 日线（收 {trend.get("price")} vs MA20 {trend.get("ma20")}），'
                "趋势型标的，等启动信号（首板/放量突破）再说")
        else:
            reasons.append(
                f'未涨停且在 20 日线下方（收 {trend.get("price")} vs MA20 {trend.get("ma20")}），'
                "无短线信号")
        if trend.get("off_high_pct") is not None:
            reasons.append(f'距 20 日高点 {trend["off_high_pct"]}%，量比 '
                           f'{trend.get("vol_ratio", "—")}')
    else:
        # 有连板：引用同层历史统计
        stat_bits = []
        if rate is not None and total:
            stat_bits.append(f"{layer} 层历史晋级率 {rate}%（N={total}）")
        if perf_med is not None:
            stat_bits.append(f"该层样本次日涨跌中位数 {perf_med}%"
                             f"{f'、胜率 {win_rate}%' if win_rate is not None else ''}")
        if fail_perf is not None:
            stat_bits.append(f"未晋级样本次日均值 {fail_perf}%")
        stat_line = "；".join(stat_bits) if stat_bits else "该层历史样本不足（分母 < 门槛，不猜）"

        if cont >= HIGH_CONT or trend.get("is_one_word"):
            stance = STANCE_LOW_ABSORB
            why = []
            if cont >= HIGH_CONT:
                why.append(f"已 {cont} 连板（高位）")
            if trend.get("is_one_word"):
                why.append("一字板买不到")
            reasons.append(f'{ "、".join(why) }：不追高，只在开板换手或次日回踩时看')
            reasons.append(stat_line)
        elif trend.get("is_exchange"):
            stance = STANCE_BUY
            reasons.append(f'{cont} 连板且为**换手板**（有量、接力意愿真实）')
            reasons.append(stat_line)
        else:
            stance = STANCE_LOW_ABSORB
            reasons.append(f'{cont} 连板但非换手板（或封板质量一般）：等换手确认再看')
            reasons.append(stat_line)

        if fwd5 and fwd5.get("n"):
            reasons.append(
                f'同层级 5 日前瞻：中位数 {fwd5.get("median")}%、胜率 {fwd5.get("win_rate")}%'
                f'（N={fwd5["n"]}）—— 高度越高，持有的时间窗越短')
        if trend.get("off_high_pct") is not None and trend["off_high_pct"] > -NEAR_HIGH_PCT:
            risks.append(f'贴着 20 日高点（{trend["off_high_pct"]}%），追高即接盘风险')
        if fail_perf is not None:
            risks.append(f"打错一次的代价：同层未晋级样本次日均值 {fail_perf}%")

    # 市场对照
    ctx_lines = [
        f'市场：{env.get("phase", "?")} 阶段，buy_window={env.get("buy_window", "?")}，'
        + f'涨停 {env.get("limit_up_count", "—")} 家 / 炸板率 {_pct(env.get("bomb_rate"))} / '
        + f'最高 {env.get("max_limit_days", "—")} 板',
    ]
    if cont >= 1:
        ctx_lines.append(f"该股 {cont} 板，位列市场最高板 "
                         f"{'之内' if cont >= (env.get('max_limit_days') or 0) else '之下'}")

    return {
        "stance": stance,
        "stance_text": _STANCE_TEXT[stance],
        "layer": layer,
        "one_liner": _one_liner(stance, cont, trend, env),
        "reasons": reasons,
        "risks": risks,
        "context": ctx_lines,
        "stats": {
            "layer": layer,
            "rate": rate, "rate_exchange": (promo_row or {}).get("rate_exchange"),
            "divergence": (promo_row or {}).get("divergence"),
            "promote_from": total, "promoted": (promo_row or {}).get("promoted"),
            "next_median": perf_med, "next_win_rate": win_rate,
            "next_n": (promo_row or {}).get("perf_n"),
            "fail_perf": fail_perf,
            "fwd5": fwd5 or {},
        },
        "tags": risk_tags(basic, trend, structure, env, first_bar_date, trade_date),
        "disclaimer": "统计参考，非投资建议；样本为历史统计，不代表未来收益。",
    }


def _one_liner(stance: str, cont: int, trend: dict, env: dict) -> str:
    if stance == STANCE_AVOID:
        if env.get("buy_window") == "NONE":
            return "情绪窗口关闭，今天不看票"
        return "风险优先，今天不参与"
    if stance == STANCE_BUY:
        return f"{cont} 连板换手板 + 情绪窗口开启，可低吸参与"
    if stance == STANCE_LOW_ABSORB:
        return "位置偏高或封板质量不足，只等回踩/开板换手"
    if cont == 0:
        return "暂无短线信号，等首板或放量突破"
    return "信号不足，观察"


def _pct(v: Any) -> str:
    return "—" if not _ok(v) else f"{round(float(v) * 100, 1)}%"
