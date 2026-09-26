"""题材聚合算法单元测试。

无 DB：ladder_day 读路径用 stub query_df；真值与 lkl 逐值对账（theme_group 落库产物
与 lkl.services.theme 同输入比对）用真实库差分跑，见报告。
本文件锁 STYLE_ONLY 判据、聚合公式四档状态、展开口径、FALSE_RELATION 判例。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from emotion_core.algorithms import theme

D = date(2026, 9, 24)


class TestStyleOnly:
    """STYLE_ONLY 三重判据：黑名单 / 前缀（锚定开头）/ 包含。"""

    @pytest.mark.parametrize("tag", [
        "昨日涨停",        # 黑名单
        "微盘股",          # 黑名单
        "全A股",           # 前缀
        "上证50",          # 前缀
        "MSCI中国A股",     # 前缀
        "基金重仓股",       # 包含
        "沪深300指数",      # 包含
        "小盘成长",         # 包含
    ])
    def test_noise_tags(self, tag):
        assert theme.is_style(tag) is True

    @pytest.mark.parametrize("tag", [
        "机器人",
        "固态电池",
        "半导体设备",
        "赛道全A股",        # 前缀锚定开头，非包含匹配 → 不判噪声（lkl 同）
    ])
    def test_real_tags_not_noise(self, tag):
        assert theme.is_style(tag) is False

    def test_filter_preserves_order(self):
        tags = ["机器人", "昨日涨停", "基金重仓股", "固态电池", "上证50"]
        assert theme.theme_filter(tags) == ["机器人", "固态电池"]

    def test_filter_all_noise(self):
        assert theme.theme_filter(["昨日涨停", "打板"]) == []


class TestGroupStatsOf:
    """lkl group_stats 本体：完整度公式与状态四档。"""

    def test_full_ladder(self):
        got = theme.group_stats_of([("600519", 6), ("000001", 3), ("002594", 2)])
        assert got == {
            "highest_board": 6, "top_code": "600519", "mid_count": 1, "low_count": 1,
            "first_board_count": 0, "completeness": 87.0,   # 60 + 15 + 12
            "status": "梯队完整", "member_count": 3,
        }

    def test_lonely_high(self):
        got = theme.group_stats_of([("600519", 5), ("002594", 2)])
        assert got["status"] == "孤高"          # h>=4 且无中位
        assert got["completeness"] == 68.0      # 50 + 10 + 8
        assert (got["highest_board"], got["mid_count"], got["low_count"]) == (5, 0, 1)
        assert got["member_count"] == 2

    def test_low_diffusion(self):
        got = theme.group_stats_of([(f"60051{i}", 2) for i in range(5)])
        assert got["status"] == "低位扩散"        # 成员>=5 且 h<=2
        assert got["completeness"] == 70.0      # 20 + 30 + 20
        assert got["highest_board"] == 2

    def test_plain(self):
        got = theme.group_stats_of([("600519", 3), ("000001", 2)])
        assert got["status"] == "一般"           # h=3：不满足四档前三
        assert got["completeness"] == 68.0      # 30 + 30 + 8
        assert got["highest_board"] == 3

    def test_height_capped_at_eight(self):
        got = theme.group_stats_of([("600519", 12)])
        assert got["highest_board"] == 12
        assert got["completeness"] == 84.0      # 10*min(12,8) + 30*(1-1.0) + 4
        assert got["status"] == "孤高"

    def test_top_code_tie_break_is_input_order(self):
        assert theme.group_stats_of([("000001", 5), ("600519", 5)])["top_code"] == "000001"
        assert theme.group_stats_of([("600519", 5), ("000001", 5)])["top_code"] == "600519"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            theme.group_stats_of([])


class TestGroupStats:
    """group_stats：成员解析（JOIN 丢行）与注入分支。"""

    def test_injected_cont_days(self):
        got = theme.group_stats(["600519", "000001", "002594"], D,
                                cont_days={"600519": 6, "000001": 3, "002594": 2})
        assert got == theme.group_stats_of([("600519", 6), ("000001", 3), ("002594", 2)])

    def test_members_without_ladder_row_dropped(self):
        """非梯队成员被丢弃（lkl INNER JOIN 语义），不记 0 板。"""
        got = theme.group_stats(["600519", "999999"], D, cont_days={"600519": 5})
        assert got["member_count"] == 1
        assert got["top_code"] == "600519"
        assert got["status"] == "孤高"

    def test_all_members_dropped_is_empty(self):
        assert theme.group_stats(["999999"], D, cont_days={}) == {}
        assert theme.group_stats([], D, cont_days={}) == {}

    def test_reads_ladder_day_when_not_injected(self, monkeypatch):
        calls = []

        def fake_query_df(sql, params=()):
            calls.append((sql, params))
            return pd.DataFrame([{"date": D, "code": "600519", "cont_days": 6}])

        monkeypatch.setattr(theme, "query_df", fake_query_df)
        got = theme.group_stats(["600519", "999999", "000001"], D)

        assert (got["member_count"], got["top_code"]) == (1, "600519")
        sql, params = calls[0]
        assert "ladder_day" in sql
        assert params == ([D], ["000001", "600519", "999999"])


class TestAggregate:
    """题材展开聚合：一股可属多题材、噪声剔除、缺数丢行。"""

    def test_ticket_shape_rows(self):
        rows = [
            {"code": "600519", "theme_name": "机器人", "tag_date": D, "cont_days": 6},
            {"code": "000001", "theme_name": "机器人", "tag_date": D, "cont_days": 3},
            {"code": "002594", "theme_name": "机器人", "tag_date": D, "cont_days": 2},
            {"code": "600519", "theme_name": "固态电池", "tag_date": D, "cont_days": 6},
            {"code": "300750", "theme_name": "昨日涨停", "tag_date": D, "cont_days": 5},
        ]
        got = theme.aggregate(rows)

        assert set(got) == {"机器人", "固态电池"}          # 噪声题材整组不出现
        assert got["机器人"] == {
            "highest_board": 6, "top_code": "600519", "mid_count": 1, "low_count": 1,
            "first_board_count": 0, "completeness": 87.0,
            "status": "梯队完整", "member_count": 3,
        }
        assert got["固态电池"] == {
            "highest_board": 6, "top_code": "600519", "mid_count": 0, "low_count": 0,
            "first_board_count": 0, "completeness": 64.0,   # 60 + 0（断层率 1.0） + 4
            "status": "孤高", "member_count": 1,
        }

    def test_stock_may_belong_to_multiple_themes(self):
        """展开口径：同一股计入其全部有效题材（lkl unnest 全部标签）。"""
        rows = [
            {"code": "600519", "theme_name": "机器人", "tag_date": D, "cont_days": 4},
            {"code": "600519", "theme_name": "固态电池", "tag_date": D, "cont_days": 4},
        ]
        got = theme.aggregate(rows)
        assert set(got) == {"机器人", "固态电池"}
        assert all(v["member_count"] == 1 for v in got.values())

    def test_lkl_primary_and_secondary_no_double_count(self):
        """primary 已在 secondary 内 → 不重复展开（lkl CASE WHEN 分支）。"""
        rows = [{"code": "600519", "primary_theme": "机器人",
                 "secondary_themes": ["机器人", "减速器"], "tag_date": D, "cont_days": 3}]
        got = theme.aggregate(rows)
        assert [v["member_count"] for v in got.values()] == [1, 1]
        assert set(got) == {"机器人", "减速器"}

    def test_lkl_primary_appended_when_absent(self):
        rows = [{"code": "600519", "primary_theme": "机器人",
                 "secondary_themes": ["昨日涨停", "减速器"], "tag_date": D, "cont_days": 3}]
        got = theme.aggregate(rows)
        assert set(got) == {"机器人", "减速器"}       # secondary 里的噪声被剔除

    def test_null_primary_or_secondary_expands_to_nothing(self):
        """lkl NULL 传播：`secondary || NULL` = NULL → unnest 零行。"""
        assert theme.aggregate([{"code": "600519", "primary_theme": None,
                                 "secondary_themes": ["机器人"],
                                 "tag_date": D, "cont_days": 3}]) == {}
        assert theme.aggregate([{"code": "600519", "primary_theme": "机器人",
                                 "secondary_themes": None,
                                 "tag_date": D, "cont_days": 3}]) == {}

    def test_missing_date_and_missing_board_row_dropped(self, monkeypatch):
        monkeypatch.setattr(theme, "query_df", lambda sql, params=(): pd.DataFrame(
            [{"date": D, "code": "600519", "cont_days": 2}]))
        rows = [
            {"code": "600519", "theme_name": "机器人", "tag_date": D},   # 走读库补板
            {"code": "999999", "theme_name": "机器人", "tag_date": D},   # 梯队无行 → 丢
            {"code": "000001", "theme_name": "机器人", "cont_days": 3},  # 无日期 → 丢
        ]
        got = theme.aggregate(rows)
        assert got["机器人"]["member_count"] == 1
        assert got["机器人"]["top_code"] == "600519"

    def test_empty_input(self):
        assert theme.aggregate([]) == {}

    def test_all_themes_noise_is_empty(self):
        rows = [{"code": "600519", "theme_name": "昨日涨停", "tag_date": D,
                 "cont_days": 5}]
        assert theme.aggregate(rows) == {}


class TestFalseRelation:
    """伪关联：单票退化判据 + lkl 成对判例本体。"""

    @pytest.mark.parametrize("tags,expected", [
        (["机器人", "昨日涨停"], False),
        (["昨日涨停", "基金重仓股"], True),
        ([], True),
    ])
    def test_single_stock(self, tags, expected):
        assert theme.false_relation("600519", tags) is expected

    def test_similar_names_without_shared_theme(self):
        rows = [("002084", "海鸥住工", ["家居", "卫浴"]),
                ("603269", "海鸥股份", ["核电", "冷却塔"]),
                ("000001", "平安银行", ["银行"])]
        assert theme.false_relation_pairs(rows) == [("海鸥住工(002084)",
                                                     "海鸥股份(603269)")]

    def test_similar_names_with_shared_theme_not_flagged(self):
        rows = [("002084", "海鸥住工", ["机器人"]),
                ("603269", "海鸥股份", ["机器人", "核电"])]
        assert theme.false_relation_pairs(rows) == []

    def test_style_only_tags_do_not_count_as_relation(self):
        """有效题材交集：噪声标签不算交集（theme_filter 后再比）。"""
        rows = [("002084", "海鸥住工", ["昨日涨停"]),
                ("603269", "海鸥股份", ["昨日涨停"])]
        assert theme.false_relation_pairs(rows) == [("海鸥住工(002084)",
                                                     "海鸥股份(603269)")]

    def test_different_names_not_compared(self):
        rows = [("600519", "贵州茅台", []), ("000001", "平安银行", [])]
        assert theme.false_relation_pairs(rows) == []
