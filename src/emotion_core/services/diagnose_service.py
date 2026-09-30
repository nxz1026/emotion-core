"""胶水层：个股诊断服务（三段式 A/B/C，docs/03-新增需求 §4.2）。

职责：把「这只票现在是什么 / 过去干过什么 / 用它赌值不值」聚合成一份可渲染结果，
供 `services/stock_service.analyze` 注入 `/api/stock`（新增键 `diagnose`，不改既有键）。

分层纪律：
- 五条件判定**只**调 `algorithms/entry.py` 的既有实现（`entry.rows` = 判定函数 + 说明行，
  `entry.passed_of` = 唯一聚合器）；本模块不重写任何条件规则。
- 梯队**只**走 `algorithms/ladder.py` 的产物：`ladder_day` 由 `ladder.persist` 落库，
  其 is_top / is_sole_top 由 `ladder.fold` 判定（换手板口径）；A 段构造的 LadderDay 只
  承载候选身份，`entry._ctx` 内部仍自行 `ladder.build` 取当日/昨日梯队，故口径同源。
- 算法层零 SQL：本模块是自有 SQL 的住所（只读 `query_df`），与既有实现同风格。

降级纪律（硬约束 4）：任何一段缺表/缺列/异常都只写 `note`，绝不抛给 `/api/stock`。
`stats=`（由 stock_service 注入已算好的昂贵统计）缺省时，C 段统计腿标注「未注入」。

B 段的 T+1/T+3/T+5 一律 `date <= as_of` 现算（PIT）：目标日之后的腿缺失就是缺失，
不用未来数据补，不猜。
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Callable

from emotion_core.algorithms import entry, stock as stock_algo
from emotion_core.domain.ladder import LadderDay
from emotion_core.domain.signal import Checklist
from emotion_core.utils.db import query_df
from emotion_core.utils.dates import prev_trading_day

log = logging.getLogger("emotion_core.diagnose_service")

# 段标题 = docs/03 §4.2 原文口径
TITLE_A = "A · 它现在是什么"
TITLE_B = "B · 它过去干过什么"
TITLE_C = "C · 用它赌值不值"

# 窗口人话（window_of：发酵→STANDARD / 高潮→ENHANCED / 冰点·退潮→NONE，见 algorithms/state.py）
_WINDOW_TEXT = {
    "STANDARD": "发酵期（标准窗口）：逐项核对五条件后参与",
    "ENHANCED": "高潮期（分歧窗口）：仅当炸板回封或换手达标时参与",
    "NONE": "禁买窗口（退潮/冰点）：本日不产生买入信号，只留观察线索",
}

# entry.rows 的行序是公开契约（entry.py:175-185：条件按 _CONDITIONS 序 + 警告恒排最后），
# 故可用位置构造 Checklist 交给 entry.passed_of 聚合；序若变动则退回 entry.checklist 重算。
_CHECK_IDS: tuple[str, ...] = ("c1", "c2", "c3", "c4", "c5", "w1")

_LIMIT = 10          # B 段各表取最近 N 条
_THEME_LIMIT = 3

# ---- SQL（只读；本模块自持，与 services/stock_service 的 data/stock_query 同风格）----

# 当日梯队在册行（ladder.persist 产物；在册门槛 = 主板 + 非 ST + 非次新 + 连板≥2）
_LADDER_SQL = """
SELECT code, cont_days, is_exchange, is_top, is_sole_top,
       y_top_group_count, y_top_survivor_count
  FROM ladder_day
 WHERE date = %s
 ORDER BY cont_days DESC, code
"""

# 该股历史「唯一最高板」日期 + 每次之后 T+1/T+3/T+5 收盘收益（PIT：只用 <= 目标日的 bar）
_SOLE_TOP_SQL = """
WITH bars AS (
  SELECT date, close,
         lead(close, 1) OVER (ORDER BY date) AS c1,
         lead(close, 3) OVER (ORDER BY date) AS c3,
         lead(close, 5) OVER (ORDER BY date) AS c5
    FROM daily_bar
   WHERE code = %s AND date <= %s)
SELECT b.date,
       round((b.c1 / nullif(b.close, 0) - 1) * 100, 2) AS t1_ret,
       round((b.c3 / nullif(b.close, 0) - 1) * 100, 2) AS t3_ret,
       round((b.c5 / nullif(b.close, 0) - 1) * 100, 2) AS t5_ret
  FROM bars b
  JOIN (SELECT date FROM ladder_day
         WHERE code = %s AND is_sole_top AND date <= %s
         ORDER BY date DESC LIMIT %s) t ON t.date = b.date
 ORDER BY b.date DESC
