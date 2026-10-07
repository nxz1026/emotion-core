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
   （`SELECT date FROM trade_calendar WHERE is_open AND date <= %s ORDER BY date DESC
   LIMIT %s`，再 `sorted`）。**2026-10-07 已改**：原先查的是 `SELECT DISTINCT date FROM
   daily_bar`，即**从被监控对象自己派生日历** ⇒ 监控自噬，详见
   `_recent_trading_days` 的 docstring。语义与参数不变，两个调用点
   （_trading_gap 的 60、check_outcome_stale 的 OUTCOME_STALE_DAYS+1）也无需改动。
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
import re
from datetime import date

from emotion_core.algorithms import alerts
from emotion_core.algorithms import pipeline as _pipeline_mod
from emotion_core.utils import dates
from emotion_core.utils.db import query_df

_DATE_IN_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}")

log = logging.getLogger("emotion_core.health")

# 阈值（交易日）——与 daily.sh 一次跑通周期对齐，非交易日 SKIP 不计数
REPORT_GAP_DAYS = 2          # 报告断档：>1 交易日前有报告即算断
DATA_GAP_DAYS = 2            # 数据链：derived_bar 缺最近交易日
OUTCOME_STALE_DAYS = 6       # 信号结果：>5 交易日前信号仍 incomplete
POOL_STALE_DAYS = 2          # 东财三池：落后衍生层 1 个交易日即算断


def _recent_trading_days(d: date, n: int) -> list[date]:
    """d 及之前最近 n 个**交易日**（升序）。

    ⚠️ 2026-10-07：原来这里查的是 ``SELECT DISTINCT date FROM daily_bar``，
    即**从被监控对象自己派生日历**。后果是**监控自噬**：
    ``daily_bar`` 一停写，派生日历同步变短 ⇒ 断档天数变小 ⇒ 低于
    ``DATA_GAP_DAYS`` ⇒ **恰好在最该报警的时候沉默**。

    2026-10-07 现场：上游 pipeline 停摆、共享表停在 2026-09-30 已 8 天，
    而这与 watchdog 的退出码缺陷、close.service 丢弃返回码三者叠加，零告警。

    改用 ``trade_calendar``（独立于行情数据；生产实测覆盖
    1990-12-19 ~ 2026-12-31，含 2026 国庆假与 10-08 之后的交易日），
    因此**不会**退化成 ``data/trade_calendar.py`` 的 ``weekday()`` 降级。
    语义与参数不变，两个调用点（``_trading_gap`` 的 60、
    ``check_outcome_stale`` 的 ``OUTCOME_STALE_DAYS+1``）无需改动。

    ⚠️ 读不到日历时返回**空列表并记 error**，而不是让上层拿着一个坏日历
    算出偏多的「应到未到」。宁可让断档检查说「我算不出来」，
    也不要给一个看似确定的错结论。
    """
    try:
        df = query_df(
            "SELECT date FROM trade_calendar WHERE is_open AND date <= %s"
            " ORDER BY date DESC LIMIT %s", (d, n))
    except Exception as exc:  # noqa: BLE001
        log.error("交易日历读取失败，断档检查给不出可信结论：%s: %s", type(exc).__name__, exc)
        return []
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


def check_pipeline_failed(trade_date: date) -> str | None:
    """日更链有步骤停在 FAILED（未恢复）→ 断档。

    ★2026-09-29 新增。原 CHECKS 三条全是**数据侧**断档（报告/derived_bar/signal_outcome），
    没有任何一条能发现「daily 自己挂了」：2026-09-27~09-28 连续 4 次 daily 在 sync/ladder
    崩掉时，health 因为排在 STEPS 末位**根本没机会执行**（主链 fail-fast 即退出），
    于是只剩 mark_failed 写的那条 alert，而它推不出去（见 alerts._push 的 import 错路径）。
    本条读 `pipeline_state` 的 latest-state 打点，让**独立于日更链**的 watchdog 能看见主链死没死。

    pipeline_state 是 UPSERT 覆盖（step 为主键，非历史），因此这里报的是"当前仍处于
    FAILED 的步骤"，不是"哪天的哪步失败过"——历史失败只由 alert 表承载。
    """
    bad = [s for s in _pipeline_mod.failures()]
    if not bad:
        return None
    names = "、".join(f"{s['step']}({s['for_date'] or '无日期'})"
                      f"{'：' + s['detail'][:120] if s['detail'] else ''}"
                      for s in bad[:5])
    return (f"日更链有 {len(bad)} 个步骤停在 FAILED（未恢复）：{names}")


