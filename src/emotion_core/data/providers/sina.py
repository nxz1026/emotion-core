"""akshare 新浪历史日线备源。"""
from __future__ import annotations

from datetime import date

import akshare as ak
import pandas as pd

from emotion_core.data.providers.base import DailyBarProvider, ProviderError, normalize_frame


class SinaProvider:
    """使用不复权新浪历史，成交量转为股、换手率转为百分数。"""

    name = "sina"

    def fetch_daily_bars(self, code: str, start: date, end: date) -> pd.DataFrame:
        try:
            symbol = ("sh" if code.startswith("6") else "sz") + code
            raw = ak.stock_zh_a_daily(symbol=symbol, start_date=start.strftime("%Y%m%d"),
                                      end_date=end.strftime("%Y%m%d"), adjust="")
            # 兼容中英文列名（akshare 版本不同返回不同列名）
            column_map = {
                "日期": "date", "date": "date",
                "开盘": "open", "open": "open",
                "最高": "high", "high": "high",
                "最低": "low", "low": "low",
                "收盘": "close", "close": "close",
                "成交量": "volume", "volume": "volume",
                "成交额": "amount", "amount": "amount",
                "换手率": "turnover_rate", "turnover": "turnover_rate",
            }
            raw = raw.rename(columns={k: v for k, v in column_map.items() if k in raw.columns})
            raw["turnover_rate"] = pd.to_numeric(raw["turnover_rate"], errors="coerce") * 100
            raw["volume"] = pd.to_numeric(raw["volume"], errors="coerce") / 100
            raw["code"] = code
            return normalize_frame(raw, code)
        except Exception as exc:
            raise ProviderError(f"sina: {str(exc)[:100]}") from exc


PROVIDER: DailyBarProvider = SinaProvider()
