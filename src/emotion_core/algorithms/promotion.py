"""晋级矩阵（PLAN §1.10 / F3）：分层名义/换手双口径晋级率。

口径（逐字对齐 lkl `services/promotion.py` @ eb53a46，ora 对账见 §对账）：
- 标的池：主板（BOARD_PREFIXES）+ 剔次新（first_bar_date 缺失按次新剔，90 自然日豁免）；
  ST 由 derived_bar 上游保证已剔。
- 分层 `PROMOTION_LAYERS`，每层：
    分母 promote_from      = 昨日 cont_days 落在该层的只数
    名义晋级 promote_nominal = 其中今日 cont_days >= 昨日+1 的只数（一字计入）
    换手晋级 promote_exchange= 名义晋级 且 今日 is_exchange（可成交口径，真机会）
    名义/换手晋级率          = 分子/分母，分母 < PROMOTION_MIN_DENOM → None（F6 不补零）
    背离度 divergence        = 名义率 - 换手率（高 = 高度靠一字推、机会在消失）
    失败负反馈 fail_perf     = 该层晋级失败股今日平均涨幅 %（样本 < 3 → None）
- 边界日（无前一交易日）或今日 derived_bar 整体缺失 → 返回 []（不产出假 0%）。

对账：`tests/oracle/fixtures/promotion_day.json` 全量 3305 行 / 661 交易日逐列一致
（promote_from/nominal/exchange/rate_*/divergence/fail_perf）。仅用主板前缀过滤会对
1->2 层少算 3 只、全量 122 行偏差；叠加次新过滤后 0 偏差。

与用户给定的「参考 SQL」差异：参考 SQL 省去了主板前缀与次新过滤，且无 fail_perf 的
daily_bar 价格连接。本实现按 docs/07 §2.5「逐字搬」补齐，语义以上表为准。

分工：算法层做纯计算（`_matrix`/`_layer_row`），SQL 取数集中在 `_fetch_pairs`（本模块内，
数据访问层 loader 尚无晋级专用查询）。
"""

# ✅ 已有 Rust 实现：src/emotion_core/core/src/promotion.rs
# 本文件保留作为参考实现和对账基准，不删除。

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Sequence

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import connect

log = logging.getLogger(__name__)

# 标的池：主板（R1/R3 梯队限主板，与 lkl config.BOARD_PREFIXES 一致）
BOARD_PREFIXES: tuple[str, ...] = ("600", "601", "603", "605", "000", "001", "002")

# 层定义：(标签, 昨日板数下限, 昨日板数上限 None=不封顶)
PROMOTION_LAYERS: tuple[tuple[str, int, int | None], ...] = (
    ("1->2", 1, 1),
    ("2->3", 2, 2),
    ("3->4", 3, 3),
    ("4->5", 4, 4),
    ("5+->6+", 5, None),
)

# 昨日每只主板非次新连板股 → 今日结果（板数 / 是否换手 / 今日涨幅%）
_PAIRS_SQL = """
SELECT y.cont_days AS y_cont,
       COALESCE(t.cont_days, 0) AS t_cont,
       COALESCE(t.is_exchange, false) AS t_exchange,
       CASE WHEN tc.close IS NOT NULL AND yc.close > 0
            THEN (tc.close / yc.close - 1) * 100 END AS perf
FROM derived_bar y
JOIN stock_basic s ON s.code = y.code
JOIN daily_bar yc ON yc.code = y.code AND yc.date = y.date
LEFT JOIN derived_bar t ON t.code = y.code AND t.date = %(d)s
LEFT JOIN daily_bar tc ON tc.code = y.code AND tc.date = %(d)s
WHERE y.date = %(prev)s
  AND y.cont_days >= 1
  AND left(y.code, 3) = ANY(%(boards)s)
  AND s.first_bar_date IS NOT NULL
  AND s.first_bar_date <= GREATEST(%(prev)s::date
                                   - %(days)s * interval '1 day', %(floor)s::date)
"""

# 昨日每只连板股的一行：(昨日板数, 今日板数, 今日是否换手, 今日涨幅%|None)
Pair = tuple[int, int, bool, float | None]


def _prev_trading_day(cur: Any, trade_date: date) -> date | None:
    """前一交易日（日历事实取自 daily_bar，无则 None）。"""
    cur.execute("SELECT MAX(date) FROM daily_bar WHERE date < %s", (trade_date,))
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def _today_bar_count(cur: Any, trade_date: date) -> int:
    """当日 derived_bar 行数（0 = 采集缺失，不等于「全天无涨停」）。"""
    cur.execute("SELECT count(*) FROM derived_bar WHERE date = %s", (trade_date,))
    return int(cur.fetchone()[0])


