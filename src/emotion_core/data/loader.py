"""DB 适配层：提供算法模块需要的所有查询函数。

所有函数返回 domain 类型（bar.py/market.py/ladder.py/signal.py），不返回裸 DataFrame。
算法模块只依赖此层，不直接写 SQL。
"""

from __future__ import annotations

import logging
from datetime import date

from emotion_core.domain.bar import DerivedBar
from emotion_core.domain.ladder import LadderDay
from emotion_core.domain.market import MarketStat, Phase
from emotion_core.domain.signal import Action, Checklist, Signal, SignalSource
from emotion_core.utils.config import CONFIG, config_hash
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
        return [
            DerivedBar(
                code=r[0],
                date=r[1],
                is_limit_up=bool(r[2]),
                is_limit_down=bool(r[3]),
                is_one_word=bool(r[4]),
                is_exchange=bool(r[5]),
                is_bomb=bool(r[6]),
                touched_limit=bool(r[7]),
                cont_days=int(r[8]),
                amplitude=float(r[9]),
            )
            for r in rows
        ]


def load_stock_basic() -> dict[str, dict]:
    """证券主数据。返回 {code: {name, is_st, first_bar_date, ...}}。"""
    with connect() as conn:
        rows = conn.execute("SELECT code, name, is_st, first_bar_date FROM stock_basic").fetchall()
        return {r[0]: {"name": r[1], "is_st": bool(r[2]), "first_bar_date": r[3]} for r in rows}


def load_market_stat(d: date) -> MarketStat | None:
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
            date=r[0],
            phase=Phase(r[1]) if r[1] else Phase.ICE,
            buy_window=r[2],
            limit_up_count=int(r[3] or 0),
            bomb_rate=None if r[4] is None else float(r[4]),
            limit_down_count=int(r[5] or 0),
            max_limit_days=int(r[6] or 0),
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
            result.append(
                [
                    LadderDay(
                        date=r[0],
                        code=r[1],
                        cont_days=int(r[2]),
                        is_exchange=bool(r[3]),
                        is_top=bool(r[4]),
                        is_sole_top=bool(r[5]),
                        y_top_group_count=int(r[6] or 0),
                        y_top_survivor_count=int(r[7] or 0),
                    )
                    for r in rows
                ]
            )
    return result


def load_signal_history(limit: int = 200) -> list[Signal]:
    """最近 limit 条 signal。"""
    with connect() as conn:
        rows = conn.execute(
            """SELECT confirm_date, code, action, buy_window, status
               FROM signal ORDER BY confirm_date DESC LIMIT %s""",
            (limit,),
        ).fetchall()
        return [
            Signal(
                code=r[1],
                date=r[0],
                action=Action(r[2]),
                checklist=Checklist(
                    c1_uniqueness=None,
                    c2_exchange=None,
                    c3_elimination=None,
                    c4_min_days=None,
                    c5_strength_diverge=None,
                    w1_crowding=None,
                ),
                source=SignalSource.LIVE,
                status=r[4],
            )
            for r in rows
        ]


