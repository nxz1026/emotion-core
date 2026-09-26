"""DB 适配层：提供算法模块需要的所有查询函数。

所有函数返回 domain 类型（bar.py/market.py/ladder.py/signal.py），不返回裸 DataFrame。
算法模块只依赖此层，不直接写 SQL。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from emotion_core.domain.bar import Bar, DerivedBar
from emotion_core.domain.market import MarketStat, Phase
from emotion_core.domain.ladder import LadderDay
from emotion_core.domain.signal import Action, Checklist, Signal, SignalSource
from emotion_core.utils.db import connect, transaction

log = logging.getLogger(__name__)


def load_derived_bars(d: date) -> list[DerivedBar]:
    """当日所有 derived_bar 行（含判据）。"""
    with connect() as conn:
        rows = conn.execute(
            """SELECT code, date, is_limit_up, is_limit_down,
                      is_one_word, is_exchange, is_bomb, touched_limit,
                      cont_days, amplitude
               FROM derived_bar WHERE date = %s""",
            (d,),
        ).fetchall()
        return [DerivedBar(
            code=r[0], date=r[1], is_limit_up=bool(r[2]), is_limit_down=bool(r[3]),
            is_one_word=bool(r[4]), is_exchange=bool(r[5]), is_bomb=bool(r[6]),
            touched_limit=bool(r[7]), cont_days=int(r[8]), amplitude=float(r[9]),
        ) for r in rows]


def load_stock_basic() -> dict[str, dict]:
    """证券主数据。返回 {code: {name, is_st, first_bar_date, ...}}。"""
    with connect() as conn:
        rows = conn.execute("SELECT code, name, is_st, first_bar_date FROM stock_basic").fetchall()
        return {r[0]: {"name": r[1], "is_st": bool(r[2]), "first_bar_date": r[3]} for r in rows}


def load_market_stat(d: date) -> Optional[MarketStat]:
    """当日 market_stat 行（不含 ecosystem，由 ecosystem 模块更新）。"""
    with connect() as conn:
        r = conn.execute(
            "SELECT date, phase, buy_window, limit_up_count, bomb_rate, limit_down_count,"
            " max_limit_days, force_liquidate, bomb_threshold, has_candidate"
            " FROM market_stat WHERE date = %s",
            (d,),
        ).fetchone()
        if r is None:
            return None
        return MarketStat(
            date=r[0], phase=Phase(r[1]) if r[1] else Phase.ICE,
            buy_window=r[2], limit_up_count=int(r[3] or 0),
            bomb_rate=None if r[4] is None else float(r[4]),
            limit_down_count=int(r[5] or 0), max_limit_days=int(r[6] or 0),
            force_liquidate=bool(r[7]),
            bomb_threshold=None if r[8] is None else float(r[8]),
            has_candidate=bool(r[9]),
        )


def load_ladder_history(d: date, n: int = 5) -> list[list[LadderDay]]:
    """最近 n 个交易日的 ladder_day 行。返回列表的列表（每日子一天）。"""
    from emotion_core.utils.dates import recent_trading_days
    days = recent_trading_days(n)
    result = []
    for day in days:
        if day >= d:
            continue
        with connect() as conn:
            rows = conn.execute(
                """SELECT date, code, cont_days, is_exchange, is_top, is_sole_top,
                          y_top_group_count, y_top_survivor_count
                   FROM ladder_day WHERE date = %s ORDER BY cont_days DESC""",
                (day,),
            ).fetchall()
            result.append([LadderDay(
                date=r[0], code=r[1], cont_days=int(r[2]), is_exchange=bool(r[3]),
                is_top=bool(r[4]), is_sole_top=bool(r[5]),
                y_top_group_count=int(r[6] or 0), y_top_survivor_count=int(r[7] or 0),
            ) for r in rows])
    return result


def load_signal_history(limit: int = 200) -> list[Signal]:
    """最近 limit 条 signal。"""
    with connect() as conn:
        rows = conn.execute(
            """SELECT confirm_date, code, action, buy_window, status
               FROM signal ORDER BY confirm_date DESC LIMIT %s""",
            (limit,),
        ).fetchall()
        return [Signal(
            code=r[1], date=r[0], action=Action(r[2]),
            checklist=Checklist(c1_uniqueness=None, c2_exchange=None, c3_elimination=None,
                                c4_min_days=None, c5_strength_diverge=None, w1_crowding=None),
            source=SignalSource.LIVE, status=r[4],
        ) for r in rows]


def upsert_ladder_day(rows: list[LadderDay]) -> int:
    """写入 ladder_day（DELETE+upsert 同一事务）。"""
    with transaction() as conn:
        if not rows:
            return 0
        d = rows[0].date
        conn.execute("DELETE FROM ladder_day WHERE date = %s", (d,))
        data = [(r.date, r.code, r.cont_days, r.is_exchange, r.is_top, r.is_sole_top,
                 r.y_top_group_count, r.y_top_survivor_count) for r in rows]
        conn.executemany(
            """INSERT INTO ladder_day
               (date, code, cont_days, is_exchange, is_top, is_sole_top,
                y_top_group_count, y_top_survivor_count)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (date, code) DO UPDATE SET
                 cont_days=EXCLUDED.cont_days, is_exchange=EXCLUDED.is_exchange,
                 is_top=EXCLUDED.is_top, is_sole_top=EXCLUDED.is_sole_top,
                 y_top_group_count=EXCLUDED.y_top_group_count,
                 y_top_survivor_count=EXCLUDED.y_top_survivor_count""",
            data,
        )
        return len(data)


def upsert_promotion_day(rows: list[dict]) -> int:
    """写入 promotion_day。rows 是 dict 列表。"""
    with transaction() as conn:
        if not rows:
            return 0
        d = rows[0]["date"]
        conn.execute("DELETE FROM promotion_day WHERE date = %s", (d,))
        data = [(r["date"], r["layer"], r["promote_nominal"], r["promote_exchange"],
                 r.get("rate_nominal"), r.get("rate_exchange"), r.get("divergence"),
                 r.get("fail_perf")) for r in rows]
        conn.executemany(
            """INSERT INTO promotion_day
               (date, layer, promote_nominal, promote_exchange, rate_nominal,
                rate_exchange, divergence, fail_perf)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (date, layer) DO UPDATE SET
                 promote_nominal=EXCLUDED.promote_nominal,
                 promote_exchange=EXCLUDED.promote_exchange,
                 rate_nominal=EXCLUDED.rate_nominal,
                 rate_exchange=EXCLUDED.rate_exchange,
                 divergence=EXCLUDED.divergence,
                 fail_perf=EXCLUDED.fail_perf""",
            data,
        )
        return len(data)


def insert_signal(sig: Signal) -> int:
    """写入 signal。"""
    with transaction() as conn:
        cl = sig.checklist
        return conn.execute(
            """INSERT INTO signal
               (confirm_date, code, action, buy_window, checklist, status)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (code, confirm_date) DO UPDATE SET
                 action=EXCLUDED.action, buy_window=EXCLUDED.buy_window,
                 checklist=EXCLUDED.checklist, status=EXCLUDED.status""",
            (sig.date, sig.code, sig.action.value, None,
             {"c1": cl.c1_uniqueness, "c2": cl.c2_exchange, "c3": cl.c3_elimination,
              "c4": cl.c4_min_days, "c5": cl.c5_strength_diverge, "w1": cl.w1_crowding},
             sig.status),
        ).rowcount


def update_market_stat_ecosystem(d: date, rating: str, reasons: str, risks: str) -> int:
    """更新 market_stat 的 ecosystem 字段。"""
    with transaction() as conn:
        return conn.execute(
            """UPDATE market_stat SET dragon_env=%s, dragon_env_reasons=%s,
                  dragon_env_risks=%s WHERE date=%s""",
            (rating, reasons, risks, d),
        ).rowcount


def upsert_market_stat(stat: MarketStat) -> int:
    """写入 market_stat。"""
    with transaction() as conn:
        return conn.execute(
            """INSERT INTO market_stat
               (date, phase, buy_window, limit_up_count, bomb_rate, limit_down_count,
                max_limit_days, force_liquidate, bomb_threshold, has_candidate)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (date) DO UPDATE SET
                 phase=EXCLUDED.phase, buy_window=EXCLUDED.buy_window,
                 limit_up_count=EXCLUDED.limit_up_count, bomb_rate=EXCLUDED.bomb_rate,
                 limit_down_count=EXCLUDED.limit_down_count,
                 max_limit_days=EXCLUDED.max_limit_days,
                 force_liquidate=EXCLUDED.force_liquidate,
                 bomb_threshold=EXCLUDED.bomb_threshold,
                 has_candidate=EXCLUDED.has_candidate""",
            (stat.date, stat.phase.value, stat.buy_window, stat.limit_up_count,
             stat.bomb_rate, stat.limit_down_count, stat.max_limit_days,
             stat.force_liquidate, stat.bomb_threshold, stat.has_candidate),
        ).rowcount
