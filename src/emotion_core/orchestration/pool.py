"""东财三池同步 CLI —— 逐日拉取 `ingest.fetch_limit_pool` 的独立入口。

**为什么不进 daily**：daily 是 fail-fast 的 13 步主链，而池同步是「批量拉取 +
有限重试」的外网调用——三池全挂会抛 RuntimeError（`fetch_limit_pool` 的 A7
护栏），直接挂进主链会因一个外部数据源不可用而拖红整条日更。故独立成单元。

背景：本函数此前**在全仓没有任何调用方**，而 daily 的 sync 步只调
`snapshot_daily`（写 daily_bar），于是 `limit_pool_em` 至今 0 行。后果不是
「对账缺一半」这么轻——`entry.py` c5 的「炸板≥1次回封」补偿分支因
`bomb_times` 恒 None 而恒不成立，等于这条判据在 ENHANCED 窗口从未生效。

用法：
    python -m emotion_core.orchestration.pool --date 2026-09-28   # 单日
    python -m emotion_core.orchestration.pool --last 5             # 最近 5 个交易日
    python -m emotion_core.orchestration.pool --latest             # 最近交易日（默认）
    python -m emotion_core.orchestration.pool --last 5 --dry-run   # 只拉不打库
    python -m emotion_core.orchestration.pool --last 3 --per-day-timeout 180

窗口：EM 池只保留最近 `_POOL_RECENT_DAYS`（当前 30）个交易日，更早的日期
拉不到——本模块会按同一上限自动钳制（与 `ingest.sync_range` 口径一致）。
"""

from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

# ── 超时防护（2026-10-09 日志巡检 F）─────────────────────────────────────────
# 旧写法只在这里 `socket.setdefaulttimeout(15)`，就认为「EM 挂死已修」。但
# 2026-10-08T09:45:49 的 `池同步 3 天` 仍只落了 09-29 一行，随即卡到
# systemd TimeoutStartSec=600 被杀（CPU 仅 935ms，说明不是在算，而是在等）。
# 代码级原因（逐行可查，不是「可能」）：akshare 池函数是
# `r = requests.get(url, params=params)`（akshare/stock_feature/stock_ztb_em.py），
# **没传 timeout** → requests 在 adapters.py:60 把它转成显式
# `TimeoutSauce(connect=None, read=None)` → urllib3 `_validate_timeout`
# （urllib3/util/timeout.py:141）对 None 原样返回 → 于是
# `HTTPConnection._new_conn` 的 `connection.py:239 socket.create_connection(addr,
# self.timeout)` 与 `connection.py:506 self.sock.settimeout(self.timeout)` 拿到的
# 都是**显式 None = 永久阻塞**。
# `socket.setdefaulttimeout` 只在调用方**根本没传**超时参数（走
# `_GLOBAL_DEFAULT_TIMEOUT` 哨兵）时才生效，被显式 None 直接绕过——这就是
# 15s 兜不住当前卡死路径的原因。故保留下面这行仅作纵深防御，真正的兜底是
# `_day_deadline` 的逐日 SIGALRM 硬超时（见下）。
# DB 侧已显式 connect_timeout=15（utils/db.py:_kwargs），与全局不冲突。
socket.setdefaulttimeout(15)

from emotion_core.utils.dates import today_sh  # noqa: E402

log = logging.getLogger("emotion_core.pool")

#: 单个交易日的抓取硬上限（秒）。systemd TimeoutStartSec=600 下，`--last 3`
#: 最坏 3×180=540s，仍留出重试与打库余量。
_DEFAULT_PER_DAY_TIMEOUT = 180.0


class _DayTimeout(BaseException):
    """单个交易日抓取的硬超时信号（2026-10-09 日志巡检 F）。

    故意继承 ``BaseException`` 而不是 ``Exception``：``ingest.fetch_limit_pool``
    逐池 ``except Exception``，``utils.fetch.retry_fetch`` 也 ``except Exception``
    （fetch.py:100 起的重试循环）。若本异常是 Exception 子类，会被它们吞成
    「本池失败 / 本日空池」，超时就被伪装成普通数据缺失——正是这次事故里最
    难查的静默失败。继承 BaseException 保证它穿过这些兜底继续上抛，
    由 ``_sync_days`` 明确记账为「硬超时」。
    """


def _on_alarm(signum, frame) -> None:
    """SIGALRM 处理器：只负责把硬超时变成异常。"""
    raise _DayTimeout("per-day fetch deadline exceeded")


@contextmanager
def _day_deadline(seconds: float) -> Iterator[None]:
    """单日抓取硬上限：到点抛 ``_DayTimeout``；``seconds<=0`` 表示关闭。

    用 ``signal.setitimer(ITIMER_REAL)`` 而不是线程池/进程池：SIGALRM 能打断
    **正在阻塞的 socket recv 与 time.sleep**（线程根本杀不掉这种状态），
    而 EM 接口挂死时进程恰恰就停在那里。约束与清理：

    - 只在主线程可用（signal 模块的硬限制）。非主线程时**直接报错**，
      不静默降级成「没有兜底」；调用方若要关就用 ``--per-day-timeout 0``。
    - ``finally`` 里无条件取消定时器，并还原原有的 SIGALRM handler 与
      原 itimer，避免污染调用方或让定时器在本日结束后又炸出来。
    """
    if seconds <= 0:
        yield
        return
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError(
            "逐日硬超时依赖 SIGALRM，只能在主线程启用；请改用 --per-day-timeout 0 关闭"
        )
    previous_handler = signal.signal(signal.SIGALRM, _on_alarm)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def _trading_days(end: date, n: int) -> list[date]:
    """截至 end（含）的最近 n 个交易日。"""
    from emotion_core.utils.dates import trading_days

    return trading_days(date(end.year - 2, 1, 1), end)[-n:]


