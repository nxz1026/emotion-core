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
- 退出码：**存在未确认的 health 断档告警即返回 1**，让 systemd 把本次运行标红，
  `systemctl --failed` 立刻可见（这是"监控自己也要可被监控"的前提）。
  0 = 无未确认的 health 断档。

  ⚠️ 2026-10-07 修正：原判据是 `health.push()` 的**新入队条数 n > 0**。而
  `push` 内部先经 `record_once` 按 `(source, 归一化 detail)` 去重——同一个**还没被
  修复、也没被 ack** 的断档，第二天起 `n` 恒为 0，于是 watchdog 每次都退 0、systemd
  一路绿灯，**恰恰在故障持续期间宣布一切正常**。去重是为了不重复轰炸告警表，不是为了
  让退出码失效；退出码必须看「现在还有没有未解决断档」，而非「这一轮有没有新发现」。
  代价是：只要断档未被 ack，unit 会一直红——这正是期望行为（红色 ⇒ 待处理），
  处理完 `--ack` 才转绿。非 health 来源的告警（如 sync/review）不算断档，不影响退出码。

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
    # 判未确认断档的口径与 health.record_once 的去重视窗一致（都用 200），否则
    # 这里数出来的条数会和入队时的判重集合对不上。
    pending = alerts.pending(200)
    gaps = [a for a in pending if a["source"] == "health"]
    if gaps:
        log.error("watchdog 检出 %d 条未确认断档（本轮新入队 %d 条）；"
                  "修好后用 --ack <id> 出队才会转绿", len(gaps), n)
        for a in gaps[:5]:
            log.error("  [%s] %s", a["id"], a["detail"])
        sys.exit(1)
    log.info("watchdog 全部检查通过（本轮新入队 %d 条，其他来源未确认告警 %d 条）",
             n, len(pending))


if __name__ == "__main__":
    main()
