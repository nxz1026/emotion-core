"""填充 stock_basic：从东财/新浪全市场列表拉取 code/name/行业/总市值，幂等 upsert。

用法：
    PYTHONPATH=src python -m emotion_core.data.fill_stock_basic

数据源：emotion_core.data.providers.eastmoney._em_clist（EM 优先，502 回退新浪）。
口径与 daily_bar 一致：_EM_FS_ALL_A = 深主板+创业板+沪主板+科创板（不含北交所）。

注意：_em_clist 的 fields 需含 f2（收盘价），否则新浪回退分支的
_reject_zero_prices 守卫会因「无价格」误判盘前快照而拒绝入库。

审核文档 §9 第 11 条：`diagnose_service` 查询 `industry` / `market_cap`，而
stock_basic 既无列也无数据源。现补两列（f100 行业名 / f20 总市值，单位元）并
在入库时写入；新浪回退源没有这两个字段，故取值一律「缺 → 保留库内旧值」
（`COALESCE(EXCLUDED.x, stock_basic.x)`），不用 NULL 覆写已有事实。

`in_market`（覆盖率门槛的分母，见 `services/coverage.py`）在 upsert 时置 true——
本列表就是当前在市全市场列表。**退市不反扫**：退市股不在本列表里，也就不会被
置回 false，分母因此偏大、门槛偏严（宁可拦，不放过），待单独工单做退市扫描。
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from emotion_core.utils.db import transaction

log = logging.getLogger("emotion_core.data.fill_stock_basic")

# f12=代码 f14=名称 f2=收盘价（仅守卫用，不入库） f100=行业 f20=总市值(元)
EM_FIELDS = ["f12", "f14", "f2", "f100", "f20"]

Row = tuple[str, str, str | None, float | None]     # (code, name, industry, market_cap)


def _rows_from_frame(raw: pd.DataFrame) -> list[Row]:
    """DataFrame（EM/Sina 同形，列名 f 码）→ 行元组；缺列取 None，不抛。

    回退源（新浪）只有 _SINA_TO_EM 收录的字段，f100/f20 必然缺列 —— 用
    `record.get` 而非属性访问，缺列即 None（由 upsert 的 COALESCE 保留旧值）。
    """
    cols = set(raw.columns)
    rows: list[Row] = []
    for rec in raw.to_dict("records"):
        code = str(rec.get("f12") or "").strip().zfill(6)
        name = str(rec.get("f14") or "").strip()
        if not code or not name or not code.isdigit():
            continue
        industry = rec.get("f100") if "f100" in cols else None
        # pandas 会把整列的 None 变成 NaN（回退源缺列/空值都走这里）→ 必须显式识别
        if industry is None or (isinstance(industry, float) and pd.isna(industry)):
            industry = None
        else:
            industry = str(industry).strip() or None
        cap = rec.get("f20") if "f20" in cols else None
        if cap is None or (isinstance(cap, float) and pd.isna(cap)):
            market_cap = None
        else:
            try:
                market_cap = float(cap)
            except (TypeError, ValueError):
                market_cap = None
        rows.append((code, name, industry, market_cap))
    return rows


def fetch_clist() -> list[Row]:
    """全市场 code/name/行业/总市值（东财优先，新浪回退）。"""
    from emotion_core.data.providers.eastmoney import _em_clist, _EM_FS_ALL_A

    return _rows_from_frame(_em_clist(_EM_FS_ALL_A, EM_FIELDS))


def _market(code: str) -> str:
    """6 开头 → sh，0/3 开头 → sz。"""
    return "sh" if code.startswith("6") else "sz"


def upsert_stock_basic(rows: list[Row]) -> int:
    """幂等 upsert stock_basic（code/name/market/industry/market_cap/in_market）。"""
    if not rows:
        return 0
    from psycopg import sql

    def _lit(v: Any):
        return sql.Literal(v) if v is not None else sql.SQL("NULL")

    with transaction() as conn:
        with conn.cursor() as cur:
            values = sql.SQL(", ").join(
                sql.SQL("({}, {}, {}, {}, {}, true)").format(
                    _lit(code), _lit(name), _lit(_market(code)),
                    _lit(industry), _lit(market_cap))
                for code, name, industry, market_cap in rows
            )
            query = sql.SQL(
                "INSERT INTO stock_basic (code, name, market, industry,"
                " market_cap, in_market) VALUES {} "
                "ON CONFLICT (code) DO UPDATE SET "
                "name=EXCLUDED.name, market=EXCLUDED.market,"
                " industry=COALESCE(EXCLUDED.industry, stock_basic.industry),"
                " market_cap=COALESCE(EXCLUDED.market_cap, stock_basic.market_cap),"
                " in_market=true, updated_at=now()"
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
