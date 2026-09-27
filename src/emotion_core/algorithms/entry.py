"""T5 买入信号（PLAN §1.5）：五条件 AND + 1 警告；逐项核对产出 checklist。

语义逐字照搬 lkl/services/entry.py（docs/07 §2.4），判定规则零改动：
- c1 唯一换手高标 / c2 换手板非一字 / c3 淘汰赛身份（严格版：幸存集合 == {候选}）
  / c4 最低板数门槛（默认 MIN_LEADER_DAYS=4）/ c5 强度与分歧补偿（仅 ENHANCED 窗口）；
  W1 同身位扎堆只警告不否决（奎爷拍板）。
- 主信号 buy_window=NONE（禁买）日恒不落库；次级（SECONDARY）在禁买日仍生成观察记录，
  且不进入交易导出（SECONDARY_EXPORT_ENABLED 默认关）。
- passed_of 是唯一聚合器：filter None 然后 all()（UNKNOWN 不否决），实现落在
  domain.Checklist.passed——实盘/回放/报告共用同一规则（P1-1）。
- 主信号落库后不再尝试次级；主未通过（含无候选）→ 尝试次级，避免同一
  (confirm_date, code) 同时存在 BUY 与 SECONDARY（lkl V14.1）。

与 lkl 的差异（全部是契约/IO 适配，不涉判定规则）：
1. domain.Checklist 是 frozen dataclass（不是 [(条件, ok, 说明)] 列表）：本模块内部仍逐项
   产出 (标签, ok, 说明) 行（`_rows`），`checklist()` 投影成 Checklist；落库写行——
   JSONB 与 lkl 存量 signal.checklist 同形（展示层按行渲染说明）。
2. W1 极性同 lkl：警告行恒 True（扎堆与否写在说明里，值上不表态）。「警告不否决」的
   机器化保障是 split_checklist 的结构分离（V4），不是靠值表态；恒 True 还避免了
   未来用 all(Checklist 六个字段) 聚合时被警告静默否决（V4 审计的原话）。
3. 候选质量列：lkl 的候选 LadderRow 自带 name/turnover_rate/bomb_times（ladder build 里
   join daily_bar + limit_pool_em），emotion-core 的 domain.LadderDay 只收录梯队 8 列，
   故本模块在 ctx 预取（`_candidate_view`，口径 = lkl ladder build 的 SELECT）；
   c1/c5 优先读 ctx、退化读候选属性——规则不变（lkl ctx 本就是「预取避免重复查询」的载体）。
4. 次级阈值不再改写全局 config（lkl checklist_secondary 临时改 config.DIVERGE_MIN_TURNOVER，
   merge-analysis 判为全局可变状态缺陷）：改为显式参数 ctx["diverge_min"]。
5. CONFIG 尚未收录的键（SECONDARY_DIVERGE_MIN_TURNOVER / REQUIRE_YESTERDAY_COMPETITION /
   POOL_RECENT_DAYS / STRATEGY_VERSION）经 `_cfg` 取 lkl config.py 原值默认；键一旦进
   CONFIG 自动生效（本次范围限 entry.py + test_entry.py 两文件，不动 utils/config.py）。
6. 自持 SQL（lkl 原语句）3 处：data 层现有入口无法服务本模块，均为实测缺陷，与
   promotion.py `_fetch_pairs` 同先例；待 loader 修好后下沉：
   - `_persist`：loader.insert_signal 的 ON CONFLICT (code, confirm_date) 与库上唯一索引
     signal_confirm_date_code_action_key (confirm_date, code, action) 不匹配，调用即
     InvalidColumnReference；
   - `current_window` / `_read_window`：loader.load_market_stat 传 bomb_rate / max_limit_days
     给 domain.MarketStat（契约字段是 bomb_count / max_height），调用即 TypeError；
   - `_candidate_view`：loader 无 ladder 候选质量列查询（lkl ladder.build 的 daily_bar +
     limit_pool_em join）。
7. domain.Signal 无 reason / buy_window 字段：`_persist` 显式收 window（禁买日次级存
   NONE，不伪装成可执行窗口）；reason 不写（展示层文案由 translate/report 生成），
   冲突时不覆写历史 reason。

对账：tests/unit/test_entry.py（逐条件行为，不连库）；另有真实库差分对账——同一候选/窗口
分别调 lkl.services.entry.checklist 与本模块，逐项比对 ok/None。
"""

