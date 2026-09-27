"""T15 加速事件（PLAN §1.11 / F2）：一字垄断与高度背离，**事件非状态**。

语义逐字照搬 lkl/services/accelerate.py（docs/07 §3.4）。实盘定义（奎爷 2026-09-01）：
一字板，或者每天开盘就涨停。命中不改 phase，只打标记；仅当 config.ACCEL_ENFORCE=True
才压 buy_window=NONE（该开关属于编排层，本模块只出判定与实测值）。

判据：
  A1 高度背离  名义最高板 - 换手最高板 >= ACCEL_HEIGHT_GAP
  A2 连续一字  名义最高板组内单票连续一字天数 >= ACCEL_ONEWORD_DAYS
  A3 一字泛滥  主板涨停股一字占比 >= ACCEL_ONEWORD_RATIO 且高于前 N 日中位数
命中规则：A1 and (A2 or A3) —— A1 是本质（机会与高度背离），A2/A3 是表现。

口径（逐条对应 lkl 注释里的历次修复，不得简化）：
- 标的池 = 主板 7 前缀 + 次新统一剔除（W2：次新连板会推高名义高度、次新一字会推高占比，
  两项都污染加速判据）。_top_streak 的候选子查询仍不带次新过滤——与 lkl 一致，且名义
  最高板组本身已由 heights 的同一口径产生。
- oneword_ratio 无涨停返回 None，不补 0（F6）：补 0 会让 A3 的「ratio > 基线」假阴性。
- _top_streak 全量取行（A8）：SQL 不过滤 cont_days>=1，否则两段独立行情的连续一字会被
  误拼；断档按市场日历升序索引判断（V3）：相邻两行中间隔了其他交易日 = 停牌缺行 → 归零。
- _baseline_ratio 传 prior_ratios 时纯内存滚动（最新在前，A1 审计 P1-1），不读 market_stat；
  预热期样本不足窗口长度 → None，A3 不成立。

与 lkl 的差异（全部是契约/IO 适配，不涉判定规则）：
1. `db.query_df` → `emotion_core.utils.db.query_df`（同签名：sql, params → DataFrame）。
2. CONFIG 尚未收录 ACCEL_* 键，经 `_cfg` 取 lkl config.py 原值默认（同 entry.py §5 先例）；
   键一旦进 utils/config.py 自动生效（本次范围限本文件 + test_accelerate.py 两文件）。
3. NEW_ISSUER_FILTER 由 CONFIG.NEW_ISSUER_MIN_DAYS / NEW_ISSUER_FLOOR 参数化拼装
   （与 data/loader.py 同先例），默认值 90 自然日 / 2023-11-26 与 lkl 字面量逐字等价。
4. 自持 SQL 4 处（lkl 原语句）：loader 无加速事件相关入口，且本模块不得改其他文件；
   与 entry.py §6 / promotion.py `_fetch_pairs` 同先例，待 loader 补入口后下沉。

对账：tests/unit/test_accelerate.py（无 DB，纯逻辑与降级分支）；另已用真实库差分对账——
同一交易日分别调 lkl.services.accelerate 与本模块的 heights / oneword_ratio / _top_streak /
_baseline_ratio / detect，逐值比对。
"""

# ✅ 已有 Rust 实现：src/emotion_core/core/src/accelerate.rs
# 本文件保留作为参考实现和对账基准，不删除。

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df

_STREAK_WINDOW = 10                # 连续一字回溯上限（自然窗口，够覆盖极端高度）


def _cfg(name: str, default: Any) -> Any:
    """读 CONFIG 策略常量；emotion-core 尚未收录的键取 lkl config.py 原值（见模块文档 §2）。"""
    return getattr(CONFIG, name, default)


ACCEL_HEIGHT_GAP: int = _cfg("ACCEL_HEIGHT_GAP", 2)              # lkl ACCEL_HEIGHT_GAP
ACCEL_ONEWORD_DAYS: int = _cfg("ACCEL_ONEWORD_DAYS", 2)          # lkl ACCEL_ONEWORD_DAYS
ACCEL_ONEWORD_RATIO: float = _cfg("ACCEL_ONEWORD_RATIO", 0.35)   # lkl ACCEL_ONEWORD_RATIO
ACCEL_BASELINE_WINDOW: int = _cfg("ACCEL_BASELINE_WINDOW", 30)   # lkl ACCEL_BASELINE_WINDOW

