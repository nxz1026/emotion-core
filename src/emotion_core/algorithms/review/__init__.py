"""T8 盘后复盘报告（PLAN §4，B 核心交付）：十段渲染，终端+MD+JSON+DB 四出口。

Part 1+2（数据收集 + 段渲染）：市场统计、信号、淘汰赛、梯队、生态、题材、
晋级、质检、对账、热度、口径、可用性、建议、反证、验证点。
Part 3（渲染与发布）：JSON/Markdown 渲染与四出口发布。
格式化工具由 review.utils 提供。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from emotion_core.algorithms import entry, exit as exit_svc, ladder, reconcile
from emotion_core.algorithms import theme as theme_svc
from emotion_core.algorithms.review.utils import (
    _CHECKS, _MARK, _atomic_write, _counter_items, _delta_of,
    _env_caveat, _env_conflict, _fmt_caliber, _fmt_catalyst,
    _fmt_stat, _jsonable, _ladder_tag, _pct, _pctv,
    _ENV_MEANING, _seal_notes, _usability, _strip_position_block,
)
from emotion_core.services import notify, position
from emotion_core.utils.config import CONFIG
from emotion_core.utils.dates import prev_trading_day
from emotion_core.utils.db import connect_ro, execute, query_df

log = logging.getLogger("emotion_core.review")

# CONFIG 未收录（emotion.py §5 / review/utils.py 同先例）：取 lkl config.py 原值
_ACCEL_ENFORCE: bool = getattr(CONFIG, "ACCEL_ENFORCE", False)
_OVERVIEW_LOOKBACK: int = getattr(CONFIG, "OVERVIEW_LOOKBACK", 20)
_MIN_UNIVERSE_WARN: int = getattr(CONFIG, "MIN_UNIVERSE_WARN", 3000)


def _recent_days_upto(d: date, n: int) -> list[date]:
    """d 及之前最近 n 个已入库交易日（升序）；不足 n 返回全部。

    契约适配：lkl `utils.dates.recent_trading_days(d, n)` 带参照日；emotion_core
    同名函数无参照日（只取全库最近 n 日，回填历史日会取错区间），故内联其 SQL。
    """
    df = query_df("SELECT DISTINCT date FROM daily_bar WHERE date <= %s"
                  " ORDER BY date DESC LIMIT %s", (d, n))
    return sorted(df["date"])


def _market_stat_row(trade_date: date | None) -> dict | None:
    if trade_date is None:
        return None
    df = query_df("SELECT * FROM market_stat WHERE date = %s", (trade_date,))
    return None if df.empty else df.iloc[0].to_dict()


def _window_of_day(trade_date: date) -> str:
    row = _market_stat_row(trade_date)
    return row["buy_window"] if row else "NONE"


def _signal_of_day(trade_date: date) -> dict | None:
    df = query_df("SELECT code, reason, buy_window, checklist FROM signal"
                   " WHERE confirm_date = %s AND action = 'BUY'", (trade_date,))
    if df.empty:
        return None
    row = df.iloc[0].to_dict()
    row["checklist"] = [(n, ok, note) for n, ok, note in row["checklist"]]
    return row


def _signal_of_day_secondary(trade_date: date) -> dict | None:
    """次级推荐信号（action='SECONDARY'）。"""
    df = query_df("SELECT code, reason, buy_window, checklist FROM signal"
                   " WHERE confirm_date = %s AND action = 'SECONDARY'", (trade_date,))
    if df.empty:
        return None
    row = df.iloc[0].to_dict()
    row["checklist"] = [(n, ok, note) for n, ok, note in row["checklist"]]
    return row


# W- 警告行在 entry._WARNINGS 里的标签前缀（"W1 同身位扎堆"）；条件行是 c1..c5。
_WARN_PREFIX = "W"


def _split_rows(rows: list) -> tuple[list, list]:
    """(标签, ok, 说明) 行 → (条件行, W- 警告行)。按标签前缀拆，不看位置。

    不复用 `entry.split_checklist`：那是对 `Checklist` **布尔投影**做的结构化
    分离（返回 Checklist + WarningOnly），而报告拿到的已经是落库的说明行三元组
    ——`entry._persist` 存的就是它，JSON 读回来是 list 而非 dataclass。
    """
    return ([r for r in rows if not r[0].startswith(_WARN_PREFIX)],
            [r for r in rows if r[0].startswith(_WARN_PREFIX)])


def _passed_of_rows(rows: list) -> bool:
    """行版的 `Checklist.passed`：只看条件行，filter UNKNOWN 后全 True 才算过。

    与 `domain.signal.Checklist.passed` 逐条等价（`entry.passed_of` 转发同一
    实现）——报告不另立通过规则，避免与实盘口径漂移。
    """
    conds, _ = _split_rows(rows)
    return all(ok is True for _, ok, _ in conds if ok is not None)


@dataclass(frozen=True)
class LadderRow:
    """梯队行 + 质量列（报告展示用）。

    `ladder_day` 表只存梯队判定，故 emotion-core 的 `LadderDay` 不带 lkl
    `LadderRow` 自带的 name/turnover_rate/bomb_times——而 `_ladder_tag`、
    `_seal_notes`、§①唯一高标、§⑦无信号理由都要读这三列。口径与
    `entry._VIEW_SQL` 一致（换手率 daily_bar 兜底、炸板次数取东财 ZT 池）。

    刻意做成**扁平** dataclass 而非「包装 + __getattr__ 透传」：JSON 出口走
    `utils._jsonable` → `dataclasses.asdict`，包装对象只会吐出包装层字段、
    把 code/cont_days 整个丢掉。
    """

    date: date
    code: str
    cont_days: int
    is_exchange: bool
    is_top: bool
    is_sole_top: bool
    y_top_group_count: int
    y_top_survivor_count: int
    name: str
    turnover_rate: float | None
    bomb_times: int | None


_VIEW_SQL = """
SELECT s.code, s.name,
       COALESCE(p.turnover_rate, b.turnover_rate) AS turnover_rate,
       p.bomb_times