# ✅ 已有 Rust 实现：src/emotion_core/core/src/entry.rs
# 本文件保留作为参考实现和对账基准，不删除。

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, Callable

from emotion_core.algorithms import ladder
from emotion_core.domain.ladder import LadderDay
from emotion_core.domain.signal import Action, Checklist, Signal, SignalSource
from emotion_core.utils.config import CONFIG, config_hash
from emotion_core.utils.db import connect, transaction
from emotion_core.utils.dates import prev_trading_day, today_sh

log = logging.getLogger(__name__)

Cand = LadderDay
Ctx = dict[str, Any]
Row = tuple[str, bool | None, str]                     # (标签, 通过?, 说明)
Cond = Callable[[Cand, Ctx], "tuple[bool | None, str]"]


def _cfg(name: str, default: Any) -> Any:
    """读 CONFIG 策略常量；emotion-core 尚未收录的键取 lkl config.py 原值（见模块文档 §5）。"""
    return getattr(CONFIG, name, default)


MIN_LEADER_DAYS: int = CONFIG.MIN_LEADER_DAYS                  # lkl MIN_LEADER_DAYS=4
SECONDARY_MIN_LEADER_DAYS: int = CONFIG.SECONDARY_MIN_DAYS      # lkl SECONDARY_MIN_LEADER_DAYS=3
DIVERGE_MIN_TURNOVER: float = CONFIG.EXCHANGE_TURNOVER_MIN      # lkl DIVERGE_MIN_TURNOVER=5.0
SECONDARY_DIVERGE_MIN_TURNOVER: float = _cfg("SECONDARY_DIVERGE_MIN_TURNOVER", 3.5)
REQUIRE_YESTERDAY_COMPETITION: bool = _cfg("REQUIRE_YESTERDAY_COMPETITION", True)
POOL_RECENT_DAYS: int = _cfg("POOL_RECENT_DAYS", 30)


def _field(cand: Cand, ctx: Ctx, key: str, default: Any = None) -> Any:
    """候选质量列：优先 ctx 预取（`_candidate_view`），退化读候选属性（lkl LadderRow 自带）。"""
    value = ctx.get(key)
    return value if value is not None else getattr(cand, key, default)


def c1_uniqueness(cand: Cand, ctx: Ctx) -> tuple[bool, str]:
    ok = ctx["sole"] is not None and cand.code == ctx["sole"].code
    return ok, (f"唯一换手高标: {_field(cand, ctx, 'name', '')}"
                f"({cand.code}) {cand.cont_days}板")


def c2_exchange(cand: Cand, ctx: Ctx) -> tuple[bool, str]:
    return cand.is_exchange, ("换手板(非一字)" if cand.is_exchange
                              else "一字板=网络垄断，无有效投票")


def c3_elimination(cand: Cand, ctx: Ctx) -> tuple[bool, str]:
    """V3→V4→P1-2：集合等值校验——候选必须属于昨日最高组且就是唯一幸存者。

    旧版只查「候选今日换手涨停」，新插队的高板（不在昨日组）也能通过；
    现在要求：候选 ∈ 昨日组 ∧ 幸存集 == {候选}（逐一比对代码，非数量）。"""
    g, s = ctx["y_comp"]
    if not REQUIRE_YESTERDAY_COMPETITION:
        return True, "R2 关闭：不要求昨日竞争"
    if not (g >= 2 and s == 1):
        return False, f"昨日最高板组{g}只→今日幸存{s}只(要求≥2竞争且仅1幸存)"
    y_codes, survivors = ctx["y_surv"]
    if cand.code not in y_codes:
        return False, (f"候选({_field(cand, ctx, 'name', '')})不在昨日最高组内——"
                       "新插队高标，淘汰赛身份不成立")
    if survivors != {cand.code}:
        return False, ("幸存者非候选——昨日组唯一幸存者另有其票，"
                       "候选已被淘汰")
    return True, f"昨日最高板组{g}只→候选胜出（集合等值：幸存=候选本身）"