def _sync_days(days: list[date], per_day_timeout: float) -> list[date]:
    """逐日同步（每天一个独立硬上限），返回失败的交易日列表。

    2026-10-09 日志巡检 F：原实现把整段交给 ``ingest.sync_range``，而它只在
    **日与日之间** try 续跑；单日内 akshare/requests 永久阻塞时根本不抛异常，
    于是整轮挂死到 systemd 600s 杀进程，当天池数据一个字都没写。这里把
    「一天」作为超时与记账单元：超时/失败只放弃该天并继续下一天，已写成功
    的天（以及同日内已提交的池）保留，不回滚、不重来。

    记账语义与 ``sync_range`` 一致（返回失败日列表），只是「失败」多了硬超时
    这一类；``days`` 按 ``_POOL_RECENT_DAYS`` 钳制，日志行保持原格式。
    """
    from emotion_core.services import ingest

    days = days[-ingest._POOL_RECENT_DAYS :]
    log.info("池同步 %d 天（EM 池历史上限 %d 日）", len(days), ingest._POOL_RECENT_DAYS)
    failed: list[date] = []
    for d in days:
        started = time.monotonic()
        try:
            with _day_deadline(per_day_timeout):
                n = ingest.fetch_limit_pool(d)
        except _DayTimeout:
            log.warning(
                "%s 池同步硬超时（单日上限 %.0fs，已耗时 %.1fs），跳过该日继续",
                d,
                per_day_timeout,
                time.monotonic() - started,
            )
            failed.append(d)
            continue
        except RuntimeError as exc:
            log.warning("%s 池同步失败，跳过：%s", d, exc)
            failed.append(d)
            continue
        bad = ingest.verify_pre_close(d)
        log.info("%s 三池=%d pre_close异常=%d", d, n, len(bad))
    return failed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m emotion_core.orchestration.pool",
        description="同步东财涨停/炸板/跌停三池 → limit_pool_em",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--date", help="交易日 YYYY-MM-DD")
    g.add_argument(
        "--last", type=int, metavar="N", help="最近 N 个交易日（受 EM 池窗口限制自动钳制）"
    )
    g.add_argument("--latest", action="store_true", help="最近一个交易日（默认行为）")
    p.add_argument("--dry-run", action="store_true", help="只拉取不打库，用于验通外网与列名")
    p.add_argument(
        "--per-day-timeout",
        type=float,
        metavar="SEC",
        default=_DEFAULT_PER_DAY_TIMEOUT,
        help="单个交易日抓取的硬上限（秒；<=0 关闭，默认 %(default)s）。"
        "需保证 systemd TimeoutStartSec > 天数 × 本值",
    )
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.date:
        try:
            end = date.fromisoformat(args.date)
        except ValueError:
            print(f"日期格式错误：{args.date!r}，应为 YYYY-MM-DD", file=sys.stderr)
            return 2
        days = _trading_days(end, 1)
    else:
        n = args.last or 1
        # ⚠️ 2026-10-07：原为 `date.today()`。机器时区 Etc/UTC ⇒ 北京时间 00:00~08:00
        # 之间少一天，同 strategy.py。口径统一走 `utils.dates.today_sh()`
        # （algorithms/entry.py:266 的 P1-3：禁止隐式 date.today()）。
        days = _trading_days(today_sh(), n)
    if not days:
        print("未能确定交易日", file=sys.stderr)
        return 1

    if args.dry_run:
        from emotion_core.services import ingest
        from emotion_core.utils.fetch import retry_fetch

        for d in days:
            for ptype, (fn, _m) in ingest._POOLS.items():
                try:
                    df = retry_fetch(fn, date=d.strftime("%Y%m%d"))
                    print(f"{d} {ptype}: {len(df)} 行")
                except Exception as exc:  # noqa: BLE001
                    print(f"{d} {ptype}: FAIL {type(exc).__name__}: {exc}")
        return 0

    try:
        failed = _sync_days(days, args.per_day_timeout)
    except RuntimeError as exc:
        # 只可能是 _day_deadline 的「非主线程」护栏：宁可让 systemd 标红，
        # 也不能在失去兜底的情况下继续跑。
        log.error("逐日硬超时不可用：%s", exc)
        return 1

    if failed:
        # 2026-10-09 日志巡检 F 的语义选择：部分天失败仍返回 1（沿用原
        # `sync_range`「failed 非空即非 0」）。缺池的交易日复盘会渲染成
        # 「东财池缺失 → 不可用于决策」，必须让 systemd 标红 + 日报告警，
        # 运维次日重跑补齐；返回 0 等于把「3 天只成 1 天」当成功，与 A7
        # 「宁可中断也不出假报告」的方向相反。
        # 注意：非超时的失败日在 _sync_days 里已逐条 WARNING，这里只汇总。
        log.error(
            "%d/%d 个交易日池同步失败（硬超时或数据源事故）：%s",
            len(failed),
            len(days),
            "、".join(str(d) for d in failed),
        )
        return 1
    log.info("池同步完成 %d 个交易日：%s ~ %s", len(days), days[0], days[-1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
