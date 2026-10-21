"""B 轮探针：盘后可执行边缘搜索（只读）。

A 止血把「打板买（T+1 涨停价）」证伪后，盘后系统在 T+1 只有两类订单可表达：

  1. 限价「回踩买」：T+1 挂 buy limit = T 日收盘 × k（k<1），成交才建仓
  2. 市价「开盘买」：T+1 开盘价成交（已证负期望，作为对照）

本探针只做 1，并严格执行「成交假设必须能触发」：

  - 成交判据：T+1 low ≤ 挂单价 → 成交；成交价 = min(挂单价, T+1 开盘价)
    （若开盘就低于挂单价，以更优的开盘价成交，这是限价单的真实行为）
  - 停牌/非交易（derived_bar.is_exchange 为 false，或缺当日 K 线）→ 不成交
  - 未成交 → 收益记 0（不参与），并单列「参与率」
  - 卖点：T+2 开盘（顺延规则同 A：T+2 开盘跌停封死 → T+3 开盘强制了结）
    A 股 T+1 交收，当日买入不能当日卖，所以最早的卖点就是 T+2 开盘
  - 费后双边 0.15%

候选宇宙沿用 A 轮（ladder_day is_sole_top 且 is_exchange 且 cont_days>=4），
另跑一个放宽版（cont_days>=3 的 is_top 组）作对照。

输出：_audit/b_pullback.csv（逐信号）+ 控制台分层统计。
"""
from __future__ import annotations

import pandas as pd

from emotion_core.utils.db import query_df

FEE = 0.0015            # 双边合计（佣金+印花+滑点），与 A 轮一致
K_GRID = (1.00, 0.99, 0.98, 0.97, 0.96, 0.95, 0.93, 0.90)

CAND_SQL = """
SELECT l.date, l.code, l.cont_days, l.is_exchange,
       l.y_top_group_count::int g, l.y_top_survivor_count::int s,
       ms.phase, ms.buy_window, ms.diverge,
       CASE WHEN y.code IS NULL THEN 0 ELSE 1 END in_y_top
FROM ladder_day l
JOIN market_stat ms ON ms.date = l.date
LEFT JOIN LATERAL (
  SELECT l2.code FROM ladder_day l2
  WHERE l2.date = (SELECT MAX(d.date) FROM ladder_day d WHERE d.date < l.date) AND l2.is_top
) y ON y.code = l.code
WHERE l.is_sole_top AND l.is_exchange AND l.cont_days >= 4
ORDER BY l.date
"""


def tier_of(r) -> str:
    if r.g >= 2 and r.s == 1 and r.in_y_top == 1:
        return "严格胜出"
    if r.in_y_top == 1:
        return "身位延续"
    return "新插队"


c = query_df(CAND_SQL)
c["date"] = pd.to_datetime(c.date)
c["code"] = c.code.astype(str).str.zfill(6)
print(f"候选宇宙 {len(c)} 天  {c.date.min().date()} ~ {c.date.max().date()}", flush=True)

lo = c.date.min() - pd.Timedelta(days=10)
hi = c.date.max() + pd.Timedelta(days=40)
codes = tuple(sorted(c.code.unique()))
px = query_df(
    "SELECT code, date, open, high, low, close FROM daily_bar "
    "WHERE date >= %s AND date <= %s AND code = ANY(%s)", (lo, hi, list(codes)))
zt = query_df(
    "SELECT code, date, is_exchange, is_one_word, is_limit_up FROM derived_bar "
    "WHERE date >= %s AND date <= %s AND code = ANY(%s)", (lo, hi, list(codes)))
for df in (px, zt):
    df["date"] = pd.to_datetime(df.date)
    df["code"] = df.code.astype(str).str.zfill(6)
px = px.merge(zt, on=["code", "date"], how="left")
for k in ("open", "high", "low", "close"):
    px[k] = px[k].astype(float)
px = px.sort_values(["code", "date"])
print(f"行情行数 {len(px)}", flush=True)

by_code = {k: v.reset_index(drop=True) for k, v in px.groupby("code")}


def stunned_open(r, prev_close: float) -> bool:
    """开盘跌停且全天未开板 → 卖不掉。"""
    o, low = float(r["open"]), float(r["low"])
    return bool(o <= prev_close * 0.902 and low >= o * 0.999)


