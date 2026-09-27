"""新浪备源回填历史日线 → daily_bar（2024-01-01 ~ 2026-09-26）。

TDX 回填（backfill_tdx）在本机海外 IP 下不可用（服务器僵尸：能连上但
get_security_bars 空响应）。本模块用代码库既定备源新浪（akshare
stock_zh_a_daily，与 2026-09-27 现有数据同源）完成同一回填目标。

用法：
    PYTHONPATH=src python -m emotion_core.data.backfill_sina [--workers N]

设计：
- 数据源：SinaProvider.fetch_daily_bars（不复权，volume 手 / amount 元 /
  turnover_rate 百分数，与 daily_bar 现有口径一致）
- 回填范围：2024-01-01 ~ 2026-09-26（2026-09-27 已有数据，不覆盖）
- 抓取起点提前到 2023-12-01：让 2024 年首根 K 线也有 pre_close
- 写入：emotion_core.data.sync.upsert_daily_bars（幂等 upsert，批量）
- 并发：线程池；单股失败重试 3 次后跳过并记日志
"""
from __future__ import annotations

import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from emotion_core.data.providers.base import valid_frame
from emotion_core.data.providers.sina import SinaProvider
from emotion_core.data.sync import upsert_daily_bars
from emotion_core.utils.db import query_df

log = logging.getLogger("emotion_core.data.backfill_sina")

START = date(2024, 1, 1)
END = date(2026, 9, 26)          # 2026-09-27 已有数据，不覆盖
FETCH_START = date(2023, 12, 1)  # 提前抓，让 2024 首根有 pre_close
BATCH_ROWS = 20000               # 累计多少行触发一次 upsert
RETRY = 3


def _all_codes() -> list[str]:
    df = query_df("SELECT code FROM stock_basic ORDER BY code")
    return df["code"].tolist()


def fetch_code(provider: SinaProvider, code: str) -> list[tuple]:
    """单股新浪日线 → daily_bar 行（过滤到回填范围）。"""
    last: Exception | None = None
    for attempt in range(RETRY):
        try:
            frame = provider.fetch_daily_bars(code, FETCH_START, END)
            if not valid_frame(frame):
                return []
            frame = frame[(frame["date"] >= START) & (frame["date"] <= END)]
            if frame.empty:
                return []
            return [tuple(r) for r in frame.itertuples(index=False, name=None)]
        except Exception as exc:  # noqa: BLE001 —— 网络源异常类型不定
            last = exc
            time.sleep(2 ** attempt)
    raise last  # type: ignore[misc]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    codes = _all_codes()
    log.info("新浪备源回填：%d 只股票，范围 %s ~ %s", len(codes), START, END)

    provider = SinaProvider()
    total_rows = 0
    ok_codes = 0
    fail_codes = 0
    buf: list[tuple] = []

    def flush():
        nonlocal buf, total_rows
        if buf:
            n = upsert_daily_bars(buf)
            total_rows += n
            log.info("upsert 累计 %d 行（本批 %d）", total_rows, n)
            buf = []

    workers = args.workers
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fetch_code, provider, c): c for c in codes}
        for i, fut in enumerate(as_completed(futures), 1):
            code = futures[fut]
            try:
                rows = fut.result()
                buf.extend(rows)
                ok_codes += 1
            except Exception as exc:  # noqa: BLE001 —— 单股失败不阻塞整体
                fail_codes += 1
                log.warning("新浪 %s 失败：%s", code, str(exc)[:120])
            if len(buf) >= BATCH_ROWS:
                flush()
            if i % 200 == 0:
                log.info("进度 %d/%d（成功 %d 失败 %d 已写 %d 行）",
                         i, len(codes), ok_codes, fail_codes, total_rows)
    flush()

    log.info("新浪备源回填完成：成功 %d 只，失败 %d 只，共写入 %d 行",
             ok_codes, fail_codes, total_rows)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    raise SystemExit(main())
