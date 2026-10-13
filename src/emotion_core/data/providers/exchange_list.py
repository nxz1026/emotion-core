"""交易所官方 A 股列表：`stock_basic.list_date`（上市日期）的权威源。

覆盖：沪 + 深全市场，**3 次 HTTP 请求**（2026-10-13 实测：深交所 A股列表 2904 行、
上交所主板A股 1702 行、科创板 618 行），0 Wind 额度、0 逐股调用。

口径：`list_date` = 交易所口径的 **A 股上市日期**。深交所接口返回字符串
（``1991-04-03``），上交所返回 ``datetime.date``，本模块统一成 ``date``。
注意它与 `stock_basic.first_bar_date`（本库有行情的第一天）语义不同，不可互相顶替：
老退市再上市的票、以及本库只回填到 2024-01-02 的事实，都会让两者分离。

为什么不用别的源：
- 巨潮 `stock_profile_cninfo` 是**逐股**接口，5221 次调用（实测 1.17s/只 ≈ 54 分钟）；
- 东财 f26（上市日期）随 clist 走，本机出口对 push2 / push2delay 是持久 502；
- 交易所官方列表本身就是权威口径，无需对账。

额度纪律：只打交易所公开接口（经 akshare），**不消耗任何 Wind 额度**。

已知抖坑：深交所接口偶发 ``ConnectionResetError(104)``，重试即通（2026-10-13 实测
第 1 次失败、第 2 次成功），故统一过 `retry_fetch`。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import akshare as ak
import pandas as pd

from emotion_core.utils.config import CONFIG
from emotion_core.utils.errors import FetchError
from emotion_core.utils.fetch import retry_fetch

log = logging.getLogger("emotion_core.data.providers.exchange_list")

_FETCH_RETRY: int = getattr(CONFIG, "FETCH_RETRY", 3)
_TIME_SLEEP: float = getattr(CONFIG, "TIME_SLEEP", 0.5)

#: 深交所：一个 symbol 覆盖深市全部 A 股（主板 + 创业板）。
_SZ_SYMBOL = "A股列表"
#: 上交所：主板与科创板是两个 symbol，各一次请求。
_SH_SYMBOLS: tuple[str, ...] = ("主板A股", "科创板")

_SZ_CODE_COL, _SZ_DATE_COL = "A股代码", "A股上市日期"
_SH_CODE_COL, _SH_DATE_COL = "证券代码", "上市日期"

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d")


def to_date(v: Any) -> date | None:
    """把交易所给的上市日期归一成 `date`；空值与无法解析的写法返回 None。"""
    if v is None:
        return None
    try:
        # 必须放在 isinstance 之前：pd.NaT 也是 datetime 的实例，先判类型会漏掉它。
        if pd.isna(v):
            return None
    except (TypeError, ValueError):  # 非标量（理论上不会出现）
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _sources() -> tuple[tuple[str, Any, dict[str, str], str, str], ...]:
    """(市场名, akshare 函数, 调用参数, 代码列, 日期列)。"""
    return (
        ("深交所A股列表", ak.stock_info_sz_name_code, {"symbol": _SZ_SYMBOL},
         _SZ_CODE_COL, _SZ_DATE_COL),
        ("上交所主板A股", ak.stock_info_sh_name_code, {"symbol": _SH_SYMBOLS[0]},
         _SH_CODE_COL, _SH_DATE_COL),
        ("上交所科创板", ak.stock_info_sh_name_code, {"symbol": _SH_SYMBOLS[1]},
         _SH_CODE_COL, _SH_DATE_COL),
    )


def _fetch_one(label: str, fn: Any, kw: dict[str, str]) -> pd.DataFrame:
    """单市场列表请求（带重试）；失败或返回空一律抛 FetchError。"""
    try:
        df = retry_fetch(fn, fetch_retry=_FETCH_RETRY, time_sleep=_TIME_SLEEP, **kw)
    except Exception as exc:  # noqa: BLE001 —— 网络源异常类型不定
        raise FetchError(f"{label}列表请求失败（{kw}）", exc) from exc
    if df is None or len(df) == 0:
        raise FetchError(f"{label}列表返回空（{kw}）", ValueError("empty frame"))
    return df


def fetch_list_date_map() -> dict[str, date]:
    """``{code: 上市日期}``，覆盖沪 + 深全市场。

    单个市场失败只记 warning 并跳过，不阻断其它市场 —— `list_date` 是可按市场增量
    补齐的展示字段，不值得为它让整条回填失败；调用方按「本次拿到多少行」对账即可。
    """
    out: dict[str, date] = {}
    for label, fn, kw, code_col, date_col in _sources():
        try:
            df = _fetch_one(label, fn, kw)
        except FetchError as exc:
            log.warning("%s", exc)
            continue
        n = 0
        for code, raw in zip(df[code_col], df[date_col], strict=True):
            d = to_date(raw)
            c = str(code).strip()
            if c and d is not None:
                out[c] = d
                n += 1
        log.info("%s：%d 行 → 解析出 %d 个上市日期", label, len(df), n)
    return out