rows: list[dict] = []
for r in c.itertuples():
    sub = by_code.get(r.code)
    if sub is None:
        continue
    hit = sub.index[sub["date"] == r.date]
    if not len(hit):
        continue
    i0 = int(hit[0])
    if i0 + 2 >= len(sub):
        continue                      # 需要 T+1 与 T+2
    t0, t1, t2 = sub.iloc[i0], sub.iloc[i0 + 1], sub.iloc[i0 + 2]
    t3 = sub.iloc[i0 + 3] if i0 + 3 < len(sub) else None
    c0 = float(t0["close"])
    if c0 <= 0:
        continue
    exch = t1["is_exchange"]
    tradeable = bool(exch) if pd.notna(exch) else True

    rec = {
        "date": r.date, "code": r.code, "cont_days": int(r.cont_days),
        "phase": r.phase, "window": r.buy_window, "diverge": bool(r.diverge),
        "tier": tier_of(r), "t0_close": c0,
        "t1_open": float(t1["open"]), "t1_low": float(t1["low"]),
        "t1_close": float(t1["close"]), "t1_limit_up": bool(t1["is_limit_up"])
        if pd.notna(t1["is_limit_up"]) else False,
        "t1_one_word": bool(t1["is_one_word"]) if pd.notna(t1["is_one_word"]) else False,
        "t2_open": float(t2["open"]),
        "t3_open": float(t3["open"]) if t3 is not None else None,
        "t2_prev_close": float(t1["close"]),
        "t3_prev_close": float(t2["close"]) if t3 is not None else None,
        "tradeable": tradeable,
    }
    for k in K_GRID:
        price = round(c0 * k, 2)
        tag = f"k{str(k).replace('.', '')}"
        if not tradeable:
            rec[f"{tag}_fill"] = False
            rec[f"{tag}_px"] = None
            rec[f"{tag}_ret"] = None
            continue
        # 跌停买不到？挂单价低于跌停价时不能成交（交易所拒绝），用 0.902 近似
        below_floor = price < c0 * 0.902
        fillable = (not below_floor) and (rec["t1_low"] <= price)
        if not fillable:
            rec[f"{tag}_fill"] = False
            rec[f"{tag}_px"] = None
            rec[f"{tag}_ret"] = None
            continue
        fill_px = min(price, rec["t1_open"])
        # 卖出：T+2 开盘；跌停封死 → T+3 开盘
        if not stunned_open(t2, rec["t2_prev_close"]):
            exit_px = rec["t2_open"]
        elif t3 is not None and not stunned_open(t3, rec["t3_prev_close"]):
            exit_px = rec["t3_open"]
        else:
            exit_px = None
        rec[f"{tag}_fill"] = True
        rec[f"{tag}_px"] = fill_px
        rec[f"{tag}_ret"] = ((exit_px / fill_px - 1) * 100 - FEE * 100
                             if exit_px else None)
    rows.append(rec)

d = pd.DataFrame(rows)
out = "_audit/b_pullback.csv"
d.to_csv(out, index=False)
print(f"模拟 {len(d)} 个信号 → {out}", flush=True)


def share(s: pd.Series) -> float:
    v = s.dropna()
    return 100 * v.mean() if len(v) else float("nan")


def blk(name: str, sub: pd.DataFrame, tag: str) -> None:
    fill = sub[f"{tag}_fill"]
    ret = sub[f"{tag}_ret"].dropna()
    n_fill = int(fill.sum())
    participate = 100 * n_fill / len(sub) if len(sub) else 0.0
    per_sig = float(sub[f"{tag}_ret"].fillna(0).mean()) if len(sub) else 0.0
    if n_fill == 0:
        print(f"  {name:30s} n={len(sub):3d} 参与 0% 每信号 +0.00%")
        return
    win = 100 * (ret > 0).mean()
    print(f"  {name:30s} n={len(sub):3d} 参与{participate:5.1f}% 成交{n_fill:3d} "
          f"成交后均值{ret.mean():+6.2f}% 中位{ret.median():+6.2f}% 胜率{win:4.0f}% "
          f"每信号{per_sig:+6.2f}%")


print("\n=== 口径 C 回踩买（T+1 限价 = T 收盘 × k）→ T+2 开盘卖，费后 ===")
for k in K_GRID:
    tag = f"k{str(k).replace('.', '')}"
    blk(f"k={k:.2f}", d, tag)

print("\n=== 按相位（每信号期望 / 参与率）===")
for k in (0.99, 0.97, 0.95, 0.93):
    tag = f"k{str(k).replace('.', '')}"
    print(f"-- k={k:.2f} --")
    for phase, g in d.groupby("phase"):
        blk(phase, g, tag)

print("\n=== 按身份档（k=0.97）===")
for tier, g in d.groupby("tier"):
    blk(tier, g, "k097")

print("\n=== 按年度（k=0.97）===")
for y, g in d.groupby(d.date.dt.year):
    blk(str(y), g, "k097")

print("\n=== 成交与未成交的 T+1 走势对照（k=0.97，看是否有逆向选择）===")
tag = "k097"
f = d[d[f"{tag}_fill"]]
nf = d[~d[f"{tag}_fill"]]
for name, g in (("成交", f), ("未成交", nf)):
    if not len(g):
        continue
    gap = 100 * (g.t1_open / g.t0_close - 1)
    day = 100 * (g.t1_close / g.t0_close - 1)
    print(f"  {name:6s} n={len(g):3d} T+1 开盘涨幅均值{gap.mean():+6.2f}% "
          f"T+1 收盘涨幅均值{day.mean():+6.2f}% 一字占比{100*g.t1_one_word.mean():.1f}%")
