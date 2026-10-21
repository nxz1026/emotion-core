"""变体搜索：单点爬坡 + hold-out 验证 + 七道反过拟合门禁 → 提案（P2）。

设计原则（docs/20 §5）：

- **选参只看过去**：`days[:-HOLD_DAYS]` 是选参区间，最后 `HOLD_DAYS`（默认 120 个
  交易日）**只用于验证**，绝不参与排序。这样"最近三个月发生了什么"不会泄漏到参数里。
- **按 LCB 排序，不按均值**：均值冠军往往是小样本噪声，`mean − 1.645σ/√n` 会把它压下去。
- **提案必须过七道门禁**，任一条不过就只记录、不提案：n≥30、hold-out LCB>0、
  hold-out 优于基线、t≥3（多重比较）、±1 邻域稳定、两段 hold-out 都为正、
  选参区间 n≥60。
- **不自动晋升**：只写 `lab.lab_promotion`（status=pending），等人工在 :8091 页拍板。

诚实的预期：B 轮已经证明这一域内没有正期望入场（ADR 0002 D1），所以这一轮最可能
的产出是「没有任何变体过门禁」。那也是一个有效结论——它证明搜索没在噪声里挖假信号。
如果它挖出了过门禁的变体，才需要人工怀疑门禁是否太松。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from emotion_core.lab import db as labdb
from emotion_core.lab import metrics, panel, replay, score
from emotion_core.lab.rules import BASELINE, LabRules, variants

log = logging.getLogger(__name__)

HOLD_DAYS = 120          # 最后 120 个交易日：只验证，不选参
MIN_SEL_N = 60           # 选参区间最少信号数
MIN_HOLD_N = 30          # 验证区间最少信号数（n<30 不给分）
MIN_T = 3.0              # 多重比较：hold-out t 值下限（对应 α≈0.001 单边）
RETIRE_GAP = 1.0         # 淘汰门限：hold-out LCB 比基线低 1pp 以上 → retired（可复活）
#: ±1 邻域的步长（只对连续型旋钮；0/1 型开关不造邻域，邻域不稳定对它们无意义）
_NEIGHBOR_STEP: dict[str, float] = {
    "min_leader_days": 1.0, "climax_zt": 10.0, "ebb_zt_perf": 0.5,
    "ferment_zt_perf": 0.5, "ice_zt_max": 10.0, "diverge_min_turnover": 0.5,
}

_INSERT_VARIANT = """
INSERT INTO lab.lab_variant (variant_id, params, status, note)
VALUES (%s, %s::jsonb, %s, %s)
ON CONFLICT (variant_id) DO UPDATE SET
    params = EXCLUDED.params, status = EXCLUDED.status, note = EXCLUDED.note
