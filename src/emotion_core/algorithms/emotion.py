"""T3 情绪周期状态机：指标层 + 时序推进 + market_stat 落库（lkl/services/emotion.py 逐字照搬）。

口径 R3：家数/炸板率/跌停数/昨涨停表现 = 全市场剔ST剔次新；最高板 = 主板。
数据源（2026-08-31 实测 EM 池仅存近30日）：全部指标自 daily_bar/derived_bar 自算，
derived_bar 已按板块比例判板（68/30→20%）且剔ST；北交所无日线源，计数天然缺北交所。
次新近似：daily_bar 首条日期 > 当日-90自然日（≈60交易日，PLAN §1.1 代理）。
状态机优先级：退潮 > 高潮 > 发酵 > 冰点；无命中延续昨日；首日种子=冰点。
2026-09 校准（奎爷拍板）：高潮炸板阈值改自适应=前30交易日炸板率中位数+σ（预热期回退
BOMB_RATE_FALLBACK 固定值）；amp 分支 12→15；冰点反转确认在"当日无唯一换手最高板候选"
时豁免（候选需最高板≥MIN_LEADER_DAYS，与冰点 h≤3 互斥，放宽只落在无候选日）；
高潮分歧降级：高潮且负反馈 nb≥3（断板/高度降/炸升/表现降/跌停升 计数）→ 窗口降为
DIVERGE_WINDOW=NONE（禁买不清仓；nb≥3 日信号晋级率 0.17 vs 其余 0.42）。

本模块职责（lkl services/emotion.py 的三段）：
1. 指标层（自持 SQL，lkl 原语句逐字）：`_counts` / `_zt_perf` / `_top_amplitude` /
   `_top_broke` / `_bomb_threshold` / `_has_candidate` → `indicators(trade_date, hist)`；
2. 时序推进 `classify(series)`：seed 不重判（W1）/ 缺数据只继承（V3）/ 负反馈计数 /
   分歧降级 / 加速叠加（F2 事件非状态）/ ·延续·首日基线 标记（R2）；
3. 落库 `persist(trade_date)`（= 增量模式最小粒度）、`run_range(start, end, warmup)`、
   `replay(start, end)`；幂等 upsert market_stat 23 列（不含 dragon_env* 三列）。

A1/A2（审计 P1-1/2，不可重复性修复）：炸板阈值与一字占比基线在 `hist` 传入时**纯内存滚动**
（最新在前），全程不读 market_stat——`replay` 从 DATA_START 零读库全量重放，同一份
derived_bar 重放多少遍结果恒一致；`run_range` 只读**目标区间之前**的旧库值当基线，
phase 继承链由 seed（昨日已落库 phase）显式续接。W1：hist 逐日前插（不再整区间冻结
days[0] 的快照），与 replay 严格等价。
F8：单日调用会让 y/b 全为 None（发酵/退潮 two_day/负反馈分支静默失效），故 run_range
自动向前多取 EMOTION_WARMUP 个交易日作前情，只回写目标区间。

与 lkl 的差异（全部是契约/IO 适配，不涉判定规则）：
1. 规则判定复用 `state._RULES` / `state.window_of`（同一套阈值序列，已过 661 天 oracle
   对账）——本模块**不重写**四条规则，避免出现第二份阈值语义；state.py 只管规则本身
   （无 seed / 缺数据 / 负反馈 / 加速叠加），时序语义在本模块。
2. 状态容器自持 `EmotionDay`：承载 lkl models.types.EmotionState 的情绪字段全集
   （含 neg_feedback / diverge / phase_inherited / is_seed / data_missing / accel_facts），
   state.EmotionState 只有 phase/buy_window/force_liquidate/reason 四字段（oracle 对账够用，
   落库不够）。dragon_env* 三列归 dragon_env.py（经 loader.update_market_stat_ecosystem
   单独 UPDATE），本模块 `_COLS` 不含，避免两模块互相覆盖。
3. 写入口自持 `_upsert_rows`：复刻 lkl `utils/db.upsert_rows` 写语义（分批 UPSERT_BATCH /
   NaN±inf→NULL 消毒 / 行宽校验 / ON CONFLICT(date) DO UPDATE）。loader.upsert_market_stat
   只覆盖 10 列，会把 neg_feedback/diverge/zt_performance_median/... 13 列留在默认值，
   与 market_stat 26 列口径（docs/06 §1.3 验收）不符；本轮范围限两文件不改 loader，
   故同 outcome.py 先例自持。略去 `revision.detect_and_log`（emotion-core 无 services/revision）。
4. 日历：区间用 `utils.dates.trading_days`；单日回退（前一日 / warm-up / 30 日滚动窗口）
   以等价单行 SQL 走 `utils.db.query_df`（自开自关）——不逐次调用
   `utils.dates.prev_trading_day`：后者每次新建只读连接且不关闭，而 run_range 需回退
   5+30 次。SQL 与 utils/dates.py 字面等价（`SELECT max(date) FROM daily_bar WHERE date < %s`）。
5. CONFIG 未收录 EMOTION_PERF_BASIS / EMOTION_WARMUP / ACCEL_ENFORCE / UPSERT_BATCH，
   经 `_cfg` 取 lkl config.py 原值（accelerate.py 同先例）；已收录的键直接用 CONFIG
   （BOMB_RATE_WINDOW=30 / BOMB_RATE_FALLBACK=0.42 / EBB_* / ICE_* / MIN_LEADER_DAYS /
   DIVERGE_* —— 与 lkl CLIMAX_BOMB_WINDOW / CLIMAX_BOMB_RATE 同值异名）。
6. 次新过滤 SQL 片段引用 accelerate 的 W2 共享常量（3 占位符版，CONFIG 参数化）。
   **仅** `_counts` / `_zt_perf` 带该过滤；`_top_amplitude` / `_top_broke` / `_has_candidate`
   与 lkl 现状一致**不带**——补上会改历史对账结果，属口径变更，不在本工单。
7. 工单写的 `indicators(df: pd.DataFrame)`：lkl 原签名是 `(trade_date, hist)`（指标由 SQL
   聚合装配，昨日涨停表现需前一日收盘、断板需前一日封板、自适应阈值需前 30 日窗口，
   无法由当日单帧 DataFrame 表达），逐字照搬优先，故取原签名；`classify` 两种入参都收
   （`list[EmotionDay]` 或 market_stat 形状的 DataFrame，用于从已落库指标重推相位）。
8. `_gt` 两侧都护 None（lkl 只护 a）：lkl `_neg_feedback` 的 `_gt(t.bomb_rate,
   y.bomb_rate)` 在「昨日无人触板（bomb_rate=None）而今日有人触板」时会抛
   TypeError（实测复现：`'>' not supported between instances of 'float' and
   'NoneType'`，与函数注释「V3：None 不计数」的意图相反）。判定结果不变——非 None
   比较逐值相同，只是不再抛异常（F6 语义按注释落地）。

对账：tests/unit/test_emotion.py（无 DB，假查询断言规则/时序/落库行）；另用一次性脚本
按日差分对账真实库 market_stat 历史行（run_range 增量链与 replay 零读库链双路）。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from emotion_core.algorithms import accelerate
from emotion_core.algorithms.accelerate import _NEW_ISSUER_FILTER, _NEW_ISSUER_PARAMS
from emotion_core.algorithms.state import _RULES, window_of
from emotion_core.utils.config import CONFIG
from emotion_core.utils.dates import trading_days
from emotion_core.utils.db import query_df, transaction

log = logging.getLogger(__name__)


def _cfg(name: str, default: Any) -> Any:
    """读 CONFIG 策略常量；emotion-core 尚未收录的键取 lkl config.py 原值（见模块文档 §5）。"""
    return getattr(CONFIG, name, default)


_EMOTION_PERF_BASIS: str = _cfg("EMOTION_PERF_BASIS", "median")   # lkl EMOTION_PERF_BASIS
_EMOTION_WARMUP: int = _cfg("EMOTION_WARMUP", 5)                 # lkl EMOTION_WARMUP
_BOMB_WINDOW: int = _cfg("BOMB_RATE_WINDOW", 30)                 # lkl CLIMAX_BOMB_WINDOW
_BOMB_FALLBACK: float = _cfg("BOMB_RATE_FALLBACK", 0.42)         # lkl CLIMAX_BOMB_RATE
_ACCEL_ENFORCE: bool = _cfg("ACCEL_ENFORCE", False)              # lkl ACCEL_ENFORCE（影子开关）
_ACCEL_BASELINE_WINDOW: int = _cfg("ACCEL_BASELINE_WINDOW", 30)  # lkl ACCEL_BASELINE_WINDOW
_UPSERT_BATCH: int = _cfg("UPSERT_BATCH", 1000)                  # lkl UPSERT_BATCH


def _lt(a: Any, b: float) -> bool:
    """a < b，None 视为不成立（F6：缺数据不触发规则，而非误触发）。"""
    return a is not None and a < b


def _gt(a: Any, b: Any) -> bool:
    """a > b，None 视为不成立（F6）。"""
    return a is not None and b is not None and a > b


@dataclass
class EmotionDay:
    """market_stat 一行的完整情绪字段（lkl models.types.EmotionState 的情绪部分）。

    ★F6 缺失语义：指标类字段 None = 数据缺失（禁补 0.0/False）；规则判据遇 None
    视为该条不成立。dragon_env* 三列不在本容器（归 dragon_env.py）。
    """
    date: date
    limit_up_count: int = 0
    bomb_rate: float | None = None           # V3：分母0（无人触板）→ None（F6 禁补0）
    zt_performance: float | None = None      # 昨日涨停今日涨幅（判据列，basis 见 config）
    max_limit_days: int = 0                  # 主板名义最高板（含一字）
    limit_down_count: int = 0
    phase: str = ""                          # 冰点/发酵/高潮/退潮
    buy_window: str = "NONE"                 # STANDARD/ENHANCED/NONE
    force_liquidate: bool = False
    reason: str = ""                         # 阶段判定说明（复盘①用）
    phase_inherited: bool = False            # R2：True=当日无规则触发、延续昨日 phase
    reason_tag: str = ""                     # R2：·延续/·反转/·加速 等叠加标记
    is_seed: bool = False                    # W1：True=增量模式播种的昨日快照——只作
                                             # y/b 继承上下文，classify 不得对其执行规则判定
    data_missing: bool = False               # V3：True=derived_bar 当日整体缺失，
                                             # classify 只继承昨日并标 ·缺数据，不判冰点
    top_amplitude: float = 0.0               # 最高板股今日最大振幅 %（高潮规则）
    # 昨日最高板组今日未封；None=无前日数据/无≥2板组（A8：默认 None
    # 与语义一致，False 必须是实测"未断板"）
    top_broke: bool | None = None
    bomb_threshold: float = float("inf")     # 自适应炸板阈值（inf=分支关闭）
    has_candidate: bool = False              # 存在唯一换手最高板候选（冰点放宽守卫）
    neg_feedback: int = 0                    # 高潮分歧负反馈计数（vs 昨日，0~5）
    diverge: bool = False                    # 高潮且 nb≥DIVERGE_NEG_MIN → 窗口降级
    zt_performance_mean: float | None = None    # 均值（展示用，与中位数并列）
    zt_performance_median: float | None = None  # 中位数（★F5 目标判据）
    tradable_max_days: int | None = None        # 换手口径最高板 H（并列展示）
    oneword_ratio: float | None = None          # 当日涨停股一字占比（加速 A3 基线）
    accelerate: bool = False                    # §1.11 加速事件命中
    accel_reason: str = ""                      # A1/A2/A3 实测值说明
    accel_facts: dict = field(default_factory=dict)  # 实测值（不入库，供评级复用）


def _ph() -> str:
    return ", ".join(["%s"] * len(CONFIG.BOARD_PREFIXES))


def _prev_day(d: date) -> date | None:
    """d 之前最近一个已入库交易日（SQL 与 utils/dates.prev_trading_day 等价，见模块文档 §4）。"""
    df = query_df("SELECT max(date) AS d FROM daily_bar WHERE date < %s", (d,))
    val = df["d"].iloc[0]
    return None if val is None or pd.isna(val) else val


def _counts(trade_date: date) -> dict:
    """全市场当日 涨停/炸板/触板/跌停 计数（剔次新）+ 主板最高板。"""
    df = query_df(
        "SELECT count(*) FILTER (WHERE d.is_limit_up) zt,"
        " count(*) FILTER (WHERE d.is_bomb) zb,"
        " count(*) FILTER (WHERE d.touched_limit) touched,"
        " count(*) FILTER (WHERE d.is_limit_down) dt,"
        " max(d.cont_days) FILTER (WHERE left(d.code,3) IN (" + _ph() + ")) maxh"
        " FROM derived_bar d JOIN stock_basic s ON s.code = d.code"
        f" WHERE d.date = %s AND {_NEW_ISSUER_FILTER}",
        (*CONFIG.BOARD_PREFIXES, trade_date, trade_date, *_NEW_ISSUER_PARAMS))
    return df.iloc[0].to_dict()


def _zt_perf(trade_date: date, prev: date | None
             ) -> tuple[float | None, float | None]:
    """昨日涨停股（全市场剔ST次新）今日涨幅 (均值, 中位数) %。

    ★F6：无前一交易日或样本为空 → (None, None)，禁补 0.0（0 会被退潮规则
    当成"表现差"、被发酵规则当成"不达标"，方向性污染判定）。
    """
    if prev is None:
        return None, None
    df = query_df(
        "SELECT avg(r) * 100 AS mean,"
        " percentile_cont(0.5) WITHIN GROUP (ORDER BY r) * 100 AS median"
        " FROM (SELECT CASE WHEN y.close > 0 THEN t.close / y.close - 1 END"
        " AS r FROM derived_bar d"
        " JOIN stock_basic s ON s.code = d.code"
        " JOIN daily_bar y ON y.code = d.code AND y.date = d.date"
        " JOIN daily_bar t ON t.code = d.code AND t.date = %s"
        f" WHERE d.date = %s AND d.is_limit_up AND {_NEW_ISSUER_FILTER}) x",
        (trade_date, prev, prev, *_NEW_ISSUER_PARAMS))
    row = df.iloc[0]
    mean = None if pd.isna(row["mean"]) else float(row["mean"])
    med = None if pd.isna(row["median"]) else float(row["median"])
    return mean, med


def _top_amplitude(trade_date: date) -> tuple[int, float]:
    """主板最高板高度 + 最高板股今日最大振幅。

    V3（二轮审计）：零涨停日返回 (0, 0.0)——此前 h=None 时退化取全市场
    振幅极值（子查询 max 无行恒 NULL 但外层 max(amplitude) 可能有值），
    可能凭空触发高潮 amp 判据。
    """
    df = query_df(
        "SELECT max(cont_days) h, max(amplitude) a FROM derived_bar"
        f" WHERE date = %s AND left(code,3) IN ({_ph()})"
        " AND cont_days = (SELECT max(cont_days) FROM derived_bar"
        f"                       WHERE date = %s AND left(code,3) IN ({_ph()}))",
        (trade_date, *CONFIG.BOARD_PREFIXES, trade_date, *CONFIG.BOARD_PREFIXES))
    h, a = df["h"].iloc[0], df["a"].iloc[0]
    if h is None or pd.isna(h):
        return 0, 0.0                     # 零涨停：无最高板组，振幅不参与判据
    return int(h), (float(a) if a is not None else 0.0)


def _top_broke(trade_date: date, prev: date | None) -> bool | None:
    """昨日最高板组（主板≥2板）今日全部未封板 = 断板。停牌视作未封。

    ★F6 修正：无前一交易日 → None；昨日**不存在** ≥2 板组（冰点日常态）也返回
    None。原实现 `NOT COALESCE(bool_or(...), false)` 在无组时得 True，把"没有高标"
    误判成"高标断板"，会向退潮规则注入假信号。
    """
    if prev is None:
        return None
    df = query_df(
        "SELECT CASE WHEN count(*) = 0 THEN NULL"
        "        ELSE NOT COALESCE(bool_or(d2.is_limit_up), false) END broke"
        " FROM derived_bar d1"
        " LEFT JOIN derived_bar d2 ON d2.code = d1.code AND d2.date = %s"
        f" WHERE d1.date = %s AND left(d1.code,3) IN ({_ph()}) AND d1.cont_days ="
        f"   (SELECT max(cont_days) FROM derived_bar WHERE date = %s"
        f"     AND left(code,3) IN ({_ph()})) AND d1.cont_days >= 2",
        (trade_date, prev, *CONFIG.BOARD_PREFIXES, prev, *CONFIG.BOARD_PREFIXES))
    if df.empty:
        return None
    val = df["broke"].iloc[0]
    return None if val is None or pd.isna(val) else bool(val)


def _bomb_threshold(trade_date: date, prior_rates: list | None = None) -> float:
    """自适应炸板阈值 = 前 BOMB_RATE_WINDOW 交易日炸板率 中位数+σ（不含当日）。

    样本不足（预热期）回退固定 BOMB_RATE_FALLBACK。σ 用样本标准差（ddof=1）。
    A1（审计 P1-1）：prior_rates 传入时纯内存计算（序列内滚动，最新在前），
    不读 market_stat——杜绝「先算后写」导致的空库/旧库不可重复问题；
    None 时回落读库（单日调试兼容路径，增量模式只读目标区间外的旧值）。
    """
    if prior_rates is not None:
        s = pd.Series([r for r in prior_rates[:_BOMB_WINDOW]
                       if r is not None and not pd.isna(r)], dtype=float)
        return (float(s.median() + s.std(ddof=1))
                if len(s) >= _BOMB_WINDOW else _BOMB_FALLBACK)
    df = query_df(
        "SELECT bomb_rate FROM market_stat WHERE date < %s"
        " AND bomb_rate IS NOT NULL ORDER BY date DESC LIMIT %s",
        (trade_date, _BOMB_WINDOW))
    if len(df) < _BOMB_WINDOW:
        return _BOMB_FALLBACK
    s = df["bomb_rate"].astype(float)
    return float(s.median() + s.std(ddof=1))


def _has_candidate(trade_date: date) -> bool:
    """当日是否存在唯一换手最高板候选（换手最高板组 size==1 且高度≥MIN_LEADER_DAYS）。

    与 ladder.sole_top 判定同构；冰点放宽守卫用。
    """
    df = query_df(
        "WITH t AS (SELECT cont_days, is_exchange,"
        " MAX(CASE WHEN is_exchange THEN cont_days END) OVER () AS th"
        " FROM derived_bar WHERE date = %s AND cont_days >= 2"
        f"   AND left(code,3) IN ({_ph()}))"
        " SELECT count(*) FILTER (WHERE cont_days = th AND is_exchange) = 1"
        "        AND max(th) >= %s AS has FROM t",
        (trade_date, *CONFIG.BOARD_PREFIXES, CONFIG.MIN_LEADER_DAYS))
    return bool(df["has"].iloc[0]) if not df.empty else False


def indicators(trade_date: date, hist: dict | None = None) -> EmotionDay:
    """单日指标（不含 phase，phase 由 classify 推进）。

    注入：自适应炸板阈值、候选守卫、双口径高度（名义/可交易）、一字占比、加速事实。
    判据列 zt_performance 按 EMOTION_PERF_BASIS 取均值或中位数（F5），
    两个值都算并各自入库，便于并跑对比。

    A1（审计 P1-1）：hist 传入时滚动基线纯内存计算，不读 market_stat——
    hist = {"bomb_rates": [...], "onewords": [...]}（最新在前，可为空）。
    None 时回落读库（兼容单日调试；增量模式只读目标区间外旧值，无污染）。

    V3（二轮审计）：derived_bar 当日整体缺失（行数 0）→ 显式数据不可用
    分支：classify 只继承昨日 phase 并打 ·缺数据 标记，不再误走
    「冰点触发」（零涨停被当冷却市况判定）。
    """
    n_rows = int(query_df(
        "SELECT count(*) n FROM derived_bar WHERE date = %s",
        (trade_date,))["n"].iloc[0])
    if n_rows == 0:
        return EmotionDay(date=trade_date, data_missing=True)
    prev = _prev_day(trade_date)
    c = _counts(trade_date)
    height, amp = _top_amplitude(trade_date)
    mean, med = _zt_perf(trade_date, prev)
    hit, reason, facts = accelerate.detect(
        trade_date, hist["onewords"] if hist else None)
    touched = c["touched"] or 0
    return EmotionDay(
        date=trade_date, limit_up_count=int(c["zt"] or 0),
        # V3（二轮审计，F6 禁补 0）：分母 0（无人触板）→ None 而非假 0.0——
        # 假 0 会滚进 30 日自适应阈值（数据缺口→假阈值→误判高潮传导链）
        bomb_rate=(c["zb"] or 0) / touched if touched else None,
        zt_performance=med if _EMOTION_PERF_BASIS == "median" else mean,
        zt_performance_mean=mean, zt_performance_median=med,
        max_limit_days=height, limit_down_count=int(c["dt"] or 0),
        top_amplitude=amp, top_broke=_top_broke(trade_date, prev),
        bomb_threshold=_bomb_threshold(
            trade_date, hist["bomb_rates"] if hist else None),
        has_candidate=_has_candidate(trade_date),
        tradable_max_days=facts.get("tradable_h"),
        oneword_ratio=facts.get("oneword_ratio"),
        accelerate=hit, accel_reason=reason, accel_facts=facts)


def _neg_feedback(t: EmotionDay, y: EmotionDay | None) -> int:
    """高位负反馈计数（vs 昨日）：断板/高度降/炸板升/表现降/跌停升，0~5。

    ★F6：None 项不计入——缺数据既不算"负反馈发生"也不算"没发生"。
    """
    if y is None:
        return 0
    perf_down = (t.zt_performance is not None and y.zt_performance is not None
                 and t.zt_performance < y.zt_performance)
    return (int(bool(t.top_broke))
            + int(t.max_limit_days < y.max_limit_days)
            + int(_gt(t.bomb_rate, y.bomb_rate))    # V3：None 不计数
            + int(perf_down)
            + int(t.limit_down_count > y.limit_down_count))


def _fmt(v: float | None, prec: int = 1) -> str:
    """判据值展示：None → '—'（F6：不印 0.0 假装数据正常）。"""
    return "—" if v is None else f"{v:.{prec}f}" if prec else str(v)


def _as_days(series: list[EmotionDay] | pd.DataFrame) -> list[EmotionDay]:
    """入参归一：list[EmotionDay] 直通；DataFrame 按 _COLS 列名逐行装配。

    工单签名 classify(series: pd.DataFrame) 的落点：从已落库的 market_stat 形状
    帧重推相位（列名同 _COLS；缺失列取 EmotionDay 默认值，NaN 归一为 None）。
    """
    if not isinstance(series, pd.DataFrame):
        return list(series)
    days: list[EmotionDay] = []
    for rec in series.to_dict("records"):
        vals: dict[str, Any] = {}
        for key in _COLS:
            if key not in rec:
                continue
            val = rec[key]
            if isinstance(val, float) and math.isnan(val):
                val = None
            if key == "date":
                if isinstance(val, str):
                    val = date.fromisoformat(val)
                elif isinstance(val, datetime):
                    val = val.date()      # pandas Timestamp/datetime → date（口径统一）
            vals[key] = val
        days.append(EmotionDay(**vals))
    return days


def classify(series: list[EmotionDay] | pd.DataFrame) -> list[EmotionDay]:
    """按优先级逐日推进状态机（纯函数），并叠加 diverge / accelerate 标记。

    规则判定复用 state._RULES（优先级序列与阈值判据的单一实现，已过 661 天
    oracle 对账）；本函数负责 lkl classify 的时序语义：seed 不重判（W1：
    seed 是昨日落库 phase 的权威快照——规则引擎不得用占位默认值重判覆盖）、
    缺数据只继承（V3）、负反馈计数、高潮分歧降级、加速叠加（F2 事件非状态）、
    ·延续/·首日基线/·反转 标记（R2：延续日不摆当日数字冒充触发依据）。

    加速为事件（F2）：命中只打标记；仅 ACCEL_ENFORCE=True 才压 buy_window=NONE。
    """
    days = _as_days(series)
    for i, t in enumerate(days):
        if t.is_seed:
            continue          # W1：seed 是昨日落库 phase 的权威快照——规则引擎
        y = days[i - 1] if i >= 1 else None     # 不得用占位默认值重判覆盖
        b = days[i - 2] if i >= 2 else None
        if t.data_missing:                      # V3：数据不可用显式分支——
            t.phase = y.phase if y else "冰点"   # 只继承，不触发任何规则（此前
            t.buy_window, t.force_liquidate = window_of(t.phase)  # 0涨停被误判冰点）
            t.phase_inherited = y is not None
            t.reason = "·缺数据（derived_bar 当日整体缺失，phase 仅延续）"
            continue
        fired = next(((rule, name) for rule, name in _RULES if rule(t, y, b)),
                     None)
        phase = fired[1] if fired else (y.phase if y else "冰点")
        t.phase = phase
        t.buy_window, t.force_liquidate = window_of(phase)
        t.neg_feedback = _neg_feedback(t, y)
        # R2：延续=当日无规则命中（phase 值相同但规则命中≠延续——
        # 如昨日冰点今日反转再确认，phase 同为冰点但是触发）
        inherited = fired is None and y is not None
        first_day = fired is None and y is None    # V3：首日无历史——
        tag = "·首日基线" if first_day else ("·延续" if inherited else "")
        if phase == "冰点" and not inherited and not first_day:
            rev = (y is not None and _lt(y.zt_performance, 0)
                   and _gt(t.zt_performance, 0))
            tag += "·反转" if rev else ("·无候选放宽" if not t.has_candidate
                                        else "·候选守卫")
        elif phase == "高潮" and t.neg_feedback >= CONFIG.DIVERGE_NEG_MIN:
            t.diverge = True
            t.buy_window = CONFIG.DIVERGE_WINDOW
            tag += f"·高位分歧降级(nb={t.neg_feedback})"
        if t.accelerate:
            tag += "·加速" + ("降级" if _ACCEL_ENFORCE else "(影子)")
            if _ACCEL_ENFORCE:
                t.buy_window = "NONE"
        t.reason_tag = tag                     # R2：tag 存档供 reason/报告复用
        t.phase_inherited = inherited
        t.reason = _phase_reason(t, inherited)
    return days


def _phase_reason(t: EmotionDay, inherited: bool) -> str:
    """R2：延续日不摆当日数字冒充触发依据（拆函数保持 classify ≤50 行）。"""
    nums = (f"涨停{t.limit_up_count} 炸率{_fmt(t.bomb_rate)}"
            f"/阈{t.bomb_threshold:.2f} 昨涨停表现{_fmt(t.zt_performance)}"
            f" 名义{t.max_limit_days}板/可交易{_fmt(t.tradable_max_days, 0)}板"
            f" 跌停{t.limit_down_count}")
    if inherited:
        return (f"{t.phase}（延续：当日无规则触发，数字仅参考）"
                f"{t.reason_tag}({nums})")
    return f"{t.phase}{t.reason_tag}({nums})"


def _r(v: float | None, prec: int = 4) -> float | None:
    """round 但 None 透传（F6：缺失不写成 0）。"""
    return None if v is None else round(v, prec)


# market_stat 的情绪列（列序对齐 lkl _COLS；dragon_env* 三列归 dragon_env.py，不在本表）
_COLS = ("date", "limit_up_count", "bomb_rate", "zt_performance",
         "max_limit_days", "limit_down_count", "phase", "buy_window",
         "force_liquidate", "reason", "top_amplitude", "top_broke",
         "bomb_threshold", "has_candidate", "neg_feedback", "diverge",
         "zt_performance_mean", "zt_performance_median", "tradable_max_days",
         "oneword_ratio", "accelerate", "accel_reason", "phase_inherited")

_UPSERT_SQL = (
    f"INSERT INTO market_stat ({', '.join(_COLS)})"
    f" VALUES ({', '.join(['%s'] * len(_COLS))})"
    " ON CONFLICT (date) DO UPDATE SET "
    + ", ".join(f"{c}=EXCLUDED.{c}" for c in _COLS if c != "date"))


def _row(s: EmotionDay) -> tuple:
    """EmotionDay → market_stat 行元组（列序对齐 _COLS）。"""
    return (s.date, s.limit_up_count, _r(s.bomb_rate), _r(s.zt_performance),
            s.max_limit_days, s.limit_down_count, s.phase, s.buy_window,
            s.force_liquidate, s.reason, _r(s.top_amplitude), s.top_broke,
            None if s.bomb_threshold == float("inf") else _r(s.bomb_threshold),
            s.has_candidate, s.neg_feedback, s.diverge,
            _r(s.zt_performance_mean), _r(s.zt_performance_median),
            s.tradable_max_days, s.oneword_ratio, s.accelerate, s.accel_reason,
            s.phase_inherited)


def _upsert_rows(rows: list[tuple]) -> int:
    """market_stat 幂等 upsert，返回写入行数（lkl utils/db.upsert_rows 写语义，见文档 §3）。"""
    if not rows:
        return 0
    # P2（三轮审计）：行宽校验防错位静默写脏数据
    if len(rows[0]) != len(_COLS):
        raise ValueError(f"upsert market_stat：行宽 {len(rows[0])} != 列数 "
                         f"{len(_COLS)}")
    # 统一消毒：NaN / ±inf → NULL（PG numeric 可存 NaN/Inf，会击穿下游 ::bigint 转换）
    clean = [tuple(None if isinstance(v, float) and not math.isfinite(v) else v
                   for v in r) for r in rows]
    with transaction() as conn, conn.cursor() as cur:
        for i in range(0, len(clean), _UPSERT_BATCH):
            cur.executemany(_UPSERT_SQL, clean[i:i + _UPSERT_BATCH])
    return len(clean)


def _warmup_start(start: date, n: int) -> date:
    """向前回退 n 个交易日，作为状态机 warm-up 起点（等价单查询，见文档 §4）。"""
    df = query_df("SELECT DISTINCT date FROM daily_bar WHERE date < %s"
                  " ORDER BY date DESC LIMIT %s", (start, n))
    if df.empty:
        return start
    return min(df["date"])


def _rolling_days(trade_date: date, window: int) -> list[date]:
    """trade_date 之前 window 个交易日（升序；等价单查询，见文档 §4）。"""
    df = query_df("SELECT DISTINCT date FROM daily_bar WHERE date < %s"
                  " ORDER BY date DESC LIMIT %s", (trade_date, window))
    if df.empty:
        return []
    return sorted(df["date"])


def _seed_and_hist(start: date) -> tuple[EmotionDay | None, dict]:
    """增量模式的播种与滚动基线：只读 **start 之前** 的旧库值。

    A1/A2（审计 P1-1/2）：禁读待重算区间 [start,end] ——区间外的既住数据
    是只读基线，本轮重算不可能污染它；phase 继承链由 seed（昨日已落库
    phase）显式续接，不再依赖固定 warm-up 猜链头。
    """
    prev = _prev_day(start)
    seed = None
    if prev is not None:
        df = query_df(
            "SELECT date, phase, bomb_rate, oneword_ratio FROM market_stat"
            " WHERE date = %s", (prev,))
        if not df.empty and df["phase"].iloc[0]:
            r = df.iloc[0]
            seed = EmotionDay(
                date=r["date"], phase=r["phase"],
                bomb_rate=r["bomb_rate"], oneword_ratio=r["oneword_ratio"],
                is_seed=True)      # W1：标记后 classify 不再对其执行规则判定
    prior = _rolling_days(start, max(_BOMB_WINDOW, _ACCEL_BASELINE_WINDOW))
    df = (query_df(
        "SELECT date, bomb_rate, oneword_ratio FROM market_stat"
        " WHERE date < %s AND date >= %s ORDER BY date DESC",
        (start, prior[0])) if prior else pd.DataFrame())
    bomb_rates = (df["bomb_rate"].tolist() if not df.empty else [])
    onewords = (df["oneword_ratio"].tolist() if not df.empty else [])
    return seed, {"bomb_rates": bomb_rates, "onewords": onewords}


def run_range(start: date, end: date, warmup: int | None = None) -> int:
    """增量模式（cron 每日）：区间指标 -> classify -> 幂等 upsert [start,end]。

    A2（审计 P1-2）：phase 继承链由 _seed_and_hist 显式续接昨日已落库
    phase，warm-up 仅补充 y/b 类状态依赖（发酵需两日前、负反馈需一日前），
    链头不再靠"5 日窗口碰运气"。
    W1（二轮审计 P0-2）：滚动基线逐日前插（与 replay 同构）——此前 hist
    是 days[0] 的快照 dict 全区间冻结，与 replay 不等价，同一天可判出
    不同 phase；上轮「增量=replay」验收是 30 日中位数恰好未移动的巧合。
    """
    wu = _EMOTION_WARMUP if warmup is None else warmup
    days = trading_days(_warmup_start(start, wu), end)
    if not days:                       # A8：无行情区间（假期回填/未来日期）早退
        log.warning("market_stat %s~%s：区间无交易日，跳过", start, end)
        return 0
    seed, hist = _seed_and_hist(days[0])
    head = [seed] if seed is not None else []
    states: list[EmotionDay] = []
    for d in days:                      # W1：逐日滚动，算完一天把当日值前插
        s = indicators(d, hist)         # V3：None（分母0日）不入窗口（F6）
        states.append(s)
        if s.bomb_rate is not None:
            hist["bomb_rates"].insert(0, s.bomb_rate)
        if s.oneword_ratio is not None:
            hist["onewords"].insert(0, s.oneword_ratio)
    series = classify(head + states)
    target = [s for s in series if s.date >= start]
    n = _upsert_rows([_row(s) for s in target])
    log.info("market_stat %s~%s：%d 日（增量，seed=%s，自 %s）",
             start, end, n, seed.phase if seed else "无", days[0])
    return n


def persist(trade_date: date) -> None:
    """单日落库：写入 market_stat（phase/buy_window/force_liquidate 及其余情绪列）。

    增量模式最小粒度 = run_range(trade_date, trade_date)：内部仍向前取
    EMOTION_WARMUP 日作前情（F8：单日 series 会让 y/b 全为 None，发酵/退潮
    two_day/负反馈分支静默失效），只回写 trade_date 一行。返回行数用 run_range。
    """
    run_range(trade_date, trade_date)


def replay(start: date, end: date) -> int:
    """全量重放（回填/重算/审计用）：从 DATA_START 纯内存滚动推进。

    A1（审计 P1-1）可重复性保证：炸板阈值与一字占比基线全部从内存序列
    滚动计算，**全程不读 market_stat**——同一份 derived_bar 无论空库、
    旧库重放多少遍，结果恒一致。只回写 [start,end]。
    """
    days = trading_days(CONFIG.DATA_START, end)
    if not days:
        log.warning("replay %s~%s：DATA_START 起无交易日", start, end)
        return 0
    bomb_rates: list = []              # 最新在前（滚动窗口）
    onewords: list = []
    states: list[EmotionDay] = []
    for d in days:
        s = indicators(d, {"bomb_rates": bomb_rates, "onewords": onewords})
        states.append(s)
        if s.bomb_rate is not None:      # V3：None 不入滚动窗口（F6）
            bomb_rates.insert(0, s.bomb_rate)
        if s.oneword_ratio is not None:
            onewords.insert(0, s.oneword_ratio)
    series = classify(states)
    target = [s for s in series if start <= s.date <= end]
    n = _upsert_rows([_row(s) for s in target])
    log.info("market_stat replay %s~%s：%d 日（全量重放自 %s，零读库基线）",
             start, end, n, days[0])
    return n
