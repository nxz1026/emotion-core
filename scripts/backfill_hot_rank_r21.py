#!/usr/bin/env python3
"""hot_rank 回填脚本（R21）。

回填范围：近一年 cont_days >= 2 的代码 ∪ signal 表中的代码（共 1,299 只）。
数据源：akshare stock_hot_rank_detail_em（东财个股人气排行历史）。
"""
import os
import sys
import time
import logging

# 设置 PYTHONPATH 以导入 emotion_core
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from emotion_core.services.ingest import fetch_hot_history, hot_universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def main() -> None:
    lookback_start = "2025-10-01"  # 近一年
    log.info("获取回填票池（lookback_start=%s）...", lookback_start)

    # 获取票池
    codes = hot_universe(lookback_start)
    log.info("回填票池：%d 只", len(codes))

    if not codes:
        log.warning("回填票池为空，跳过")
        return

    # 执行回填（逐票日志，ON CONFLICT 自动去重）
    start = time.time()
    total = 0
    fails = []
    skipped = 0
    for i, code in enumerate(codes):
        try:
            n = fetch_hot_history(code)
            total += n
            if (i + 1) % 50 == 0 or n > 0:
                log.info("[%d/%d] %s: %d rows", i + 1, len(codes), code, n)
        except Exception as exc:
            fails.append(code)
            log.warning("[%d/%d] %s FAIL: %s", i + 1, len(codes), code, str(exc)[:80])
        time.sleep(0.5)  # 增加间隔到 0.5s 减少限流
    elapsed = time.time() - start
    log.info("回填完成：%d 行，耗时 %.1f 秒，失败 %d: %s", total, elapsed, len(fails), fails[:20])


if __name__ == "__main__":
    main()