def _c3_secondary(cand: Cand, ctx: Ctx) -> tuple[bool, str]:
    """次级 c3：从昨日同身位竞争中晋级。

    主版 c3 要求「候选=昨日最高组唯一幸存者」——对降档候选（3板，
    昨日2板梯队）不可能满足：昨日最高组是更高板数，候选不在其中。
    次级放宽为「淘汰赛晋级」语义：昨日 N-1 板换手连板≥2 只（有竞争），
    候选今日晋级 N 板且为唯一换手高标。保留竞争过滤，去掉身位错配。
    """
    if not REQUIRE_YESTERDAY_COMPETITION:
        return True, "R2 关闭：不要求昨日竞争"
    peer = ctx["y_peer"]
    if peer < 2:
        return False, (f"昨日{ctx['min_days'] - 1}板身位换手连板{peer}只"
                       "(要求≥2竞争)")
    return True, (f"昨日{ctx['min_days'] - 1}板身位{peer}只竞争→"
                  f"候选今日晋级{ctx['min_days']}板（次级降档："
                  "不要求昨日最高组唯一幸存）")


def c4_min_days(cand: Cand, ctx: Ctx) -> tuple[bool, str]:
    md = ctx.get("min_days", MIN_LEADER_DAYS)
    return cand.cont_days >= md, f"{cand.cont_days}板 >= 门槛{md}"


def c5_strength_diverge(cand: Cand, ctx: Ctx) -> tuple[bool | None, str]:
    """仅分歧(ENHANCED)窗口要求强度补偿：炸板回封 或 换手率达标。

    F6/A8：换手率缺失 ≠ 0——数据缺失时不能给出"换手不足"的假判定，
    未炸板且无换手数据 → 不确定，按惯例警告不否决（过），但说明里写清缺口。
    V4（二轮审计，奎爷拍板④a）：EM 池窗口外的历史回放——炸板回封腿
    无池数据（恒 False 假象）+ 换手率为当前股本反算，本腿记 None
    （UNKNOWN）**不参与 passed 判定**，标注「不可核验」。
    """
    if ctx["window"] != "ENHANCED":
        return True, "标准窗口不适用"
    if not ctx.get("c5_verifiable", True):
        return None, ("⚠ EM池窗口外：回封腿无池数据、换手率系当前股本反算"
                      "——本条件不可核验(UNKNOWN)，不计入通过/否决")
    threshold = ctx.get("diverge_min", DIVERGE_MIN_TURNOVER)
    bomb_times = _field(cand, ctx, "bomb_times", 0) or 0
    if bomb_times >= 1:
        return True, f"炸板{bomb_times}次回封"
    turnover = _field(cand, ctx, "turnover_rate")
    if turnover is None or (isinstance(turnover, float) and math.isnan(turnover)):
        return True, (f"⚠ 换手率缺失（非 0）——强度补偿无法核验，"
                      f"阈值{threshold}%，建议人工确认")
    ok = float(turnover) >= threshold
    return ok, (f"换手{float(turnover):.2f}%"
                f"{'≥' if ok else '<'}阈值{threshold}%")


# V5（二轮审计「条件编号常量化」）：编号从函数名剥离——此前显示名靠
# fn.__name__[3:] 切片，插/删条件即全体错位；显示名入显式元组，函数名
# 只表达语义。聚合/报告按列表序即编号序，不再依赖字符串切片。
# 三元组 = (判定函数, domain.Checklist 字段名, 落库行标签)。
_CONDITIONS: tuple[tuple[Cond, str, str], ...] = (
    (c1_uniqueness, "c1_uniqueness", "c1 唯一换手高标"),
    (c2_exchange, "c2_exchange", "c2 换手板非一字"),
    (c3_elimination, "c3_elimination", "c3 淘汰赛身份"),
    (c4_min_days, "c4_min_days", "c4 最低板数门槛"),
    (c5_strength_diverge, "c5_strength_diverge", "c5 强度与分歧补偿"),
)

