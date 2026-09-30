"""胶水层：信号历史回填服务。

职责：逐日调 entry.check_signal(source=REPLAY) 回填历史 signal。
使用 trading_days 工具函数遍历交易日，避免日历日迭代。

运维入口（此前缺失，回填是一次性手工执行，无可复现入口）：

    PYTHONPATH=src .venv/bin/python -m emotion_core.services.replay_service \\
        --start 2024-01-01 --end 2026-09-30 [--dry-run]

退出码：0 成功 / 2 参数错误 / 1 运行异常（systemd 判成败只需非 0）。
`--dry-run` 只打印将被回填的区间与交易日数，**不写库**；它不是口径预演——
逐日五条件判定只在真实写入路径（`run` → `entry.check_signal`）里跑。
"""
from __future__ import annotations

import logging
import sys
from datetime import date

from emotion_core.algorithms import entry
from emotion_core.domain.signal import SignalSource
from emotion_core.utils.dates import trading_days

log = logging.getLogger("emotion_core.replay_service")


def run(start: date, end: date) -> int:
    """回填历史 signal。

    Args:
        start: 起始日期。
        end: 结束日期。

    Returns:
        回填信号数。
    """
    days = trading_days(start, end)
    count = 0
    for cur in days:
        sig = entry.check_signal(cur, SignalSource.REPLAY)
        if sig is not None:
            count += 1
    log.info("replay_service %s~%s: %d/%d 个信号", start, end, count, len(days))
    return count


def _parse_day(value: str, flag: str) -> date:
    """命令行日期解析；非法格式抛 ValueError（由 main 折成退出码 2）。"""
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{flag} 需为 YYYY-MM-DD，收到 {value!r}") from None


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：回填历史 signal。返回退出码（0 成功 / 2 参数错 / 1 运行异常）。

    静默判据（不写库）绝不隐形：dry-run 打印待回填区间与交易日数，
    真实运行打印落库信号数——两者都不改 `run` 的行为与签名。
    """
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m emotion_core.services.replay_service",
        description="回填历史 signal（逐交易日调 entry.check_signal，source=replay）。")
    ap.add_argument("--start", required=True, metavar="YYYY-MM-DD", help="起始日（含）")
    ap.add_argument("--end", required=True, metavar="YYYY-MM-DD", help="结束日（含）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将回填的区间与交易日数，不写库")
    ns = ap.parse_args(argv)          # 参数缺失/非法：argparse 自带退出码 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        start = _parse_day(ns.start, "--start")
        end = _parse_day(ns.end, "--end")
        if start > end:
            raise ValueError(f"--start ({start}) 晚于 --end ({end})")
    except ValueError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2

    days = trading_days(start, end)
    if ns.dry_run:
        print(f"[dry-run] 区间 {start} ~ {end}：{len(days)} 个交易日 "
              f"（{days[0] if days else '—'} ~ {days[-1] if days else '—'}），不写库")
        return 0

    try:
        n = run(start, end)
    except Exception as exc:          # CLI 边界兜底：异常折成非 0，不吞栈
        log.error("回填失败 %s ~ %s: %s", start, end, exc, exc_info=True)
        return 1
    print(f"回填完成：{n} 条信号（区间 {start} ~ {end}，{len(days)} 个交易日）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
