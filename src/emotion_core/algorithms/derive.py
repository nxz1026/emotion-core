"""T2 衍生层：daily_bar → derived_bar（涨停/一字/换手/炸板/振幅/连板数）。

口径（PLAN §1.2 / D7 / R3）：
- universe = 全市场（沪深主板 + 创业板 + 科创板），剔 ST、**剔北交所（C7）**
  —— 注意不是「仅主板」：主板前缀只用于梯队/最高板（emotion/ ladder 侧过滤），
  情绪家数口径是全市场。
- is_limit_up: close == 涨停价（收盘封住）；touched_limit: high >= 涨停价
- is_bomb: 触板未封；is_one_word: 涨停且 low==涨停价（全天封死）
- is_exchange(D7 有效投票): is_limit_up 且 low < 涨停价（含 T 字/换手板）
- 涨停价用"分"整数半up舍入：(pre_cents*11+5)//10，与 utils/price.py 等价
"""

# ✅ 已有 Rust 实现：src/emotion_core/core/src/indicators.rs (compute_derived)
# 本文件保留作为参考实现和对账基准，不删除。

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import execute

log = logging.getLogger("emotion_core.derive")


def _streaks(flags: list[bool]) -> list[int]:
    """连板数参考实现（单测锁死；生产走 SQL 窗口函数，二者语义一致）。"""
    out: list[int] = []
    run = 0
    for f in flags:
        run = run + 1 if f else 0
        out.append(run)
    return out


def _pct_case(col: str = "code") -> str:
    """按板块前缀生成涨停分子 CASE 表达式（110=+10%），config 驱动禁 magic number。"""
    whens = " ".join(f"WHEN left({col}, 2) = '{p}' THEN {v}"
                     for p, v in sorted(CONFIG.LIMIT_PCT_BY_PREFIX.items()))
    return f"CASE {whens} ELSE {CONFIG.LIMIT_PCT_DEFAULT} END"


def _bse_filter(col: str = "s.code") -> str:
    """C7：北交所剔除条件（config 驱动）。

    数据层保留北交所（daily_bar / stock_basic 照存），但**不参与情绪判定**：
    涨停价/连板/家数口径全按沪深算。此前只靠「北交所无日线源」隐式成立——
    一旦补上北交所源，这些行会静默混进 zt/zb/跌停计数（家数口径是全市场，
    唯一挡住它的就是本条件）。守护测试见
    tests/caliber/test_c7_bse_excluded_from_facts.py。

    实现用 `left(col, n) <> '前缀'` 而**不用 `LIKE '4%'`**：SQL 直接交给
    psycopg.execute()，语句里的裸 `%` 会被当成占位符解析（ProgrammingError:
    only '%s', '%b', '%t' are allowed as placeholders）——前缀比较同时避开
    这个坑，且与 Rust/Python 镜像实现逐字同形。
    """
    conds = " AND ".join(f"left({col}, {len(p)}) <> '{p}'"
                         for p in CONFIG.BSE_EXCLUDED_PREFIXES)
    return f"({conds})"


def _finite(col: str) -> str:
    """价格列可用性：非 NULL 且非 NaN（numeric 允许 'NaN' 字面量）。

    Postgres 的 numeric/float NaN 与 IEEE 不同：**NaN 等于 NaN 且大于一切非 NaN**，
    所以 `x = x`、`x > 0` 对 NaN 都为真，NaN 行会穿过 `pre_close > 0` 直接进
    `round(...)::bigint` → `FeatureNotSupported: cannot convert NaN to bigint`。
    实测 2026 库内有 226 行（新股上市首日，pre_close 无昨收 → EM 给 NaN）。
    显式比较 'NaN' 是唯一可靠的排除方式。
    """
    return f"({col} IS NOT NULL AND {col} <> 'NaN'::numeric)"


