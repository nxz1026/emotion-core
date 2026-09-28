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
from emotion_core.utils.config import CONFIG
from emotion_core.utils.dates import trading_days

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
    """交易日守卫：非交易日返回 False（两条独立判据）。

    ① 工作日（周一~周五）；② 在 daily_bar 派生的交易日历里。

    为什么必须有 ①：日历本身就是从 `daily_bar` 的 distinct date 派生的
    （`utils/dates.py`「交易日历是 data 的事实」），**脏数据会把非交易日变成
    「交易日」**。实测事故：库内 2026-09-27（周日）有 5221 行，且 OHLC/成交额
    与 2026-09-24 逐行完全相同（旧版 snapshot 在 V1 守卫内移前把实时快照盖上了
    传入日期）→ 守卫被骗过 → 目标日=周日 → sync 报「EM 最新数据日期 2026-09-24
    ≠ 传入 2026-09-27」→ 日更链卡死在 sync（新 coverage/derive/emotion 步骤
    永远跑不到）。工作日检查不依赖库内任何数据，挡的就是这一类。

    仍挡不住的：节假日（工作日但休市，如 2026-09-25 中秋）。那需要**独立**日历源
    （交易所日历/akshare tool_trade_date_hist_sina）——已作为遗留项记录，不在本轮改。
    """
    if trade_date.weekday() >= 5:
        log.info("非交易日（周末）%s，跳过", trade_date)
        return False
    days = trading_days(CONFIG.DATA_START, date.today())
    if trade_date not in days:
        log.info("非交易日 %s，跳过", trade_date)
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
        trade_date = date.today()

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
