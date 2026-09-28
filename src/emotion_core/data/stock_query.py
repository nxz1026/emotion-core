"""个股诊断的 SQL 层（data/ 是 SQL 的唯一住所）。

数据现状约束（2026-09-28 实测）：`signal` / `ladder_day` / `promotion_day` /
`theme_tag` / `limit_pool_em` / `hot_rank` / `signal_outcome` **均为 0 行**（日更链
从未成功跑完一轮），有数据的只有 `daily_bar` / `derived_bar` / `market_stat` /
`stock_basic`。故这里所有口径都从这四张表现算，且全部 **PIT 安全**（只用
`date <= 目标日` 的样本）；等那些表有数据后按同签名加"增强"分支即可。

三个必须记住的坑：
1. numeric 的 `NaN` **等于 NaN 且大于一切非 NaN**，`x > 0` / `x = x` 都挡不住，
   必须显式 `x <> 'NaN'::numeric`（见 `_finite`）；
2. psycopg 参数化语句里**任何裸 `%` 都会被当占位符**，故模糊匹配一律把 `%` 放进
   参数值（`name LIKE %s` + `f"%{kw}%"`），绝不写进 SQL 字面量；
3. 晋级率的分子必须是「**日历次日**是否晋级」，不能用 `lead(cont_days)` 那种
   "下一次涨停"写法（会把停牌间隔/跨段重来混进来，实测第一版把 1 板算成 17.5%）。
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df


def _finite(col: str) -> str:
    """非空且非 PG NaN 的数值列判据。"""
    return f"({col} IS NOT NULL AND {col} <> 'NaN'::numeric)"


def _layer_case(cont: str) -> str:
    """连板层级标签，与 `algorithms/promotion.py: PROMOTION_LAYERS` 同标签。"""
    return (f"CASE WHEN {cont} >= 5 THEN '5+->6+' "
            f"ELSE {cont} || '->' || ({cont} + 1) END")


def _layer_sql(cont: str) -> str:
    """层级标签 → cont_days 条件（`_layer_case` 的逆向，用于按层级取样本）。"""
    return (f"(CASE WHEN {cont} >= 5 THEN '5+->6+' "
            f"ELSE {cont} || '->' || ({cont} + 1) END)")


_LAYER_CASE = _layer_case("v.cont_days")

# 连板层级标签（顺序即展示顺序）。必须与 algorithms/promotion.py 的
# PROMOTION_LAYERS 标签一致——tests/unit/test_stock_query.py 有守护测试比对。
LAYER_LABELS: tuple[str, ...] = ("1->2", "2->3", "3->4", "4->5", "5+->6+")


def latest_trade_date() -> date | None:
    """库里最后一个有日线的交易日。"""
    df = query_df("SELECT max(date) AS d FROM daily_bar")
    return None if df.empty or df.iloc[0]["d"] is None else df.iloc[0]["d"]


def resolve_code(text: str, limit: int = 10) -> list[dict]:
    """把用户输入解析成候选股票：代码精确 → 代码前缀 → 名称模糊。

    名称模糊的 `%` 走参数值，不进 SQL 字面量（psycopg 占位符规则）。
    """
    q = (text or "").strip()
    if not q:
        return []
    digits = "".join(ch for ch in q if ch.isdigit())
    rows: list[dict] = []
    if digits:
        df = query_df(
            """SELECT code, name, is_st, industry, first_bar_date
               FROM stock_basic
               WHERE code = %s OR code LIKE %s
               ORDER BY (code = %s) DESC, code LIMIT %s""",
            (digits, f"{digits}%", digits, limit))
        rows = df.to_dict("records")
    if not rows:
        df = query_df(
            """SELECT code, name, is_st, industry, first_bar_date
               FROM stock_basic WHERE name LIKE %s
               ORDER BY (name = %s) DESC, code LIMIT %s""",
            (f"%{q}%", q, limit))
        rows = df.to_dict("records")
    return rows


def basic_row(code: str) -> dict | None:
    """证券主数据一行。"""
    df = query_df(
        """SELECT code, name, is_st, industry, market_cap, list_date,
                  first_bar_date, in_market
           FROM stock_basic WHERE code = %s""", (code,))
    return None if df.empty else df.iloc[0].to_dict()


def recent_series(code: str, end: date, bars: int = 30) -> list[dict]:
    """最近 bars 根日线（升序），带判据与涨跌幅。"""
    df = query_df(
        f"""SELECT d.date, d.open, d.high, d.low, d.close, d.pre_close,
                   d.volume, d.amount, d.turnover_rate,
                   CASE WHEN {_finite('d.pre_close')} AND d.pre_close <> 0
                        THEN round((d.close / d.pre_close - 1) * 100, 2) END AS pct_chg,
                   coalesce(v.is_limit_up, false)   AS is_limit_up,
                   coalesce(v.is_limit_down, false) AS is_limit_down,
                   coalesce(v.is_one_word, false)   AS is_one_word,
                   coalesce(v.is_exchange, false)   AS is_exchange,
                   coalesce(v.is_bomb, false)       AS is_bomb,
                   coalesce(v.touched_limit, false) AS touched_limit,
                   coalesce(v.cont_days, 0)         AS cont_days,
                   v.amplitude
            FROM daily_bar d
            LEFT JOIN derived_bar v ON v.code = d.code AND v.date = d.date
            WHERE d.code = %s AND d.date <= %s
            ORDER BY d.date DESC LIMIT %s""",
        (code, end, bars))
    if df.empty:
        return []
    return df.iloc[::-1].to_dict("records")


def structure_window(code: str, end: date, days: int = 60) -> dict:
    """近 days 个交易日的涨停结构统计（该股自己的样本）。"""
    df = query_df(
        """SELECT count(*) FILTER (WHERE v.is_limit_up)                        AS limit_up_n,
                   count(*) FILTER (WHERE v.touched_limit AND NOT v.is_limit_up) AS bomb_n,
                   count(*) FILTER (WHERE v.is_limit_up AND v.is_one_word)    AS one_word_n,
                   count(*) FILTER (WHERE v.is_limit_up AND v.is_exchange)    AS exchange_n,
                   count(*) FILTER (WHERE v.is_limit_down)                    AS limit_down_n,
                   coalesce(max(v.cont_days) FILTER (WHERE v.is_limit_up), 0) AS max_cont,
                   count(*)                                                   AS sample_n
            FROM (SELECT date FROM daily_bar
                  WHERE code = %s AND date <= %s ORDER BY date DESC LIMIT %s) w
            JOIN derived_bar v ON v.code = %s AND v.date = w.date""",
        (code, end, days, code))
    row = df.iloc[0].to_dict() if not df.empty else {}
    return {k: (0 if v is None else v) for k, v in row.items()}


def market_env(d: date) -> dict | None:
    """目标日市场情绪环境（market_stat 一行）。"""
    df = query_df(
        """SELECT date, phase, buy_window, limit_up_count, limit_down_count,
                  max_limit_days, bomb_rate, oneword_ratio, zt_performance,
                  bomb_threshold, force_liquidate, has_candidate, tradable_max_days
           FROM market_stat WHERE date = %s""", (d,))
    return None if df.empty else df.iloc[0].to_dict()


def promotion_table(end: date, min_denom: int | None = None,
                    pair_date: date | None = None) -> list[dict]:
    """各连板层级的**历史晋级率**（全市场时间聚合，PIT）。

    口径与 `algorithms/promotion.py` 对齐：分层标签、主板池、剔次新、
    次日 = **日历下一交易日**、名义/换手双口径、`rate` 分子用 `>= cont+1`、
    换手口径分母同为"昨日该层只数"。这样页面统计与日后 `promotion_day` 落库值同源。

    Args:
        end: 只用 `date < end` 的样本（PIT）。
        min_denom: 分母门槛，缺省 CONFIG.PROMOTION_MIN_DENOM（不足 → rate None）。
        pair_date: 只统计「次日 = pair_date」那一组，用于与 `promotion_matrix(单日)`
            逐层比对（真库校准用）。
    """
    boards = list(CONFIG.BOARD_PREFIXES)
    df = query_df(
        f"""WITH cal AS (SELECT DISTINCT date FROM daily_bar),
                 nxt AS (SELECT date, lead(date) OVER (ORDER BY date) AS next_date FROM cal),
                 y AS (
                   SELECT v.code, v.date, v.cont_days, n.next_date,
                          {_LAYER_CASE} AS layer
                   FROM derived_bar v
                   JOIN stock_basic s ON s.code = v.code
                   JOIN nxt n ON n.date = v.date
                   WHERE v.is_limit_up AND v.cont_days >= 1 AND v.date < %s
                     AND (%s::date IS NULL OR n.next_date = %s::date)
                     AND left(v.code, 3) = ANY(%s)
                     AND s.first_bar_date IS NOT NULL
                     AND s.first_bar_date <= GREATEST(v.date
                           - %s * interval '1 day', %s::date)
                 )
           SELECT y.layer,
                  count(*) AS total,
                  count(*) FILTER (WHERE coalesce(t.cont_days, 0) >= y.cont_days + 1)
                      AS promoted,
                  count(*) FILTER (WHERE coalesce(t.cont_days, 0) >= y.cont_days + 1
                                     AND t.is_exchange) AS promoted_ex,
                  count(perf) AS perf_n,
                  round(avg(perf)::numeric, 2) AS perf_avg,
                  round(percentile_cont(0.5) WITHIN GROUP (ORDER BY perf)::numeric, 2)
                      AS perf_median,
                  count(*) FILTER (WHERE perf > 0) AS perf_win_n,
                  count(*) FILTER (WHERE coalesce(t.cont_days, 0) < y.cont_days + 1
                                     AND perf IS NOT NULL) AS fail_n,
                  round(avg(perf) FILTER (WHERE coalesce(t.cont_days, 0)
                                          < y.cont_days + 1)::numeric, 2) AS fail_perf
           FROM y
           LEFT JOIN derived_bar t ON t.code = y.code AND t.date = y.next_date
           LEFT JOIN daily_bar yc ON yc.code = y.code AND yc.date = y.date
           LEFT JOIN daily_bar tc ON tc.code = y.code AND tc.date = y.next_date
           LEFT JOIN LATERAL (
               SELECT CASE WHEN {_finite('tc.close')} AND {_finite('yc.close')}
                            AND yc.close <> 0
                           THEN (tc.close / yc.close - 1) * 100 END AS perf) p ON true
           GROUP BY y.layer ORDER BY y.layer""",
        (end, pair_date, pair_date, boards, CONFIG.NEW_ISSUER_MIN_DAYS,
         CONFIG.NEW_ISSUER_FLOOR))
    floor_n = CONFIG.PROMOTION_MIN_DENOM if min_denom is None else min_denom
    # 无样本的层级也要出 0 行（与 promotion.py 一致：全层返回，不吞层）
    rows = {r["layer"]: r for r in df.to_dict("records")}
    empty = {"layer": None, "total": 0, "promoted": 0, "promoted_ex": 0, "perf_n": 0,
             "perf_avg": None, "perf_median": None, "perf_win_n": 0,
             "fail_n": 0, "fail_perf": None}
    out: list[dict] = []
    for label in LAYER_LABELS:
        r = rows.get(label) or dict(empty, layer=label)
        total = int(r["total"] or 0)
        promoted = int(r["promoted"] or 0)
        perf_n = int(r["perf_n"] or 0)
        out.append({
            "layer": r["layer"], "total": total, "promoted": promoted,
            # 分母不足门槛 → None（F6：不猜、不补零）
            "rate": None if total < floor_n else round(100.0 * promoted / total, 1),
            "rate_ratio": None if total < floor_n else round(promoted / total, 4),
            # 与 lkl/promotion.py 同义：换手口径分母同为"昨日该层只数"，
            # 分子是晋级里今日为换手板的只数（分母不是换手板总数！）
            "promoted_ex": int(r["promoted_ex"] or 0),
            "rate_exchange": (None if total < floor_n
                              else round(100.0 * (r["promoted_ex"] or 0) / total, 1)),
            "rate_exchange_ratio": (None if total < floor_n
                                    else round(int(r["promoted_ex"] or 0) / total, 4)),
            "divergence": (None if total < floor_n
                           else round(100.0 * (promoted - int(r["promoted_ex"] or 0)) / total, 1)),
            "fail_n": int(r["fail_n"] or 0),
            "fail_perf": None if r["fail_perf"] is None else float(r["fail_perf"]),
            "perf_n": perf_n,
            "perf_avg": None if r["perf_avg"] is None else float(r["perf_avg"]),
            "perf_median": None if r["perf_median"] is None else float(r["perf_median"]),
            "win_rate": (None if not perf_n
                         else round(100.0 * int(r["perf_win_n"] or 0) / perf_n, 1)),
        })
    return out


def layer_forward(end: date, layer_label: str, horizon: int = 5,
                  sample_cap: int = 600) -> dict:
    """同层级样本的 horizon 日前瞻收益分布（PIT，样本上限 sample_cap）。

    样本池与 `promotion_table` 一致（主板非次新连板股）；入场价 = 该日收盘，
    出场价 = 其后第 horizon 根日线收盘；(code, date) 是主键，LATERAL 定位很快。
    """
    boards = list(CONFIG.BOARD_PREFIXES)
    df = query_df(
        f"""SELECT s.code, s.date, b.close AS entry_close, nx.close AS exit_close,
                   CASE WHEN {_finite('b.close')} AND b.close <> 0 AND {_finite('nx.close')}
                        THEN (nx.close / b.close - 1) * 100 END AS ret
            FROM (SELECT v.code, v.date FROM derived_bar v
                  JOIN stock_basic s ON s.code = v.code
                  WHERE v.is_limit_up AND v.date < %s
                    AND left(v.code, 3) = ANY(%s)
                    AND s.first_bar_date IS NOT NULL
                    AND s.first_bar_date <= GREATEST(v.date
                          - %s * interval '1 day', %s::date)
                    AND {_layer_sql('v.cont_days')} = %s
                  ORDER BY v.date DESC LIMIT %s) s
            JOIN daily_bar b ON b.code = s.code AND b.date = s.date
            LEFT JOIN LATERAL (
                SELECT d2.close FROM daily_bar d2
                WHERE d2.code = s.code AND d2.date > s.date
                ORDER BY d2.date LIMIT 1 OFFSET %s) nx ON true""",
        (end, boards, CONFIG.NEW_ISSUER_MIN_DAYS, CONFIG.NEW_ISSUER_FLOOR,
         layer_label, sample_cap, horizon - 1))
    # 注意：pandas 把 SQL NULL 变 NaN，`r is not None` 挡不住 NaN（真库上
    # 尾部样本没有后续日线时就是这种情况），必须用 isfinite 过滤
    rets = (sorted(float(r) for r in df["ret"].tolist()
                   if r is not None and math.isfinite(float(r)))
            if "ret" in df.columns else [])
    if not rets:
        return {"layer": layer_label, "horizon": horizon, "n": 0,
                "median": None, "win_rate": None, "avg": None}
    mid = len(rets) // 2
    median = rets[mid] if len(rets) % 2 else (rets[mid - 1] + rets[mid]) / 2
    return {
        "layer": layer_label, "horizon": horizon, "n": len(rets),
        "median": round(median, 2),
        "win_rate": round(100.0 * sum(1 for r in rets if r > 0) / len(rets), 1),
        "avg": round(sum(rets) / len(rets), 2),
    }


def themes_of(code: str, d: date) -> list[dict]:
    """该股当日题材标签。`theme_tag` 当前 0 行 → 返回空列表（页面如实标注）。"""
    df = query_df(
        """SELECT date, primary_theme, secondary_themes, role, catalyst, confidence
           FROM theme_tag WHERE code = %s AND date <= %s
           ORDER BY date DESC LIMIT 3""",
        (code, d))
    return df.to_dict("records")


def market_top(d: date) -> list[dict]:
    """目标日全市场最高连板梯队（用于"该股 vs 市场"对照）。"""
    df = query_df(
        """SELECT v.code, s.name, v.cont_days, v.is_one_word, v.is_exchange
           FROM derived_bar v JOIN stock_basic s ON s.code = v.code
           WHERE v.date = %s AND v.is_limit_up
           ORDER BY v.cont_days DESC, v.code LIMIT 10""", (d,))
    return df.to_dict("records")


def bare_percent_check(sql: str) -> dict[str, Any]:
    """自检辅助：确认 SQL 里没有裸 `%`（psycopg 占位符事故的守护钩子）。"""
    stripped = sql.replace("%s", "").replace("%b", "").replace("%t", "")
    return {"bare_percent": "%" in stripped}
