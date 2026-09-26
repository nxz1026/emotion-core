"""T6 卖出建议（PLAN §1.6）：退潮清仓(b) > 断板卖出(a)；高潮/分歧期无建议。

语义逐字照搬 lkl/services/exit.py @ eb53a46（docs/07 §3.2 第 4 层，54 行），判定逻辑
零改动：
- b 退潮（market_stat.force_liquidate=True）：全部 OPEN 持仓无条件出清仓建议，优先级
  最高——此分支**不读**当日判据行（退潮当天仍封板的持仓也清）；
- a 断板：当日 derived_bar 行存在且 is_limit_up=False → 卖出建议（今日未封住）；
- c 其余（涨停 / 一字 / 当日无判据行）→ 无建议。缺行 = 缺数，**不**当作断板（F6）；
- 无 market_stat 行（stat=None）→ 不走退潮分支，退化为逐仓断板检查（lkl 同）。

执行语义（契约 v2，lkl REQUIREMENTS §exec）：SELL 恒 exec_hint=CLOSE_ALL =
「清仓全部持仓」；buy_window 只是**买入口径的市场标签**（退潮期为 NONE），与卖出无关，
执行器不得以 buy_window=NONE 为由拒单。BUY 侧恒 OPEN_POS（entry 侧）。

与 lkl 的差异（全部是契约/IO 适配，不涉判定规则）：
1. domain.Signal 是买入口径契约（code/date/action/checklist/source/status），lkl Signal
   的 reason / buy_window / exec_hint 三列无对应字段；且 SELL 建议**不落库**（signal 表
   无 exec_hint 列；lkl 的 trade/export、review、dashboard 也是现算现用）。故本模块在
   算法层补 `SellSignal(Signal)` 子类承载这三列——`isinstance(sig, Signal)` 成立，
   返回类型仍是 `list[Signal]`；domain/signal.py 补齐卖出契约后即下沉。
2. 持仓来源：lkl 用 services/position.current()；emotion-core 的 data/loader.py 无
   position 查询、domain/ 无 Position 类型，本模块自持 SQL + 局部投影 `Holding`
   （exit 判定只读 code / entry_date 两列）。与 entry._persist / promotion._fetch_pairs
   同先例：待 loader 补齐后下沉（本次范围限 exit.py + test_exit.py 两文件）。
3. market_stat：loader.load_market_stat 向 domain.MarketStat 传 bomb_rate /
   max_limit_days（契约字段是 bomb_count / max_height），调用即 TypeError → 自持 SQL
   （与 entry.current_window 同因）。返回 dict 而非 MarketStat：本模块只读三列，
   lkl 亦为 dict（df.iloc[0].to_dict()）。
4. derived_bar：loader 只有 load_derived_bars（**全日** ~5000 行/次），且其构造对
   amplitude IS NULL 的行（存量 15 行，全为 688xxx）调 float(None) 即崩；本模块按
   (date, code) 单行取（lkl 原语句同款粒度），amplitude 缺数留 None（F6 不补 0）。
5. frozen dataclass：lkl 在 suggestions 里原地改 s.confirm_date，本实现用
   dataclasses.replace 等价改写——「退潮分支先填开仓日 → suggestions 统一改写为建议日」
   两步语义逐字保留（单独调 _sell 时 date=开仓日，与 lkl 一致）。

对账：tests/unit/test_exit.py 逐组合（涨停/断板/一字/退潮/缺行，不连库）；
真实库差分见交付记录：同 trade_date 与 lkl.services.exit.suggestions 逐字段一致。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from emotion_core.domain.bar import DerivedBar
from emotion_core.domain.signal import Action, Checklist, Signal, SignalSource
from emotion_core.utils.db import connect_ro

# 执行语义常量（契约 v2：SELL=CLOSE_ALL 清仓，BUY=OPEN_POS 开仓）
EXEC_CLOSE_ALL = "CLOSE_ALL"

_DERIVED_SQL = """
SELECT code, date, is_limit_up, is_limit_down, is_one_word, is_exchange,
       is_bomb, touched_limit, cont_days, amplitude
