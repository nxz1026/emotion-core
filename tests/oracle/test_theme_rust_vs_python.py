"""对账测试：theme Rust vs Python 同输入同输出，逐字段比对。

覆盖 8 个纯逻辑函数 + 1 个数据结构：
- is_style(tag) — STYLE_ONLY 三重判据（黑名单 / 前缀 / 包含）
- theme_filter(tags) — 噪声剔除（保序）
- row_themes(row) — 题材展开口径（工单形状 / lkl 原生形状 / NULL 传播）
- group_stats_of(members) — 梯队聚合公式（含空输入 ValueError）
- group_stats(codes, cont_days) — 注入分支（JOIN 丢行，None ↔ {}）
- aggregate(theme_tags, cont_days_map) — 题材展开聚合（键序 = 首次出现序）
- false_relation(code, themes) — 单票伪关联
- false_relation_pairs(rows) — 成对判例

Python 参考：src/emotion_core/algorithms/theme.py。
SQL 取数不在对账范围：aggregate 的 ladder_day 补读由 cont_days_map 注入
（等价 _fetch_cont_days 返回）；Python 侧用 monkeypatch query_df 造假返回对账。
日期两侧同传 datetime.date 或 ISO 字符串（Rust 侧 str() 归一）。
"""
from __future__ import annotations

import sys
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, "src")
from emotion_core.algorithms import theme  # noqa: E402
from emotion_core.core import emotion_core_rust as rust  # noqa: E402

D = date(2026, 9, 24)
D2 = date(2026, 9, 23)

STATS_FIELDS = ("highest_board", "top_code", "mid_count", "low_count",
                "first_board_count", "completeness", "status", "member_count")


def _assert_stats_equal(rust_stats, py_stats, ctx=""):
    """GroupStats（Rust pyclass）与 Python dict/GroupStats 逐字段比对。"""
    for f in STATS_FIELDS:
        got = getattr(rust_stats, f)
        want = py_stats[f] if isinstance(py_stats, dict) else getattr(py_stats, f)
        assert got == want, f"{ctx} field {f}: rust={got!r} py={want!r}"


def _assert_agg_equal(rust_dict, py_dict, ctx=""):
    """aggregate 结果比对：键集合、键序、每个题材统计逐字段。"""
    assert list(rust_dict.keys()) == list(py_dict.keys()), (
        f"{ctx} keys/order: rust={list(rust_dict.keys())} py={list(py_dict.keys())}")
    for k in py_dict:
        _assert_stats_equal(rust_dict[k], py_dict[k], ctx=f"{ctx}[{k}]")


def _patch_query_df(monkeypatch, cont_map):
    """monkeypatch theme.query_df：按 (date, code) 映射造假返回（只含命中对）。"""
    def fake_query_df(sql, params=()):
        dates, codes = params
        rows = [{"date": d, "code": c, "cont_days": cont_map[(d, c)]}
                for d in dates for c in codes if (d, c) in cont_map]
        return pd.DataFrame(rows)

    monkeypatch.setattr(theme, "query_df", fake_query_df)


# ---------- is_style ----------

@pytest.mark.parametrize("tag", sorted(theme.THEME_STYLE_BLACKLIST))
def test_is_style_blacklist(tag):
    assert rust.is_style(tag) is True
    assert rust.is_style(tag) == theme.is_style(tag)


@pytest.mark.parametrize("tag", [
    "全A股",          # 前缀（黑名单亦收）
    "标普500",        # 前缀
    "富时罗素",       # 前缀
    "纳入MSCI",       # 前缀（MSCI 在开头）
    "上证50",         # 前缀
    "深证成指",       # 前缀
    "股权激励计划",     # 前缀
    "财报披露",       # 前缀
    "首板",           # 前缀（黑名单亦收）
    "赛道全A股",      # 前缀锚定开头，非包含匹配 → 不判噪声（lkl 同）
    "机器人",
    "固态电池",
    "半导体设备",
    "减速器",
    "海鸥住工",
])
def test_is_style_behavior(tag):
    assert rust.is_style(tag) == theme.is_style(tag)


