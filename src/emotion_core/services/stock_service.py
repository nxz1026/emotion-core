"""个股诊断服务：查数据 → 规则判定 → （可选）LLM 通俗解读。

分层约定：本模块是 `data/`（SQL）与 `algorithms/stock.py`（纯判定）的粘合层，
并且是**唯一**碰 LLM 的地方（信号链 emotion/ladder/entry/exit 不导入本模块）。
整条链路**只读**：不写任何库表。

LLM 参与是可选增强（用户 2026-09-28 拍板开启）：`CONFIG.STOCK_LLM_PROFILE`
为 `off`/空 → 只出规则层结论；密钥缺失或调用失败 → 页面照常出规则层结论，
并如实标注失败原因（`llm.ok=false` + `llm.error`），绝不让页面 500。
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

from emotion_core.algorithms import stock as stock_algo
from emotion_core.data import stock_query as q
from emotion_core.utils.config import CONFIG

log = logging.getLogger("emotion_core.services.stock")

# 昂贵的统计（晋级率全表聚合 ~2s、前瞻分布 ~0.1s/层）按目标日 + TTL 缓存
_CACHE: dict[tuple, tuple[float, Any]] = {}
_LLM_OFF = ("", "off", "none", "false", "0")


def _cached(key: tuple, producer):
    """带 TTL 的进程内缓存（页面高频点击时避免反复全表聚合）。"""
    ttl = max(0, int(CONFIG.STOCK_STAT_CACHE_SEC))
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = producer()
    _CACHE[key] = (now, value)
    return value


def clear_cache() -> None:
    """清空统计缓存（测试与运维用）。"""
    _CACHE.clear()


def promotion_stats(end: date) -> list[dict]:
    """全市场历史晋级率表（缓存）。"""
    return _cached(("promo", end), lambda: q.promotion_table(end))


def forward_stats(end: date, layer: str) -> dict:
    """同层级 5 日前瞻收益分布（缓存）。"""
    return _cached(("fwd5", end, layer),
                   lambda: q.layer_forward(end, layer, 5))


def _promo_row(end: date, layer: str | None) -> dict | None:
    if not layer:
        return None
    for row in promotion_stats(end):
        if row["layer"] == layer:
            return row
    return None


def _fmt_env(env: dict | None) -> str:
    if not env:
        return "（该日 market_stat 无数据）"
    bomb = env.get("bomb_rate")
    return (f'- 阶段：{env.get("phase")}；买入窗口 buy_window={env.get("buy_window")}\n'
            f'- 涨停 {env.get("limit_up_count")} 家 / 跌停 {env.get("limit_down_count")} 家；'
            f'最高 {env.get("max_limit_days")} 板\n'
            f'- 炸板率 {bomb}；一字占比 {env.get("oneword_ratio")}；'
            f'昨日涨停今日表现 {env.get("zt_performance")}\n'
            f'- force_liquidate={env.get("force_liquidate")}')


def _fmt_state(trend: dict, structure: dict) -> str:
    return (f'- 收盘 {trend.get("price")}（涨跌 {trend.get("pct_chg")}%），'
            f'MA5 {trend.get("ma5")} / MA10 {trend.get("ma10")} / MA20 {trend.get("ma20")}\n'
            f'- 连板 {trend.get("cont_days")} 板；今日涨停={trend.get("is_limit_up")}'
            f'、一字={trend.get("is_one_word")}、换手板={trend.get("is_exchange")}\n'
            f'- 距 20 日高点 {trend.get("off_high_pct")}%；量比 {trend.get("vol_ratio")}；'
            f'换手率 {trend.get("turnover_rate")}；振幅 {trend.get("amplitude")}\n'
            f'- 近 {structure.get("sample_n")} 个交易日：涨停 {structure.get("limit_up_n")} 次、'
            f'炸板 {structure.get("bomb_n")} 次、一字 {structure.get("one_word_n")} 次、'
            f'换手板 {structure.get("exchange_n")} 次、最高 {structure.get("max_cont")} 板')


def _fmt_stats(stats: dict, fwd5: dict) -> str:
    parts = []
    if stats.get("rate") is not None:
        parts.append(f'- {stats["layer"]} 层历史晋级率 {stats["rate"]}%'
                     f'（{stats["promoted"]}/{stats["promote_from"]}），'
                     f'换手口径 {stats.get("rate_exchange")}%，'
                     f'背离 {stats.get("divergence")}')
    else:
        parts.append(f'- {stats.get("layer") or "该层"} 历史样本不足（分母 < 门槛），不猜')
    if stats.get("next_median") is not None:
        parts.append(f'- 该层样本次日涨跌中位数 {stats["next_median"]}%、'
                     f'胜率 {stats.get("next_win_rate")}%（N={stats.get("next_n")}）')
    if stats.get("fail_perf") is not None:
        parts.append(f'- 未晋级样本次日均值 {stats["fail_perf"]}%')
    if fwd5.get("n"):
        parts.append(f'- 同层 5 日前瞻：中位数 {fwd5.get("median")}%、'
                     f'胜率 {fwd5.get("win_rate")}%、均值 {fwd5.get("avg")}%（N={fwd5["n"]}）')
    return "\n".join(parts) or "- （无可用统计）"


def llm_comment(payload: dict) -> dict:
    """可选 LLM 解读。返回 {enabled, ok, profile, model, text, error}。"""
    profile = (CONFIG.STOCK_LLM_PROFILE or "").strip()
    if profile.lower() in _LLM_OFF:
        return {"enabled": False, "ok": False, "profile": None,
                "text": "", "error": "LLM 未启用（EC_STOCK_LLM_PROFILE=off）"}
    try:
        from emotion_core.llm.render import render_prompt
        from emotion_core.services.llm import get_client

        basic, trend, structure = payload["basic"], payload["trend"], payload["structure"]
        env, verdict = payload["market_env"], payload["verdict"]
        prompt = render_prompt(
            "stock",
            code=basic.get("code"), name=basic.get("name"),
            trade_date=payload["trade_date"], is_st=basic.get("is_st"),
            is_new=payload["is_new_issuer"],
            industry=basic.get("industry") or "未接入",
            market_cap=basic.get("market_cap") or "未接入",
            stance_text=verdict["stance_text"], one_liner=verdict["one_liner"],
            reasons="；".join(verdict["reasons"]) or "无",
            risks="；".join(verdict["risks"]) or "无",
            tags="、".join(t["name"] for t in verdict["tags"]) or "无",
            market_env=_fmt_env(env), stock_state=_fmt_state(trend, structure),
            stats=_fmt_stats(verdict["stats"], verdict["stats"].get("fwd5") or {}),
        )
        # 用 chat()（不是 complete()）：complete() 只回字符串，拿不到 model 字段
        reply = get_client(profile, max_tokens=CONFIG.STOCK_LLM_MAX_TOKENS).chat(
            [{"role": "system",
              "content": "你是A股情绪周期体系的个股诊断助手，只依据给定数据说话。"},
             {"role": "user", "content": prompt}],
            purpose="stock-diagnosis")
        return {"enabled": True, "ok": True, "profile": profile,
                "model": getattr(reply, "model", None), "text": reply.text, "error": ""}
    except Exception as exc:  # noqa: BLE001 —— LLM 失败绝不影响规则层结论
        log.warning("个股 LLM 解读失败（profile=%s）：%s", profile, exc)
        return {"enabled": True, "ok": False, "profile": profile, "model": None,
                "text": "", "error": str(exc)[:300]}


def _payload(code: str, end: date, basic: dict, series: list[dict],
             structure: dict, env: dict, top: list[dict],
             themes: list[dict]) -> dict:
    trend = stock_algo.trend_metrics(series)
    layer = stock_algo.layer_label(trend["cont_days"]) if trend["cont_days"] >= 1 else None
    promo_row = _promo_row(end, layer)
    fwd5 = forward_stats(end, layer) if layer else {"n": 0}
    v = stock_algo.verdict(basic, trend, structure, env or {}, promo_row,
                           None, fwd5, basic.get("first_bar_date"), end)
    return {
        "code": code,
        "trade_date": end,
        "basic": basic,
        "is_new_issuer": stock_algo.is_new_issuer(basic.get("first_bar_date"), end),
        "trend": trend,
        "structure": structure,
        "market_env": env or {},
        "market_top": top,
        "themes": themes,
        "promotion_table": promotion_stats(end),
        "series": series,
        "verdict": v,
    }


def llm_for(code: str, trade_date: date | None = None) -> dict:
    """只跑 LLM 解读（页面异步取用；规则层结果不依赖它）。"""
    payload = analyze(code, trade_date, use_llm=False)
    if not payload.get("ok"):
        return {"enabled": True, "ok": False, "profile": CONFIG.STOCK_LLM_PROFILE,
                "text": "", "error": payload.get("error", "分析失败")}
    return llm_comment(payload)


def analyze(query: str, trade_date: date | None = None,
            use_llm: bool = False) -> dict:
    """入口：把用户输入（代码或名称）诊断成一份可渲染的结果。

    Returns:
        {"ok": True, ...payload+llm} 或
        {"ok": False, "error": "...", "candidates": [...]}
    """
    text = (query or "").strip()
    if not text:
        return {"ok": False, "error": "请输入股票代码或名称", "candidates": []}
    end = trade_date or q.latest_trade_date()
    if end is None:
        return {"ok": False, "error": "数据库无日线数据", "candidates": []}

    candidates = q.resolve_code(text)
    if not candidates:
        return {"ok": False, "error": f"未找到与「{text}」匹配的股票", "candidates": []}
    code = candidates[0]["code"]
    basic = q.basic_row(code)
    if basic is None:
        return {"ok": False, "error": f"{code} 无证券主数据", "candidates": candidates}
    series = stock_algo.attach_volume_ratio(
        q.recent_series(code, end, max(30, int(CONFIG.STOCK_TREND_DAYS))))
    if not series:
        return {"ok": False, "error": f"{code} 在 {end} 之前没有日线数据",
                "candidates": candidates}
    payload = _payload(code, end, basic, series,
                       q.structure_window(code, end, 60),
                       q.market_env(end), q.market_top(end), q.themes_of(code, end))
    payload["ok"] = True
    payload["candidates"] = candidates if len(candidates) > 1 else []
    payload["llm"] = (llm_comment(payload) if use_llm else
                      {"enabled": False, "ok": False, "profile": None, "text": "",
                       "error": "按需异步获取（GET /api/stock/llm?code=…）"})
    return payload
