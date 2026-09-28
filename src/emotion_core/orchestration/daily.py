"""编排层：每日全流程入口。

一个 Python 脚本，线性步骤 + 表打点 + 失败即停。
步骤顺序严格照 lkl daily.sh（策略语义）。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from emotion_core.orchestration import pipeline
from emotion_core.services.coverage import EXIT_COVERAGE_BLOCKED, CoverageBlocked
from emotion_core.utils.dates import today_sh

log = logging.getLogger("emotion_core.daily")

# 步骤注册表（顺序即执行顺序）
STEPS = [
    ("sync", "数据同步"),
    ("coverage", "覆盖率门槛"),
    ("derive", "判据+连板"),
    ("market", "状态机"),
    ("ladder", "梯队"),
    ("signal", "买入信号"),
    ("promotion", "晋级率"),
    ("theme", "题材"),
    ("ecosystem", "生态评级"),
    ("strategy", "策略观察"),
    ("outcome", "结果回填"),
    ("health", "健康推送"),
]


def _trading_day_guard(trade_date: date) -> bool:
    """交易日守卫：非交易日返回 False（判据来自**独立日历源**，不看库内数据）。

    判据 = `data/trade_calendar.is_trading_day`（akshare 新浪交易日历，落
    `trade_calendar` 表缓存）；未来日期另加一道「数据不可能存在」的拦截。

    为什么不用 `utils/dates.trading_days`：它从 `daily_bar` 的 distinct date 派生
    （「日历是 data 的事实」），**脏数据会把非交易日变成交易日**——实测事故：
    库内 2026-09-27（周日）有 5221 行、与 2026-09-24 逐行全等（旧版 snapshot 把
    实时快照盖上传入日期），日历因此认为周日开市，守卫被骗过 → 目标日=周日 →
    sync 报「EM 最新数据日期 2026-09-24 ≠ 传入 2026-09-27」→ 日更链卡死在 sync，
    新增的 coverage/derive/emotion 步骤永远跑不到。独立日历源与库内数据无关，
    从根上断掉这条自噬路径。

    语义边界（刻意选择）：**当日是交易日但行情还没发布 → 不跳过**，让它走到 sync
    由 EM 日期守卫响亮报 FAILED（真数据延迟必须可见，不能静默 0 退出）。
    历史上「非交易日无数据」与「交易日无数据」都被旧的日历判据静默跳过了。
    """
    from emotion_core.data import trade_calendar

    today = today_sh()
    if trade_date > today:
        log.warning("目标日 %s 在未来（今天 %s），跳过", trade_date, today)
        return False
    if not trade_calendar.is_trading_day(trade_date):
        log.info("非交易日 %s（独立日历源），跳过", trade_date)
        return False
    return True


def run_daily(trade_date: date | None = None, *, from_step: str | None = None, dry_run: bool = False) -> int:
    """执行每日全流程。

    Args:
        trade_date: 交易日，默认今天。
        from_step: 从指定步骤开始（断点续跑）。
        dry_run: 只打印步骤不执行。

    Returns:
        exit code（0=成功，非0=失败）。
    """
    if trade_date is None:
        trade_date = today_sh()

    if not _trading_day_guard(trade_date):
        return 0

    log.info("===== emotion-core daily %s =====", trade_date)

    started = from_step is None
    for step, desc in STEPS:
        if not started:
            if step == from_step:
                started = True
            else:
                continue

        if dry_run:
            log.info("[dry-run] %s: %s", step, desc)
            continue

        pipeline.mark_running(step, trade_date)
        try:
            _run_step(step, trade_date)
            pipeline.mark_done(step, trade_date)
        except CoverageBlocked as exc:
            # 半截数据比报错危险：拒绝装配，专用退出码 76（docs/01 A12）。
            pipeline.mark_failed(step, trade_date, str(exc))
            log.error("daily blocked at %s: %s", step, exc)
            return EXIT_COVERAGE_BLOCKED
        except Exception as exc:
            pipeline.mark_failed(step, trade_date, str(exc))
            log.error("daily failed at %s: %s", step, exc)
            return 1

    log.info("===== emotion-core daily done =====")
    return 0


def _run_step(step: str, trade_date: date) -> None:
    """执行单个步骤。"""
    if step == "sync":
        from emotion_core.services.ingest import snapshot_daily
        snapshot_daily(trade_date)
    elif step == "coverage":
        from emotion_core.services.coverage import gate
        gate(trade_date)          # 不足 0.90 抛 CoverageBlocked → 退出码 76
    elif step == "derive":
        from emotion_core.services.derive_service import run as derive_run
        derive_run(trade_date)
    elif step == "market":
        from emotion_core.services.market_service import run as market_run
        market_run(trade_date)
    elif step == "ladder":
        from emotion_core.algorithms import ladder
        ladder.persist(trade_date)
    elif step == "signal":
        from emotion_core.services.signal_service import run as signal_run
        signal_run(trade_date)
    elif step == "promotion":
        from emotion_core.services.promotion_service import run as promotion_run
        promotion_run(trade_date)
    elif step == "theme":
        from emotion_core.services.theme_service import run as theme_run
        theme_run(trade_date)
    elif step == "ecosystem":
        from emotion_core.services.ecosystem_service import run as ecosystem_run
        ecosystem_run(trade_date)
    elif step == "strategy":
        from emotion_core.services.strategy import run_for_date as strategy_run
        strategy_run(trade_date)
    elif step == "outcome":
        # 回填 signal_outcome（L3 结果），不是回填 signal——replay_service 是
        # 「历史 signal 回填」另一件事，此前接错（审核文档 §9 第 12 条）。
        from emotion_core.algorithms import outcome
        outcome.backfill()
    elif step == "health":
        # 三条断档检查（报告/数据链/信号回填）→ 去重入 alert 队列 → 推 webhook。
        # health.push 内部对 webhook 失败降级为 warning，不抛（不拦主链）。
        from emotion_core.algorithms import health
        health.push(trade_date)
    else:
        raise ValueError(f"未知步骤: {step}")


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="emotion-core daily")
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="交易日 YYYY-MM-DD")
    parser.add_argument("--from-step", type=str, default=None, help="从指定步骤开始")
    parser.add_argument("--dry-run", action="store_true", help="只打印步骤不执行")
    args = parser.parse_args()
    sys.exit(run_daily(args.date, from_step=args.from_step, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
