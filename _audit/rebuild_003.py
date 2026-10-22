"""BOARD_PREFIXES 补 "003" 之后的全量重算（一次性数据操作，2026-10-22）。

顺序（下游依赖上游，不能乱）：
  1. ladder_day      ← ladder.persist(每日)：候选池变了，梯队必须重算
  2. promotion_day   ← promotion_service.run(每日)：晋级率吃梯队同池
  3. market_stat     ← emotion.replay(区间)：情绪计数 SQL 也按 BOARD_PREFIXES 限主板
  4. signal          ← 先删 source='replay' 再 replay_service.run(区间)：判据吃梯队
  5. signal_outcome  ← outcome.backfill(force=True)：期望值随信号重算

用法：PYTHONPATH=src .venv/bin/python -u _audit/rebuild_003.py
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pandas as pd

from emotion_core.algorithms import emotion, ladder, outcome
from emotion_core.services import promotion_service, replay_service
from emotion_core.utils.db import connect, connect_ro, query_df
from emotion_core.utils.dates import trading_days

START = date(2024, 1, 1)
END = date(2026, 10, 22)
BACKUP_DIR = Path(__file__).resolve().parent
TABLES = ("ladder_day", "promotion_day", "market_stat", "signal", "signal_outcome")


def backup() -> None:
    for t in TABLES:
        path = BACKUP_DIR / f"pre_003_{t}_20261022.csv"
        df = query_df(f"SELECT * FROM {t}")
        df.to_csv(path, index=False)
        print(f"[backup] {t}: {len(df)} 行 → {path.name}", flush=True)


def rebuild_ladder(days: list[date]) -> None:
    t0 = time.time()
    total = 0
    for i, d in enumerate(days, 1):
        total += ladder.persist(d)
        if i % 100 == 0 or i == len(days):
            print(f"[ladder] {i}/{len(days)} 天，累计候选 {total} 行，"
                  f"{time.time() - t0:.1f}s", flush=True)


def rebuild_promotion(days: list[date]) -> None:
    t0 = time.time()
    for i, d in enumerate(days, 1):
        promotion_service.run(d)
        if i % 100 == 0 or i == len(days):
            print(f"[promotion] {i}/{len(days)} 天，{time.time() - t0:.1f}s", flush=True)


def rebuild_market() -> None:
    t0 = time.time()
    emotion.replay(START, END)
    print(f"[market] emotion.replay 完成，{time.time() - t0:.1f}s", flush=True)


def rebuild_signals() -> None:
    with connect() as conn:
        conn.execute("DELETE FROM signal WHERE source = 'replay'")
        conn.commit()
    print("[signal] 已删除 source='replay' 的旧行", flush=True)
    t0 = time.time()
    n = replay_service.run(START, END)
    print(f"[signal] 回填 {n} 条，{time.time() - t0:.1f}s", flush=True)


def rebuild_outcome() -> None:
    t0 = time.time()
    n = outcome.backfill(force=True)
    print(f"[outcome] 回填 {n} 条，{time.time() - t0:.1f}s", flush=True)


def main() -> None:
    days = trading_days(START, END)
    print(f"重算区间 {START} ~ {END}，{len(days)} 个交易日", flush=True)
    backup()
    rebuild_ladder(days)
    rebuild_promotion(days)
    rebuild_market()
    rebuild_signals()
    rebuild_outcome()
    print("=== 全部完成 ===", flush=True)

    with connect_ro() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*), count(DISTINCT date) FROM ladder_day")
        print("ladder_day:", cur.fetchone(), flush=True)
        cur.execute("SELECT action, count(*) FROM signal GROUP BY action ORDER BY action")
        print("signal:", cur.fetchall(), flush=True)
        cur.execute("SELECT phase, buy_window, count(*) FROM market_stat "
                    "GROUP BY phase, buy_window ORDER BY phase")
        print("market_stat:", cur.fetchall(), flush=True)


if __name__ == "__main__":
    main()