def load_ladder_candidates(d: date) -> list[tuple[str, int, bool]]:
    """梯队候选行 (code, cont_days, is_exchange)：主板 + 剔 ST + cont_days>=2。

    口径 = lkl/services/ladder.py build() 的 WHERE 子句：主板 8 前缀（R3 + 2026-10-22 补 003）、
    次新剔除（W2，first_bar_date 缺失也剔）、整体剔 ST（C5）。
    """
    ph = ", ".join(["%s"] * len(CONFIG.BOARD_PREFIXES))
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT d.code, d.cont_days, d.is_exchange
                FROM derived_bar d JOIN stock_basic s USING (code)
                WHERE d.date = %s AND d.cont_days >= 2 AND NOT s.is_st
                  AND left(d.code, 3) IN ({ph})
                  AND (s.first_bar_date IS NOT NULL
                       AND s.first_bar_date <= GREATEST(%s::date - %s * interval '1 day',
                                                        %s::date))
                ORDER BY d.cont_days DESC, d.code""",
            (d, *CONFIG.BOARD_PREFIXES, d, CONFIG.NEW_ISSUER_MIN_DAYS, CONFIG.NEW_ISSUER_FLOOR),
        ).fetchall()
    return [(r[0], int(r[1]), bool(r[2])) for r in rows]


def load_suspended_candidates(d: date) -> list[tuple[str, int]]:
    """当日「停牌但仍有连板身位」的行 (code, prev_cont_days)（R67-2 旁路）。

    判据与 `data.bar_guard.is_padding_row()` 同源：OHL 三价全 0 且零成交
    （`open=high=low=0 AND volume=0`）——**不断言 close=0**：上游有**两种**顺延形状，
    ① `close=0`（无价占位行）与 ② `close=pre_close>0`（带上一真实收盘的顺延行，
    停牌日常见，例如 600825 于 2026-10-14/10-15）。只盯 close=0 会把 ② 整类漏掉。
    身位取该 code 在 D 之前最近一条 `cont_days > 0` 的 `derived_bar.cont_days`
    （`compute_derived` 遇停牌把 cont_days 记 0 但不断链，故要往前找最近非 0 值）。

    只保留 `prev_cont >= 2` 且主板前缀、非 ST、非次新的行——口径与
    `load_ladder_candidates()` 完全一致；这里不并进正常候选，避免影响身位口径
    （owner 拍板：停牌身位不计入名义最高板数字，只在 ②段单独展示）。
    """
    ph = ", ".join(["%s"] * len(CONFIG.BOARD_PREFIXES))
    with connect() as conn:
        rows = conn.execute(
            f"""WITH susp AS (
                    SELECT b.code, b.date FROM daily_bar b
                     WHERE b.date = %s AND b.volume = 0
                       AND b.open = 0 AND b.high = 0 AND b.low = 0
                ),
                ranked AS (
                    SELECT s.code, s.date, d.cont_days,
                           row_number() OVER (PARTITION BY s.code
                                              ORDER BY d.date DESC) AS rn
                      FROM susp s
                      JOIN derived_bar d
                        ON d.code = s.code AND d.date < s.date
                       AND d.cont_days > 0
                )
                SELECT r.code, r.cont_days
                  FROM ranked r JOIN stock_basic s USING (code)
                 WHERE r.rn = 1 AND r.cont_days >= 2 AND NOT s.is_st
                   AND left(r.code, 3) IN ({ph})
                   AND (s.first_bar_date IS NOT NULL
                        AND s.first_bar_date <= GREATEST(%s::date - %s * interval '1 day',
                                                         %s::date))
                 ORDER BY r.cont_days DESC, r.code""",
            (d, *CONFIG.BOARD_PREFIXES, d, CONFIG.NEW_ISSUER_MIN_DAYS, CONFIG.NEW_ISSUER_FLOOR),
        ).fetchall()
    return [(r[0], int(r[1])) for r in rows]


def load_exchange_codes(d: date) -> set[str]:
    """当日主板换手板代码集（幸存口径：不受 ST / 连板数过滤，同 lkl y_survivors）。"""
    ph = ", ".join(["%s"] * len(CONFIG.BOARD_PREFIXES))
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT code FROM derived_bar
                WHERE date = %s AND is_exchange AND left(code, 3) IN ({ph})""",
            (d, *CONFIG.BOARD_PREFIXES),
        ).fetchall()
    return {r[0] for r in rows}


def count_derived_rows(d: date) -> int:
    """当日 derived_bar 行数（上游完整性凭证：0 行 = 缺数日，禁止清空下游）。"""
    with connect() as conn:
        return int(
            conn.execute("SELECT count(*) FROM derived_bar WHERE date = %s", (d,)).fetchone()[0]
        )


