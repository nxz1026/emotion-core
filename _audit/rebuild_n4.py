"""N4（`_rule_ebb` 收紧）之后的全量重算（一次性数据操作，2026-10-23）。

只动三张表（ladder_day / promotion_day 与本次判据无关）：
  1. market_stat     ← emotion.replay(START, END)：相位/窗口/退潮天数全部重算
  2. signal          ← 先删 source='replay' 再 replay_service.run：窗口变了，观察记录跟着变
  3. signal_outcome  ← outcome.backfill(force=True)：期望值随信号重算

用法：PYTHONPATH=src .venv/bin/python -u _audit/rebuild_n4.py
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from emotion_core.algorithms import emotion, outcome
from emotion_core.services import replay_service
from emotion_core.utils.db import connect, query_df

START = date(2024, 1, 1)
BACKUP_DIR = Path(__file__).resolve().parent
TABLES = ("market_stat", "signal", "signal_outcome")


def latest() -> date:
    df = query_df("SELECT max(date) AS d FROM market_stat")
    return df["d"].iloc[0]


def backup() -> None:
    for t in TABLES:
        df = query_df(f"SELECT * FROM {t}")
        path = BACKUP_DIR / f"pre_n4_{t}_20261023.csv"
        df.to_csv(path, index=False)
        print(f"[backup] {t}: {len(df)} 行 → {path.name}", flush=True)


def rebuild_market(end: date) -> None:
    t0 = time.time()
    emotion.replay(START, end)
    print(f"[market] emotion.replay 完成，{time.time() - t0:.1f}s", flush=True)


def rebuild_signals(end: date) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM signal WHERE source = 'replay'")
        conn.commit()
    print("[signal] 已删除 source='replay' 的旧行", flush=True)
    t0 = time.time()
    n = replay_service.run(START, end)
    print(f"[signal] replay_service.run → {n} 条，{time.time() - t0:.1f}s", flush=True)


def rebuild_outcome() -> None:
    t0 = time.time()
    n = outcome.backfill(force=True)
    print(f"[outcome] backfill → {n} 行，{time.time() - t0:.1f}s", flush=True)


def stats() -> None:
    print("\n=== 重算后统计 ===", flush=True)
    df = query_df("SELECT phase, buy_window, count(*) AS n FROM market_stat GROUP BY 1,2 ORDER BY 1,2")
    print("[market_stat 相位×窗口]", flush=True)
    print(df.to_string(index=False), flush=True)
    df = query_df("SELECT action, count(*) AS n FROM signal GROUP BY 1 ORDER BY 1")
    print("\n[signal 按动作]", flush=True)
    print(df.to_string(index=False), flush=True)
    df = query_df("SELECT count(*) AS n, avg(rule_ret_a)::numeric(10,3) AS ret_a, "
                  "avg(t1_gap)::numeric(10,3) AS t1_gap FROM signal_outcome WHERE complete")
    print("\n[signal_outcome 完整行]", flush=True)
    print(df.to_string(index=False), flush=True)


def main() -> None:
    end = latest()
    print(f"重算区间：{START} ~ {end}", flush=True)
    backup()
    rebuild_market(end)
    rebuild_signals(end)
    rebuild_outcome()
    stats()
    print("\n=== 全部完成 ===", flush=True)


if __name__ == "__main__":
    main()
