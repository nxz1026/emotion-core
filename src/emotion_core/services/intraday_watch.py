"""盘中触发台账的收盘结算（ADR 0003 §D6；常驻采集器尚未启用，本模块先跑结算）。

**为什么现在只有 `--settle`**
CPT 交付的 `public.intraday_seal_snapshot` 只有 5 列（`at/code/sealed/bid1_lots/
limit_price`），没有 `price/prev_close/chg_pct/name` —— 本仓判据（`seal_hit` 要涨幅、
`seal_weaken` 要现价）算不了，常驻采集器（§D4 `--loop`）因此**暂不创建**（见 ADR 0003
§H.4 与 §H.3 缺口①）。但结算这一半可以先把路铺好：一旦 `trigger_log` 有行，
`--settle` 就能把「封没封住、次日开盘卖多少钱」补齐，不必等采集器上线。

**结算口径**（与 B 轮/C 轮探针一致，逐条可对照）
- `closed_sealed` = 当日 `derived_bar.is_limit_up`（收盘还封着）
- `broke` = 当日 `derived_bar.is_bomb`（盘中开过板）
- 卖点 = **T+1 开盘**；若 T+1 开盘即跌停封死（开盘=收盘=跌停）则顺延 T+2 开盘，
  `rolled=True`；T+2 仍封死 → 不写 `exit_px`（留 NULL 等下一轮再结算，不猜价）
- `ret_pct` = (exit/entry − 1) × 100 − 双边费 0.15%（`FEE_PCT`）
- `settled_at` = now()，由 `loader.save_settlements` 写

用法：
    python -m emotion_core.services.intraday_watch --settle --date 2026-10-22
    python -m emotion_core.services.intraday_watch --settle --date 2026-10-22 --dry-run
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Iterable

from emotion_core.algorithms import exit as exit_svc
from emotion_core.data import loader
from emotion_core.utils.db import connect_ro
from emotion_core.utils.dates import next_trading_day

log = logging.getLogger("emotion_core.intraday_watch")

#: 双边费用（%）：佣金+印花税近似值，与 `_audit/probe_fill.py` / Lab 的 `FEE` 同源。
FEE_PCT: float = 0.15


def _derived(d: date, codes: Iterable[str]) -> dict[str, dict[str, Any]]:
    """取当日判据行（只读 is_limit_up / is_bomb），缺数不进字典。"""
    codes = list(codes)
    if not codes:
        return {}
    with connect_ro() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT code, is_limit_up, is_bomb FROM derived_bar "
            "WHERE date = %s AND code = ANY(%s)",
            (d, codes))
        return {r[0]: {"is_limit_up": bool(r[1]), "is_bomb": bool(r[2])}
                for r in cur.fetchall()}


def _open_px(d: date, code: str) -> float | None:
    """取某日开盘价；无行或非正价返回 None（停牌/缺数都不猜）。"""
    with connect_ro() as conn, conn.cursor() as cur:
        cur.execute("SELECT open FROM daily_bar WHERE date = %s AND code = %s", (d, code))
        row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    px = float(row[0])
    return px if px > 0 else None


def settle_one(trig: dict[str, Any], derived: dict[str, Any] | None,
               open_t1: float | None, open_t2: float | None
               ) -> dict[str, Any] | None:
    """结算单条触发（纯函数，便于单测）。

    Args:
        trig: `loader.load_triggers` 的行（要 `date`/`code`/`entry_px`）。
        derived: 当日 `derived_bar` 判据行（`is_limit_up`/`is_bomb`）；None=缺数。
        open_t1: T+1 开盘价；T+1 跌停封死或不存在时为 None。
        open_t2: T+2 开盘价（顺延时用）。

    Returns:
        结算行 dict；缺数（如 T+2 也封死）返回 None，表示「本轮不结算、下轮再来」。
    """
    code = trig["code"]
    entry = trig.get("entry_px") if trig.get("entry_px") is not None else trig.get("trigger_price")
    if entry is None or float(entry) <= 0:
        return None
    if derived is None:
        return None                     # 判据行缺数：封没封住不知道，不结算

    if open_t1 is not None:
        exit_px, rolled = float(open_t1), False
    elif open_t2 is not None:
        exit_px, rolled = float(open_t2), True
    else:
        return None                     # T+1 封死、T+2 也没有 → 留 NULL 等下轮

    ret = (exit_px / float(entry) - 1.0) * 100.0 - FEE_PCT
    return {
        "date": trig["date"], "code": code,
        "broke": derived["is_bomb"], "broke_ts": None,
        "closed_sealed": derived["is_limit_up"],
        "exit_px": round(exit_px, 2), "rolled": rolled,
        "ret_pct": round(ret, 3),
    }


def settle(d: date, *, dry_run: bool = False) -> dict[str, Any]:
    """结算某日全部未结算触发，返回统计（dry_run 只算不写）。"""
    triggers = loader.load_triggers(d, unsettled_only=True)
    if not triggers:
        return {"date": d, "triggers": 0, "settled": 0, "pending": 0, "rows": []}

    derived = _derived(d, [t["code"] for t in triggers])
    t1 = next_trading_day(d)
    rows: list[dict] = []
    for trig in triggers:
        code = trig["code"]
        open_t1 = None
        open_t2 = None
        if t1 is not None and not exit_svc.open_locked_limit_down(t1, code):
            open_t1 = _open_px(t1, code)
        if open_t1 is None and t1 is not None:
            t2 = next_trading_day(t1)
            if t2 is not None and not exit_svc.open_locked_limit_down(t2, code):
                open_t2 = _open_px(t2, code)
        row = settle_one(trig, derived.get(code), open_t1, open_t2)
        if row is not None:
            rows.append(row)

    if rows and not dry_run:
        loader.save_settlements(rows)
    log.info("settle %s：触发 %d 条，结算 %d 条，待定 %d 条",
             d, len(triggers), len(rows), len(triggers) - len(rows))
    return {"date": d, "triggers": len(triggers), "settled": len(rows),
            "pending": len(triggers) - len(rows), "rows": rows}


def main(argv: list[str] | None = None) -> int:
    """CLI：只有 `--settle`；常驻采集器（`--loop`）等 CPT 补齐 5 列缺口后再建。"""
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m emotion_core.services.intraday_watch",
        description="盘中触发台账的收盘结算（ADR 0003 §D6）")
    ap.add_argument("--settle", action="store_true", help="结算指定日期的全部未结算触发")
    ap.add_argument("--date", metavar="YYYY-MM-DD", help="触发日（默认今天）")
    ap.add_argument("--dry-run", action="store_true", help="只算不写")
    ns = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not ns.settle:
        ap.error("目前只支持 --settle（--loop 等 CPT 补齐 price/prev_close 后再建）")
    d = date.fromisoformat(ns.date) if ns.date else date.today()
    out = settle(d, dry_run=ns.dry_run)
    tag = "（dry-run，未写库）" if ns.dry_run else ""
    print(f"{d} 触发 {out['triggers']} 条 · 结算 {out['settled']} 条 · "
          f"待定 {out['pending']} 条{tag}")
    for row in out["rows"]:
        print(f"  {row['code']} 封住={row['closed_sealed']} 开过板={row['broke']} "
              f"卖价={row['exit_px']} 顺延={row['rolled']} 收益={row['ret_pct']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
