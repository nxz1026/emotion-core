"""腾讯日线适配器：stock_zh_a_hist_tx（退市股兜底）。

docs/04 §1：腾讯 stock_zh_a_hist_tx 对 bj 报 KeyError: 'day'，
但对退市股有兜底作用。
"""
from __future__ import annotations

from datetime import date

import akshare as ak
import pandas as pd

from emotion_core.data.providers.base import DailyBarProvider, ProviderError, normalize_frame


class TencentProvider:
    """使用不复权腾讯历史，成交量转为手、换手率转为百分数。"""

    name = "tencent"

    def fetch_daily_bars(self, code: str, start: date, end: date) -> pd.DataFrame:
        try:
            symbol = ("sh" if code.startswith("6") else "sz") + code
            raw = ak.stock_zh_a_hist_tx(symbol=symbol,
                                        start_date=start.strftime("%Y%m%d"),
                                        end_date=end.strftime("%Y%m%d"),
                                        adjust="")
            raw = raw.rename(columns={"date": "date", "open": "open", "high": "high",
                                      "low": "low", "close": "close", "volume": "volume",
                                      "amount": "amount"})
            raw["volume"] = pd.to_numeric(raw["volume"], errors="coerce") / 100
            return normalize_frame(raw, code)
        except Exception as exc:
            raise ProviderError(f"tencent: {str(exc)[:100]}") from exc


PROVIDER: DailyBarProvider = TencentProvider()
