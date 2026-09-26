"""T9 信号质量评估（§1.7，E3）：无买入时点假设，统计信号前瞻表现。

语义逐字照搬 lkl/services/evaluate.py 的 replay / forward_stats / ebb_days
三函数及其私有辅助（_next_bars / _limit_up_on / _ebb_exit / _exit_family /
_rule_ret / _pit_drift），判定与口径零改动。
本次迁移范围为上述三函数：lkl 同文件的报告组装（report / *_report /
distribution_stats / hypothesis_test / walk_forward / adoption_summary 等）
不在本轮范围，未随本文件迁入。
- replay 只读逻辑层产出信号（不写 signal 生产表），并保留未过项明细；
- 正式收益口径 = E 退潮清仓 ?? A 断板开盘（退潮优先合成）；
- 前向窗口不完整（右删失）→ 极值记 None，不用窗口末近似。

与 lkl 的差异（全部是契约/IO 适配，不涉判定规则）：
1. 候选无 name 字段：lkl 的 LadderRow 由 ladder.build join stock_basic 自带 name，
   emotion-core 的 domain.LadderDay 只有梯队 8 列（见 entry.py 模块文档 §3），
   故 name 经 entry._candidate_view 预取（同 stock_basic.name，同一数据源）。
2. 逐项行：lkl entry.checklist 返回 [(标签, ok, 说明)] 列表，emotion-core 的
   entry.checklist 投影成 domain.Checklist（说明文本不再随返回值暴露）；本模块
   经 entry._evaluate 取「Checklist + 逐项行」，再用 `_split_rows` 按 W- 前缀
   分离条件/警告——与 lkl entry.split_checklist 的划分逐一等价（emotion-core 的
   entry.split_checklist 已改为 (Checklist, WarningOnly) 结构化分离，不返回说明）。
3. as_of 显式化：缺省 today_sh()（禁隐式 date.today()），同一数据同代码重跑同结论（P1-3）。
4. CONFIG 尚未收录的键（FEE_COMMISSION / FEE_STAMP）经 `_cfg` 取 lkl config.py
   原值默认（0.0002 / 0.0005）；键一旦进 CONFIG 自动生效。
5. 自持 SQL（lkl 原语句）5 处：base 收盘、前向 bars、derived_bar 涨停止、退潮日、
   stock_basic 漂移列——data 层无同口径入口，先例见 entry.py 模块文档 §6。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import pandas as pd

from emotion_core.algorithms import entry, ladder
from emotion_core.utils.config import CONFIG
from emotion_core.utils.dates import today_sh, trading_days
from emotion_core.utils.db import query_df

log = logging.getLogger(__name__)

Row = tuple[str, bool | None, str]                     # (标签, 通过?, 说明)


def _cfg(name: str, default: Any) -> Any:
    """读 CONFIG 策略常量；emotion-core 尚未收录的键取 lkl config.py 原值。"""
    return getattr(CONFIG, name, default)


def replay(start: date, end: date, min_days: int | None = None,
           as_of: date | None = None) -> pd.DataFrame:
    """逐日重放：窗口非NONE + 唯一最高板 + 五条件 → 信号明细（含未过项）。

    W4（二轮审计 P0-4/c5）：加 c5_verifiable 列——信号日距今超
    POOL_RECENT_DAYS（EM 池历史窗口）的信号，其炸板回封腿与换手率
    均来自当前股本反算/池缺失，标注 False 不混入 passed 可信统计。
    P1-3：as_of = 可复现基准日（同数据同代码重跑必须同结论）；
    缺省 today_sh()（显式 +08:00，禁隐式 date.today()）。
    """
    md = entry.MIN_LEADER_DAYS if min_days is None else min_days
    out = []
    days = trading_days(start, end)
    base = as_of or today_sh()
    oldest = base - timedelta(days=entry.POOL_RECENT_DAYS)
    for i, d in enumerate(days):
        if i and i % 100 == 0:
            log.info("replay 进度 %d/%d", i, len(days))
        window = entry.current_window(d)
        if window == "NONE":
            continue
        cand = ladder.sole_top(ladder.build(d), min_days=md)
        if cand is None:
            continue
        cl, rows = entry._evaluate(cand, window, min_days=md,
                                   diverge_min=entry.DIVERGE_MIN_TURNOVER,
                                   as_of=base)
        conds, warns = _split_rows(rows)
        # P1-1：passed 只走唯一聚合器 entry.passed_of（与实盘同一规则）
        passed = entry.passed_of(cl)
        # P2-12：point-in-time 漂移标注——当前 ST 或上市/股本变更晚于
        # 信号日的样本，历史换手率与过滤口径已漂移，标不可比较
        pit_drift = bool(_pit_drift(cand.code, d))
        out.append({"date": d, "code": cand.code,
                    "name": entry._candidate_view(cand).get("name", ""),
                    "cont_days": cand.cont_days, "window": window,
                    "passed": passed,
                    "fails": [n for n, ok, _ in conds if ok is False],
                    "unknown": [n for n, ok, _ in conds if ok is None],
                    "warn_notes": [t for _, _, t in warns],
                    "c5_verifiable": d >= oldest,
                    "pit_drift": pit_drift})
    return pd.DataFrame(out)


def _split_rows(rows: list[Row]) -> tuple[list[Row], list[Row]]:
    """条件行 / W- 警告行分离（= lkl entry.split_checklist 的行语义）。"""
    return ([r for r in rows if not r[0].startswith("W")],
            [r for r in rows if r[0].startswith("W")])


def _next_bars(code: str, d0: date, n: int = 5) -> pd.DataFrame:
    return query_df(
        f"SELECT date, open, high, low, close FROM daily_bar"
        f" WHERE code = %s AND date > %s ORDER BY date LIMIT {int(n)}",
        (code, d0))


def _limit_up_on(code: str, d: date) -> bool:
    df = query_df("SELECT is_limit_up FROM derived_bar"
                  " WHERE code = %s AND date = %s", (code, d))
    return False if df.empty else bool(df["is_limit_up"].iloc[0])


def _rule_ret(code: str, d0: date, ebbs: list[date] | None = None) -> float | None:
    """正式口径（P2 修复：退潮优先合成）= E 退潮清仓 ?? A 断板开盘卖。

    与实盘 exit.suggestions 同一优先级（退潮清仓 > 断板卖出）——
    此前正式口径只取 A，退潮日的收益被错记为持有。E=None（无退潮/
    清仓日在窗外）时回落 A；影子口径 D/E 明细见 _exit_family。"""
    fam = _exit_family(code, d0, ebbs)
    return fam.get("E_退潮清仓") if fam.get("E_退潮清仓") is not None \
        else fam.get("A_断板开盘")


def forward_stats(sigs: pd.DataFrame) -> pd.DataFrame:
    """每信号前瞻：T+1高开% / T+1晋级 / T+3、T+5 最大涨幅与最大回撤 / 规则化收益。"""
    rows = []
    ebbs = ebb_days(sigs["date"].min() if not sigs.empty else date(2020, 1, 1))
    for s in sigs.itertuples():
        bdf = query_df("SELECT close FROM daily_bar WHERE code=%s AND date=%s",
                       (s.code, s.date))
        if bdf.empty:                   # V4：基准价缺失保护（此前中断整个 replay）
            log.warning("forward_stats %s %s：基准日无行情，跳过", s.code, s.date)
            continue
        base = bdf["close"].iloc[0]
        nb = _next_bars(s.code, s.date)
        r = {"date": s.date, "code": s.code, "window": s.window,
             "cont_days": s.cont_days}
        if nb.empty:
            rows.append(r | {"t1_gap": None})
            continue
        r["t1_gap"] = round(float(nb["open"].iloc[0] / base - 1) * 100, 2)
        r["t1_promote"] = _limit_up_on(s.code, nb["date"].iloc[0])
        # A4（审计 P1-5 修复）：前向窗口不完整时极值会被截断（右删失），
        # 近期信号系统性偏小——只有 len(w)==h 才计入统计，不足置 None（pending）。
        for h in (3, 5):
            w = nb.head(h)
            full = len(w) == h
            r[f"max_up{h}"] = (round(float(w["high"].max() / base - 1) * 100, 2)
                               if full else None)
            r[f"max_dd{h}"] = (round(float(w["low"].min() / base - 1) * 100, 2)
                               if full else None)
        r["rule_ret"] = _rule_ret(s.code, s.date, ebbs)
        rows.append(r)
    return pd.DataFrame(rows)


def _pit_drift(code: str, d: date) -> bool:
    """P2-12：样本是否受非 point-in-time 口径影响。

    当前 is_st=True（历史可能不 ST）或上市日不早于信号日
    （股本/口径在窗口内变更）→ True，统计时应排除或单列。
    """
    r = query_df("SELECT is_st, first_bar_date FROM stock_basic"
                 " WHERE code = %s", (code,))
    if r.empty:
        return False
    st_now = bool(r["is_st"].iloc[0])
    fb = r["first_bar_date"].iloc[0]
    return st_now or (fb is not None and not pd.isna(fb) and fb > d)


def ebb_days(start: date) -> list[date]:
    """P3-5：退潮日全序列（自 start 起）——一次性预取注入，替代
    _ebb_exit 内部逐信号查库（此前纯 mock 测试也连真库）。"""
    df = query_df("SELECT date FROM market_stat WHERE date >= %s"
                  " AND force_liquidate ORDER BY date", (start,))
    return list(df["date"])


def _ebb_exit(nb: pd.DataFrame, d0: date, ret,
              ebbs: list[date] | None = None) -> float | None:
    """V4：信号后首个强制清仓日收盘强平；清仓日在前向窗外 → None(pending)。

    ebbs：调用方注入的退潮日序列（replay/report 预取一次）；
    None 时兜底自查（独立调用兼容）。"""
    days = ebbs if ebbs is not None else ebb_days(d0)
    ed = next((d for d in days if d > d0), None)
    if ed is None:
        return None
    idx = nb.index[nb["date"] == ed]
    if len(idx):
        return ret(float(nb["close"].iloc[idx[0]]))
    # P3（三轮审计）：窗外 pending / 清仓日无行情均为不可结算
    return None


def _exit_family(code: str, d0: date, ebbs: list[date] | None = None) -> dict:
    """出口规则族（同一 T+1 开盘买入前提，费后%，T+1 制度合法——最早 T+2 卖）：
    A 断板开盘=正式口径（首个前日未涨停日的开盘）；B 断板收盘（断板判定当日
    收盘，T+1 断板则顺延 T+2 收盘）；C 固定 T+2 收盘（最短合法持有）；
    D 半仓 = 50%×C + 50%×B；E 退潮清仓（正式口径优先项，P2 三轮）。

    正式口径 = E??A（退潮优先合成，见 _rule_ret）。
    V4（二轮审计）：① 右删失——前向窗口 <5 日且窗外仍涨停 → 各规则记
    None（pending），不再「按窗口末收盘近似」混入统计污染中位数；② T+1
    合法性——窗口仅 1 根 bar 时全部 None（当日买当日卖违反 T+1）。
    """
    nb = _next_bars(code, d0)
    if nb.empty:
        return {}
    entry_p = float(nb["open"].iloc[0])
    n = len(nb)
    if n < 2:                       # V4②：单 bar = 当日买当日卖，制度非法
        return {}
    lim = [_limit_up_on(code, d) for d in nb["date"]]
    fee = _cfg("FEE_COMMISSION", 0.0002) * 2 + _cfg("FEE_STAMP", 0.0005)

    def ret(p: float) -> float:
        return round((p / entry_p - 1 - fee) * 100, 2)

    def bounded_ret(idx: int) -> float | None:
        """V4①：窗口内未触发（窗外仍涨停/断板未出现）→ None 不近似。"""
        return ret(float(nb["open"].iloc[idx])) if idx is not None else None

    fb = next((i for i in range(1, n) if not lim[i - 1]), None)
    a = bounded_ret(fb) if fb is not None else None    # 窗内未断板 → pending
    brk = next((i for i in range(n) if not lim[i]), None)
    si = 1 if brk == 0 else brk
    b = ret(float(nb["close"].iloc[si])) if si is not None else None
    c = ret(float(nb["close"].iloc[1]))               # n>=2 保证 T+2 合法
    # V4（二轮审计）：E 退潮清仓腿——对齐实盘 exit.suggestions 优先级
    # （退潮清仓 > 断板卖出）；回测缺此腿导致强制清仓日的收益被错记为持有。
    e = _ebb_exit(nb, d0, ret, ebbs)
    return {"A_断板开盘": a, "B_断板收盘": b, "C_两日收盘": c,
            "D_半仓": round(0.5 * c + 0.5 * b, 2) if b is not None else None,
            "E_退潮清仓": e}