# 次级 checklist：条件同主版，仅 c3 换次级版（阈值经 ctx 放宽，见模块文档 §4）
_SECONDARY_CONDITIONS: tuple[tuple[Cond, str, str], ...] = (
    (c1_uniqueness, "c1_uniqueness", "c1 唯一换手高标"),
    (c2_exchange, "c2_exchange", "c2 换手板非一字"),
    (_c3_secondary, "c3_elimination", "c3 淘汰赛身份"),
    (c4_min_days, "c4_min_days", "c4 最低板数门槛"),
    (c5_strength_diverge, "c5_strength_diverge", "c5 强度与分歧补偿"),
)


def w1_crowding(cand: Cand, ctx: Ctx) -> tuple[bool, str]:
    """警告不否决（奎爷拍板）：绝对最高板≥2只同身位扎堆时，换手高标历史组
    中位 -8.69%/胜率 8%（P3a，n=12 小样本）——提示降仓或放弃，不计否决。

    极性同 lkl：恒 True（警告行不表态），扎堆与否只写说明——落库行保留说明，
    展示层据此渲染；恒 True 也保证任何按值聚合的调用方都不会被警告否决。
    """
    rows = ctx["rows"]
    if not rows:
        return True, "无同身位扎堆"
    h = max(r.cont_days for r in rows)
    n = sum(1 for r in rows if r.cont_days == h)
    if n >= 2:
        return True, (f"⚠ 同身位扎堆：绝对最高 {h}板 {n} 只"
                      f"（历史组中位-8.69%/胜率8%，建议降仓或放弃）")
    return True, "无同身位扎堆"


_WARNINGS: tuple[tuple[Cond, str, str], ...] = (
    (w1_crowding, "w1_crowding", "W1 同身位扎堆"),
)

_FIELD_OF: dict[str, str] = {label: field
                             for _, field, label in _CONDITIONS + _WARNINGS}


@dataclass(frozen=True)
class WarningOnly:
    """W- 警告投影（V4「警告不否决」的结构化保障）。

    split_checklist 把警告从这里单独取出：passed 只看 Checklist 的五条件，
    警告不论返回 True/False 都没有否决权。待 domain 契约层扩列后移入
    domain/signal.py（本次范围限两个文件）。
    """
    w1_crowding: bool | None


_VIEW_SQL = """
SELECT s.name,
       COALESCE(p.turnover_rate, b.turnover_rate) AS turnover_rate,
       p.bomb_times
FROM stock_basic s
LEFT JOIN daily_bar b ON b.code = s.code AND b.date = %(d)s
LEFT JOIN limit_pool_em p ON p.code = s.code AND p.date = %(d)s
     AND p.pool_type = 'ZT'
WHERE s.code = %(code)s
"""


def _candidate_view(cand: Cand) -> dict[str, Any]:
    """候选质量列预取：口径 = lkl ladder.build 的 SELECT（换手率以 daily_bar 兜底、
    炸板次数取东财 ZT 池）。

    缺失即 None（F6：不补零）。daily_bar 缺行时换手率 None → c5 走「无法核验」分支；
    lkl 因 INNER JOIN daily_bar 该候选根本不存在——两边都不产出假判定。
    """
    with connect() as conn:
        row = conn.execute(_VIEW_SQL, {"code": cand.code, "d": cand.date}).fetchone()
    if row is None:
        return {}
    return {"name": row[0] or "",
            "turnover_rate": None if row[1] is None else float(row[1]),
            "bomb_times": None if row[2] is None else int(row[2])}


