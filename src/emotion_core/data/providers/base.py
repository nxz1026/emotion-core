"""日线行情 Provider 共同协议与归一化工具。

从 lkl/providers/base.py 移植，去掉 lkl 依赖。
"""
from __future__ import annotations

from datetime import date
from typing import Protocol

import pandas as pd

BAR_COLS = ["code", "date", "open", "high", "low", "close",
            "pre_close", "volume", "amount", "turnover_rate"]


class ProviderError(Exception):
    """Provider 网络、解析或数据错误。"""


# ── 抓取边界的两类异常：唯一定义处在本层（数据源协议层）──────────────
# P3-4 分层语义：网络故障→FetchError（可重试），协议变化→DataError（重试无意义）。
# 2026-09-29 架构守护（tests/architecture）：原先这两个类在 `services/ingest.py`
# 与 `data/providers/eastmoney.py` 各逐字重复一份，逼得 `pytdx_provider.py` 反向
# import `services`。现收归本层，两处改为从这里 import 并同名再导出——外部
# `from emotion_core.services.ingest import DataError` 的既有写法照旧可用。
class FetchError(Exception):
    """网络/HTTP 层失败（超时、5xx、连接拒绝）——可重试。"""

    def __init__(self, msg, cause):
        super().__init__(f"{msg}: {cause}")
        self.cause = cause


class DataError(Exception):
    """协议/数据结构非法（非 JSON、非 dict、字段缺失）——重试无意义。"""


class DailyBarProvider(Protocol):
    """输出 daily_bar 落库同口径 DataFrame 的日线适配器。"""

    name: str

    def fetch_daily_bars(self, code: str, start: date, end: date) -> pd.DataFrame:
        """获取不复权日线，列名、单位与 daily_bar 完全一致。"""


def normalize_frame(frame: pd.DataFrame, code: str) -> pd.DataFrame:
    """校准字段、类型、排序，并按前一收盘价补齐缺省 pre_close。"""
    required = [col for col in BAR_COLS if col != "pre_close"]
    missing = [col for col in required if col not in frame.columns]
    if missing:
        raise ValueError(f"缺少列: {','.join(missing)}")
    out = frame.copy()
    out["code"] = code
    out["date"] = pd.to_datetime(out["date"], errors="raise").dt.date
    out = out.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    for col in BAR_COLS[2:]:
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
    if "pre_close" not in out:
        out["pre_close"] = out["close"].shift(1)
    return out[BAR_COLS]


def valid_frame(frame: pd.DataFrame) -> bool:
    """回退口径守卫：字段齐全、价格正数、最高不低于最低、日期升序。"""
    if frame.empty or list(frame.columns) != BAR_COLS:
        return False
    if frame["date"].duplicated().any() or not frame["date"].is_monotonic_increasing:
        return False
    prices = frame[["open", "high", "low", "close"]]
    return bool((prices > 0).all().all() and (frame["high"] >= frame["low"]).all())
