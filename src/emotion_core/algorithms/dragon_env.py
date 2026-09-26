"""T16 生态可用性评级（PLAN §1.12 / F4）：龙空龙策略能否在当前生态工作。

语义逐字照搬 lkl/services/dragon_env.py（docs/07 §3.13、docs/11 §3.1「生态评级」）：
判定树、阈值方向、UNKNOWN 规则一律照抄，未增删任何条件。

语义与 buy_window 正交（风险登记 13）：本模块评「生态可不可干」，buy_window 是
「今天给不给买」。两者可能矛盾，报告须分别标注，禁止合并表述。

有利 G1~G4 需多数成立；不利 B1~B5 触发任一即 UNFAVORABLE。
★F6：数据不足的条件返回 None（UNKNOWN），不计入有利也不触发不利——宁降档不猜。

判据：
  有利 G1 可交易高度扩张  近 N 日换手最高板 H 单调不降且至少一日上升
  有利 G2 主线梯队完整    主题材库**存在**成员数达标的主线题材（首板维度未实现 → UNKNOWN）
  有利 G3 断板负反馈温和  最高层晋级失败股近 N 日平均跌幅 > 阈值
  有利 G4 胜者上方有空间  H <= 近 N 日最高 - HEADROOM
  不利 B1 加速事件命中    加速事件命中（accelerate.detect）
  不利 B2 高度靠一字制造  A3 命中且当日 promotion_day 最大背离达标
  不利 B3 胜出次日核按钮  近 N 日 sole_top 次日平均跌幅 < 阈值
  不利 B4 无板块支持      **换手最高板组**各票题材全孤立
  不利 B5 多高标跨题材    同身位组可信题材唯一值 >1
裁决（_verdict）：任一不利成立 → UNFAVORABLE；有有利被明确证伪 → NEUTRAL；
核心条件（DRAGON_CORE_GOODS = G1/G4）全真且无不利成立 → FAVORABLE；否则 NEUTRAL。

★口径（逐条对应 lkl 注释里的历次修复，不得简化）：
- G2 是「存在性」判定（审计 P1-4①）：达标题材中取高度第一者，而非只看最高度题材；
  首板维度落地前只按成员数判定（②），命中即记 UNKNOWN——不证伪、不成立。
- B4 走「换手最高板组」而非「highest_board 第一名」（R3 拍板 a）：板块支持跟着可交易
  标的走，一字孤标不得否决整个生态。
- B5 置信度加权（V5）：conf < 0.6 的弱映射视同无标注，不参与 DISTINCT 计数。
- B3 预览/复盘分离（V5 P1 前视，奎爷拍板①a）：live 模式次日收盘尚不存在，nxt 恒 NULL
  → UNKNOWN，不拿未来数据当判据；replay 放宽上界 +3 自然日（读到「次日收盘」即 B3
  语义本身），报告须标注复盘态——实证：库里 661 日 B3 note 均带「复盘态」字样。

与 lkl 的差异（全部是契约/IO 适配，不涉判定规则）：
1. `lkl.utils.db.query_df` → `emotion_core.utils.db.query_df`（同签名：sql, params → DataFrame）。
2. persist 的 UPDATE 走 `data/loader.py::update_market_stat_ecosystem`（同一语句、同一字段；
   jsonb 列由 psycopg 传 text 参数，Postgres 走 I/O assignment cast，已实测通过）。**写入口**
   集中在 loader，本模块不自持任何写 SQL；loader 亦为本模块预留了该入口。
   读 SQL 则自持 11 处（lkl 原语句逐字）：loader 只有 derived_bar/ladder_day/market_stat 的
   领域化入口，theme_group/theme_tag/promotion_day 聚合读与其形状不符，与 entry.py /
   promotion.py / accelerate.py 同先例——待 loader 补齐对应读入口后下沉。
3. CONFIG 尚未收录 DRAGON_* 键，经 `_cfg` 取 lkl config.py 原值默认（accelerate.py 同先例）；
   键一旦进 utils/config.py 自动生效（本次范围限本文件 + test_dragon_env.py 两文件）。
4. `lkl.services.accelerate.detect` → `emotion_core.algorithms.accelerate.detect`（同三元组契约）。
5. 文件/模块名取本工单指定的 `dragon_env.py`（docs/11 §3.1 曾写作 `ecosystem.py`）。
6. 新增 `ladder_health` / `promotion_strength` 两个**只读汇总视图**（工单要求）：不设阈值、
   不参与评级、不写库，值域全部来自 ladder_day / promotion_day 已落库事实；评级仍只由
   g1~g4 / b1~b5 与 _verdict 决定。rate 的签名与返回值照 lkl（评级词汇实证为
   FAVORABLE/NEUTRAL/UNFAVORABLE 三值，见 market_stat.dragon_env），不作 A/B/C/D 改写。

对账：tests/unit/test_dragon_env.py（无 DB，纯逻辑与降级分支）；另用真实库差分对账——
同一交易日分别调 lkl.services.dragon_env 与本模块同名函数，逐值比对。
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

import pandas as pd

from emotion_core.algorithms import accelerate
from emotion_core.data.loader import update_market_stat_ecosystem
from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df

log = logging.getLogger(__name__)

Status = bool | None                 # True 成立 / False 不成立 / None 证据不足


def _cfg(name: str, default: Any) -> Any:
    """读 CONFIG 策略常量；emotion-core 尚未收录的键取 lkl config.py 原值（见模块文档 §3）。"""
    return getattr(CONFIG, name, default)


DRAGON_CORE_GOODS: tuple[str, ...] = _cfg("DRAGON_CORE_GOODS", ("G1", "G4"))
DRAGON_H_EXPAND_DAYS: int = _cfg("DRAGON_H_EXPAND_DAYS", 3)
DRAGON_HEIGHT_REF_WINDOW: int = _cfg("DRAGON_HEIGHT_REF_WINDOW", 20)
DRAGON_G4_HEADROOM: int = _cfg("DRAGON_G4_HEADROOM", 1)
DRAGON_FEEDBACK_LOOKBACK: int = _cfg("DRAGON_FEEDBACK_LOOKBACK", 5)
DRAGON_G3_MIN_PERF: float = _cfg("DRAGON_G3_MIN_PERF", -3.0)
DRAGON_B3_MAX_PERF: float = _cfg("DRAGON_B3_MAX_PERF", -7.0)
DRAGON_B2_DIVERGENCE: float = _cfg("DRAGON_B2_DIVERGENCE", 0.3)
DRAGON_G2_MIN_MEMBERS: int = _cfg("DRAGON_G2_MIN_MEMBERS", 3)


def _series(end: date, n: int, exchange_only: bool) -> list[int]:
    """近 n 交易日主板最高板序列（exchange_only=True 取换手口径 H）。"""
    col = "max(CASE WHEN is_exchange THEN cont_days END)" if exchange_only \
        else "max(cont_days)"
    df = query_df(
        f"SELECT date, {col} h FROM derived_bar"
        " WHERE cont_days >= 1 AND left(code, 3) IN ("
        + ", ".join(["%s"] * len(CONFIG.BOARD_PREFIXES)) + ")"
        " AND date <= %s GROUP BY date ORDER BY date DESC LIMIT %s",
        (*CONFIG.BOARD_PREFIXES, end, n))
    return [int(v) for v in reversed(df["h"].dropna().tolist())]


def g1_height_expanding(trade_date: date) -> tuple[Status, str]:
    """G1 可交易高度扩张：近 N 日 H 单调不降且至少一日上升。"""
    hs = _series(trade_date, DRAGON_H_EXPAND_DAYS, True)
    if len(hs) < DRAGON_H_EXPAND_DAYS:
        return None, f"H 序列仅 {len(hs)} 日，不足 {DRAGON_H_EXPAND_DAYS}"
    ok = all(b >= a for a, b in pairwise(hs)) and hs[-1] > hs[0]
    return ok, f"H {'→'.join(map(str, hs))}"


def g4_headroom(trade_date: date) -> tuple[Status, str]:
    """G4 胜者上方有空间：H <= 近 N 日最高 - HEADROOM。"""
    hs = _series(trade_date, DRAGON_HEIGHT_REF_WINDOW, True)
    if len(hs) < DRAGON_HEIGHT_REF_WINDOW // 2:
        return None, f"参照窗口仅 {len(hs)} 日"
    h, ref = hs[-1], max(hs)
    ok = h <= ref - DRAGON_G4_HEADROOM
    return ok, f"H={h} 近{DRAGON_HEIGHT_REF_WINDOW}日最高={ref}"


def g2_theme_ladder(trade_date: date) -> tuple[Status, str]:
    """G2 存在梯队完整主线：主题材库中**存在**成员数达标的主线题材。

    ★A3 修复两层（审计 P1-4）：
    1) 原 SQL 只看 highest_board 最高的单一题材（如 09-01：O2O 高度第一
       但成员 1），把「存在微盘股成员 3 达标」误判成 False——语义应为存在性，
       改为达标题材中取最高度者。
    2) theme P1 口径硬编码 first_board_count=0（首板属 P1.5），首板条件
       恒不可满足——若照常判定即恒 False，在「有利被证伪→NEUTRAL」规则下
       FAVORABLE 将随 theme 积累归零。故首板口径落地前只按成员数判定，
       首板条件返回 UNKNOWN（不证伪、不成立）。
    """
    df = query_df(
        "SELECT theme, member_count, first_board_count, highest_board"
        " FROM theme_group WHERE date = %s"
        " AND member_count >= %s"
        " ORDER BY highest_board DESC, member_count DESC LIMIT 1",
        (trade_date, DRAGON_G2_MIN_MEMBERS))
    if df.empty:
        top = query_df(
            "SELECT theme, member_count FROM theme_group WHERE date = %s"
            " ORDER BY highest_board DESC, member_count DESC LIMIT 1",
            (trade_date,))
        if top.empty:
            return None, "theme_group 当日无数据"
        return False, (f"最强题材 {top.iloc[0]['theme']} 成员"
                       f"{int(top.iloc[0]['member_count'] or 0)}"
                       f"（<{DRAGON_G2_MIN_MEMBERS}，无达标主线）")
    r = df.iloc[0]
    # V5（二轮审计）：文案方向修正——成员达标 ≠ 无数据；首板维度未实现是
    # 唯一缺口，不得让读者误以为整个 G2 无数据
    return None, (f"主线题材 {r['theme']} 成员{int(r['member_count'])} 达标"
                  f"（首板维度未实现 P1.5，首板完整性不计——G2 记 UNKNOWN"
                  " 不证伪，非「尚无数据」)")


def g3_break_feedback(trade_date: date) -> tuple[Status, str]:
    """G3 断板负反馈温和：最高层晋级失败股近 N 日平均跌幅 > 阈值。"""
    df = query_df(
        "SELECT fail_perf FROM promotion_day"
        " WHERE date <= %s AND fail_perf IS NOT NULL"
        " AND layer = (SELECT max(layer) FROM promotion_day p2 WHERE p2.date <= %s)"
        " ORDER BY date DESC LIMIT %s",
        (trade_date, trade_date, DRAGON_FEEDBACK_LOOKBACK))
    if len(df) < 2:
        return None, f"断板反馈样本 {len(df)} 不足"
    avg = round(float(df["fail_perf"].astype(float).mean()), 2)
    return avg > DRAGON_G3_MIN_PERF, f"最高层失败股均涨幅 {avg}%"


def b1_acceleration(trade_date: date, accel_hit: bool) -> tuple[Status, str]:
    """B1 加速事件命中（§1.11）。"""
    return accel_hit, "加速事件命中" if accel_hit else "无加速事件"


def b2_oneword_made(trade_date: date, a3_hit: bool) -> tuple[Status, str]:
    """B2 名义高度主要由一字制造：A3 命中且当日最大背离达标。"""
    if not a3_hit:
        return False, "A3 未命中"
    df = query_df(
        "SELECT max(divergence) d FROM promotion_day WHERE date = %s", (trade_date,))
    d = df["d"].iloc[0]
    if d is None or pd.isna(d):
        return None, "背离度无数据"
    d = float(d)
    return d >= DRAGON_B2_DIVERGENCE, f"最大背离 {d:.2f}"


def _tradable_theme_members(trade_date: date) -> list[tuple[str, str | None, int]]:
    """R3：换手最高板组各票题材 → (code, theme, member_count)。

    换手板口径与 ladder.top_group 一致（is_exchange 且最高 cont_days）；
    成员数取该题材当日 theme_group.member_count。
    """
    df = query_df(
        "SELECT l.code, COALESCE(s.name, l.code) name, t.primary_theme theme,"
        " g.member_count mc FROM ladder_day l"
        " LEFT JOIN theme_tag t ON t.date = l.date AND t.code = l.code"
        " LEFT JOIN theme_group g ON g.date = t.date AND g.theme = t.primary_theme"
        " LEFT JOIN stock_basic s ON s.code = l.code"
        " WHERE l.date = %s AND l.is_exchange = true"
        " AND l.cont_days = (SELECT max(cont_days) FROM ladder_day"
        "                    WHERE date = l.date AND is_exchange = true)",
        (trade_date,))
    if df.empty:
        return []
    def _mc(v):                          # R3：NaN/None 统一 0（防御 join 空档）
        return int(v) if v is not None and not pd.isna(v) else 0
    def _th(v):                          # R3：pandas NaN 归一 None——NaN
        return None if v is None or pd.isna(v) else str(v)  # truthy 绕过 UNKNOWN
    return [(str(r["code"]), _th(r["theme"]), _mc(r["mc"]))
            for _, r in df.iterrows()]


def b4_no_sector(trade_date: date) -> tuple[Status, str]:
    """B4 无板块支持（R3 奎爷拍板 a）：**换手最高板组**各自题材全孤立。

    旧口径（highest_board DESC 第一名）让一字孤标（如 09-01 海鸥住工
    O2O，置信 0.4）否决整个生态——但龙空龙可参与的标的是换手板，
    板块支持应跟着可交易标的走。新口径：换手最高板组每只票的题材
    成员数均 ≤1 才成立；任一票有板块跟随即不成立。
    """
    rows = _tradable_theme_members(trade_date)
    if not rows:
        return None, "换手最高板组无数据（梯队断层或 theme 未跑）"
    supported = [(c, th, mc) for c, th, mc in rows if th and mc >= 2]
    if supported:
        c, th, mc = max(supported, key=lambda r: r[2])
        return False, f"换手最高板 {c}({th}) 成员{mc}——板块有跟随"
    no_theme = [c for c, th, _ in rows if not th]
    if no_theme:
        return None, (f"换手最高板 {','.join(no_theme)} 无题材标签"
                      "（theme 未覆盖），B4 记 UNKNOWN")
    parts = [f"{c}({th or '—'})成员{mc}" for c, th, mc in rows]
    return True, "换手最高板全孤立：" + "、".join(parts)


def b5_cross_theme(trade_date: date) -> tuple[Status, str]:
    """B5 多高标跨题材：同身位组 primary_theme 唯一值 >1 → 淘汰是板数巧合。

    V5（二轮审计）：置信度加权——conf<0.6（孤标签弱映射 0.4）视同无标注，
    不参与 DISTINCT 计数：避免一条 0.4 弱概念映射制造虚假「跨题材」。
    0.85 公告催化与 0.6 概念（成员≥3）为可信区间。
    """
    df = query_df(
        "SELECT count(DISTINCT t.primary_theme) n, count(*) c,"
        " count(*) FILTER (WHERE t.confidence >= 0.6) c_ok"
        " FROM ladder_day l LEFT JOIN theme_tag t"
        "   ON t.code = l.code AND t.date = l.date"
        " WHERE l.date = %s AND l.is_top", (trade_date,))
    n, c = int(df["n"].iloc[0] or 0), int(df["c"].iloc[0] or 0)
    c_ok = int(df["c_ok"].iloc[0] or 0)
    if c == 0:
        return None, "当日无同身位组"
    if c_ok == 0:
        return None, (f"同身位 {c} 只均无可信题材标注"
                      "（confidence<0.6 弱映射不计），B5 记 UNKNOWN")
    return n > 1, f"同身位 {c} 只（可信标注 {c_ok}）跨 {n} 题材"


_B3_SQL = """
WITH tops AS (
  SELECT date, code FROM ladder_day
   WHERE is_sole_top AND date <= %s ORDER BY date DESC LIMIT %s),
