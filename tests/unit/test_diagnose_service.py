"""个股诊断服务（services.diagnose_service）：diagnose 三段式，不连库。"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from emotion_core.domain.signal import Checklist
from emotion_core.services import diagnose_service

D = date(2026, 9, 25)

# entry.rows 的六行（口径 = algorithms/entry.py，测试里替身，避免连库）
COND_ROWS = [
    ("c1 唯一换手高标", True, "唯一换手高标: 平安银行(000001) 4板"),
    ("c2 换手板非一字", True, "换手板(非一字)"),
    ("c3 淘汰赛身份", True, "昨日最高板组2只→候选胜出"),
    ("c4 最低板数门槛", True, "4板 >= 门槛4"),
    ("c5 强度与分歧补偿", None, "⚠ 换手率缺失（非 0）"),
    ("W1 同身位扎堆", True, "⚠ 同身位扎堆：绝对最高 4板 2 只"),
]


def _frames(*, ladder=None, technical=None, fundamental=None, market=None,
            sole_top=None, signals=None, themes=None):
    """按 SQL 内容分派的 query_df 替身（与既有 5 个测试的分派语义一致，各表独立成帧）。"""
    def fake(sql, params=(), conn=None):
        if "theme_tag" in sql:
            return themes if themes is not None else pd.DataFrame()
        if "signal" in sql:
            return signals if signals is not None else pd.DataFrame()
        if "ladder_day" in sql and "daily_bar" in sql:
            return sole_top if sole_top is not None else pd.DataFrame()
        if "ladder_day" in sql:
            return ladder if ladder is not None else pd.DataFrame()
        if "derived_bar" in sql:
            return technical if technical is not None else pd.DataFrame()
        if "stock_basic" in sql:
            return fundamental if fundamental is not None else pd.DataFrame()
        if "market_stat" in sql:
            return market if market is not None else pd.DataFrame()
        return pd.DataFrame()
    return fake


LADDER_DF = pd.DataFrame({
    "code": ["000001", "000002"], "cont_days": [4, 4], "is_exchange": [True, False],
    "is_top": [True, False], "is_sole_top": [True, False],
    "y_top_group_count": [2, 2], "y_top_survivor_count": [1, 1],
})
MARKET_DF = pd.DataFrame({
    "date": [D], "phase": ["高潮"], "buy_window": ["ENHANCED"], "has_candidate": [True],
    "diverge": [True], "dragon_env": ["FAVORABLE"],
    "dragon_env_reasons": [[{"cond": "G1 可交易高度扩张", "ok": True, "note": "高度上行"}]],
    "dragon_env_risks": [[]],
})


class TestDiagnose:

    def test_basic_shape(self, monkeypatch):
        monkeypatch.setattr(diagnose_service, "query_df",
                            lambda sql, params=(), conn=None: pd.DataFrame())
        monkeypatch.setattr(diagnose_service, "prev_trading_day",
                            lambda d: date(2026, 9, 25))
        result = diagnose_service.diagnose("000001")
        assert result["code"] == "000001"
        assert "technical" in result
        assert "fundamental" in result
        assert "market" in result

    def test_with_as_of_date(self, monkeypatch):
        monkeypatch.setattr(diagnose_service, "query_df",
                            lambda sql, params=(), conn=None: pd.DataFrame())
        result = diagnose_service.diagnose("000001", date(2026, 9, 20))
        assert result["as_of"] == date(2026, 9, 20)

    def _make_query(self, technical_df, fundamental_df, market_df):
        """Return a query_df stub that dispatches on SQL content."""
        def fake_query(sql, params=(), conn=None):
            if "derived_bar" in sql:
                return technical_df
            if "stock_basic" in sql:
                return fundamental_df
            if "market_stat" in sql:
                return market_df
            return pd.DataFrame()
        return fake_query

    def test_technical_data_present(self, monkeypatch):
        tech_df = pd.DataFrame({
            "is_limit_up": [True],
            "is_exchange": [False],
            "cont_days": [3],
            "amplitude": [10.5],
        })
        monkeypatch.setattr(diagnose_service, "query_df",
                            self._make_query(tech_df, pd.DataFrame(), pd.DataFrame()))
        result = diagnose_service.diagnose("000001", date(2026, 9, 25))
        assert result["technical"]["is_limit_up"] is True
        assert result["technical"]["cont_days"] == 3
        assert result["technical"]["amplitude"] == 10.5

    def test_fundamental_data_present(self, monkeypatch):
        fund_df = pd.DataFrame({
            "name": ["平安银行"],
            "industry": ["银行"],
            "market_cap": [1000.5],
        })
        monkeypatch.setattr(diagnose_service, "query_df",
                            self._make_query(pd.DataFrame(), fund_df, pd.DataFrame()))
        result = diagnose_service.diagnose("000001", date(2026, 9, 25))
        assert result["fundamental"]["name"] == "平安银行"
        assert result["fundamental"]["industry"] == "银行"

    def test_market_data_present(self, monkeypatch):
        mkt_df = pd.DataFrame({
            "phase": ["UPTREND"],
            "buy_window": ["OPEN"],
        })
        monkeypatch.setattr(diagnose_service, "query_df",
                            self._make_query(pd.DataFrame(), pd.DataFrame(), mkt_df))
        result = diagnose_service.diagnose("000001", date(2026, 9, 25))
        assert result["market"]["phase"] == "UPTREND"
        assert result["market"]["buy_window"] == "OPEN"


class TestSegA:
    """A 段：当日身份 + 五条件逐项（判定只走 entry，本模块不重写规则）。"""

    def test_not_applicable_when_absent_from_ladder(self, monkeypatch):
        monkeypatch.setattr(diagnose_service, "query_df", _frames())
        monkeypatch.setattr(diagnose_service.entry, "rows",
                            lambda *a, **k: pytest.fail("非候选不得调用 entry.rows"))
        a = diagnose_service.diagnose("000001", D)["A"]
        assert a["applicable"] is False
        assert a["conditions"] == [] and a["passed"] is None
        assert "非候选" in a["note"] and a["reason"]

    def test_conditions_from_entry_rows(self, monkeypatch):
        seen = {}

        def fake_rows(cand, window, min_days=None, as_of=None, secondary=False):
            seen["cand"] = cand
            return COND_ROWS

        monkeypatch.setattr(diagnose_service, "query_df",
                            _frames(ladder=LADDER_DF, market=MARKET_DF))
        monkeypatch.setattr(diagnose_service.entry, "rows", fake_rows)
        a = diagnose_service.diagnose("000001", D)["A"]
        assert a["applicable"] is True
        assert a["cont_days"] == 4 and a["is_sole_top"] is True
        # 交给 entry 的候选身份 = ladder_day 在册行（不是本地编的）
        cand = seen["cand"]
        assert (cand.date, cand.code, cand.cont_days, cand.is_exchange,
                cand.y_top_group_count, cand.y_top_survivor_count) == (D, "000001", 4, True, 2, 1)
        assert a["layer"] == "4->5"
        assert [c["id"] for c in a["conditions"]] == ["c1", "c2", "c3", "c4", "c5"]
        assert a["conditions"][0]["status"] == "PASS"
        # UNKNOWN 不否决：c5 为 None 时 passed 仍为 True（口径 = entry.passed_of）
        assert a["conditions"][4]["status"] == "UNKNOWN"
        assert a["passed"] is True
        assert a["warnings"][0]["id"] == "w1" and a["warnings"][0]["status"] == "WARN"
        assert a["peers"]["same_cont"] == 2 and a["window"] == "ENHANCED"

    def test_failed_condition_fails_passed(self, monkeypatch):
        rows = [*COND_ROWS[:3], ("c4 最低板数门槛", False, "4板 >= 门槛5"), COND_ROWS[4],
                COND_ROWS[5]]
        monkeypatch.setattr(diagnose_service, "query_df",
                            _frames(ladder=LADDER_DF))
        monkeypatch.setattr(diagnose_service.entry, "rows",
                            lambda *a, **k: rows)
        a = diagnose_service.diagnose("000001", D)["A"]
        assert a["conditions"][3]["status"] == "FAIL"
        assert a["passed"] is False

    def test_rows_without_warning_row_still_aggregates(self, monkeypatch):
        """entry._WARNINGS 为空时只有 5 行 → 警告位留 None，仍按位置构造给聚合器。"""
        monkeypatch.setattr(diagnose_service, "query_df", _frames(ladder=LADDER_DF))
        monkeypatch.setattr(diagnose_service.entry, "rows",
                            lambda *a, **k: COND_ROWS[:5])
        a = diagnose_service.diagnose("000001", D)["A"]
        assert a["warnings"] == [] and a["passed"] is True

    def test_entry_rows_row_order_change_falls_back_to_checklist(self, monkeypatch):
        """行序变了（未来有人改 entry 的条件序）→ 退回 entry.checklist，不自行聚合。"""
        called = []
        monkeypatch.setattr(diagnose_service, "query_df", _frames(ladder=LADDER_DF))
        monkeypatch.setattr(diagnose_service.entry, "rows",
                            lambda *a, **k: list(reversed(COND_ROWS)))

        def fake_checklist(cand, window, min_days=None, as_of=None):
            called.append(cand.code)
            return Checklist(True, True, True, True, True, None)
        monkeypatch.setattr(diagnose_service.entry, "checklist", fake_checklist)
        a = diagnose_service.diagnose("000001", D)["A"]
        assert called == ["000001"] and a["passed"] is True

    def test_entry_failure_degrades(self, monkeypatch):
        monkeypatch.setattr(diagnose_service, "query_df", _frames(ladder=LADDER_DF))
        monkeypatch.setattr(diagnose_service.entry, "rows",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("上游缺数")))
        a = diagnose_service.diagnose("000001", D)["A"]
        assert a["applicable"] is True and a["conditions"] == []
        assert "不可核验" in a["note"]


class TestSegB:
    """B 段：历史身份 + 历史唯一最高板与其后 T+1/T+3/T+5 + 历史信号与结果。"""

    def test_history_and_signals(self, monkeypatch):
        fundamental = pd.DataFrame({
            "name": ["平安银行"], "industry": ["银行"], "market_cap": [1000.5],
            "is_st": [False], "list_date": [date(1991, 4, 3)],
            "first_bar_date": [date(2026, 9, 1)], "in_market": [True],
        })
        sole_top = pd.DataFrame({
            "date": [date(2026, 9, 1), date(2026, 8, 3)],
            "t1_ret": [3.0, -1.0], "t3_ret": [-2.0, 5.0], "t5_ret": [None, 8.0],
        })
        signals = pd.DataFrame({
            "confirm_date": [date(2026, 9, 1)], "action": ["BUY"], "status": ["ADOPTED"],
            "buy_window": ["ENHANCED"], "source": ["replay"],
            "checklist": [[["c1 唯一换手高标", True, "唯一"]]],
            "t1_gap": [1.5], "t1_promote": [True], "t1_close_ret": [3.0],
            "max_up5": [9.0], "max_dd5": [-4.0], "t5_close_ret": [None],
            "rule_ret_a": [2.2], "rule_ret_d": [None], "complete": [False],
        })
        monkeypatch.setattr(diagnose_service, "query_df",
                            _frames(fundamental=fundamental, sole_top=sole_top,
                                    signals=signals))
        b = diagnose_service.diagnose("000001", D)["B"]
        assert b["identity"]["first_bar_date"] == date(2026, 9, 1)
        assert b["identity"]["is_new_issuer"] is True       # 24 天 < 90 天
        assert b["sole_top_n"] == 2
        assert [h["t1_ret"] for h in b["sole_top_history"]] == [3.0, -1.0]
        assert b["return_summary"]["avg_t1"] == 1.0
        assert b["return_summary"]["win_rate_t1"] == 50.0
        assert b["return_summary"]["avg_t5"] == 8.0         # None 不计入
        assert b["signal_n"] == 1
        sig = b["signals"][0]
        assert sig["outcome"]["t1_promote"] is True
        assert sig["outcome"]["t5_close_ret"] is None
        assert sig["checklist"] == [{"label": "c1 唯一换手高标", "ok": True, "note": "唯一"}]

    def test_missing_first_bar_marks_unverifiable(self, monkeypatch):
        monkeypatch.setattr(diagnose_service, "query_df", _frames())
        b = diagnose_service.diagnose("000001", D)["B"]
        assert b["identity"]["is_new_issuer"] is None
        assert "不可核验" in b["note"]
        assert b["sole_top_n"] == 0 and b["signals"] == []


class TestSegC:
    """C 段：晋级率双口径 + 题材完整性 + 生态评级 + 同状态赔率。"""

    STATS = {
        "layer": "4->5",
        "promo_row": {"layer": "4->5", "total": 100, "promoted": 20, "rate": 20.0,
                      "rate_exchange": 15.0, "divergence": 5.0, "perf_median": 1.1,
                      "win_rate": 55.0, "perf_n": 100, "fail_perf": -2.0},
        "fwd5": {"n": 300, "median": 1.0, "win_rate": 52.0, "avg": 1.5},
        "buy_point": {"limit_price": 12.3, "odds": {"promote_rate": 20.0}},
    }

    def test_full_stats_and_themes(self, monkeypatch):
        themes = pd.DataFrame({
            "date": [date(2026, 9, 24)], "primary_theme": ["证券"], "role": ["LEADER"],
            "confidence": [0.8], "completeness": [75.0], "status": ["完整"],
            "highest_board": [4], "member_count": [12], "top_code": ["000001"],
        })
        monkeypatch.setattr(diagnose_service, "query_df",
                            _frames(market=MARKET_DF, themes=themes))
        c = diagnose_service.diagnose("000001", D, stats=self.STATS)["C"]
        assert c["available"] is True and c["layer"] == "4->5"
        assert c["promotion"]["rate"] == 20.0 and c["promotion"]["rate_exchange"] == 15.0
        assert c["promotion"]["divergence"] == 5.0
        assert c["forward5"]["n"] == 300
        assert c["dragon_env"]["rating"] == "FAVORABLE"
        assert c["dragon_env"]["reasons"][0]["cond"] == "G1 可交易高度扩张"
        assert c["diverge"] is True
        assert c["odds"]["limit_price"] == 12.3
        assert c["themes"][0]["completeness"] == 75.0

    def test_without_stats_degrades_with_note(self, monkeypatch):
        monkeypatch.setattr(diagnose_service, "query_df", _frames())
        c = diagnose_service.diagnose("000001", D)["C"]
        assert c["available"] is False
        assert "未注入" in c["note"] and c["promotion"] is None


class TestRobustness:
    def test_db_failure_never_raises(self, monkeypatch):
        def boom(sql, params=(), conn=None):
            raise RuntimeError("db down")
        monkeypatch.setattr(diagnose_service, "query_df", boom)
        out = diagnose_service.diagnose("000001", D)
        assert set(out) >= {"A", "B", "C", "technical", "fundamental", "market"}
        assert out["A"]["applicable"] is False
        assert out["market"] == {} and out["fundamental"] == {}
