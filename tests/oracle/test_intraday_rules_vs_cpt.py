"""本仓盘中判据与 CPT `cpt/domain/intraday_rules.py` 的跨仓对账。

ADR 0003 §E2 定的边界：**触发判据归本仓**，CPT 只交原始五档。
但两边都要算「涨停价」「是不是封板」，这两件事一旦各算各的，
就会出现「本仓说封了、CPT 说没封」——而没有对账就没人发现。

2026-10-22 的第一次实跑就抓到一处真分歧：本仓 `board_pct_limit` 把
ST 判定放在最前，北交所 ST（920xxx）落到「非主板 → 20%」，CPT 按 30%。
40 条 `limit_price` 不一致、60 条 `is_at_limit` 不一致，全在这个组合上。
已修（见 `algorithms/intraday.py::board_pct_limit` 的 docstring）。

本文件是**门禁**：CPT 仓不在本机时整体 skip（`TestConstantsMatchCptLive` 既有先例），
在时逐条比对，差一条就红。
"""

from __future__ import annotations

import os
import sys

import pytest

from emotion_core.algorithms import intraday as it

CPT_PATH = "/home/ubuntu/DSH/Chan_Pattern_Trader"

#: 覆盖各板块 × ST 组合 × 价格档（含低价股与高价股）。
_CODES = (
    "600519",
    "601398",
    "603288",
    "605499",
    "000001",
    "002594",
    "300750",
    "301159",
    "688981",
    "689009",
    "920296",
    "830799",
    "430047",
)
_NAMES = (None, "ST测试", "*ST海投", "N文峰")
_PRICES = (1.01, 3.03, 7.77, 10.0, 11.45, 12.34, 100.0, 1309.59, 0.99, 5.55)


@pytest.fixture(scope="module")
def cpt_rules():
    if os.path.isdir(CPT_PATH) and CPT_PATH not in sys.path:
        sys.path.insert(0, CPT_PATH)
    try:
        from cpt.domain import intraday_rules
    except ImportError:  # pragma: no cover - 非同一机器
        pytest.skip("CPT 仓不可用（非同一机器）")
    return intraday_rules


class TestBoardPctMatchesCpt:
    def test_board_pct_limit_equal(self, cpt_rules):
        bad = []
        for code in _CODES:
            for name in _NAMES:
                mine = it.board_pct_limit(code, name=name)
                theirs = cpt_rules.board_pct_limit(code, name=name)
                if mine != theirs:
                    bad.append((code, name, mine, theirs))
        assert not bad, f"板块幅度分歧：{bad}"

    def test_st_name_equal(self, cpt_rules):
        for name in ("", "ST测试", "*ST海投", "st测试", "N文峰", None):
            assert it.is_st_name(name) == cpt_rules.is_st_name(name)

    def test_is_main_board_equal(self, cpt_rules):
        for code in _CODES:
            assert it.is_main_board(code) == cpt_rules.is_main_board(code)


class TestLimitPriceMatchesCpt:
    def test_limit_price_equal(self, cpt_rules):
        bad = []
        for code in _CODES:
            for name in _NAMES:
                for pre in _PRICES:
                    mine = it.limit_price(code, pre, name=name)
                    theirs = cpt_rules.limit_up_price(code, pre, name=name)
                    if mine is None or theirs is None:
                        if (mine is None) != (theirs is None):
                            bad.append((code, name, pre, mine, theirs))
                        continue
                    if abs(mine - theirs) > 1e-9:
                        bad.append((code, name, pre, mine, theirs))
        assert not bad, f"涨停价分歧：{bad}"

    def test_no_pre_close_gives_none(self, cpt_rules):
        for code in _CODES:
            assert it.limit_price(code, 0.0) is None
            assert it.limit_price(code, -1.0) is None
            assert cpt_rules.limit_up_price(code, 0.0) is None


class TestIsAtLimitMatchesCpt:
    def test_is_at_limit_equal(self, cpt_rules):
        bad = []
        for code in _CODES:
            for name in _NAMES:
                for pre in _PRICES:
                    limit = cpt_rules.limit_up_price(code, pre, name=name)
                    if limit is None:
                        continue
                    for last in (limit, round(limit - 0.01, 2), round(limit + 0.01, 2), round(limit - 1, 2)):
                        quote = {"last": last, "prev_close": pre, "code": code, "name": name}
                        mine = it.is_at_limit(quote)
                        theirs = cpt_rules.is_at_limit_up(code, last=last, pre_close=pre, name=name)
                        if mine != theirs:
                            bad.append((code, name, pre, last, mine, theirs))
        assert not bad, f"封板判定分歧：{bad}"


class TestPinnedCases:
    """把已知边界钉死，防止两边同时改错。"""

    def test_bse_st_is_thirty_percent(self):
        # 2026-10-22 修的那条：北交所 ST 是 30%，不是 20% 也不是 5%。
        assert it.board_pct_limit("920296", name="ST测试") == 30.0
        assert it.board_pct_limit("830799", name="ST测试") == 30.0
        assert it.board_pct_limit("430047", name="ST测试") == 30.0

    def test_growth_st_is_twenty_percent(self):
        assert it.board_pct_limit("300750", name="ST测试") == 20.0
        assert it.board_pct_limit("688981", name="*ST海投") == 20.0

    def test_main_st_is_five_percent(self):
        assert it.board_pct_limit("600519", name="ST测试") == 5.0
        assert it.board_pct_limit("000001", name="*ST海投") == 5.0

    def test_main_is_ten_percent(self):
        assert it.board_pct_limit("600519") == 10.0
        assert it.board_pct_limit("002594", name="N文峰") == 10.0
