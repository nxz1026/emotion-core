"""日更数据同步：东财主源 + 新浪备源 + TDX 回填。

docs/04 §2.1 双源设计：
- 东财 push2delay clist（1 次分页拿全市场）
- 新浪 stock_zh_a_daily（逐股备源）
- TDX pytdx（历史回填）

增量策略：只拉最近 N 天 + ON CONFLICT DO UPDATE（幂等）。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from emotion_core.data.bar_guard import check_bars
from emotion_core.data.providers.base import BAR_COLS, ProviderError, valid_frame
from emotion_core.utils.dates import today_sh
from emotion_core.utils.db import transaction
from emotion_core.utils.errors import DataError

log = logging.getLogger("emotion_core.sync")


def sync_daily(days: int = 5) -> int:
    """增量同步最近 N 天日线数据。返回写入行数。

    流程：
    1. 东财 clist 拿全市场快照（1 次分页）
    2. 解析 → daily_bar 行
    3. 写入 daily_bar（ON CONFLICT DO UPDATE）
    4. 东财 502 时回退新浪（逐股拉取）

    2026-10-17 修：`end` 不再取 `date.today()`（日历日），改取**源自己的数据日期**
    `em_data_date()`（含收盘判定）。原先周末/节假日/盘中补跑会把「最近一个交易日
    的快照」盖上今天的日期；若再被外部脚本传入一个历史日期，就是 2026-09-29 那起
    5218 行污染的形状（见 `data/bar_guard.py`）。
    """
    from emotion_core.data.providers.eastmoney import em_data_date

    try:
        end = em_data_date()
    except Exception as exc:  # noqa: BLE001 —— 守卫不可用不该拦住同步
        log.warning("数据日期守卫不可用（%s），退回日历日", exc)
        end = today_sh()
    start = end - timedelta(days=days)
    log.info("sync_daily: %s ~ %s", start, end)

    # 1. 东财 clist 快照
    try:
        rows = fetch_snapshot(end)
        if rows:
            return upsert_daily_bars(rows)
    except Exception as exc:
        log.warning("东财快照失败（%s），回退新浪", exc)

    # 2. 新浪备源（逐股拉取）
    codes = _all_codes()
    rows = fallback_rows(codes, start, end)
    return upsert_daily_bars(rows)


def fetch_snapshot(trade_date: date, *, allow_data_date_mismatch: bool = False) -> list[tuple]:
    """东财 clist 全市场快照 → daily_bar 行（**只许写快照自己的日期**）。

    2026-10-17 加日期闸门：快照自带 `f124`（源说自己是哪个交易日），与
    `trade_date` 不等即抛 `DataError`，绝不按调用方给的日期盖章 —— 2026-09-29
    的 5218 行就是「调用方说 09-29、源其实是 10-13 的盘中快照」写出来的。
    新浪回退帧没有 f124，此时退回守卫口径 `em_data_date()`。
    """
    from emotion_core.data.providers.eastmoney import (
        _EM_FS_ALL_A,
        _SPOT_MAP,
        _em_clist,
        em_data_date,
        snapshot_data_date,
    )

    raw = _em_clist(_EM_FS_ALL_A, [*_SPOT_MAP, "f124"])
    src_date = snapshot_data_date(raw)
    if src_date is None:
        try:
            src_date = em_data_date()
        except Exception as exc:  # noqa: BLE001 —— 守卫不可用时不假装通过
            raise DataError(
                f"快照无 f124 且日期守卫不可用（{exc}）：无法确认快照属于哪一天，拒绝入库"
            ) from exc
    if src_date != trade_date and not allow_data_date_mismatch:
        raise DataError(
            f"快照自带日期 {src_date} ≠ 请求写入日期 {trade_date}："
            "拒绝把当前快照盖上别的日期（补数请核对目标交易日）"
        )
    raw = raw.rename(columns=_SPOT_MAP)
    # 过滤 bj*（北交所）
    raw = raw[~raw["code"].astype(str).str.startswith("bj")]
    raw["date"] = trade_date
    # 转为 daily_bar 行
    rows = []
    for r in raw.itertuples():
        rows.append(
            (
                str(r.code).zfill(6),
                r.date,
                float(r.open),
                float(r.high),
                float(r.low),
                float(r.close),
                float(r.pre_close) if pd.notna(r.pre_close) else None,
                float(r.volume) if pd.notna(r.volume) else None,
                float(r.amount) if pd.notna(r.amount) else None,
                float(r.turnover_rate) if pd.notna(r.turnover_rate) else None,
            )
        )
    return rows


def fallback_rows(codes: list[str], start: date, end: date) -> list[tuple]:
    """新浪备源：逐股拉取日线。"""
    from emotion_core.data.providers.sina import SinaProvider

    provider = SinaProvider()
    rows = []
    for code in codes:
        try:
            frame = provider.fetch_daily_bars(code, start, end)
            if valid_frame(frame):
                rows.extend(tuple(r) for r in frame.itertuples(index=False, name=None))
        except ProviderError as exc:
            log.warning("新浪备源 %s 失败：%s", code, exc)
    return rows


def upsert_daily_bars(rows: list[tuple], *, validate: bool = True, allow_gap: bool = False) -> int:
    """写入 daily_bar（幂等 upsert）。返回写入行数。

    2026-10-17：写库前过一遍 `data.bar_guard` 自检（形状 / 顺延 / 整批自洽）。
    不通过抛 `BarGuardError`，**不静默丢行**；历史修复场景可显式
    `validate=False`（形状也一起跳过，故优先用 `allow_gap=True`）。
    """
    if not rows:
        return 0
    if validate:
        check_bars(rows, allow_gap=allow_gap)
    from psycopg import sql

    with transaction() as conn:
        with conn.cursor() as cur:
            # 构造 VALUES 子句（psycopg3 cursor 无 mogrify，用 sql.Literal）
            values = sql.SQL(", ").join(
                sql.SQL("({})").format(sql.SQL(", ").join(sql.Literal(v) for v in row))
                for row in rows
            )
            query = sql.SQL(
                "INSERT INTO daily_bar ({}) VALUES {} "
                "ON CONFLICT (code, date) DO UPDATE SET "
                "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
                "close=EXCLUDED.close, pre_close=EXCLUDED.pre_close, "
                "volume=EXCLUDED.volume, amount=EXCLUDED.amount, "
                "turnover_rate=EXCLUDED.turnover_rate"
            ).format(
                sql.SQL(", ").join(sql.Identifier(c) for c in BAR_COLS),
                values,
            )
            cur.execute(query)
    return len(rows)


def _all_codes() -> list[str]:
    """从 stock_basic 获取全部股票代码。"""
    from emotion_core.utils.db import query_df

    df = query_df("SELECT code FROM stock_basic ORDER BY code")
    return df["code"].tolist()
