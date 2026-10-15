#!/usr/bin/env python3
"""Wind 逐日涨跌停名单回补（拍板③：配额友好、可断点续跑）。

背景见 `emotion_core/services/limit_state_wind.py` 的模块 docstring：自算口径
（`derived_bar.is_limit_up`）照搬 lkl，**不含 ST ±5% 档**，2025-06-03 实测低估
约 15.2%，需要一份 Wind 权威名单落 `ref_limit_state` 做对账。

用法::

    PYTHONPATH=src DB_CONFIG=/home/ubuntu/.dbconfig .venv/bin/python \\
        scripts/backfill_limit_state_wind.py --start 2024-01-01

每天在 `--max-calls` 预算内**从最新交易日往回**拉（默认 40 次，可用环境变量
`EMOTION_WIND_DAILY_CALL_BUDGET` 覆盖）。跑完的 (date, direction) 在
`ref_limit_state_run` 记 `ok`/`truncated`，下次运行自动跳过 —— 所以「额度每天
有限制，慢慢拉」就是同一条命令每天重复跑，直到输出 `remaining 0`。

只写 `ref_limit_state` / `ref_limit_state_run` 两张参考表，**不碰** `derived_bar`。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from emotion_core.services import limit_state_wind as lsw  # noqa: E402
from emotion_core.services.wind_client import WindClient  # noqa: E402
from emotion_core.utils.db import query_df  # noqa: E402

DEFAULT_BUDGET = 40
#: 已"完成"的状态：`ok` 永不重抓；`truncated`/`suspect` 只在重抓次数没到上限前重抓
#: （见 `target_dates` 的 max_retries），到顶就当成完成、不再烧额度。
DONE_STATUSES = (lsw.STATUS_OK, lsw.STATUS_TRUNCATED)
#: 需要重试的状态（真·切不动的 truncated 与疑似问句解析丢票的 suspect）。
RETRY_STATUSES = (lsw.STATUS_TRUNCATED, lsw.STATUS_SUSPECT)
DEFAULT_MAX_RETRIES = 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Wind 逐日涨跌停名单回补（可断点续跑）")
    p.add_argument("--start", default="2024-01-01", help="起始日期（含），默认 2024-01-01")
    p.add_argument("--end", default=None, help="结束日期（含），默认库内最新 derived_bar 日")
    p.add_argument("--direction", default="up", choices=["up", "down", "both"],
                   help="up=涨停（默认）/ down=跌停 / both")
    p.add_argument("--max-calls", type=int,
                   default=int(os.environ.get("EMOTION_WIND_DAILY_CALL_BUDGET",
                                              DEFAULT_BUDGET)),
                   help="本次运行的 Wind 调用预算（默认取环境变量，否则 40）")
    p.add_argument("--days", type=int, default=0,
                   help="本次最多处理多少个 (日期,方向) 组合（0=只受预算限制）")
    p.add_argument("--sleep", type=float, default=0.5, help="每次调用后的礼貌间隔秒")
    p.add_argument("--oldest-first", action="store_true", help="从最旧开始（默认从最新往回）")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不调用、不落库")
    p.add_argument("--redo", action="store_true",
                   help="忽略已抓取状态，窗口内全部重抓（用于重跑 truncated/修口径后回填）")
    p.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES,
                   help=f"truncated/suspect 的重抓次数上限（默认 {DEFAULT_MAX_RETRIES}，"
                        "到顶即视为完成、不再自动重试）")
    return p.parse_args(argv)


def target_dates(start: date, end: date, direction: str, redo: bool = False,
                 max_retries: int = DEFAULT_MAX_RETRIES) -> list[date]:
    """待办日期：库内真实有行情的交易日，去掉已完成（或重试到顶）的。

    `ok` 永不重抓；`truncated`（叶子仍触顶）与 `suspect`（问句解析丢票）在
    `retries < max_retries` 之前每天都会被排进来重抓，到顶即视为完成——这样
    「修好解析后自动补上丢的票」，又不会被一块永远切不动的叶子天天烧额度。
    `exhausted`/`error` 不受上限约束（下次继续就有进展）。
    """
    df = query_df(
        "SELECT DISTINCT d.date FROM derived_bar d"
        " JOIN trade_calendar c ON c.date = d.date AND c.is_open"
        " WHERE d.date BETWEEN %s AND %s ORDER BY d.date",
        (start, end))
    days = [row for row in df["date"]]
    if redo:
        return days
    done = query_df(
        "SELECT date FROM ref_limit_state_run"
        " WHERE direction = %s AND (status = %s"
        "       OR (status = ANY(%s) AND retries >= %s))"
        "   AND date BETWEEN %s AND %s",
        (direction, lsw.STATUS_OK, list(RETRY_STATUSES), max_retries, start, end))
    done_set = {row for row in done["date"]} if len(done) else set()
    return [d for d in days if d not in done_set]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    start = date.fromisoformat(args.start)
    if args.end:
        end = date.fromisoformat(args.end)
    else:
        latest = query_df("SELECT max(date) AS d FROM derived_bar")
        end = latest["d"].iloc[0]
    directions = list(lsw.DIRECTIONS) if args.direction == "both" else [args.direction]

    runs: list[tuple[date, str]] = []
    for direction in directions:
        days = target_dates(start, end, direction, redo=args.redo,
                            max_retries=args.max_retries)
        days.sort(reverse=not args.oldest_first)
        runs.extend((d, direction) for d in days)

    print(f"窗口 {start} → {end}｜方向 {directions}｜待办 {len(runs)} 个 (日期,方向)"
          f"｜预算 {args.max_calls} 次调用")
    if not runs:
        print("待办为空：窗口内全部已抓取（或库内无行情）。")
        return 0
    if args.dry_run:
        head = ", ".join(f"{d}:{w}" for d, w in runs[:6])
        print(f"dry-run：最近待办 {head}{' …' if len(runs) > 6 else ''}")
        return 0

    client = WindClient()
    budget = args.max_calls
    handled = 0
    for day, direction in runs:
        if budget <= 0:
            break
        if args.days and handled >= args.days:
            break
        t0 = time.time()
        result = lsw.fetch_day(client, day, direction, max_calls=budget,
                               sleep_sec=args.sleep)
        budget -= result.calls
        handled += 1
        if result.error:
            print(f"{day} {direction}: ERROR calls={result.calls} {result.error}")
            if "积分" in result.error or "额度" in result.error or "限流" in result.error:
                print("→ 疑似额度/限流，已停：次日再跑同一条命令即可续。")
                break
            continue
        stats = lsw.persist_day(result)
        print(f"{day} {direction}: {stats['status']:9s} wind={stats['n_wind']:4d}"
              f" self={stats['n_self']:4d} only_wind={stats['n_only_wind']:3d}"
              f" only_self={stats['n_only_self']:3d} calls={result.calls}"
              f" retries={stats['retries']} leaves={result.leaves}"
              f" {time.time() - t0:.0f}s")
        if stats["status"] == lsw.STATUS_SUSPECT:
            print("⚠ suspect：有子片在「已知有票的市值区间」回空（问句解析丢票），"
                  f"名单不完整；重抓次数 <{args.max_retries} 时下次运行会自动重抓。")
        if stats["status"] == lsw.STATUS_TRUNCATED:
            print(f"⚠ truncated：有叶子到顶仍触顶（市值无区分度）；"
                  f"重抓次数 <{args.max_retries} 时会再试，到顶即视为完成。")
        if stats["n_only_self"]:
            print(f"⚠ only_self={stats['n_only_self']}：本库判涨停而 Wind 未列；"
                  "若确认是本库口径（ST ±5%）之外的真涨停，说明 Wind 侧漏票。")
        if result.exhausted:
            print("→ 预算耗尽：本次到此为止。")
            break

    left = len(runs) - handled
    print(f"完成 {handled} 个组合，用掉 {args.max_calls - budget} 次调用；"
          f"本窗口剩余 {left} 个待办。")
    if left:
        print("下次继续（同一条命令）："
              f"PYTHONPATH=src DB_CONFIG=/home/ubuntu/.dbconfig .venv/bin/python"
              f" scripts/backfill_limit_state_wind.py --start {args.start}"
              f" --direction {args.direction}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
