"""T6 卖出建议（W3′ 口径B）：**T+2 开盘无条件卖**，跌停封死顺延 T+3 强制了结。

裁决来源：`_audit/2026-10-21-修正方案-实测定档.md`（奎爷 2026-10-21 批准）。
旧规则「退潮清仓(b) > 断板卖出(a)」已废止，两条都被实测证伪：

- 退潮清仓：退潮日次日隔夜 +3.53%/胜率 73%（n=168），「退潮→次日」是四个相位里
  最好的持仓环境（`_audit/probe_phase2.py`）。按相位清仓等于在最好的时候卖。
- 断板卖出：持有到断板 mean **−1.90%**/胜率 38.1%（n=181，中位持有 1 天），
  而 T+1 封板买入、T+2 开盘卖出 mean **+3.54%**/胜率 68%（n=183）。
  alpha 全在隔夜，**持有越久越亏**：T+3 开盘 +2.69%/55%、T+4 开盘 +1.32%/48%
  （`_audit/probe_exec2.py`）。

判定（逐持仓，按 entry_date 之后经过的交易日数 n）：
- n = 0（建仓当日）→ 无建议（当日买当日不卖）；
- n = 1（T+2）→ 开盘卖出；若开盘即跌停且全天封死（low == open）→ 顺延，无建议；
- n ≥ 2（T+3 及以后）→ **强制了结**（原定 T+2 开盘卖，跌停封死顺延后到了结日）；
- entry_date 无行情（停牌等）→ 按经过的交易日推进，不因缺数跳过规则（F6 只用于判据）。

执行语义（契约 v2）：SELL 恒 exec_hint=CLOSE_ALL =「清仓该持仓」；buy_window 与卖出
无关，执行器不得以 buy_window=NONE 为由拒单。

与 lkl 的差异（全部是契约/IO 适配，判定规则已按 W3′ 重写）：
1. domain.Signal 无 reason / buy_window / exec_hint 三列，故本模块补 `SellSignal(Signal)`
   子类承载；SELL 不落库（signal 表无 exec_hint 列），review/dashboard 现算现用。
2. 持仓来源自持 SQL + 局部投影 `Holding`（只读 code / entry_date 两列）。
3. market_stat 自持 SQL（loader.load_market_stat 字段不匹配，见 entry.current_window 同因）。
4. derived_bar 按 (date, code) 单行取（loader 只有全日 ~5000 行接口）。
5. frozen dataclass：lkl 原地改 s.confirm_date，本实现用 dataclasses.replace。

对账：tests/unit/test_exit.py 逐组合（T+2 卖 / 跌停顺延 / T+3 强制 / 建仓当日 / 缺行，
不连库）；真实库差分见交付记录。
"""

# ✅ Rust 孪生：src/emotion_core/core/src/exit.rs（旧规则参考实现，生产走本文件）
# Rust 侧未同步 W3′，oracle 对账范围见 tests/oracle/test_exit_rust_vs_python.py。

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from emotion_core.domain.bar import DerivedBar
from emotion_core.domain.signal import Action, Checklist, Signal, SignalSource
from emotion_core.utils.dates import trading_days
from emotion_core.utils.db import connect_ro
from emotion_core.utils.price import limit_down_price_cents

# 执行语义常量（契约 v2：SELL=CLOSE_ALL 清仓，BUY=OPEN_POS 开仓）
EXEC_CLOSE_ALL = "CLOSE_ALL"

# 口径B 持有窗口：建仓日之后第 1 个交易日开盘卖，第 2 个交易日强制了结
SELL_AFTER_DAYS = 1
FORCE_AFTER_DAYS = 2

_DERIVED_SQL = """
SELECT code, date, is_limit_up, is_limit_down, is_one_word, is_exchange,
       is_bomb, touched_limit, cont_days, amplitude
FROM derived_bar WHERE date = %s AND code = %s
"""

_OPEN_SQL = "SELECT open, low, pre_close, close FROM daily_bar WHERE date = %s AND code = %s"

_POSITION_SQL = "SELECT code, entry_date FROM position WHERE status = 'OPEN' ORDER BY entry_date"

# SELL 建议不参与 checklist 判定（lkl 的 SELL 行 checklist 为空）：六项 UNKNOWN。
# Checklist.passed（滤 None 后 all）空真——只表示「无否决条件」，不代表卖出被核验过。
_NO_CHECKLIST = Checklist(
    c1_uniqueness=None,
    c2_exchange=None,
    c3_elimination=None,
    c4_min_days=None,
    c5_strength_diverge=None,
    w1_crowding=None,
)


@dataclass(frozen=True)
class Holding:
    """持仓最小投影：exit 判定只读 code / entry_date。"""

    code: str
    entry_date: date


@dataclass(frozen=True)
class SellSignal(Signal):
    """SELL 信号 = domain.Signal + 卖出契约三列（reason / buy_window / exec_hint）。"""

    reason: str = ""
    buy_window: str = ""
    exec_hint: str = EXEC_CLOSE_ALL


