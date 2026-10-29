"""预算化回填 ST PIT（``ref_security_status``）——每天花固定次数的 Wind 调用。

用 Wind ``search_stocks`` 枚举「实施ST / 撤销ST」事件，折成区间写入
``ref_security_status``，替代逐股烧配额的 ``get_stock_events`` 路径。

**断点续跑**：窗口进度落 ``ref_security_status_window``、原始事件落
``ref_security_status_event``；只有**全部窗口都 ok** 才重建 ``ref_security_status``
（半途重建会把「撤销事件还没拉到」的股票写成永不闭合的开区间——2026-10-14 首次
实跑就是这么得到 297 条开区间、随后整表回滚的）。重复跑同一条命令即续拉。

用法（仓库根）：

    PYTHONPATH=src DB_CONFIG=/home/ubuntu/.dbconfig \\
        .venv/bin/python scripts/backfill_st_pit_wind.py [起始日期 YYYY-MM-DD] \\
        [--end YYYY-MM-DD] [--max-calls N] [--force] [--dry-run]

``--max-calls`` 默认取 ``EMOTION_WIND_DAILY_CALL_BUDGET``，再退回 40：Wind 日额度
与逐日涨跌停对账共用，**优先让对账跑**（对账有健康检查消费端，ST PIT 暂无）。
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

from emotion_core.services.ref_security_status import (
    _default_call_budget,
    backfill_security_status_bulk,
    bulk_progress,
)
from emotion_core.utils.dates import today_sh


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    ap = argparse.ArgumentParser(description="预算化回填 ST PIT（可断点续跑）")
    ap.add_argument("start", nargs="?", default="2024-01-01",
                    help="起始日期 YYYY-MM-DD（默认 2024-01-01）")
    ap.add_argument("--end", default=None, help="结束日期（默认今天）")
    ap.add_argument("--max-calls", type=int, default=None,
                    help="本次调用预算（默认 EMOTION_WIND_DAILY_CALL_BUDGET 或 40）")
    ap.add_argument("--force", action="store_true", help="忽略进度，全部窗口重抓")
    ap.add_argument("--dry-run", action="store_true", help="只打印进度，不调用 Wind")
    args = ap.parse_args(argv)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else today_sh()
    before = bulk_progress(start, end)
    print(f"窗口 {before['total']} 个｜ok {before['ok']}｜待办 "
          f"{before['pending']}｜上次失败 {before['failed']}｜"
          f"预算 {args.max_calls or _default_call_budget()} 次调用")
    if args.dry_run:
        if before["first_pending"]:
            kind, ws, we = before["first_pending"]
            print(f"下一个待办窗口：{kind} {ws}~{we}")
        return 0

    codes, rows = backfill_security_status_bulk(
        start=start, end=end, budget_calls=args.max_calls, force=args.force)
    after = bulk_progress(start, end)
    if after["ok"] < after["total"]:
        print(f"未完成：ok {after['ok']}/{after['total']}，待办 {after['pending']}，"
              f"失败 {after['failed']} —— 重复跑同一条命令即续拉")
        return 0
    print(f"OK: {codes} 个 code，{rows} 条 ST 区间"
          f"（窗口 {after['ok']}/{after['total']} 全完成）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
