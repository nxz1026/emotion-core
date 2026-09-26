"""T2 衍生层对账：自算 cont_days vs 东财连板数（reconcile / reconcile_range）。

语义逐字照搬 lkl/services/derive.py:127-167。差异仅 IO 适配：
- `from lkl import config` + `config.BOARD_PREFIXES` →
  `from emotion_core.utils.config import CONFIG` + `CONFIG.BOARD_PREFIXES`（同值）；
- `from lkl.utils import db` + `db.query_df` → `from emotion_core.utils.db import query_df`。
SQL 文本（含 f-string 占位与 `%%ST%%` 转义）与参数顺序逐字不变。
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df


def reconcile(trade_date: date) -> pd.DataFrame:
    """主板口径：自算 cont_days vs 东财连板数，返回差异行（含缺失）。"""
    ph = ", ".join(["%s"] * len(CONFIG.BOARD_PREFIXES))
    return query_df(
        f"SELECT p.code, p.name, p.cont_days_em, d.cont_days, d.is_limit_up "
        f"FROM limit_pool_em p LEFT JOIN derived_bar d "
        f"  ON d.code = p.code AND d.date = p.date "
        f"WHERE p.date = %s AND p.pool_type = 'ZT' AND NOT p.name LIKE '%%ST%%' "
        f"  AND left(p.code, 3) IN ({ph}) "
        f"  AND d.cont_days IS DISTINCT FROM p.cont_days_em",
        (trade_date, *CONFIG.BOARD_PREFIXES))


def reconcile_range(start: date, end: date) -> pd.DataFrame:
    """区间对账汇总：逐日 池内主板数/差异数/一致率。

    V6（二轮审计）：单条聚合 SQL 替代逐日两次查询（645 天≈1290 次 SSL
    握手 → 1 次）；差异计数沿用 reconcile 的口径（ZT 池主板自算 vs 池差集）。
    """
    ph = ", ".join(["%s"] * len(CONFIG.BOARD_PREFIXES))
    df = query_df(
        "SELECT p.date, count(*) AS pool_mb,"
        " count(*) FILTER (WHERE d.code IS NULL) AS diff,"
        " count(*) FILTER (WHERE d.code IS NOT NULL"
        "   AND d.cont_days <> p.cont_days_em) AS height_diff"
        " FROM limit_pool_em p"
        " JOIN stock_basic s ON s.code = p.code"
        " LEFT JOIN derived_bar d ON d.code = p.code AND d.date = p.date"
        "   AND d.is_limit_up"
        " WHERE p.date BETWEEN %s AND %s AND p.pool_type = 'ZT' AND NOT s.is_st"
        f" AND left(p.code,3) IN ({ph})"
        " GROUP BY p.date ORDER BY p.date",
        (start, end, *CONFIG.BOARD_PREFIXES))
    if df.empty:
        return df
    # P1-10：高度差异独立列——缺票(diff)与板数不同(height_diff)分开报，
    # 同码同日但连板高度错时不再冒充匹配。
    df["mismatch"] = df["diff"] + df["height_diff"]
    df["match_pct"] = ((df["pool_mb"] - df["mismatch"]) / df["pool_mb"] * 100
                       ).round(2).where(df["pool_mb"] > 0, 100.0)
    return df.reset_index(drop=True)
