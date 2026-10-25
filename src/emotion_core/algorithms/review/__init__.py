"""T8 盘后复盘报告（PLAN §4，B 核心交付）：十段渲染，终端+MD+JSON+DB 四出口。

Part 1+2（数据收集 + 段渲染）：市场统计、信号、淘汰赛、梯队、生态、题材、
晋级、质检、对账、热度、口径、可用性、建议、反证、验证点。
Part 3（渲染与发布）：JSON/Markdown 渲染与四出口发布。
格式化工具由 review.utils 提供。
"""

from __future__ import annotations

import re

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from emotion_core.algorithms import (
    caliber_b,
    entry,
    exit as exit_svc,
    ladder,
    phase_persistence,
    reconcile,
)
from emotion_core.algorithms import theme as theme_svc
from emotion_core.algorithms.review.utils import (
    _CHECKS,
    _MARK,
    _atomic_write,
    _counter_items,
    _delta_of,
    _env_caveat,
    _env_conflict,
    _fmt_caliber,
    _fmt_catalyst,
    _fmt_stat,
    _human_cond,
    _jsonable,
    _ladder_tag,
    _pct,
    _pctv,
    _ENV_MEANING,
    _seal_notes,
    _usability,
    _strip_position_block,
)
from emotion_core.algorithms.review.wording import translate_phase, translate_window
from emotion_core.services import notify, position
from emotion_core.utils.config import CONFIG
from emotion_core.utils.dates import prev_trading_day
from emotion_core.utils.db import connect_ro, execute, query_df

log = logging.getLogger("emotion_core.review")

# CONFIG 未收录（emotion.py §5 / review/utils.py 同先例）：取 lkl config.py 原值
_ACCEL_ENFORCE: bool = getattr(CONFIG, "ACCEL_ENFORCE", False)
_OVERVIEW_LOOKBACK: int = getattr(CONFIG, "OVERVIEW_LOOKBACK", 20)
_MIN_UNIVERSE_WARN: int = getattr(CONFIG, "MIN_UNIVERSE_WARN", 3000)
# ★2026-09-29 新增，非 CONFIG 字段（同上先例，取字面量）：
# 可用性告警的「新鲜度」窗口。报告的 detail 内嵌 trade_date，而
# `alerts.record_dedup` 按 (source, detail) 精确比对 → **每个交易日都是一条独立告警**。
# 单日跑只多一条，符合预期；但历史回补会一次性灌入数百条未确认 WARN，
# 把 alert 表埋掉——正是 2026-09-27~28「监控失明」那类噪声的同构问题
# （health 侧已用 _dedup_key 抹日期跨日去重，这里改为**按新鲜度**收敛）。
# 半年之前缺东财池的报告是历史事实、不可操作，不该占用告警通道；
# 但报告正文里的「数据可信度 UNKNOWN」标记照旧渲染，读者仍能看见。
_ALERT_WITHIN_DAYS: int = 5


def _recent_days_upto(d: date, n: int) -> list[date]:
    """d 及之前最近 n 个已入库交易日（升序）；不足 n 返回全部。

    契约适配：lkl `utils.dates.recent_trading_days(d, n)` 带参照日；emotion_core
    同名函数无参照日（只取全库最近 n 日，回填历史日会取错区间），故内联其 SQL。
    """
    df = query_df(
        "SELECT DISTINCT date FROM daily_bar WHERE date <= %s ORDER BY date DESC LIMIT %s", (d, n)
    )
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
    df = query_df(
        "SELECT code, reason, buy_window, checklist FROM signal"
        " WHERE confirm_date = %s AND action = 'BUY'",
        (trade_date,),
    )
    if df.empty:
        return None
    row = df.iloc[0].to_dict()
    row["checklist"] = [(n, ok, note) for n, ok, note in row["checklist"]]
    return row


def _signal_of_day_secondary(trade_date: date) -> dict | None:
    """次级推荐信号（action='SECONDARY'）。"""
    df = query_df(
        "SELECT code, reason, buy_window, checklist FROM signal"
        " WHERE confirm_date = %s AND action = 'SECONDARY'",
        (trade_date,),
    )
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
    return (
        [r for r in rows if not r[0].startswith(_WARN_PREFIX)],
        [r for r in rows if r[0].startswith(_WARN_PREFIX)],
    )


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
    is_one_word: bool | None = None
    is_suspended: bool = False


