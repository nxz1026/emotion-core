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
import threading
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

#: 单市场请求的硬超时（秒）。akshare 内部 requests **不带超时**，上游不回包就会把
#: 调用方无限期钉住——2026-10-13 实测：daily 的 `basic` 步在上交所列表请求上卡死
#: 7 小时，顺带把当天 derive/market/ladder/signal/... 全部拖没。见 `_fetch_one`。
_FETCH_TIMEOUT: float = 20.0

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
    """单市场列表请求（带重试 + **硬超时**）；失败/超时/返回空一律抛 FetchError。

    为什么要超时：akshare 内部的 ``requests`` 调用没有 timeout 参数，上游半开连接
    或不回包时 ``retry_fetch`` 也救不了——它只会无限等下去。2026-10-13 实测
    ``ak.stock_info_sh_name_code`` 把 daily 的 ``basic`` 步钉死 7 小时（进程
    ``S`` 状态、ESTAB 到 ``202.122.113.77:443``），当天后续 9 个步骤全部没跑。

    CPython 无法真正 kill 一个卡在 socket read 的线程，所以用 **daemon 线程 +
    ``join(timeout)``**：超时后调用方立刻放弃该市场（按设计只 warning 跳过），
    卡住的线程随进程退出消失。代价是超时场景会泄漏一个线程——远小于卡死流水线。

    超时值 ``_FETCH_TIMEOUT`` 取 20s：正常一次列表请求实测 <2s（含重试），
    20s 足够容错又不至于让某个市场拖住整步。
    """
    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["df"] = retry_fetch(
                fn, fetch_retry=_FETCH_RETRY, time_sleep=_TIME_SLEEP, **kw)
        except Exception as exc:  # noqa: BLE001 —— 网络源异常类型不定
            box["err"] = exc

    t = threading.Thread(target=_run, name=f"exchange-list-{label}", daemon=True)
    t.start()
    t.join(_FETCH_TIMEOUT)
    if t.is_alive():
        raise FetchError(f"{label}列表请求超时（>{_FETCH_TIMEOUT:g}s）",
                         TimeoutError(f"timeout after {_FETCH_TIMEOUT:g}s"))
    if "err" in box:
        exc = box["err"]
        raise FetchError(f"{label}列表请求失败（{kw}）", exc) from exc
    df = box.get("df")
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
            n = 0
            for code, raw in zip(df[code_col], df[date_col], strict=True):
                d = to_date(raw)
                c = str(code).strip()
                if len(c) == 6 and c.isdigit() and d is not None:
                    out[c] = d
                    n += 1
            log.info("%s：%d 行 → 解析出 %d 个上市日期", label, len(df), n)
        except FetchError as exc:
            log.warning("%s", exc)
        except (KeyError, ValueError, TypeError) as exc:
            # 列名漂移（KeyError）/ 两列长度不齐（strict=True → ValueError）：只丢这
            # 一个市场。2026-10-13 订正——try 原先只包住 _fetch_one，解析循环在外面，
            # 任一市场改列名会让**另外两个市场已拿到的数据一起丢**，与上面 docstring
            # 承诺的「单市场失败只 warning 跳过」不符。
            log.warning("%s 解析失败（%r），跳过该市场", label, exc)
    return out
