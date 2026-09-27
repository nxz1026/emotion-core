"""L3 信号结果回填：前瞻实际结果物化（entry_proxy=信号次日开盘价）。

语义逐字照搬 lkl/services/outcome.py（docs/07-算法层搬运方案.md §3.4，69 行），
判定与口径零改动：
- entry_proxy = 信号**次日**开盘价；收益相对 entry_proxy（验证算法方向的度量）；
- rule_ret_a = 口径 A（断板次日开盘卖）、rule_ret_d = 影子口径 D（半仓法），
  两腿均取自 evaluate._exit_family（同一 T+1 开盘买入前提，费后 %）；
- complete = 前向 5 日齐；不足 5 根 bar 时 max_up5 / max_dd5 / t5_close_ret 记
  None（不用窗口末近似——与 evaluate 的右删失口径一致）；
- 信号日无收盘价、或前向无 bar → 该信号返回 None（缺数不入库，下轮重试）；
- 幂等 upsert（冲突键 confirm_date + code + action），每日流程末尾跑一次。

与 lkl 的差异（全部是契约/IO 适配，不涉判定规则）：
1. 写入口自持：lkl 的 db.upsert_rows（分批 INSERT .. ON CONFLICT DO UPDATE +
   NaN/±inf→NULL 消毒 + 行宽校验）未随本轮迁入 utils/db.py，本轮范围又限
   outcome.py + test_outcome.py 两文件，故 `_upsert_rows` 在本模块复刻其写语义
   （同一 SQL 形状、同一分批大小、同一消毒、同一返回值 = 写入行数）。
   略去 lkl 的 `_check_idents`：本模块表名 / 列名 / 冲突键均为字面量常量，
   无外部注入口，无注入面可防。
2. A2-9 数据修订留痕（lkl revision.detect_and_log）未接：emotion-core 无
   services/revision（data_revision 仍属算法层待建，docs/09-展示层设计.md:300），
   模块就位后在 `_upsert_rows` 内接回——届时探测失败也不得拦写（lkl 同）。
3. 事务 / 连接：lkl get_conn() → utils.db.transaction()（正常退出提交、
   异常回滚后重抛）；只读查询走 query_df（自开自关），与 evaluate.py 同款。
4. UPSERT_BATCH 经 `_cfg` 取 CONFIG，未收录时用 lkl config.py 原值 1000。
5. 逐字保留、勿当遗漏的 lkl 行为：pending_signals **不按 action 过滤**——
   BUY / SELL / SECONDARY 一律回填（lkl 原语句无 action 条件；「只认 BUY」是
   交易导出与展示层的口径，不在此处）。

自持 SQL 2 处（lkl 原语句）：signal LEFT JOIN signal_outcome 待办集、基准日收盘价。
"""

# ✅ 已有 Rust 实现：src/emotion_core/core/src/outcome.rs
# 本文件保留作为参考实现和对账基准，不删除。

from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any

from emotion_core.algorithms.evaluate import _exit_family, _limit_up_on, _next_bars
from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df, transaction

log = logging.getLogger(__name__)

_TABLE = "signal_outcome"
_COLS = ["confirm_date", "code", "action", "entry_proxy", "t1_gap",
         "t1_promote", "t1_close_ret", "max_up5", "max_dd5",
         "t5_close_ret", "rule_ret_a", "rule_ret_d", "complete"]
_CONFLICT = ["confirm_date", "code", "action"]
_UPSERT_SQL = (
    f"INSERT INTO {_TABLE} ({', '.join(_COLS)})"
    f" VALUES ({', '.join(['%s'] * len(_COLS))})"
    f" ON CONFLICT ({', '.join(_CONFLICT)}) DO UPDATE SET "
    + ", ".join(f"{c}=EXCLUDED.{c}" for c in _COLS if c not in _CONFLICT))


def _cfg(name: str, default: Any) -> Any:
    """读 CONFIG 策略常量；emotion-core 尚未收录的键取 lkl config.py 原值。"""
    return getattr(CONFIG, name, default)


def _pct(new: float, base: float) -> float:
    return round((float(new) / float(base) - 1) * 100, 2)


def _outcome_row(confirm_date: date, code: str, action: str) -> tuple | None:
    """单信号前瞻行；信号日无收盘价（缺数）返回 None。

    rule_ret_a=口径A（断板次日开盘卖）、rule_ret_d=影子口径D（半仓法）。
    """
    base = query_df("SELECT close FROM daily_bar WHERE code=%s AND date=%s",
                    (code, confirm_date))
    if base.empty:
        return None
    base = float(base["close"].iloc[0])
    nb = _next_bars(code, confirm_date, n=5)
    if nb.empty:
        return None
    entry = float(nb["open"].iloc[0])
    fam = _exit_family(code, confirm_date)
    row = (confirm_date, code, action, round(entry, 3),
           _pct(entry, base), _limit_up_on(code, nb["date"].iloc[0]),
           _pct(nb["close"].iloc[0], entry), None, None, None,
           fam.get("A_断板开盘"), fam.get("D_半仓"), False)
    if len(nb) >= 5:
        w = nb.head(5)
        row = (*row[:7], _pct(w["high"].max(), entry),
               _pct(w["low"].min(), entry), _pct(w["close"].iloc[4], entry),
               row[10], row[11], True)
    return row


def pending_signals(force: bool = False) -> list[tuple[date, str, str]]:
    """待回填信号：新信号 + 未 complete 的；force=全量重算。"""
    where = ("WHERE o.confirm_date IS NULL OR NOT o.complete" if not force else "")
    df = query_df(
        "SELECT s.confirm_date, s.code, s.action FROM signal s"
        " LEFT JOIN signal_outcome o USING (confirm_date, code, action) "
        + where)
    return [(r.confirm_date, r.code, r.action) for r in df.itertuples()]


def _upsert_rows(rows: list[tuple]) -> int:
    """signal_outcome 幂等 upsert，返回写入行数（lkl utils/db.upsert_rows 写语义）。"""
    if not rows:
        return 0
    # P2（lkl 三轮审计）：行宽校验防错位静默写脏数据
    if len(rows[0]) != len(_COLS):
        raise ValueError(f"upsert {_TABLE}：行宽 {len(rows[0])} != 列数 "
                         f"{len(_COLS)}")
    # 统一消毒：NaN / ±inf → NULL（PG numeric 可存 NaN/Inf，会击穿下游 ::bigint 转换）
    clean = [tuple(None if isinstance(v, float) and not math.isfinite(v) else v
                   for v in r) for r in rows]
    batch = _cfg("UPSERT_BATCH", 1000)
    with transaction() as conn:
        with conn.cursor() as cur:
            for i in range(0, len(clean), batch):
                cur.executemany(_UPSERT_SQL, clean[i:i + batch])
    return len(clean)


def backfill(force: bool = False) -> int:
    """回填全部未完成信号 → signal_outcome（幂等）。返回处理数。"""
    todo = pending_signals(force)
    rows = [r for r in (
        _outcome_row(d, c, a) for d, c, a in todo) if r is not None]
    n = _upsert_rows(rows)
    log.info("signal_outcome：处理 %d 条（完成入库 %d）", len(todo), n)
    return n
