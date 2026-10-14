"""编排层：每日全流程入口。

一个 Python 脚本，线性步骤 + 表打点 + 失败即停。
步骤顺序严格照 lkl daily.sh（策略语义）。

打点带**产出摘要**：`_run_step` 的返回值（默认 `""`）即 `pipeline_state.detail`——
`status=OK` 只证明「没抛异常」，摘要才让「写了 0 行」与「正常产出」可区分，
见 _run_step 与 _signal_detail。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from emotion_core.orchestration import pipeline
from emotion_core.services.coverage import EXIT_COVERAGE_BLOCKED, CoverageBlocked
from emotion_core.utils.dates import today_sh

log = logging.getLogger("emotion_core.daily")

# 步骤注册表（顺序即执行顺序）
STEPS = [
    ("sync", "数据同步"),
    ("coverage", "覆盖率门槛"),
    # R20：此前 fetch_hot_snapshot / backfill_hot_rank 定义了但**全仓零调用**，
    # 导致 hot_rank 长期 0 行（CPT /pool 的「热门 5 只」全靠 ladder_day 顶替）。
    # 位置在 coverage 之后而不是 sync 之后：test_coverage 守的契约是
    # 「coverage 紧跟 sync」（拉完立刻验、早失败），插中间会破坏它。hot 不参与
    # coverage 门槛，故排在门槛之后同样安全。
    ("hot", "人气榜"),
    # R21：`fill_stock_basic` 此前只有 CLI `main()`、生产零调用方 ⇒
    # stock_basic.industry / market_cap / float_shares 长期全表 NULL（面板「行业
    # 未接入」）。basic 步把它和另外两条「补缺列」源串起来：全市场快照 →
    # 交易所上市日期（仅缺失）→ 巨潮行业（仅缺失）。位置同 hot 一样在 coverage
    # 之后（coverage 与 sync 的相邻契约由 test_coverage 守，不能插中间），
    # 且在算法步骤之前，让当日新股的基础信息先落地。
    ("basic", "基础信息刷新"),
    ("derive", "判据+连板"),
    ("market", "状态机"),
    ("ladder", "梯队"),
    ("signal", "买入信号"),
    ("promotion", "晋级率"),
    ("theme", "题材"),
    ("ecosystem", "生态评级"),
    ("strategy", "策略观察"),
    ("outcome", "结果回填"),
    ("health", "健康推送"),
]


def _trading_day_guard(trade_date: date) -> bool:
    """交易日守卫：非交易日返回 False（判据来自**独立日历源**，不看库内数据）。

    判据 = `data/trade_calendar.is_trading_day`（akshare 新浪交易日历，落
    `trade_calendar` 表缓存）；未来日期另加一道「数据不可能存在」的拦截。

    为什么不用 `utils/dates.trading_days`：它从 `daily_bar` 的 distinct date 派生
    （「日历是 data 的事实」），**脏数据会把非交易日变成交易日**——实测事故：
    库内 2026-09-27（周日）有 5221 行、与 2026-09-24 逐行全等（旧版 snapshot 把
    实时快照盖上传入日期），日历因此认为周日开市，守卫被骗过 → 目标日=周日 →
    sync 报「EM 最新数据日期 2026-09-24 ≠ 传入 2026-09-27」→ 日更链卡死在 sync，
    新增的 coverage/derive/emotion 步骤永远跑不到。独立日历源与库内数据无关，
    从根上断掉这条自噬路径。

    语义边界（刻意选择）：**当日是交易日但行情还没发布 → 不跳过**，让它走到 sync
    由 EM 日期守卫响亮报 FAILED（真数据延迟必须可见，不能静默 0 退出）。
    历史上「非交易日无数据」与「交易日无数据」都被旧的日历判据静默跳过了。
    """
    from emotion_core.data import trade_calendar

    today = today_sh()
    if trade_date > today:
        log.warning("目标日 %s 在未来（今天 %s），跳过", trade_date, today)
        return False
    if not trade_calendar.is_trading_day(trade_date):
        log.info("非交易日 %s（独立日历源），跳过", trade_date)
        return False
    return True


def run_daily(trade_date: date | None = None, *, from_step: str | None = None, dry_run: bool = False) -> int:
    """执行每日全流程。

    Args:
        trade_date: 交易日，默认今天。
        from_step: 从指定步骤开始（断点续跑）。
        dry_run: 只打印步骤不执行。

    Returns:
        exit code（0=成功，非0=失败）。
    """
    if trade_date is None:
        trade_date = today_sh()

    if not _trading_day_guard(trade_date):
        return 0

    log.info("===== emotion-core daily %s =====", trade_date)

    started = from_step is None
    for step, desc in STEPS:
        if not started:
            if step == from_step:
                started = True
            else:
                continue

        if dry_run:
            log.info("[dry-run] %s: %s", step, desc)
            continue

        pipeline.mark_running(step, trade_date)
        try:
            # detail：本步产出摘要（此前恒不带 → pipeline_state.detail 恒空，
            # 「写入 0 行」与「正常产出」在库里无从区分）。`or ""`：桩函数/旧实现
            # 返回 None 时不得把 None 落库。语义见 _run_step。
            detail = _run_step(step, trade_date) or ""
            pipeline.mark_done(step, trade_date, detail)
        except CoverageBlocked as exc:
            # 半截数据比报错危险：拒绝装配，专用退出码 76（docs/01 A12）。
            pipeline.mark_failed(step, trade_date, str(exc))
            log.error("daily blocked at %s: %s", step, exc)
            return EXIT_COVERAGE_BLOCKED
        except Exception as exc:
            pipeline.mark_failed(step, trade_date, str(exc))
            log.error("daily failed at %s: %s", step, exc)
            return 1

    log.info("===== emotion-core daily done =====")
    return 0


def _signal_detail(trade_date: date, sig: object | None) -> str:
    """signal 步产出摘要（进 ``pipeline_state.detail``）。

    为什么需要它：``entry.check_signal`` 只在五条件全过时才 ``_persist``
    （algorithms/entry.py:430-431），其余情况只写日志 ⇒「当天没信号」与「步骤
    根本没跑」在库内无法区分——这正是「signal 表 source='live' 恒 0 行、而
    pipeline_state 每日 OK/detail=''」能长期潜伏的原因（docs/05 §P4 ①）。

    字段来源全部是真实属性，不编造：``Signal`` 只有 code / date / action /
    checklist / source / status（domain/signal.py:39-47，**没有** buy_window），
    故 ``buy_window`` 取 ``entry.current_window(trade_date)`` 的当日买入窗口
    （algorithms/entry.py:400），这是事实读取、不是判据复算。

    格式：``signals=1 code=600519 action=BUY buy_window=ENHANCED source=live``
    / ``signals=0 buy_window=NONE``。无信号时不塞清单（库里上限 500 字符，越短越好）；
    ``signals`` 只反映**主信号**（``check_signal`` 返回 None 时次级推荐仍可能落库，
    它吞掉了 ``check_secondary_signal`` 的返回值）。
    """
    try:
        from emotion_core.algorithms import entry
        window = entry.current_window(trade_date)
    except Exception as exc:                      # noqa: BLE001
        # 打点是旁路：读窗口失败不得把 signal 步变成 FAILED（信号本身可能已落库）
        log.warning("signal 明细降级：buy_window 读取失败 %s", exc)
        window = "unknown"
    if sig is None:
        return f"signals=0 buy_window={window}"
    return (f"signals=1 code={sig.code} action={sig.action.value}"
            f" buy_window={window} source={sig.source.value}")


#: basic 步里巨潮行业回填的单次上限。正常日 only_missing 只有当日新股（个位数），
#: 给上限是防「表被清空 / 源改口径」时一次拉几千只（逐股 ~1.2s）把 daily 拖住。
_INDUSTRY_LIMIT = 100


def _brief(exc: Exception, limit: int = 60) -> str:
    """异常摘要（进 detail；``pipeline_state.detail`` 上限 500 字符）。"""
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    return text[:limit]


def _basic_detail() -> str:
    """basic 步：刷新 stock_basic 的 name/industry/market_cap/float_shares/list_date。

    三条源串成「先补新代码、再补缺列」：

        全市场快照（新浪/东财）→ 交易所上市日期（仅缺失）→ 巨潮行业（仅缺失）

    为什么这三条失败只 warning + 落 detail、不抛（与 sync/hot 的硬失败刻意不同）：
    它们都是**元数据富化**，不是当日信号的输入。新浪/交易所/巨潮任何一家抖动就
    让 `run_daily` `return 1`，等于用「市值晚一天更新」换掉当天 derive/market/
    signal/theme 的全部产出，代价不对等。可见性交给 detail 与 warning 日志，而不是
    交给 status——`pipeline_state.detail` 正是为「status=OK 但没产出」这个盲区设的
    （见模块 docstring 与 _signal_detail）。
    """
    parts: list[str] = []

    try:
        from emotion_core.data import fill_stock_basic
        parts.append(f"spot={fill_stock_basic.backfill()}")
    except Exception as exc:                      # noqa: BLE001 —— 源异常类型不定
        log.warning("basic: 全市场快照刷新失败（保留旧值）：%s", exc)
        parts.append(f"spot=FAILED({_brief(exc)})")

    # 必须在快照之后：新股的 code 要先由快照写进 stock_basic，
    # 下面两步的 only_missing 才把它们算进目标集合。
    try:
        from emotion_core.data import fill_list_date
        parts.append(f"list_date={fill_list_date.backfill(only_missing=True)}")
    except Exception as exc:                      # noqa: BLE001
        log.warning("basic: 上市日期回填失败（保留旧值）：%s", exc)
        parts.append(f"list_date=FAILED({_brief(exc)})")

    try:
        from emotion_core.data import fill_industry
        parts.append("industry=" + str(fill_industry.backfill(
            "cninfo", only_missing=True, limit=_INDUSTRY_LIMIT)))
    except Exception as exc:                      # noqa: BLE001
        log.warning("basic: 行业回填失败（保留旧值）：%s", exc)
        parts.append(f"industry=FAILED({_brief(exc)})")

    return " ".join(parts)


def _run_step(step: str, trade_date: date) -> str:
    """执行单个步骤；返回写进 ``pipeline_state.detail`` 的短摘要。

    默认 ``""``（无产出摘要的步骤）。只有**算法/服务层已有现成返回值**、
    编排层不必新增查询的步骤才给摘要（``ladder`` / ``signal``）：口径只有业务层
    一处，编排层复刻判据就是第二份真相。

    ``run_daily`` 侧按 ``_run_step(...) or ""`` 取值——桩函数（测试）返回 None
    时不得把 None 塞进 ``mark_done``。
    """
    if step == "sync":
        from emotion_core.services.ingest import snapshot_daily
        snapshot_daily(trade_date)
    elif step == "hot":
        # 东财人气榜 top100。``fetch_hot_snapshot`` 自带守卫：当前榜只对应最新
        # 交易日，传入别的日期会 raise。
        #
        # 为什么不把 raise 直接放出去（那样更简单）：那会让**所有历史回补**全挂。
        # ``run_daily(from_step=..., 旧交易日)`` 跑到 hot 步必然守卫失败 →
        # 整条 daily 返回 1，「人气榜不能回补」是**不适用**，不是**失败**。
        # 口径与 ``_trading_day_guard`` 一致：非交易日直接返回 0，不算失败。
        from emotion_core.data.providers.eastmoney import em_data_date
        current = em_data_date()
        if current != trade_date:
            log.info("hot: 跳过 %s（人气榜只对应 %s，不回补历史）", trade_date, current)
            return ""
        from emotion_core.services.ingest import fetch_hot_snapshot
        fetch_hot_snapshot(trade_date)
    elif step == "basic":
        # 快照类刷新：stock_basic 的那几列没有日期维度（是「最新值」），
        # 历史补跑时刷新它等于拿今天的快照覆盖历史语境，语义上无意义且白打三次
        # 网络源 —— 与 hot 的「不回补历史」同口径。
        if trade_date != today_sh():
            log.info("basic: 跳过 %s（基础信息是快照列，不回补历史）", trade_date)
            return ""
        return _basic_detail()
    elif step == "coverage":
        from emotion_core.services.coverage import gate
        gate(trade_date)          # 不足 0.90 抛 CoverageBlocked → 退出码 76
    elif step == "derive":
        from emotion_core.services.derive_service import run as derive_run
        derive_run(trade_date)
    elif step == "market":
        from emotion_core.services.market_service import run as market_run
        market_run(trade_date)
    elif step == "ladder":
        from emotion_core.algorithms import ladder
        # 现成返回值即当日梯队行数（= 候选数），不改算法层、不新增查询
        return f"候选={ladder.persist(trade_date)}"
    elif step == "signal":
        from emotion_core.services.signal_service import run as signal_run
        return _signal_detail(trade_date, signal_run(trade_date))
    elif step == "promotion":
        from emotion_core.services.promotion_service import run as promotion_run
        promotion_run(trade_date)
    elif step == "theme":
        from emotion_core.services.theme_service import run as theme_run
        # 现成返回值即当日题材组数（同 ladder 步：口径只在业务层一处，编排层不新增查询）。
        # 2026-10-14 实测缺口：本步此前丢弃返回值 ⇒ detail 恒为 ''，而这一次题材源刚
        # 从 NullProvider 换成新浪概念，「题材源挂了(0 组)」与「正常产出(24 组)」在
        # pipeline_state 里长得一模一样——正是本文件开头说的那种不可区分。
        return f"题材组={theme_run(trade_date)}"
    elif step == "ecosystem":
        from emotion_core.services.ecosystem_service import run as ecosystem_run
        ecosystem_run(trade_date)
    elif step == "strategy":
        from emotion_core.services.strategy import run_for_date as strategy_run
        strategy_run(trade_date)
    elif step == "outcome":
        # 回填 signal_outcome（L3 结果），不是回填 signal——replay_service 是
        # 「历史 signal 回填」另一件事，此前接错（审核文档 §9 第 12 条）。
        from emotion_core.algorithms import outcome
        outcome.backfill()
    elif step == "health":
        # 三条断档检查（报告/数据链/信号回填）→ 去重入 alert 队列 → 推 webhook。
        # health.push 内部对 webhook 失败降级为 warning，不抛（不拦主链）。
        from emotion_core.algorithms import health
        health.push(trade_date)
    else:
        raise ValueError(f"未知步骤: {step}")
    return ""


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="emotion-core daily")
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="交易日 YYYY-MM-DD")
    parser.add_argument("--from-step", type=str, default=None, help="从指定步骤开始")
    parser.add_argument("--dry-run", action="store_true", help="只打印步骤不执行")
    args = parser.parse_args()
    # 必须显式配置：没有 handler 时只有 WARNING+ 会被 logging.lastResort 打到 stderr，
    # 于是 systemd journal 里看不到步骤进度与「非交易日跳过」，只有告警——
    # 运维无法判断跑过哪些步骤（实测 09-27 那次补跑日志只剩一行 sync 失败）。
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(run_daily(args.date, from_step=args.from_step, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
