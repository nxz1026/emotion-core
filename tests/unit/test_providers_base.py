"""providers/base.py 日线 Provider 协议与归一化工具测试。"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from emotion_core.data.providers.base import (
    BAR_COLS,
    ProviderError,
    normalize_frame,
    valid_frame,
)


class TestBarCols:
    def test_expected_columns(self):
        assert BAR_COLS == ["code", "date", "open", "high", "low", "close",
                            "pre_close", "volume", "amount", "turnover_rate"]

    def test_code_first_date_second(self):
        assert BAR_COLS[0] == "code"
        assert BAR_COLS[1] == "date"


class TestNormalizeFrame:
    def test_basic(self):
        df = pd.DataFrame({
            "code": ["600519"],
            "date": ["2024-06-15"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
            "volume": [1000.0],
            "amount": [10000.0],
            "turnover_rate": [1.0],
        })
        result = normalize_frame(df, "600519")
        assert list(result.columns) == BAR_COLS
        assert result["code"].iloc[0] == "600519"

    def test_missing_column_raises(self):
        df = pd.DataFrame({"date": ["2024-06-15"], "open": [10.0]})
        with pytest.raises(ValueError, match="缺少列"):
            normalize_frame(df, "600519")

    def test_code_column_is_not_required(self):
        """★2026-10-07 回归：`code` 是本函数**自己补**的（out["code"] = code），
        把它算进必需列是自相矛盾的。

        原实现如此，导致任何不带 code 列的 provider 恒定抛「缺少列: code」——
        PytdxProvider 因此从未成功返回过一行，只是被 test_tencent.py 里 mock 掉的
        normalize_frame 掩盖了。
        """
        df = pd.DataFrame({
            "date": ["2024-06-15"],
            "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
            "volume": [1000.0], "amount": [10000.0], "turnover_rate": [1.0],
        })
        assert "code" not in df.columns, "本测试的前提就是输入没有 code 列"
        result = normalize_frame(df, "600519")
        assert list(result.columns) == BAR_COLS
        assert result["code"].iloc[0] == "600519"

    def test_existing_code_column_is_overwritten_by_argument(self):
        """调用方自带的 code 必须被参数覆盖，不能两条 code 列并存。"""
        df = pd.DataFrame({
            "code": ["WRONG"],
            "date": ["2024-06-15"],
            "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
            "volume": [1000.0], "amount": [10000.0], "turnover_rate": [1.0],
        })
        result = normalize_frame(df, "600519")
        assert result["code"].tolist() == ["600519"]

    def test_pre_close_auto_fill(self):
        df = pd.DataFrame({
            "code": ["600519", "600519"],
            "date": ["2024-06-14", "2024-06-15"],
            "open": [10.0, 10.5],
            "high": [10.5, 11.0],
            "low": [9.5, 10.0],
            "close": [10.5, 11.0],
            "volume": [1000.0, 1100.0],
            "amount": [10000.0, 11000.0],
            "turnover_rate": [1.0, 1.1],
        })
        result = normalize_frame(df, "600519")
        assert "pre_close" in result.columns
        # First row pre_close should be NaN, second row = first close
        assert pd.isna(result["pre_close"].iloc[0])
        assert result["pre_close"].iloc[1] == 10.5

    def test_sorted_by_date(self):
        df = pd.DataFrame({
            "code": ["600519", "600519"],
            "date": ["2024-06-15", "2024-06-14"],
            "open": [10.5, 10.0],
            "high": [11.0, 10.5],
            "low": [10.0, 9.5],
            "close": [11.0, 10.5],
            "volume": [1100.0, 1000.0],
            "amount": [11000.0, 10000.0],
            "turnover_rate": [1.1, 1.0],
        })
        result = normalize_frame(df, "600519")
        assert result["date"].iloc[0] < result["date"].iloc[1]

    def test_drop_duplicates(self):
        df = pd.DataFrame({
            "code": ["600519", "600519"],
            "date": ["2024-06-15", "2024-06-15"],
            "open": [10.0, 10.0],
            "high": [11.0, 11.0],
            "low": [9.5, 9.5],
            "close": [10.5, 10.5],
            "volume": [1000.0, 1000.0],
            "amount": [10000.0, 10000.0],
            "turnover_rate": [1.0, 1.0],
        })
        result = normalize_frame(df, "600519")
        assert len(result) == 1


class TestValidFrame:
    def test_empty(self):
        assert valid_frame(pd.DataFrame()) is False

    def test_wrong_columns(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        assert valid_frame(df) is False

    def test_valid(self):
        df = pd.DataFrame({
            "code": ["600519"],
            "date": [date(2024, 6, 15)],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
            "pre_close": [10.0],
            "volume": [1000.0],
            "amount": [10000.0],
            "turnover_rate": [1.0],
        })
        assert valid_frame(df) is True

    def test_negative_price(self):
        df = pd.DataFrame({
            "code": ["600519"],
            "date": [date(2024, 6, 15)],
            "open": [-10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
            "pre_close": [10.0],
            "volume": [1000.0],
            "amount": [10000.0],
            "turnover_rate": [1.0],
        })
        assert valid_frame(df) is False

    def test_high_less_than_low(self):
        df = pd.DataFrame({
            "code": ["600519"],
            "date": [date(2024, 6, 15)],
            "open": [10.0],
            "high": [9.0],
            "low": [9.5],
            "close": [10.5],
            "pre_close": [10.0],
            "volume": [1000.0],
            "amount": [10000.0],
            "turnover_rate": [1.0],
        })
        assert valid_frame(df) is False

    def test_duplicate_dates(self):
        df = pd.DataFrame({
            "code": ["600519", "600519"],
            "date": [date(2024, 6, 15), date(2024, 6, 15)],
            "open": [10.0, 10.0],
            "high": [11.0, 11.0],
            "low": [9.5, 9.5],
            "close": [10.5, 10.5],
            "pre_close": [10.0, 10.5],
            "volume": [1000.0, 1000.0],
            "amount": [10000.0, 10000.0],
            "turnover_rate": [1.0, 1.0],
        })
        assert valid_frame(df) is False


class TestProviderError:
    def test_is_exception(self):
        err = ProviderError("test")
        assert isinstance(err, Exception)

    def test_str(self):
        err = ProviderError("tencent: failed")
        assert "tencent: failed" in str(err)
