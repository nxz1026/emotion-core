"""个股诊断 SQL 层（data/stock_query.py）：无库结构守卫 + 口径对齐断言。

本文件**不连数据库**（与仓库测试基线一致），靠 monkeypatch `query_df` 抓取
真实 SQL/参数来断言，守三件曾经真踩过的事：
1. psycopg 参数化语句里的裸 `%`（`LIKE '4%'` 会直接报占位符错误）；
2. 晋级率口径必须与 `algorithms/promotion.py` 同源（层标签、主板池、剔次新、
   分子用 `>= cont+1`、换手口径分母同为"昨日该层只数"）；
3. PIT：所有统计都带 `date < 目标日` 的上界，且分母不足门槛时给 None 而不是补零。
"""
from __future__ import annotations

import re
from datetime import date

import pandas as pd
import pytest

from emotion_core.algorithms import promotion
from emotion_core.data import stock_query as q
from emotion_core.utils.config import CONFIG

D = date(2026, 9, 24)


class _Capture:
    """记录每次查询的 SQL 与参数。"""

    def __init__(self, frames):
        self.frames = frames
        self.calls: list[tuple[str, tuple]] = []

    def __call__(self, sql, params=(), conn=None):
        self.calls.append((sql, params))
        return self.frames.pop(0) if self.frames else pd.DataFrame()


def _bare_percent(sql: str) -> bool:
    """SQL 里是否存在非占位符的 `%`（`%s`/`%b`/`%t` 之外）。"""
    return bool(re.search(r"%(?![sbt])", sql))


def test_layer_labels_match_promotion_layers():
    """页面层级标签必须与算法层同源，否则统计口径会分叉。"""
    assert q.LAYER_LABELS == tuple(name for name, _, _ in promotion.PROMOTION_LAYERS)


def test_board_prefixes_single_source():
    """主板池不得在 data 层另抄一份（promotion.py 与 CONFIG 必须一致）。"""
    assert tuple(CONFIG.BOARD_PREFIXES) == tuple(promotion.BOARD_PREFIXES)


def test_resolve_code_puts_wildcards_in_params_not_sql(monkeypatch):
    cap = _Capture([pd.DataFrame([{"code": "601811", "name": "新华文轩"}])])
    monkeypatch.setattr(q, "query_df", cap)
    rows = q.resolve_code("新华")
    sql, params = cap.calls[0]
    assert rows and rows[0]["code"] == "601811"
    assert not _bare_percent(sql), f"SQL 里有裸 %：{sql}"
    assert params[0] == "%新华%"          # 通配符只在参数里
    assert "601811" not in sql            # 用户输入不得拼进 SQL


def test_resolve_code_digits_uses_prefix_param(monkeypatch):
    cap = _Capture([pd.DataFrame([{"code": "601811"}])])
    monkeypatch.setattr(q, "query_df", cap)
    q.resolve_code("6018")
    sql, params = cap.calls[0]
    assert not _bare_percent(sql)
    assert params[0] == "6018" and params[1] == "6018%"


def test_promotion_table_sql_caliber_and_pit(monkeypatch):
    cap = _Capture([pd.DataFrame([{"layer": "1->2", "total": 30, "promoted": 6,
                                   "promoted_ex": 5, "perf_n": 30, "perf_avg": 1.0,
                                   "perf_median": 0.5, "perf_win_n": 18,
                                   "fail_n": 24, "fail_perf": -1.0}])])
    monkeypatch.setattr(q, "query_df", cap)
    rows = q.promotion_table(D)
    sql, params = cap.calls[0]
    assert not _bare_percent(sql)
    assert ">= v.cont_days + 1" in sql or ">= y.cont_days + 1" in sql  # 分子用 >=，非 =
    assert "left(v.code, 3) = ANY(%s)" in sql                          # 主板池
    assert "first_bar_date <= GREATEST" in sql                          # 剔次新
    assert "v.date < %s" in sql                                        # PIT 上界
    assert D in params and CONFIG.NEW_ISSUER_FLOOR in params
    assert list(CONFIG.BOARD_PREFIXES) in params
    # 全层返回：只给了一行也要补齐 5 层（无样本层给 0，不能吞层）
    assert [r["layer"] for r in rows] == list(q.LAYER_LABELS)
    row = rows[0]
    assert row["total"] == 30 and row["promoted"] == 6
    assert row["rate"] == 20.0 and row["rate_ratio"] == 0.2
    # 换手口径分母同为"昨日该层只数"（与 promotion._layer_row 一致）
    assert row["rate_exchange"] == round(100.0 * 5 / 30, 1)
    assert row["divergence"] == round(100.0 * (6 - 5) / 30, 1)
    assert rows[4]["total"] == 0 and rows[4]["rate"] is None


