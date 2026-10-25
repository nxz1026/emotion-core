"""`caliber_b` 单测：口径B 的成交判定、顺延、截断与分组口径（全部 DB-free）。

这里钉的四件事都是踩过的坑：
1. 成交判据是 `high >= 涨停价`（触及即成交），不是「收盘还封着」；
2. 未成交按 0 计进「每信号」，但**不能**混进「成交后」；
3. T+2 开盘=收盘=跌停才算封死、才顺延 T+3；
4. T+2 缺失 → `filled=None`（不计入任何口径），不猜价。
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from emotion_core.algorithms import caliber_b as cb

D0 = date(2026, 9, 1)
D1 = date(2026, 9, 2)
D2 = date(2026, 9, 3)
D3 = date(2026, 9, 4)


def _bars(rows: list[dict]) -> dict[str, pd.DataFrame]:
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return {code: g.set_index("date") for code, g in df.groupby("code")}


def _row(
    d: date,
    *,
    close: float,
    high: float | None = None,
    open_: float | None = None,
    pre: float | None = None,
    vol: float = 1e6,
    ld: bool = False,
) -> dict:
    return {
        "code": "600001",
        "date": d,
        "open": open_ if open_ is not None else close,
        "high": high if high is not None else close,
        "low": close,
        "close": close,
        "pre_close": pre,
        "volume": vol,
        "is_limit_down": ld,
    }


def _sig(d: date = D0) -> pd.Series:
    return pd.Series(
        {
            "confirm_date": d,
            "code": "600001",
            "name": "测试股",
            "phase": "退潮",
            "diverge": False,
            "tier": "新插队",
        }
    )


class TestTierOf:
    def test_parses_tier_from_c3_row(self):
        rows = [
            ["c1 x", True, "唯一换手高标"],
            ["c3 身份档（不否决）", None, "身份 身位延续：昨天在组里"],
        ]
        assert cb._tier_of(rows) == "身位延续"

    def test_accepts_json_string(self):
        rows = [["c3 身份档（不否决）", None, "身份 新插队：不在昨日最高组内"]]
        assert cb._tier_of(json.dumps(rows, ensure_ascii=False)) == "新插队"

    def test_missing_or_bad_input_gives_none(self):
        assert cb._tier_of(None) is None
        assert cb._tier_of("not json") is None
        assert cb._tier_of([["c1 x", True, "无身份"]]) is None


class TestOne:
    def test_filled_when_high_touches_limit(self):
        # T 收 10.00 → 涨停价 11.00；T+1 最高 11.00 即成交
        bars = _bars(
            [
                _row(D0, close=10.0),
                _row(D1, close=11.0, high=11.0, pre=10.0),
                _row(D2, close=11.5, open_=11.2, pre=11.0),
            ]
        )
        got = cb._one(_sig(), bars)
        assert got["filled"] is True
        assert got["entry_px"] == 11.0
        assert got["ret_pct"] == pytest.approx(round((11.2 / 11.0 - 1) * 100 - 0.15, 3))

    def test_not_filled_when_high_below_limit(self):
        bars = _bars(
            [
                _row(D0, close=10.0),
                _row(D1, close=10.8, high=10.9, pre=10.0),
                _row(D2, close=11.0, open_=11.0, pre=10.8),
            ]
        )
        got = cb._one(_sig(), bars)
        assert got["filled"] is False
        assert got["ret_pct"] == 0.0
        assert got["reason_null"] == "未触及涨停价"

    def test_rolled_when_t1_open_and_close_at_limit_down(self):
        # T+2 开盘=收盘=跌停（pre 10.0 → 跌停 9.00）→ 顺延 T+3 开盘
        bars = _bars(
            [
                _row(D0, close=10.0),
                _row(D1, close=11.0, high=11.0, pre=10.0),
                _row(D2, close=9.0, open_=9.0, pre=11.0, ld=True),
                _row(D3, close=9.2, open_=9.1, pre=9.0),
            ]
        )
        got = cb._one(_sig(), bars)
        assert got["rolled"] is True
        assert got["exit_px"] == 9.1

    def test_not_rolled_when_limit_down_but_intraday_recovered(self):
        # 开盘跌停但收盘拉起来 → 有成交，不判封死
        bars = _bars(
            [
                _row(D0, close=10.0),
                _row(D1, close=11.0, high=11.0, pre=10.0),
                _row(D2, close=10.2, open_=9.0, pre=11.0),
            ]
        )
        got = cb._one(_sig(), bars)
        assert got["rolled"] is False
        assert got["exit_px"] == 9.0

    def test_censored_when_t2_missing(self):
        bars = _bars([_row(D0, close=10.0), _row(D1, close=11.0, high=11.0, pre=10.0)])
        got = cb._one(_sig(), bars)
        assert got["filled"] is None
        assert got["ret_pct"] is None

    def test_missing_code_gives_none(self):
        assert cb._one(_sig(), {}) is None


class TestSummarize:
    def _df(self) -> pd.DataFrame:
        rows = [
            # 成交：+3% / −3%
            {
                "confirm_date": D0,
                "code": "600001",
                "name": "a",
                "phase": "退潮",
                "diverge": False,
                "tier": "新插队",
                "entry_px": 11.0,
                "exit_px": 11.6,
                "rolled": False,
                "filled": True,
                "ret_pct": 3.0,
                "reason_null": None,
            },
            {
                "confirm_date": D0,
                "code": "600002",
                "name": "b",
                "phase": "退潮",
                "diverge": False,
                "tier": "新插队",
                "entry_px": 11.0,
                "exit_px": 10.4,
                "rolled": False,
                "filled": True,
                "ret_pct": -3.0,
                "reason_null": None,
            },
            # 未成交：0 只进「每信号」
            {
                "confirm_date": D0,
                "code": "600003",
                "name": "c",
                "phase": "高潮",
                "diverge": False,
                "tier": "身位延续",
                "entry_px": 11.0,
                "exit_px": None,
                "rolled": False,
                "filled": False,
                "ret_pct": 0.0,
                "reason_null": "未触及涨停价",
            },
            # 右截断：不进任何口径
            {
                "confirm_date": D0,
                "code": "600004",
                "name": "d",
                "phase": "高潮",
                "diverge": False,
                "tier": "新插队",
                "entry_px": 11.0,
                "exit_px": None,
                "rolled": False,
                "filled": None,
                "ret_pct": None,
                "reason_null": "T+2 缺失（样本右截断）",
            },
        ]
        return pd.DataFrame(rows)

    def test_fill_rate_excludes_censored(self):
        res = cb.summarize(self._df())
        base = next(g for g in res["groups"] if g["group"] == "全部")
        assert base["n"] == 3  # 右截断那条不计
        assert base["fill_rate"] == pytest.approx(200 / 3, abs=0.1)

    def test_per_signal_counts_zero_but_filled_does_not(self):
        base = next(g for g in cb.summarize(self._df())["groups"] if g["group"] == "全部")
        assert base["mean"] == pytest.approx(0.0, abs=1e-9)  # (3-3+0)/3
        assert base["mean_filled"] == pytest.approx(0.0, abs=1e-9)  # (3-3)/2
        assert base["n_filled"] == 2

    def test_groups_split_by_phase_and_tier(self):
        names = [g["group"] for g in cb.summarize(self._df())["groups"]]
        assert "退潮" in names and "高潮" in names
        assert "新插队" in names and "身位延续" in names

    def test_empty_input_gives_zero(self):
        assert cb.summarize(pd.DataFrame()) == {"n": 0, "groups": []}
