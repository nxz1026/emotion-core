"""Lab 每日流水线：物化面板 → 给主线基线打分 → 落 `lab.lab_score`。

单独进程（`emotion-lab.service`）、单独日志、只写 `lab` schema。
主线任何一步都不依赖本模块；本模块失败也不影响主线（unit 之间没有 Requires）。

P1 只做「度量」：算出主线现行规则在固定口径下的历史表现，作为后续所有变体的对照。
**没有任何自动化会把变体提升成主线**——那必须人看提案再点。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from emotion_core.lab import db as labdb
from emotion_core.lab import panel, score

log = logging.getLogger(__name__)

DEFAULT_START = "2024-01-01"


def _last_settled() -> date:
    """market_stat 只有主线跑完当日才会写入 —— 用它当窗口末尾，避免拿盘中半天的数据打分。"""
    df = labdb.query_df("SELECT max(date) AS d FROM market_stat")
    if df.empty or df.iloc[0]["d"] is None:
        raise RuntimeError("market_stat 为空：主线还没落库，Lab 无法打分")
    return date.fromisoformat(str(df.iloc[0]["d"]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lab 每日流水线（面板 + 基线打分）")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default="", help="默认取 market_stat 的最后一天（当日已跑完主线）")
    ap.add_argument("--no-build", action="store_true", help="复用已有面板缓存")
    ap.add_argument("--dry-run", action="store_true", help="只算不写 lab.lab_score")
    args = ap.parse_args(argv)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else _last_settled()
    log.info("打分窗口 %s ~ %s（末日本身是已收盘且主线已落库的那天）", start, end)

    if not args.no_build:
        df = panel.build(start, end)
        panel.save(df)
    else:
        df = panel.load()
    log.info("面板 %d 行（%s~%s）", len(df), start, end)

    out = score.run(start, end, persist=not args.dry_run)
    log.info(
        "基线：n=%s 成交率=%s%% 每信号=%s%% 胜率=%s%% LCB=%s%%",
        out["n"], out["fill_rate"], out["expectancy"], out["win_rate"], out["lcb"],
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