@pytest.mark.parametrize("tag", [
    "基金重仓股",      # 包含：重仓
    "沪深300指数",     # 包含：指数
    "小盘成长",        # 包含：小盘
    "大盘蓝筹",        # 包含：大盘
    "中盘",           # 包含：中盘
    "等权",           # 包含：等权
    "国资改革",        # 包含：国资
    "综合",           # 包含：综合
    "标的",           # 包含：标的
    "微盘",           # 包含：微盘
    "点位贡献",        # 包含：点位贡献
])
def test_is_style_contains(tag):
    assert rust.is_style(tag) is True
    assert rust.is_style(tag) == theme.is_style(tag)


# ---------- theme_filter ----------

@pytest.mark.parametrize("tags,expected", [
    (["机器人", "昨日涨停", "基金重仓股", "固态电池", "上证50"], ["机器人", "固态电池"]),
    (["昨日涨停", "打板"], []),
    ([], []),
    (["机器人", "固态电池"], ["机器人", "固态电池"]),    # 全保留且保序
    (["固态电池", "机器人"], ["固态电池", "机器人"]),    # 顺序跟随输入
    (["机器人", "微盘股", "机器人"], ["机器人", "机器人"]),  # 重复保留
])
def test_theme_filter(tags, expected):
    got = rust.theme_filter(tags)
    assert got == expected
    assert got == theme.theme_filter(tags)


# ---------- row_themes（Python 侧 _row_themes） ----------

@pytest.mark.parametrize("row,expected", [
    # 工单形状：单标签直接成表
    ({"code": "600519", "theme_name": "机器人", "tag_date": D}, ["机器人"]),
    ({"code": "600519", "theme_name": "固态电池", "tag_date": D, "cont_days": 3}, ["固态电池"]),
    # 工单形状优先于原生形状（同 row 带 primary/secondary 也走单标签）
    ({"code": "600519", "theme_name": "机器人", "primary_theme": "减速器",
      "secondary_themes": ["减速器"], "tag_date": D}, ["机器人"]),
    # 工单形状值为 None / "" → 空列表
    ({"code": "600519", "theme_name": None, "tag_date": D}, []),
    ({"code": "600519", "theme_name": "", "tag_date": D}, []),
    # lkl 原生形状：主名已在次名列表内 → 原样（不重复展开）
    ({"code": "600519", "primary_theme": "机器人",
      "secondary_themes": ["机器人", "减速器"], "tag_date": D}, ["机器人", "减速器"]),
    # 主名不在次名列表 → 追加在末位
    ({"code": "600519", "primary_theme": "机器人",
      "secondary_themes": ["减速器", "固态电池"], "tag_date": D}, ["减速器", "固态电池", "机器人"]),
    # NULL 传播：主名或次名列表任一为 NULL/缺失 → 空列表
    ({"code": "600519", "primary_theme": None,
      "secondary_themes": ["机器人"], "tag_date": D}, []),
    ({"code": "600519", "primary_theme": "机器人",
      "secondary_themes": None, "tag_date": D}, []),
    ({"code": "600519", "primary_theme": "机器人", "tag_date": D}, []),
    ({"code": "600519", "secondary_themes": ["机器人"], "tag_date": D}, []),
    # 次名列表含重复 → 原样保留（lkl unnest 口径，一股可重复计入）
    ({"code": "600519", "primary_theme": "机器人",
      "secondary_themes": ["机器人", "机器人"], "tag_date": D}, ["机器人", "机器人"]),
])
def test_row_themes(row, expected):
    got = rust.row_themes(row)
    assert got == expected
    assert got == theme._row_themes(row)


