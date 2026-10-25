"""CPT 盘中采集观察期监控（ADR 0003 §D10 条件② 的可观测化）。

为什么要有这个：ADR 0003 §D10 规定「CPT 盘中定时任务连续 5 个交易日无失败」是
本仓建表/起采集器的开工闸门。但这条标准有两个硬伤：

1. **不可观测** —— 只能人肉翻 `/home/ubuntu/logs/intraday-collect.log`，
   数「哪天没有 error」，数错了就放行或卡住，没人复核。
2. **"无失败"定义太松** —— 采集器每轮都 `skipped`（比如交易日判据用错表）时，
   同样「一条 error 都没有」，但其实一天都没采到东西。
   2026-10-22 实测就是这个坑：日志里 `outcome:"skipped"` 混在 `ok` 里，
   一度看起来「连续 5 天无失败」，实则当天只采到 2 轮真数据。

本模块把闸门改成**可计算**的：读 CPT 的采集日志 + 本仓 `trade_calendar`，
按「交易日 × 轮次」判定每个交易日的状态，输出「已连续干净 N 个交易日 / 还差 M 天」。

口径（写死在这里，页面与 CLI 共用）：
- 一个交易日**干净** = 当天 `outcome=ok` 的轮数 ≥ `MIN_OK_ROUNDS`
  且 `outcome=error` 的轮数 == 0；
- `empty` 不算失败（CPT 侧口径：没票接近涨停是正常结果）；
- `skipped` 计入「没采到」——落到 `ok` 轮数不足上，不单列惩罚；
- 连续 = 从**最近一个已完成的交易日**往回数，连续干净才算数；
  交易日以 `public.trade_calendar.is_open` 为准（不看 `daily_bar`——
  日线是盘后写的，盘中看它必然以为今天休市，这条 CPT 已经踩过一次）。

只读：不写任何库表，不改任何服务。
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from emotion_core.lab import db as labdb

log = logging.getLogger(__name__)

#: CPT 盘中采集日志（CPT 侧 `CPT_INTRADAY_LOG` 默认路径）。
CPT_LOG_PATH = Path("/home/ubuntu/logs/intraday-collect.log")

#: CPT 盘中采集的 systemd 单元。
CPT_COLLECT_UNIT = "cpt-intraday-collect.service"

#: CPT 看板单元（ADR 0003 §D10 条件①）。
CPT_DASHBOARD_UNIT = "cpt-dashboard.service"

#: ADR 0003 §D10 要求的连续干净交易日数。
REQUIRED_DAYS = 5

#: 一个交易日算「干净」至少要采到的成功轮数（交易时段每 2 分钟一轮，
#: 一个完整时段约 150 轮；取 10 是宽松下限，只防「整天被 skipped」）。
MIN_OK_ROUNDS = 10

#: 北京时区（本机系统时区是 UTC，日志时间戳带 +08:00 偏移）。
BJ_TZ = "Asia/Shanghai"

_TRADING_DAYS_SQL = (
    "SELECT date FROM trade_calendar WHERE is_open AND date >= %s AND date <= %s ORDER BY date"
)


@dataclass(frozen=True)
class Round:
    """一轮采集。"""

    ts: datetime
    outcome: str
    detail: str = ""


@dataclass(frozen=True)
class DayStat:
    """一个交易日的采集情况。"""

    day: date
    ok: int
    empty: int
    error: int
    skipped: int

    @property
    def rounds(self) -> int:
        return self.ok + self.empty + self.error + self.skipped

    def is_clean(self, min_ok_rounds: int = MIN_OK_ROUNDS) -> bool:
        """干净 = 没有 error，且采到的成功轮数够（skipped/empty 都不算失败）。"""
        return self.error == 0 and self.ok >= min_ok_rounds

    def as_dict(self, min_ok_rounds: int = MIN_OK_ROUNDS) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "ok": self.ok,
            "empty": self.empty,
            "error": self.error,
            "skipped": self.skipped,
            "rounds": self.rounds,
            "clean": self.is_clean(min_ok_rounds),
        }


@dataclass(frozen=True)
class WatchResult:
    """观察期结论。"""

    days: list[DayStat]
    streak: int
    required: int
    pending_day: date | None
    dashboard_errors: int | None
    log_path: str
    log_missing: bool

    @property
    def ready(self) -> bool:
        return self.streak >= self.required

    def as_dict(self, min_ok_rounds: int = MIN_OK_ROUNDS) -> dict[str, Any]:
        return {
            "days": [d.as_dict(min_ok_rounds) for d in self.days],
            "streak": self.streak,
            "required": self.required,
            "ready": self.ready,
            "pending_day": self.pending_day.isoformat() if self.pending_day else None,
            "dashboard_errors": self.dashboard_errors,
            "log_path": self.log_path,
            "log_missing": self.log_missing,
        }


def parse_log(path: Path | str = CPT_LOG_PATH) -> list[Round]:
    """读 CPT 采集日志，取 JSON 行。解析不了就跳过（不猜）。"""
    p = Path(path)
    if not p.is_file():
        return []
    out: list[Round] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        raw_ts = rec.get("at")
        if not raw_ts:
            continue
        try:
            ts = datetime.fromisoformat(str(raw_ts))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_bj_tz())
        out.append(
            Round(
                ts=ts.astimezone(_bj_tz()),
                outcome=str(rec.get("outcome") or "unknown"),
                detail=str(rec.get("error") or rec.get("reason") or ""),
            )
        )
    return out


def _bj_tz() -> Any:
    from zoneinfo import ZoneInfo

    return ZoneInfo(BJ_TZ)


def group_by_day(rounds: list[Round]) -> dict[date, DayStat]:
    """按北京日期归组。"""
    buckets: dict[date, Counter] = {}
    for r in rounds:
        buckets.setdefault(r.ts.date(), Counter())[r.outcome] += 1
    out: dict[date, DayStat] = {}
    for day, c in buckets.items():
        out[day] = DayStat(
            day=day,
            ok=c.get("ok", 0),
            empty=c.get("empty", 0),
            error=c.get("error", 0),
            skipped=c.get("skipped", 0),
        )
    return out


def trading_days(conn: Any, start: date, end: date) -> list[date]:
    """交易日历（`public.trade_calendar.is_open`）。"""
    df = labdb.query_df(_TRADING_DAYS_SQL, (start, end))
    if df.empty:
        return []
    return [d.date() if hasattr(d, "date") else d for d in df["date"].tolist()]


def dashboard_errors(since: date, unit: str = CPT_DASHBOARD_UNIT) -> int | None:
    """CPT 看板的错误条数（journalctl -p err）。取不到就返回 None，不当 0。"""
    since_str = since.isoformat() + " 00:00:00"
    cmd = [
        "journalctl",
        "-u",
        unit,
        "--since",
        since_str,
        "-p",
        "err",
        "--no-pager",
        "-o",
        "cat",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: PERF203
        log.warning("journalctl 失败：%s", exc)
        return None
    if proc.returncode != 0:
        return None
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        return 0
    if lines[0].startswith("-- No entries --"):
        return 0
    return len(lines)


def evaluate(
    conn: Any,
    *,
    now: datetime | None = None,
    log_path: Path | str = CPT_LOG_PATH,
    required: int = REQUIRED_DAYS,
    lookback_days: int = 20,
    min_ok_rounds: int = MIN_OK_ROUNDS,
    with_dashboard: bool = True,
) -> WatchResult:
    """算出观察期进度。

    `min_ok_rounds` 走参数是给测试用的；生产用默认。
    """
    now = now or datetime.now(_bj_tz())
    today = now.date()
    start = today - timedelta(days=lookback_days)
    days = trading_days(conn, start, today)
    stats = group_by_day(parse_log(log_path))

    # 今天若还没收盘，这一轮还没结束 —— 不计入连续串，单独标 pending。
    close_t = now.replace(hour=15, minute=0, second=0, microsecond=0)
    pending_day: date | None = None
    if today in days and now < close_t:
        pending_day = today
        days = [d for d in days if d != today]

    rows: list[DayStat] = []
    for d in days:
        st = stats.get(d)
        rows.append(st if st is not None else DayStat(day=d, ok=0, empty=0, error=0, skipped=0))

    streak = 0
    for st in reversed(rows):
        if st.is_clean(min_ok_rounds):
            streak += 1
        else:
            break

    derr = dashboard_errors(start) if with_dashboard else None
    return WatchResult(
        days=rows,
        streak=streak,
        required=required,
        pending_day=pending_day,
        dashboard_errors=derr,
        log_path=str(log_path),
        log_missing=not Path(log_path).is_file(),
    )


def render_text(res: WatchResult) -> str:
    """给人看的表格（CLI 用）。"""
    lines = [
        f"观察期闸门：连续干净 {res.streak}/{res.required} 个交易日 → "
        f"{'可以开工（建表 + 起采集器）' if res.ready else '还不能开工'}",
        f"日志：{res.log_path}" + ("（不存在）" if res.log_missing else ""),
        f"CPT 看板错误条数：{res.dashboard_errors if res.dashboard_errors is not None else '—'}",
    ]
    if res.pending_day:
        lines.append(f"今天（{res.pending_day}）盘中还没结束，本日不计连续串")
    lines.append("")
    lines.append("日期        轮次  ok  empty  error  skipped  干净")
    for d in res.days:
        lines.append(
            f"{d.day.isoformat()}  {d.rounds:>4}  {d.ok:>2}  {d.empty:>5}  "
            f"{d.error:>5}  {d.skipped:>7}  {'是' if d.is_clean() else '否'}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CPT 盘中采集观察期监控（只读）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--log", default=str(CPT_LOG_PATH), help="CPT 采集日志路径")
    ap.add_argument("--required", type=int, default=REQUIRED_DAYS)
    ap.add_argument("--no-dashboard", action="store_true", help="不查 CPT 看板错误")
    args = ap.parse_args(argv)

    conn = labdb.connect_lab()
    try:
        res = evaluate(
            conn,
            log_path=args.log,
            required=args.required,
            with_dashboard=not args.no_dashboard,
        )
    finally:
        conn.close()

    if args.json:
        print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_text(res))
    return 0 if res.ready else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
