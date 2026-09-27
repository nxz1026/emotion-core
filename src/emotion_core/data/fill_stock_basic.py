"""填充 stock_basic：从东财/新浪全市场列表拉取 code/name，幂等 upsert。

用法：
    PYTHONPATH=src python -m emotion_core.data.fill_stock_basic

数据源：emotion_core.data.providers.eastmoney._em_clist（EM 优先，502 回退新浪）。
口径与 daily_bar 一致：_EM_FS_ALL_A = 深主板+创业板+沪主板+科创板（不含北交所）。

注意：_em_clist 的 fields 需含 f2（收盘价），否则新浪回退分支的
_reject_zero_prices 守卫会因「无价格」误判盘前快照而拒绝入库。
"""
from __future__ import annotations

import logging

from emotion_core.utils.db import transaction

log = logging.getLogger("emotion_core.data.fill_stock_basic")


def fetch_clist() -> list[tuple[str, str]]:
    """全市场 code/name 列表（东财优先，新浪回退）。"""
    from emotion_core.data.providers.eastmoney import _em_clist, _EM_FS_ALL_A

    # f2 仅用于通过新浪回退分支的 _reject_zero_prices 守卫，不入库
    raw = _em_clist(_EM_FS_ALL_A, ["f12", "f14", "f2"])
    rows = []
    for r in raw.itertuples():
        code = str(r.f12).zfill(6)
        name = str(r.f14).strip()
        if code and name and code.isdigit():
            rows.append((code, name))
    return rows


def _market(code: str) -> str:
    """6 开头 → sh，0/3 开头 → sz。"""
    return "sh" if code.startswith("6") else "sz"


def upsert_stock_basic(rows: list[tuple[str, str]]) -> int:
    """幂等 upsert stock_basic（code/name/market）。返回写入行数。"""
    if not rows:
        return 0
    from psycopg import sql

    with transaction() as conn:
        with conn.cursor() as cur:
            values = sql.SQL(", ").join(
                sql.SQL("({})").format(
                    sql.SQL(", ").join(sql.Literal(v) for v in (code, name, _market(code)))
                )
                for code, name in rows
            )
            query = sql.SQL(
                "INSERT INTO stock_basic (code, name, market) VALUES {} "
                "ON CONFLICT (code) DO UPDATE SET "
                "name=EXCLUDED.name, market=EXCLUDED.market, updated_at=now()"
            ).format(values)
            cur.execute(query)
    return len(rows)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    rows = fetch_clist()
    log.info("拉取全市场 %d 只", len(rows))
    n = upsert_stock_basic(rows)
    log.info("stock_basic upsert %d 行", n)
    return n


if __name__ == "__main__":
    main()