def test_row_themes_ticket_shape_with_string_date():
    row = {"code": "600519", "theme_name": "机器人", "tag_date": "2026-09-24"}
    assert rust.row_themes(row) == theme._row_themes(row) == ["机器人"]


# ---------- group_stats_of ----------

@pytest.mark.parametrize("members", [
    [("600519", 6), ("000001", 3), ("002594", 2)],          # 梯队完整
    [("600519", 5), ("002594", 2)],                          # 孤高
    [("600510", 2), ("600511", 2), ("600512", 2),
     ("600513", 2), ("600514", 2)],                          # 低位扩散（5 成员 h=2）
    [("600519", 3), ("000001", 2)],                          # 一般（h=3）
    [("600519", 12)],                                        # 高度封顶 8 + 孤高
    [("600519", 1)],                                         # 单成员 h=1
    [("600519", 2)],                                         # 单成员 h=2
    [("600519", 4), ("000001", 3), ("002594", 2), ("300750", 2)],  # 断层率 0（2/3/4 齐）
    [("600519", 5), ("000001", 3), ("002594", 2)],           # 断层：4 缺 → rate 1/3
    [("600519", 4), ("002594", 2)],                          # 断层：3 缺 → rate 1/2
    [("000001", 5), ("600519", 5)],                          # 并列最高板 → 行序首个
    [("600519", 5), ("000001", 5)],                          # 并列最高板 → 行序首个（反向）
    [("600519", 8), ("000001", 7), ("002594", 6),
     ("300750", 5), ("601318", 4), ("002001", 3)],            # 6 成员满配
    [("600519", 4), ("000001", 4), ("002594", 2),
     ("300750", 2), ("601318", 3)],                          # h=4 有 mid 无 low → 一般
])
def test_group_stats_of(members):
    got = rust.group_stats_of(members)
    want = theme.group_stats_of(members)
    _assert_stats_equal(got, want, ctx=f"members={members}")


def test_group_stats_of_empty_raises():
    """空输入：Python 抛 ValueError（max() 空序列），Rust 同。"""
    with pytest.raises(ValueError):
        theme.group_stats_of([])
    with pytest.raises(ValueError):
        rust.group_stats_of([])


# ---------- group_stats（注入分支） ----------

@pytest.mark.parametrize("codes,cont_days,expected_members", [
    (["600519", "000001", "002594"],
     {"600519": 6, "000001": 3, "002594": 2}, [("600519", 6), ("000001", 3), ("002594", 2)]),
    (["600519", "999999"], {"600519": 5}, [("600519", 5)]),          # 无梯队行 → 丢
    (["999999"], {"600519": 5}, []),                                  # 全丢 → {}
    ([], {}, []),                                                     # 空代码集 → {}
    (["600519", "000001", "002594"], {}, []),                        # 映射空 → 全丢
    (["600519", "000001"], {"600519": 5, "000001": 5},               # 并列取行序首个
     [("600519", 5), ("000001", 5)]),
    (["000001", "600519"], {"600519": 5, "000001": 5},               # 并列取行序首个（反向）
     [("000001", 5), ("600519", 5)]),
])
def test_group_stats(codes, cont_days, expected_members):
    py = theme.group_stats(codes, D, cont_days=dict(cont_days))
    got = rust.group_stats(codes, dict(cont_days))
    if not expected_members:
        assert py == {}
        assert got is None
    else:
        assert py == theme.group_stats_of(expected_members)
        _assert_stats_equal(got, py, ctx=f"codes={codes}")


# ---------- aggregate ----------

def test_aggregate_ticket_shape_rows():
    rows = [
        {"code": "600519", "theme_name": "机器人", "tag_date": D, "cont_days": 6},
        {"code": "000001", "theme_name": "机器人", "tag_date": D, "cont_days": 3},
        {"code": "002594", "theme_name": "机器人", "tag_date": D, "cont_days": 2},
        {"code": "600519", "theme_name": "固态电池", "tag_date": D, "cont_days": 6},
        {"code": "300750", "theme_name": "昨日涨停", "tag_date": D, "cont_days": 5},
    ]
    _assert_agg_equal(rust.aggregate(rows), theme.aggregate(rows), ctx="ticket")


