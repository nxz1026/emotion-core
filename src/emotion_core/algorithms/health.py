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
★2026-10-15 本仓新增两条检查（`check_limit_state_audit` / `check_theme_coverage`），
SQL 与阈值为本仓自有——lkl 无 Wind 逐日对账、也没有新浪题材源，故不构成口径偏离。
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
REPORT_GAP_DAYS = 2  # 报告断档：>1 交易日前有报告即算断
DATA_GAP_DAYS = 2  # 数据链：derived_bar 缺最近交易日
OUTCOME_STALE_DAYS = 6  # 信号结果：>5 交易日前信号仍 incomplete
POOL_STALE_DAYS = 2  # 东财三池：落后衍生层 1 个交易日即算断
# ★2026-10-15 新增：对账与题材的消费端阈值（此前两者只落数据、无人消费）
LIMIT_AUDIT_DAYS = 10  # 对账台账窗口：最近 N 条 (日期,方向)
LIMIT_AUDIT_STALE_DAYS = 3  # 对账台账落后 derived_bar 的交易日数
LIMIT_AUDIT_ONLY_WIND_PCT = 5.0  # only_wind 占 Wind 名单比例上限（%，已知 ST 偏差约 2.3）
THEME_STALE_DAYS = 2  # 题材标签落后 derived_bar 的交易日数
THEME_COVERAGE_MIN_PCT = 40.0  # 题材标签覆盖率下限（标签票数 / 当日有行情票数）
THEME_PRIMARY_MIN_PCT = 60.0  # primary_theme 非空占标签行比例下限（实测 84%）


class _CalendarUnavailable(RuntimeError):
    r"""交易日历读不到 ⇒ 断档检查**给不出结论**。

    ⚠️ 2026-10-25 静态审计：原来读不到时**返回空列表** ⇒
    \`_trading_gap\` 对空列表求和 ⇒ **gap = 0** ⇒
    **6 条断档检查同时静默放行**（报告/数据/三池/题材/对账/结果）。

    而本函数的 docstring 明写「宁可让断档检查说『我算不出来』」⇒
    **实现没有兑现这个承诺**。⇒ 现在用异常把「算不出来」显式传上去。
    """


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
            " ORDER BY date DESC LIMIT %s",
            (d, n),
        )
    except Exception as exc:  # noqa: BLE001
        log.error("交易日历读取失败，断档检查给不出可信结论：%s: %s", type(exc).__name__, exc)
        # ⚠️ 原来这里 `return []` ⇒ 上层算出 gap=0 ⇒ **当成「没断档」** ⇒
        #    6 条断档检查一起静默放行。⇒ 改成**显式抛**，让上层报「无法判定」。
        raise _CalendarUnavailable(f"交易日历读取失败（{type(exc).__name__}: {exc}）") from exc
    return sorted(df["date"])


def _trading_gap(trade_date: date, since: date | None) -> int | None:
    """(since, trade_date) 开区间内的已入库交易日数。

    ⚠️ 返回 **None = 算不出来**（交易日历不可用）⇒ 调用方**必须**把它
    变成一条「无法判定」的告警，**不能**当成 0（= 没断档）。
    since=None 返回极大值（不适用）。
    """
    if since is None:
        return 10**9
    try:
        days = _recent_trading_days(trade_date, 60)
    except _CalendarUnavailable:
        return None
    return sum(1 for d in days if since < d < trade_date)


def check_report_gap(trade_date: date) -> str | None:
    """最近成功报告距今是否 ≥REPORT_GAP_DAYS 交易日；无报告返回告警文本。"""
    df = query_df("SELECT max(date) AS d FROM review_report WHERE date < %s", (trade_date,))
    last = df["d"].iloc[0] if not df.empty else None
    if last is None:
        return f"从未产出报告（截至 {trade_date} 无 review_report 落盘）"
    gap = _trading_gap(trade_date, last)
    if gap is None:
        return "报告断档：**无法判定**（交易日历不可用）—— 宁可说算不出来，也不报『没断档』"
    return (
        f"报告断档：最近成功报告 {last} 距今 {gap} 个交易日（阈值"
        f" {REPORT_GAP_DAYS}），疑似 daily.sh 未跑/中断"
        if gap >= REPORT_GAP_DAYS
        else None
    )


def check_data_gap(trade_date: date) -> str | None:
    """derived_bar 最近数据日距今是否 ≥DATA_GAP_DAYS 交易日（采集停摆）。"""
    df = query_df("SELECT max(date) AS d FROM derived_bar WHERE date < %s", (trade_date,))
    last = df["d"].iloc[0] if not df.empty else None
    if last is None:
        return f"derived_bar 无任何历史数据（截至 {trade_date}）"
    gap = _trading_gap(trade_date, last)
    if gap is None:
        return "derived_bar 断档：**无法判定**（交易日历不可用）"
    return (
        f"derived_bar 最近数据日 {last} 距今 {gap} 个交易日（阈值"
        f" {DATA_GAP_DAYS}），疑似 fetch/derive 采集停摆"
        if gap >= DATA_GAP_DAYS
        else None
    )


