"""P3-3 自选股（watchlist）：add/rm/list + 当日状态摘要。

watchlist 表 = 用户自选清单（code PK, name 冗余快照, note）。CLI 与报告
段共用 status_rows()——单查询取齐当日行情/派生态，避免双实现漂移。

当日状态字段（derived_bar 当日行）：
  limit_up=收盘封涨停；bomb=触及未封(炸板)；limit_down=跌停；
  cont_days=连板数；pct=当日涨跌幅（daily_bar 收盘算）。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.utils.db import execute, query_df

log = logging.getLogger("emotion_core.watchlist")


def add(code: str, note: str = "") -> int:
    """加入自选（name 从 stock_basic 冗余快照；重复 code 幂等更新 note）。"""
    execute(
        "INSERT INTO watchlist (code, name, note)"
        " SELECT %s, COALESCE((SELECT name FROM stock_basic WHERE code=%s), ''), %s"
        " ON CONFLICT (code) DO UPDATE SET note=EXCLUDED.note,"
        " name=COALESCE(EXCLUDED.name, watchlist.name)",
        (code, code, note))
    return 1       # 幂等：存在也返回成功


def remove(code: str) -> int:
    """移出自选；不存在返回 0。"""
    return execute("DELETE FROM watchlist WHERE code = %s", (code,))


def list_all() -> list[dict]:
    df = query_df(
        "SELECT w.code, w.name, w.added_at, w.note FROM watchlist w"
        " ORDER BY w.added_at, w.code")
    return [{"code": r.code, "name": r.name, "added_at": r.added_at,
             "note": r.note or ""} for r in df.itertuples()]


def status_rows(trade_date: date | None = None) -> list[dict]:
    """自选清单 + 当日状态（无当日行情=空仓未上市/停牌/数据缺，pct None）。

    供 `emotion-core watch status [date]` 与报告段共用，同源渲染。
    """
    td = trade_date
    if td is None:
        df = query_df("SELECT max(date) d FROM derived_bar")
        td = df["d"].iloc[0] if not df.empty else None
    if td is None:
        return []
    rows = query_df(
        "SELECT w.code, w.name, w.note,"
        "       d.is_limit_up, d.touched_limit, d.is_bomb, d.is_limit_down,"
        "       d.cont_days, d.is_exchange, b.close, b.pre_close"
        " FROM watchlist w"
        " LEFT JOIN derived_bar d ON d.code = w.code AND d.date = %s"
        " LEFT JOIN daily_bar b ON b.code = w.code AND b.date = %s"
        " ORDER BY w.added_at, w.code", (td, td))
    out = []
    for r in rows.itertuples():
        pct = None
        if r.close is not None and r.pre_close:
            pct = round((float(r.close) / float(r.pre_close) - 1) * 100, 2)
        out.append({"code": r.code, "name": r.name, "note": r.note or "",
                    "date": td, "is_limit_up": bool(r.is_limit_up),
                    "touched_limit": bool(r.touched_limit),
                    "is_bomb": bool(r.is_bomb),
                    "is_limit_down": bool(r.is_limit_down),
                    "cont_days": int(r.cont_days) if r.cont_days is not None else 0,
                    "is_exchange": bool(r.is_exchange),
                    "pct": pct})
    return out


def fmt_status(row: dict) -> str:
    """单行状态 → 终端/报告文本。"""
    n = row["cont_days"]
    if row["is_limit_up"]:
        kind = "一字" if not row["is_exchange"] else "换手"
        tag = f"{kind}{n}板" if n >= 2 else ("一字涨停" if not row["is_exchange"]
                                             else "涨停(首板)")
    elif row["is_bomb"] or row["touched_limit"]:
        tag = "炸板"
    elif row["is_limit_down"]:
        tag = "跌停"
    else:
        tag = "—"
    pct = f"{row['pct']:+.2f}%" if row["pct"] is not None else "无行情"
    return f"{row['name'] or row['code']}({row['code']}) {tag} {pct}"