def replace_ladder_day(d: date, rows: list[LadderDay]) -> int:
    """按日全量替换 ladder_day（DELETE + upsert 同一事务），rows 为空即清空当日。

    全量替换而非纯 upsert：源数据修正/口径变化后，纯 upsert 留不下「已跌出梯队」
    的幽灵行（lkl A8）。DELETE 与 INSERT 必须同事务（W3）。
    """
    with transaction() as conn:
        conn.execute("DELETE FROM ladder_day WHERE date = %s", (d,))
        if not rows:
            return 0
        data = [
            (
                r.date,
                r.code,
                r.cont_days,
                r.is_exchange,
                r.is_top,
                r.is_sole_top,
                r.y_top_group_count,
                r.y_top_survivor_count,
                bool(getattr(r, "is_suspended", False)),
            )
            for r in rows
        ]
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO ladder_day
                   (date, code, cont_days, is_exchange, is_top, is_sole_top,
                    y_top_group_count, y_top_survivor_count, is_suspended)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (date, code) DO UPDATE SET
                     cont_days=EXCLUDED.cont_days, is_exchange=EXCLUDED.is_exchange,
                     is_top=EXCLUDED.is_top, is_sole_top=EXCLUDED.is_sole_top,
                     y_top_group_count=EXCLUDED.y_top_group_count,
                     y_top_survivor_count=EXCLUDED.y_top_survivor_count,
                     is_suspended=EXCLUDED.is_suspended""",
                data,
            )
        return len(data)


def upsert_promotion_day(rows: list[dict]) -> int:
    """写入 promotion_day（按日 DELETE + upsert，列集与 algorithms/promotion.py 一致）。

    列集与 ON CONFLICT 子句**必须**与 `promotion._INSERT_SQL` 逐列相同：此前本函数
    漏写 `promote_from` 且冲突时不更新它，同一张表两条写入路径会产出不同结果
    （审核文档 §9 第 6 条）。守护测试见
    tests/unit/test_promotion_persist_parity.py。
    """
    with transaction() as conn:
        if not rows:
            return 0
        d = rows[0]["date"]
        conn.execute("DELETE FROM promotion_day WHERE date = %s", (d,))
        data = [
            (
                r["date"],
                r["layer"],
                r["promote_from"],
                r["promote_nominal"],
                r["promote_exchange"],
                r.get("rate_nominal"),
                r.get("rate_exchange"),
                r.get("divergence"),
                r.get("fail_perf"),
            )
            for r in rows
        ]
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO promotion_day
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
                     fail_perf=EXCLUDED.fail_perf""",
                data,
            )
        return len(data)


def insert_signal(sig: Signal) -> int:
    """写入 signal（列集与 algorithms/entry.py 的 `_persist` 一致）。

    source / config_hash 与 `entry._INSERT_SQL` 同列：source 区分实盘 live 与
    历史回填 replay（审核文档 §9 第 10 条），config_hash 记产出该信号的策略版本。
    ON CONFLICT 用库上真实唯一约束 (confirm_date, code, action)——旧写法
    (code, confirm_date) 与 `signal_confirm_date_code_action_key` 不匹配，调用即
    InvalidColumnReference（entry.py 模块文档 §6 已记录）。
    """
    with transaction() as conn:
        cl = sig.checklist
        return conn.execute(
            """INSERT INTO signal
               (confirm_date, code, action, buy_window, checklist, status,
                source, config_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (confirm_date, code, action) DO UPDATE SET
                 buy_window=EXCLUDED.buy_window,
                 checklist=EXCLUDED.checklist, status=EXCLUDED.status,
                 source=EXCLUDED.source, config_hash=EXCLUDED.config_hash""",
            (
                sig.date,
                sig.code,
                sig.action.value,
                None,
                {
                    "c1": cl.c1_uniqueness,
                    "c2": cl.c2_exchange,
                    "c3": cl.c3_elimination,
                    "c4": cl.c4_min_days,
                    "c5": cl.c5_strength_diverge,
                    "w1": cl.w1_crowding,
                },
                sig.status,
                sig.source.value,
                config_hash(),
            ),
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
            (
                stat.date,
                stat.phase.value,
                stat.buy_window,
                stat.limit_up_count,
                stat.bomb_count,
                stat.limit_down_count,
                stat.max_height,
                stat.force_liquidate,
                stat.bomb_threshold,
                stat.has_candidate,
            ),
        ).rowcount


