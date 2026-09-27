"""C7 口径守护：北交所不参与情绪判定（数据层保留）。

裁决（docs/13 §S2 C7）：北交所（6 位纯代码 4/83/87/88/920 开头，或 akshare 的
bj 前缀）只留在 `daily_bar` / `stock_basic`，**不进判据层**——涨停价、连板、家数
一律按沪深算。此前这条只靠「北交所没有日线源」隐式成立：一旦补上北交所源，
这些行会静默混进 zt/zb/跌停计数（家数口径是全市场，唯一挡它的就是本条件）。

四层证据：
1. `derive._DERIVE_SQL` 渲染后逐个板块前缀排除（config 驱动，非硬编码）；
2. `CONFIG.BOARD_PREFIXES`（最高板/梯队口径）里没有任何北交所前缀；
3. `emotion._counts` 的最高板限主板、家数读 derived_bar（已剔北交所）；
4. `utils/price.board_pct_milli` 对北交所给 30%（300 千分比），不是主板的 10%。
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from emotion_core.algorithms import derive, emotion
from emotion_core.utils.config import CONFIG
from emotion_core.utils.price import board_pct_milli

_BSE_CODES = ["430047", "830799", "871981", "920001", "bj430047"]


def _rendered_derive_sql() -> str:
    return derive.render_sql()


def test_derive_sql_excludes_every_bse_prefix():
    sql = _rendered_derive_sql()
    assert CONFIG.BSE_EXCLUDED_PREFIXES, "北交所前缀表不得为空"
    for p in CONFIG.BSE_EXCLUDED_PREFIXES:
        assert f"left(s.code, {len(p)}) <> '{p}'" in sql, f"缺北交所前缀排除 {p}"
    for code in _BSE_CODES:
        assert code.startswith(tuple(CONFIG.BSE_EXCLUDED_PREFIXES)), (
            f"{code} 未被子串排除覆盖")


def test_derive_sql_carries_no_bare_percent():
    """语句直接进 psycopg.execute()：裸 `%`（如 LIKE '4%'）会被当占位符 →
    ProgrammingError: only '%s','%b','%t' are allowed as placeholders。
    允许出现的只有参数占位 `%s`。"""
    sql = derive.render_sql()
    assert sql.replace("%s", "").count("%") == 0, "除 %s 外不得出现裸百分号"
    assert "LIKE" not in sql
    assert sql.count("%s") == 2, "仅 b.date BETWEEN %s AND %s 两个参数"


def test_board_prefixes_are_mainboard_only():
    for p in ("4", "8", "92", "bj"):
        assert not any(x.startswith(p) for x in CONFIG.BOARD_PREFIXES), (
            f"最高板/梯队口径混入北交所前缀 {p}")
    assert len(CONFIG.BOARD_PREFIXES) == 7


def test_counts_maxh_limited_to_mainboard_and_reads_derived_bar(monkeypatch):
    seen: dict[str, object] = {}

    def fake_query(sql: str, params=()):
        seen["sql"] = sql
        seen["params"] = tuple(params)
        return pd.DataFrame([{"zt": 0, "zb": 0, "touched": 0, "dt": 0,
                              "maxh": None}])

    monkeypatch.setattr(emotion, "query_df", fake_query)
    emotion._counts(date(2026, 9, 25))
    sql, params = str(seen["sql"]), seen["params"]
    assert "FROM derived_bar" in sql
    assert "left(d.code,3) IN" in sql, "最高板必须限主板前缀"
    assert "max(d.cont_days)" in sql
    for p in CONFIG.BOARD_PREFIXES:
        assert p in params, f"主板前缀 {p} 未进参数"
    assert "first_bar_date" in sql, "家数口径必须带次新过滤（_NEW_ISSUER_FILTER）"


def test_price_impl_gives_bse_30pct():
    for code in _BSE_CODES:
        assert board_pct_milli(code) == 300, f"{code} 北交所应为 30%"
    assert board_pct_milli("600000") == 100
    assert board_pct_milli("300001") == 200
    assert board_pct_milli("688001") == 200
