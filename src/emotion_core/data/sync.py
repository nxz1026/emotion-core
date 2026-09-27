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

from emotion_core.data.providers.base import BAR_COLS, ProviderError, valid_frame
from emotion_core.utils.db import transaction

log = logging.getLogger("emotion_core.sync")


def sync_daily(days: int = 5) -> int:
    """增量同步最近 N 天日线数据。返回写入行数。

    流程：
    1. 东财 clist 拿全市场快照（1 次分页）
    2. 解析 → daily_bar 行
    3. 写入 daily_bar（ON CONFLICT DO UPDATE）
    4. 东财 502 时回退新浪（逐股拉取）
    """
    end = date.today()
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


def fetch_snapshot(trade_date: date) -> list[tuple]:
    """东财 clist 全市场快照 → daily_bar 行。"""
    from emotion_core.data.providers.eastmoney import _em_clist, _EM_FS_ALL_A, _SPOT_MAP

    raw = _em_clist(_EM_FS_ALL_A, list(_SPOT_MAP))
    raw = raw.rename(columns=_SPOT_MAP)
    # 过滤 bj*（北交所）
    raw = raw[~raw["code"].astype(str).str.startswith("bj")]
    raw["date"] = trade_date
    # 转为 daily_bar 行
    rows = []
    for r in raw.itertuples():
        rows.append((
            str(r.code).zfill(6), r.date,
            float(r.open), float(r.high), float(r.low), float(r.close),
            float(r.pre_close) if pd.notna(r.pre_close) else None,
            float(r.volume) if pd.notna(r.volume) else None,
            float(r.amount) if pd.notna(r.amount) else None,
            float(r.turnover_rate) if pd.notna(r.turnover_rate) else None,
        ))
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


def upsert_daily_bars(rows: list[tuple]) -> int:
    """写入 daily_bar（幂等 upsert）。返回写入行数。"""
    if not rows:
        return 0
    with transaction() as conn:
        with conn.cursor() as cur:
            args = ",".join(
                cur.mogrify("(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", r).decode()
                for r in rows
            )
            cur.execute(
                f"INSERT INTO daily_bar ({','.join(BAR_COLS)}) VALUES {args} "
                "ON CONFLICT (code, date) DO UPDATE SET "
                "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
                "close=EXCLUDED.close, pre_close=EXCLUDED.pre_close, "
                "volume=EXCLUDED.volume, amount=EXCLUDED.amount, "
                "turnover_rate=EXCLUDED.turnover_rate"
            )
    return len(rows)


def _all_codes() -> list[str]:
    """从 stock_basic 获取全部股票代码。"""
    from emotion_core.utils.db import query_df

    df = query_df("SELECT code FROM stock_basic ORDER BY code")
    return df["code"].tolist()
