"""策略观察 CLI 入口。

用法：
    python -m emotion_core.orchestration.strategy [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from emotion_core.services.strategy import run_for_date
from emotion_core.utils.dates import today_sh

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("emotion_core.strategy")


def main() -> int:
    parser = argparse.ArgumentParser(description="Emotion Core Strategy Observer")
    parser.add_argument("--date", type=date.fromisoformat, default=None,
                        help="交易日 YYYY-MM-DD（默认今天）")
    args = parser.parse_args()

    # ⚠️ 2026-10-07：原为 `date.today()`。机器时区是 Etc/UTC，北京时间 00:00~08:00
    # 之间它比 `today_sh()` 整整少一天 ⇒ 策略观察会去看「昨天」。全仓口径是
    # `utils.dates.today_sh()`（algorithms/entry.py:266 的 P1-3 明文禁止隐式
    # date.today()：同一数据 30 天后重跑必须同一结论）。
    trade_date = args.date or today_sh()
    log.info("===== strategy observer %s =====", trade_date)

    try:
        count = run_for_date(trade_date)
        log.info("===== strategy observer done: %d signals =====", count)
        return 0
    except Exception as exc:
        log.error("strategy observer failed: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
