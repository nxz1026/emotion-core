"""T13 题材坐标（lkl/services/theme.py 的算法层）：概念标签 → 题材聚合。

语义逐字照搬 lkl/services/theme.py（docs/07 §3.3、§4；docs/11 §3.1「题材结构」）：
STYLE_ONLY 三重判据（黑名单 / 前缀 / 包含）、题材展开口径（unnest 全部标签，一股可属
多题材）、梯队聚合公式（完整度三项权重 + 状态四档）、FALSE_RELATION 判例，一律照抄，
未增删任何条件、未调任何阈值。

与 lkl 的差异（全部是契约 / IO 适配，不涉判定规则）：

1. **Wind MCP 移出算法层**（docs/07 §4 依赖注入裁决 E3）：`_wind_call`（subprocess 调
   node CLI）/ `_tags_from` / `_ann_texts` / `fetch_tags` / `fetch_catalysts` /
   `extract_event` 属数据获取，由 `data/theme_source.py` 的 ThemeProvider 承接。
   本模块不 import subprocess / shutil / json，只收标签行、只做聚合，测试无需 Wind 环境。
   同层裁决（persist_tags / aggregate 的 DELETE+INSERT / run 编排）归
   `services/theme_service.py`——本模块不写库。
2. **工单签名**（本文件对外契约）：
   - `aggregate(theme_tags) -> dict`：lkl `aggregate(trade_date) -> int` 把「展开 + groupby
     + group_stats」与「theme_group 落库」混在一处，本模块只做前者并返回 {题材: 统计}。
     lkl 的 A6「空不清旧」守卫（聚合源为空时不动库）同位置保留为「返回 {}」，落库侧
     据此拒绝清空。
   - `group_stats(codes, trade_date) -> dict`：lkl 收 `[(code, cont_days)]`（调用方已 JOIN），
     本模块按 (trade_date, code) 读 ladder_day 补连板数——与 lkl `JOIN ladder_day` 同语义
     （无梯队行的成员被丢弃，不是记 0）；`cont_days` 可注入以离线测试。
   - `false_relation(code, themes) -> bool`：lkl 判例是**成对**判定（名称前2字相同且有效题材
     交集为空）；单票层面该判据退化为「有效题材为空」（与自身的交集为空）。成对判例本体
     保留在 `false_relation_pairs`（报告层 `_fmt_false_relation` 的输入契约）。
3. 读 SQL 自持 1 处（ladder_day 补 cont_days），与 entry.py / promotion.py / accelerate.py /
   dragon_env.py 同先例；loader 补齐读入口后下沉。
4. CONFIG 尚未收录 THEME_STYLE_* 键，经 `_cfg` 取 lkl config.py 原值（dragon_env 同先例）；
   键一旦进 utils/config.py 自动生效（本次范围限本文件 + test_theme.py 两文件）。

★ 确定性：lkl 的 `group_stats` 用 `max(members, key=...)` 取 top_code，**并列时取行序首个**；
lkl 的 `_aggregate_rows` / `aggregate` SQL **没有 ORDER BY**，故并列最高板的 top_code 由
执行计划决定（lkl 自身落库产物与重跑产物在 3/825 与 2/825 处互不一致，全部是 cont_days
并列的取序差异）。本模块保持该取序语义不变（不新造并列规则），调用方应按稳定顺序传入
theme_tag 行（services 层的读 SQL 补 ORDER BY）以获得可复现 top_code。

对账（真实库差分，2026-08-31~09-24 全部 17 个交易日 / 825 个题材组）：
- 本模块 aggregate(theme_tag 行) vs theme_group 落库产物：8 字段中 7 字段 825/825 全等，
  top_code 3 处差异，全部为并列最高板的取序（见上）；diff 脚本一次性跑，见报告。
- vs lkl.services.theme._aggregate_rows 同输入重跑：5 处差异，同为并列取序。
- is_style：443 个真实标签（theme_group.theme ∪ theme_tag.secondary_themes）0 处不一致。
- false_relation：2026-09-24 真实输入与 lkl 输出相同（当日无判例，两侧均为 []）。
tests/unit/test_theme.py 锁纯逻辑与注入分支（无 DB）。
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df

log = logging.getLogger(__name__)


def _cfg(name: str, default: Any) -> Any:
    """读 CONFIG 策略常量；emotion-core 尚未收录的键取 lkl config.py 原值（见模块文档 §4）。"""
    return getattr(CONFIG, name, default)


# ── STYLE_ONLY 判据（lkl lkl/config.py 原值逐字；三重判据不用同一匹配方式）──
THEME_STYLE_BLACKLIST: frozenset[str] = _cfg("THEME_STYLE_BLACKLIST", frozenset({
    "全部A股", "新质生产力综合", "连板", "打板", "外资企业", "市场情绪",
    "TMT", "小市值", "微盘股", "融资融券", "深股通", "沪股通", "次新股",
    "举牌", "年报预增", "预盈预亏", "ST板块", "低价股", "破净股",
    "超涨", "首板", "领涨龙头", "贷款回购", "合资企业", "养老金",
    "昨日涨停", "昨日连板", "昨日触板", "涨停板", "昨日炸板",
    "龙虎榜", "成交主力", "高振幅", "破净", "预减", "预增", "预盈",
    "预亏", "股票质押", "连板反包", "反包", "趋势股",
}))
THEME_STYLE_PREFIXES: tuple[str, ...] = _cfg("THEME_STYLE_PREFIXES", (
    "全A", "标普", "富时", "纳入", "MSCI", "上证", "深证",
    "股权激励", "财报披露", "首板"))
THEME_STYLE_CONTAINS: tuple[str, ...] = _cfg("THEME_STYLE_CONTAINS", (
    "重仓", "标的", "综合", "点位贡献", "指数", "国资",
    "微盘", "小盘", "大盘", "中盘", "等权"))  # R4：市值/指数族漏网


def is_style(tag: str) -> bool:
    """STYLE_ONLY 噪声：黑名单 / 全A* 前缀 / *重仓* 包含。"""
    return (tag in THEME_STYLE_BLACKLIST
            or tag.startswith(THEME_STYLE_PREFIXES)
            or any(s in tag for s in THEME_STYLE_CONTAINS))


def theme_filter(tags: list[str]) -> list[str]:
    return [t for t in tags if not is_style(t)]


def _row_themes(row: dict) -> list[str]:
    """theme_tag 行 → 展开后的题材名列表（lkl unnest 口径，逐字）。

    lkl SQL：`CASE WHEN primary_theme = ANY(secondary_themes) THEN secondary_themes
    ELSE secondary_themes || primary_theme END`——主名已在次名列表内则原样，
    否则主名**追加在末位**（并列 top_code 的取序即由此产生）。
    数组或主名任一为 NULL → `secondary_themes || primary_theme` = NULL → unnest 零行。
    工单行形状 {code, theme_name, tag_date} 是单标签，直接成表（优先于原生形状）。
    """
    if "theme_name" in row:
        return [row["theme_name"]] if row["theme_name"] else []
    secondary, primary = row.get("secondary_themes"), row.get("primary_theme")
    if secondary is None or primary is None:
        return []
    secondary = list(secondary)
    return secondary if primary in secondary else [*secondary, primary]


def _fetch_cont_days(pairs: Iterable[tuple[date, str]]) -> dict[tuple[date, str], int]:
    """ladder_day 按 (date, code) 取连板数（lkl `JOIN ladder_day l ON l.date = tt.date
    AND l.code = tt.code` 的等价读）。返回仅含命中对——无行即不在梯队（INNER JOIN 丢行）。

    本模块唯一 SQL（只读）；写入口属 services 层（模块文档 §1）。
    """
    pairs = set(pairs)
    if not pairs:
        return {}
    df = query_df(
        "SELECT date, code, cont_days FROM ladder_day"
        " WHERE date = ANY(%s) AND code = ANY(%s)",
        (sorted({d for d, _ in pairs}), sorted({c for _, c in pairs})))
    got = {(r.date, str(r.code)): int(r.cont_days) for r in df.itertuples(index=False)}
    return {p: got[p] for p in pairs if p in got}


def load_cont_days(trade_date: date, codes: Sequence[str]) -> dict[str, int]:
    """当日 ladder_day 的 {code: cont_days}（group_stats 的单日读入口，可被替换/注入）。"""
    return {code: days for (_, code), days in
            _fetch_cont_days((trade_date, c) for c in codes).items()}


def group_stats_of(members: list[tuple[str, int]]) -> dict:
    """lkl `group_stats` 本体（逐字）：[(code, cont_days)] → 梯队聚合。completeness 临时
    公式（权重待 P3 用晋级率定标）：10*min(h,8) + 30*(1-断层率) + 20*min(1,成员/5)。

    top_code 取行序首个最高板（lkl 的 `max(members, ...)`，平局由输入顺序决定）。
    空输入按 lkl 行为抛 ValueError（`max()` 空序列）——不静默返回零值统计。
    """
    days = [c for _, c in members]
    h = max(days)
    levels = set(days)
    gaps = sum(1 for x in range(2, h) if x not in levels) if h > 2 else 0
    gap_rate = gaps / (h - 2) if h > 2 else 0.0
    mid = sum(1 for x in days if 3 <= x < h)
    low = sum(1 for x in days if x == 2)
    top = max(members, key=lambda m: m[1])[0]
    comp = round(10 * min(h, 8) + 30 * (1 - gap_rate)
                 + 20 * min(1.0, len(days) / 5), 1)
    if h >= 4 and mid and low:
        status = "梯队完整"
    elif h >= 4 and not mid:
        status = "孤高"
    elif len(days) >= 5 and h <= 2:
        status = "低位扩散"
    else:
        status = "一般"
    return {"highest_board": h, "top_code": top, "mid_count": mid,
            "low_count": low, "first_board_count": 0, "completeness": comp,
            "status": status, "member_count": len(days)}


def group_stats(codes: list[str], trade_date: date,
                cont_days: Mapping[str, int] | None = None) -> dict:
    """题材组统计：成员代码 + 交易日 → 梯队聚合（聚合本体见 group_stats_of）。

    lkl 收 [(code, cont_days)]（调用方已 JOIN）；本模块按 (trade_date, code) 读 ladder_day
    补连板数（lkl `JOIN ladder_day` 同语义：无梯队行的代码被丢弃，不记 0）。
    cont_days 注入时不读库（离线测试 / 调用方已有映射）。

    codes 的顺序即并列最高板 top_code 的取序（lkl 同：`max` 取行序首个）。
    全体成员被丢弃 → {}（空题材组不成立；lkl 中该分支由 groupby 屏蔽）。
    """
    cd = load_cont_days(trade_date, codes) if cont_days is None else dict(cont_days)
    members = [(c, int(cd[c])) for c in codes if c in cd]
    return group_stats_of(members) if members else {}


def aggregate(theme_tags: list[dict]) -> dict[str, dict]:
    """题材展开聚合（lkl `_aggregate_rows` 的纯函数化）：展开全部标签 → 按题材分组 → 梯队聚合。

    输入行必备 code / tag_date；题材来源二选一（见 _row_themes）——工单形状 `theme_name`，
    或 lkl 原生形状 `primary_theme` + `secondary_themes`（一股可属多题材）。
    可选键 `cont_days`：直接带连板数，省一次 ladder_day 读；缺失者按 (tag_date, code) 补读。
    无梯队行的成员被丢弃（lkl INNER JOIN 语义，不记 0）。

    输出：{题材: group_stats_of 统计}，键序 = 题材在展开口径下的首次出现序。
    STYLE_ONLY 题材在展开后剔除（is_style，与 lkl 同位置同判据）。
    空输入 / 题材全被剔除 / 成员全无梯队行 → {}——落库侧据此不清旧（A6 守卫，模块文档 §2）。
    """
    if not theme_tags:
        return {}
    expanded: list[tuple[date, str, str, int | None]] = []
    for row in theme_tags:
        code, td = row.get("code"), row.get("tag_date")
        if not code or td is None:
            continue          # 无股票/无日期：无从 JOIN 梯队，丢行（不是记 0）
        cd = row.get("cont_days")
        for theme in _row_themes(row):
            if not is_style(theme):
                expanded.append((td, str(code), theme, None if cd is None else int(cd)))
    if not expanded:
        return {}
    missing = {(td, code) for td, code, _, cd in expanded if cd is None}
    joined = _fetch_cont_days(missing) if missing else {}
    groups: dict[str, list[tuple[str, int]]] = {}
    for td, code, theme, cd in expanded:
        days = cd if cd is not None else joined.get((td, code))
        if days is None:
            continue
        groups.setdefault(theme, []).append((code, days))
    return {theme: group_stats_of(members) for theme, members in groups.items()}


def false_relation(code: str, themes: list[str]) -> bool:
    """伪关联检测（单票）：有效题材为空 → 该票题材关联为伪（无板块支撑）。

    lkl 判例是成对判定（名称前2字相同 **且** 有效题材交集为空，防名称相似误归同组）；
    对单票取「与自身的交集」，退化为 theme_filter(themes) 为空。判据不含代码本身
    （lkl 同：只用名称与题材），code 仅作调用方对齐与日志定位。
    """
    return not theme_filter(themes)


def false_relation_pairs(rows: list[tuple[str, str, list[str]]]) -> list[tuple[str, str]]:
    """纯函数：名称前2字相同但有效题材交集为空 → FALSE_RELATION 判例对
    （例：海鸥住工 vs 海鸥股份，防名称相似误归同组）。

    逐字照搬 lkl：输入契约 = 报告层 `_fmt_false_relation` 的 [(code, name, tags)]，
    输出仍是 [(a, b)] 字符串对（a/b 含代码，供报告直出）。
    """
    out = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            (c1, n1, t1), (c2, n2, t2) = rows[i], rows[j]
            if n1[:2] == n2[:2] and not (set(theme_filter(t1))
                                         & set(theme_filter(t2))):
                out.append((f"{n1}({c1})", f"{n2}({c2})"))
    return out