def check_outcome_stale(trade_date: date) -> str | None:
    """≥6 交易日前 BUY 信号仍无 complete outcome → L3 回填断档。

    complete=True 需前向 5 日数据齐（outcome.py 语义）；confirm_date 距今
    ≥6 交易日仍未 complete 即异常（daily.sh 每日末尾 outcome 幂等回填）。
    """
    try:
        cutoff = _recent_trading_days(trade_date, OUTCOME_STALE_DAYS + 1)
    except _CalendarUnavailable:
        # ⚠️ 原来日历读不到 ⇒ cutoff=[] ⇒ len(cutoff)=0 < 7 ⇒ **当没过期**
        return "BUY 结果回填：**无法判定**（交易日历不可用）"
    if len(cutoff) < OUTCOME_STALE_DAYS + 1:
        return None  # 历史不足，无从判定
    stale = cutoff[0]  # 第 6 个交易日前
    df = query_df(
        "SELECT s.confirm_date, s.code FROM signal s"
        " LEFT JOIN signal_outcome o USING (confirm_date, code, action)"
        " WHERE s.action = 'BUY' AND s.confirm_date <= %s"
        "   AND (o.confirm_date IS NULL OR NOT o.complete)"
        " ORDER BY s.confirm_date DESC LIMIT 5",
        (stale,),
    )
    if df.empty:
        return None
    names = "、".join(f"{r.confirm_date} {r.code}" for r in df.itertuples())
    return (
        f"{len(df)} 条 ≥{OUTCOME_STALE_DAYS} 交易日前 BUY 信号无 complete"
        f" outcome（L3 未回填）：{names}"
    )


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
    names = "、".join(
        f"{s['step']}({s['for_date'] or '无日期'}){'：' + s['detail'][:120] if s['detail'] else ''}"
        for s in bad[:5]
    )
    return f"日更链有 {len(bad)} 个步骤停在 FAILED（未恢复）：{names}"


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
        return (
            "limit_pool_em 无任何数据：东财三池从未同步——c5「炸板≥1次回封」"
            "补偿分支恒不成立，复盘双源对账不可信"
        )
    if last_data is None:
        return None  # 衍生层也无数据，归 data 断档管
    gap = _trading_gap(last_data, last_pool)
    if gap is None:
        return "东财三池断档：**无法判定**（交易日历不可用）"
    return (
        f"东财三池断档：limit_pool_em 最近 {last_pool}，落后 derived_bar"
        f" {last_data} 共 {gap} 个交易日（阈值 {POOL_STALE_DAYS}）——"
        f"c5 炸板回封补偿与双源对账均失效"
        if gap >= POOL_STALE_DAYS
        else None
    )


