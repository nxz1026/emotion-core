"""策略观察 CLI 入口。

用法：
    python -m emotion_core.orchestration.strategy [--date YYYY-MM-DD]
                                                  [--codes 600825,000420]

``--codes`` 与 ``--date`` 组合用于**补跑历史缺口**：显式给定候选代码，
跳过自动候选池（手动自选 + 热门池）。

2026-10-09 日志巡检 D：新增 ``--codes``，把「失败补跑」从只能跑当日
扩展到可以指定历史缺口逐个补齐。
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


def parse_codes(raw: str) -> list[str]:
    """解析 ``--codes``：逗号分隔、去空白、``zfill(6)``；非法值抛 ``ValueError``。

    非法 = 空段 / 非纯数字 / 补零后超过 6 位（如 ``sh600825``、``1234567``）。
    """
    codes: list[str] = []
    for token in raw.split(","):
        item = token.strip()
        if not item or not item.isdigit() or len(item) > 6:
            raise ValueError(f"非法代码：{token.strip()!r}（应为 1~6 位数字）")
        codes.append(item.zfill(6))
    if not codes:
        raise ValueError("--codes 不能为空")
    return codes


def main() -> int:
    parser = argparse.ArgumentParser(description="Emotion Core Strategy Observer")
    parser.add_argument(
        "--date", type=date.fromisoformat, default=None, help="交易日 YYYY-MM-DD（默认今天）"
    )
    parser.add_argument(
        "--codes", default=None, help="显式候选代码，逗号分隔（补跑历史缺口用；如 600825,000420）"
    )
    args = parser.parse_args()

    codes: list[str] | None = None
    if args.codes is not None:
        try:
            codes = parse_codes(args.codes)
        except ValueError as exc:
            parser.error(str(exc))  # argparse 语义：打印用法并以退出码 2 结束

    # ⚠️ 2026-10-07：原为 `date.today()`。机器时区是 Etc/UTC，北京时间 00:00~08:00
    # 之间它比 `today_sh()` 整整少一天 ⇒ 策略观察会去看「昨天」。全仓口径是
    # `utils.dates.today_sh()`（algorithms/entry.py:266 的 P1-3 明文禁止隐式
    # date.today()：同一数据 30 天后重跑必须同一结论）。
    trade_date = args.date or today_sh()
    log.info("===== strategy observer %s =====", trade_date)

    try:
        count = run_for_date(trade_date, codes)
        log.info("===== strategy observer done: %d signals =====", count)
        return 0
    except Exception as exc:
        log.error("strategy observer failed: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