FROM stock_basic s
LEFT JOIN daily_bar b ON b.code = s.code AND b.date = %(d)s
LEFT JOIN limit_pool_em p ON p.code = s.code AND p.date = %(d)s
     AND p.pool_type = 'ZT'
WHERE s.code = ANY(%(codes)s)
"""


def ladder_view(trade_date: date, rows: list) -> list[LadderRow]:
    """给梯队行补齐质量列。缺失即 None，不补零（与 `entry._candidate_view` 同口径）。

    一次 SQL 取齐当日全部候选：`_ladder_tag` 是逐行调用的，按行查库会退化成
    N+1（09-28 有 7 行梯队，回放 20 个交易日就是上百次往返）。
    """
    if not rows:
        return []
    with connect_ro() as conn:
        got = conn.execute(_VIEW_SQL, {"d": trade_date,
                                      "codes": [r.code for r in rows]}).fetchall()
    q = {r[0]: (r[1] or "", r[2], r[3]) for r in got}
    out = []
    for r in rows:
        nm, turn, bomb = q.get(r.code, ("", None, None))
        out.append(LadderRow(
            date=r.date, code=r.code, cont_days=r.cont_days,
            is_exchange=r.is_exchange, is_top=r.is_top,
            is_sole_top=r.is_sole_top,
            y_top_group_count=r.y_top_group_count,
            y_top_survivor_count=r.y_top_survivor_count,
            name=nm,
            turnover_rate=None if turn is None else float(turn),
            bomb_times=None if bomb is None else int(bomb)))
    return out


def elimination_rows(trade_date: date) -> list[dict]:
    """昨日最高板组 → 今日各自结果（晋级/炸板未封/断板/停牌）。"""
    prev = prev_trading_day(trade_date)
    if prev is None:
        return []
    y_tg = ladder_view(prev, ladder.top_group(ladder.build(prev)))
    if not y_tg:
        return []
    df = query_df(
        "SELECT code, is_limit_up, touched_limit, cont_days FROM derived_bar"
        " WHERE date = %s", (trade_date,))
    state = {r.code: (bool(r.is_limit_up), bool(r.touched_limit), int(r.cont_days))
             for r in df.itertuples()}
    out = []
    for r in y_tg:
        if r.code not in state:
            result = "停牌/无数据"
        elif state[r.code][0]:
            result = f"晋级 {state[r.code][2]}板"
        elif state[r.code][1]:
            result = "触板未封(炸板)"
        else:
            result = "断板"
        out.append({"code": r.code, "name": r.name, "y_cont": r.cont_days,
                    "today": result})
    return out


def _pool_missing(trade_date: date) -> list[str]:
    """A7：真缺失判定 = 池 0 行 **且** 自算口径有货。"""
    pool = query_df(
        "SELECT count(*) FILTER (WHERE pool_type='ZT') AS zt,"
        " count(*) FILTER (WHERE pool_type='ZB') AS zb,"
        " count(*) FILTER (WHERE pool_type='DT') AS dt"
        " FROM limit_pool_em WHERE date = %s", (trade_date,)).iloc[0]
    mine = query_df(
        "SELECT count(*) FILTER (WHERE is_limit_up) AS zt,"
        " count(*) FILTER (WHERE is_limit_down) AS dt,"
        " count(*) FILTER (WHERE is_bomb) AS zb"
        " FROM derived_bar WHERE date = %s", (trade_date,)).iloc[0]
    out = []
    if int(pool["zt"] or 0) == 0 and int(mine["zt"] or 0) > 0:
        out.append("ZT")
    if int(pool["zb"] or 0) == 0 and int(mine["zb"] or 0) > 0:
        out.append("ZB")
    if int(pool["dt"] or 0) == 0 and int(mine["dt"] or 0) > 0:
        out.append("DT")
    return out


def _quality(trade_date: date) -> dict:
    """数据质检：对账差异 + 缺数警示。A7：空池≠无差异，真缺失须显式报。"""
    warns = []
    n = query_df("SELECT count(*) n FROM daily_bar WHERE date = %s",
                 (trade_date,))["n"].iloc[0]
    if n < _MIN_UNIVERSE_WARN:
        warns.append(f"当日 daily_bar 仅 {n} 行，疑似缺数")
    missing = _pool_missing(trade_date)
    if missing:
        warns.append("东财" + "、".join(f"{k}池" for k in missing)
                     + " 0 行但自算口径有货——对账口径缺失，"
                     "下方「无差异」不可信（A7）")
    diff = reconcile(trade_date) if not missing else None
    return {"diff": diff, "y_comp": ladder.y_competition(trade_date),
            "warns": warns}


def _seal_map(trade_date: date) -> dict[str, tuple[str, str]]:
    """R1：炸板票首/末回封时间（limit_pool_em，东财口径），梯队附注用。"""
    df = query_df(
        "SELECT code, first_seal, last_seal FROM limit_pool_em"
        " WHERE date = %s AND pool_type = 'ZT'", (trade_date,))
    return ({r["code"]: (r["first_seal"], r["last_seal"])
             for _, r in df.iterrows()} if not df.empty else {})


def collect(trade_date: date) -> dict:
    """聚合报告所需全部数据（一次取齐，渲染函数纯格式化）。"""
    # 补质量列：ladder.build 给的是 LadderDay（无 name/bomb_times），
    # 下面 sole / ladder_rows 直接进渲染段，缺列会 AttributeError。
    rows = ladder_view(trade_date, ladder.build(trade_date))
    d = {
        "date": trade_date,
        "stat": _market_stat_row(trade_date),
        "prev_stat": _market_stat_row(prev_trading_day(trade_date)),
        "ladder_rows": rows,
        "seal_map": _seal_map(trade_date),
        "sole": ladder.sole_top(rows),
        "elimination": elimination_rows(trade_date),
        "window": _window_of_day(trade_date),
        "signal": _signal_of_day(trade_date),
        "secondary_signal": _signal_of_day_secondary(trade_date),
        "sells": exit_svc.suggestions(trade_date),
        "positions": position.current(),
        "hot_rank": _hot_rank_of(trade_date),
        "quality": _quality(trade_date),
        "caliber": _caliber(trade_date),
        "themes": _theme_rows(trade_date),
        "theme_tags": _theme_tag_rows(trade_date),
        "promotion": _promotion_rows(trade_date),
        "dragon_env": _dragon_env_of(trade_date),
        "orphan_adoptions": _orphan_adoptions(),
    }
    d["no_signal_reason"] = _no_signal_reason(d)
    d["usability"] = _usability(d)
    return d


def _promotion_rows(trade_date: date) -> pd.DataFrame:
    """当日晋级矩阵（§1.10）；未跑过 promotion 则空表。"""
    return query_df(
        "SELECT layer, promote_from, promote_nominal, promote_exchange,"
        " rate_nominal, rate_exchange, divergence, fail_perf"
        " FROM promotion_day WHERE date = %s"
        " ORDER BY promote_from DESC, layer", (trade_date,))


def _dragon_env_of(trade_date: date) -> dict | None:
    """生态评级（§1.12），从 market_stat 读回（由 dragon_env 写入）。"""
    row = _market_stat_row(trade_date)
    if not row or not row.get("dragon_env"):
        return None
    return {"rating": row["dragon_env"],
            "goods": row.get("dragon_env_reasons") or [],
            "bads": row.get("dragon_env_risks") or []}


def _health_of(trade_date: date) -> str:
    """V9：链路健康度——最近一次 review_report 成功落盘距今几个交易日。

    daily.sh 非交易日 SKIP 是正常行为（不计数）；跨过非交易日的缺口
    以已入库交易日数衡量，>1 个交易日即显式报「链路疑似断档」。
    """
    # P1-11：报告生成中查 max(date) 必然不含「正在生成的今天」——INSERT 在
    # 报告末尾。把生成日本身视为「即将成功」，gap 只数 (last, trade_date)
    # 开区间内的交易日，昨日有报告 → gap=0 不误报断档。
    df = query_df(
        "SELECT max(date) AS d FROM review_report WHERE date < %s", (trade_date,))
    last = df["d"].iloc[0]
    if last is None:
        return "链路健康：本库从未产出报告"
    days = _recent_days_upto(trade_date, 60)
    gap = sum(1 for d_ in days if last < d_ < trade_date)
    return (f"链路健康：最近成功报告 {last}"
            + ("" if gap == 0 else f"（距今 {gap} 个交易日，⚠ 疑似断档）"))


def _sec_overview(d: dict) -> str:
    """⓪ 速览（V9）：链路健康 + 较昨日变化。放最前，30 秒读完当日结论。

    产品 A1-1a：首行即「本报告可用于决策」总判（三态：OK/PARTIAL/
    UNKNOWN），缺数绝不显示绿色——用户不用翻到⑩质检才知道可信度。
    P1（产品审计）：+ 数据健康度徽章 + 口径覆盖声明 + 空仓原因可见化。
    """
    u = d.get("usability") or _usability(d)
    mark = {"OK": "✅", "PARTIAL": "⚠", "UNKNOWN": "⚠"}[u["state"]]
    lines = ["## ⓪ 速览", "",
             f"**数据可信度：{mark} {u['state']}** —— {u['note']}",
             "", _health_of(d["date"]), ""]
    # #7 口径覆盖声明（精简版，完整口径见⑩质检段）
    c = d.get("caliber")
    if c:
        bj = "含" if c.get("include_bj") else "不含"
        lines.append(f"**口径覆盖**：{c['universe']} ｜ 剔ST ｜ 剔次新 ｜ {bj}北交所"
                     f" ｜ 含20cm ｜ 自算涨停 {c['counts_self']['zt']} / "
                     f"东财池 {c['counts_em_pool']['zt']}")
    lines.append("")
    # #2 空仓/不动理由可见化（仅当 dict 含所需字段，否则跳过）
    if not d.get("signal") and "window" in d:
        reason = _no_signal_reason(d)
        if reason:
            lines.append(f"**今日建议**：{reason}")
    lines.append("**较昨日变化**")
    s, p = d["stat"], d["prev_stat"]
    if not s:
        lines.append("- 当日 market_stat 无数据（先跑 `lkl emotion`）")
    else:
        p = p or {}
        for label, key in (("涨停家数", "limit_up_count"),
                           ("名义最高板", "max_limit_days"),
                           ("可交易最高板", "tradable_max_days")):
            lines.append(_delta_of(s.get(key), p.get(key), label))
        if s.get("phase") != (p or {}).get("phase"):
            lines.append(f"- 阶段：{(p or {}).get('phase', '—')} → "
                         f"{s.get('phase')}")
        else:
            lines.append(f"- 阶段：{s.get('phase')}（持平）")
        lines.append(_delta_of(s.get("buy_window"), p.get("buy_window"),
                               "buy_window"))
        lines.append(_delta_of(s.get("dragon_env"), p.get("dragon_env"),
                               "生态评级"))
    return "\n".join(lines) + "\n"


def _sec_trend(d: dict) -> str:
    """⑫ 近 N 日走势（V9）：phase/buy_window/生态评级时间线，查异常翻转。"""
    days = _recent_days_upto(d["date"], _OVERVIEW_LOOKBACK)
    if len(days) < 2:
        return "## ⑫ 近 20 日走势\n\n历史不足 2 个交易日，无时间线\n"
    df = query_df(
        "SELECT date, phase, buy_window, dragon_env FROM market_stat"
        " WHERE date = ANY(%s) ORDER BY date", (days,))
    lines = ["## ⑫ 近 20 日走势（phase / 窗口 / 生态）", "",
             "| 日期 | 阶段 | 窗口 | 生态 |", "|---|---|---|---|"]
    for r in df.itertuples():
        lines.append(f"| {r.date} | {r.phase or '—'} | {r.buy_window or '—'}"
                     f" | {r.dragon_env or '—'} |")
    return "\n".join(lines) + "\n"


def _sec_reconcile_pos(d: dict) -> str:
    """⓪附：持仓对账（V9）——ADOPTED 信号无对应 OPEN 持仓 → 漏录警告。

    产品审计 5.1.3：人工回执 adopted 后忘录持仓是纯静默的，退潮清仓等
    卖出建议不会为该仓位生成。此段堵住盲区：只对账不代录。
    """
    lines = ["", "**持仓对账**："]
    if not d["orphan_adoptions"]:
        lines.append("已采纳信号与持仓记录一致（或当日无已采纳信号）✓")
    else:
        lines.append("⚠ 以下信号已标记采纳但无对应 OPEN 持仓，"
                     "请确认是否漏录（漏录则卖出建议不覆盖该仓位）：")
        lines += [f"- {r['date']} {r['code']}（信号#{r['id']}）"
                  for r in d["orphan_adoptions"]]
    return "\n".join(lines) + "\n"


def _orphan_adoptions() -> list[dict]:
    """ADOPTED 买入信号中，code 无 OPEN 持仓的清单（近 10 个交易日）。"""
    df = query_df(
        "SELECT s.id, s.confirm_date AS date, s.code FROM signal s"
        " WHERE s.action='BUY' AND s.status='ADOPTED'"
        " AND NOT EXISTS (SELECT 1 FROM position p WHERE p.code = s.code"
        " AND p.status='OPEN')"
        " AND s.confirm_date >= CURRENT_DATE - INTERVAL '14 days'"
        " ORDER BY s.confirm_date DESC")
    return df.to_dict("records") if not df.empty else []


def _sec_emotion(d: dict) -> str:
    """① 情绪面板：指标 + 阶段（R2：区分当日触发/历史延续）+ 加速横幅。"""
    if not d["stat"]:
        return "## ① 情绪面板\n\nmarket_stat 缺当日数据：先跑 `lkl emotion`\n"
    s = d["stat"]
    banner = ""
    if s.get("accelerate"):
        mode = "已降级" if _ACCEL_ENFORCE else "影子模式，未干预窗口"
        banner = (f"\n\n⚡ **加速事件命中**（{mode}）\n\n{s.get('accel_reason', '')}\n")
    inherited = bool(s.get("phase_inherited"))
    phase_label = f"{s['phase']}（延续）" if inherited else f"{s['phase']}（当日触发）"
    return ("## ① 情绪面板\n\n" + _fmt_stat(s, d["prev_stat"])
            + f"\n\n**阶段：{phase_label}** buy_window={s['buy_window']}"
            + ("\n\n⚠ **退潮清仓信号生效**" if s["force_liquidate"] else "")
            + banner
            + f"\n\n依据：{s.get('reason', '')}\n")


def _sec_ladder(d: dict) -> str:
    rows = d["ladder_rows"]
    if not rows:
        return "## ② 梯队全景\n\n无连板梯队（当日无 ≥2 板）\n"
    by_h: dict[int, list] = {}
    for r in rows:
        by_h.setdefault(r.cont_days, []).append(r)
    lines = ["## ② 梯队全景", ""]
    for h in sorted(by_h, reverse=True):
        tags = [_ladder_tag(r) for r in by_h[h]]
        lines.append(f"- **{h}板** [{len(tags)}只] " + "、".join(tags))
    lines.append("")
    lines.append("> 开板计数为东财 zbc 字段（逐笔微观口径，一分钟分时看到的"
                 "明显开板阶段通常少于该值）。判据 c5「炸板回封」用"
                 " zbc>=1，语义为「开过板且封住」，不受影响。")
    for note in _seal_notes(rows, d.get("seal_map", {})):
        lines.append(f"> {note}")
    return "\n".join(lines) + "\n"


def _theme_rows(trade_date: date) -> pd.DataFrame:
    df = query_df(
        "SELECT g.theme, g.highest_board, COALESCE(s.name, g.top_code) top_name,"
        " g.mid_count, g.low_count, g.first_board_count, g.completeness,"
        " g.status, g.member_count FROM theme_group g"
        " LEFT JOIN stock_basic s ON s.code = g.top_code"
        " WHERE g.date = %s ORDER BY g.member_count DESC, g.highest_board DESC,"
        " g.completeness DESC LIMIT 12", (trade_date,))
    return df


def _theme_tag_rows(trade_date: date) -> pd.DataFrame:
    return query_df(
        "SELECT t.code, s.name, l.cont_days, t.primary_theme, t.catalyst,"
        " t.catalyst_source, t.evidence_date, t.confidence, t.role,"
        " COALESCE(t.secondary_themes, '{}'::text[]) AS secondary_themes"
        " FROM theme_tag t JOIN stock_basic s ON s.code = t.code"
        " JOIN ladder_day l ON l.date = t.date AND l.code = t.code"
        " WHERE t.date = %s ORDER BY l.cont_days DESC", (trade_date,))


def _fmt_false_relation(tags: pd.DataFrame) -> str:
    """名称相似但题材无交集 → 提示防归同组（theme_svc 封装）。"""
    pairs = theme_svc.false_relation_pairs(
        [(r.code, r.name, list(r.secondary_themes)) for r in tags.itertuples()])
    if not pairs:
        return ""
    return ("\n> ⚠ FALSE_RELATION 提示：" + "；".join(
        f"{a} vs {b} 名称相似但题材标签无交集，勿归同组" for a, b in pairs))


def _sec_theme(d: dict) -> str:
    """③ 题材结构（T13：Wind 概念标签+公告催化聚合，仅梯队口径）。"""
    t = d["themes"]
    if t.empty:
        return "## ③ 题材结构\n\n_未生成（先跑 lkl theme <date>）_\n"
    lines = ["## ③ 题材结构", "",
             "| 题材 | 最高板 | 中位 | 低位 | 首板* | 完整度 | 状态 | 成员 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in t.itertuples():
        lines.append(f"| {r.theme} | {r.highest_board}（{r.top_name}） "
                     f"| {r.mid_count} | {r.low_count} | {r.first_board_count} "
                     f"| {r.completeness} | {r.status} | {r.member_count} |")
    lines.append(_fmt_catalyst(d["theme_tags"]))
    lines.append(_fmt_false_relation(d["theme_tags"]))
    lines += ["", "*P1 仅梯队（cont_days≥2）口径，首板盲区待 P1.5 扩展；"
              "标签为 Wind 概念（风格噪声已滤），催化剂为公告关键词规则。"]
    return "\n".join(lines) + "\n"


def _sec_elimination(d: dict) -> str:
    """③ 淘汰赛况：昨日最高板组今日各自结果。"""
    rows = d["elimination"]
    if not rows:
        return "## ④ 淘汰赛况\n\n昨日无 ≥2 板最高板组（无淘汰赛）\n"
    lines = ["## ④ 淘汰赛况", "",
             "| 股票 | 昨日板数 | 今日结果 |", "|---|---|---|"]
    lines += [f"| {r['name']}({r['code']}) | {r['y_cont']} | {r['today']} |"
              for r in rows]
    g, s = d["quality"].get("y_comp", (0, 0))
    lines += ["", f"昨日最高板组 {g} 只 → 今日幸存 {s} 只"]
    if d["sole"]:
        c = d["sole"]
        abs_max = max((r.cont_days for r in d["ladder_rows"]), default=0)
        note = (f"（绝对最高 {abs_max}板为一字垄断，D7 不计有效投票）"
                if abs_max > c.cont_days else "")
        lines.append(f"**唯一换手高标确认：{c.name}({c.code}) {c.cont_days}板**"
                     + note)
    return "\n".join(lines) + "\n"


def _sec_promotion(d: dict) -> str:
    """⑤ 晋级矩阵：各层名义/换手双口径与背离度（§1.10）。"""
    p = d["promotion"]
    if p is None or p.empty:
        return "## ⑤ 晋级矩阵\n\n_未生成（先跑 `lkl promotion <date>`）_\n"
    lines = ["## ⑤ 晋级矩阵", "",
             "| 层 | 分母 | 名义晋级 | 换手晋级 | 名义率 | 换手率 | 背离 | 失败股今日 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in p.itertuples():
        lines.append(f"| {r.layer} | {r.promote_from} | {r.promote_nominal} "
                     f"| {r.promote_exchange} | {_pct(r.rate_nominal)} "
                     f"| {_pct(r.rate_exchange)} | {_pct(r.divergence)} "
                     f"| {_pctv(r.fail_perf)} |")
    lines += ["", "> 换手率＝可成交口径（真机会）；背离＝名义−换手，高＝高度靠一字推"
              "、机会在消失。分母 < 3 不出率（样本不足，非 0%）。"]
    return "\n".join(lines) + "\n"


def _sec_watchlist(d: dict) -> str:
    """⑥附·自选股状态：当日涨停/炸板/涨跌幅快照（无自选返回空串）。

    P3-3：watchlist 表 + status_rows() 同源渲染（CLI `lkl watch status`
    与报告段共用），空清单不占报告篇幅。
    """
    from emotion_core.services import watchlist
    rows = watchlist.status_rows(d["date"])
    if not rows:
        return ""
    lines = ["", "**自选股当日状态**："]
    lines += [f"- {watchlist.fmt_status(r)}" for r in rows]
    return "\n".join(lines) + "\n"


def _sec_dragon_env(d: dict) -> str:
    """⑦ 生态评级：三档 + 条件逐项；与 buy_window 语义分开（风险登记13）。"""
    de = d["dragon_env"]
    if not de:
        return "## ⑦ 生态评级\n\n_未生成（先跑 `lkl dragon-env <date>`）_\n"
    lines = ["## ⑦ 生态评级（龙空龙可用性，**非买入信号**）", "",
             f"**评级：{de['rating']}** —— {_ENV_MEANING.get(de['rating'], '')}"
             f"{_env_caveat(de)}", "",
             *_env_conflict(d),
             f"> 语义区分：本段评「生态可不可干」；今日 `buy_window={d['window']}`"
             f" 是「今天给不给买」。两者独立，出现矛盾时以各自语义分别理解，不合并。", ""]
    for title, key in (("有利条件", "goods"), ("不利条件", "bads")):
        lines.append(f"**{title}**")
        lines += [f"- [{_MARK(i.get('ok'))}] {i.get('cond')}：{i.get('note')}"
                  for i in de[key]]
        lines.append("")
    return "\n".join(lines) + "\n"


def _sec_counter(d: dict) -> str:
    """⑧ 反证与不确定性：无内容也显式写「无反证」，不省略本段。"""
    items = _counter_items(d)
    lines = ["## ⑧ 反证与不确定性", ""]
    lines.append("本报告结论暂无已知反证；所有判据方向一致。" if not items
                 else "\n".join(f"- {t}" for t in items))
    return "\n".join(lines) + "\n"


def _sec_next_check(d: dict) -> str:
    """⑨ 次日验证点：只写观察条件，禁止写买入指令（引擎 v1 §8 措辞纪律）。"""
    phase = (d["stat"] or {}).get("phase", "")
    lines = ["## ⑨ 次日验证点", ""]
    if not phase:
        return "\n".join(lines + ["_缺情绪阶段数据，无法给出验证点。_"]) + "\n"
    lines.append(f"当前阶段 **{phase}**，出现以下现象才确认状态延续或切换：")
    lines += [f"- {t}" for t in _CHECKS.get(phase, ["（该阶段验证点模板待补）"])]
    if (d["stat"] or {}).get("accelerate"):
        lines.append("- ⚡ 加速事件已命中：重点观察一字板高标是否开板放量"
                     "（开板即分歧日，属验证窗口而非买点）")
    return "\n".join(lines) + "\n"


def _data_facts_of(d: dict) -> list[str]:
    """P2-1：信号分层·数据事实层——只放原始可观测数据（不做判断）。

    与「关键判据」区分：本层给出候选/梯队/淘汰赛的裸数值与状态，
    判断（通过与否）由下一层 checklist 给出。数据缺时显式写缺，
    不编造。
    """
    facts: list[str] = []
    rows = d["ladder_rows"]
    abs_max = max((r.cont_days for r in rows), default=0)
    facts.append(f"梯队最高 {abs_max}板"
                 + (f"（{len([r for r in rows if r.cont_days == abs_max])} 只同身位）"
                    if abs_max else ""))
    g, s = d["quality"].get("y_comp", (0, 0))
    facts.append(f"昨日最高板组 {g} 只 → 今日幸存 {s} 只")
    sole = d["sole"]
    if sole is not None:
        turn = f"{sole.turnover_rate:.1f}%" if sole.turnover_rate else "缺数据"
        facts.append(f"唯一换手高标 {sole.name}({sole.code}) {sole.cont_days}板"
                     f"（换手率 {turn}）")
    else:
        facts.append("无唯一换手高标候选")
    return facts


def _sec_advice(d: dict) -> str:
    """⑥ 明日参考：信号 checklist / 空仓理由 / 卖出建议 / 持仓表头。

    P2（产品审计 #1）：信号分层展示——结论 → 关键判据 → 数据事实 → 风险提示。
    """
    lines = ["## ⑥ 明日参考（只给信息不给指令）", ""]
    if d["positions"]:
        lines.append("**当前持仓**：" + "、".join(
            f"#{p.id} {p.code} {p.entry_date}@{p.entry_price}×{p.shares}"
            for p in d["positions"]))
        lines.append("")
    if d["signal"]:
        sig = d["signal"]
        conds, warns = _split_rows(sig["checklist"])
        passed = _passed_of_rows(sig["checklist"])
        verdict = "✅ 通过（ALL GREEN）" if passed else "❌ 未通过（有否决项）"
        lines.append(f"**买入信号**：{sig['reason']}（窗口 {sig['buy_window']}）")
        lines.append("")
        lines.append(f"**结论**：{verdict}")
        lines.append("")
        # P2-1: 数据事实层 —— 原始可观测数据（梯队/淘汰赛/候选，不做判断）
        lines.append("**数据事实**：")
        for f in _data_facts_of(d):
            lines.append(f"- {f}")
        lines.append("")
        lines.append("**关键判据**：")
        for name, ok, note in conds:
            mark = "✓" if ok else ("✗" if ok is False else "?")
            lines.append(f"- [{mark}] {name}: {note}")
        if warns:
            lines.append("")
            lines.append("**风险提示**：")
            for name, ok, note in warns:
                lines.append(f"- ⚠ {name}: {note}")
    else:
        lines.append(_no_signal_reason(d))
    lines += _sec_secondary_block(d.get("secondary_signal"))
    for s in d["sells"]:
        lines.append(f"**卖出建议**：{s.code} —— {s.reason}")
    if not d["sells"] and d["positions"]:
        lines.append("持仓无卖出建议（仍封板且非退潮）")
    return "\n".join(lines) + _hot_of(d) + _sec_reconcile_pos(d) + "\n"


def _sec_secondary_block(sec: dict | None) -> list[str]:
    """V14.1 拆函数（E1 红线）：次级推荐区块——checklist + 观察标注。

    NONE 窗口次级系观察记录（不导出、无仓位语义）——显式标注，防读者把
    禁买日的次级推荐误读为可执行信号（奎爷 2026-09-09 拍板）。
    """
    if not sec:
        return []
    conds, warns = _split_rows(sec["checklist"])
    passed = _passed_of_rows(sec["checklist"])
    verdict = "✅ 通过（ALL GREEN）" if passed else "❌ 未通过（有否决项）"
    lines = ["", f"**次级推荐**：{sec['reason']}（窗口 {sec['buy_window']}"
             "，放宽阈值 c3/c4/c5）"]
    if sec.get("buy_window") == "NONE":
        lines += ["", "> ⚠ 本记录为**禁买日观察记录**：当日 buy_window=NONE 禁买，"
                   "次级仅为身位线索，不构成买入指令，也不会导出到交易端。"]
    lines += ["", f"**结论**：{verdict}", ""]
    for name, ok, note in conds:
        mark = "✓" if ok else ("✗" if ok is False else "?")
        lines.append(f"- [{mark}] {name}: {note}")
    if warns:
        lines += ["", "**风险提示**："]
        lines += [f"- ⚠ {name}: {note}" for name, _, note in warns]
    return lines


def _hot_rank_of(trade_date: date) -> int | None:
    """V5：当日唯一候选人气榜排名 → collect（MD/JSON 同源）；无数据 None。"""
    sole = ladder.sole_top(ladder.build(trade_date))
    if sole is None:
        return None
    df = query_df("SELECT rank FROM hot_rank WHERE code = %s AND date = %s",
                  (sole.code, trade_date))
    return int(df["rank"].iloc[0]) if not df.empty else None


def _hot_of(d: dict) -> str:
    """T12 热度观察列：唯一候选当日人气榜排名（无数据留空，不参与判定）。"""
    if not d["sole"]:
        return ""
    df = query_df("SELECT rank FROM hot_rank WHERE code = %s AND date = %s",
                  (d["sole"].code, d["date"]))
    if df.empty:
        return ""
    return (f"\n\n热度观察：{d['sole'].name} 人气榜第 {int(df['rank'].iloc[0])}"
            " 名（影子验证列，不参与判定）")


def _no_signal_reason(d: dict) -> str:
    """空仓理由：定位卡在哪个条件。"""
    if d["window"] == "NONE":
        ph = d["stat"]["phase"] if d["stat"] else "?"
        if d["stat"] is not None and d["stat"].get("diverge"):
            ph += "·高位分歧降级"
        sec_hint = ""
        sec = d.get("secondary_signal")
        if sec:
            sec_hint = (f"（另有次级观察记录 {sec.get('code')}——"
                        "仅身位线索，不构成买入指令，详见⑥段）")
        return f"**空仓理由**：buy_window=NONE（{ph} 禁买）{sec_hint}"
    cand = d["sole"]
    if cand is None:
        return "**空仓理由**：无唯一换手高标候选（梯队断层或一字垄断）"
    # 用 rows 而非 checklist()：这里要的是带说明的行（逐条讲清卡在哪），
    # checklist() 只回布尔投影。判定聚合仍走同一套阈值，见 entry.rows 文档。
    conds, warns_raw = _split_rows(entry.rows(cand, d["window"]))
    fails = [f"{n}: {note}" for n, ok, note in conds if not ok]
    warns = [note for _, _, note in warns_raw if "⚠" in note]
    head = "**候选通过，未成全信号**" if not fails else \
        "**空仓理由（候选 " + cand.name + "）**\n" + "\n".join(
            f"- [✗] {f}" for f in fails)
    return head + "".join(f"\n- [{w}]" for w in warns)


def _caliber(trade_date: date) -> dict:
    """口径元数据：统计边界显式声明 + 双源计数（自算 vs 东财池）。"""
    pool = query_df(
        "SELECT count(*) FILTER (WHERE pool_type='ZT') zt,"
        " count(*) FILTER (WHERE pool_type='DT') dt"
        " FROM limit_pool_em WHERE date = %s", (trade_date,)).iloc[0]
    mine = query_df(
        "SELECT count(*) FILTER (WHERE is_limit_up) zt,"
        " count(*) FILTER (WHERE is_limit_down) dt FROM derived_bar"
        " WHERE date = %s", (trade_date,)).iloc[0]
    return {
        "universe": "A股全市场(沪深主板/创业板/科创板)",
        "st_excluded": True,
        "new_stocks_excluded": "上市<90自然日(first_bar_date代理)",
        "include_bj": False,
        "include_20cm": True,
        "snapshot_time": "盘后终值(EM f124≈15:00 BJT)",
        "limit_method": "收盘封板(close==limit_up_price)",
        "counts_self": {"zt": int(mine["zt"]), "dt": int(mine["dt"])},
        "counts_em_pool": {"zt": int(pool["zt"]), "dt": int(pool["dt"])},
        "diff_expectation": "东财池含ST/次新，自算剔除→自算≤池为预期方向；"
                            "Wind口径通常另含北交所/盘中触及，差异先对照本块边界",
    }


def _alert_usability(trade_date: date, d: dict) -> None:
    """A2-7：报告可用性非 OK → 入 alert（WARN 级，可确认追踪）。"""
    u = d.get("usability") or _usability(d)
    if u["state"] == "OK":
        return
    try:
        from emotion_core.algorithms import alerts
        alerts.record_dedup("WARN", "review",
                            f"{trade_date} 报告可用性 {u['state']}：{u['note']}")
    except Exception:                             # noqa: BLE001
        log.debug("可用性告警入队失败（不拦发布）")


def _sec_quality(d: dict) -> str:
    """⑩ 数据质检：四态渲染（P1-12）——缺数据 ≠ 无差异。"""
    q = d["quality"]
    diff = q["diff"]
    lines = ["## ⑩ 数据质检", ""]
    if diff is None:
        lines.append("⚠ 东财池缺失：无法对账（不代表无差异）")
    elif diff.empty:
        lines.append("自算连板 vs 东财：无差异 ✓")
    else:
        lines.append(f"⚠ 对账差异 {len(diff)} 行（当日信号建议人工复核）：")
        lines.append(diff.to_string(index=False))
    lines.append(_fmt_caliber(d["caliber"]))
    for warn in q.get("warns", []):
        lines.append(f"⚠ {warn}")
    return "\n".join(lines) + "\n"


def render_json(d: dict) -> str:
    """collect() 结果 → 机器可读决策快照（lkl/daily@2）。"""
    payload = {"schema": "lkl/daily@2", **d}
    return json.dumps(payload, ensure_ascii=False, indent=1, default=_jsonable)


_REQUIRED = {_sec_emotion, _sec_ladder, _sec_quality}

_SECTIONS = (_sec_overview, _sec_emotion, _sec_ladder, _sec_theme,
             _sec_elimination, _sec_promotion, _sec_advice, _sec_watchlist,
             _sec_dragon_env, _sec_counter, _sec_next_check, _sec_quality,
             _sec_trend)


def render_markdown(trade_date: date, d: dict | None = None) -> str:
    """⓪+十段+⑫ Markdown，段落顺序见 PLAN §4；d 可传入避免重取。"""
    d = d if d is not None else collect(trade_date)
    head = f"# 龙空龙复盘 {trade_date}\n\n"
    parts = [head]
    for fn in _SECTIONS:
        try:
            parts.append(fn(d))
        except Exception as exc:  # noqa: BLE001
            if fn in _REQUIRED:
                raise RuntimeError(
                    f"必需段 {fn.__name__} 渲染失败：{exc}——拒绝发布，"
                    "请先修复数据") from exc
            log.warning("段 %s 渲染失败：%s", fn.__name__, exc)
            parts.append(f"> ⚠ 本段渲染失败（{exc}），数据可能有缺口，"
                         "请人工核对该段依赖的表")
    return "\n".join(parts)


def publish(trade_date: date) -> str:
    """四出口：终端打印 + reports/*.md + reports/*.json 落盘 + review_report 入库。"""
    d = collect(trade_date)
    md = render_markdown(trade_date, d)
    print(md)
    _alert_usability(trade_date, d)
    out = Path("reports") / f"{trade_date}.md"
    out.parent.mkdir(exist_ok=True)
    out_json = out.with_suffix(".json")
    _atomic_write(out, md)
    _atomic_write(out_json, render_json(d))
    execute("INSERT INTO review_report (date, markdown) VALUES (%s,%s)"
            " ON CONFLICT (date) DO UPDATE SET markdown=EXCLUDED.markdown,"
            " created_at=now()", (trade_date, md))
    notify.push(md, trade_date)
    log.info("复盘报告 %s -> %s + %s + DB", trade_date, out, out_json)
    return str(out)