def check_limit_state_audit(trade_date: date) -> str | None:
    """Wind 逐日涨跌停对账的**消费端**告警（★2026-10-15 新增）。

    对账通道（`services/limit_state_wind.py` + `scripts/backfill_limit_state_wind.py`）
    只把名单与计数落进 `ref_limit_state` / `ref_limit_state_run`，**没有任何消费方**：
    2026-08-17 那次「台账记 ok、实际丢 7 只」就是靠人肉 diff 才发现的。本条把它接进
    每日健康检查，三种红灯（全部对照上游权威名单，不看行数）：

    - `n_only_self > 0`：本库判涨停而 Wind 名单没有。实测 42 天全 0，一旦非 0
      要么本库口径出了假阳性、要么 Wind 名单被截断 ⇒ 两种都必须看。
    - `only_wind / n_wind` 超阈值：自算系统性漏票（典型=ST ±5% 档缺失，实测约 2.3%；
      阈值 5% 能抓口径退化，又不会天天复报已知偏差）。
    - 台账断更：最近抓取日落后 `derived_bar` 最近日 ≥ 阈值 ⇒ 每日自动化停了。

    名单不全的日子（status `truncated`/`suspect`）不参与占比、单独点名：它们靠
    `retries` 上限自动重抓，若长期停在窗口里说明重抓也没救。
    """
    df = query_df(
        "SELECT date, direction, status, n_wind, n_only_wind, n_only_self"
        " FROM ref_limit_state_run WHERE date <= %s"
        " ORDER BY date DESC LIMIT %s",
        (trade_date, LIMIT_AUDIT_DAYS),
    )
    if df.empty:
        return (
            "Wind 逐日涨跌停对账台账为空（ref_limit_state_run 无记录）——"
            "逐日对账未启动或全部失败，derived_bar 涨停口径无权威校验"
        )
    rows = df.to_dict("records")
    bad = [r for r in rows if str(r.get("status")) in ("truncated", "suspect")]
    ok_rows = [r for r in rows if str(r.get("status")) == "ok"]
    problems: list[str] = []
    #: **已知数据源差异**（owner 裁决 Q2=A：只记录、不告警）
    known_deviations: list[str] = []
    wind = sum(int(r["n_wind"] or 0) for r in ok_rows)
    only_wind = sum(int(r["n_only_wind"] or 0) for r in ok_rows)
    only_self = sum(int(r["n_only_self"] or 0) for r in ok_rows)
    if only_self > 0:
        # ★ 2026-10-24 owner 裁决（Q2 = A）：**以本库为准**，
        #   把 Wind 的差异记为**已知数据源差异** —— 不再当红灯。
        #
        # 实测（近 10 个交易日）：only_self 有 7 天非零、合计 58 只，
        # 而 status 全是 ok ⇒ 真正让 watchdog 亮红的是这一条，
        # 不是「断档」。owner 定调：本库口径为准，Wind 名单侧视为**已知偏差**。
        #
        # ⚠️ 但**不静默**：记进日志（可在巡检日志里回溯），
        #    且**其余三类问题仍然是红灯**（only_wind 超阈值 / 名单不全 / 台账落后）
        #    —— 那些是本库自己的问题。
        known_deviations.append(
            f"only_self 合计 {only_self} 只（本库判涨停而 Wind 未列）—— 已知数据源差异"
        )
        log.info(
            "Wind 逐日涨跌停对账：已知数据源差异 only_self 合计 %d 只（窗口 %d 条台账）"
            " —— 按 owner 裁决（Q2=A）以本库为准，不计入告警",
            only_self,
            len(rows),
        )
    pct = only_wind / wind * 100 if wind else 0.0
    if wind and pct > LIMIT_AUDIT_ONLY_WIND_PCT:
        problems.append(
            f"only_wind 占比 {pct:.2f}%（阈值 {LIMIT_AUDIT_ONLY_WIND_PCT}%，"
            f"自算漏票 {only_wind}/{wind}）"
        )
    if bad:
        days = "、".join(f"{r['date']}:{r['direction']}({r['status']})" for r in bad[:5])
        problems.append(f"{len(bad)} 天名单不全待重抓：{days}")
    got = query_df("SELECT max(date) AS d FROM derived_bar")
    last_data = got["d"].iloc[0] if not got.empty else None
    last_ledger = max(r["date"] for r in rows)
    if last_data is not None:
        gap = _trading_gap(last_data, last_ledger)
        if gap is None:
            problems.append("台账落后度：**无法判定**（交易日历不可用）")
        elif gap >= LIMIT_AUDIT_STALE_DAYS:
            problems.append(
                f"台账最近抓取日 {last_ledger}，落后 derived_bar"
                f" {last_data} 共 {gap} 个交易日（阈值 {LIMIT_AUDIT_STALE_DAYS}）"
            )
    if known_deviations:
        # 有已知差异但没有真问题 ⇒ **不告警**（返回 None）
        # ⇒ 调用方（CHECKS 框架）不会记 alert ⇒ watchdog 不再为此亮红
        log.info("Wind 对账已知差异：%s", "；".join(known_deviations))
    if not problems:
        return None
    detail = (
        f"Wind 逐日涨跌停对账异常（窗口 {len(rows)} 条台账、"
        f"{len(ok_rows)} 条可算）：" + "；".join(problems)
    )
    if known_deviations:
        detail += "（另有已知数据源差异：" + "；".join(known_deviations) + "）"
    return detail


def limit_state_deviation(trade_date: date) -> dict:
    """Wind 逐日涨跌停对账的**已知差异**快照（★2026-10-25 新增，给日报用）。

    owner 裁决 Q2=A：only_self（本库判涨停而 Wind 名单没有）**以本库为准**，
    不当红灯（见 `check_limit_state_audit`）。但「不告警」不等于「不看见」——
    差异静默累积，没人知道本库的涨停口径哪天开始和权威名单分家。

    本函数把那串差异读出来，供日报趋势段展示：每天多少只、连续几天。
    与 `check_limit_state_audit` 共用同一张台账 `ref_limit_state_run`，
    但**口径不同**：这里只取 `status='ok'` 的行（名单不全的日子不参与统计），
    且不产生任何告警。
    """
    df = query_df(
        "SELECT date, direction, status, n_wind, n_only_wind, n_only_self"
        " FROM ref_limit_state_run WHERE date <= %s"
        " ORDER BY date DESC LIMIT %s",
        (trade_date, LIMIT_AUDIT_DAYS),
    )
    if df.empty:
        return {"available": False, "reason": "对账台账为空", "days": [], "total": 0}
    rows = df.to_dict("records")
    ok_rows = [r for r in rows if str(r.get("status")) == "ok"]
    per_day: list[dict] = []
    total = 0
    for r in ok_rows:
        n = int(r.get("n_only_self") or 0)
        if n <= 0:
            continue
        total += n
        per_day.append(
            {
                "date": r["date"],
                "direction": r.get("direction"),
                "n_only_self": n,
                "n_wind": int(r.get("n_wind") or 0),
            }
        )
    return {
        "available": True,
        "days": per_day,
        "total": total,
        "n_days": len(per_day),
        "window_rows": len(rows),
        "ok_rows": len(ok_rows),
    }