"""

# 该股历史信号 + 结果（signal_outcome 已有 T+1/T+5 两腿 + 规则化收益）
_SIGNAL_SQL = """
SELECT s.confirm_date, s.action, s.status, s.buy_window, s.checklist, s.source,
       o.t1_gap, o.t1_promote, o.t1_close_ret, o.max_up5, o.max_dd5,
       o.t5_close_ret, o.rule_ret_a, o.rule_ret_d, o.complete
  FROM signal s
  LEFT JOIN signal_outcome o
    ON o.confirm_date = s.confirm_date AND o.code = s.code AND o.action = s.action
 WHERE s.code = %s AND s.confirm_date <= %s
 ORDER BY s.confirm_date DESC LIMIT %s
"""

# 该股题材标签 + 所在题材组的完整性口径（theme_group.completeness / status）
_THEME_SQL = """
SELECT t.date, t.primary_theme, t.role, t.confidence,
       g.completeness, g.status, g.highest_board, g.member_count, g.top_code
  FROM theme_tag t
  LEFT JOIN theme_group g
    ON g.date = t.date AND g.theme = t.primary_theme
 WHERE t.code = %s AND t.date <= %s
 ORDER BY t.date DESC LIMIT %s
"""

# 目标日市场环境 + 生态评级（dragon_env 及其理由/风险为 market_stat 已落库产物）
_MARKET_SQL = """
SELECT date, phase, buy_window, has_candidate, diverge, force_liquidate,
       max_limit_days, dragon_env, dragon_env_reasons, dragon_env_risks,
       accelerate, accel_reason, reason, tradable_max_days
  FROM market_stat
 WHERE date = %s
"""

_FUNDAMENTAL_SQL = """
SELECT name, industry, market_cap, is_st, list_date, first_bar_date, in_market
  FROM stock_basic
 WHERE code = %s
"""

_TECHNICAL_SQL = """
SELECT is_limit_up, is_exchange, cont_days, amplitude
  FROM derived_bar
 WHERE code = %s AND date = %s