# W2 次新剔除共享 SQL 片段：引用处需先铺好 %s=目标日期、%s=自然日门槛、%s=窗口边缘
# （与 lkl NEW_ISSUER_FILTER 字面量等价；口径：first_bar_date 缺失 → 视作次新剔除）。
_NEW_ISSUER_FILTER = ("(s.first_bar_date IS NOT NULL AND s.first_bar_date <="
                      " GREATEST(%s::date - %s * interval '1 day', %s::date))")
# 次新过滤参数（顺序同 _NEW_ISSUER_FILTER 的占位符）
_NEW_ISSUER_PARAMS: tuple[Any, ...] = (CONFIG.NEW_ISSUER_MIN_DAYS, CONFIG.NEW_ISSUER_FLOOR)


def _ph() -> str:
    return ", ".join(["%s"] * len(CONFIG.BOARD_PREFIXES))


def heights(trade_date: date) -> tuple[int, int]:
    """主板 (名义最高板, 可交易/换手最高板)；无数据返回 (0, 0)。

    W2：次新统一剔除（config.NEW_ISSUER_FILTER）——此前 heights 只过滤
    主板前缀，次新连板会推高名义高度，污染加速判据 A1 的高度背离。
    """
    df = query_df(
        "SELECT max(d.cont_days) h,"
        " max(CASE WHEN d.is_exchange THEN d.cont_days END) th"
        " FROM derived_bar d JOIN stock_basic s ON s.code = d.code"
        " WHERE d.date = %s AND d.cont_days >= 1"
        f" AND left(d.code, 3) IN ({_ph()})"
        f" AND {_NEW_ISSUER_FILTER}",
        (trade_date, *CONFIG.BOARD_PREFIXES, trade_date, *_NEW_ISSUER_PARAMS))
    r = df.iloc[0]
    return (int(r["h"]) if pd.notna(r["h"]) else 0,
            int(r["th"]) if pd.notna(r["th"]) else 0)


def oneword_ratio(trade_date: date) -> float | None:
    """主板涨停股中一字板占比；无涨停返回 None（F6 不补 0）。

    W2：次新统一剔除——次新一字板（新股连板常态）会推高占比污染 A3。
    """
    df = query_df(
        "SELECT count(*) FILTER (WHERE d.is_limit_up) zt,"
        " count(*) FILTER (WHERE d.is_one_word) ow"
        " FROM derived_bar d JOIN stock_basic s ON s.code = d.code"
        " WHERE d.date = %s AND left(d.code, 3) IN (" + _ph() + ")"
        f" AND {_NEW_ISSUER_FILTER}",
        (trade_date, *CONFIG.BOARD_PREFIXES, trade_date, *_NEW_ISSUER_PARAMS))
    zt = int(df["zt"].iloc[0] or 0)
    if zt == 0:
        return None
    return round(int(df["ow"].iloc[0] or 0) / zt, 4)


def _top_streak(trade_date: date, nominal_h: int) -> int:
    """名义最高板组内，单票截至今日的最长连续一字天数。

    「截至今日」= 当前连续长度（逐日推进取末值），不是历史最长段：组内返回值
    为各票当前连续长度的最大值（同 lkl 原语义，逐字照搬）。

    A8 修复（审计 P2）：SQL 不再过滤 cont_days>=1——此前非涨停断档日被
    直接剔除，两段独立行情的连续一字会被误拼成一段。改为全量取行，
    由代码按「is_one_word 且当日涨停」的真实序列逐日推进，断档即归零重数。
    """
    if nominal_h < 1:
        return 0
    since = trade_date - timedelta(days=_STREAK_WINDOW * 2)
    df = query_df(
        "SELECT code, date, is_one_word, is_limit_up FROM derived_bar"
        " WHERE date BETWEEN %s AND %s"
        f" AND code IN (SELECT code FROM derived_bar WHERE date = %s"
        f"              AND cont_days = %s AND left(code, 3) IN ({_ph()}))"
        " ORDER BY code, date",
        (since, trade_date, trade_date, nominal_h, *CONFIG.BOARD_PREFIXES))
    if df.empty:
        return 0
    # V3：市场日历升序列表——该股相邻两行中间隔了其他交易日 = 停牌缺行，
    # 断档归零（此前 zip 只走存在的行，停牌前后两段一字被误拼成连续）
    cal = sorted(set(query_df(
        "SELECT DISTINCT date FROM derived_bar"
        " WHERE date BETWEEN %s AND %s", (since, trade_date))["date"]))
    pos = {d: i for i, d in enumerate(cal)}
    best = 0
    for _, g in df.groupby("code"):
        seq = g.sort_values("date")
        n = 0
        last_idx = None
        for r in seq.itertuples():
            if last_idx is not None and pos[r.date] - last_idx > 1:
                n = 0                     # 中间存在停牌缺行 → 断档
            n = n + 1 if (r.is_one_word and r.is_limit_up) else 0
            last_idx = pos[r.date]
        best = max(best, n)
    return best