def test_promotion_table_returns_none_below_min_denom(monkeypatch):
    cap = _Capture([pd.DataFrame([{"layer": "1->2", "total": 2, "promoted": 1,
                                   "promoted_ex": 1, "perf_n": 2, "perf_avg": 1.0,
                                   "perf_median": 1.0, "perf_win_n": 1,
                                   "fail_n": 1, "fail_perf": -2.0}])])
    monkeypatch.setattr(q, "query_df", cap)
    row = q.promotion_table(D, min_denom=CONFIG.PROMOTION_MIN_DENOM)[0]
    assert row["total"] == 2
    assert row["rate"] is None and row["rate_exchange"] is None  # F6：不猜、不补零
    assert row["perf_median"] is not None                        # 收益统计仍如实给出


def test_promotion_table_pair_date_is_passed_for_single_day_calibration(monkeypatch):
    cap = _Capture([pd.DataFrame([])])
    monkeypatch.setattr(q, "query_df", cap)
    q.promotion_table(D, pair_date=D)
    _sql, params = cap.calls[0]
    assert D in params


def test_layer_forward_uses_lateral_and_pit(monkeypatch):
    cap = _Capture([pd.DataFrame([{"ret": 1.0}, {"ret": -3.0}, {"ret": 5.0},
                                  {"ret": None}])])
    monkeypatch.setattr(q, "query_df", cap)
    out = q.layer_forward(D, "2->3", 5)
    sql = cap.calls[0][0]
    assert not _bare_percent(sql)
    assert "LEFT JOIN LATERAL" in sql and "OFFSET %s" in sql
    assert "v.date < %s" in sql
    assert out["n"] == 3 and out["median"] == 1.0 and out["win_rate"] == 66.7
    assert out["layer"] == "2->3" and out["horizon"] == 5


def test_layer_forward_empty_sample_is_honest(monkeypatch):
    cap = _Capture([pd.DataFrame([])])
    monkeypatch.setattr(q, "query_df", cap)
    out = q.layer_forward(D, "3->4", 1)
    assert out["n"] == 0 and out["median"] is None and out["win_rate"] is None


def test_numeric_queries_guard_pg_nan(monkeypatch):
    """PG 的 numeric NaN 大于一切非 NaN，涉及价格的除法必须显式排除。"""
    cap = _Capture([pd.DataFrame([]) for _ in range(4)])
    monkeypatch.setattr(q, "query_df", cap)
    q.recent_series("601811", D, 30)
    q.structure_window("601811", D, 60)
    q.layer_forward(D, "1->2", 5)
    q.promotion_table(D)
    priced = [sql for sql, _ in cap.calls if "close" in sql or "pre_close" in sql]
    assert priced, "应有涉及价格列的口径查询"
    for sql in priced:
        assert "<> 'NaN'::numeric" in sql, sql


def test_bare_percent_check_helper():
    assert q.bare_percent_check("WHERE code LIKE %s")["bare_percent"] is False
    assert q.bare_percent_check("WHERE code LIKE '4%'")["bare_percent"] is True


@pytest.mark.parametrize("func", ["latest_trade_date", "basic_row", "market_env",
                                  "market_top", "themes_of"])
def test_readonly_queries_are_select_only(func, monkeypatch):
    """整条链路只读：不得出现任何写语句。"""
    cap = _Capture([pd.DataFrame([])])
    monkeypatch.setattr(q, "query_df", cap)
    args = {"latest_trade_date": (), "basic_row": ("601811",),
            "market_env": (D,), "market_top": (D,), "themes_of": ("601811", D)}[func]
    getattr(q, func)(*args)
    sql = cap.calls[0][0].lower()
    for verb in ("insert", "update", "delete", "drop", "alter"):
        assert verb not in sql