nxt AS (
  SELECT d.code, d.date, d.close,
         lead(d.close) OVER (PARTITION BY d.code ORDER BY d.date) AS nc
  FROM daily_bar d
  WHERE d.code IN (SELECT code FROM tops) AND d.date <= %s)
SELECT avg(n.nc / n.close - 1) * 100 p, count(n.nc) c
FROM tops t JOIN nxt n ON n.code = t.code AND n.date = t.date"""


def b3_next_day_dump(trade_date: date, mode: str = "live") -> tuple[Status, str]:
    """B3 胜出次日即核按钮：近 N 日 sole_top 次日平均跌幅 < 阈值。

    V5（二轮审计 P1 前视，奎爷拍板①a 预览/复盘分离）：
    - live（当日运行）：nxt 加 date <= trade_date 上界——次收盘尚不存在，
      lead 恒 NULL → B3=None（UNKNOWN），不拿未来数据当判据；
    - replay（历史回填）：上界放宽 trade_date + 1 日（读到"次日收盘"即
      B3 语义本身），但当日实盘不可见——报告标注复盘态。
    """
    bound = trade_date if mode == "live" else trade_date + timedelta(days=3)
    df = query_df(_B3_SQL,
                  (trade_date, DRAGON_FEEDBACK_LOOKBACK, bound))
    p, c = df["p"].iloc[0], int(df["c"].iloc[0] or 0)
    if c < 2 or p is None or pd.isna(p):
        return None, f"胜出者次日样本 {c} 不足"
    p = round(float(p), 2)
    note = (f"近{c}次胜出次日均涨幅 {p}%"
            + ("（复盘态：读到次日收盘，当日实盘不可见）" if mode != "live" else ""))
    return p < DRAGON_B3_MAX_PERF, note


def _is_core(name: str) -> bool:
    """核心条件判定：G1/G4 纯行情可算、历史全覆盖；G2/G3 为增强条件。"""
    return name[:2] in DRAGON_CORE_GOODS


def _verdict(goods: list, bads: list) -> str:
    """分级裁决（B 方案）：核心全真 + 无不利成立 + 无有利被证伪 → FAVORABLE。

    增强条件 UNKNOWN 不再永久封死 FAVORABLE——theme_group 于 08-31 才上线，
    要求四条全真会让该档在数据积累前恒不可达（实测 0/645 天）。但也不静默
    当作成立：报告层检出 ok=None 后显式标注"增强条件未验证"，诚实性不丢。
    """
    if any(s is True for _, s, _ in bads):
        return "UNFAVORABLE"
    if any(s is False for _, s, _ in goods):
        return "NEUTRAL"                       # 有有利条件被明确证伪，不够格
    core = [s for n, s, _ in goods if _is_core(n)]
    if core and all(s is True for s in core):
        return "FAVORABLE"
    return "NEUTRAL"


def rate(trade_date: date, accel: tuple[bool, str, dict] | None = None,
         mode: str = "live") -> dict:
    """评级主入口：返回 {rating, goods, bads}，条件逐项带 状态 + 说明。

    accel 可传入 accelerate.detect() 结果复用，避免重复查询（emotion 链路用）；
    工单里写作 `prior` 的那个参数即此 `accel`（lkl 原名，不另设别名）。
    """
    hit, _, facts = accel if accel is not None else accelerate.detect(trade_date)
    goods = [("G1 可交易高度扩张", *g1_height_expanding(trade_date)),
             ("G2 主线梯队完整", *g2_theme_ladder(trade_date)),
             ("G3 断板负反馈温和", *g3_break_feedback(trade_date)),
             ("G4 胜者上方有空间", *g4_headroom(trade_date))]
    bads = [("B1 加速事件", *b1_acceleration(trade_date, hit)),
            ("B2 高度靠一字制造", *b2_oneword_made(trade_date, facts.get("a3", False))),
            ("B3 胜出次日核按钮", *b3_next_day_dump(trade_date, mode)),
            ("B4 无板块支持", *b4_no_sector(trade_date)),
            ("B5 高标跨题材", *b5_cross_theme(trade_date))]
    return {"rating": _verdict(goods, bads), "goods": goods, "bads": bads}


def _ser(items: list) -> list[dict]:
    """[(条件, 状态, 说明)] → jsonb 友好结构；None 状态序列化为 null（F6 可审计）。"""
    return [{"cond": n, "ok": s, "note": t} for n, s, t in items]


def persist(trade_date: date, accel: tuple | None = None,
            mode: str = "live") -> int:
    """评级写回 market_stat（UPDATE；行须已由 emotion.run_range 产出）。"""
    r = rate(trade_date, accel, mode)
    return update_market_stat_ecosystem(
        trade_date, r["rating"],
        json.dumps(_ser(r["goods"]), ensure_ascii=False),
        json.dumps(_ser(r["bads"]), ensure_ascii=False))


def run_range(start: date, end: date) -> int:
    """区间评级：仅处理 market_stat 已有行的交易日，返回成功天数。

    A8（审计 P2 修复）：原写法 `r[0] for r in df["date"]` 遍历的是 Series 的
    date 元素，TypeError——此前 CLI 直接循环 persist 绕过了它，回填 645 日
    "成功"掩盖了坏死路径。改 tolist() 并让 CLI 复用本函数。
    """
    days = query_df(
        "SELECT date FROM market_stat WHERE date BETWEEN %s AND %s"
        " AND tradable_max_days IS NOT NULL ORDER BY date",
        (start, end))["date"].tolist()
    n = sum(1 for d in days if persist(d, mode="replay"))
    log.info("dragon_env %s~%s：%d/%d 日", start, end, n, len(days))
    return n


# ── 只读汇总视图（工单要求；无判据、不进评级、不写库）────────────────


def ladder_health(trade_date: date) -> dict:
    """梯队健康度：连板高度 / 梯队家数 / 断层。

    口径：ladder_day 当日行（主板 7 前缀 + 剔 ST + 剔次新 + cont_days>=2，
    由 ladder.build 落库）。高度分两个口径——`height_nominal` 名义最高板、
    `height_exchange` 换手最高板（龙空龙可交易标的，与 ladder.top_group 同源）。
    `gaps` 是 2~height_exchange 之间无换手成员的板数层（断层）。
    无当日行时高度为 None、`groups` 空、`top_group` 空列表——缺数不当「合法零」。
    """
    df = query_df(
        "SELECT code, cont_days, is_exchange, is_sole_top FROM ladder_day"
        " WHERE date = %s ORDER BY cont_days DESC, code", (trade_date,))
    rows = [(str(r["code"]), int(r["cont_days"]), bool(r["is_exchange"]),
             bool(r["is_sole_top"])) for _, r in df.iterrows()]
    if not rows:
        return {"date": trade_date, "rows": 0, "height_nominal": None,
                "height_exchange": None, "groups": {}, "gaps": [],
                "sole_top": None, "top_group": []}
    groups: dict[int, int] = {}
    for _, cont, _, _ in rows:
        groups[cont] = groups.get(cont, 0) + 1
    nominal = max(groups)
    ex_levels = {cont for _, cont, is_ex, _ in rows if is_ex}
    height_exchange = max(ex_levels) if ex_levels else None
    gaps = ([lv for lv in range(2, height_exchange + 1) if lv not in ex_levels]
            if height_exchange is not None else [])
    sole = next((code for code, _, _, is_sole in rows if is_sole), None)
    top_group = ([code for code, cont, is_ex, _ in rows
                  if is_ex and cont == height_exchange]
                 if height_exchange is not None else [])
    return {"date": trade_date, "rows": len(rows), "height_nominal": nominal,
            "height_exchange": height_exchange,
            "groups": dict(sorted(groups.items(), reverse=True)),
            "gaps": gaps, "sole_top": sole, "top_group": top_group}


def promotion_strength(trade_date: date,
                       prior: list | None = None) -> dict:
    """晋级强度：分层晋级率 + 连板持续性（环比）。

    值域全部来自 promotion_day 已落库事实（名义/换手双口径晋级率、背离、失败负反馈），
    不设阈值、不出结论。`deep_layer` 为换手晋级率有效的最深层（连板高位是否接得上），
    `deep_rate_*` 是其晋级率。

    prior：上一（或前置）日的层行 `list[dict]`，键 `layer` / `rate_exchange`；
    传入时纯内存算环比 `delta_exchange`，不读库（同 accelerate 的 prior_ratios
    可重复性修复）。缺层或缺值 → delta 为 None，不补零。
    """
    df = query_df(
        "SELECT layer, promote_nominal, promote_exchange, rate_nominal,"
        " rate_exchange, divergence, fail_perf FROM promotion_day"
        " WHERE date = %s ORDER BY layer", (trade_date,))
    prior_rates = {str(r["layer"]): r.get("rate_exchange")
                   for r in (prior or [])}
    layers: list[dict] = []
    deep: dict | None = None
    for _, r in df.iterrows():
        layer = str(r["layer"])
        rate_ex = None if pd.isna(r["rate_exchange"]) else float(r["rate_exchange"])
        base = prior_rates.get(layer)
        delta = (round(rate_ex - float(base), 4)
                 if rate_ex is not None and base is not None and not pd.isna(base)
                 else None)
        layers.append({
            "layer": layer,
            "promote_nominal": int(r["promote_nominal"] or 0),
            "promote_exchange": int(r["promote_exchange"] or 0),
            "rate_nominal": None if pd.isna(r["rate_nominal"]) else float(r["rate_nominal"]),
            "rate_exchange": rate_ex,
            "divergence": None if pd.isna(r["divergence"]) else float(r["divergence"]),
            "fail_perf": None if pd.isna(r["fail_perf"]) else float(r["fail_perf"]),
            "delta_exchange": delta,
        })
        if rate_ex is not None:
            deep = layers[-1]
    return {"date": trade_date, "layers": layers,
            "deep_layer": deep["layer"] if deep else None,
            "deep_rate_nominal": deep["rate_nominal"] if deep else None,
            "deep_rate_exchange": deep["rate_exchange"] if deep else None,
            "deep_delta_exchange": deep["delta_exchange"] if deep else None}
