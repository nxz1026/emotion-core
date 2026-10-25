"""内存内信号复刻 + 变体回放（P2）。

目标不是"再写一套 entry"，而是**只换参数、判定仍走主线函数**：
- 条件判定调 `entry._rows(cand, ctx, entry._CONDITIONS)`（含 W1 警告行）；
- 聚合调 `entry.passed_of`（`Checklist.passed` 语义）；
- 身份档调 `entry.c3_tier`，进不进名单调 `entry.position_tier` × 变体倍率。

Lab 自己只补两件主线从数据库读的东西：**候选池的行/最高组/昨日竞争实况**（从
面板按日算）与 **相位序列**（`state.classify_series` 用变体参数重跑）。

口径等价性由 `tests/unit/test_lab_replay.py::test_baseline_matches_signal_table`
钉住：默认变体必须与 `signal` 表里 `source='replay'` 的 BUY 集合逐条相同。

⚠ 时点纪律（跨三轮踩过的同一个坑）：本模块只用**决策时可得**的输入。
`is_limit_up` / `is_exchange` / `cont_days` 都来自 T 日收盘后的派生表，作为
"今天谁最强"的判据没问题；但**绝不能**用它们当"买到了/成交了"的证据——
成交只能由 T+1 的 `high >= 挂单价` 判（见 `lab/metrics.py`）。
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd

from emotion_core.algorithms import entry, state
from emotion_core.lab import panel as labpanel
from emotion_core.lab.rules import BASELINE, LabRules

log = logging.getLogger(__name__)


class Cand:
    """`entry` 条件函数需要的候选视图（只读、无 IO）。

    主线传的是 `domain.LadderDay`；这里只需要字段名相同，条件函数用
    `getattr`/`ctx` 取值，所以轻量替身足够，不必构造整个领域对象。
    """

    __slots__ = ("date", "code", "cont_days", "is_exchange", "is_suspended")

    def __init__(
        self,
        d: date,
        code: str,
        cont_days: int,
        is_exchange: bool = True,
        is_suspended: bool = False,
    ) -> None:
        self.date = d
        self.code = code
        self.cont_days = int(cont_days)
        self.is_exchange = bool(is_exchange)
        self.is_suspended = bool(is_suspended)


@dataclass(frozen=True)
class Prepared:
    """面板的一次性预处理（跨变体复用；变体只重跑相位与拦截）。"""

    days: list[date]
    rows: dict[date, list[dict[str, Any]]]
    tops: dict[date, frozenset[str]]
    exchange: dict[date, frozenset[str]]
    diverge: dict[date, bool]
    metrics: list[state.DayMetrics]


def _num(v: Any) -> float | None:
    """NaN/None/NaT → None（F6：缺数不补零）。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return float(v)


def prepare(panel: pd.DataFrame) -> Prepared:
    """按日切好面板：当日行、最高组、换手板集合、分歧标记、状态机指标。

    最高组口径 = `ladder.top_group`：换手板（非停牌）里连板数最大的那些。
    """
    panel = panel.sort_values(["d0", "code"])
    days = sorted({d for d in panel["d0"]})
    rows: dict[date, list[dict[str, Any]]] = {}
    tops: dict[date, frozenset[str]] = {}
    exchange: dict[date, frozenset[str]] = {}
    diverge: dict[date, bool] = {}
    metrics: list[state.DayMetrics] = []
    for d, g in panel.groupby("d0", sort=True):
        recs = g.to_dict("records")
        rows[d] = recs
        ex = [r for r in recs if r["is_exchange"] and not r["is_suspended"]]
        ex_codes = frozenset(r["code"] for r in recs if r["is_exchange"] and not r["is_suspended"])
        if ex:
            h = max(int(r["cont_days"]) for r in ex)
            tops[d] = frozenset(r["code"] for r in ex if int(r["cont_days"]) == h)
        else:
            tops[d] = frozenset()
        exchange[d] = ex_codes
        first = recs[0]
        diverge[d] = bool(first["diverge"])
        metrics.append(
            state.DayMetrics(
                date=d,
                limit_up_count=int(first["limit_up_count"]),
                bomb_rate=_num(first["bomb_rate"]),
                zt_performance=_num(first["zt_performance"]),
                max_limit_days=int(first["max_limit_days"]),
                limit_down_count=int(first["limit_down_count"]),
                top_amplitude=_num(first["top_amplitude"]),
                top_broke=None
                if first["top_broke"] is None or pd.isna(first["top_broke"])
                else bool(first["top_broke"]),
                bomb_threshold=float(first["bomb_threshold"]),
                has_candidate=bool(first["has_candidate"]),
                tradable_max_days=int(first["tradable_max_days"]),
                oneword_ratio=_num(first["oneword_ratio"]),
            )
        )
    return Prepared(
        days=days, rows=rows, tops=tops, exchange=exchange, diverge=diverge, metrics=metrics
    )


