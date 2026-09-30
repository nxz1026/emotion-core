"""快照日期（历史回看）与日历：日期解析、月历网格、跨页透传（不连库）。

守的是这次用户报的那个真问题：「直观层看不到推荐/显示未知」——根因是展示层默认查
`date.today()`（09-28 无快照），而最后快照是 09-24。所以这里必须钉死：
默认日 = 库里最后一个有快照的交易日；非法/无快照的日期一律回落并在页面提示。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from emotion_core.presentation import loaders, server, snapshot

DATES = [date(2026, 9, 24), date(2026, 9, 23), date(2026, 9, 22),
         date(2026, 9, 21), date(2026, 9, 18)]


@pytest.fixture(autouse=True)
def _fake_dates(monkeypatch):
    """把"有快照的日期集合"钉成固定的 5 天（含跨周末），不连库。"""
    snapshot.clear_cache()
    monkeypatch.setattr(snapshot, "query_df",
                        lambda *a, **k: pd.DataFrame({"date": list(DATES)}))
    yield
    snapshot.clear_cache()


def test_available_and_latest():
    assert snapshot.available_dates() == DATES
    assert snapshot.latest_date() == date(2026, 9, 24)


def test_is_trading_day_means_has_snapshot():
    assert snapshot.is_trading_day(date(2026, 9, 22)) is True
    assert snapshot.is_trading_day(date(2026, 9, 27)) is False   # 周日


@pytest.mark.parametrize("raw,expected", [
    (None, date(2026, 9, 24)),            # 不传 → 最新快照
    ("", date(2026, 9, 24)),
    ("2026-09-22", date(2026, 9, 22)),    # 有快照 → 用它
    ("2026-09-27", date(2026, 9, 24)),    # 周日无快照 → 回落最新
    ("2026-09-25", date(2026, 9, 24)),    # 交易日但没跑 → 回落最新
    ("abc", date(2026, 9, 24)),           # 非法 → 回落最新
    ("2026-13-45", date(2026, 9, 24)),
    ("2026-09-22T09:00:00", date(2026, 9, 22)),   # 带时间前缀也认
])
def test_resolve_date(raw, expected):
    assert snapshot.resolve_date(raw) == expected


def test_calendar_grid_shape_and_flags():
    cal = snapshot.calendar_ctx(date(2026, 9, 22), today=date(2026, 9, 27))
    assert cal["month_label"] == "2026 年 9 月"
    assert cal["weekday_labels"] == ("一", "二", "三", "四", "五", "六", "日")
    assert all(len(w) == 7 for w in cal["weeks"])
    cells = {c["iso"]: c for w in cal["weeks"] for c in w}
    assert cells["2026-09-22"]["has_data"] and cells["2026-09-22"]["is_selected"]
    assert cells["2026-09-24"]["is_latest"]
    assert not cells["2026-09-27"]["has_data"]          # 周日
    assert cells["2026-09-27"]["is_today"]              # 今天（无快照）
    assert cells["2026-09-22"]["in_month"]
    assert cal["month_days"] == 5                        # 本月有几个快照日
    assert cal["prev_month"] == "2026-08" and cal["next_month"] == "2026-10"
    assert cal["prev_has_data"] is False                 # 8 月无快照
    assert cal["next_has_data"] is False


def test_calendar_month_param_and_invalid():
    cal = snapshot.calendar_ctx(date(2026, 9, 22), month="2026-08")
    assert cal["month_label"] == "2026 年 8 月"
    assert cal["month_days"] == 0
    assert cal["next_month"] == "2026-09" and cal["next_has_data"] is True
    bad = snapshot.calendar_ctx(date(2026, 9, 22), month="不是月份")
    assert bad["month_label"] == "2026 年 9 月"            # 非法 → 锚点日所在月


def test_banner_shows_latest_vs_history():
    latest = snapshot.banner_ctx(date(2026, 9, 24))
    assert latest["is_latest"] and latest["days_behind"] == 0
    assert latest["next"] is None                         # 已是最新，不能往后翻
    assert latest["prev"] == date(2026, 9, 23)            # 上一交易日更早

    hist = snapshot.banner_ctx(date(2026, 9, 22))
    assert hist["is_latest"] is False
    assert hist["days_behind"] == 2
    assert hist["next"] == date(2026, 9, 23)              # 更晚的快照
    assert hist["weekday"] == "二"
    assert hist["selected_iso"] == "2026-09-22"


def test_banner_without_any_snapshot():
    empty = snapshot.banner_ctx(None)
    assert empty["selected"] is None and empty["is_latest"] is False


def test_loaders_default_to_latest_snapshot_not_today(monkeypatch):
    """核心回归：默认日期必须是最新快照日（曾经是 date.today()）。"""
    seen: list[tuple] = []

    def fake_query(sql, params=(), conn=None):
        seen.append((sql, params))
        return pd.DataFrame([])

    monkeypatch.setattr(loaders, "query_df", fake_query)
    loaders.load_market_snapshot()
    loaders.load_signals()
    loaders.load_ladder()
    assert seen, "应当发起查询"
    for _sql, params in seen:
        assert params[0] == date(2026, 9, 24), params


def test_loaders_accept_explicit_date(monkeypatch):
    seen: list[tuple] = []
    monkeypatch.setattr(loaders, "query_df",
                        lambda sql, params=(), conn=None: (seen.append(params),
                                                           pd.DataFrame([]))[1])
    loaders.load_market_snapshot(date(2026, 9, 22))
    assert seen == [(date(2026, 9, 22),)]


def test_top_ladder_and_counts_queries_are_safe(monkeypatch):
    seen: list[str] = []

    def fake_query(sql, params=(), conn=None):
        seen.append(sql)
        if "count(*)" in sql:
            return pd.DataFrame([{"n": 0}])
        return pd.DataFrame([])

    monkeypatch.setattr(loaders, "query_df", fake_query)
    loaders.load_top_ladder()
    counts = loaders.load_signal_counts()
    assert counts == {"day": 0, "total": 0, "ladder_day": 0, "ladder_total": 0}
    for sql in seen:
        assert "%" not in sql.replace("%s", ""), sql      # 无裸 %
        for verb in ("insert", "update", "delete"):
            assert verb not in sql.lower()


def test_render_dashboard_carries_snapshot_date_everywhere():
    for layer, path in (("intuitive", "/emotion/"), ("logic", "/emotion/logic"),
                        ("algorithm", "/emotion/algorithm"),
                        ("strategy", "/emotion/strategy"), ("stock", "/emotion/stock")):
        html = server.render_dashboard(layer, date="2026-09-22", path=path)
        assert "2026-09-22" in html, layer
        assert "历史回看" in html, layer
        assert "最新快照 2026-09-24" in html, layer
        assert 'class="snapshot-bar"' in html and "snapshot-calendar" in html, layer
        assert f'href="{path}?date=' in html, layer            # 日历链接回到当前页


def test_nav_links_keep_selected_date():
    html = server.render_dashboard("logic", date="2026-09-22", path="/emotion/logic")
    for target in ("/emotion/", "/emotion/logic", "/emotion/algorithm",
                   "/emotion/strategy", "/emotion/stock"):
        assert f'href="{target}?date=2026-09-22"' in html, target


def test_links_survive_nginx_stripped_prefix():
    """线上 nginx `proxy_pass .../` 会剥掉 `/emotion` 前缀，服务端只见 `/`、`/logic`。

    此时日历上下交易日、月份切换与"跳转"表单仍必须指回 `/emotion/...`；
    否则它们会落到门户根路径（另一个服务）而不是当前层。
    """
    html = server.render_dashboard("intuitive", date="2026-09-22", path="/")
    assert 'action="/emotion/"' in html                 # 跳转表单
    assert 'href="/?date=' not in html and 'href="/?month=' not in html

    html = server.render_dashboard("logic", date="2026-09-22", path="/logic")
    assert 'action="/emotion/logic"' in html
    assert 'href="/logic?date=' not in html


def test_invalid_date_falls_back_to_latest_on_page():
    html = server.render_dashboard("intuitive", date="2026-09-27", path="/emotion/")
    assert "2026-09-24（周四）" in html
    assert "最新快照" in html


def test_intuitive_page_opens_calendar_others_collapsed():
    assert "snapshot-calendar" in server.render_dashboard("intuitive", path="/emotion/")
    opened = server.render_dashboard("intuitive", path="/emotion/")
    collapsed = server.render_dashboard("logic", path="/emotion/logic")
    assert '<details class="snapshot-calendar" open>' in opened      # 直观层默认展开
    assert '<details class="snapshot-calendar" open>' not in collapsed


def test_intuitive_page_explains_empty_signals(monkeypatch):
    monkeypatch.setattr(server, "_load_intuitive_data", lambda d=None: {
        "phase": "高潮", "phase_desc": "x", "buy_window": "ENHANCED",
        "force_liquidate": False, "limit_up_count": 49, "max_limit_days": 5,
        "limit_down_count": 13, "ladder_count": 0, "signal_count": 0,
        "dragon_env": "", "dragon_desc": "", "accelerate": False, "accel_reason": "",
        "recommendation": None, "recommendations": [], "top_ladder": [],
        "signal_counts": {"day": 0, "total": 0, "ladder_day": 0, "ladder_total": 0},
        "neg_exp": {"mean": None, "median": None, "n": 0, "hit_rate": None},
        "no_buy_reason": "策略过滤条件较严（五条件 checklist + 生态评级）。"})
    html = server.render_dashboard("intuitive", date="2026-09-24", path="/emotion/")
    assert "没有 BUY 推荐" in html
    assert "signal" in html and "全表" in html and "0 行" in html
    assert "策略过滤" in html or "五条件" in html or "生态评级" in html  # 策略解释替代 DB 心跳
    assert "当日最高板梯队" in html
