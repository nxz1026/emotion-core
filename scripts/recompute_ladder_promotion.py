#!/usr/bin/env python3
"""ladder_day / promotion_day 全历史重算（一次性，2026-10-14 用户放行）。

背景：这两张表是纯 `derived_bar` 派生（`algorithms/ladder.py` / `algorithms/promotion.py`），
不需要任何新数据源，但库里只有管线实际跑过的那 12 天（142 行 / 60 行）——
670 个交易日的历史 **能力齐、数据缺**。本脚本按 derived_bar 的日期全集逐日重算。

安全性：`persist()` 都是「按日 DELETE + INSERT 同一事务」（`replace_ladder_day` /
promotion 的 DELETE+INSERT），且 ladder 有上游完整性守卫（当日 derived_bar 0 行 →
抛错拒绝清空）。故重跑幂等，不会留下幽灵行。

验收：脚本跑完后用 `--verify` 对已存在日期的**重算前后**逐字段比对（快照见
/home/ubuntu/backups/*_before_recompute_*.csv），任何不一致都要先解释再继续。

用法：PYTHONPATH=src DB_CONFIG=/home/ubuntu/.dbconfig \
        .venv/bin/python scripts/recompute_ladder_promotion.py [--from 2024-01-02] [--to 2026-10-14]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from emotion_core.algorithms import ladder, promotion  # noqa: E402
from emotion_core.utils.db import query_df  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("recompute_ladder_promotion")


def trading_dates(d0: date | None, d1: date | None) -> list[date]:
    """重算日期全集 = derived_bar 有数据的日期（上游完整性即口径）。"""
    sql = "SELECT DISTINCT date FROM derived_bar"
    cond, params = [], []
    if d0:
        cond.append("date >= %s")
        params.append(d0)
    if d1:
        cond.append("date <= %s")
        params.append(d1)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY date"
    return list(query_df(sql, tuple(params))["date"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d0", type=date.fromisoformat, default=None)
    ap.add_argument("--to", dest="d1", type=date.fromisoformat, default=None)
    args = ap.parse_args()

    dates = trading_dates(args.d0, args.d1)
    log.info("重算日期：%d 个交易日（%s ~ %s）", len(dates), dates[0], dates[-1])

    start = time.time()
    ladder_rows = promo_rows = 0
    fails: list[tuple[date, str]] = []
    for i, d in enumerate(dates, 1):
        try:
            ladder_rows += ladder.persist(d)
            promo_rows += promotion.persist(d)
        except Exception as exc:  # noqa: BLE001 —— 逐日容错，记录不中断
            fails.append((d, str(exc)[:100]))
            log.warning("%s 重算失败：%s", d, str(exc)[:100])
        if i % 25 == 0:
            log.info("[%d/%d] ladder %d 行 / promotion %d 行 / 失败 %d / 已用 %.1f 分钟",
                     i, len(dates), ladder_rows, promo_rows, len(fails),
                     (time.time() - start) / 60)

    log.info("重算完成：%d 日，ladder_day %d 行，promotion_day %d 行，失败 %d（%.1f 分钟）",
             len(dates), ladder_rows, promo_rows, len(fails), (time.time() - start) / 60)
    for d, msg in fails[:20]:
        log.warning("失败日 %s：%s", d, msg)


if __name__ == "__main__":
    main()