FROM derived_bar WHERE date = %s AND code = %s
"""

_STAT_SQL = ("SELECT phase, buy_window, force_liquidate"
             " FROM market_stat WHERE date = %s")

_POSITION_SQL = ("SELECT code, entry_date FROM position"
                 " WHERE status = 'OPEN' ORDER BY entry_date")

# SELL 建议不参与 checklist 判定（lkl 的 SELL 行 checklist 为空）：六项 UNKNOWN。
# 与 loader.load_signal_history 对历史行的处理同形；Checklist.passed（滤 None 后 all）
# 空真——只表示「无否决条件」，不代表卖出被任何条件核验过。
_NO_CHECKLIST = Checklist(c1_uniqueness=None, c2_exchange=None, c3_elimination=None,
                          c4_min_days=None, c5_strength_diverge=None, w1_crowding=None)


@dataclass(frozen=True)
class Holding:
    """持仓最小投影：exit 判定只读 code / entry_date。

    lkl 用 models.Position（连 entry_price/shares/note 一并取），emotion-core 契约层
    尚无 Position 类型；domain/position.py 补齐后本类即删。
    """
    code: str
    entry_date: date


@dataclass(frozen=True)
class SellSignal(Signal):
    """SELL 信号 = domain.Signal + 卖出契约三列（reason / buy_window / exec_hint）。

    domain.Signal 无这三列（见模块文档 §1）；不落库，只供 trade/export（decisions.json
    的 reason / window / exec）、日报、dashboard 现算现用。
    """
    reason: str = ""
    buy_window: str = ""
    exec_hint: str = EXEC_CLOSE_ALL


def _stat(trade_date: date) -> dict | None:
    """当日市场状态行（无行 → None）：phase / buy_window / force_liquidate。

    force_liquidate 统一 bool()：lkl 走 pandas 时 NULL 得 None（假值），bool(None)=False
    与之等价；三列均无 NULL 存量行（实测 661 行）。
    """
    with connect_ro() as conn:
        row = conn.execute(_STAT_SQL, (trade_date,)).fetchone()
    if row is None:
        return None
    return {"phase": row[0], "buy_window": row[1], "force_liquidate": bool(row[2])}


def _derived(trade_date: date, code: str) -> DerivedBar | None:
    """当日 (date, code) 判据行；无行 → None（缺数 ≠ 未涨停，F6）。"""
    with connect_ro() as conn:
        r = conn.execute(_DERIVED_SQL, (trade_date, code)).fetchone()
    if r is None:
        return None
    return DerivedBar(code=r[0], date=r[1], is_limit_up=bool(r[2]),
                      is_limit_down=bool(r[3]), is_one_word=bool(r[4]),
                      is_exchange=bool(r[5]), is_bomb=bool(r[6]),
                      touched_limit=bool(r[7]), cont_days=int(r[8]),
                      amplitude=None if r[9] is None else float(r[9]))


def _open_positions() -> list[Holding]:
    """OPEN 持仓（lkl position.current() 的等价语句：同 WHERE / ORDER BY）。"""
    with connect_ro() as conn:
        rows = conn.execute(_POSITION_SQL).fetchall()
    return [Holding(code=r[0], entry_date=r[1]) for r in rows]


def _sell_signal(code: str, when: date, reason: str, buy_window: str) -> SellSignal:
    """构造 SELL 信号：exec_hint 恒 CLOSE_ALL（契约 v2），source 恒 live。"""
    return SellSignal(code=code, date=when, action=Action.SELL,
                      checklist=_NO_CHECKLIST, source=SignalSource.LIVE,
                      reason=reason, buy_window=buy_window,
                      exec_hint=EXEC_CLOSE_ALL)


def _sell(pos: Holding, stat: dict | None, day: DerivedBar | None) -> SellSignal | None:
    """退潮(b) 或 断板(a) 卖出建议；c 规则=无建议。

    exec_hint=CLOSE_ALL（契约 v2）：SELL 语义=清仓全部持仓，执行器
    不得再以 window=NONE 为由拒单——window 是买入口径，与卖出无关。
    """
    if stat and stat["force_liquidate"]:
        return _sell_signal(pos.code, pos.entry_date, "退潮期清仓建议", "NONE")
    if day is not None and not day.is_limit_up:
        return _sell_signal(pos.code, day.date,
                            f"断板建议（原{day.cont_days or ''}连板今日未封）", "")
    return None


def suggestions(trade_date: date) -> list[Signal]:
    """当日全部卖出建议：退潮优先，否则逐仓断板检查。"""
    stat = _stat(trade_date)
    open_pos = _open_positions()
    if stat and stat["force_liquidate"]:
        out = [s for p in open_pos if (s := _sell(p, stat, None))]
        # lkl：s.confirm_date = trade_date —— 退潮建议的日期是建议日，不是开仓日
        return [replace(s, date=trade_date) for s in out]
    return [s for p in open_pos if (s := _sell(p, None, _derived(trade_date, p.code)))]
