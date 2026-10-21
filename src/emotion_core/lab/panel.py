"""面板物化：把回放需要的字段一次性读出来，缓存到 `var/lab/panel.pkl`。

为什么只物化「梯队候选」而不是全市场：候选宇宙本来就只有 `ladder_day` 里每天
几十只（675 天 ≈ 2 万行），全市场 170 万行既没必要也吃内存。B 轮教训：不要用
`LEFT JOIN LATERAL` 逐行找前一日（933s 未返回），这里用 `row_number()` 一次取两日。
"""

from __future__ import annotations

import argparse
import logging
import pickle
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from emotion_core.lab import db as labdb

log = logging.getLogger(__name__)

PANEL_DIR = Path(__file__).resolve().parents[3] / "var" / "lab"
PANEL_PATH = PANEL_DIR / "panel.pkl"

#: 候选宇宙 + 市场状态 + 候选质量 + 当日 K 线 + 之后两日 K 线（一次查完）
#:
#: 市场指标取**全部**状态机输入（`state.DayMetrics` 的 12 个字段）+ phase/diverge：
#: P2 的 Lab 变体要重跑相位判定，少一列就判不出来（也不能拿 phase 反推）。
#: 候选质量列与 `entry._VIEW_SQL` 逐字同源：换手率 `COALESCE(东财ZT池, daily_bar)`、
#: 炸板次数取东财 ZT 池、票名取 `stock_basic.name`。
_SQL = """
WITH l AS (
  SELECT l.date AS d0, l.code, l.cont_days, l.is_exchange, l.is_top, l.is_sole_top,
         l.y_top_group_count, l.y_top_survivor_count, l.is_suspended,
         m.phase, m.buy_window, m.diverge, m.limit_up_count, m.bomb_rate,
         m.zt_performance, m.zt_performance_median, m.max_limit_days,
         m.limit_down_count, m.top_amplitude, m.top_broke, m.bomb_threshold,
         m.has_candidate, m.tradable_max_days, m.oneword_ratio,
         d.is_one_word, d.is_limit_up, d.is_bomb, d.touched_limit,
         p.bomb_times, s.name AS stock_name,
         COALESCE(p.turnover_rate, b0.turnover_rate) AS turnover_rate
  FROM ladder_day l
  JOIN market_stat m ON m.date = l.date
  LEFT JOIN derived_bar d ON d.code = l.code AND d.date = l.date
  LEFT JOIN daily_bar b0 ON b0.code = l.code AND b0.date = l.date
  LEFT JOIN limit_pool_em p
         ON p.code = l.code AND p.date = l.date AND p.pool_type = 'ZT'
  LEFT JOIN stock_basic s ON s.code = l.code
  WHERE l.date BETWEEN %s AND %s
),
b0 AS (
  SELECT l.*,
         b.pre_close, b.open, b.high, b.low, b.close, b.volume
  FROM l
  LEFT JOIN daily_bar b ON b.code = l.code AND b.date = l.d0
),
nxt AS (
  SELECT b0.d0, b0.code, b.date AS nd, b.open, b.high, b.low, b.close, b.pre_close,
         b.volume,
         row_number() OVER (PARTITION BY b0.d0, b0.code ORDER BY b.date) AS rn
  FROM b0
  JOIN daily_bar b
    ON b.code = b0.code AND b.date > b0.d0 AND b.date <= b0.d0 + INTERVAL '12 days'
)
SELECT b0.*,
       n1.nd   AS n1_date, n1.open AS n1_open, n1.high AS n1_high, n1.low AS n1_low,
       n1.close AS n1_close, n1.pre_close AS n1_pre_close, n1.volume AS n1_volume,
       n2.open AS n2_open, n2.close AS n2_close, n3.open AS n3_open
FROM b0
LEFT JOIN nxt n1 ON n1.d0 = b0.d0 AND n1.code = b0.code AND n1.rn = 1
LEFT JOIN nxt n2 ON n2.d0 = b0.d0 AND n2.code = b0.code AND n2.rn = 2
LEFT JOIN nxt n3 ON n3.d0 = b0.d0 AND n3.code = b0.code AND n3.rn = 3
ORDER BY b0.d0, b0.code
"""

_FLOAT_COLS = (
    "pre_close", "open", "high", "low", "close", "volume",
    "n1_open", "n1_high", "n1_low", "n1_close", "n1_pre_close", "n1_volume",
    "n2_open", "n2_close", "n3_open",
    "bomb_rate", "zt_performance", "zt_performance_median", "top_amplitude",
    "bomb_threshold", "oneword_ratio", "turnover_rate",
)


def build(start: date, end: date) -> pd.DataFrame:
    """从 DB 物化面板（只读）。"""
    df = labdb.query_df(_SQL, (start, end))
    df["d0"] = pd.to_datetime(df["d0"]).dt.date
    for col in _FLOAT_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    log.info("面板 %d 行 / %d 天 / %d 票", len(df), df["d0"].nunique(), df["code"].nunique())
    return df


def save(df: pd.DataFrame, path: Path = PANEL_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        pickle.dump(df, fh, protocol=pickle.HIGHEST_PROTOCOL)
    log.info("面板缓存 → %s（%.1f MB）", path, path.stat().st_size / 1e6)
    return path


def load(path: Path = PANEL_PATH) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"面板缓存不存在：{path}（先跑 panel --build）")
    with path.open("rb") as fh:
        df: pd.DataFrame = pickle.load(fh)
    return df


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="物化回放面板")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=str(date.today()))
    ap.add_argument("--build", action="store_true", help="从 DB 重建并缓存")
    args = ap.parse_args(argv)
    if not args.build:
        ap.print_help()
        return 2
    df = build(date.fromisoformat(args.start), date.fromisoformat(args.end))
    meta: dict[str, Any] = {"rows": len(df), "start": args.start, "end": args.end}
    save(df)
    print(f"面板就绪：{meta['rows']} 行 → {PANEL_PATH}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
