"""T4 连板梯队与龙头（ladder_day 表）。

语义逐字照搬 lkl/services/ladder.py（docs/07 §3.3；验收 docs/07 §T4）。
本模块 = 纯逻辑（fold / top_group / sole_top）+ 编排（build / persist / y_survivors）；
SQL 住 data/loader.py（架构纪律：SQL 唯一住所，docs/11 §4、loader 模块文档）。

口径：
- 标的池 = 主板 7 前缀 + 次新剔除 + 整体剔 ST（CONFIG.BOARD_PREFIXES / NEW_ISSUER_*，
  R3/C5/W2）。不能简化成「全市场 cont_days>=2」：实测 lkl 661 天历史里 417 天会多出
  创业板/科创板/北交所行，另 32 天会多出未过 90 自然日的次新。
- is_top = 换手板（is_exchange）最高身位组成员；一字缩量板不进最高层竞争
  （术语：换手高标是「换手口径最高」，绝对最高板可能更高——一字垄断）。
- is_sole_top = 换手同身位仅 1 只 且 cont_days >= CONFIG.MIN_LEADER_DAYS（§1.4）。
- y_top_group_count / y_top_survivor_count = 昨日最高换手组只数 / 其中今日仍换手的
  只数（R2 淘汰赛，供 entry 条件 3）。幸存口径是今日 is_exchange（一字缩量板不算
  可参与幸存，V3）。
- persist 按日全量替换：DELETE + INSERT 同一事务（lkl A8/W3）——纯 upsert 留不下
  「已跌出梯队」的幽灵行，而横跨两事务会在 INSERT 崩溃时永久清零当日。

对账：tests/oracle/test_ladder_vs_lkl.py（fixture 快照逐字段）；另已用 661 天全量
DB 回放验证全部 6 个业务字段 0 处不一致。
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date

from emotion_core.data.loader import (
    count_derived_rows,
    load_exchange_codes,
    load_ladder_candidates,
    replace_ladder_day,
)
from emotion_core.domain.ladder import LadderDay
from emotion_core.utils.config import CONFIG
from emotion_core.utils.dates import prev_trading_day

log = logging.getLogger(__name__)

Candidate = tuple[str, int, bool]  # (code, cont_days, is_exchange)


def top_group(rows: list[LadderDay]) -> list[LadderDay]:
    """同身位组：换手板口径的最高连板集合。"""
    ex = [r for r in rows if r.is_exchange]
    if not ex:
        return []
    h = max(r.cont_days for r in ex)
    return [r for r in ex if r.cont_days == h]


def sole_top(rows: list[LadderDay], min_days: int | None = None) -> LadderDay | None:
    """唯一换手高标（§1.4）：换手同身位仅 1 只且达最低板数门槛。"""
    md = CONFIG.MIN_LEADER_DAYS if min_days is None else min_days
    tg = top_group(rows)
    return tg[0] if len(tg) == 1 and tg[0].cont_days >= md else None


def fold(trade_date: date, rows: list[Candidate],
         prev_rows: list[Candidate], today_exchange: set[str]) -> list[LadderDay]:
    """纯函数：候选行 → 梯队行（is_top/is_sole_top/R2 计数），按板数降序。

    Args:
        rows: 当日候选（主板 + 剔 ST + cont_days>=2），顺序无关。
        prev_rows: 昨日候选，口径同 rows（R2 淘汰赛分母）。
        today_exchange: 今日主板换手板代码集（幸存分子）。
    """
    def _mk(day: date, candidate: Candidate) -> LadderDay:
        code, days, exchange = candidate
        return LadderDay(date=day, code=code, cont_days=days, is_exchange=exchange,
                         is_top=False, is_sole_top=False,
                         y_top_group_count=0, y_top_survivor_count=0)

    base = [_mk(trade_date, r) for r in rows]
    base.sort(key=lambda r: (-r.cont_days, r.code))
    top_codes = {r.code for r in top_group(base)}
    sole = sole_top(base)
    prev_top = {r.code for r in top_group([_mk(trade_date, r) for r in prev_rows])}
    group_count = len(prev_top)
    survivor_count = len(prev_top & today_exchange)
    return [replace(r, is_top=r.code in top_codes,
                    is_sole_top=sole is not None and r.code == sole.code,
                    y_top_group_count=group_count,
                    y_top_survivor_count=survivor_count)
            for r in base]


def build(trade_date: date) -> list[LadderDay]:
    """当日梯队（主板口径，按板数降序）。无候选时返回空列表（真平静，非缺数）。"""
    rows = load_ladder_candidates(trade_date)
    prev = prev_trading_day(trade_date)
    prev_rows = load_ladder_candidates(prev) if prev is not None else []
    today_exchange = load_exchange_codes(trade_date) if prev_rows else set()
    return fold(trade_date, rows, prev_rows, today_exchange)


def y_survivors(trade_date: date) -> set[str]:
    """昨日最高换手组中今日仍 is_exchange 的代码集（R2 幸存者，V3 口径）。"""
    prev = prev_trading_day(trade_date)
    if prev is None:
        return set()
    y_codes = {r.code for r in top_group(build(prev))}
    if not y_codes:
        return set()
    return y_codes & load_exchange_codes(trade_date)


def _guard_no_data(trade_date: date) -> None:
    """上游完整性凭证（lkl P2）：derived_bar 当日 0 行 = 缺数日，禁止当「合法零」。

    derived_bar 是全市场衍生表，任何交易日都应有数千行；有行而梯队为空才是真平静。
    """
    if count_derived_rows(trade_date) == 0:
        raise RuntimeError(f"ladder_day {trade_date}：derived_bar 当日 0 行——"
                           "上游缺数，拒绝清空，请先重跑采集")


def persist(trade_date: date) -> int:
    """梯队判定结果写 ladder_day（按日全量替换，单事务），返回写入行数。

    Raises:
        RuntimeError: 上游缺数（derived_bar 当日 0 行），拒绝清空已判定历史。
    """
    rows = build(trade_date)
    if not rows:
        _guard_no_data(trade_date)
        replace_ladder_day(trade_date, [])
        log.warning("ladder_day %s：当日无梯队（真平静，清空旧记录）", trade_date)
        return 0
    n = replace_ladder_day(trade_date, rows)
    log.info("ladder_day %s：%d 行（最高组 %d 只，唯一高标 %s）", trade_date, n,
             len(top_group(rows)), (sole.code if (sole := sole_top(rows)) else "无"))
    return n
