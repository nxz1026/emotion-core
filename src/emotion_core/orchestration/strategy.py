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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("emotion_core.strategy")


def main() -> int:
    parser = argparse.ArgumentParser(description="Emotion Core Strategy Observer")
    parser.add_argument("--date", type=date.fromisoformat, default=None,
                        help="交易日 YYYY-MM-DD（默认今天）")
    args = parser.parse_args()

    trade_date = args.date or date.today()
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
