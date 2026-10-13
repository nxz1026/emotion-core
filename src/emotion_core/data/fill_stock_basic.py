"""填充 stock_basic：从东财/新浪全市场列表拉取 code/name/行业/市值/流通股本，幂等 upsert。

用法：
    PYTHONPATH=src python -m emotion_core.data.fill_stock_basic

数据源：emotion_core.data.providers.eastmoney._em_clist（EM 优先，502 回退新浪）。
口径与 daily_bar 一致：_EM_FS_ALL_A = 深主板+创业板+沪主板+科创板（不含北交所）。

注意：_em_clist 的 fields 需含 f2（收盘价），否则新浪回退分支的
_reject_zero_prices 守卫会因「无价格」误判盘前快照而拒绝入库。

审核文档 §9 第 11 条：`diagnose_service` 查询 `industry` / `market_cap`，而
stock_basic 既无列也无数据源。现补列并在入库时写入：

- ``industry`` ← f100 行业名。**新浪回退源确实没有**（新浪只给证监会门类级的
  `所属行业`，粒度太粗，故不映射）→ 一律「缺 → 保留库内旧值」。
- ``market_cap`` ← f20 总市值（元）。**注意 A+B 股公司**（如 000016 深康佳A）
  这里取到的是 A+B 合计总市值，比「深交所 A股总股本 × 价」大；`float_shares`
  不受影响，因为 f21 只含 A 股流通市值。
- ``float_shares`` ← f21 流通市值（元）÷ 价格，本地反推（零额外请求）。价格取
  f2 收盘价；停牌股 f2 = 0，退到 f18 昨收。

f20/f21 是 2026-10-12 才接入 `_SINA_TO_EM` 的（配 `_SINA_UNIT_SCALE` 做
万元→元换算）。此前它们在回退源下缺列，而 upsert 又是 COALESCE 不覆写，于是
「首次即 NULL 就永远 NULL」——本机 EM 持续 502 时 `market_cap` / `float_shares`
全表 5221 行皆为 NULL，正是此故。所有可缺列统一走
``COALESCE(EXCLUDED.x, stock_basic.x)``，不用 NULL 覆写已有事实。

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

# f12=代码 f14=名称 f2=收盘价（护栏 + 反推流通股本用，不入库）
# f18=昨收（停牌股 f2=0 时当价格用） f100=行业
# f20=总市值(元) f21=流通市值(元，仅用于反推 float_shares)
EM_FIELDS = ["f12", "f14", "f2", "f18", "f100", "f20", "f21"]

#: (code, name, industry, market_cap, float_shares)
Row = tuple[str, str, str | None, float | None, float | None]


def _as_float(v: Any) -> float | None:
    """None / NaN / 不可解析 → None。

    pandas 会把整列的 None 变成 NaN（回退源缺列与空值都走这里）→ 必须显式识别，
    否则 NaN 会被当有效值写进库。
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_text(v: Any) -> str | None:
    """None / NaN / 全空白 → None，否则 strip 后的字符串。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return str(v).strip() or None


def _float_shares_from(rec: dict, cols: set[str]) -> float | None:
    """流通股本 = f21 流通市值（元）÷ 价格；任一无效或价格非正则回 None。

    价格优先取 f2（当日收盘价）；**停牌股 f2 = 0**（2026-10-12 实测有 8 只，
    daily_bar 里 close/volume 也全为 0，所以没有别的兜底源），此时退到 f18（昨收）。
    停牌不改变股本，而 Sina/EM 的 f21 本身就是按停牌前最后收盘价算的，故相除仍得
    正确的股数。f2 有值时一律用 f2，正常股行为不变。

    f21 是快照值（最新交易日），故本列同样是**快照语义**：每次回填覆盖为当日股本，
    与「DDL 只有单列、无日期」一致，不做历史回溯。除权当日会有偏差，量级正确。
    """
    if "f21" not in cols:
        return None
    cap = _as_float(rec.get("f21"))
    if cap is None:
        return None
    price = _as_float(rec.get("f2")) if "f2" in cols else None
    if price is None or price <= 0:
        price = _as_float(rec.get("f18")) if "f18" in cols else None
    if price is None or price <= 0:
        return None
    return cap / price


def _rows_from_frame(raw: pd.DataFrame) -> list[Row]:
    """DataFrame（EM/Sina 同形，列名 f 码）→ 行元组；缺列取 None，不抛。

    回退源（新浪）只有 `_SINA_TO_EM` 收录的字段：f100（行业名）必然缺列 → None，
    由 upsert 的 COALESCE 保留旧值；f20/f21 现已收录，故总市值与流通股本在回退源
    下同样有值。一律用 `record.get` 而非属性访问，缺列即 None。
    """
    cols = set(raw.columns)
    rows: list[Row] = []
    for rec in raw.to_dict("records"):
        code = str(rec.get("f12") or "").strip().zfill(6)
        name = str(rec.get("f14") or "").strip()
        if not code or not name or not code.isdigit():
            continue
        industry = _as_text(rec.get("f100")) if "f100" in cols else None
        market_cap = _as_float(rec.get("f20")) if "f20" in cols else None
        rows.append((code, name, industry, market_cap,
                     _float_shares_from(rec, cols)))
    return rows


def fetch_clist() -> list[Row]:
    """全市场 code/name/行业/总市值（东财优先，新浪回退）。"""
    from emotion_core.data.providers.eastmoney import _EM_FS_ALL_A, _em_clist

    return _rows_from_frame(_em_clist(_EM_FS_ALL_A, EM_FIELDS))


def _market(code: str) -> str:
    """6 开头 → sh，0/3 开头 → sz。"""
    return "sh" if code.startswith("6") else "sz"


def upsert_stock_basic(rows: list[Row]) -> int:
    """幂等 upsert stock_basic（code/name/market/industry/market_cap/float_shares/in_market）。"""
    if not rows:
        return 0
    from psycopg import sql

    def _lit(v: Any):
        return sql.Literal(v) if v is not None else sql.SQL("NULL")

    with transaction() as conn:
        with conn.cursor() as cur:
            values = sql.SQL(", ").join(
                sql.SQL("({}, {}, {}, {}, {}, {}, true)").format(
                    _lit(code), _lit(name), _lit(_market(code)),
                    _lit(industry), _lit(market_cap), _lit(float_shares))
                for code, name, industry, market_cap, float_shares in rows
            )
            query = sql.SQL(
                "INSERT INTO stock_basic (code, name, market, industry,"
                " market_cap, float_shares, in_market) VALUES {} "
                "ON CONFLICT (code) DO UPDATE SET "
                "name=EXCLUDED.name, market=EXCLUDED.market,"
                " industry=COALESCE(EXCLUDED.industry, stock_basic.industry),"
                " market_cap=COALESCE(EXCLUDED.market_cap, stock_basic.market_cap),"
                " float_shares=COALESCE(EXCLUDED.float_shares, stock_basic.float_shares),"
                " in_market=true, updated_at=now()"
            ).format(values)
            cur.execute(query)
    return len(rows)


def backfill() -> int:
    """全市场快照刷新（抓 → 写），返回写入行数。

    供编排层（`orchestration/daily.py` 的 basic 步）与 `main()` 共用一份实现：
    此前只有 `main()`，而 CLI 不是可调用入口 ⇒ 这个模块在生产上**零调用方**，
    `industry` / `market_cap` / `float_shares` 一度全表 NULL 就是这么来的。
    """
    rows = fetch_clist()
    log.info("拉取全市场 %d 只", len(rows))
    n = upsert_stock_basic(rows)
    log.info("stock_basic upsert %d 行", n)
    return n


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return backfill()


if __name__ == "__main__":
    main()