# ── 盘中触发台账（trigger_log，ADR 0003 §D2/§D6）────────────────────────────
# 写入口径：首次触发为准（ON CONFLICT DO NOTHING）。同一只票当天可能被多轮
# 扫描命中，但「第一次封板的时刻与价格」是事实，后到的轮次不能覆盖它 ——
# 否则台账会变成「最后一次扫描时它还封着吗」，那是另一件事（由结算列表达）。

_TRIGGER_INSERT_SQL = """
    INSERT INTO trigger_log
      (date, code, name, first_seal_ts, trigger_price, seal_bid1_vol, prev_days, entry_px)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (date, code) DO NOTHING
"""

_TRIGGER_SETTLE_SQL = """
    UPDATE trigger_log SET
      broke=%s, broke_ts=%s, closed_sealed=%s,
      exit_px=%s, rolled=%s, ret_pct=%s, settled_at=now()
    WHERE date=%s AND code=%s
"""


def insert_triggers(rows: list[dict]) -> int:
    """写入盘中触发行（幂等：已存在的 (date, code) 不覆盖）。返回写入行数。

    每行键：date/code/name/first_seal_ts/trigger_price/seal_bid1_vol/prev_days/entry_px。
    `entry_px` 与 `trigger_price` 目前都等于封板价——分开存是为了将来若有
    「触发价 ≠ 成交价」（例如排队没成交）时不用改表。
    """
    if not rows:
        return 0
    data = [
        (
            r["date"],
            r["code"],
            r.get("name"),
            r.get("first_seal_ts"),
            r.get("trigger_price"),
            r.get("seal_bid1_vol"),
            r.get("prev_days"),
            r.get("entry_px"),
        )
        for r in rows
    ]
    with transaction() as conn, conn.cursor() as cur:
        cur.executemany(_TRIGGER_INSERT_SQL, data)
        return cur.rowcount


def load_triggers(d: date, *, unsettled_only: bool = False) -> list[dict]:
    """读某日触发台账（settle 与报告共用）。"""
    sql = (
        "SELECT date, code, name, first_seal_ts, trigger_price, seal_bid1_vol,"
        " broke, broke_ts, closed_sealed, prev_days, entry_px, exit_px,"
        " rolled, ret_pct, settled_at FROM trigger_log WHERE date = %s"
    )
    if unsettled_only:
        sql += " AND settled_at IS NULL"
    sql += " ORDER BY code"
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (d,))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def pending_trigger_dates(before: date) -> list[date]:
    """返回 ``before`` 之前还有未结算触发的日期（升序）。

    结算不是「结今天」而是「结所有还欠着的」：D 日盘中触发的卖出价是
    D+1 的开盘，而 D+1 的日线要等 D+1 盘后 pipeline 才落库 —— 所以
    结账天然滞后一天。只结「今天」会永远漏掉昨天那批（2026-10-23 实测：
    5 条触发在 15:05 结 0 条，因为当时 D+1 的日线还不存在）。
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT date FROM trigger_log "
            "WHERE settled_at IS NULL AND date < %s ORDER BY date",
            (before,),
        )
        return [row[0] for row in cur.fetchall()]


def save_settlements(rows: list[dict]) -> int:
    """回填结算列（broke/closed_sealed/exit_px/rolled/ret_pct/settled_at）。"""
    if not rows:
        return 0
    data = [
        (
            r["broke"],
            r.get("broke_ts"),
            r["closed_sealed"],
            r.get("exit_px"),
            r.get("rolled"),
            r.get("ret_pct"),
            r["date"],
            r["code"],
        )
        for r in rows
    ]
    with transaction() as conn, conn.cursor() as cur:
        cur.executemany(_TRIGGER_SETTLE_SQL, data)
        return cur.rowcount