def check_pool_stale(trade_date: date) -> str | None:
    """东财三池停摆：limit_pool_em 最近日落后 derived_bar 最近日 ≥阈值。

    ★2026-09-29 新增。本条堵的正是刚被发现的静默缺口：`ingest.sync_range` 在全仓
    **没有任何调用方**，而 daily 的 sync 步只调 `snapshot_daily`（写 daily_bar），
    于是 `limit_pool_em` 至今 0 行。不是"拉取失败"——是"从未被安排进任何流程"，
    故数据侧的三条断档（报告/derived_bar/outcome）全都看不见它。

    参照系用 **derived_bar 的最近日**而非 trade_date 本身：池是独立的
    systemd 单元，若在 daily 之后、watchdog 之前跑，"今天还没落"属正常，
    用 trade_date 判会天天误报。落后衍生层才说明同步单元真停摆了。

    断档不只影响对账——`entry.py` c5 的「炸板≥1次回封」补偿分支靠
    bomb_times，该列来自 limit_pool_em，池空则该分支恒不成立。
    """
    got = query_df("SELECT max(date) AS d FROM limit_pool_em")
    have = query_df("SELECT max(date) AS d FROM derived_bar")
    last_pool = got["d"].iloc[0] if not got.empty else None
    last_data = have["d"].iloc[0] if not have.empty else None
    if last_pool is None:
        return ("limit_pool_em 无任何数据：东财三池从未同步——c5「炸板≥1次回封」"
                "补偿分支恒不成立，复盘双源对账不可信")
    if last_data is None:
        return None                        # 衍生层也无数据，归 data 断档管
    gap = _trading_gap(last_data, last_pool)
    return (f"东财三池断档：limit_pool_em 最近 {last_pool}，落后 derived_bar"
            f" {last_data} 共 {gap} 个交易日（阈值 {POOL_STALE_DAYS}）——"
            f"c5 炸板回封补偿与双源对账均失效"
            if gap >= POOL_STALE_DAYS else None)


CHECKS = (("report", check_report_gap), ("data", check_data_gap),
          ("outcome", check_outcome_stale), ("pipeline", check_pipeline_failed),
          ("pool", check_pool_stale))


def run(trade_date: date | None = None) -> list[str]:
    """全部检查，返回告警 detail 列表（未断档的不含）。"""
    td = trade_date or dates.today_sh()
    return [detail for _, fn in CHECKS
            for detail in [fn(td)] if detail]


def _dedup_key(detail: str) -> str:
    """把 detail 里的日期抹成 <date>，得到跨日稳定的去重键。

    ★2026-09-29 新增。断档文案天然嵌日期：「从未产出报告（截至 2026-09-29 无
    review_report 落盘）」。而 `alerts.record_dedup` 按 (source, detail) 精确比对，
    于是**同一个未修复的问题每天都会长出一条新告警**（09-28 落 id=8、09-29 落
    id=9，文案只差日期）。实测 18:05 部署后首轮就复现：去重形同虚设，告警表
    每天 +1 条无人认领的同义告警。抹掉日期后，「同一类断档」才真正只占一行，
    告警表也不会被日期刷屏——这正是 lkl 2026-09-18 P2-7 引入 record_dedup 的原意。
    """
    return _DATE_IN_TEXT.sub("<date>", detail)


def record_once(details: list[str], trade_date: date | None = None) -> int:
    """断档入 alert 队列（去重：同 source 已有**同类**未确认告警则跳过）。

    返回新入队条数；webhook 推送由调用方（push_pending_webhook）统一做。

    ★2026-09-29 两处修：
    ① 原实现按 **source** 去重，而 `src` 恒为字面量 "health"——只要有**任意一条**
       未确认 health 告警，`pending` 就恒含 "health"，此后所有断档全被 `continue`
       吞掉。本仓恰好落进这个死锁：review_report 从未产出（0 行），2026-09-29
       00:34 那条「从未产出报告」永久未 ack → health 自此**整体失明**，data_gap /
       outcome_stale 再怎么坏都不会再入队。现按归一化 detail 去重：同一问题不
       重复轰炸，不同问题不再互相静默。
    ② 改用 `alerts.record_dedup` 做最后一道精确比对（防并发下重复写入），
       但**跨日**判重必须先在 Python 侧抹掉日期，见 `_dedup_key`。
    """
    try:
        seen = {_dedup_key(a["detail"]) for a in alerts.pending(200)
                if a["source"] == "health"}
    except Exception:                            # noqa: BLE001
        log.warning("health 未确认告警读取失败（退化为仅本轮内去重）")
        seen = set()
    n = 0
    for detail in details:
        key = _dedup_key(detail)
        if key in seen:
            continue
        seen.add(key)
        try:
            if alerts.record_dedup("WARN", "health", detail) is not None:
                n += 1
        except Exception:                        # noqa: BLE001
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
