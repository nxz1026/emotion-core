"""日线行情 Provider 共同协议与归一化工具。

从 lkl/providers/base.py 移植，去掉 lkl 依赖。
"""
from __future__ import annotations

from datetime import date
from typing import Protocol

import pandas as pd

from emotion_core.utils.errors import DataError, FetchError, ProviderError

BAR_COLS = ["code", "date", "open", "high", "low", "close",
            "pre_close", "volume", "amount", "turnover_rate"]

__all__ = [
    "BAR_COLS",
    "DailyBarProvider",
    "DataError",
    "FetchError",
    "ProviderError",
    "normalize_frame",
    "valid_frame",
]


class DailyBarProvider(Protocol):
    """输出 daily_bar 落库同口径 DataFrame 的日线适配器。"""

    name: str

    def fetch_daily_bars(self, code: str, start: date, end: date) -> pd.DataFrame:
        """获取不复权日线，列名、单位与 daily_bar 完全一致。"""


def normalize_frame(frame: pd.DataFrame, code: str) -> pd.DataFrame:
    """校准字段、类型、排序，并按前一收盘价补齐缺省 pre_close。

    ⚠️ 2026-10-07 修：`code` **不在**必需列里——本函数下一行就是 `out["code"] = code`，
    要求调用方自带 code 是自相矛盾的。原先把 `code` 算进 `required`，导致任何不带
    code 列的 provider 恒定抛 `缺少列: code`：`PytdxProvider`（TDX 返回体没有 code）
    与 `TencentProvider` 都因此**从未成功返回过一行**，只是被测试里 mock 掉的
    `normalize_frame` 掩盖了。pre_close 同理是「缺失时自动补」，不进必需列。
    """
    required = [col for col in BAR_COLS if col not in ("code", "pre_close")]
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
