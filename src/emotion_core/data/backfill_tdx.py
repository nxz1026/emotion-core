"""TDX 回填历史日线 → daily_bar（2024-01-01 ~ 2026-09-26）。

用法：
    PYTHONPATH=src python -m emotion_core.data.backfill_tdx [--workers N]

设计：
- 数据源：pytdx_provider._tdx_all_bars(market, code, until)
  market: 1=上海（6 开头），0=深圳（0/3 开头）
  until: 2026-09-27（已有数据的日期）。_tdx_all_bars 从最新页往前翻，
  首页最旧根 2026-09-25 <= 2026-09-27 → 拉完首页（最新 800 根）即停，
  覆盖约 2023-06 ~ 2026-09-25，完整包含回填范围。
- 回填范围：2024-01-01 ~ 2026-09-26（2026-09-27 已有新浪数据，不覆盖）
- 写入：emotion_core.data.sync.upsert_daily_bars（幂等 upsert）
- 并发：线程池（TDX 连接是线程内长连接，每线程独立）
- 容错：单股失败记日志继续；连接全挂则快速失败并记录
"""
from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import pandas as pd

from emotion_core.data.providers.pytdx_provider import _tdx, _tdx_all_bars
from emotion_core.data.sync import upsert_daily_bars
from emotion_core.utils.db import query_df

log = logging.getLogger("emotion_core.data.backfill_tdx")

START = date(2024, 1, 1)
END = date(2026, 9, 26)          # 2026-09-27 已有新浪数据，不覆盖
UNTIL = date(2026, 9, 27)        # _tdx_all_bars 的 until（见模块 docstring）
BATCH_ROWS = 50000               # 累计多少行触发一次 upsert


def _all_codes() -> list[str]:
    df = query_df("SELECT code FROM stock_basic ORDER BY code")
    return df["code"].tolist()


def _market(code: str) -> int:
    return 1 if code.startswith("6") else 0


def fetch_code(code: str) -> list[tuple]:
    """单股 TDX 日线 → daily_bar 行（过滤到回填范围）。"""
    bars = _tdx_all_bars(_market(code), code, UNTIL)
    if not bars:
        return []
    raw = pd.DataFrame(bars)
    raw["date"] = pd.to_datetime(raw["datetime"].str[:10]).dt.date
    # ⚠️ 2026-10-07 修：与 providers/pytdx_provider.fetch_daily_bars 同一个错。
    # TDX 降序返回（见 _tdx_all_bars docstring），原实现先按区间过滤、再在降序帧
    # 上 shift(1)，于是 pre_close 取到的是**次日**收盘价（未来价）。涨停判定基准
    # 反向，且此类错误在库里不报错、只在数值上看不出来。
    raw = raw.sort_values("date", kind="stable")
    raw = raw.rename(columns={"vol": "volume"})
    raw["volume"] = pd.to_numeric(raw["volume"], errors="coerce")
    raw["amount"] = pd.to_numeric(raw.get("amount"), errors="coerce")
    # 在区间过滤**之前**播种：_tdx_all_bars 按 UNTIL=2026-09-27 翻页，实测单页
    # 800 根即回溯到约 2023-06，覆盖 START=2024-01-01 之前，区间首行有 pre_close。
    raw["pre_close"] = raw["close"].astype(float).shift(1)
    raw["turnover_rate"] = None
    raw = raw[(raw["date"] >= START) & (raw["date"] <= END)]
    if raw.empty:
        return []
    rows = []
    for r in raw.itertuples():
        rows.append((
            code, r.date,
            float(r.open), float(r.high), float(r.low), float(r.close),
            float(r.pre_close) if pd.notna(r.pre_close) else None,
            float(r.volume) if pd.notna(r.volume) else None,
            float(r.amount) if pd.notna(r.amount) else None,
            None,
        ))
    return rows


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    codes = _all_codes()
    log.info("TDX 回填：%d 只股票，范围 %s ~ %s", len(codes), START, END)

    # 连接 + 数据健全性检查（快速失败，避免 5200 次空跑）
    # 本机海外 IP 下 TDX 服务器多为「僵尸」：能连上、get_security_count 有响应，
    # 但 get_security_bars 只回 2 字节空体（ret_count=800 无数据）。故连上后
    # 必须验一只真实股票，0 根即判 TDX 不可用。
    try:
        _tdx()
        probe = _tdx_all_bars(1, "600519", UNTIL)
        if not probe:
            log.error("TDX 连接成功但无数据（僵尸服务器：get_security_bars 空响应），"
                      "回填中止。探针 600519 返回 0 根")
            return 2
        log.info("TDX 探针 600519：%d 根（最新 %s）", len(probe), probe[0]["datetime"])
    except Exception as exc:
        log.error("TDX 连接失败，回填中止：%s", exc)
        return 1

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
        futures = {ex.submit(fetch_code, c): c for c in codes}
        for i, fut in enumerate(as_completed(futures), 1):
            code = futures[fut]
            try:
                rows = fut.result()
                buf.extend(rows)
                ok_codes += 1
                if rows:
                    log.debug("%s: %d 行", code, len(rows))
            except Exception as exc:  # noqa: BLE001 —— 单股失败不阻塞整体
                fail_codes += 1
                log.warning("TDX %s 失败：%s", code, str(exc)[:120])
            if len(buf) >= BATCH_ROWS:
                flush()
            if i % 500 == 0:
                log.info("进度 %d/%d（成功 %d 失败 %d）", i, len(codes), ok_codes, fail_codes)
    flush()

    log.info("TDX 回填完成：成功 %d 只，失败 %d 只，共写入 %d 行", ok_codes, fail_codes, total_rows)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    raise SystemExit(main())