def _ctx(cand: Cand, window: str, *, min_days: int, diverge_min: float,
         as_of: date | None = None, include_peer: bool = False) -> Ctx:
    """预取 ctx（lkl 同构：ctx 承担全部 IO，条件函数只做纯判定）。

    as_of = 可复现基准日（回放传评估日；缺省 today_sh() 显式化，禁止隐式
    date.today()——同一数据 30 天后重跑必须同一结论，P1-3）。
    """
    rows = ladder.build(cand.date)
    prev = prev_trading_day(cand.date)
    prev_rows = ladder.build(prev) if prev is not None else []
    y_codes = {r.code for r in ladder.top_group(prev_rows)}
    survivors = ladder.y_survivors(cand.date)          # V3：幸存 = 今日仍换手
    base = as_of or today_sh()
    ctx: Ctx = {
        "sole": ladder.sole_top(rows, min_days),
        "window": window,
        "min_days": min_days,
        "diverge_min": diverge_min,
        "y_comp": (len(y_codes), len(survivors)),      # R2 淘汰赛况
        "rows": rows,
        "y_surv": (y_codes, survivors),                # P1-2 集合等值校验
        # V4④a：EM 池窗口外（数据源只有近 POOL_RECENT_DAYS 天）→ c5 不可核验
        "c5_verifiable": cand.date >= base - timedelta(days=POOL_RECENT_DAYS),
    }
    ctx.update(_candidate_view(cand))
    if include_peer:
        ctx["y_peer"] = sum(1 for r in prev_rows
                            if r.cont_days == cand.cont_days - 1 and r.is_exchange)
    return ctx


def _rows(cand: Cand, ctx: Ctx, conditions: tuple[tuple[Cond, str, str], ...]) -> list[Row]:
    """五条件逐行 + 警告行（恒排在最后，报告/落库按此序）。"""
    out = [(label, ok, note) for fn, _, label in conditions
           for ok, note in [fn(cand, ctx)]]
    out += [(label, ok, note) for fn, _, label in _WARNINGS
            for ok, note in [fn(cand, ctx)]]
    return out


def _to_checklist(rows: list[Row]) -> Checklist:
    return Checklist(**{_FIELD_OF[label]: ok for label, ok, _ in rows})


def _evaluate(cand: Cand, window: str, *, min_days: int, diverge_min: float,
              as_of: date | None = None,
              conditions: tuple[tuple[Cond, str, str], ...] = _CONDITIONS,
              include_peer: bool = False) -> tuple[Checklist, list[Row]]:
    """一次评估同时产出 Checklist（判定用）与行（落库/报告用），ctx 只建一次。"""
    ctx = _ctx(cand, window, min_days=min_days, diverge_min=diverge_min,
               as_of=as_of, include_peer=include_peer)
    rows = _rows(cand, ctx, conditions)
    return _to_checklist(rows), rows


def checklist(cand: Cand, window: str, min_days: int | None = None,
              as_of: date | None = None) -> Checklist:
    """五条件逐项核对 + 警告项。min_days 供 R1 参数矩阵。"""
    md = MIN_LEADER_DAYS if min_days is None else min_days
    cl, _ = _evaluate(cand, window, min_days=md, diverge_min=DIVERGE_MIN_TURNOVER,
                      as_of=as_of)
    return cl


def checklist_secondary(cand: Cand, window: str, as_of: date | None = None) -> Checklist:
    """次级 checklist：条件同主版，阈值放宽并换次级 c3。

    放宽项：
    - c3：淘汰赛身份——主版要求「昨日最高组唯一幸存者=候选」；降档候选
      （3板，昨日2板梯队）不在昨日最高组，主版必败。次级改为「昨日同身位
      （N-1板）换手连板≥2只竞争，候选今日晋级」（_c3_secondary）。
    - c4：最低板数 SECONDARY_MIN_LEADER_DAYS=3（主=4）
    - c5：分歧换手下限 SECONDARY_DIVERGE_MIN_TURNOVER=3.5%（主=5.0%）
    c1/c2 身份类不松——仍须唯一换手高标、非一字。
    """
    cl, _ = _evaluate(cand, window, min_days=SECONDARY_MIN_LEADER_DAYS,
                      diverge_min=SECONDARY_DIVERGE_MIN_TURNOVER, as_of=as_of,
                      conditions=_SECONDARY_CONDITIONS, include_peer=True)
    return cl


