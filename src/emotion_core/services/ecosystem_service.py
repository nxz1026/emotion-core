"""胶水层：生态评级服务。

职责：调 algorithms.dragon_env.rate → 写入 market_stat 的
dragon_env / dragon_env_reasons / dragon_env_risks 三列。
封装 data/loader.py 中与生态评级相关的落库函数，
避免 algorithms 层直连 data 层（架构纪律：docs/11 §4）。

依赖方向：algorithms → services → data → domain
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import date

from emotion_core.algorithms import accelerate, dragon_env
from emotion_core.data.loader import (
    update_market_stat_ecosystem as _update_market_stat_ecosystem,
)

log = logging.getLogger("emotion_core.ecosystem_service")


def update_market_stat_ecosystem(d: date, rating: str, reasons: str, risks: str) -> int:
    """写入生态评级到 market_stat（代理 data.loader.update_market_stat_ecosystem）。"""
    return _update_market_stat_ecosystem(d, rating, reasons, risks)


def run(trade_date: date, mode: str = "live") -> str:
    """计算并写入生态评级（评级 + 理由 + 风险，三列一次写全）。

    Args:
        trade_date: 交易日。
        mode: ``live``（当日增量，B3 看不到未来）或 ``replay``（历史回填）。

    Returns:
        评级词汇（FAVORABLE / NEUTRAL / UNFAVORABLE）。
    """
    # 获取加速事件结果（供评级使用）
    accel = accelerate.detect(trade_date)
    rating = dragon_env.rate(trade_date, accel, mode)

    # 三列一次写全。原实现是内联「UPDATE market_stat SET dragon_env = %s」，
    # 把 rate() 已经算出的 goods/bads 当场丢掉 —— 于是 dragon_env_reasons /
    # dragon_env_risks 建库以来恒为 NULL，面板「生态评级」只剩光杆评级、
    # 既无理由也无风险。这里直接复用 persist() 的同一套序列化（_ser + json），
    # 走 data.loader 的唯一落库口，不再自己拼 UPDATE。
    _update_market_stat_ecosystem(
        trade_date,
        str(rating["rating"]),
        json.dumps(dragon_env._ser(rating.get("goods") or []), ensure_ascii=False),
        json.dumps(dragon_env._ser(rating.get("bads") or []), ensure_ascii=False),
    )

    log.info("ecosystem_service %s: %s", trade_date, rating["rating"])
    return str(rating["rating"])


def run_range(start: date, end: date) -> int:
    """区间回填生态评级（评级 + 理由 + 风险），返回成功天数。

    口径：只处理 ``market_stat`` 里有行、且 ``tradable_max_days`` 非空的交易日
    （历史复盘日不在其中），并走 ``mode="replay"``——历史日的 B3 必须看得见
    「胜出次日」的结果，否则该条件永远样本不足。

    成本：整段只读库内 ``market_stat`` / ``ladder_day`` / ``derived_bar`` 等，
    **不碰 Wind / 新浪 / 东财 / 巨潮任何一个外部源**，因此不消耗任何数据配额，
    回填 ROI 就是本地查询时间（669 日量级约数分钟）。这也是它和行业回填
    （巨潮逐股）在额度上的根本差别。
    """
    return dragon_env.run_range(start, end, persist_fn=update_market_stat_ecosystem)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI：``python -m emotion_core.services.ecosystem_service --start ... --end ...``。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="emotion-core 生态评级回填")
    parser.add_argument("--start", type=date.fromisoformat, required=True, help="起始日 YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, required=True, help="结束日 YYYY-MM-DD")
    args = parser.parse_args(argv)
    n = run_range(args.start, args.end)
    print(f"生态评级回填 {args.start}~{args.end}：{n} 日")
    return 0


if __name__ == "__main__":  # 少了这段 `python -m ...` 只会 import 一把、
    sys.exit(main())  # 静默 exit 0 —— 第一次实跑就是这么空转的