def test_aggregate_lkl_shape_rows():
    rows = [
        {"code": "600519", "primary_theme": "机器人",
         "secondary_themes": ["机器人", "减速器"], "tag_date": D, "cont_days": 3},
        {"code": "000001", "primary_theme": "机器人",
         "secondary_themes": ["减速器"], "tag_date": D, "cont_days": 2},
    ]
    _assert_agg_equal(rust.aggregate(rows), theme.aggregate(rows), ctx="lkl-shape")


def test_aggregate_primary_appended_when_absent():
    rows = [{"code": "600519", "primary_theme": "机器人",
             "secondary_themes": ["昨日涨停", "减速器"], "tag_date": D, "cont_days": 3}]
    _assert_agg_equal(rust.aggregate(rows), theme.aggregate(rows), ctx="primary-append")


def test_aggregate_null_primary_or_secondary_is_empty():
    assert rust.aggregate([{"code": "600519", "primary_theme": None,
                            "secondary_themes": ["机器人"],
                            "tag_date": D, "cont_days": 3}]) == {}
    assert rust.aggregate([{"code": "600519", "primary_theme": "机器人",
                            "secondary_themes": None,
                            "tag_date": D, "cont_days": 3}]) == {}
    assert theme.aggregate([{"code": "600519", "primary_theme": None,
                             "secondary_themes": ["机器人"],
                             "tag_date": D, "cont_days": 3}]) == {}
    assert theme.aggregate([{"code": "600519", "primary_theme": "机器人",
                             "secondary_themes": None,
                             "tag_date": D, "cont_days": 3}]) == {}


def test_aggregate_empty_input():
    assert rust.aggregate([]) == {}
    assert theme.aggregate([]) == {}


def test_aggregate_all_noise_is_empty():
    rows = [{"code": "600519", "theme_name": "昨日涨停", "tag_date": D, "cont_days": 5}]
    assert rust.aggregate(rows) == {}
    assert theme.aggregate(rows) == {}


def test_aggregate_missing_code_or_date_rows_dropped(monkeypatch):
    _patch_query_df(monkeypatch, {(D, "600519"): 2})
    rows = [
        {"code": "600519", "theme_name": "机器人", "tag_date": D},   # 走补读
        {"code": "999999", "theme_name": "机器人", "tag_date": D},   # 梯队无行 → 丢
        {"code": "000001", "theme_name": "机器人", "cont_days": 3},  # 无日期 → 丢
        {"theme_name": "机器人", "tag_date": D, "cont_days": 3},     # 无代码 → 丢
        {"code": "", "theme_name": "机器人", "tag_date": D, "cont_days": 3},  # 空代码 → 丢
    ]
    _assert_agg_equal(rust.aggregate(rows, {("2026-09-24", "600519"): 2}),
                      theme.aggregate(rows), ctx="drop-rows")


def test_aggregate_injected_lookup(monkeypatch):
    """缺 cont_days 的行按 (tag_date, code) 补读：Rust 注入映射 vs Python 假 query_df。"""
    cont_map = {(D, "600519"): 6, (D, "000001"): 3}
    _patch_query_df(monkeypatch, cont_map)
    rows = [
        {"code": "600519", "theme_name": "机器人", "tag_date": D},   # 补读 → 6
        {"code": "000001", "theme_name": "机器人", "tag_date": D},   # 补读 → 3
        {"code": "999999", "theme_name": "机器人", "tag_date": D},   # 无梯队行 → 丢
        {"code": "002594", "theme_name": "固态电池", "tag_date": D, "cont_days": 2},  # 自带
        {"code": "300750", "theme_name": "固态电池", "tag_date": D,
         "cont_days": None},                                         # 显式 None → 补读（无行 → 丢）
    ]
    rust_map = {("2026-09-24", "600519"): 6, ("2026-09-24", "000001"): 3}
    _assert_agg_equal(rust.aggregate(rows, rust_map), theme.aggregate(rows), ctx="injected")