def _baseline_ratio(trade_date: date,
                    prior_ratios: list | None = None) -> float | None:
    """前 ACCEL_BASELINE_WINDOW 交易日一字占比中位数（不含当日）。

    A1（审计 P1-1）：prior_ratios 传入时纯内存滚动计算（最新在前），
    不读 market_stat——回填期间该列为空导致 A3 全程失效的根因即在此；
    None 时回落读库（单日调试/增量模式，只读目标区间外旧值）。
    预热期（样本不足窗口长度）无基线 → None，A3 不成立。
    """
    if prior_ratios is not None:
        vals = [r for r in prior_ratios[:ACCEL_BASELINE_WINDOW]
                if r is not None and not pd.isna(r)]
        if len(vals) < ACCEL_BASELINE_WINDOW:
            return None
        return round(float(pd.Series(vals, dtype=float).median()), 4)
    df = query_df(
        "SELECT oneword_ratio FROM market_stat WHERE date < %s"
        " AND oneword_ratio IS NOT NULL ORDER BY date DESC LIMIT %s",
        (trade_date, ACCEL_BASELINE_WINDOW))
    if len(df) < ACCEL_BASELINE_WINDOW:
        return None                      # 预热期无基线，A3 不成立
    return round(float(df["oneword_ratio"].astype(float).median()), 4)


def _hit(a1: bool, a2: bool, a3: bool) -> bool:
    """命中规则：A1 且 (A2 或 A3)。A1 是本质（高度与机会背离），A2/A3 是表现；
    单有 A3 只是普涨放量，不构成加速。"""
    return a1 and (a2 or a3)


def detect(trade_date: date,
           prior_ratios: list | None = None) -> tuple[bool, str, dict]:
    """返回 (是否加速, 说明, 实测值)。实测值供 dragon_env 与报告复用。

    A1：prior_ratios 传入时 A3 基线纯内存滚动（最新在前），不读库——
    与 emotion._bomb_threshold 同构的可重复性修复。
    """
    nominal_h, tradable_h = heights(trade_date)
    ratio = oneword_ratio(trade_date)
    base = _baseline_ratio(trade_date, prior_ratios)
    streak = _top_streak(trade_date, nominal_h)
    gap = nominal_h - tradable_h
    a1 = gap >= ACCEL_HEIGHT_GAP
    a2 = streak >= ACCEL_ONEWORD_DAYS
    a3 = (ratio is not None and base is not None
          and ratio >= ACCEL_ONEWORD_RATIO and ratio > base)
    hit = _hit(a1, a2, a3)
    facts = {"nominal_h": nominal_h, "tradable_h": tradable_h, "gap": gap,
             "streak": streak, "oneword_ratio": ratio, "baseline_ratio": base,
             "a1": a1, "a2": a2, "a3": a3}
    reason = (f"A1高度背离{gap}(阈{ACCEL_HEIGHT_GAP})={'✓' if a1 else '✗'} "
              f"A2连续一字{streak}日(阈{ACCEL_ONEWORD_DAYS})={'✓' if a2 else '✗'} "
              f"A3一字占比{ratio if ratio is not None else '—'}"
              f"/基线{base if base is not None else '—'}"
              f"(阈{ACCEL_ONEWORD_RATIO})={'✓' if a3 else '✗'}")
    return hit, reason, facts
