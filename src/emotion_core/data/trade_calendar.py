"""交易日历（独立日历源）——回答「交易所哪天开市」，与库内数据无关。

**为什么需要独立源**：`utils/dates.trading_days()` 是从 `daily_bar` 的 distinct date
派生的（「交易日历是 data 的事实」），语义是「哪天有数据」。一旦脏数据把非交易日写进
`daily_bar`，它就会把非交易日当成交易日——实测事故：`2026-09-27`（周日）有 5221 行、
与 `2026-09-24` 逐行全等（旧版 snapshot 把实时快照盖上了传入日期），日历因此认为周日
开市，日更守卫被自家脏数据骗过，链卡死在 sync 的 EM 日期守卫上。

两个函数语义不同，不要互相替代：

- `utils/dates.trading_days` → **有数据的日期**（历史迭代用，data 的事实）
- 本模块 `is_trading_day`  → **交易所开市日**（守卫用，独立于库内任何数据）

**源与缓存**：akshare `tool_trade_date_hist_sina`（新浪，与 EM 行情源独立）拉全量开市日，
materialize 成区间内每一自然日的 `is_open` 落 `trade_calendar` 表，之后**离线可判**。
懒刷新：目标日落在缓存区间之外（或表为空）时自动 refresh 一次；取源失败则退回
「工作日」保守判据并告警——宁可漏拦节假日，绝不放行已知非交易日。

运维：`python -m emotion_core.data.trade_calendar refresh`（也可 `--show 2026-09-25`）。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from emotion_core.utils.dates import today_sh
from emotion_core.utils.db import connect_ro, transaction

log = logging.getLogger(__name__)

SOURCE = "sina:tool_trade_date_hist_sina"
_DATE_ROW = object()  # 缓存未命中哨兵（区别于「查到 is_open=false」）


def _fetch_open_dates() -> list[date]:
    """拉全量开市日（升序）。唯一联网点，便于测试替换。"""
    import akshare as ak  # 局部导入：无网/未装时其余功能不受影响

    df = ak.tool_trade_date_hist_sina()
    col = "trade_date" if "trade_date" in df.columns else df.columns[0]
    out: list[date] = []
    for v in df[col]:
        d = v.date() if hasattr(v, "date") else date.fromisoformat(str(v)[:10])
        out.append(d)
    return sorted(set(out))


def rows_from_open_dates(open_dates: list[date]) -> list[tuple[date, bool]]:
    """把「开市日列表」materialize 成区间内每一自然日的 (date, is_open)。

    只存开市日无法区分「休市」与「超出缓存区间」，故补齐区间内全部自然日：
    13 年 ~4800 行，代价可忽略，换来 is_trading_day 的确定性。
    """
    if not open_dates:
        return []
    lo, hi = min(open_dates), max(open_dates)
    opens = set(open_dates)
    out: list[tuple[date, bool]] = []
    d = lo
    while d <= hi:
        out.append((d, d in opens))
        d += timedelta(days=1)
    return out


def _upsert(cur: Any, rows: list[tuple[date, bool]]) -> None:
    cur.executemany(
        """INSERT INTO trade_calendar (date, is_open, source, updated_at)
           VALUES (%s, %s, %s, now())
           ON CONFLICT (date) DO UPDATE
             SET is_open = EXCLUDED.is_open,
                 source = EXCLUDED.source,
                 updated_at = now()""",
        [(d, is_open, SOURCE) for d, is_open in rows],
    )


def refresh(conn: Any | None = None) -> dict[str, Any]:
    """拉源并 upsert 落库（幂等）。返回 {open_n, rows, first, last, source}。

    conn 给出时不做事务托管（提交由调用方负责）。
    """
    open_dates = _fetch_open_dates()
    rows = rows_from_open_dates(open_dates)
    if not rows:
        raise RuntimeError("交易日历源返回空数据，拒绝清空已有日历")

    if conn is None:
        with transaction() as c, c.cursor() as cur:
            _upsert(cur, rows)
    else:
        with conn.cursor() as cur:
            _upsert(cur, rows)
    info = {"open_n": len(open_dates), "rows": len(rows),
            "first": rows[0][0], "last": rows[-1][0], "source": SOURCE}
    log.info("交易日历刷新：%d 行（开市 %d），区间 %s ~ %s",
             info["rows"], info["open_n"], info["first"], info["last"])
    return info


def cached_range() -> tuple[date | None, date | None]:
    """缓存区间（表为空 → (None, None)）。"""
    with connect_ro() as conn:
        r = conn.execute("SELECT min(date), max(date) FROM trade_calendar").fetchone()
    return (r[0], r[1]) if r and r[0] else (None, None)


def lookup(d: date) -> bool | None:
    """查缓存：True/False；表内无此行（区间外）→ None。"""
    with connect_ro() as conn:
        r = conn.execute("SELECT is_open FROM trade_calendar WHERE date = %s", (d,)).fetchone()
    return None if r is None else bool(r[0])


def _weekday(d: date) -> bool:
    """保守判据：只用星期，独立于库内数据与网络。"""
    return d.weekday() < 5


def is_trading_day(d: date, *, auto_refresh: bool = True) -> bool:
    """目标日是否开市。缓存未命中时按需刷新；取源失败则退回工作日判据并告警。"""
    try:
        hit = lookup(d)
    except Exception as exc:  # noqa: BLE001 —— 库不可用不能放行非交易日
        log.warning("交易日历查库失败（%s），退回工作日判据", exc)
        return _weekday(d)
    if hit is not None:
        return hit

    if auto_refresh:
        try:
            refresh()
            hit = lookup(d)
        except Exception as exc:  # noqa: BLE001 —— 网络/akshare 故障不该拦住日更
            log.warning("交易日历刷新失败（%s），退回工作日判据：%s", exc, d)
    if hit is None:
        log.warning("交易日历无 %s 记录，退回工作日判据", d)
        return _weekday(d)
    return hit


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="交易日历（独立源）")
    ap.add_argument("cmd", nargs="?", default="refresh", choices=["refresh", "show"])
    ap.add_argument("--date", default=None, help="show 用：YYYY-MM-DD，缺省今天")
    ns = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if ns.cmd == "refresh":
        info = refresh()
        print(f"OK 交易日历 {info['rows']} 行（开市 {info['open_n']}）"
              f" 区间 {info['first']} ~ {info['last']} 源 {info['source']}")
        return 0
    d = date.fromisoformat(ns.date) if ns.date else today_sh()
    print(f"{d} {d.strftime('%a')} -> {'开市' if is_trading_day(d) else '休市'}（源 {SOURCE}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