def check_theme_coverage(trade_date: date) -> str | None:
    """题材自产的空转/覆盖率告警（★2026-10-15 新增）。

    题材此前是**假 OK**：`theme_service.run` 走 NullProvider 直接 `return 0`，
    步骤照样记 OK，健康检查只看行数/状态抓不到。本条按三层判：

    - **当日空转**：`derived_bar` 已有 `trade_date` 的数据、而 `theme_tag` 没有
      当日行 ⇒ 题材步骤报了 OK 却没产出（provider 未激活/源失效）。以 derived_bar
      为参照系，独立 cron 在盘中跑（数据未落）时不会误报。
    - **断档**：`theme_tag` 最近日落后 `derived_bar` 最近日 ≥ 阈值。
    - **覆盖率**：最近题材日的「有标签票数 / 当日有行情票数」低于阈值，或
      `primary_theme` 非空占比过低（属性桶剔除后应稳定在 80% 以上）。
    """
    got = query_df("SELECT max(date) AS d FROM theme_tag")
    have = query_df("SELECT max(date) AS d FROM derived_bar")
    last_theme = got["d"].iloc[0] if not got.empty else None
    last_data = have["d"].iloc[0] if not have.empty else None
    if last_theme is None:
        return (
            "theme_tag 无任何数据：题材未自产（provider 未激活或走 NullProvider"
            "空转），题材组/题材标签全为空壳"
        )
    if last_data is not None and last_data == trade_date and last_theme < trade_date:
        return (
            f"题材当日空转（假 OK）：derived_bar 已有 {trade_date} 数据，"
            f"theme_tag 最新仍为 {last_theme}——题材步骤报 OK 但无产出"
        )
    if last_data is not None:
        gap = _trading_gap(last_data, last_theme)
        if gap is None:
            return "题材断档：**无法判定**（交易日历不可用）"
        if gap >= THEME_STALE_DAYS:
            return (
                f"题材断档：theme_tag 最近 {last_theme}，落后 derived_bar"
                f" {last_data} 共 {gap} 个交易日（阈值 {THEME_STALE_DAYS}）"
            )
    cov = query_df(
        "SELECT count(DISTINCT code) AS tagged, count(primary_theme) AS primary"
        " FROM theme_tag WHERE date = %s",
        (last_theme,),
    )
    tagged = int(cov["tagged"].iloc[0]) if not cov.empty else 0
    primary = int(cov["primary"].iloc[0]) if not cov.empty else 0
    uni = query_df("SELECT count(DISTINCT code) AS n FROM daily_bar WHERE date = %s", (last_theme,))
    total = int(uni["n"].iloc[0]) if not uni.empty else 0
    problems: list[str] = []
    if total and tagged / total * 100 < THEME_COVERAGE_MIN_PCT:
        problems.append(
            f"标签覆盖率 {tagged}/{total} = {tagged / total * 100:.1f}%"
            f"（阈值 {THEME_COVERAGE_MIN_PCT}%）"
        )
    if tagged and primary / tagged * 100 < THEME_PRIMARY_MIN_PCT:
        problems.append(
            f"primary_theme 非空占比 {primary}/{tagged} = "
            f"{primary / tagged * 100:.1f}%（阈值 {THEME_PRIMARY_MIN_PCT}%）"
        )
    if not problems:
        return None
    return f"题材覆盖率异常（{last_theme}）：" + "；".join(problems)


CHECKS = (
    ("report", check_report_gap),
    ("data", check_data_gap),
    ("outcome", check_outcome_stale),
    ("pipeline", check_pipeline_failed),
    ("pool", check_pool_stale),
    ("limit_audit", check_limit_state_audit),
    ("theme", check_theme_coverage),
)


def run(trade_date: date | None = None) -> list[str]:
    """全部检查，返回告警 detail 列表（未断档的不含）。"""
    td = trade_date or dates.today_sh()
    return [detail for _, fn in CHECKS for detail in [fn(td)] if detail]


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
        seen = {_dedup_key(a["detail"]) for a in alerts.pending(200) if a["source"] == "health"}
    except Exception:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
        log.warning("health webhook 推送失败（不影响告警入队）")
    return n
