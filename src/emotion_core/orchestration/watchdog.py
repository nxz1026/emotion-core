"""链路看门狗：独立于 daily 主链的监控入口（2026-09-29 新增）。

## 为什么需要它

`health` 检查原本只是 daily 的**最后一个步骤**（STEPS 里排末位）。而 daily 是
fail-fast：任何一步抛异常即刻返回 1，后面的步骤根本不执行。于是形成自噬结构——
**主链死在 sync，监控它的 health 就一起死了**。2026-09-27~09-28 连续 4 次 daily 崩溃
（akshare 缺失 ×1、snapshot 守卫 ×2、psycopg3 executemany ×1），没有一次是 health
自己发现的：唯一记录是 `mark_failed` 落的那条 alert，而 alert 的 webhook 推送又因
`alerts._push` 的 import 路径写错恒定失败。三道防线同时失效，故障静默了 3 天。

`health.push()` 本身早就写好了，文档串里就写着"供独立 cron 在每日开盘前调用"，
只是**从没被部署成定时任务**，且被 `record_once` 的去重死锁彻底堵死（见该函数注释）。
本模块是它的**薄 CLI 封装**：只负责参数解析、退出码和 ack 出队，检查逻辑一律不重复实现。

## 与主链的关系

- 幂等：同一问题靠 `alerts.record_dedup`（按 source+detail）去重，不重复轰炸；
- 只读 + 写 alert：跑它不碰 daily_bar / derived_bar / signal 等业务表；
- 退出码：**检出断档返回 1**，让 systemd 把本次运行标红，`systemctl --failed` 立刻可见
  （这是"监控自己也要可被监控"的前提）。0 = 全部检查通过。

## 用法

    python -m emotion_core.orchestration.watchdog              # 跑一轮检查
    python -m emotion_core.orchestration.watchdog --list       # 看待确认告警
    python -m emotion_core.orchestration.watchdog --ack 1,2,3  # 确认并出队
    python -m emotion_core.orchestration.watchdog --date 2026-09-28
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from emotion_core.algorithms import alerts, health

log = logging.getLogger("emotion_core.watchdog")


def _fmt(items: list[dict]) -> str:
    if not items:
        return "（无未确认告警）"
    return "\n".join(f"[{a['id']}] {a['level']}/{a['source']}: {a['detail']}"
                     for a in items)


def main() -> None:
    parser = argparse.ArgumentParser(description="emotion-core 链路看门狗")
    parser.add_argument("--date", type=date.fromisoformat, default=None,
                        help="交易日 YYYY-MM-DD，默认今天")
    parser.add_argument("--list", action="store_true", help="只列未确认告警")
    parser.add_argument("--ack", type=str, default=None,
                        help="确认并出队的告警 id，逗号分隔")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.ack:
        ids = [int(x) for x in args.ack.split(",") if x.strip()]
        print(f"已确认 {alerts.ack(ids)} 条：{ids}")
        return

    if args.list:
        print(_fmt(alerts.pending(200)))
        return

    # 复用 health.push：检查→去重入队→推送。日志由 logging 统一出。
    n = health.push(args.date)
    pending = alerts.pending(30)
    if n:
        log.error("watchdog 新入队 %d 条断档；当前未确认告警 %d 条", n, len(pending))
        sys.exit(1)
    log.info("watchdog 全部检查通过（未确认告警 %d 条）", len(pending))


if __name__ == "__main__":
    main()