"""


def _try(fn: Callable, *args, **kwargs) -> Any:
    """执行并折异常为 None——本服务任何一段都不许把异常抛给 /api/stock。"""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("diagnose 调用 %s 失败：%s", getattr(fn, "__qualname__", fn), exc)
        return None


def _frame(sql: str, params: tuple = ()):
    """只读查询；异常 → None（调用方按「缺数」降级，不抛）。"""
    return _try(query_df, sql, params)


def _opt(row: Any, key: str, default: Any = None) -> Any:
    """可选列读取：旧查询/桩未提供的列 → default（F6：缺失即 None，不补零）。"""
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    if value is None or value != value:      # None / NaN（不 import pandas 也能判）
        return default
    return value


def _rows(df) -> list[dict]:
    if df is None or df.empty:
        return []
    return df.to_dict("records")


def _as_rows(raw: Any) -> list[dict]:
    """signal.checklist（jsonb，与 entry._persist 同形：[[标签, ok, 说明], ...]）→ 结构化。"""
    if isinstance(raw, str):
        raw = _try(json.loads, raw)
    out = []
    for item in raw or []:
        if isinstance(item, (list, tuple)) and len(item) == 3:
            label, ok, note = item
            out.append({"label": str(label), "ok": ok, "note": str(note)})
    return out


def _as_json(raw: Any) -> Any:
    if isinstance(raw, str):
        return _try(json.loads, raw)
    return raw


# --------------------------------------------------------------------------- A 段

def _why_not_candidate(technical: dict) -> str:
    """非候选时给一句可核验的原因（只用已取的当日行情，不猜）。"""
    if not technical:
        return "当日无行情行（停牌 / 未上市 / 缺数）"
    if not technical.get("is_limit_up"):
        return "当日未涨停"
    if not int(technical.get("cont_days") or 0):
        return "当日无连板记录"
    return (f"当日 {technical.get('cont_days')} 连板，但不在梯队在册集合"
            "（次新 / ST / 非主板之一）")


def _checklist_of(cond_rows: list, cand: LadderDay, window: str,
                  as_of: date) -> Checklist:
    """条件行 → domain.Checklist。位序不符时退回 entry.checklist 重算（不自行聚合）。"""
    ids = tuple(str(label).split(" ", 1)[0].lower() for label, _, _ in cond_rows)
    if ids == _CHECK_IDS:
        return Checklist(*[ok for _, ok, _ in cond_rows])
    if ids == _CHECK_IDS[:-1]:        # 无警告行（entry._WARNINGS 为空时）→ 警告位留 None
        return Checklist(*[ok for _, ok, _ in cond_rows], None)
    log.warning("entry.rows 行序变化 %s，退回 entry.checklist 重算", ids)
    return entry.checklist(cand, window, as_of=as_of)


def _seg_a(code: str, as_of: date, window: str, has_market_row: bool,
           technical: dict) -> dict:
    """A 段：当日身份（连板/换手/梯队层级/唯一最高板）+ 五条件逐项 + W1 警告。"""
    seg: dict[str, Any] = {
        "title": TITLE_A, "applicable": False, "note": "", "reason": "",
        "layer": None, "cont_days": None, "is_exchange": None, "is_top": None,
        "is_sole_top": None, "ladder_role": "", "peers": {},
        "window": window, "window_text": _WINDOW_TEXT.get(window, "窗口取值未知"),
        "window_source": "market_stat" if has_market_row else "缺当日行，按 NONE（保守）",
        "conditions": [], "warnings": [], "passed": None,
        "ladder": [], "ladder_count": 0,
    }
    ladder_rows = []
    for r in _rows(_frame(_LADDER_SQL, (as_of,))):
        ladder_rows.append({
            "code": str(r["code"]),
            "cont_days": int(_opt(r, "cont_days", 0) or 0),
            "is_exchange": bool(_opt(r, "is_exchange", False)),
            "is_top": bool(_opt(r, "is_top", False)),
            "is_sole_top": bool(_opt(r, "is_sole_top", False)),
            # 仅承载（判定不由这两个字段参与：entry._ctx 用 ladder.build 自算 y_comp）
            "y_top_group_count": int(_opt(r, "y_top_group_count") or 0),
            "y_top_survivor_count": int(_opt(r, "y_top_survivor_count") or 0),
        })
    seg["ladder_count"] = len(ladder_rows)
    seg["ladder"] = ladder_rows[:20]

    mine = next((r for r in ladder_rows if r["code"] == code), None)
    if mine is None:
        seg["reason"] = _why_not_candidate(technical)
        seg["note"] = ("当日非候选：ladder_day 无该股行 ⇒ 不在梯队"
                       "（在册门槛 = 主板 + 非 ST + 非次新 + 连板≥2）。"
                       "五条件不适用，不编造 PASS/FAIL。")
        return seg

    seg["applicable"] = True
    seg["cont_days"] = mine["cont_days"]
    seg["is_exchange"] = mine["is_exchange"]
    seg["is_top"] = mine["is_top"]
    seg["is_sole_top"] = mine["is_sole_top"]
    seg["layer"] = stock_algo.layer_label(mine["cont_days"])
    same = [r for r in ladder_rows if r["cont_days"] == mine["cont_days"]]
    seg["peers"] = {
        "same_cont": len(same),
        "same_cont_exchange": sum(1 for r in same if r["is_exchange"]),
        "max_cont": max((r["cont_days"] for r in ladder_rows), default=0),
    }
    seg["ladder_role"] = ("唯一最高板（换手口径）" if mine["is_sole_top"]
                          else (f"最高身位成员（并列 {len(same)} 只）" if mine["is_top"]
                                else f"{mine['cont_days']} 板成员"))

    cand = LadderDay(date=as_of, code=code, cont_days=mine["cont_days"],
                     is_exchange=mine["is_exchange"], is_top=mine["is_top"],
                     is_sole_top=mine["is_sole_top"],
                     y_top_group_count=mine["y_top_group_count"],
                     y_top_survivor_count=mine["y_top_survivor_count"])
    cond_rows = _try(entry.rows, cand, window, as_of=as_of)
    if not cond_rows:
        seg["note"] = ("五条件不可核验：entry.rows 未产出（上游缺数或数据库异常），"
                       "不编造 PASS/FAIL。")
        return seg

    for label, ok, note in cond_rows:
        cid = str(label).split(" ", 1)[0].lower()
        if cid.startswith("w"):
            seg["warnings"].append({
                "id": cid, "label": str(label), "ok": ok,
                "status": "WARN" if str(note).startswith("⚠") else "OK",
                "note": str(note)})
        else:
            seg["conditions"].append({
                "id": cid, "label": str(label), "ok": ok,
                "status": "UNKNOWN" if ok is None else ("PASS" if ok else "FAIL"),
                "note": str(note)})
    cl = _try(_checklist_of, cond_rows, cand, window, as_of)
    seg["passed"] = None if cl is None else entry.passed_of(cl)
    return seg


# --------------------------------------------------------------------------- B 段

def _seg_b(code: str, as_of: date, basic: dict) -> dict:
    """B 段：历史身份 + 历史唯一最高板与后续 T+1/T+3/T+5 + 历史信号及结果。"""
    first_bar = basic.get("first_bar_date")
    new_issuer = None if first_bar is None else _try(stock_algo.is_new_issuer,
                                                     first_bar, as_of)
    seg: dict[str, Any] = {
        "title": TITLE_B, "available": True,
        "identity": {
            "name": basic.get("name"), "industry": basic.get("industry"),
            "market_cap": basic.get("market_cap"), "first_bar_date": first_bar,
            "list_date": basic.get("list_date"), "is_st": basic.get("is_st"),
            "in_market": basic.get("in_market"), "is_new_issuer": new_issuer,
        },
        "sole_top_history": [], "sole_top_n": 0, "return_summary": {},
        "signals": [], "signal_n": 0, "note": "",
    }
    if first_bar is None:
        seg["note"] = "stock_basic.first_bar_date 缺失 → 次新判定不可核验。"

    for r in _rows(_frame(_SOLE_TOP_SQL, (code, as_of, code, as_of, _LIMIT))):
        seg["sole_top_history"].append({
            "date": _opt(r, "date"),
            "t1_ret": _opt(r, "t1_ret"), "t3_ret": _opt(r, "t3_ret"),
            "t5_ret": _opt(r, "t5_ret"),
        })
    seg["sole_top_n"] = len(seg["sole_top_history"])
    hist = seg["sole_top_history"]
    for key in ("t1_ret", "t3_ret", "t5_ret"):
        seg["return_summary"][f"avg_{key.split('_')[0]}"] = stock_algo.mean(
            [h[key] for h in hist])
    t1 = [h["t1_ret"] for h in hist if h["t1_ret"] is not None]
    seg["return_summary"]["win_rate_t1"] = (
        None if not t1 else round(sum(1 for v in t1 if float(v) > 0) / len(t1) * 100, 1))

    for r in _rows(_frame(_SIGNAL_SQL, (code, as_of, _LIMIT))):
        seg["signals"].append({
            "confirm_date": _opt(r, "confirm_date"), "action": _opt(r, "action"),
            "status": _opt(r, "status"), "buy_window": _opt(r, "buy_window"),
            "source": _opt(r, "source"), "checklist": _as_rows(_opt(r, "checklist")),
            "outcome": {
                "t1_gap": _opt(r, "t1_gap"), "t1_promote": _opt(r, "t1_promote"),
                "t1_close_ret": _opt(r, "t1_close_ret"), "max_up5": _opt(r, "max_up5"),
                "max_dd5": _opt(r, "max_dd5"), "t5_close_ret": _opt(r, "t5_close_ret"),
                "rule_ret_a": _opt(r, "rule_ret_a"),
                "rule_ret_d": _opt(r, "rule_ret_d"), "complete": _opt(r, "complete"),
            },
        })
    seg["signal_n"] = len(seg["signals"])
    return seg


# --------------------------------------------------------------------------- C 段

def _seg_c(code: str, as_of: date, layer: str | None, market_row: Any,
           stats: dict | None) -> dict:
    """C 段：晋级率双口径 + divergence + 题材完整性 + 生态评级 + 同状态赔率。"""
    stats = stats or {}
    promo_row = stats.get("promo_row") or None
    fwd5 = stats.get("fwd5") or None
    buy_point = stats.get("buy_point") or None

    promotion = None
    if promo_row:
        promotion = {k: promo_row.get(k) for k in
                     ("layer", "total", "promoted", "rate", "rate_exchange",
                      "divergence", "perf_median", "win_rate", "perf_n", "fail_perf")}

    rating = _opt(market_row, "dragon_env")
    dragon = None
    if rating:
        dragon = {"rating": rating,
                  "reasons": _as_json(_opt(market_row, "dragon_env_reasons")),
                  "risks": _as_json(_opt(market_row, "dragon_env_risks")),
                  "accelerate": _opt(market_row, "accelerate"),
                  "accel_reason": _opt(market_row, "accel_reason")}

    themes = []
    for r in _rows(_frame(_THEME_SQL, (code, as_of, _THEME_LIMIT))):
        themes.append({k: _opt(r, k) for k in
                       ("date", "primary_theme", "role", "confidence", "completeness",
                        "status", "highest_board", "member_count", "top_code")})

    seg: dict[str, Any] = {
        "title": TITLE_C, "layer": layer, "promotion": promotion,
        "forward5": fwd5, "dragon_env": dragon,
        "diverge": _opt(market_row, "diverge"), "odds": buy_point,
        "themes": themes, "available": False, "note": "",
    }
    missing = []
    if not stats:
        missing.append("统计上下文未注入（直连调用 diagnose）")
    if not promo_row:
        missing.append("同层级晋级率无行")
    if not fwd5:
        missing.append("同层级前瞻样本不足")
    if not dragon:
        missing.append("当日无生态评级")
    if not themes:
        missing.append("当日无题材标签/题材组行")
    seg["available"] = bool(promotion or fwd5 or dragon or themes or buy_point)
    seg["note"] = "" if seg["available"] else "统计腿全部缺数：" + "；".join(missing)
    if seg["available"] and missing:
        seg["note"] = "部分缺数：" + "；".join(missing)
    return seg


# --------------------------------------------------------------------------- 入口

def diagnose(code: str, as_of: date | None = None, *,
             stats: dict | None = None) -> dict:
    """三段式个股诊断（/api/stock 的 `diagnose` 段；也可独立调用）。

    Args:
        code: 股票代码。
        as_of: 评估日期，默认上一交易日。
        stats: 由 `services/stock_service` 注入的已算好统计
            {"layer", "promo_row", "fwd5", "buy_point"}；缺省时 C 段统计腿降级。

    Returns:
        {"code", "as_of", "technical", "fundamental", "market", "A", "B", "C"}。
        任一段缺数只写 note，不抛异常。
    """
    if as_of is None:
        prev = prev_trading_day(date.today())
        as_of = prev if prev else date.today()
    code = (code or "").strip()

    technical = {}
    for r in _rows(_frame(_TECHNICAL_SQL, (code, as_of)))[:1]:
        technical = {
            "is_limit_up": bool(_opt(r, "is_limit_up", False)),
            "is_exchange": bool(_opt(r, "is_exchange", False)),
            "cont_days": int(_opt(r, "cont_days", 0) or 0),
            "amplitude": _opt(r, "amplitude"),
        }

    fundamental = {}
    for r in _rows(_frame(_FUNDAMENTAL_SQL, (code,)))[:1]:
        fundamental = {
            "name": str(_opt(r, "name", "")),
            "industry": None if _opt(r, "industry") is None else str(_opt(r, "industry")),
            "market_cap": _opt(r, "market_cap"),
            "is_st": _opt(r, "is_st"),
            "list_date": _opt(r, "list_date"),
            "first_bar_date": _opt(r, "first_bar_date"),
            "in_market": _opt(r, "in_market"),
        }

    market_row: Any = {}
    market: dict[str, Any] = {}
    for r in _rows(_frame(_MARKET_SQL, (as_of,)))[:1]:
        market_row = r
        market = {"phase": str(_opt(r, "phase", "")),
                  "buy_window": str(_opt(r, "buy_window", ""))}
        for key in ("has_candidate", "diverge", "force_liquidate", "max_limit_days",
                    "dragon_env", "accelerate", "accel_reason", "reason",
                    "tradable_max_days"):
            market[key] = _opt(r, key)

    window = market.get("buy_window") or "NONE"

    def guard(title: str, fn: Callable, *args, **kwargs) -> dict:
        out = _try(fn, *args, **kwargs)
        if isinstance(out, dict):
            return out
        return {"title": title, "available": False,
                "note": "该段数据不可用（查询/判定异常），已降级。"}

    seg_a = guard(TITLE_A, _seg_a, code, as_of, window, bool(market_row), technical)
    seg_b = guard(TITLE_B, _seg_b, code, as_of, fundamental)
    # C 段层级：优先 A 段的梯队在册层（ladder_day），否则用调用方注入的连板层级
    # （stock_service 的 verdict.layer 来自当日行情 cont_days；一字板/未在册时仍可给层）
    layer = (seg_a or {}).get("layer") or (stats or {}).get("layer")
    seg_c = guard(TITLE_C, _seg_c, code, as_of, layer, market_row, stats)
    return {
        "code": code, "as_of": as_of, "technical": technical,
        "fundamental": fundamental, "market": market,
        "A": seg_a, "B": seg_b, "C": seg_c,
        "note": "三段式：A 当日身份与五条件（口径 = algorithms/entry.py）；"
                "B 历史身份、唯一最高板与信号结果；C 统计赔率（PIT，非未来承诺）。",
    }
