#!/usr/bin/env python3
"""hot_rank 全 A 回填（一次性，2026-10-14 用户放行）。

背景：`hot_rank` 370 天有数据，但只有 1,371 只票有排名史 —— 那是"排名史拼图"，
不是完整榜：老日期 1,231 行里只有 66 只 rank<=100，看行数会严重高估覆盖率。
东财 `getHisList` 是**逐票**历史接口，无法按日批量抓，故只能逐票补齐。

票池口径：`stock_basic.in_market` 且**尚无排名史**的票。脚本天然可重入——
票池由 "NOT IN (SELECT code FROM hot_rank)" 现算，中断后重跑自动续传
（失败票写 0 行，下次仍会被选中）。

并发口径（2026-10-14 实测）：单线程 3.69s/票（3,854 票要 4 小时，不可接受）；
6 并发 0.77s/票（约 50 分钟），12 并发出现 ReadTimeout —— 故默认 6，且逐票 3 次重试。

与 scripts/backfill_hot_rank_r21.py（R21，lookback 连板∪signal）的区别：
那是 1,299 只的样本域（影子验证用），本脚本是**全市场**（重建完整榜用）。

用法：PYTHONPATH=src DB_CONFIG=/home/ubuntu/.dbconfig \
        .venv/bin/python scripts/backfill_hot_rank_all.py [--workers 6] [--limit N]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from emotion_core.services.ingest import fetch_hot_history  # noqa: E402
from emotion_core.utils.db import query_df  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_hot_rank_all")

_ATTEMPTS = 3
_lock = threading.Lock()
_stat = {"done": 0, "rows": 0, "empty": 0, "fail": 0}


def pending_codes() -> list[str]:
    """在市且无排名史的票（稳定顺序，便于中断后对账）。"""
    df = query_df(
        "SELECT code FROM stock_basic"
        " WHERE in_market AND code NOT IN (SELECT DISTINCT code FROM hot_rank)"
        " ORDER BY code")
    return list(df["code"])


def _one(code: str) -> int:
    """单票回填（含重试）；返回写入行数，异常上抛给调用方记录。"""
    last: Exception | None = None
    for i in range(_ATTEMPTS):
        try:
            return fetch_hot_history(code)
        except Exception as exc:  # noqa: BLE001 —— 网络源异常类型不定
            last = exc
            time.sleep(1.5 * (i + 1))
    raise last  # type: ignore[misc]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（0=全部，冒烟用）")
    args = ap.parse_args()

    codes = pending_codes()
    if args.limit:
        codes = codes[: args.limit]
    # 0.77s/票 是 6 并发下的**实际墙钟**单票耗时（含无 sleep 的并发折抵），见模块文档。
    log.info("待回填票池：%d 只（workers=%d，估约 %.0f 分钟）",
             len(codes), args.workers, len(codes) * 0.77 * (6 / args.workers) / 60)
    if not codes:
        log.info("无待回填票，退出")
        return

    start = time.time()
    fails: list[str] = []

    def task(code: str) -> None:
        n = _one(code)
        with _lock:
            _stat["done"] += 1
            _stat["rows"] += n
            if n == 0:
                _stat["empty"] += 1
            if _stat["done"] % 25 == 0:
                log.info("[%d/%d] 累计 %d 行 / 空 %d / 失败 %d / 已用 %.1f 分钟",
                         _stat["done"], len(codes), _stat["rows"], _stat["empty"],
                         _stat["fail"], (time.time() - start) / 60)

    with ThreadPoolExecutor(args.workers) as ex:
        futures = {ex.submit(task, c): c for c in codes}
        for fut, code in futures.items():
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001 —— 逐票容错，不中断全量
                with _lock:
                    _stat["fail"] += 1
                fails.append(code)
                log.warning("%s FAIL: %s", code, str(exc)[:80])

    log.info("回填完成：票池 %d，写入 %d 行，空返回 %d，失败 %d（%.1f 分钟）",
             len(codes), _stat["rows"], _stat["empty"], _stat["fail"],
             (time.time() - start) / 60)
    if fails:
        log.warning("失败票 %d 只（前 50）：%s", len(fails), fails[:50])


if __name__ == "__main__":
    main()
