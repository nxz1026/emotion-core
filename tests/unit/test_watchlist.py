"""P3-3 自选股 watchlist 测试：CLI 语义 + 状态行 + 报告段（不连库）。

fmt_status 是纯函数（核心判定逻辑）；status_rows 桩化 db；报告段
_sec_watchlist 桩化 service，验证空清单不占位。
"""
from datetime import date

import pandas as pd

from emotion_core.algorithms import review
from emotion_core.services import watchlist

D = date(2026, 9, 4)


# ---------------- fmt_status 状态判定（纯函数核心） ----------------

def _row(is_limit_up=False, touched=False, bomb=False, is_limit_down=False,
         cont_days=0, is_exchange=True, pct=None):
    return {"code": "605577", "name": "龙版传媒", "note": "",
            "date": D, "is_limit_up": is_limit_up,
            "touched_limit": touched, "is_bomb": bomb,
            "is_limit_down": is_limit_down, "cont_days": cont_days,
            "is_exchange": is_exchange, "pct": pct}


def test_fmt_exchange_multi_board():
    s = watchlist.fmt_status(_row(is_limit_up=True, cont_days=5, pct=9.97))
    assert "换手5板" in s and "+9.97%" in s


def test_fmt_one_word_board():
    s = watchlist.fmt_status(_row(is_limit_up=True, cont_days=2,
                                  is_exchange=False, pct=9.99))
    assert "一字2板" in s


def test_fmt_first_board_exchange():
    s = watchlist.fmt_status(_row(is_limit_up=True, cont_days=1, pct=10.0))
    assert "涨停(首板)" in s


def test_fmt_bomb():
    s = watchlist.fmt_status(_row(touched=True, pct=3.2))
    assert "炸板" in s and "+3.20%" in s


def test_fmt_limit_down():
    s = watchlist.fmt_status(_row(is_limit_down=True, pct=-9.9))
    assert "跌停" in s and "-9.90%" in s


def test_fmt_flat_no_market():
    s = watchlist.fmt_status(_row())
    assert "无行情" in s


# ---------------- status_rows：当日状态聚合（桩 db） ----------------

def test_status_rows_joins_today(monkeypatch):
    """LEFT JOIN derived_bar+daily_bar → 单行含封板/炸板/涨跌幅。"""
    got = {}

    def fake_query(sql, p=None):
        got["sql"] = sql
        got["params"] = p
        return pd.DataFrame([{"code": "605577", "name": "龙版传媒",
                              "note": "", "is_limit_up": True,
                              "touched_limit": True, "is_bomb": False,
                              "is_limit_down": False, "cont_days": 5,
                              "is_exchange": True, "close": 15.0,
                              "pre_close": 13.64}])
    monkeypatch.setattr(watchlist, "query_df", fake_query)
    rows = watchlist.status_rows(D)
    assert got["params"] == (D, D)          # 两 JOIN 共用同日
    assert len(rows) == 1 and rows[0]["is_limit_up"] and rows[0]["cont_days"] == 5
    assert rows[0]["pct"] == round((15.0 / 13.64 - 1) * 100, 2)


def test_status_rows_pct_none_no_preclose(monkeypatch):
    """无 pre_close（停牌/除权首日）→ pct None，不除零。"""
    monkeypatch.setattr(watchlist, "query_df",
                        lambda sql, p=None: pd.DataFrame(
                            [{"code": "605577", "name": "X", "note": "",
                              "is_limit_up": False, "touched_limit": False,
                              "is_bomb": False, "is_limit_down": False,
                              "cont_days": 0, "is_exchange": False,
                              "close": 10.0, "pre_close": None}]))
    rows = watchlist.status_rows(D)
    assert rows[0]["pct"] is None


def test_status_rows_empty_db(monkeypatch):
    """无任何行情日 → 空列表。"""
    monkeypatch.setattr(watchlist, "query_df",
                        lambda sql, p=None: pd.DataFrame())
    assert watchlist.status_rows() == []


# ---------------- 报告段：空清单不占位 ----------------

def test_sec_watchlist_empty_when_no_rows(monkeypatch):
    monkeypatch.setattr(watchlist, "status_rows", lambda d=None: [])
    d = {"date": D}
    assert review._sec_watchlist(d) == ""


def test_sec_watchlist_renders_rows(monkeypatch):
    monkeypatch.setattr(watchlist, "status_rows",
                        lambda d=None: [_row(is_limit_up=True, cont_days=5,
                                             pct=9.97)])
    d = {"date": D}
    out = review._sec_watchlist(d)
    assert "自选股当日状态" in out and "换手5板" in out
