"""盘中触发台账：读 CPT 一轮 → 判触发 → 写 `trigger_log`；收盘后 `--settle` 结算。

**两个半场**
- `--round`：读 CPT `public.intraday_seal_snapshot` 的**最新一轮**（R93 起 9 列，
  `price/prev_close/is_st/limit_price/bid1_lots` 够本仓判据用），逐票判「封住首板」
  （`algorithms/intraday.seal_hit`：在涨停价 + 昨日非板 + 10:00 前 + 封单 ≥30 万手），
  命中就写一行 `trigger_log`（`ON CONFLICT DO NOTHING`，首次触发为准）。
- `--settle`：收盘后把当日触发的「封没封住、次日开盘卖多少钱」补齐。

**为什么是「读一轮」而不是「本仓自己采」**
ADR 0003 §E2：原始采集归 CPT（它已在跑 `cpt-intraday-collect.timer`），触发判据归本仓。
两个进程同时采同一批票只会浪费配额、还会给出两份不一致的快照。

**`prev_days` 的取法**：昨日 `derived_bar.cont_days`。缺行（新股/停牌）→ `None`，
`seal_hit` 对 `None` 判 False —— 不知道昨天是不是板，就不能说今天是首板。

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
from datetime import date, time
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from emotion_core.algorithms import exit as exit_svc
from emotion_core.algorithms import intraday
from emotion_core.data import loader
from emotion_core.data.providers import cpt_intraday
from emotion_core.utils.db import connect_ro
from emotion_core.utils.dates import next_trading_day, prev_trading_day

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


#: 北京时区（CPT 的 `at` 是 UTC，交易日/时刻都要先换算再判）
_BJ = ZoneInfo("Asia/Shanghai")


def prev_days_of(d: date, codes: Iterable[str]) -> dict[str, int | None]:
    """昨日连板数：`derived_bar.cont_days`（缺行 → None，不补 0）。"""
    codes = list(codes)
    if not codes:
        return {}
    prev = prev_trading_day(d)
    if prev is None:
        return {c: None for c in codes}
    with connect_ro() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT code, cont_days FROM derived_bar WHERE date = %s AND code = ANY(%s)",
            (prev, codes))
        return {r[0]: (int(r[1]) if r[1] is not None else None) for r in cur.fetchall()}


def _names(codes: Iterable[str]) -> dict[str, str]:
    """票名（`stock_basic`）；查不到就不给名字，不拿代码充名字。"""
    codes = list(codes)
    if not codes:
        return {}
    with connect_ro() as conn, conn.cursor() as cur:
        cur.execute("SELECT code, name FROM stock_basic WHERE code = ANY(%s)", (codes,))
        return {r[0]: r[1] for r in cur.fetchall() if r[1]}


def scan_round(
    records: dict[str, dict[str, Any]],
    *,
    d: date,
    now: time,
    prev_days: dict[str, int | None],
    names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """判一轮：返回应当写进 `trigger_log` 的行（纯函数，便于单测）。

    Args:
        records: `cpt_intraday.fetch_round_from_cpt()` 的 `records`（CPT 原始 9 列）。
        d: 北京日期。
        now: 北京时刻（判「10:00 前」）。
        prev_days: 昨日连板数，来自 `prev_days_of`。
        names: 票名，来自 `_names`。

    Returns:
        `trigger_log` 行列表；判据缺数据（无 `price`、昨日板数未知、封单 NULL）一律不触发。
    """
    names = names or {}
    rows: list[dict[str, Any]] = []
    for code in sorted(records):
        rec = records[code]
        quote = cpt_intraday.to_quote(rec)
        if quote is None:
            continue
        quote["code"] = code
        name = names.get(code)
        if name:
            quote["name"] = name
        if not intraday.seal_hit(quote, prev_days.get(code), now, code=code):
            continue
        entry = quote.get("limit_price")
        if entry is None:
            prev_close = quote.get("prev_close")
            entry = intraday.limit_price(
                code, float(prev_close), name=quote.get("name")) if prev_close else None
        if entry is None:
            continue
        rows.append({
            "date": d,
            "code": code,
            "name": name,
            "first_seal_ts": now.replace(microsecond=0),
            "trigger_price": round(float(entry), 2),
            "seal_bid1_vol": int(round(float(quote["bid1_vol"]))),
            "prev_days": 0,
            "entry_px": round(float(entry), 2),
        })
    return rows


def run_round(*, dry_run: bool = False) -> dict[str, Any]:
    """读 CPT 最新一轮 → 判触发 → 写 `trigger_log`（ingest 一次，幂等）。"""
    fetched = cpt_intraday.fetch_round_from_cpt()
    if fetched is None:
        log.warning("CPT 表不存在或读不到，本轮跳过")
        return {"round_at": None, "records": 0, "triggers": 0, "written": 0, "rows": []}
    round_at, records = fetched
    if not records:
        return {"round_at": round_at, "records": 0, "triggers": 0, "written": 0, "rows": []}
    bj = round_at.astimezone(_BJ)
    d, now = bj.date(), bj.time().replace(microsecond=0)
    codes = list(records)
    rows = scan_round(records, d=d, now=now,
                      prev_days=prev_days_of(d, codes), names=_names(codes))
    written = 0 if dry_run or not rows else loader.insert_triggers(rows)
    log.info("round %s（北京 %s）：%d 票 → 触发 %d 条 → 写入 %d 条",
             round_at.isoformat(), bj.strftime("%H:%M:%S"), len(records), len(rows), written)
    return {"round_at": round_at, "records": len(records), "triggers": len(rows),
            "written": written, "rows": rows, "date": d, "now": now}


def main(argv: list[str] | None = None) -> int:
    """CLI：`--round` 判一轮触发，`--settle` 结算某日。"""
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m emotion_core.services.intraday_watch",
        description="盘中触发台账：判一轮（--round）/ 收盘结算（--settle）（ADR 0003）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--round", action="store_true",
                   help="读 CPT 最新一轮并判触发（写 trigger_log）")
    g.add_argument("--settle", action="store_true", help="结算指定日期的全部未结算触发")
    ap.add_argument("--date", metavar="YYYY-MM-DD", help="结算日（默认今天）")
    ap.add_argument("--dry-run", action="store_true", help="只算不写")
    ns = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if ns.round:
        out = run_round(dry_run=ns.dry_run)
        if out["round_at"] is None:
            print("CPT 表不存在或读不到，本轮跳过")
            return 1
        tag = "（dry-run，未写库）" if ns.dry_run else ""
        print(f"最新一轮 {out['round_at'].astimezone(_BJ):%Y-%m-%d %H:%M:%S}（北京）· "
              f"{out['records']} 票 · 触发 {out['triggers']} 条 · 写入 {out['written']} 条{tag}")
        for row in out["rows"]:
            print(f"  {row['code']} {row['name'] or ''} 触发价={row['trigger_price']} "
                  f"封单={row['seal_bid1_vol']} 股 首封={row['first_seal_ts']}")
        return 0

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