"""

_INSERT_SCORE = """
INSERT INTO lab.lab_score (
    variant_id, window_start, window_end, n, fill_rate, expectancy, median,
    win_rate, lcb, worst, sub_worst, trials, window_name, detail, updated_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
ON CONFLICT (variant_id, window_start, window_end) DO UPDATE SET
    n = EXCLUDED.n, fill_rate = EXCLUDED.fill_rate, expectancy = EXCLUDED.expectancy,
    median = EXCLUDED.median, win_rate = EXCLUDED.win_rate, lcb = EXCLUDED.lcb,
    worst = EXCLUDED.worst, sub_worst = EXCLUDED.sub_worst, trials = EXCLUDED.trials,
    window_name = EXCLUDED.window_name, detail = EXCLUDED.detail, updated_at = now()
"""

_INSERT_PROMO = """
INSERT INTO lab.lab_promotion (proposal, actions, evidence, status)
VALUES (%s, %s::jsonb, %s::jsonb, 'pending')
"""


def variant_id(rules: LabRules) -> str:
    """参数指纹 → 稳定 id（同参同 id，改一个值就换 id）。

    基线变体直接复用 `score.BASELINE_ID`（`baseline_mainline`），否则同一个策略会在
    页面上出现两条（一条来自 P1 打分、一条来自搜索），排行榜会自己跟自己对打。
    """
    if rules == BASELINE:
        return score.BASELINE_ID
    blob = json.dumps(rules.as_dict(), sort_keys=True, default=str)
    return "v_" + hashlib.sha256(blob.encode()).hexdigest()[:10]


def _t(rets: list[float]) -> float:
    """单样本 t 值（mean / (σ/√n)）；n<2 或 σ=0 时返回 0。"""
    n = len(rets)
    if n < 2:
        return 0.0
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    if var <= 0:
        return 0.0
    return mean / ((var ** 0.5) / (n ** 0.5))


@dataclass(frozen=True)
class Window:
    name: str
    start: date
    end: date
    rets: list[float]

    @property
    def stats(self) -> dict[str, Any]:
        return metrics.stats(self.rets)


def _split(per: pd.DataFrame, sel_days: list[date],
           hold_days: list[date]) -> tuple[Window, Window]:
    sel_set, hold_set = set(sel_days), set(hold_days)

    def pick(keys: set[date]) -> list[float]:
        sub = per[per["d0"].isin(keys)]
        return list(metrics.per_signal(sub.to_dict("records")))

    return (
        Window("sel", sel_days[0], sel_days[-1], pick(sel_set)),
        Window("hold", hold_days[0], hold_days[-1], pick(hold_set)),
    )


def _neighbors(rules: LabRules) -> list[LabRules]:
    """±1 邻域（只对数值型旋钮；0/1 型不造邻域）。"""
    out: list[LabRules] = []
    for name, step in _NEIGHBOR_STEP.items():
        cur = getattr(rules, name)
        for delta in (-step, step):
            try:
                out.append(rules.replace(**{name: round(cur + delta, 4)}))
            except (TypeError, ValueError):      # pragma: no cover - 防御
                continue
    return out


def evaluate(prep: replay.Prepared, pan: pd.DataFrame, rules: LabRules,
             *, as_of: date, sel_days: list[date],
             hold_days: list[date]) -> dict[str, Any]:
    """回放一个变体 → 选参/验证两段统计。"""
    sig = replay.signals(prep, rules, as_of=as_of,
                         start=sel_days[0], end=hold_days[-1])
    per = score.score_signals(sig, pan)
    if per.empty:
        sel = Window("sel", sel_days[0], sel_days[-1], [])
        hold = Window("hold", hold_days[0], hold_days[-1], [])
    else:
        sel, hold = _split(per, sel_days, hold_days)
    return {
        "variant_id": variant_id(rules),
        "rules": rules,
        "signals": len(sig),
        "sel": sel,
        "hold": hold,
        "hold_t": _t(hold.rets),
    }


def _gates(ev: dict[str, Any], base: dict[str, Any], neighbors: list[dict[str, Any]]
           ) -> tuple[bool, list[str]]:
    """七道门禁。返回 (是否全过, 未过原因列表)。"""
    reasons: list[str] = []
    sel, hold = ev["sel"], ev["hold"]
    if sel.stats["n"] < MIN_SEL_N:
        reasons.append(f"选参区间样本不足（n={sel.stats['n']}<{MIN_SEL_N}）")
    if hold.stats["n"] < MIN_HOLD_N:
        reasons.append(f"验证区间样本不足（n={hold.stats['n']}<{MIN_HOLD_N}）")
    if hold.stats["lcb"] <= 0:
        reasons.append(f"hold-out LCB≤0（{hold.stats['lcb']}%）")
    if hold.stats["mean"] <= base["hold"].stats["mean"]:
        reasons.append(
            f"hold-out 未优于基线（{hold.stats['mean']}% vs {base['hold'].stats['mean']}%）")
    if ev["hold_t"] < MIN_T:
        reasons.append(f"t={round(ev['hold_t'], 2)}<{MIN_T}（多重比较）")
    half = max(1, len(hold.rets) // 2)
    for i, part in enumerate((hold.rets[:half], hold.rets[half:]), 1):
        if part and sum(part) / len(part) <= 0:
            reasons.append(f"hold-out 第 {i} 段均值≤0")
    weak = [n["variant_id"] for n in neighbors
            if n["hold"].stats["n"] >= MIN_HOLD_N and n["hold"].stats["mean"] <= 0]
    if weak:
        reasons.append(f"邻域不稳定（{len(weak)} 个 ±1 邻居 hold-out≤0）")
    return (not reasons), reasons


def run(start: date, end: date, *, as_of: date, persist: bool = False,
        limit: int | None = None, hold_days: int = HOLD_DAYS) -> dict[str, Any]:
    """跑全部单点变体：选参排序（sel LCB）+ hold-out 验证 + 门禁 + 提案。"""
    pan = panel.load()
    prep = replay.prepare(pan[pan["d0"] >= start])
    days = [d for d in prep.days if start <= d <= end]
    if len(days) <= hold_days + MIN_SEL_N:
        raise SystemExit(f"样本太短：{len(days)} 天 ≤ hold {hold_days} 天")
    sel_days, hold_days_list = days[:-hold_days], days[-hold_days:]
    cands = variants(limit)
    log.info("变体 %d 个；选参 %s~%s；验证 %s~%s",
             len(cands), sel_days[0], sel_days[-1], hold_days_list[0], hold_days_list[-1])

    evs = [evaluate(prep, pan, r, as_of=as_of, sel_days=sel_days,
                    hold_days=hold_days_list) for r in cands]
    base = next(e for e in evs if e["variant_id"] == variant_id(BASELINE))
    ranked = sorted(evs, key=lambda e: e["sel"].stats["lcb"], reverse=True)

    # 邻域：只为「sel LCB 排名进入前 20%」的变体算（省掉几十次回放）
    top_n = max(1, len(ranked) // 5)
    top_ids = {e["variant_id"] for e in ranked[:top_n]}
    neigh: dict[str, list[dict[str, Any]]] = {}
    for e in evs:
        if e["variant_id"] not in top_ids:
            continue
        neigh[e["variant_id"]] = [
            evaluate(prep, pan, nr, as_of=as_of, sel_days=sel_days,
                     hold_days=hold_days_list)
            for nr in _neighbors(e["rules"])]

    results: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    for rank, e in enumerate(ranked):
        ok, reasons = _gates(e, base, neigh.get(e["variant_id"], []))
        if e["variant_id"] not in top_ids and e["variant_id"] != base["variant_id"]:
            reasons = reasons + ["选参区间 LCB 未进前 20%"]
            ok = False
        row = {
            "rank": rank + 1,
            "variant_id": e["variant_id"],
            "params": e["rules"].as_dict(),
            "signals": e["signals"],
            "sel": e["sel"].stats,
            "hold": e["hold"].stats,
            "hold_t": round(e["hold_t"], 2),
            "passes": ok,
            "reasons": reasons,
        }
        results.append(row)
        if ok:
            proposals.append(row)

    if persist:
        _persist(results, proposals, base, (sel_days, hold_days_list),
                 trials=len(evs))
    return {"baseline": {"variant_id": base["variant_id"],
                         "sel": base["sel"].stats, "hold": base["hold"].stats,
                         "hold_t": round(base["hold_t"], 2)},
            "vars": len(evs), "results": results, "proposals": proposals}


def _persist(results: list[dict[str, Any]],
             proposals: list[dict[str, Any]], base: dict[str, Any],
             windows: tuple[list[date], list[date]], *, trials: int) -> None:
    """只写 lab schema：变体登记 + 两段评分 + 待批提案。"""
    sel_days, hold_days = windows
    base_lcb = base["hold"].stats["lcb"]
    conn = labdb.connect_lab()
    try:
        for row in results:
            if row["variant_id"] == base["variant_id"]:
                status, note = "active", "基线"
            elif row["passes"]:
                status, note = "pending", "过七道门禁待批"
            elif (row["hold"]["n"] >= MIN_HOLD_N
                  and base_lcb is not None
                  and row["hold"]["lcb"] is not None
                  and row["hold"]["lcb"] <= base_lcb - RETIRE_GAP):
                status, note = "retired", "hold-out LCB 比基线低 1pp 以上（可复活）"
            else:
                status, note = "draft", "；".join(row["reasons"][:2])
            labdb.execute(conn, _INSERT_VARIANT,
                          (row["variant_id"],
                           json.dumps(row["params"], ensure_ascii=False),
                           status, note))
            for w, (w0, w1) in (("sel", (sel_days[0], sel_days[-1])),
                                ("hold", (hold_days[0], hold_days[-1]))):
                st = row[w]
                labdb.execute(conn, _INSERT_SCORE, (
                    row["variant_id"], w0, w1,
                    st["n"], None, st["mean"], st["median"], st["win_rate"],
                    st["lcb"], st["worst"], None, trials, w,
                    json.dumps({"reliable": st["reliable"]}, ensure_ascii=False),
                ))
        for p in proposals:
            changed = {k: v for k, v in p["params"].items()
                       if v != getattr(BASELINE, k)}
            labdb.execute(conn, _INSERT_PROMO, (
                f"[Lab] {p['variant_id']}：{len(changed)} 个参数改动，"
                f"hold-out 每信号 {p['hold']['mean']}% vs 基线 "
                f"{base['hold'].stats['mean']}%",
                json.dumps({"variant_id": p["variant_id"], "changes": changed},
                           ensure_ascii=False),
                json.dumps({"sel": p["sel"], "hold": p["hold"],
                            "hold_t": p["hold_t"], "trials": trials},
                           ensure_ascii=False),
            ))
        conn.commit()
        log.info("已写入 lab：变体 %d、提案 %d", len(results), len(proposals))
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lab 变体搜索（hold-out + 七门禁）")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=str(date.today()))
    ap.add_argument("--as-of", default=str(date.today()))
    ap.add_argument("--hold", type=int, default=HOLD_DAYS)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--persist", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    out = run(date.fromisoformat(args.start), date.fromisoformat(args.end),
              as_of=date.fromisoformat(args.as_of), persist=args.persist,
              limit=args.limit, hold_days=args.hold)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0
    b = out["baseline"]
    print(f"基线 {b['variant_id']}：选参 {b['sel']['n']} 条 {b['sel']['mean']}% / "
          f"hold-out {b['hold']['n']} 条 {b['hold']['mean']}% LCB={b['hold']['lcb']}% "
          f"t={b['hold_t']}")
    print(f"变体 {out['vars']} 个，过门禁 {len(out['proposals'])} 个")
    for r in out["results"][:10]:
        print(f"  #{r['rank']:2d} {r['variant_id']} sel={r['sel']['mean']}% "
              f"hold={r['hold']['mean']}% t={r['hold_t']} "
              f"{'✅' if r['passes'] else '· ' + r['reasons'][0]}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