def _fetch_pairs(cur: Any, trade_date: date, prev: date) -> list[Pair]:
    """取昨日→今日配对行（主板 + 剔次新，90 日门槛与地板取自 config 单一来源）。"""
    cur.execute(_PAIRS_SQL, {"d": trade_date, "prev": prev,
                             "boards": list(BOARD_PREFIXES),
                             "days": CONFIG.NEW_ISSUER_MIN_DAYS,
                             "floor": CONFIG.NEW_ISSUER_FLOOR})
    return [(int(r[0]), int(r[1]), bool(r[2]), None if r[3] is None else float(r[3]))
            for r in cur.fetchall()]


def _rate(part: int, whole: int, min_denom: int) -> float | None:
    """比率；分母不足门槛返回 None（F6 不猜）。"""
    if whole < min_denom:
        return None
    return round(part / whole, 4)


def _layer_row(trade_date: date, layer: str, lo: int, hi: int | None,
               pairs: Sequence[Pair], min_denom: int) -> dict[str, Any]:
    """单层统计：筛该层 → 数名义/换手晋级 → 算比率、背离、失败负反馈。"""
    sel = [p for p in pairs if p[0] >= lo and (hi is None or p[0] <= hi)]
    promoted = [p for p in sel if p[1] >= p[0] + 1]
    n_from = len(sel)
    n_nom = len(promoted)
    n_exch = sum(1 for p in promoted if p[2])
    r_nom = _rate(n_nom, n_from, min_denom)
    r_exch = _rate(n_exch, n_from, min_denom)
    div = None if r_nom is None or r_exch is None else round(r_nom - r_exch, 4)
    failed = [p[3] for p in sel if p[1] < p[0] + 1 and p[3] is not None]
    fail_perf = None if len(failed) < min_denom else round(sum(failed) / len(failed), 4)
    return {
        "date": trade_date,
        "layer": layer,
        "promote_from": n_from,
        "promote_nominal": n_nom,
        "promote_exchange": n_exch,
        "rate_nominal": r_nom,
        "rate_exchange": r_exch,
        "divergence": div,
        "fail_perf": fail_perf,
    }


def _matrix(trade_date: date, pairs: Sequence[Pair],
            min_denom: int = CONFIG.PROMOTION_MIN_DENOM) -> list[dict[str, Any]]:
    """按 PROMOTION_LAYERS 生成全层行（纯函数）。"""
    return [_layer_row(trade_date, name, lo, hi, pairs, min_denom)
            for name, lo, hi in PROMOTION_LAYERS]


def promotion_matrix(trade_date: date, *, conn: Any | None = None) -> list[dict[str, Any]]:
    """当日全层晋级矩阵；边界/缺数据日返回 []（不产出假数）。

    Args:
        trade_date: 交易日。
        conn: 可选 psycopg 连接（复用调用方事务）；默认自开自关。
    Returns:
        5 行 dict（PROMOTION_LAYERS 顺序）；无昨日数据或今日整体缺失 → []。
    """
    own = conn is None
    if own:
        conn = connect()
    try:
        with conn.cursor() as cur:
            prev = _prev_trading_day(cur, trade_date)
            if prev is None:
                log.warning("晋级矩阵 %s：无前一交易日，跳过", trade_date)
                return []
            if _today_bar_count(cur, trade_date) == 0:
                log.warning("晋级矩阵 %s：derived_bar 当日整体缺失，不产出假 0%%", trade_date)
                return []
            pairs = _fetch_pairs(cur, trade_date, prev)
    finally:
        if own:
            conn.close()
    if not pairs:
        return []
    return _matrix(trade_date, pairs)


_INSERT_SQL = """
INSERT INTO promotion_day
  (date, layer, promote_from, promote_nominal, promote_exchange,
   rate_nominal, rate_exchange, divergence, fail_perf)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (date, layer) DO UPDATE SET
  promote_from=EXCLUDED.promote_from,
  promote_nominal=EXCLUDED.promote_nominal,
  promote_exchange=EXCLUDED.promote_exchange,
  rate_nominal=EXCLUDED.rate_nominal,
  rate_exchange=EXCLUDED.rate_exchange,
  divergence=EXCLUDED.divergence,
  fail_perf=EXCLUDED.fail_perf
"""


def persist(trade_date: date) -> int:
    """写 promotion_day（按日 DELETE + upsert，同一事务），返回写入行数。

    A8：空结果同样替换——当日无晋级数据时清旧防幽灵行（源修正后旧层不得残留）。
    W3：DELETE+INSERT 同一连接事务，中途异常整体回滚。
    """
    rows = promotion_matrix(trade_date)
    with connect() as conn:
        conn.execute("DELETE FROM promotion_day WHERE date = %s", (trade_date,))
        if not rows:
            log.warning("promotion_day %s：当日无晋级数据（真平静，清空）", trade_date)
            return 0
        data = [(r["date"], r["layer"], r["promote_from"], r["promote_nominal"],
                 r["promote_exchange"], r["rate_nominal"], r["rate_exchange"],
                 r["divergence"], r["fail_perf"]) for r in rows]
        with conn.cursor() as cur:
            cur.executemany(_INSERT_SQL, data)
    log.info("promotion_day %s：%d 层", trade_date, len(rows))
    return len(rows)