def passed_of(cl: Checklist) -> bool:
    """P1-1：唯一聚合器——实盘/回测/报告共用同一通过规则。

    UNKNOWN(None) 不否决：过滤 None 后全 True 才算过（与回测一致）。
    实现落在 domain.Checklist.passed（契约层唯一住所），此处只做入口转发；
    警告不在该属性内（split_checklist 已结构化分离）。
    """
    return cl.passed


def split_checklist(cl: Checklist) -> tuple[Checklist, WarningOnly]:
    """V4（二轮审计「警告不否决」结构化）：五条件与 W- 警告分离。

    此前 checklist 单列表混排，聚合方 all(ok...) 会把 W- 行一起吞——
    今天 _WARNINGS 恒 True/None 无事，但未来任何警告返回 False 即
    静默获得否决权。结构上锁死：条件与警告各自成列，passed 只看条件。
    """
    return replace(cl, w1_crowding=None), WarningOnly(w1_crowding=cl.w1_crowding)


_WINDOW_SQL = "SELECT buy_window FROM market_stat WHERE date = %s"


def _read_window(trade_date: date) -> str | None:
    """当日 buy_window 原文（market_stat 无该日行 → None）。

    自持 SQL：data.loader.load_market_stat 与 domain.MarketStat 构造不匹配
    （传 bomb_rate / max_limit_days，契约里是 bomb_count / max_height），调用即
    TypeError（已实测）——与 `_persist` 同因（见模块文档 §6）。
    """
    with connect() as conn:
        row = conn.execute(_WINDOW_SQL, (trade_date,)).fetchone()
    return None if row is None else row[0]


def current_window(trade_date: date) -> str:
    """当日买入窗口（market_stat 缺数按 NONE）。

    缺数 / NULL / 空串一律按 NONE（禁买，保守方向；实测存量 661 行无 NULL/空）。
    """
    return _read_window(trade_date) or "NONE"


def check_signal(trade_date: date,
                 source: SignalSource = SignalSource.LIVE) -> Signal | None:
    """当日买入信号：窗口非NONE + 唯一最高板候选 + 五条件全过 → 落库。

    buy_window=NONE（禁买）日主信号恒不落库；但次级观察记录仍生成
    （check_secondary_signal 内已豁免 NONE 守卫）——见其次级 docstring。
    source 供 replay_service 回填打标（S3/R7），默认 live。
    """
    window = current_window(trade_date)
    if window == "NONE":
        log.info("%s buy_window=NONE，禁买（主信号）", trade_date)
        check_secondary_signal(trade_date, source)   # 禁买日仍留观察线索（不导出）
        return None
    cand = ladder.sole_top(ladder.build(trade_date))
    if cand is None:
        log.info("%s 无唯一最高板候选", trade_date)
        check_secondary_signal(trade_date, source)   # 次级门槛更低，可能仍有候选
        return None
    cl, rows = _evaluate(cand, window, min_days=MIN_LEADER_DAYS,
                         diverge_min=DIVERGE_MIN_TURNOVER)
    sig = Signal(code=cand.code, date=trade_date, action=Action.BUY,
                 checklist=cl, source=source)
    if passed_of(cl):        # P1-1：与回测同一聚合器（UNKNOWN 不否决）
        _persist(sig, window, rows)
        return sig
    log.info("%s 候选 %s 未全过: %s", trade_date, cand.code,
             [n for n, ok, _ in rows if not ok])
    # 主信号未通过：尝试次级推荐（放宽阈值），不影响主路径返回 None
    check_secondary_signal(trade_date, source)
    return None


_INSERT_SQL = """
INSERT INTO signal (confirm_date, code, action, reason, buy_window, checklist,
                    strategy_version, status, source, config_hash)
VALUES (%s, %s, %s, NULL, %s, %s::jsonb, %s, 'SUGGESTED', %s, %s)
ON CONFLICT (confirm_date, code, action) DO UPDATE SET
  buy_window       = EXCLUDED.buy_window,
  checklist        = EXCLUDED.checklist,
  strategy_version = COALESCE(EXCLUDED.strategy_version, signal.strategy_version),
  source           = COALESCE(signal.source, EXCLUDED.source),
  config_hash      = EXCLUDED.config_hash
"""


