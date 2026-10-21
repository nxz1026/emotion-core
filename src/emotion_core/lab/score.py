"""打分：把主线当前规则的历史表现算成可对比的数字，写进 `lab.lab_score`。

P1 只有一条变体：`baseline_mainline`（= 主线现行规则产出的观察名单）。
它的作用不是"好看"，而是给后面所有变体一个**同口径的对照基线**：
同样的成交假设（口径B 触及即成交）、同样的费、同样的切片方式。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from typing import Any

import pandas as pd

from emotion_core.lab import db as labdb
from emotion_core.lab import metrics, panel

log = logging.getLogger(__name__)

BASELINE_ID = "baseline_mainline"

_SIG_SQL = """
SELECT confirm_date AS d0, code, action, buy_window, source
FROM signal
WHERE confirm_date BETWEEN %s AND %s AND action = %s
ORDER BY confirm_date, code
"""

_INSERT_SCORE = """
INSERT INTO lab.lab_score (
    variant_id, window_start, window_end, n, fill_rate, expectancy, median,
    win_rate, lcb, worst, sub_worst, trials, detail, updated_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
ON CONFLICT (variant_id, window_start, window_end) DO UPDATE SET
    n = EXCLUDED.n, fill_rate = EXCLUDED.fill_rate, expectancy = EXCLUDED.expectancy,
    median = EXCLUDED.median, win_rate = EXCLUDED.win_rate, lcb = EXCLUDED.lcb,
    worst = EXCLUDED.worst, sub_worst = EXCLUDED.sub_worst, trials = EXCLUDED.trials,
    detail = EXCLUDED.detail, updated_at = now()
"""

_INSERT_VARIANT = """
INSERT INTO lab.lab_variant (variant_id, params, status, note)
VALUES (%s, %s::jsonb, %s, %s)
ON CONFLICT (variant_id) DO UPDATE SET params = EXCLUDED.params, note = EXCLUDED.note
"""


def score_signals(sigs: pd.DataFrame, pan: pd.DataFrame) -> pd.DataFrame:
    """信号 × 面板 → 逐条口径B 结果。"""
    merged = sigs.merge(
        pan,
        left_on=["d0", "code"],
        right_on=["d0", "code"],
        how="left",
        suffixes=("", "_pan"),
    )
    out: list[dict[str, Any]] = []
    for r in merged.itertuples(index=False):
        res = metrics.caliber_b_ret(
            code=str(r.code),
            base_close=_f(r.close),
            t1_high=_f(r.n1_high),
            t1_close=_f(r.n1_close),
            t1_volume=_f(r.n1_volume),
            t2_open=_f(r.n2_open),
            t2_close=_f(r.n2_close),
            t3_open=_f(r.n3_open),
        )
        out.append(
            {
                "d0": r.d0,
                "code": r.code,
                "phase": getattr(r, "phase", None),
                "buy_window": getattr(r, "buy_window", None),
                "cont_days": getattr(r, "cont_days", None),
                **res,
            }
        )
    return pd.DataFrame(out)


def summarize(df: pd.DataFrame, window: tuple[date, date]) -> dict[str, Any]:
    """整体 + 切片。未成交按 0 计入「每信号」，另列成交后统计。"""
    per = metrics.per_signal(df.to_dict("records"))
    filled = metrics.per_signal(df.to_dict("records"), count_unfilled_as_zero=False)
    base = metrics.stats(per)
    filled_stats = metrics.stats(filled)
    fill_rate = round(100.0 * len(filled) / base["n"], 1) if base["n"] else None

    slices: dict[str, dict[str, Any]] = {}
    if "phase" in df.columns:
        for phase, sub in df.groupby(df["phase"].fillna("—")):
            slices[f"phase:{phase}"] = metrics.stats(
                metrics.per_signal(sub.to_dict("records"))
            )
    if "d0" in df.columns and len(df):
        years = pd.to_datetime(df["d0"]).dt.year
        for year, sub in df.groupby(years):
            slices[f"year:{year}"] = metrics.stats(
                metrics.per_signal(sub.to_dict("records"))
            )
    if "cont_days" in df.columns:
        for days, sub in df.groupby(df["cont_days"].fillna(-1)):
            key = f"cont:{int(days) if days >= 0 else '—'}"
            slices[key] = metrics.stats(metrics.per_signal(sub.to_dict("records")))

    return {
        "window_start": window[0],
        "window_end": window[1],
        "n": base["n"],
        "fill_rate": fill_rate,
        "expectancy": base["mean"],
        "median": base["median"],
        "win_rate": base["win_rate"],
        "lcb": base["lcb"],
        "worst": base["worst"],
        "sub_worst": metrics.worst_slice(slices),
        "detail": {"all": base, "filled": filled_stats, "slices": slices},
    }


def run(start: date, end: date, *, persist: bool = False,
        action: str = "BUY", variant_id: str = BASELINE_ID) -> dict[str, Any]:
    pan = panel.load()
    sigs = labdb.query_df(_SIG_SQL, (start, end, action))
    log.info("信号 %d 条（%s, %s~%s）", len(sigs), action, start, end)
    scored = score_signals(sigs, pan)
    out = summarize(scored, (start, end))

    if persist:
        conn = labdb.connect_lab()
        try:
            labdb.execute(
                conn, _INSERT_VARIANT,
                (variant_id, json.dumps({"source": "mainline", "action": action}),
                 "active", "主线现行规则（P1 对照基线）"),
            )
            labdb.execute(
                conn, _INSERT_SCORE,
                (
                    variant_id, out["window_start"], out["window_end"], out["n"],
                    out["fill_rate"], out["expectancy"], out["median"], out["win_rate"],
                    out["lcb"], out["worst"], out["sub_worst"], 1,
                    json.dumps(out["detail"], ensure_ascii=False, default=str),
                ),
            )
            conn.commit()
            log.info("已写入 lab.lab_score（%s）", variant_id)
        finally:
            conn.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lab 打分（P1 只有主线基线）")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=str(date.today()))
    ap.add_argument("--action", default="BUY")
    ap.add_argument("--persist", action="store_true", help="写 lab.lab_score")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    out = run(
        date.fromisoformat(args.start), date.fromisoformat(args.end),
        persist=args.persist, action=args.action,
    )
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0
    allst = out["detail"]["all"]
    filled = out["detail"]["filled"]
    print(
        f"n={out['n']} 成交率={out['fill_rate']}% 每信号={out['expectancy']}% "
        f"胜率={out['win_rate']}% LCB={out['lcb']}% 最差={out['worst']}%"
    )
    print(
        f"成交后：n={filled['n']} 均值={filled['mean']}% 中位={filled['median']}% "
        f"胜率={filled['win_rate']}%"
    )
    for key, sl in sorted(out["detail"]["slices"].items()):
        flag = "" if sl["reliable"] else "（样本不足）"
        print(f"  {key:16s} n={sl['n']:4d} 均值={sl['mean']}% 胜率={sl['win_rate']}%{flag}")
    return 0


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