def test_aggregate_injected_lookup_multi_date(monkeypatch):
    """多日期混合：部分行自带 cont_days，部分按日补读。"""
    cont_map = {(D, "600519"): 6, (D2, "000001"): 2}
    _patch_query_df(monkeypatch, cont_map)
    rows = [
        {"code": "600519", "theme_name": "机器人", "tag_date": D},        # 补读 → 6
        {"code": "000001", "theme_name": "机器人", "tag_date": D2},       # 补读 → 2
        {"code": "002594", "theme_name": "机器人", "tag_date": D2, "cont_days": 4},  # 自带
    ]
    rust_map = {("2026-09-24", "600519"): 6, ("2026-09-23", "000001"): 2}
    _assert_agg_equal(rust.aggregate(rows, rust_map), theme.aggregate(rows), ctx="multi-date")


def test_aggregate_no_lookup_available_drops(monkeypatch):
    """缺 cont_days 且无注入映射（Python 侧空 ladder_day）→ 全部丢行。"""
    _patch_query_df(monkeypatch, {})  # 空映射：查不到任何行
    rows = [
        {"code": "600519", "theme_name": "机器人", "tag_date": D},   # 补读无行 → 丢
        {"code": "002594", "theme_name": "机器人", "tag_date": D, "cont_days": 2},
    ]
    _assert_agg_equal(rust.aggregate(rows), theme.aggregate(rows), ctx="no-lookup")
    # Rust 侧不传映射（None）等价
    _assert_agg_equal(rust.aggregate(rows, None), theme.aggregate(rows), ctx="no-lookup-none")


def test_aggregate_string_dates(monkeypatch):
    """tag_date 传 ISO 字符串（Rust 侧 str() 归一，与 date 对象同语义）。"""
    cont_map = {("2026-09-24", "600519"): 6, ("2026-09-24", "000001"): 3}
    _patch_query_df(monkeypatch, cont_map)
    rows = [
        {"code": "600519", "theme_name": "机器人", "tag_date": "2026-09-24"},
        {"code": "000001", "theme_name": "机器人", "tag_date": "2026-09-24"},
    ]
    rust_map = {("2026-09-24", "600519"): 6, ("2026-09-24", "000001"): 3}
    _assert_agg_equal(rust.aggregate(rows, rust_map),
                      rust.aggregate([{**r, "tag_date": D} for r in rows], rust_map),
                      ctx="string-dates-rust")
    _assert_agg_equal(rust.aggregate(rows, rust_map),
                      theme.aggregate(rows), ctx="string-dates-vs-py")


def test_aggregate_duplicate_theme_double_count():
    """次名列表含重复主名 → 原样展开，该股在组内重复计入（lkl unnest 口径）。"""
    rows = [{"code": "600519", "primary_theme": "机器人",
             "secondary_themes": ["机器人", "机器人"], "tag_date": D, "cont_days": 3}]
    _assert_agg_equal(rust.aggregate(rows), theme.aggregate(rows), ctx="dup-theme")


def test_aggregate_key_order_is_first_appearance():
    """键序 = 题材在展开口径下的首次出现序（Python dict 插入序）。"""
    rows = [
        {"code": "600519", "theme_name": "固态电池", "tag_date": D, "cont_days": 6},
        {"code": "000001", "theme_name": "机器人", "tag_date": D, "cont_days": 3},
        {"code": "002594", "theme_name": "固态电池", "tag_date": D, "cont_days": 2},
    ]
    got = rust.aggregate(rows)
    want = theme.aggregate(rows)
    assert list(got.keys()) == list(want.keys()) == ["固态电池", "机器人"]
    _assert_agg_equal(got, want, ctx="key-order")


