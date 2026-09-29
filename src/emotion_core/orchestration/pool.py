"""东财三池同步 CLI —— `ingest.sync_range` 的独立入口。

**为什么不进 daily**：daily 是 fail-fast 的 12 步主链，而池同步是「批量拉取 +
有限重试」的外网调用——三池全挂会抛 RuntimeError（`fetch_limit_pool` 的 A7
护栏），直接挂进主链会因一个外部数据源不可用而拖红整条日更。故独立成单元。

背景：本函数此前**在全仓没有任何调用方**，而 daily 的 sync 步只调
`snapshot_daily`（写 daily_bar），于是 `limit_pool_em` 至今 0 行。后果不是
「对账缺一半」这么轻——`entry.py` c5 的「炸板≥1次回封」补偿分支因
`bomb_times` 恒 None 而恒不成立，等于这条判据在 ENHANCED 窗口从未生效。

用法：
    python -m emotion_core.orchestration.pool --date 2026-09-28   # 单日
    python -m emotion_core.orchestration.pool --last 5             # 最近 5 个交易日
    python -m emotion_core.orchestration.pool --latest             # 最近交易日（默认）
    python -m emotion_core.orchestration.pool --last 5 --dry-run   # 只拉不打库

窗口：EM 池只保留最近 `_POOL_RECENT_DAYS`（当前 30）个交易日，更早的日期
拉不到——`sync_range` 会自动钳制。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

log = logging.getLogger("emotion_core.pool")


def _trading_days(end: date, n: int) -> list[date]:
    """截至 end（含）的最近 n 个交易日。"""
    from emotion_core.utils.dates import trading_days
    return trading_days(date(end.year - 2, 1, 1), end)[-n:]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m emotion_core.orchestration.pool",
        description="同步东财涨停/炸板/跌停三池 → limit_pool_em")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--date", help="交易日 YYYY-MM-DD")
    g.add_argument("--last", type=int, metavar="N",
                   help="最近 N 个交易日（受 EM 池窗口限制自动钳制）")
    g.add_argument("--latest", action="store_true",
                   help="最近一个交易日（默认行为）")
    p.add_argument("--dry-run", action="store_true",
                   help="只拉取不打库，用于验通外网与列名")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.date:
        try:
            end = date.fromisoformat(args.date)
        except ValueError:
            print(f"日期格式错误：{args.date!r}，应为 YYYY-MM-DD", file=sys.stderr)
            return 2
        days = _trading_days(end, 1)
    else:
        n = args.last or 1
        days = _trading_days(date.today(), n)
    if not days:
        print("未能确定交易日", file=sys.stderr)
        return 1

    if args.dry_run:
        from emotion_core.services import ingest
        from emotion_core.utils.fetch import retry_fetch
        for d in days:
            for ptype, (fn, _m) in ingest._POOLS.items():
                try:
                    df = retry_fetch(fn, date=d.strftime("%Y%m%d"))
                    print(f"{d} {ptype}: {len(df)} 行")
                except Exception as exc:  # noqa: BLE001
                    print(f"{d} {ptype}: FAIL {type(exc).__name__}: {exc}")
        return 0

    from emotion_core.services.ingest import sync_range
    try:
        failed = sync_range(days[0], days[-1])
    except RuntimeError as exc:
        # 三池全败 = 数据源事故（A7：宁可中断也不出假报告），退出码让 systemd 标红。
        log.error("池同步失败：%s", exc)
        return 1
    if failed:
        # 「超出东财池实达窗口」不是本部署的故障，但**不能静默当成功**：
        # 这些交易日的复盘会渲染成「东财池缺失 → 不可用于决策」。
        log.error("%d/%d 个交易日池同步失败（多为超出东财实达窗口）：%s",
                  len(failed), len(days), "、".join(str(d) for d in failed))
        return 1
    log.info("池同步完成 %d 个交易日：%s ~ %s", len(days), days[0], days[-1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
