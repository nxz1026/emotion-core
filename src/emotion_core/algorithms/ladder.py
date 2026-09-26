"""T4 连板梯队 + 龙头判定。语义逐字照搬 lkl/services/ladder.py。

口径（不得改动）：
- H = 换手口径最高连板数（一字板不参与最高层判定，海鸥住工规则）
- 同身位组 = cont_days == H 且 is_exchange
- 唯一换手高标 = 同身位仅 1 只 且 H >= CONFIG.MIN_LEADER_DAYS
- 主板口径（拍板 D1）：仅 600/601/603/605/000/001/002；次新（首 bar 晚于
  目标日 -90 自然日）剔除
- ladder_day 按日全量替换：DELETE + INSERT 同一事务（W3 审计 P0-8）

与 oracle 的两处显式差异（结构，非口径）：
1. build(rows) 是零 SQL 纯函数，入参为 derived_bar dict 行；SQL 归
   build_from_db / persist。oracle 的 build(trade_date) 自带 SQL 与 JOIN。
2. is_top / is_sole_top 由 build 填写——本项目 domain.LadderDay 是 frozen 且含
   该两字段，oracle 则在 persist 阶段才回填；R2 计数（y_*）纯函数层无法得
   知，build 留 0，persist 回填。其余字段语义不变。

术语：换手高标是「换手口径最高」，绝对最高板可能更高（一字垄断）。
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date

import psycopg

from emotion_core.domain.ladder import LadderDay
from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import connect, query_df
from emotion_core.utils.dates import prev_trading_day

log = logging.getLogger("emotion_core.ladder")

# 主板前缀：lkl config.BOARD_PREFIXES 逐字照搬。config.py 尚无该常量，
# 暂存本文件（唯一出口纪律的例外，待 config.py 补齐后迁走）。
MAIN_BOARD_PREFIXES: tuple[str, ...] = ("600", "601", "603", "605", "000", "001", "002")

# 次新剔除：lkl config.NEW_ISSUER_FILTER 逐字照搬（首 bar 缺失视作次新，
# 保守方向）。引用处需按 SQL 出现顺序铺两个 %s = 目标日期。
NEW_ISSUER_FILTER = ("(s.first_bar_date IS NOT NULL AND s.first_bar_date <="
                     " GREATEST(%s - interval '90 days', date '2023-11-26'))")

_BOARD_PH = ", ".join(["%s"] * len(MAIN_BOARD_PREFIXES))

_LADDER_SQL = (
    "SELECT d.date, d.code, d.cont_days, d.is_exchange"
    " FROM derived_bar d"
    " JOIN stock_basic s USING (code)"
    f" WHERE d.date = %s AND d.cont_days >= 2 AND left(d.code, 3) IN ({_BOARD_PH})"
    f" AND {NEW_ISSUER_FILTER}"
    " ORDER BY d.cont_days DESC, d.code"
)

_UP_SQL = ("SELECT code FROM derived_bar"
           f" WHERE date = %s AND is_exchange AND left(code, 3) IN ({_BOARD_PH})")

_NO_DATA_SQL = "SELECT count(*) AS n FROM derived_bar WHERE date = %s"

_UPSERT_SQL = (
    "INSERT INTO ladder_day"
    " (date, code, cont_days, is_exchange, is_top, is_sole_top,"
    "  y_top_group_count, y_top_survivor_count)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    " ON CONFLICT (date, code) DO UPDATE SET"
    "   cont_days = EXCLUDED.cont_days, is_exchange = EXCLUDED.is_exchange,"
    "   is_top = EXCLUDED.is_top, is_sole_top = EXCLUDED.is_sole_top,"
    "   y_top_group_count = EXCLUDED.y_top_group_count,"
    "   y_top_survivor_count = EXCLUDED.y_top_survivor_count"
)


def top_group(rows: list[LadderDay]) -> list[LadderDay]:
    """同身位组：换手板口径的最高连板集合。"""
    ex = [r for r in rows if r.is_exchange]
    if not ex:
        return []
    h = max(r.cont_days for r in ex)
    return [r for r in ex if r.cont_days == h]


def sole_top(rows: list[LadderDay], min_days: int | None = None) -> LadderDay | None:
    """唯一换手高标（§1.4）：换手同身位仅 1 只且达最低板数门槛。"""
    md = CONFIG.MIN_LEADER_DAYS if min_days is None else min_days
    tg = top_group(rows)
    return tg[0] if len(tg) == 1 and tg[0].cont_days >= md else None


def build(rows: list[dict]) -> list[LadderDay]:
    """derived_bar 行（dict，键 = 列名）-> 当日梯队，按板数降序。

    纯函数：不连 DB、不做板数判定（cont_days / is_exchange / is_one_word 由
    indicators.compute_derived 产出）。输入顺序无关——内部按
    (cont_days DESC, code ASC) 排序，与 oracle SQL 的 ORDER BY 一致。
    """
    ladder = [LadderDay(
        date=r["date"], code=r["code"], cont_days=int(r["cont_days"]),
        is_exchange=bool(r.get("is_exchange", False)),
        is_top=False, is_sole_top=False,
        # R2 计数需查昨日盘面，纯函数层不可得：留 0，由 persist 回填。
        y_top_group_count=int(r.get("y_top_group_count", 0)),
        y_top_survivor_count=int(r.get("y_top_survivor_count", 0)),
    ) for r in rows]
    ladder.sort(key=lambda r: (-r.cont_days, r.code))
    top_codes = {r.code for r in top_group(ladder)}
    sole = sole_top(ladder)
    return [replace(r, is_top=r.code in top_codes,
                    is_sole_top=sole is not None and r.code == sole.code)
            for r in ladder]


def build_from_db(trade_date: date,
                  conn: psycopg.Connection | None = None) -> list[LadderDay]:
    """当日梯队（主板 cont_days>=2，剔除次新），读 derived_bar -> build()。

    conn 传入则复用调用方连接/事务，否则自开自关。
    """
    df = query_df(_LADDER_SQL, (trade_date, *MAIN_BOARD_PREFIXES, trade_date),
                  conn=conn)
    return build(df.to_dict("records"))


def y_survivors(trade_date: date,
                conn: psycopg.Connection | None = None) -> tuple[set[str], set[str]]:
    """P1-2：昨日最高组代码集 + 其中今日换手幸存代码集。

    幸存口径 V3：is_exchange（换手）——一字缩量板不算可参与幸存。
    """
    prev = prev_trading_day(trade_date, conn=conn)
    if prev is None:
        return set(), set()
    y_tg = top_group(build_from_db(prev, conn=conn))
    if not y_tg:
        return set(), set()
    y_codes = {r.code for r in y_tg}
    up = set(query_df(_UP_SQL, (trade_date, *MAIN_BOARD_PREFIXES), conn=conn)["code"])
    return y_codes, y_codes & up


def y_competition(trade_date: date,
                  conn: psycopg.Connection | None = None) -> tuple[int, int]:
    """R2 淘汰赛况（兼容包装）：昨日组只数、今日幸存只数。"""
    y, s = y_survivors(trade_date, conn=conn)
    return len(y), len(s)


def _guard_no_data(trade_date: date) -> None:
    """P2（三轮审计）：上游完整性凭证——derived_bar 当日 0 行 = 缺数日
    （它是全市场衍生表，任何交易日都应有数千行），禁止当「合法零」
    清掉有效数据；有行但梯队为空才是真平静。"""
    n = query_df(_NO_DATA_SQL, (trade_date,))["n"].iloc[0]
    if n == 0:
        raise RuntimeError(f"ladder_day {trade_date}：derived_bar 当日 0 行——"
                           "上游缺数，拒绝清空，请先重跑采集")


def persist(trade_date: date) -> int:
    """梯队判定结果写 ladder_day（含 is_top/is_sole_top/R2 计数）。

    A8（审计 P2）：按日全量替换——upsert 无法移除「已跌出梯队的旧票」。
    W3（二轮审计 P0-8）：DELETE+INSERT 同一事务，失败整体回滚。
    """
    rows = build_from_db(trade_date)
    if not rows:
        _guard_no_data(trade_date)
        with connect() as conn:
            conn.execute("DELETE FROM ladder_day WHERE date = %s", (trade_date,))
        log.warning("ladder_day %s：当日无梯队（真平静，清空旧记录）", trade_date)
        return 0
    top_codes = {r.code for r in rows if r.is_top}
    g, s = y_competition(trade_date)
    data = [(r.date, r.code, r.cont_days, r.is_exchange, r.is_top,
             r.is_sole_top, g, s) for r in rows]
    with connect() as conn:                 # 单事务：异常回滚，不复现「清空后崩」
        conn.execute("DELETE FROM ladder_day WHERE date = %s", (trade_date,))
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, data)
    log.info("ladder_day %s：%d 行（最高组 %d 只，唯一高标 %s）",
             trade_date, len(data), len(top_codes),
             sole.code if (sole := sole_top(rows)) else "无")
    return len(data)