_VIEW_SQL = """
SELECT s.code, s.name,
       COALESCE(p.turnover_rate, b.turnover_rate) AS turnover_rate,
       p.bomb_times,
       db.is_one_word
FROM stock_basic s
LEFT JOIN daily_bar b ON b.code = s.code AND b.date = %(d)s
LEFT JOIN limit_pool_em p ON p.code = s.code AND p.date = %(d)s
     AND p.pool_type = 'ZT'
LEFT JOIN derived_bar db ON db.code = s.code AND db.date = %(d)s
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
        got = conn.execute(_VIEW_SQL, {"d": trade_date, "codes": [r.code for r in rows]}).fetchall()
    q = {r[0]: (r[1] or "", r[2], r[3], r[4]) for r in got}
    out = []
    for r in rows:
        nm, turn, bomb, oneword = q.get(r.code, ("", None, None, None))
        out.append(
            LadderRow(
                date=r.date,
                code=r.code,
                cont_days=r.cont_days,
                is_exchange=r.is_exchange,
                is_top=r.is_top,
                is_sole_top=r.is_sole_top,
                y_top_group_count=r.y_top_group_count,
                y_top_survivor_count=r.y_top_survivor_count,
                name=nm,
                turnover_rate=None if turn is None else float(turn),
                bomb_times=None if bomb is None else int(bomb),
                is_one_word=None if oneword is None else bool(oneword),
                is_suspended=bool(getattr(r, "is_suspended", False)),
            )
        )
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
        "SELECT code, is_limit_up, touched_limit, cont_days FROM derived_bar WHERE date = %s",
        (trade_date,),
    )
    state = {
        r.code: (bool(r.is_limit_up), bool(r.touched_limit), int(r.cont_days))
        for r in df.itertuples()
    }
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
        out.append({"code": r.code, "name": r.name, "y_cont": r.cont_days, "today": result})
    return out


def _pool_missing(trade_date: date) -> list[str]:
    """A7：真缺失判定 = 池 0 行 **且** 自算口径有货。"""
    pool = query_df(
        "SELECT count(*) FILTER (WHERE pool_type='ZT') AS zt,"
        " count(*) FILTER (WHERE pool_type='ZB') AS zb,"
        " count(*) FILTER (WHERE pool_type='DT') AS dt"
        " FROM limit_pool_em WHERE date = %s",
        (trade_date,),
    ).iloc[0]
    mine = query_df(
        "SELECT count(*) FILTER (WHERE is_limit_up) AS zt,"
        " count(*) FILTER (WHERE is_limit_down) AS dt,"
        " count(*) FILTER (WHERE is_bomb) AS zb"
        " FROM derived_bar WHERE date = %s",
        (trade_date,),
    ).iloc[0]
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
    n = query_df("SELECT count(*) n FROM daily_bar WHERE date = %s", (trade_date,))["n"].iloc[0]
    if n < _MIN_UNIVERSE_WARN:
        warns.append(f"当日 daily_bar 仅 {n} 行，疑似缺数")
    missing = _pool_missing(trade_date)
    if missing:
        warns.append(
            "东财" + "、".join(f"{k}池" for k in missing) + " 0 行但自算口径有货——对账口径缺失，"
            "下方「无差异」不可信（A7）"
        )
    # ★2026-09-29：`reconcile` 是**模块**不是函数（`from ... import ladder,
    # reconcile` 绑的是模块对象），调用须写 `reconcile.reconcile(...)`。
    # 这行此前是死代码：`if not missing` 守卫让它在 limit_pool_em 恒空的整个
    # 时期从不执行——池一补上就崩，填池反而暴露出这条潜伏断点。
    diff = reconcile.reconcile(trade_date) if not missing else None
    return {"diff": diff, "y_comp": ladder.y_competition(trade_date), "warns": warns}


def _seal_map(trade_date: date) -> dict[str, tuple[str, str]]:
    """R1：炸板票首/末回封时间（limit_pool_em，东财口径），梯队附注用。"""
    df = query_df(
        "SELECT code, first_seal, last_seal FROM limit_pool_em"
        " WHERE date = %s AND pool_type = 'ZT'",
        (trade_date,),
    )
    return (
        {r["code"]: (r["first_seal"], r["last_seal"]) for _, r in df.iterrows()}
        if not df.empty
        else {}
    )


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
        " ORDER BY promote_from DESC, layer",
        (trade_date,),
    )


def _dragon_env_of(trade_date: date) -> dict | None:
    """生态评级（§1.12），从 market_stat 读回（由 dragon_env 写入）。"""
    row = _market_stat_row(trade_date)
    if not row or not row.get("dragon_env"):
        return None
    return {
        "rating": row["dragon_env"],
        "goods": row.get("dragon_env_reasons") or [],
        "bads": row.get("dragon_env_risks") or [],
    }


def _health_of(trade_date: date) -> str:
    """V9：链路健康度——最近一次 review_report 成功落盘距今几个交易日。

    daily.sh 非交易日 SKIP 是正常行为（不计数）；跨过非交易日的缺口
    以已入库交易日数衡量，>1 个交易日即显式报「链路疑似断档」。
    """
    # P1-11：报告生成中查 max(date) 必然不含「正在生成的今天」——INSERT 在
    # 报告末尾。把生成日本身视为「即将成功」，gap 只数 (last, trade_date)
    # 开区间内的交易日，昨日有报告 → gap=0 不误报断档。
    df = query_df("SELECT max(date) AS d FROM review_report WHERE date < %s", (trade_date,))
    last = df["d"].iloc[0]
    if last is None:
        return "链路健康：本库从未产出报告"
    days = _recent_days_upto(trade_date, 60)
    gap = sum(1 for d_ in days if last < d_ < trade_date)
    return f"链路健康：最近成功报告 {last}" + (
        "" if gap == 0 else f"（距今 {gap} 个交易日，⚠ 疑似断档）"
    )


_MAX_NOTE_NAMES = 3


def _top_boards_note(d: dict, key: str) -> str:
    """「名义最高板 / 可交易最高板」数字后面缀的票名，如 `（新华传媒 600825 一字）`。

    R67-1：速览只给数字时 owner 手机上不知道是哪只票（10-12 收到「名义最高板：9
    → 10」）。这里按 `stat[key]` 反查当日同高度的梯队行补票名。

    - `max_limit_days`：`ladder_rows` 里 `cont_days == stat[key]` 的行；
    - `tradable_max_days`：同高且额外要求 `is_exchange is True`（换手板）；
    - 停牌行不算身位（owner 拍板：停牌身位不计入最高板数字），一律排除；
    - 找不到同高行 → 兜底直查 `derived_bar`（数字来自
      `algorithms/emotion.py:_top_amplitude()`，口径是 derived_bar 全主板，
      可能含被 ladder_day 剔掉的 ST/次新，故不能只信 ladder_rows）；
    - 一只都没有 / 任何异常 → 返回 ""，绝不抛异常拖垮报告渲染。
    """
    try:
        stat = d.get("stat") or {}
        height = stat.get(key)
        if height in (None, ""):
            return ""
        try:
            height = int(height)
        except (TypeError, ValueError):
            return ""
        if height <= 0:
            return ""

        rows = [
            r
            for r in (d.get("ladder_rows") or [])
            if r.cont_days == height and not getattr(r, "is_suspended", False)
        ]
        if key == "tradable_max_days":
            rows = [r for r in rows if r.is_exchange]
        items = [(r.name or "", r.code, bool(r.is_one_word)) for r in rows]
        if not items:
            # 兜底：数字来源是 derived_bar（不做 ST/次新剔除），可能无同高梯队行
            exch = " AND d.is_exchange" if key == "tradable_max_days" else ""
            df = query_df(
                "SELECT d.code, s.name, d.is_one_word FROM derived_bar d"
                " JOIN stock_basic s USING (code)"
                f" WHERE d.date = %s AND d.cont_days = %s{exch}"
                " ORDER BY d.code LIMIT %s",
                (d.get("date"), height, _MAX_NOTE_NAMES),
            )
            if df is None or df.empty:
                return ""
            items = [
                (str(r["name"] or ""), str(r["code"]), bool(r["is_one_word"]))
                for _, r in df.iterrows()
            ]
        if not items:
            return ""

        def _fmt(nm: str, code: str, one: bool) -> str:
            tag = f"{nm} {code}".strip()
            return f"{tag} 一字" if one else tag

        shown = items[:_MAX_NOTE_NAMES]
        text = "、".join(_fmt(nm, code, one) for nm, code, one in shown)
        if len(items) > _MAX_NOTE_NAMES:
            text += f" 等 {len(items)} 只"
        return f"（{text}）"
    except Exception as exc:  # noqa: BLE001 —— 速览不能因补票名而失败
        log.warning("速览最高板票名补全失败（%s，忽略）：%s", key, exc)
        return ""


def _close_of(trade_date: date, code: str) -> float | None:
    """当日收盘价（口径B 买点 = 收盘 × 板比的涨停价）；缺数 None，不猜。"""
    df = query_df("SELECT close FROM daily_bar WHERE date = %s AND code = %s", (trade_date, code))
    if df is None or df.empty:
        return None
    v = df["close"].iloc[0]
    return None if v is None or pd.isna(v) else float(v)


def _name_of(d: dict, code: str) -> str:
    """候选票名：先在当日梯队行里找，找不到留空（不为了好看去查全表）。"""
    for r in d.get("ladder_rows") or []:
        if r.code == code:
            return r.name or ""
    sole = d.get("sole")
    return (sole.name or "") if sole is not None and sole.code == code else ""


def _action_card(d: dict) -> list[str]:
    """⓪ 顶部卡（A 止血版）：市场温度 / 观察名单 / 次日验证点。

    A 止血（2026-10-21 奎爷拍板 + ADR 0002 复核节）：口径B 打板按「触及即成交」
    实测每信号 −1.26%（成交后 −2.01%、胜率 50%、炸板占成交 30% 且均亏 15%），
    故本卡**不给买点、不给卖点、不给仓位**——只回答「今天市场什么样、谁进了
    观察名单、明天看什么」。信息与 ⑥⑨ 段同源，这里只做翻译与压缩，不新增判定。
    """
    if "window" not in d or not d.get("stat"):
        return []
    s = d["stat"]
    phase = s.get("phase") or "?"
    mood = f"**市场**：{translate_phase(phase)}（{phase}）"
    if s.get("diverge"):
        mood += "·高位分歧"
    lc, br = s.get("limit_up_count"), s.get("bomb_rate")
    detail = []
    if lc is not None:
        detail.append(f"涨停 {int(lc)} 家")
    if br is not None and not pd.isna(br):
        detail.append(f"炸板 {_pct(br)}")
    if detail:
        mood += "——" + "，".join(detail)
    lines = [mood, ""]

    sig = d.get("signal")
    if sig:
        code = sig["code"]
        who = f"{code} {_name_of(d, code)}".strip()
        lines.append(f"**观察**：{who} —— 硬条件全过，只进观察名单，不出买点")
        lines.append("")
        lines.append(
            "> 盘后可执行的打板口径实测每信号 −1.26%（成交后 −2.01%、"
            "胜率 50%、炸板占成交 30% 且均亏 15%）→ 本报告不出买点、不给仓位。"
        )
    else:
        # 不出手的原因统一由 _no_signal_reason 给，顶部与⑥段不许两套说法
        lines.append(f"**观察**：{_no_signal_reason(d, short=True)}")
    checks = _CHECKS.get(phase) or []
    if checks:
        lines += ["", f"**次日验证点**：{checks[0]}"]
    lines.append("")
    return lines


def _sec_overview(d: dict) -> str:
    """⓪ 速览（V9）：链路健康 + 较昨日变化。放最前，30 秒读完当日结论。

    产品 A1-1a：首行即「本报告可用于决策」总判（三态：OK/PARTIAL/
    UNKNOWN），缺数绝不显示绿色——用户不用翻到⑩质检才知道可信度。
    P1（产品审计）：+ 数据健康度徽章 + 口径覆盖声明 + 空仓原因可见化。
    """
    u = d.get("usability") or _usability(d)
    mark = {"OK": "✅", "PARTIAL": "⚠", "UNKNOWN": "⚠"}[u["state"]]
    lines = (
        ["## ⓪ 速览", ""]
        + _action_card(d)
        + [f"**数据可信度：{mark} {u['state']}** —— {u['note']}", "", _health_of(d["date"]), ""]
    )
    # #7 口径覆盖声明（精简版，完整口径见⑩质检段）
    c = d.get("caliber")
    if c:
        bj = "含" if c.get("include_bj") else "不含"
        lines.append(
            f"**口径覆盖**：{c['universe']} ｜ 剔ST ｜ 剔次新 ｜ {bj}北交所"
            f" ｜ 含20cm ｜ 自算涨停 {c['counts_self']['zt']} / "
            f"东财池 {c['counts_em_pool']['zt']}"
        )
    lines.append("")
    # #2 空仓/不动理由可见化（仅当 dict 含所需字段，否则跳过）。
    # W1′：不出手的原因已经进了顶部操作卡的「买」行，这里不再重复一遍；
    # 只有操作卡没渲染（旧调用方缺字段）时才补。
    if not _action_card(d) and not d.get("signal") and "window" in d:
        reason = _no_signal_reason(d)
        if reason:
            lines.append(f"**今日建议**：{reason}")
    lines.append("**较昨日变化**")
    s, p = d["stat"], d["prev_stat"]
    if not s:
        lines.append("- 当日 market_stat 无数据（先跑 `lkl emotion`）")
    else:
        p = p or {}
        for label, key in (
            ("涨停家数", "limit_up_count"),
            ("名义最高板", "max_limit_days"),
            ("可交易最高板", "tradable_max_days"),
        ):
            suffix = "" if key == "limit_up_count" else _top_boards_note(d, key)
            lines.append(_delta_of(s.get(key), p.get(key), label, suffix))
        if s.get("phase") != (p or {}).get("phase"):
            lines.append(f"- 阶段：{(p or {}).get('phase', '—')} → {s.get('phase')}")
        else:
            lines.append(f"- 阶段：{s.get('phase')}（持平）")
        # W8′：字段名换成用户能读的词，且档位翻成人话（A 止血后＝情绪强度档）
        lines.append(
            _delta_of(
                translate_window(s.get("buy_window") or "NONE"),
                translate_window((p or {}).get("buy_window") or ""),
                "情绪档",
            )
        )
        lines.append(_delta_of(s.get("dragon_env"), p.get("dragon_env"), "生态评级"))
    return "\n".join(lines) + "\n"


def _sec_trend(d: dict) -> str:
    """⑫ 近 N 日走势（V9）：phase/buy_window/生态评级时间线，查异常翻转。"""
    days = _recent_days_upto(d["date"], _OVERVIEW_LOOKBACK)
    if len(days) < 2:
        return "## ⑫ 近 20 日走势\n\n历史不足 2 个交易日，无时间线\n"
    df = query_df(
        "SELECT date, phase, buy_window, dragon_env FROM market_stat"
        " WHERE date = ANY(%s) ORDER BY date",
        (days,),
    )
    lines = [
        "## ⑫ 近 20 日走势（情绪 / 情绪档 / 生态）",
        "",
        "| 日期 | 情绪 | 情绪档 | 生态 |",
        "|---|---|---|---|",
    ]
    for r in df.itertuples():
        ph = r.phase or "—"
        win = translate_window(r.buy_window) if r.buy_window else "—"
        lines.append(
            f"| {r.date} | {ph}（{translate_phase(ph)}）"
            if r.phase
            else f"| {r.date} | — | {win} | {r.dragon_env or '—'} |"
        )
        if r.phase:
            lines[-1] += f" | {win} | {r.dragon_env or '—'} |"
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
        lines.append(
            "⚠ 以下信号已标记采纳但无对应 OPEN 持仓，请确认是否漏录（漏录则卖出建议不覆盖该仓位）："
        )
        lines += [f"- {r['date']} {r['code']}（信号#{r['id']}）" for r in d["orphan_adoptions"]]
    return "\n".join(lines) + "\n"


def _orphan_adoptions() -> list[dict]:
    """ADOPTED 买入信号中，code 无 OPEN 持仓的清单（近 10 个交易日）。"""
    df = query_df(
        "SELECT s.id, s.confirm_date AS date, s.code FROM signal s"
        " WHERE s.action='BUY' AND s.status='ADOPTED'"
        " AND NOT EXISTS (SELECT 1 FROM position p WHERE p.code = s.code"
        " AND p.status='OPEN')"
        " AND s.confirm_date >= CURRENT_DATE - INTERVAL '14 days'"
        " ORDER BY s.confirm_date DESC"
    )
    return df.to_dict("records") if not df.empty else []


def _phase_persistence_line(d: dict) -> str:
    """相位持续性一句话；算不出来返回空串（这一句是锦上添花，不能拖垮 ① 段）。"""
    try:
        res = phase_persistence.persistence(d["date"])
    except Exception:  # noqa: BLE001 — 展示失败不许影响报告
        return ""
    return phase_persistence.line(res)


def _sec_emotion(d: dict) -> str:
    """① 情绪面板：指标 + 阶段（R2：区分当日触发/历史延续）+ 加速横幅。"""
    if not d["stat"]:
        return "## ① 情绪面板\n\nmarket_stat 缺当日数据：先跑 `lkl emotion`\n"
    s = d["stat"]
    banner = ""
    if s.get("accelerate"):
        mode = "已降级" if _ACCEL_ENFORCE else "影子模式，未干预窗口"
        banner = (
            f"\n\n⚡ **加速事件命中**（{mode}）\n\n"
            f"内部判据（排查用，不参与买卖）：{s.get('accel_reason', '')}\n"
        )
    inherited = bool(s.get("phase_inherited"))
    phase_label = f"{s['phase']}（延续昨日）" if inherited else f"{s['phase']}（当日触发）"
    window = s.get("buy_window") or "NONE"
    tail = f"情绪档：{translate_window(window)}"
    if s.get("diverge"):
        tail += "（高位分歧）"
    return (
        "## ① 情绪面板\n\n"
        + _fmt_stat(s, d["prev_stat"])
        + f"\n\n**市场情绪：{translate_phase(s['phase'])} · {phase_label}**"
        + f"\n\n{tail}"
        + ("\n\n⚠ **退潮清仓信号生效**" if s["force_liquidate"] else "")
        + banner
        + f"\n\n**为什么这么判**：{_human_state_reason(s)}"
        + (
            f"\n\n**这个相位还能撑多久**：{_phase_persistence_line(d)}"
            if _phase_persistence_line(d)
            else ""
        )
        + "\n"
    )


#: 市场相位触发词 → 人话（`market_stat.reason` 括号里那句话）。
_STATE_TRIGGER_HUMAN = {
    "反转": "跌停/炸板开始收敛（反转迹象）",
    "无候选放宽": "没有可做的候选，按放宽条件判定",
    "两日最高板炸板": "最高板连着两天炸板",
    "延续：当日无规则触发，数字仅参考": "今天没有任何规则触发，沿用昨天的判断",
}


def _human_state_reason(s: dict) -> str:
    """把 `market_stat.reason` 的机器串翻成一句人话（W8′）。

    原始串形如 `退潮（延续：当日无规则触发…）·加速(影子)(涨停55 炸率0.2/阈0.39 …)`：
    括号里的数字在①段的表里已经逐列给过，这里不再重复；`·加速(影子)` 由专门的
    横幅展示，也不重复。拿不准触发词时只回「触发了某相的规则」，不编理由。
    """
    raw = (s or {}).get("reason") or ""
    if not raw:
        return "无判定依据记录"
    head = raw.split("·")[0]
    m = re.match(r"^[^（(]*[（(]([^）)]*)[）)]", head)
    trig = m.group(1) if m else ""
    if trig in _STATE_TRIGGER_HUMAN:
        return _STATE_TRIGGER_HUMAN[trig]
    if s.get("phase_inherited"):
        return "今天没有规则触发，沿用昨天的判断"
    if re.match(r"^nb=\d", trig):
        return f"高位股负反馈集中（{trig}）"
    return f"当天数据触发了「{s.get('phase')}」的判定规则"


def _sec_ladder(d: dict) -> str:
    rows = d["ladder_rows"]
    # R67-2：停牌行（旁路展示）与正常梯队行分开——停牌不计身位、不并进板数分组，
    # 只在梯队末尾单独一行「N板（停牌）」，避免污染最高板口径。
    susp = [r for r in rows if getattr(r, "is_suspended", False)]
    rows = [r for r in rows if not getattr(r, "is_suspended", False)]
    if not rows and not susp:
        return "## ② 梯队全景\n\n无连板梯队（当日无 ≥2 板）\n"
    by_h: dict[int, list] = {}
    for r in rows:
        by_h.setdefault(r.cont_days, []).append(r)
    lines = ["## ② 梯队全景", ""]
    for h in sorted(by_h, reverse=True):
        tags = [_ladder_tag(r) for r in by_h[h]]
        lines.append(f"- **{h}板** [{len(tags)}只] " + "、".join(tags))
    for h in sorted({r.cont_days for r in susp}, reverse=True):
        tags = [f"{r.name}({r.code})" for r in susp if r.cont_days == h]
        lines.append(f"- **{h}板（停牌）** [{len(tags)}只] " + "、".join(tags))
    lines.append("")
    lines.append(
        "> 开板计数为东财 zbc 字段（逐笔微观口径，一分钟分时看到的"
        "明显开板阶段通常少于该值）。判据 c5「炸板回封」用"
        " zbc>=1，语义为「开过板且封住」，不受影响。"
    )
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
        " g.completeness DESC LIMIT 12",
        (trade_date,),
    )
    return df


def _theme_tag_rows(trade_date: date) -> pd.DataFrame:
    return query_df(
        "SELECT t.code, s.name, l.cont_days, t.primary_theme, t.catalyst,"
        " t.catalyst_source, t.evidence_date, t.confidence, t.role,"
        " COALESCE(t.secondary_themes, '{}'::text[]) AS secondary_themes"
        " FROM theme_tag t JOIN stock_basic s ON s.code = t.code"
        " JOIN ladder_day l ON l.date = t.date AND l.code = t.code"
        " WHERE t.date = %s ORDER BY l.cont_days DESC",
        (trade_date,),
    )


def _fmt_false_relation(tags: pd.DataFrame) -> str:
    """名称相似但题材无交集 → 提示防归同组（theme_svc 封装）。"""
    pairs = theme_svc.false_relation_pairs(
        [(r.code, r.name, list(r.secondary_themes)) for r in tags.itertuples()]
    )
    if not pairs:
        return ""
    return "\n> ⚠ FALSE_RELATION 提示：" + "；".join(
        f"{a} vs {b} 名称相似但题材标签无交集，勿归同组" for a, b in pairs
    )


def _sec_theme(d: dict) -> str:
    """③ 题材结构（T13：Wind 概念标签+公告催化聚合，仅梯队口径）。"""
    t = d["themes"]
    if t.empty:
        return "## ③ 题材结构\n\n_未生成（先跑 lkl theme <date>）_\n"
    lines = [
        "## ③ 题材结构",
        "",
        "| 题材 | 最高板 | 中位 | 低位 | 首板* | 完整度 | 状态 | 成员 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in t.itertuples():
        lines.append(
            f"| {r.theme} | {r.highest_board}（{r.top_name}） "
            f"| {r.mid_count} | {r.low_count} | {r.first_board_count} "
            f"| {r.completeness} | {r.status} | {r.member_count} |"
        )
    lines.append(_fmt_catalyst(d["theme_tags"]))
    lines.append(_fmt_false_relation(d["theme_tags"]))
    lines += [
        "",
        "*P1 仅梯队（cont_days≥2）口径，首板盲区待 P1.5 扩展；"
        "标签为 Wind 概念（风格噪声已滤），催化剂为公告关键词规则。",
    ]
    return "\n".join(lines) + "\n"


def _sec_elimination(d: dict) -> str:
    """③ 淘汰赛况：昨日最高板组今日各自结果。"""
    rows = d["elimination"]
    if not rows:
        return "## ④ 淘汰赛况\n\n昨日无 ≥2 板最高板组（无淘汰赛）\n"
    lines = ["## ④ 淘汰赛况", "", "| 股票 | 昨日板数 | 今日结果 |", "|---|---|---|"]
    lines += [f"| {r['name']}({r['code']}) | {r['y_cont']} | {r['today']} |" for r in rows]
    g, s = d["quality"].get("y_comp", (0, 0))
    lines += ["", f"昨日最高板组 {g} 只 → 今日幸存 {s} 只"]
    if d["sole"]:
        c = d["sole"]
        abs_max = max((r.cont_days for r in d["ladder_rows"]), default=0)
        note = (
            f"（绝对最高 {abs_max}板为一字垄断，D7 不计有效投票）" if abs_max > c.cont_days else ""
        )
        lines.append(f"**唯一换手高标确认：{c.name}({c.code}) {c.cont_days}板**" + note)
    return "\n".join(lines) + "\n"


def _sec_promotion(d: dict) -> str:
    """⑤ 晋级矩阵：各层名义/换手双口径与背离度（§1.10）。"""
    p = d["promotion"]
    if p is None or p.empty:
        return "## ⑤ 晋级矩阵\n\n_未生成（先跑 `lkl promotion <date>`）_\n"
    lines = [
        "## ⑤ 晋级矩阵",
        "",
        "| 层 | 分母 | 名义晋级 | 换手晋级 | 名义率 | 换手率 | 背离 | 失败股今日 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in p.itertuples():
        lines.append(
            f"| {r.layer} | {r.promote_from} | {r.promote_nominal} "
            f"| {r.promote_exchange} | {_pct(r.rate_nominal)} "
            f"| {_pct(r.rate_exchange)} | {_pct(r.divergence)} "
            f"| {_pctv(r.fail_perf)} |"
        )
    lines += [
        "",
        "> 换手率＝可成交口径（真机会）；背离＝名义−换手，高＝高度靠一字推"
        "、机会在消失。分母 < 3 不出率（样本不足，非 0%）。",
    ]
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
    lines = [
        "## ⑦ 生态评级（龙空龙可用性，**非买入信号**）",
        "",
        f"**评级：{de['rating']}** —— {_ENV_MEANING.get(de['rating'], '')}{_env_caveat(de)}",
        "",
        *_env_conflict(d),
        f"> 语义区分：本段评「生态可不可干」；今日 `buy_window={d['window']}`"
        f" 是「今天给不给买」。两者独立，出现矛盾时以各自语义分别理解，不合并。",
        "",
    ]
    for title, key in (("有利条件", "goods"), ("不利条件", "bads")):
        lines.append(f"**{title}**")
        lines += [f"- [{_MARK(i.get('ok'))}] {i.get('cond')}：{i.get('note')}" for i in de[key]]
        lines.append("")
    return "\n".join(lines) + "\n"


def _sec_counter(d: dict) -> str:
    """⑧ 反证与不确定性：无内容也显式写「无反证」，不省略本段。"""
    items = _counter_items(d)
    lines = ["## ⑧ 反证与不确定性", ""]
    lines.append(
        "本报告结论暂无已知反证；所有判据方向一致。"
        if not items
        else "\n".join(f"- {t}" for t in items)
    )
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
        lines.append(
            "- ⚡ 加速事件已命中：重点观察一字板高标是否开板放量"
            "（开板即分歧日，属验证窗口而非买点）"
        )
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
    facts.append(
        f"梯队最高 {abs_max}板"
        + (f"（{len([r for r in rows if r.cont_days == abs_max])} 只同身位）" if abs_max else "")
    )
    g, s = d["quality"].get("y_comp", (0, 0))
    facts.append(f"昨日最高板组 {g} 只 → 今日幸存 {s} 只")
    sole = d["sole"]
    if sole is not None:
        turn = f"{sole.turnover_rate:.1f}%" if sole.turnover_rate else "缺数据"
        facts.append(f"唯一换手高标 {sole.name}({sole.code}) {sole.cont_days}板（换手率 {turn}）")
    else:
        facts.append("无唯一换手高标候选")
    return facts


def _sec_advice(d: dict) -> str:
    """⑥ 明日参考：信号 checklist / 空仓理由 / 卖出建议 / 持仓表头。

    P2（产品审计 #1）：信号分层展示——结论 → 关键判据 → 数据事实 → 风险提示。
    """
    lines = ["## ⑥ 明日参考（只给信息不给指令）", ""]
    if d["positions"]:
        lines.append(
            "**当前持仓**："
            + "、".join(
                f"#{p.id} {p.code} {p.entry_date}@{p.entry_price}×{p.shares}"
                for p in d["positions"]
            )
        )
        lines.append("")
    if d["signal"]:
        sig = d["signal"]
        conds, warns = _split_rows(sig["checklist"])
        # W1′：W1 行恒 True（只是观察项），只有真扎堆时 note 才带 ⚠；
        # 没扎堆就别在「风险提示」里挂一条假警告。
        warns = [w for w in warns if "⚠" in w[2]]
        passed = _passed_of_rows(sig["checklist"])
        verdict = (
            "✅ 硬条件全部通过（只进观察名单；本系统不出买入建议）"
            if passed
            else "❌ 未通过（有否决项）"
        )
        who = f"{sig['code']} {_name_of(d, sig['code'])}".strip()
        lines.append(f"**观察名单：{who}**")
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
            mark = "✓" if ok else ("✗" if ok is False else "·")
            lines.append(f"- [{mark}] {_human_cond(name)}：{note}")
        if warns:
            lines.append("")
            lines.append("**风险提示**：")
            for name, ok, note in warns:
                lines.append(f"- ⚠ {name}: {note}")
    else:
        lines.append(_no_signal_reason(d))
    lines += _sec_secondary_block(d.get("secondary_signal"))
    # A 止血：不再给卖出指令。口径B 的买入前提已废（打板成交即负期望），
    # 按口径B 排出来的「T+2 开盘卖」就失去依据；持仓明细仍由 ⑩/对账段如实列出。
    lines += _sec_review(d)
    if d["positions"]:
        lines.append(
            "**持仓**：本系统不再给买卖指令（口径B 复核证伪，见 ADR 0002）。"
            "若你仍按旧口径在建仓后第 1 个交易日开盘离场，那是你自己的纪律，"
            "不在本报告的建议范围内。"
        )
    return "\n".join(lines) + _hot_of(d) + _sec_reconcile_pos(d) + "\n"


_REVIEW_SIGNALS = 30
"""⑥ 段「事后对照」取最近多少条观察记录（= 最近 N 条 BUY 行，不是 N 个自然日）。"""


def _fmt_signed(v: float | None, unit: str = "%") -> str:
    """带符号的数值；缺数给 —（不填 0）。"""
    if v is None:
        return "—"
    return f"{v:+.2f}{unit}"


def _sec_review(d: dict) -> list[str]:
    """⑥ 段尾部：把最近 N 条观察记录按口径B 重算一遍（事后对照，不给建议）。"""
    try:
        df = caliber_b.evaluate(_REVIEW_SIGNALS)
        summary = caliber_b.summarize(df)
    except Exception as exc:  # noqa: BLE001 — 度量失败不许拖垮报告
        return ["", f"**事后对照**：这轮算不出来（{type(exc).__name__}: {exc}）"]
    if not summary["n"]:
        return ["", "**事后对照**：`signal` 表里还没有可评估的观察记录"]

    groups = summary["groups"]
    base = next((g for g in groups if g["group"] == "全部"), None)
    if base is None:
        return ["", "**事后对照**：这轮算不出来（没有分组结果）"]
    filled_n = sum(1 for x in df["filled"].tolist() if x is True)
    out = [
        "",
        f"**事后对照（口径B）**：最近 {summary['n']} 条观察记录，"
        f"按「当时真打板买入」重算一遍（买点=次日涨停价、触及即成交、"
        f"T+2 开盘卖、费后 0.15%）——{summary['n']} 条里 {filled_n} 条能买到，"
        f"每信号 {_fmt_signed(base['mean'])}，买到后 {_fmt_signed(base['mean_filled'])}",
    ]
    keep = ["全部"] + [
        g["group"]
        for g in groups
        if g["group"] in ("退潮", "高潮", "发酵", "冰点", "严格胜出", "身位延续", "新插队")
    ]
    rows = [g for name in keep for g in groups if g["group"] == name]
    if rows:
        out += [
            "",
            "| 分组 | 条数 | 成交率 | 每信号 | 成交后 | 成交胜率 |",
            "|---|---|---|---|---|---|",
        ]
        for g in rows:
            out.append(
                f"| {g['group']} | {g['n']} | {g['fill_rate']:.0f}% | "
                f"{_fmt_signed(g['mean'])} | {_fmt_signed(g['mean_filled'])} | "
                f"{g['win_rate_filled'] if g['win_rate_filled'] is not None else '—'}% |"
            )
        out.append("")
        out.append(
            "> 口径B 的成交假设是「T+1 盘中触及涨停即成交」——炸板撤不回，"
            "所以这是真实可执行的算法，也是它必然为负的原因（ADR 0002）。"
            "未成交的条目按 0 计入「每信号」，同时单列成交率。"
        )
    return out


def _sec_secondary_block(sec: dict | None) -> list[str]:
    """V14.1 拆函数（E1 红线）：次级推荐区块——checklist + 观察标注。

    NONE 窗口次级系观察记录（不导出、无仓位语义）——显式标注，防读者把
    禁买日的次级推荐误读为可执行信号（奎爷 2026-09-09 拍板）。
    """
    if not sec:
        return []
    conds, warns = _split_rows(sec["checklist"])
    warns = [w for w in warns if "⚠" in w[2]]
    passed = _passed_of_rows(sec["checklist"])
    verdict = "✅ 通过（ALL GREEN）" if passed else "❌ 未通过（有否决项）"
    why = (sec.get("reason") or "").strip() or "硬条件放宽档的观察记录"
    lines = [
        "",
        f"**次级观察**：{sec['code']} —— {why}"
        f"（情绪档：{translate_window(sec['buy_window'])}；"
        "门槛放宽：低位身位、3 板起、换手要求降档）",
    ]
    if sec.get("buy_window") == "NONE":
        lines += [
            "",
            "> ⚠ 本记录是**不出手日的观察记录**：只提供身位线索，不能下单，也不会导出到交易端。",
        ]
    lines += ["", f"**结论**：{verdict}", ""]
    for name, ok, note in conds:
        mark = "✓" if ok else ("✗" if ok is False else "·")
        lines.append(f"- [{mark}] {_human_cond(name)}：{note}")
    if warns:
        lines += ["", "**风险提示**："]
        lines += [f"- ⚠ {name}: {note}" for name, _, note in warns]
    return lines


def _hot_rank_of(trade_date: date) -> int | None:
    """V5：当日唯一候选人气榜排名 → collect（MD/JSON 同源）；无数据 None。"""
    sole = ladder.sole_top(ladder.build(trade_date))
    if sole is None:
        return None
    df = query_df(
        "SELECT rank FROM hot_rank WHERE code = %s AND date = %s", (sole.code, trade_date)
    )
    return int(df["rank"].iloc[0]) if not df.empty else None


def _hot_of(d: dict) -> str:
    """T12 热度观察列：唯一候选当日人气榜排名（无数据留空，不参与判定）。"""
    if not d["sole"]:
        return ""
    df = query_df(
        "SELECT rank FROM hot_rank WHERE code = %s AND date = %s", (d["sole"].code, d["date"])
    )
    if df.empty:
        return ""
    return (
        f"\n\n热度观察：{d['sole'].name} 人气榜第 {int(df['rank'].iloc[0])}"
        " 名（影子验证列，不参与判定）"
    )


def _no_signal_reason(d: dict, short: bool = False) -> str:
    """今天不出手的原因（W11′ 人话版）：先给结论，再给卡在哪一条。

    short=True 只回一句结论（操作卡用）；默认带逐条判据与警告（⑥段用）。
    """
    if d["window"] == "NONE":
        ph = d["stat"]["phase"] if d["stat"] else "?"
        gen = translate_phase(ph)
        if ph == "冰点":
            head = f"市场处于{gen}（冰点）——连板高度太低，今天不产观察标的"
        elif ph == "高潮":
            head = (
                f"市场处于{gen}（高潮），但高位分歧降级（昨日高位股负反馈集中）——今天不产观察标的"
            )
        else:
            # 相位与窗口打架：W7′ 之后只有冰点/高潮降级会出 NONE，
            # 退潮还写着 NONE 说明 market_stat 没按新口径重算（数据是旧的）。
            head = (
                f"市场处于{gen}（{ph}），但库里 buy_window 依旧是 NONE"
                "——口径已改为退潮可买，需重跑 emotion 步骤刷新 market_stat"
            )
        if short:
            return head
        sec_hint = ""
        sec = d.get("secondary_signal")
        if sec:
            sec_hint = f"（另有次级观察记录 {sec.get('code')}——只有身位线索，不能下单，详见⑥段）"
        return f"**今天不出手**：{head}{sec_hint}"
    cand = d["sole"]
    if cand is None:
        msg = "今天没有符合条件的连板龙头（梯队断层，或高板全是一字板）"
        return msg if short else f"**今天不出手**：{msg}"
    # 用 rows 而非 checklist()：这里要的是带说明的行（逐条讲清卡在哪），
    # checklist() 只回布尔投影。判定聚合仍走同一套阈值，见 entry.rows 文档。
    conds, warns_raw = _split_rows(entry.rows(cand, d["window"]))
    fails = [f"{_human_cond(n)}：{note}" for n, ok, note in conds if not ok]
    warns = [note for _, _, note in warns_raw if "⚠" in note]
    if fails:
        if short:
            return f"{cand.name} 卡在「{fails[0].split('：')[0]}」"
        head = "**今天不出手（候选 " + cand.name + "）**\n" + "\n".join(f"- [✗] {f}" for f in fails)
    else:
        # 硬条件全过却没有记录 → 只可能是档位为「不参与」（分歧降级 + 新插队）
        try:
            pt = entry.tier_of(cand, d["window"])
            why = f"{cand.name} 是{pt.tier}，当前环境下不进观察名单（{pt.reason}）"
        except Exception as exc:  # noqa: BLE001 —— 报告不能因档位算不出来而崩
            log.warning("档位计算失败（%s，降级为文案）：%s", cand.code, exc)
            why = f"{cand.name} 硬条件通过，但当前环境下不进观察名单（档位=不参与）"
        return why if short else f"**今天不出手**：{why}"
    return head + "".join(f"\n- [{w}]" for w in warns)


def _caliber(trade_date: date) -> dict:
    """口径元数据：统计边界显式声明 + 双源计数（自算 vs 东财池）。"""
    pool = query_df(
        "SELECT count(*) FILTER (WHERE pool_type='ZT') zt,"
        " count(*) FILTER (WHERE pool_type='DT') dt"
        " FROM limit_pool_em WHERE date = %s",
        (trade_date,),
    ).iloc[0]
    mine = query_df(
        "SELECT count(*) FILTER (WHERE is_limit_up) zt,"
        " count(*) FILTER (WHERE is_limit_down) dt FROM derived_bar"
        " WHERE date = %s",
        (trade_date,),
    ).iloc[0]
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
    """A2-7：报告可用性非 OK → 入 alert（WARN 级，可确认追踪）。

    ★2026-09-29 加新鲜度闸：只对**最近 _ALERT_WITHIN_DAYS 个交易日内**的报告告警。
    参照系取 derived_bar 最近日（而非 today_sh）——回补历史日时，参照系就是被回补
    到的最新那天，判定与「当下」一致；非交易日不误判。
    报告正文的可用性标记不受影响，仍然照常渲染。
    """
    u = d.get("usability") or _usability(d)
    if u["state"] == "OK":
        return
    try:
        from emotion_core.utils.db import query_df

        newest = query_df("SELECT max(date) d FROM derived_bar")["d"].iloc[0]
        if newest is not None and trade_date not in _recent_days_upto(newest, _ALERT_WITHIN_DAYS):
            log.info(
                "%s 报告可用性 %s：超出 %d 交易日新鲜度窗，不入告警",
                trade_date,
                u["state"],
                _ALERT_WITHIN_DAYS,
            )
            return
        from emotion_core.algorithms import alerts

        alerts.record_dedup("WARN", "review", f"{trade_date} 报告可用性 {u['state']}：{u['note']}")
    except Exception:  # noqa: BLE001
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

_SECTIONS = (
    _sec_overview,
    _sec_emotion,
    _sec_ladder,
    _sec_theme,
    _sec_elimination,
    _sec_promotion,
    _sec_advice,
    _sec_watchlist,
    _sec_dragon_env,
    _sec_counter,
    _sec_next_check,
    _sec_quality,
    _sec_trend,
)


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
                    f"必需段 {fn.__name__} 渲染失败：{exc}——拒绝发布，请先修复数据"
                ) from exc
            log.warning("段 %s 渲染失败：%s", fn.__name__, exc)
            parts.append(f"> ⚠ 本段渲染失败（{exc}），数据可能有缺口，请人工核对该段依赖的表")
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
    execute(
        "INSERT INTO review_report (date, markdown) VALUES (%s,%s)"
        " ON CONFLICT (date) DO UPDATE SET markdown=EXCLUDED.markdown,"
        " created_at=now()",
        (trade_date, md),
    )
    notify.push(md, trade_date)
    log.info("复盘报告 %s -> %s + %s + DB", trade_date, out, out_json)
    return str(out)