def phases(prep: Prepared, rules: LabRules) -> dict[date, state.EmotionState]:
    """用变体参数重跑 675 天的相位序列（纯内存，不写库）。"""
    series = state.classify_series(list(prep.metrics), rules.state_rules())
    return {s.date: s for s in series}


def _ctx(
    day: date,
    window: str,
    rules: LabRules,
    prep: Prepared,
    cand_row: dict[str, Any],
    prev_top: frozenset[str],
    today_ex: frozenset[str],
    as_of: date,
) -> tuple[Cand, dict[str, Any]]:
    cand = Cand(day, cand_row["code"], int(cand_row["cont_days"]))
    survivors = prev_top & today_ex
    ctx: dict[str, Any] = {
        "sole": cand,
        "window": window,
        "min_days": rules.min_leader_days,
        "diverge_min": rules.diverge_min_turnover,
        "y_comp": (len(prev_top), len(survivors)),
        "rows": [
            Cand(
                day, r["code"], int(r["cont_days"]), bool(r["is_exchange"]), bool(r["is_suspended"])
            )
            for r in prep.rows[day]
        ],
        "y_surv": (prev_top, survivors),
        "c5_verifiable": day >= as_of - timedelta(days=entry.POOL_RECENT_DAYS),
        "name": cand_row.get("stock_name") or "",
        "turnover_rate": _num(cand_row.get("turnover_rate")),
        "bomb_times": (
            None
            if cand_row.get("bomb_times") is None or pd.isna(cand_row.get("bomb_times"))
            else int(cand_row["bomb_times"])
        ),
        "close": _num(cand_row.get("close")),
    }
    return cand, ctx


def signals(
    prep: Prepared,
    rules: LabRules,
    *,
    as_of: date,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """回放一个变体，返回它当天的观察名单成员（= 主线的 BUY 集合）。

    与 `entry.check_signal` 同序：窗口 NONE → 跳过；最高组必须唯一；c4 门槛；
    五条件全过（c3 不否决）；档位非「不参与」。**不产买点/仓位**（A 止血口径）。
    """
    st_map = phases(prep, rules)
    out: list[dict[str, Any]] = []
    for i, day in enumerate(prep.days):
        if start is not None and day < start:
            continue
        if end is not None and day > end:
            continue
        st = st_map[day]
        if st.buy_window == "NONE":
            continue
        if i == 0:
            continue  # 无昨日梯队 → 竞争实况不可算（主线同此）
        tg = prep.tops[day]
        if len(tg) != 1:
            continue
        code = next(iter(tg))
        cand_row = next((r for r in prep.rows[day] if r["code"] == code), None)
        if cand_row is None or int(cand_row["cont_days"]) < rules.min_leader_days:
            continue
        prev_top = prep.tops[prep.days[i - 1]]
        cand, ctx = _ctx(
            day, st.buy_window, rules, prep, cand_row, prev_top, prep.exchange[day], as_of
        )
        rows = entry._rows(cand, ctx, entry._CONDITIONS)
        conds = [(ok, label) for label, ok, _ in rows if not label.startswith("W")]
        if not all(ok is True for ok, _ in conds if ok is not None):
            continue
        tier = entry.c3_tier(cand, ctx)
        if not rules.is_open(st.phase, prep.diverge[day], tier):
            continue
        out.append(
            {
                "d0": day,
                "code": code,
                "phase": st.phase,
                "buy_window": st.buy_window,
                "tier": tier,
                "cont_days": int(cand_row["cont_days"]),
                "close": _num(cand_row.get("close")),
            }
        )
    df = pd.DataFrame(
        out, columns=["d0", "code", "phase", "buy_window", "tier", "cont_days", "close"]
    )
    log.info("变体回放：%d 条观察记录（%s~%s）", len(df), start, end)
    return df


def main(argv: list[str] | None = None) -> int:
    """命令行：复刻基线并与 `signal` 表对照（只读）。"""
    ap = argparse.ArgumentParser(description="Lab 变体回放（默认只读对照）")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=str(date.today()))
    ap.add_argument(
        "--as-of", default=str(date.today()), help="c5 可核验基准日（主线回放当天 = 今天）"
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    as_of = date.fromisoformat(args.as_of)
    prep = prepare(labpanel.load())
    sig = signals(prep, BASELINE, as_of=as_of, start=start, end=end)
    print(f"基线变体：{len(sig)} 条观察记录")
    if not args.json:
        from emotion_core.lab import db as labdb

        live = labdb.query_df(
            "SELECT confirm_date AS d0, code FROM signal"
            " WHERE action = 'BUY' AND source = 'replay'"
            "   AND confirm_date BETWEEN %s AND %s",
            (start, end),
        )
        live["d0"] = pd.to_datetime(live["d0"]).dt.date
        a = {(r.d0, r.code) for r in sig.itertuples()}
        b = {(r.d0, r.code) for r in live.itertuples()}
        print(f"signal 表 replay BUY：{len(b)} 条")
        print(f"仅在 lab：{sorted(a - b)[:10]}（{len(a - b)} 条）")
        print(f"仅在 signal：{sorted(b - a)[:10]}（{len(b - a)} 条）")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
