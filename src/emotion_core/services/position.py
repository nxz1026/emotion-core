"""T7 实仓管理（R4 手动录入）：open/close/current。

语义逐字照搬 lkl/services/position.py。差异仅 IO 适配：
- `from lkl.utils import db` + `db.query_df`/`db.execute` →
  `from emotion_core.utils.db import query_df, execute`（同签名）；
  SQL 与参数顺序逐字不变。
- `from lkl.models.types import Position` →
  `from emotion_core.domain.position import Position`
  （字段 code/entry_date/entry_price/shares/status/note/id 与 lkl 一致）。
"""
from __future__ import annotations

from datetime import date

from emotion_core.domain.position import Position
from emotion_core.utils.db import execute, query_df


def open_pos(code: str, entry_date: date, price: float, shares: int,
             note: str = "") -> int:
    df = query_df(
        "INSERT INTO position (code, entry_date, entry_price, shares, note)"
        " VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (code, entry_date, price, shares, note))
    return int(df["id"].iloc[0])


def close_pos(pos_id: int, close_date: date, price: float) -> None:
    execute("UPDATE position SET status='CLOSED', closed_date=%s,"
            " close_price=%s WHERE id=%s AND status='OPEN'",
            (close_date, price, pos_id))


def current() -> list[Position]:
    df = query_df("SELECT id, code, entry_date, entry_price, shares, status,"
                  " note FROM position WHERE status='OPEN' ORDER BY entry_date")
    return [Position(code=r.code, entry_date=r.entry_date,
                     entry_price=float(r.entry_price), shares=int(r.shares),
                     status=r.status, note=r.note or "", id=int(r.id))
            for r in df.itertuples()]
