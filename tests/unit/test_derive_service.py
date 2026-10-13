"""判据 + 连板服务（services.derive_service）：run 计算写入，不连库。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from emotion_core.services import derive_service


class TestToCents:
    def test_none_returns_zero(self):
        assert derive_service._to_cents(None) == 0

    def test_nan_returns_zero(self):
        assert derive_service._to_cents(float("nan")) == 0

    def test_converts_to_cents(self):
        assert derive_service._to_cents(10.5) == 1050


class TestToInt:
    def test_none_returns_zero(self):
        assert derive_service._to_int(None) == 0

    def test_nan_returns_zero(self):
        # 回归：688496 停牌，volume 为 NULL → pandas 读成 NaN → int(nan) 曾抛 ValueError
        assert derive_service._to_int(float("nan")) == 0

    def test_truncates(self):
        assert derive_service._to_int(1000000) == 1000000
        assert derive_service._to_int(3.7) == 3


class TestToFloat:
    def test_none_and_nan_return_zero(self):
        assert derive_service._to_float(None) == 0.0
        assert derive_service._to_float(float("nan")) == 0.0

    def test_passes_value(self):
        assert derive_service._to_float(5.0) == 5.0


class TestLoadBars:
    def test_empty_returns_empty(self, monkeypatch):
        monkeypatch.setattr(derive_service, "query_df",
                            lambda sql, params=(), conn=None: pd.DataFrame())
        assert derive_service._load_bars(date(2026, 9, 25)) == []

    def test_loads_and_converts(self, monkeypatch):
        codes_df = pd.DataFrame({"code": ["000001"]})
        bars_df = pd.DataFrame({
            "code": ["000001"],
            "date": [date(2026, 9, 25)],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "pre_close": [10.0],
            "volume": [1000000],
            "turnover_rate": [5.0],
        })

        call_count = []

        def fake_query(sql, params=(), conn=None):
            call_count.append(sql)
            if "DISTINCT code" in sql:
                return codes_df
            return bars_df

        monkeypatch.setattr(derive_service, "query_df", fake_query)
        bars = derive_service._load_bars(date(2026, 9, 25))
        assert len(bars) == 1
        assert bars[0].code == "000001"
        assert bars[0].close_cents == 1050

    def test_suspended_stock_all_nan_columns_does_not_crash(self, monkeypatch):
        """回归：688496 停牌行的 open/high/low/volume/amount 全为 NaN。

        此前 `int(r.volume)` 抛 `ValueError: cannot convert float NaN to integer`，
        让 2026-10-13 的 derive 步失败、当日其余 9 步全没跑。
        """
        nan = float("nan")
        codes_df = pd.DataFrame({"code": ["688496"]})
        bars_df = pd.DataFrame({
            "code": ["688496"],
            "date": [date(2026, 10, 13)],
            "open": [nan], "high": [nan], "low": [nan],
            "close": [0.58], "pre_close": [0.58],
            "volume": [nan], "turnover_rate": [nan],
        })

        def fake_query(sql, params=(), conn=None):
            return codes_df if "DISTINCT code" in sql else bars_df

        monkeypatch.setattr(derive_service, "query_df", fake_query)
        bars = derive_service._load_bars(date(2026, 10, 13))
        assert len(bars) == 1
        b = bars[0]
        assert b.volume == 0
        assert b.turnover_rate == 0.0
        assert b.open_cents == 0 and b.close_cents == 58


class TestFilterByDate:
    def test_filters_correctly(self):
        from emotion_core.domain.bar import Bar
        bars = [
            Bar(code="000001", date=date(2026, 9, 24), open_cents=1000,
                 high_cents=1100, low_cents=900, close_cents=1050,
                 pre_close_cents=1000, volume=1000, turnover_rate=5.0),
            Bar(code="000001", date=date(2026, 9, 25), open_cents=1050,
                 high_cents=1150, low_cents=950, close_cents=1100,
                 pre_close_cents=1050, volume=1000, turnover_rate=5.0),
        ]
        result = derive_service._filter_by_date(bars, date(2026, 9, 25))
        assert len(result) == 1
        assert result[0].date == date(2026, 9, 25)


class TestRun:
    def test_no_data_returns_zero(self, monkeypatch):
        monkeypatch.setattr(derive_service, "_load_bars",
                            lambda d: [])
        assert derive_service.run(date(2026, 9, 25)) == 0

    def test_computes_and_writes(self, monkeypatch):
        from emotion_core.domain.bar import Bar

        bars = [
            Bar(code="000001", date=date(2026, 9, 25), open_cents=1000,
                 high_cents=1100, low_cents=900, close_cents=1100,
                 pre_close_cents=1000, volume=1000, turnover_rate=5.0),
        ]
        monkeypatch.setattr(derive_service, "_load_bars", lambda d: bars)
        monkeypatch.setattr(derive_service.indicators, "compute_derived",
                            lambda b: b)
        monkeypatch.setattr(derive_service, "_persist_derived",
                            lambda rows: len(rows))
        n = derive_service.run(date(2026, 9, 25))
        assert n == 1
