"""daily_bar 写库前的自检（bar guard）。

由来（2026-10-17 排查）：2026-09-29 的 5218 行被写成了 **2026-10-13 的盘中快照** ——
行的 ``date`` 是 09-29，而 ``open`` / ``pre_close`` 与 10-13 的真实行**逐字相等**、
``volume`` 只有当日全天的 54%~63%：

* 600519：快照 open 1287.00 / pre_close 1288.00 / volume 12923.70
  vs 10-13 真实 open 1287.00 / pre_close 1288.00 / volume 26573.00
* 002242：快照 open 15.82 / pre_close 16.64 / volume 883135.32
  vs 10-13 真实 open 15.820 / pre_close 16.640 / volume 1397449.00

即「调用方给的日期」盖住了「源自己的日期」。故设两层防线：

1. **取数处**（``data/sync.fetch_snapshot``、``services/ingest.snapshot_daily``）：
   源说自己是哪个交易日，就只能写哪个交易日（快照自带 f124 优先，退
   ``em_data_date()`` 守卫）。
2. **写库前**（本模块）：最后一道网 —— 任何写入方（含临时脚本）只要走本仓的
   daily_bar 写库出口，就绕不过下面三类判据。

三类判据（09-29 那类事故三条都命中）：

* **形状**：非顺延行必须 ``low ≤ min(open, close) ≤ max(open, close) ≤ high`` 且
  ``close > 0``；
* **顺延**：OHLC 全 0 且零成交的行 ``close`` 必须 == ``pre_close``（顺延不产生新价格）；
* **整批自洽**：``pre_close`` 与该股库内上一根 bar 的 ``close`` 的**中位**偏离 >
  ``BATCH_MEDIAN_GAP_PCT_MAX`` ⇒ 整批判为「不属于这个日期」，拒绝入库。逐行
  ``MAX_PRE_CLOSE_GAP_PCT``（A 股单日涨跌幅上限）只记告警不否决 —— 除权除息日
  ``pre_close ≠ 前收`` 是合法的（实测常态 21~45 行/日）。

判据阈值实测（2026-10-17）：修复前的 09-29 批次 5224 行中位偏离 **3.32%**、
形状违规 0、顺延行 13 行里 8 行 ``close ≠ pre_close``；正常日 2026-10-16 的 5225
行中位偏离 **0.00%**、超大偏离 8 行、顺延行 10 行里 6 行（皆为整行全 0 的坏行）。
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Final

from emotion_core.data.providers.base import BAR_COLS
from emotion_core.utils.db import transaction

log = logging.getLogger("emotion_core.bar_guard")

#: A 股单日涨跌幅上限（ST 5%、创业板/科创板 20%）——逐行告警线，不是否决线。
MAX_PRE_CLOSE_GAP_PCT: Final[float] = 21.0
#: 整批 ``pre_close`` 与前收的中位偏离上限；超过即判「整批写错日期」。
BATCH_MEDIAN_GAP_PCT_MAX: Final[float] = 1.0
#: 不足这么多行不做整批统计（小批没有统计意义，避免误报）。
BATCH_MIN_ROWS: Final[int] = 20
#: 前收查询一次送多少对 (code, date) 给数据库。
_PREV_CLOSE_CHUNK: Final[int] = 5000
#: 报错信息里最多列几处样例。
_SAMPLES: Final[int] = 5

__all__ = [
    "BATCH_MEDIAN_GAP_PCT_MAX",
    "BATCH_MIN_ROWS",
    "MAX_PRE_CLOSE_GAP_PCT",
    "BarGuardError",
    "chain_gaps",
    "check_bars",
    "is_padding_row",
    "prev_closes",
    "row_problems",
]


class BarGuardError(RuntimeError):
    """daily_bar 写库前自检不通过 —— 拒绝入库（不静默丢行）。"""


def _f(v: object) -> float:
    """宽容取数：None / NaN / 非数字一律当 0（判据只在有值时生效）。"""
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def is_padding_row(row: Sequence[object]) -> bool:
    """顺延行：OHLC 全 0 且零成交（停牌日/无成交日由上游顺延出的占位行）。"""
    return _f(row[2]) == 0 and _f(row[3]) == 0 and _f(row[4]) == 0 and _f(row[7]) == 0


def row_problems(rows: Sequence[Sequence[object]]) -> list[str]:
    """单行判据（不查库）：形状 + 顺延。返回人类可读的问题清单。"""
    out: list[str] = []
    width = len(BAR_COLS)
    for i, r in enumerate(rows):
        if len(r) != width:
            out.append(f"行 {i}: 行宽 {len(r)} != {width}")
            continue
        code, d = r[0], r[1]
        o, h, low = _f(r[2]), _f(r[3]), _f(r[4])
        close, pre, vol = _f(r[5]), _f(r[6]), _f(r[7])
        if is_padding_row(r):
            # 两种顺延形状都要认：
            #   ① close=0 —— 上游既有的「无价占位行」（全市场每天 6~15 行，合法）
            #   ② close=pre_close>0 —— 带上一真实收盘的顺延行（停牌日）
            # 只有「close>0 且 ≠ pre_close」才是错：那正是 2026-09-29 那起
            # 「拿回补时刻的最新价当顺延价」留下的脚印。
            if close > 0 and close != pre:
                out.append(f"{code}@{d} 顺延行 close({close}) ≠ pre_close({pre})")
        elif low > min(o, close) or h < max(o, close):
            out.append(f"{code}@{d} OHLC 不自洽 open={o} high={h} low={low} close={close}")
        elif close <= 0:
            out.append(f"{code}@{d} 收盘价非正 close={close}")
        elif vol < 0:
            out.append(f"{code}@{d} 成交量为负 volume={vol}")
    return out


def prev_closes(rows: Sequence[Sequence[object]]) -> dict[tuple[str, date], float]:
    """本批每行「该股库内上一根 bar 的 close」。

    库内查不到的（该股首根 bar / 库不可用）**不进结果**，对应行自动跳过链式判据：
    首根 bar 没有前收可比，不是错误。
    """
    pairs = sorted({(str(r[0]), r[1]) for r in rows if len(r) == len(BAR_COLS)})
    if not pairs:
        return {}
    from psycopg import sql as pgsql  # noqa: PLC0415 —— 惰性导入，保持模块无驱动依赖

    out: dict[tuple[str, date], float] = {}
    for i in range(0, len(pairs), _PREV_CLOSE_CHUNK):
        chunk = pairs[i : i + _PREV_CLOSE_CHUNK]
        values = pgsql.SQL(", ").join([pgsql.SQL("(%s::text, %s::date)")] * len(chunk))
        params: list[object] = [v for pair in chunk for v in pair]
        query = pgsql.SQL(
            "SELECT v.code, v.date, p.close FROM (VALUES {}) AS v(code, date) "
            "LEFT JOIN LATERAL ("
            "  SELECT close FROM daily_bar b"
            "  WHERE b.code = v.code AND b.date < v.date"
            "  ORDER BY b.date DESC LIMIT 1) p ON TRUE"
        ).format(values)
        with transaction() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            for code, d, close in cur.fetchall():
                if close is not None:
                    out[(str(code), d)] = float(close)
    return out


def chain_gaps(
    rows: Sequence[Sequence[object]], prev: Mapping[tuple[str, date], float]
) -> list[float]:
    """每行 ``pre_close`` 与库内前收的偏离（百分数）；无前收的行不参与。"""
    gaps: list[float] = []
    for r in rows:
        if len(r) != len(BAR_COLS):
            continue
        pc = prev.get((str(r[0]), r[1]))
        pre = _f(r[6])
        if pc and pc > 0 and pre > 0:
            gaps.append(abs(pre / pc - 1.0) * 100.0)
    return gaps


def _short(problems: Sequence[str], total: int) -> str:
    shown = "；".join(problems[:_SAMPLES])
    more = "" if len(problems) <= _SAMPLES else f"…（其余 {len(problems) - _SAMPLES} 处略）"
    return f"{len(problems)} 处（本批 {total} 行）：{shown}{more}"


def check_bars(
    rows: Sequence[Sequence[object]],
    *,
    prev_close: Mapping[tuple[str, date], float] | None = None,
    allow_gap: bool = False,
    lookup: bool = True,
) -> None:
    """写库前自检；不通过抛 :class:`BarGuardError`（调用方决定是否放行）。

    ``prev_close`` 显式传入可省掉一次库查询（测试与批量场景）；
    ``allow_gap=True`` 只跳过「整批自洽」判据（历史修复场景），
    形状/顺延两类硬判据**不可关**。
    """
    if not rows:
        return
    problems = row_problems(rows)
    if problems:
        raise BarGuardError("形状/顺延自检不通过 " + _short(problems, len(rows)) + "；拒绝入库。")
    if prev_close is None and lookup:
        try:
            prev_close = prev_closes(rows)
        except Exception as exc:  # noqa: BLE001 —— 库抖动不该拦住写入
            log.warning("bar guard：前收读取失败（%s），本批跳过整批自洽判据", exc)
            return
    if not prev_close:
        return
    gaps = chain_gaps(rows, prev_close)
    big = [g for g in gaps if g > MAX_PRE_CLOSE_GAP_PCT]
    if big:
        log.warning(
            "bar guard：%d/%d 行 pre_close 与前收偏离 > %.0f%%"
            "（除权除息日属正常；成片出现即整批写错日期，最大 %.2f%%）",
            len(big),
            len(gaps),
            MAX_PRE_CLOSE_GAP_PCT,
            max(big),
        )
    if allow_gap or len(gaps) < BATCH_MIN_ROWS:
        return
    med = statistics.median(gaps)
    if med > BATCH_MEDIAN_GAP_PCT_MAX:
        raise BarGuardError(
            f"整批自洽不通过：pre_close 与库内前收的中位偏离 {med:.2f}%"
            f"（上限 {BATCH_MEDIAN_GAP_PCT_MAX:.1f}%，{len(gaps)} 行参与统计）"
            f"——本批 {len(rows)} 行与库内前收不自洽，疑似整批写错日期；拒绝入库"
            f"（确认无误可传 allow_gap=True）。"
        )