# 涨停价 SQL 整数实现（板块感知，情绪计数用）
_DERIVE_SQL = """
WITH u AS (
  SELECT b.code, b.date,
         ((round(b.pre_close * 100))::bigint * {pct} + 50) / 100 AS up_c,
         ((round(b.pre_close * 100))::bigint * (200 - {pct}) + 50) / 100 AS dn_c,
         (round(b.close * 100))::bigint AS c_c,
         (round(b.high * 100))::bigint  AS h_c,
         (round(b.low * 100))::bigint   AS l_c,
         (b.high - b.low) / b.pre_close * 100 AS amp
  FROM daily_bar b JOIN stock_basic s ON s.code = b.code
  WHERE NOT s.is_st
    AND {bse}
    AND b.date BETWEEN %s AND %s
    AND b.pre_close > 0 AND {finite_pre}          -- 无昨收（首日/NaN）行剔除
    AND {finite_close} AND {finite_high} AND {finite_low})
INSERT INTO derived_bar (code, date, is_limit_up, touched_limit, is_bomb,
                         is_one_word, is_exchange, is_limit_down, amplitude)
SELECT code, date,
       COALESCE(c_c = up_c, false), COALESCE(h_c >= up_c, false),
       COALESCE(h_c >= up_c AND c_c <> up_c, false),
       COALESCE(c_c = up_c AND l_c = up_c, false),
       COALESCE(c_c = up_c AND l_c < up_c, false),
       COALESCE(c_c = dn_c, false), amp
FROM u
ON CONFLICT (code, date) DO UPDATE SET
  is_limit_up = EXCLUDED.is_limit_up, touched_limit = EXCLUDED.touched_limit,
  is_bomb = EXCLUDED.is_bomb, is_one_word = EXCLUDED.is_one_word,
  is_exchange = EXCLUDED.is_exchange, is_limit_down = EXCLUDED.is_limit_down,
  amplitude = EXCLUDED.amplitude"""


def render_sql() -> str:
    """渲染执行用的衍生 SQL（占位符全部替换），供执行与口径守护测试共用。

    三组 config 驱动谓词：涨停价比 {pct}（板块比例）、北交所剔除 {bse}（C7）、
    价格可用性 {finite_*}（NULL/NaN 剔除）。
    """
    return (_DERIVE_SQL
            .replace("{pct}", _pct_case("b.code"))
            .replace("{bse}", _bse_filter("s.code"))
            .replace("{finite_pre}", _finite("b.pre_close"))
            .replace("{finite_close}", _finite("b.close"))
            .replace("{finite_high}", _finite("b.high"))
            .replace("{finite_low}", _finite("b.low")))


def compute_derived(start: date, end: date) -> int:
    """区间衍生（SQL 下推全市场，幂等 upsert）。"""
    return execute(render_sql(), (start, end))


_CONT_SQL = """
WITH f AS (
  SELECT code, date, is_limit_up,
         SUM(CASE WHEN NOT is_limit_up THEN 1 ELSE 0 END)
           OVER (PARTITION BY code ORDER BY date) AS grp
  FROM derived_bar WHERE date BETWEEN %s AND %s),
s AS (
  SELECT code, date,
         ROW_NUMBER() OVER (PARTITION BY code, grp ORDER BY date) AS rn
  FROM f WHERE is_limit_up)
UPDATE derived_bar d SET cont_days = s.rn
FROM s WHERE d.code = s.code AND d.date = s.date"""


def compute_cont_days(start: date, end: date) -> int:
    """连板数回填（SQL 窗口函数）。"""
    return execute(_CONT_SQL, (start, end))


def zero_reset(start: date, end: date) -> int:
    """非涨停日 cont_days 归零。"""
    return execute(
        "UPDATE derived_bar SET cont_days = 0 "
        "WHERE date BETWEEN %s AND %s AND NOT is_limit_up AND cont_days <> 0",
        (start, end))


def derive_range(start: date, end: date) -> None:
    """全量重算编排（幂等）：衍生 + cont_days + 归零。"""
    warm = CONFIG.DATA_START - timedelta(days=30)
    n = compute_derived(warm, end)
    c = compute_cont_days(warm, end)
    z = zero_reset(warm, end)
    log.info("derived_bar %s~%s：%d 行衍生，%d 行连板回填，%d 行归零"
             "（喂养窗口自 %s）", start, end, n, c, z, warm)