def _persist(sig: Signal, window: str, rows: list[Row]) -> None:
    """落库（lkl 原语句：按 (confirm_date, code, action) upsert，同事务）。

    reason / strategy_version：reason 不在 domain.Signal 契约内 → 写 NULL 且冲突时
    不覆写历史 reason；strategy_version 取 CONFIG.STRATEGY_VERSION，缺键写 NULL 且
    冲突时保留库内旧值（COALESCE——不做有损覆写）。
    checklist 存的是逐项行 (标签, ok, 说明)——与 lkl 存量 signal.checklist 同形。
    source（审核文档 §9 第 10 条）：写 live / replay，冲突时**保留库内首个来源**
    （COALESCE——回放不得把实盘信号改标成 replay，反之亦然；历史 NULL 行顺带补全）。
    config_hash：本次产出的策略版本指纹，冲突时按最后一次运行覆盖。
    """
    with transaction() as conn:
        conn.execute(_INSERT_SQL, (sig.date, sig.code, sig.action.value, window,
                                   json.dumps(rows, ensure_ascii=False),
                                   _cfg("STRATEGY_VERSION", None),
                                   sig.source.value, config_hash()))


# ---- 次级推荐（次推荐）：放宽阈值，独立落库，action='SECONDARY' ----
# 主推荐（check_signal）完全不动：MIN_LEADER_DAYS、DIVERGE_MIN_TURNOVER、
# REQUIRE_YESTERDAY_COMPETITION 均为原值。次级单独定义 checklist_secondary
# 与 check_secondary_signal，后者在主信号未通过时被调用（主通过则跳过，
# 避免同一 confirm_date 产生双 action 记录）。


def check_secondary_signal(trade_date: date,
                           source: SignalSource = SignalSource.LIVE) -> Signal | None:
    """次级买入信号：次级唯一高标 + 次级 checklist 全过 → 落库 SECONDARY。

    - 主推荐（check_signal）未通过时才尝试次级，避免同一 confirm_date 同
      code 同时存在 BUY 与 SECONDARY（主信号已落库即代表更优）。
    - ★禁买日（buy_window=NONE）仍生成次级观察记录（奎爷 2026-09-09
      拍板）：次级是放宽阈值的观察性推荐，其存在价值恰在主链路不给信号
      的日子里提供「假如明天转暖，谁在身位上」的线索；SECONDARY 本就不
      进入交易导出（SECONDARY_EXPORT_ENABLED 默认关），不构成禁买语义
      破坏。主推荐 BUY 的 NONE 守卫不动——禁买日绝不产可执行 BUY。
    - 候选取 ladder.sole_top(min_days=SECONDARY_MIN_LEADER_DAYS)——门槛降 1 档。
    """
    window = current_window(trade_date)
    if window == "NONE":
        log.info("%s buy_window=NONE——次级仍生成（观察记录，不导出）", trade_date)
    rows_today = ladder.build(trade_date)
    cand = ladder.sole_top(rows_today, SECONDARY_MIN_LEADER_DAYS)
    if cand is None:
        log.info("%s 无次级唯一高标候选（min_days=%d）",
                 trade_date, SECONDARY_MIN_LEADER_DAYS)
        return None
    cl, rows = _evaluate(cand, window, min_days=SECONDARY_MIN_LEADER_DAYS,
                         diverge_min=SECONDARY_DIVERGE_MIN_TURNOVER,
                         conditions=_SECONDARY_CONDITIONS, include_peer=True)
    sig = Signal(code=cand.code, date=trade_date, action=Action.SECONDARY,
                 checklist=cl, source=source)
    if passed_of(cl):
        _persist(sig, window, rows)
        log.info("%s 次级推荐 %s 生成", trade_date, cand.code)
        return sig
    log.info("%s 次级候选 %s 未全过: %s", trade_date, cand.code,
             [n for n, ok, _ in rows if not ok])
    return None