# ---------- false_relation ----------

@pytest.mark.parametrize("tags,expected", [
    (["机器人", "昨日涨停"], False),
    (["昨日涨停", "基金重仓股"], True),
    ([], True),
    (["机器人"], False),
    (["微盘股"], True),                       # 单噪声标签 → 伪
    (["机器人", "固态电池"], False),
])
def test_false_relation(tags, expected):
    got = rust.false_relation("600519", tags)
    assert got is expected
    assert got == theme.false_relation("600519", tags)


# ---------- false_relation_pairs ----------

@pytest.mark.parametrize("rows,expected", [
    # 名称前 2 字相同 + 有效题材交集为空 → 判例
    ([("002084", "海鸥住工", ["家居", "卫浴"]),
      ("603269", "海鸥股份", ["核电", "冷却塔"]),
      ("000001", "平安银行", ["银行"])],
     [("海鸥住工(002084)", "海鸥股份(603269)")]),
    # 共享有效题材 → 不判
    ([("002084", "海鸥住工", ["机器人"]),
      ("603269", "海鸥股份", ["机器人", "核电"])], []),
    # 噪声标签不算交集（theme_filter 后再比）
    ([("002084", "海鸥住工", ["昨日涨停"]),
      ("603269", "海鸥股份", ["昨日涨停"])],
     [("海鸥住工(002084)", "海鸥股份(603269)")]),
    # 名称前 2 字不同 → 不比较
    ([("600519", "贵州茅台", []), ("000001", "平安银行", [])], []),
    ([("600519", "贵州茅台", ["白酒"]), ("000001", "平保银行", ["保险"])], []),
    # 单行 → 无对
    ([("002084", "海鸥住工", ["家居"])], []),
    # 空输入 → 无对
    ([], []),
    # 1 字名称：n[:2] 即整名（字符口径，非字节）
    ([("000001", "平", ["银行"]), ("000002", "平", ["保险"])],
     [("平(000001)", "平(000002)")]),
    # 空名称：""[:2] == ""[:2] → 前 2 字相同
    ([("000001", "", ["银行"]), ("000002", "", ["保险"])],
     [("(000001)", "(000002)")]),
    # 3 行多对：A-B 判、B-C 判、A-C 共享题材（家居）不判
    ([("000001", "海鸥住工", ["家居"]),
      ("000002", "海鸥股份", ["核电"]),
      ("000003", "海鸥集团", ["家居"])],
     [("海鸥住工(000001)", "海鸥股份(000002)"),
      ("海鸥股份(000002)", "海鸥集团(000003)")]),
    # 4 字节字符（emoji）：前 2 字符相同
    ([("000001", "🤖🤖A", ["机器人"]), ("000002", "🤖🤖B", ["核电"])],
     [("🤖🤖A(000001)", "🤖🤖B(000002)")]),
])
def test_false_relation_pairs(rows, expected):
    got = rust.false_relation_pairs(rows)
    assert got == expected
    assert got == theme.false_relation_pairs(rows)


# ---------- GroupStats 数据结构 ----------

def test_group_stats_pyclass_fields():
    """Rust GroupStats 可构造、字段可读写（get/set），与 Python dict 字段对齐。"""
    gs = rust.GroupStats(6, "600519", 1, 1, 0, 87.0, "梯队完整", 3)
    assert gs.highest_board == 6
    assert gs.top_code == "600519"
    assert gs.mid_count == 1
    assert gs.low_count == 1
    assert gs.first_board_count == 0
    assert gs.completeness == 87.0
    assert gs.status == "梯队完整"
    assert gs.member_count == 3
    gs.status = "孤高"
    assert gs.status == "孤高"
    gs.member_count = 2
    assert gs.member_count == 2
