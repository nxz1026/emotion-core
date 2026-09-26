"""P2-5 链路健康推送：断档主动触达（独立于 daily.sh 成败）。

与 doctor（人工一键自检清单）职责区分：health 面向**主动推送**——
可 cron 独立运行（如每个交易日 09:30），发现断档即入 alert 队列
（WARN 级，可确认），由 push_pending_webhook 统一触达 webhook。

语义逐字照搬 lkl/services/health.py（docs/07-算法层搬运方案.md §3.4，131 行），
判定与口径零改动：
- 检查三条：报告断档（REPORT_GAP_DAYS=2）/ 数据链断档（DATA_GAP_DAYS=2）/
  信号回填断档（OUTCOME_STALE_DAYS=6），阈值常量、SQL 文本、`、` 连接文案逐字保留；
- `_trading_gap` 为 (since, trade_date) **开区间**内已入库交易日数（since 与 trade_date
  自身均不计），since=None 返回 10**9；
- `run` 按 CHECKS 顺序 (("report",…),("data",…),("outcome",…)) 收集并丢弃 None；
- `record_once` 去重按 `alerts.pending(200)` 的 source 集合（src 恒 "health"）——
  同 source 已有未确认告警则跳过，确认后才允许再次入队；
- `push` = record_once(run(td)) 后 push_pending_webhook（webhook 失败仅 warning，不抛）。

与 lkl 的差异（全部是契约/IO 适配，不涉判定逻辑）：
1. 读库入口 `lkl.utils.db.query_df` → `emotion_core.utils.db.query_df`（同签名
   `query_df(sql, params=(), conn=None) -> DataFrame`），SQL 文本逐字未改。
2. ★ 缺失依赖降级：lkl 的 `dates.recent_trading_days(d, n)`（d 及之前最近 n 个交易日、
   升序）未随本轮迁入——`emotion_core.utils.dates.recent_trading_days(n)` 签名/语义不同
   （无日期上界）；本轮范围限定只新增 health.py + test_health.py 两文件，公共 util 不在
   范围内，故本模块内自持 `_recent_trading_days(d, n)`，SQL 与 lkl 逐字
   （`SELECT DISTINCT date FROM daily_bar WHERE date <= %s ORDER BY date DESC LIMIT %s`，
   再 `sorted`）。**已知 gap**：待公共 util 补齐日期上界后应改回
   dates.recent_trading_days，两处调用点（_trading_gap 的 60、check_outcome_stale 的
   OUTCOME_STALE_DAYS+1）不变。
3. `dates.today_sh()` 已在 emotion_core.utils.dates 就位，调用点保持
   `from emotion_core.utils import dates` / `dates.today_sh()` 与 lkl 同形。
4. alerts 路径 `lkl.services.alerts` → `emotion_core.algorithms.alerts`（pending /
   record / push_pending_webhook 同签名）；lkl 在函数内局部 import，本模块改为模块顶层
   `from emotion_core.algorithms import alerts`——无循环依赖、无行为差异，且便于独立导入
   与注入替身。alert 表读写语义（WARN 级、按 source 去重、webhook 只推未确认）全部由
   alerts 模块承载，health 不重复实现。
5. 逐字保留、勿当遗漏的 lkl 行为：`record_once` 每轮**只入队一次**——首条入队后即把
   "health" 加入 pending 集合，同一轮后续 detail 全部跳过（lkl 原逻辑如此）。

自持 SQL 1 处（lkl dates 原语句）：`_recent_trading_days`；其余 SQL 均为 lkl health 原语句。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.algorithms import alerts
from emotion_core.utils import dates
from emotion_core.utils.db import query_df

log = logging.getLogger("emotion_core.health")

# 阈值（交易日）——与 daily.sh 一次跑通周期对齐，非交易日 SKIP 不计数
REPORT_GAP_DAYS = 2          # 报告断档：>1 交易日前有报告即算断
DATA_GAP_DAYS = 2            # 数据链：derived_bar 缺最近交易日
OUTCOME_STALE_DAYS = 6       # 信号结果：>5 交易日前信号仍 incomplete


def _recent_trading_days(d: date, n: int) -> list[date]:
    """d 及之前最近 n 个已入库交易日（升序）——lkl dates.recent_trading_days 语义。"""
    df = query_df(
        "SELECT DISTINCT date FROM daily_bar WHERE date <= %s"
        " ORDER BY date DESC LIMIT %s", (d, n))
    return sorted(df["date"])


def _trading_gap(trade_date: date, since: date | None) -> int:
    """(since, trade_date) 开区间内的已入库交易日数；since=None 返回极大值。"""
    if since is None:
        return 10**9
    days = _recent_trading_days(trade_date, 60)
    return sum(1 for d in days if since < d < trade_date)


def check_report_gap(trade_date: date) -> str | None:
    """最近成功报告距今是否 ≥REPORT_GAP_DAYS 交易日；无报告返回告警文本。"""
    df = query_df("SELECT max(date) AS d FROM review_report"
                  " WHERE date < %s", (trade_date,))
    last = df["d"].iloc[0] if not df.empty else None
    if last is None:
        return f"从未产出报告（截至 {trade_date} 无 review_report 落盘）"
    gap = _trading_gap(trade_date, last)
    return (f"报告断档：最近成功报告 {last} 距今 {gap} 个交易日（阈值"
            f" {REPORT_GAP_DAYS}），疑似 daily.sh 未跑/中断"
            if gap >= REPORT_GAP_DAYS else None)


def check_data_gap(trade_date: date) -> str | None:
    """derived_bar 最近数据日距今是否 ≥DATA_GAP_DAYS 交易日（采集停摆）。"""
    df = query_df("SELECT max(date) AS d FROM derived_bar WHERE date < %s",
                  (trade_date,))
    last = df["d"].iloc[0] if not df.empty else None
    if last is None:
        return f"derived_bar 无任何历史数据（截至 {trade_date}）"
    gap = _trading_gap(trade_date, last)
    return (f"derived_bar 最近数据日 {last} 距今 {gap} 个交易日（阈值"
            f" {DATA_GAP_DAYS}），疑似 fetch/derive 采集停摆"
            if gap >= DATA_GAP_DAYS else None)


def check_outcome_stale(trade_date: date) -> str | None:
    """≥6 交易日前 BUY 信号仍无 complete outcome → L3 回填断档。

    complete=True 需前向 5 日数据齐（outcome.py 语义）；confirm_date 距今
    ≥6 交易日仍未 complete 即异常（daily.sh 每日末尾 outcome 幂等回填）。
    """
    cutoff = _recent_trading_days(trade_date, OUTCOME_STALE_DAYS + 1)
    if len(cutoff) < OUTCOME_STALE_DAYS + 1:
        return None                        # 历史不足，无从判定
    stale = cutoff[0]                      # 第 6 个交易日前
    df = query_df(
        "SELECT s.confirm_date, s.code FROM signal s"
        " LEFT JOIN signal_outcome o USING (confirm_date, code, action)"
        " WHERE s.action = 'BUY' AND s.confirm_date <= %s"
        "   AND (o.confirm_date IS NULL OR NOT o.complete)"
        " ORDER BY s.confirm_date DESC LIMIT 5", (stale,))
    if df.empty:
        return None
    names = "、".join(f"{r.confirm_date} {r.code}" for r in df.itertuples())
    return (f"{len(df)} 条 ≥{OUTCOME_STALE_DAYS} 交易日前 BUY 信号无 complete"
            f" outcome（L3 未回填）：{names}")


CHECKS = (("report", check_report_gap), ("data", check_data_gap),
          ("outcome", check_outcome_stale))


def run(trade_date: date | None = None) -> list[str]:
    """全部检查，返回告警 detail 列表（未断档的不含）。"""
    td = trade_date or dates.today_sh()
    return [detail for _, fn in CHECKS
            for detail in [fn(td)] if detail]


def record_once(details: list[str], trade_date: date | None = None) -> int:
    """断档入 alert 队列（去重：同 source 已有未确认告警则跳过）。

    返回新入队条数；webhook 推送由调用方（push_pending_webhook）统一做。
    """
    pending = {a["source"] for a in alerts.pending(200)}
    n = 0
    for detail in details:
        src = "health"
        if src in pending:
            continue                        # 未确认断档仍在，不重复轰炸
        try:
            alerts.record("WARN", src, detail)
            n += 1
            pending.add(src)
        except Exception:                    # noqa: BLE001
            log.warning("health 告警入队失败：%s", detail)
    return n


def push(trade_date: date | None = None) -> int:
    """检查→去重入队→推送未确认告警。返回本轮入队断档条数。

    daily.sh 主链成功后无需再跑（无断档=空转）；供独立 cron 在每日
    开盘前调用——主链若昨夜中断，此处即主动触达，不等用户发现。
    """
    n = record_once(run(trade_date), trade_date)
    try:
        alerts.push_pending_webhook()
    except Exception:                        # noqa: BLE001
        log.warning("health webhook 推送失败（不影响告警入队）")
    return n
