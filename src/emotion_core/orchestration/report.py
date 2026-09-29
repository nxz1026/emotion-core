"""复盘报告 CLI —— `review.publish` 的独立入口。

**为什么不塞进 daily**：daily 是 fail-fast 的 12 步主链，报告渲染依赖的表比
主链多（持仓、题材、情绪），任何一段数据缺口都会把整条日更拖红，而报告本身
只是可读性问题。故独立成单元，`--from-step` 之外的第三条腿。

用法：
    python -m emotion_core.orchestration.report --date 2026-09-28
    python -m emotion_core.orchestration.report --latest     # 最近有 derived_bar 的交易日
    python -m emotion_core.orchestration.report --list 10    # 列出可出报告的交易日
    python -m emotion_core.orchestration.report --date 2026-09-28 --dry-run  # 不入库不落盘

幂等：同日重跑覆盖 `review_report`（ON CONFLICT DO UPDATE），不产生重复行。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from emotion_core.utils.db import query_df

log = logging.getLogger("emotion_core.report")


def _list_dates(n: int) -> list[date]:
    """可出报告的交易日：有 derived_bar 的日子（报告的根表）。

    刻意不用 `daily_bar`：derived_bar 是衍生层根表，报告的晋级/梯队/信号
    全部由它派生，它缺行 = 那天没跑过 derive，出报告只会满屏缺数。
    """
    df = query_df("SELECT DISTINCT date FROM derived_bar"
                  " ORDER BY date DESC LIMIT %s", (n,))
    return [r.date for r in df.itertuples()]


def _latest() -> date | None:
    dates = _list_dates(1)
    return dates[0] if dates else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m emotion_core.orchestration.report",
        description="生成复盘报告（终端 + reports/*.md|json + review_report 入库）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--date", help="交易日 YYYY-MM-DD")
    g.add_argument("--latest", action="store_true", help="最近一个已入库的交易日")
    g.add_argument("--list", type=int, metavar="N", help="列出最近 N 个可出报告的交易日")
    p.add_argument("--dry-run", action="store_true",
                   help="只渲染到终端，不落盘、不入库、不推送")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.list:
        dates = _list_dates(args.list)
        if not dates:
            print("没有任何已入库的 derived_bar，无法出报告")
            return 1
        for d in dates:
            print(d.isoformat())
        return 0

    if args.latest:
        trade_date = _latest()
        if trade_date is None:
            print("没有任何已入库的 derived_bar，无法出报告")
            return 1
    else:
        try:
            trade_date = date.fromisoformat(args.date)
        except ValueError:
            print(f"日期格式错误：{args.date!r}，应为 YYYY-MM-DD", file=sys.stderr)
            return 2

    if args.dry_run:
        from emotion_core.algorithms.review import collect, render_markdown
        print(render_markdown(trade_date, collect(trade_date)))
        return 0

    from emotion_core.algorithms.review import publish
    try:
        out = publish(trade_date)
    except RuntimeError as exc:
        # 必需段渲染失败 = 数据缺口，模块主动拒发。退出码 1 让 systemd 标红，
        # 但这是「数据没到位」而非「程序崩了」，日志里已带具体段名。
        log.error("报告未发布：%s", exc)
        return 1
    log.info("已发布：%s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