def _derived(trade_date: date, code: str) -> DerivedBar | None:
    """当日 (date, code) 判据行；无行 → None（缺数 ≠ 未涨停，F6）。"""
    with connect_ro() as conn:
        r = conn.execute(_DERIVED_SQL, (trade_date, code)).fetchone()
    if r is None:
        return None
    return DerivedBar(
        code=r[0],
        date=r[1],
        is_limit_up=bool(r[2]),
        is_limit_down=bool(r[3]),
        is_one_word=bool(r[4]),
        is_exchange=bool(r[5]),
        is_bomb=bool(r[6]),
        touched_limit=bool(r[7]),
        cont_days=int(r[8]),
        amplitude=None if r[9] is None else float(r[9]),
    )


def _open_bar(trade_date: date, code: str) -> tuple[float, float, float | None, float] | None:
    """当日 (open, low, pre_close, close)；无行 → None。pre_close 缺数留 None（不补算）。"""
    with connect_ro() as conn:
        r = conn.execute(_OPEN_SQL, (trade_date, code)).fetchone()
    if r is None:
        return None
    return (float(r[0]), float(r[1]), None if r[2] is None else float(r[2]), float(r[3]))


def _open_locked_limit_down(trade_date: date, code: str) -> bool:
    """开盘即跌停且**收盘仍在跌停**（全天封死）→ 卖不掉。

    只看开盘不够：开盘跌停后盘中撬开是有成交的，开盘价卖得掉。判「封死」要求
    开盘价与收盘价都在跌停价（low ≤ open 恒成立，不提供额外信息）。
    缺数按「卖得掉」处理（保守：宁可给卖出建议，不静默顺延）。
    """
    bar = _open_bar(trade_date, code)
    if bar is None:
        return False
    op, _low, pre_close, close = bar
    if pre_close is None or pre_close <= 0:
        return False
    ld = limit_down_price_cents(int(round(pre_close * 100)), code) / 100
    return op <= ld + 1e-9 and close <= ld + 1e-9


def _open_positions() -> list[Holding]:
    """OPEN 持仓（lkl position.current() 的等价语句：同 WHERE / ORDER BY）。"""
    with connect_ro() as conn:
        rows = conn.execute(_POSITION_SQL).fetchall()
    return [Holding(code=r[0], entry_date=r[1]) for r in rows]


def _sell_signal(code: str, when: date, reason: str, buy_window: str) -> SellSignal:
    """构造 SELL 信号：exec_hint 恒 CLOSE_ALL（契约 v2），source 恒 live。"""
    return SellSignal(
        code=code,
        date=when,
        action=Action.SELL,
        checklist=_NO_CHECKLIST,
        source=SignalSource.LIVE,
        reason=reason,
        buy_window=buy_window,
        exec_hint=EXEC_CLOSE_ALL,
    )


def held_days(entry_date: date, trade_date: date) -> int:
    """entry_date 之后到 trade_date（含）经过的交易日数；trade_date ≤ entry_date → 0。

    n=1 即口径B 的 T+2 卖出日，n≥2 是强制了结日。
    """
    if trade_date <= entry_date:
        return 0
    days = trading_days(entry_date + timedelta(days=1), trade_date)
    return len(days)


def open_locked_limit_down(trade_date: date, code: str) -> bool:
    """公开入口：当日开盘跌停封死（卖不掉）。与持仓卖出建议共用同一判据。

    holding_service 的持仓视角也要回答「今天能不能卖」，必须同源，否则
    报告说「顺延」而持仓面板说「卖出」，两边打架。
    """
    return _open_locked_limit_down(trade_date, code)


def _sell(pos: Holding, trade_date: date) -> SellSignal | None:
    """口径B 卖出建议：T+2 开盘卖 / 跌停封死顺延 / T+3 强制了结。"""
    n = held_days(pos.entry_date, trade_date)
    if n <= 0:
        return None
    if n >= FORCE_AFTER_DAYS:
        return _sell_signal(
            pos.code,
            trade_date,
            f"强制了结（口径B：原定建仓后第{SELL_AFTER_DAYS}个交易日开盘卖，已持有{n}个交易日）",
            "",
        )
    if _open_locked_limit_down(trade_date, pos.code):
        return None  # 开盘跌停封死，卖不掉 → 顺延到下一交易日（届时强制了结）
    day = _derived(trade_date, pos.code)
    hit = "开盘卖出" if day is None or not day.is_limit_up else "开盘卖出（昨日封板也照卖）"
    return _sell_signal(
        pos.code, trade_date, f"T+2 {hit}（口径B：隔夜 alpha 收敛，不持有到断板）", ""
    )


def suggestions(trade_date: date) -> list[Signal]:
    """当日全部卖出建议：逐持仓按口径B 的持有天数判定。"""
    return [s for p in _open_positions() if (s := _sell(p, trade_date))]
