"""Lab 流水线：物化面板 → 基线打分 → 变体搜索（hold-out + 七门禁）→ 飞书摘要。

单独进程（`emotion-lab.service`）、单独日志、只写 `lab` schema。
主线任何一步都不依赖本模块；本模块失败也不影响主线（unit 之间没有 Requires）。

P1 做「度量」：算出主线现行规则在固定口径下的历史表现，作为所有变体的对照。
P2 做「搜索」：单点变体 → 选参区间排序 → 最后 120 个交易日验证 → 门禁 → 提案。
**没有任何自动化会把变体提升成主线**——必须人看提案、手动改常量、记 ADR。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from emotion_core.lab import db as labdb
from emotion_core.lab import notify, panel, score, search

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
    ap.add_argument("--dry-run", action="store_true", help="只算不写 lab")
    ap.add_argument("--no-search", action="store_true", help="只打分基线，不跑变体搜索")
    ap.add_argument("--no-push", action="store_true", help="不推飞书摘要")
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
    if args.no_search:
        return 0

    # c5 的「可核验」基准取运行当天（与主线 check_signal 的 as_of=None 同义）
    found = search.run(start, end, as_of=date.today(), persist=not args.dry_run)
    log.info("变体 %d 个，过门禁 %d 个", found["vars"], len(found["proposals"]))
    if not args.no_push and not args.dry_run:
        notify.push(notify.digest(found))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
