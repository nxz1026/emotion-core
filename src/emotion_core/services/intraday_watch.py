"""盘中触发台账：读 CPT 一轮 → 判触发 → 写 `trigger_log`；收盘后 `--settle` 结算。

**两个半场**
- `--round`：读 CPT `public.intraday_seal_snapshot` 的**最新一轮**（R93 起 9 列，
  `price/prev_close/is_st/limit_price/bid1_lots` 够本仓判据用），逐票判「封住首板」
  （`algorithms/intraday.seal_hit`：在涨停价 + 昨日非板 + 10:00 前 + 封单 ≥30 万手），
  命中就写一行 `trigger_log`（`ON CONFLICT DO NOTHING`，首次触发为准）。
- `--loop`：常驻循环，盘中每 30 秒判一轮（`emotion-intraday.service` 的口）；
  场次按北京 09:25–15:05 + `trade_calendar.is_open` 判定，非盘中待命不扫表。
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
import time as time_mod
from datetime import date, datetime, time
from typing import Any, Final, Iterable
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

#: 场次边界（北京），按 **CPT 的采集窗口**切两段，而不是一整段 09:25–15:05。
#:
#: 为什么断开中间那一个半小时：CPT 的 timer 是 `09..11,13..14` 每 2 分钟一轮，
#: 11:30–13:00 午休**本来就不落轮次**。若当成一整段，卡死告警会在午休里
#: 按 `ALARM_GAP_SEC` 反复触发（每天三条假警），反而把真事故淹掉。
#: 上午多留 2 分钟（到 11:32）、下午提前 2 分钟（12:58 起），是给 CPT 的
#: 最后一轮/第一轮留落库余量。与 ADR 0003 §D7 一致。
SESSION_WINDOWS: Final[tuple[tuple[time, time], ...]] = (
    (time(9, 25), time(11, 32)),
    (time(12, 58), time(15, 5)),
)

#: 场次最外沿，只用于日志与本文件的边界判断。
SESSION_START: Final[time] = SESSION_WINDOWS[0][0]
SESSION_END: Final[time] = SESSION_WINDOWS[-1][1]

#: 早盘首轮宽限：CPT 09:30 起采、约 09:32 落第一轮，在此之前「没有轮次」是正常的，
#: 不告警（否则每天开盘前必来一条假警）。
ALARM_GRACE_UNTIL: Final[time] = time(9, 35)

#: 非盘中时的空闲轮询间隔（秒）。不扫表，只等下一次场次判定。
IDLE_SEC: Final[int] = 120

#: CPT 每 2 分钟落一轮。盘中超过这么多分钟没有新一轮，就当采集卡住并告警 ——
#: 常驻进程只会「读不到就跳过」，不报警的话 CPT 挂了没人知道。
STALE_MINUTES: Final[int] = 8

#: 同一类告警的最小间隔（秒）。CPT 挂一上午不该刷一百条。
ALARM_GAP_SEC: Final[int] = 1800

#: 收盘后自动结算的时刻（北京）。盘中不结 —— 收盘价都还没定。
SETTLE_AFTER: Final[time] = time(15, 5)


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


def _hits(
    records: dict[str, dict[str, Any]], *, now: time,
    prev_days: dict[str, int | None], names: dict[str, str] | None,
) -> dict[str, dict[str, Any]]:
    """这一轮「封住首板」的票：`{code: quote}`（quote 已补 code/name）。"""
    names = names or {}
    out: dict[str, dict[str, Any]] = {}
    for code in sorted(records):
        quote = cpt_intraday.to_quote(records[code])
        if quote is None:
            continue
        quote["code"] = code
        name = names.get(code)
        if name:
            quote["name"] = name
        if intraday.seal_hit(quote, prev_days.get(code), now, code=code):
            out[code] = quote
    return out


def scan_round(
    records: dict[str, dict[str, Any]],
    *,
    d: date,
    now: time,
    prev_days: dict[str, int | None],
    names: dict[str, str] | None = None,
    prev_round: tuple[time | datetime, dict[str, dict[str, Any]]] | None = None,
    confirm_ticks: int = 1,
) -> list[dict[str, Any]]:
    """判一轮：返回应当写进 `trigger_log` 的行（纯函数，便于单测）。

    Args:
        records: `cpt_intraday.fetch_round` 的 `records`（CPT 原始 9 列）。
        d: 北京日期。
        now: 北京时刻（判「10:00 前」）。
        prev_days: 昨日连板数，来自 `prev_days_of`。
        names: 票名，来自 `_names`。
        prev_round: 上一轮 `(时刻, records)`，跨轮确认用。`datetime`（UTC）会被
            转成北京时刻；传 `time` 则原样用。
        confirm_ticks: 需要连续几轮都封住才算触发（`SEAL_CONFIRM_TICKS`）。
            `2` 时要求本轮与上一轮**都**命中；没有上一轮（当天首轮）就不触发 ——
            宁可晚两分钟，也不要「单轮封单冲高就记一笔」。

    Returns:
        `trigger_log` 行列表；判据缺数据（无 `price`、昨日板数未知、封单 NULL）一律不触发。
    """
    names = names or {}
    rows: list[dict[str, Any]] = []
    hits_now = _hits(records, now=now, prev_days=prev_days, names=names)
    if not hits_now:
        return rows
    if confirm_ticks >= 2:
        if prev_round is None:
            log.info("跨轮确认：当天还没有上一轮，本轮不触发")
            return rows
        p_at, p_records = prev_round
        # `fetch_rounds` 给的是 UTC datetime；`seal_hit` 要的是**北京时刻**。
        # 不转换会在「本轮有命中」时炸 TypeError（10:00 之后命中恒为空，掩盖了它）。
        p_now = p_at.astimezone(_BJ).time() if isinstance(p_at, datetime) else p_at
        hits_prev = _hits(p_records, now=p_now, prev_days=prev_days, names=names)
        hits_now = {c: q for c, q in hits_now.items() if c in hits_prev}
    for code in sorted(hits_now):
        quote = hits_now[code]
        name = names.get(code)
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


def run_round(
    *, dry_run: bool = False, confirm_ticks: int | None = None, quiet: bool = False
) -> dict[str, Any]:
    """读 CPT 最近一轮（+ 上一轮做确认）→ 判触发 → 写 `trigger_log`（幂等）。

    `quiet=True` 且本轮没写入时，汇总行降到 DEBUG：常驻进程 30 秒一轮、CPT 2 分钟
    才落一轮，同一条轮次会被读 4 次，INFO 级别下一天能刷出两千多条一样的日志。
    """
    ticks = intraday.SEAL_CONFIRM_TICKS if confirm_ticks is None else int(confirm_ticks)
    want = max(2, int(ticks)) if ticks >= 2 else 1
    rounds = (
        cpt_intraday.fetch_rounds_from_cpt(want) if want > 1
        else _single_round()
    )
    if rounds is None:
        log.warning("CPT 表不存在或读不到，本轮跳过")
        return {"round_at": None, "records": 0, "triggers": 0, "written": 0, "rows": []}
    if not rounds:
        return {"round_at": None, "records": 0, "triggers": 0, "written": 0, "rows": []}
    round_at, records = rounds[0]
    if not records:
        return {"round_at": round_at, "records": 0, "triggers": 0, "written": 0, "rows": []}
    bj = round_at.astimezone(_BJ)
    d, now = bj.date(), bj.time().replace(microsecond=0)
    prev_round = None
    if len(rounds) > 1:
        p_at, p_records = rounds[1]
        p_bj = p_at.astimezone(_BJ)
        prev_round = (p_bj.time().replace(microsecond=0), p_records)
    codes = list(records)
    rows = scan_round(records, d=d, now=now, prev_days=prev_days_of(d, codes),
                      names=_names(codes), prev_round=prev_round,
                      confirm_ticks=ticks)
    written = 0 if dry_run or not rows else loader.insert_triggers(rows)
    level = logging.DEBUG if quiet and not written else logging.INFO
    log.log(level, "round %s（北京 %s）：%d 票 → 确认轮数 %d → 触发 %d 条 → 写入 %d 条",
            round_at.isoformat(), bj.strftime("%H:%M:%S"), len(records),
            len(rounds), len(rows), written)
    return {"round_at": round_at, "records": len(records), "triggers": len(rows),
            "written": written, "rows": rows, "date": d, "now": now,
            "rounds": len(rounds)}


def _single_round() -> list[tuple[Any, dict[str, dict[str, Any]]]] | None:
    """`confirm_ticks == 1` 时只读最新一轮（省一次跨轮查询）。"""
    fetched = cpt_intraday.fetch_round_from_cpt()
    if fetched is None:
        return None
    at, records = fetched
    return [(at, records)] if records else []


def is_trading_day(d: date) -> bool:
    """是不是交易日：以 `trade_calendar.is_open` 为准。

    不看 `daily_bar` 的最新日期 —— 日线是盘后 pipeline 才写的，盘中查它必然
    得到「今天不是交易日」（CPT R89 就是这么翻的车，见 ADR 0003 §D10 ②）。
    日历表没有这天时退回「周一到周五」。
    """
    with connect_ro() as conn, conn.cursor() as cur:
        cur.execute("SELECT is_open FROM trade_calendar WHERE date = %s", (d,))
        row = cur.fetchone()
    return bool(row[0]) if row else d.weekday() < 5


def _round_age_min(round_at: Any, now_bj: datetime) -> float | None:
    """最新一轮距今多少分钟。`round_at` 是 UTC datetime；取不到返回 None。"""
    if not isinstance(round_at, datetime):
        return None
    at_bj = round_at if round_at.tzinfo else round_at.replace(tzinfo=ZoneInfo("UTC"))
    return (now_bj - at_bj.astimezone(_BJ)).total_seconds() / 60.0


def _peek_round_at() -> Any:
    """只看最新一轮的时间戳（判「还是同一轮」用，不读行）。"""
    try:
        with connect_ro() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT max(at) FROM {cpt_intraday.CPT_INTRADAY_TABLE} "
                f"WHERE code NOT LIKE {cpt_intraday._SENTINEL_PATTERN}"
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:  # noqa: BLE001 - 读不到就当「不知道」，走完整一轮
        return None


def _alarm(text: str) -> bool:
    """推一条告警（纯文本，不走 LLM 改写 —— 告警的价值全在字面准确）。"""
    from emotion_core.services import notify

    return notify.push_text(text)


def in_session(at_bj: datetime, *, trading_day: bool | None = None) -> bool:
    """此刻该不该扫：工作日 + 场内时段（午休不算）+ 交易日（`trading_day` 可注入）。"""
    if at_bj.weekday() >= 5:
        return False
    at = at_bj.time().replace(microsecond=0)
    if not any(start <= at <= end for start, end in SESSION_WINDOWS):
        return False
    if trading_day is None:
        trading_day = is_trading_day(at_bj.date())
    return bool(trading_day)


def _stale_text(res: dict[str, Any], now_bj: datetime, age: float | None) -> str:
    """卡死告警正文：说清「现在几点、数据停在哪、去哪里查」。"""
    tail = ("查：systemctl status cpt-intraday-collect.timer；"
            "tail -20 /home/ubuntu/logs/intraday-collect.log")
    if age is None:
        return (f"⚠ emotion-core 盘中触发停机\n"
                f"{now_bj:%Y-%m-%d %H:%M}（北京）读不到 CPT 封板表的最新一轮。\n"
                f"本进程还在跑，但没有数据就不会产生触发记录。\n{tail}")
    round_at = res.get("round_at")
    at_bj = round_at.astimezone(_BJ) if isinstance(round_at, datetime) else "?"
    return (f"⚠ emotion-core 盘中触发停机\n"
            f"{now_bj:%Y-%m-%d %H:%M}（北京）CPT 最新一轮是 "
            f"{at_bj:%H:%M}，已 {age:.0f} 分钟没有新轮次"
            f"（阈值 {STALE_MINUTES} 分钟）。\n{tail}")


def _settle_text(res: dict[str, Any]) -> str:
    """收盘结算摘要正文。"""
    return (f"emotion-core 盘中台账已结算 {res['date']:%Y-%m-%d}\n"
            f"触发 {res['triggers']} 条 · 结算 {res['settled']} 条 · "
            f"待定 {res['pending']} 条")


def loop(
    *, dry_run: bool = False, interval: int | None = None,
    max_rounds: int | None = None, sleep=None, now_fn=None,
) -> int:
    """常驻循环：盘中每 `SCAN_INTERVAL_SEC` 判一轮，非盘中待命。

    盘中额外做两件事（cron 撤掉后靠它们自监控）：
      1. **卡死告警**：最新一轮距今超过 `STALE_MINUTES` 分钟就推一条告警
         （同类告警 `ALARM_GAP_SEC` 内不重复）。没有这条，CPT 挂一上午
         本进程只会「读不到就跳过」，台账不长也没人知道。
      2. **收盘自动结算**：过了 `SETTLE_AFTER` 且当天还没结过 → 跑一次
         `settle` 并推汇总。没有这条，台账会永远停在 `settled_at IS NULL`。

    `max_rounds`/`sleep`/`now_fn` 只给单测用（生产由 systemd 常驻）。
    """
    step = max(5, int(interval if interval is not None else intraday.SCAN_INTERVAL_SEC))
    wait = sleep or time_mod.sleep
    clock = now_fn or (lambda: datetime.now(_BJ))
    log.info("盘中扫描循环启动：间隔 %ds，场次 %s（北京，午休不扫）",
             step, " / ".join(f"{a:%H:%M}–{b:%H:%M}" for a, b in SESSION_WINDOWS))
    last: bool | None = None
    last_alarm: datetime | None = None
    last_round: Any = None
    settled: date | None = None
    done = 0
    while max_rounds is None or done < max_rounds:
        now = clock()
        active = in_session(now)
        if active != last:
            log.info("场次切换：%s（北京 %s）", "进入盘中" if active else "离开盘中",
                     now.strftime("%Y-%m-%d %H:%M:%S"))
            last = active
        if not active:
            if now.time() >= SETTLE_AFTER and settled != now.date():
                try:
                    res = settle(now.date(), dry_run=dry_run)
                    settled = now.date()
                    log.info("收盘结算：触发 %d 条，结算 %d 条，待定 %d 条",
                             res["triggers"], res["settled"], res["pending"])
                    if res["triggers"]:
                        _alarm(_settle_text(res))
                except Exception:  # noqa: BLE001 - 结算失败不该让循环退出
                    log.exception("收盘结算失败，明天再试")
            wait(IDLE_SEC)
            done += 1
            continue
        res: dict[str, Any] = {}
        try:
            same = last_round is not None and last_round == _peek_round_at()
            res = run_round(dry_run=dry_run, quiet=same) or {}
            last_round = res.get("round_at")
        except Exception:  # noqa: BLE001 - 单轮失败不停摆
            log.exception("本轮扫描失败，等下一轮")
        age = _round_age_min(res.get("round_at"), now)
        if now.time() >= ALARM_GRACE_UNTIL and (age is None or age > STALE_MINUTES):
            gap = last_alarm is None or (now - last_alarm).total_seconds() >= ALARM_GAP_SEC
            if gap:
                ok = _alarm(_stale_text(res, now, age))
                last_alarm = now
                # 成功也要留痕：告警是异步的，日志没有「已推送」就等于没法回溯
                log.warning("卡死告警已触发（%s）：最新一轮 %s，距今 %s 分钟",
                            "已推送" if ok else "推送未配置/失败（不影响扫描）",
                            res.get("round_at"), "?" if age is None else f"{age:.0f}")
        wait(step)
        done += 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI：`--round` 判一轮触发，`--settle` 结算某日。"""
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m emotion_core.services.intraday_watch",
        description="盘中触发台账：判一轮（--round）/ 收盘结算（--settle）（ADR 0003）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--round", action="store_true",
                   help="读 CPT 最新一轮并判触发（写 trigger_log）")
    g.add_argument("--loop", action="store_true",
                   help="常驻：盘中每 SCAN_INTERVAL_SEC 判一轮（systemd 用）")
    g.add_argument("--settle", action="store_true", help="结算指定日期的全部未结算触发")
    ap.add_argument("--date", metavar="YYYY-MM-DD", help="结算日（默认今天）")
    ap.add_argument("--dry-run", action="store_true", help="只算不写")
    ns = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if ns.loop:
        try:
            return loop(dry_run=ns.dry_run)
        except KeyboardInterrupt:
            return 0

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
