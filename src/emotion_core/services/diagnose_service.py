"""胶水层：个股诊断服务。

职责：三段式个股诊断（技术面 + 基本面 + 市场环境）。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.algorithms import state, ladder
from emotion_core.utils.db import query_df
from emotion_core.utils.dates import prev_trading_day

log = logging.getLogger("emotion_core.diagnose_service")


def diagnose(code: str, as_of: date | None = None) -> dict:
    """三段式个股诊断。

    Args:
        code: 股票代码。
        as_of: 评估日期，默认昨日。

    Returns:
        诊断结果 dict。
    """
    if as_of is None:
        prev = prev_trading_day(date.today())
        as_of = prev if prev else date.today()

    # 技术面：连板状态 + 判据
    df = query_df(
        "SELECT is_limit_up, is_exchange, cont_days, amplitude"
        " FROM derived_bar WHERE code = %s AND date = %s",
        (code, as_of),
    )
    technical = {}
    if not df.empty:
        r = df.iloc[0]
        technical = {
            "is_limit_up": bool(r["is_limit_up"]),
            "is_exchange": bool(r["is_exchange"]),
            "cont_days": int(r["cont_days"]),
            "amplitude": float(r["amplitude"]) if r["amplitude"] else None,
        }

    # 基本面：市值/行业
    basic = query_df(
        "SELECT name, industry, market_cap FROM stock_basic WHERE code = %s", (code,)
    )
    fundamental = {}
    if not basic.empty:
        r = basic.iloc[0]
        fundamental = {
            "name": str(r["name"]),
            "industry": str(r["industry"]) if r["industry"] else None,
            "market_cap": float(r["market_cap"]) if r["market_cap"] else None,
        }

    # 市场环境
    mkt = query_df(
        "SELECT phase, buy_window FROM market_stat WHERE date = %s", (as_of,)
    )
    market = {}
    if not mkt.empty:
        r = mkt.iloc[0]
        market = {
            "phase": str(r["phase"]),
            "buy_window": str(r["buy_window"]),
        }

    return {"code": code, "as_of": as_of, "technical": technical,
            "fundamental": fundamental, "market": market}
