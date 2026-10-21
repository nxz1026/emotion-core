"""B 轮探针 2：修正「成交假设」后重测可执行边缘（只读）。

## 为什么重写
`_audit/probe_b_pullback.py` 第一版把 `derived_bar.is_exchange`（= 换手板：
is_limit_up 且 low < 涨停价）当成「当天可成交」，于是 427 个信号里只统计出
43 个「T+1 回踩后又封回涨停」的样本 → 100% 胜率、成交后 +17%。
那是**事后视角**，与 A 轮 V-C 的错法同源（用收盘是否封住反推能否成交）。

本探针的成交判据只用「当天真的在交易」这一事实：T+1 有量价、价格 > 0，
且限价单价格落在 T+1 的 [low, high] 区间内。

## 三族（全部费后 0.15%，T+1 交收：最早 T+2 开盘才能卖）
F1 回踩买：T 日唯一高标（sole_top & cont>=4）→ T+1 挂 limit = T 收盘×k
F2 超跌反转：T 日跌幅 <= -7% 且非跌停 → T+1 开盘买 → T+2/T+3/T+5 开盘卖
F3 温和高开追强：T 日唯一高标 → T+1 开盘买，按 T+1 高开幅度分桶（决策发生在
   开盘那一刻，属「开盘条件单」，比盘后无条件下单多一层可执行性要求）

输出：_audit/b_alt.csv（逐信号）+ 控制台分层。
"""
from __future__ import annotations

import pandas as pd

from emotion_core.utils.db import query_df

FEE = 0.0015
K_GRID = (1.00, 0.99, 0.97, 0.95, 0.93)

CAND_SQL = """
SELECT l.date, l.code, l.cont_days, l.y_top_group_count::int g,
       l.y_top_survivor_count::int s, ms.phase, ms.buy_window, ms.diverge,
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


def stat(name: str, s: pd.Series, n_total: int | None = None) -> None:
    v = s.dropna()
    if not len(v):
        print(f"  {name:34s} n=0")
        return
    extra = ""
    if n_total:
        extra = f" 参与{100 * len(v) / n_total:5.1f}% 每信号{v.mean() * len(v) / n_total:+6.2f}%"
    print(f"  {name:34s} n={len(v):4d} 均值{v.mean():+6.2f}% 中位{v.median():+6.2f}% "
          f"胜率{100 * (v > 0).mean():4.0f}% 最差{v.min():+7.2f}% 最好{v.max():+7.2f}%{extra}")


def load_px(codes, lo, hi) -> dict[str, pd.DataFrame]:
    px = query_df(
        "SELECT code, date, open, high, low, close, volume FROM daily_bar "
        "WHERE date >= %s AND date <= %s AND code = ANY(%s)", (lo, hi, list(codes)))
    px["date"] = pd.to_datetime(px.date)
    px["code"] = px.code.astype(str).str.zfill(6)
    for k in ("open", "high", "low", "close"):
        px[k] = px[k].astype(float)
    px = px.sort_values(["code", "date"])
    return {k: v.reset_index(drop=True) for k, v in px.groupby("code")}


def bar(sub: pd.DataFrame, d: pd.Timestamp):
    h = sub.index[sub["date"] == d]
    return None if not len(h) else int(h[0])


def usable(*rows) -> bool:
    """当天真的在交易：价格>0 且成交量>0。"""
    for r in rows:
        if r is None:
            return False
        if min(float(r["open"]), float(r["close"]), float(r["low"])) <= 0:
            return False
        if float(r["volume"] or 0) <= 0:
            return False
    return True


# ── F1 回踩买 ────────────────────────────────────────────────────────────
c = query_df(CAND_SQL)
c["date"] = pd.to_datetime(c.date)
c["code"] = c.code.astype(str).str.zfill(6)
lo, hi = c.date.min() - pd.Timedelta(days=10), c.date.max() + pd.Timedelta(days=40)
by = load_px(c.code.unique(), lo, hi)
print(f"F1 候选 {len(c)} 天", flush=True)

f1: list[dict] = []
for r in c.itertuples():
    sub = by.get(r.code)
    if sub is None:
        continue
    i = bar(sub, r.date)
    if i is None or i + 2 >= len(sub):
        continue
    t0, t1, t2 = sub.iloc[i], sub.iloc[i + 1], sub.iloc[i + 2]
    if not usable(t0, t1, t2):
        continue
    c0 = float(t0["close"])
    rec = {"date": r.date, "code": r.code, "phase": r.phase, "tier": tier_of(r),
           "t0_close": c0, "t1_open": float(t1["open"]), "t1_low": float(t1["low"]),
           "t2_open": float(t2["open"])}
    for k in K_GRID:
        price = round(c0 * k, 2)
        tag = f"k{str(k).replace('.', '')}"
        ok = price >= c0 * 0.902 and float(t1["low"]) <= price      # 跌停价以下挂不上
        rec[f"{tag}_fill"] = ok
        if ok:
            px_ = min(price, float(t1["open"]))
            rec[f"{tag}_ret"] = (float(t2["open"]) / px_ - 1) * 100 - FEE * 100
        else:
            rec[f"{tag}_ret"] = None
    f1.append(rec)

d1 = pd.DataFrame(f1)
d1.to_csv("_audit/b_alt_f1.csv", index=False)
print(f"\n=== F1 回踩买（T+1 限价 = T 收盘×k）→ T+2 开盘卖，未成交=0 ===")
for k in K_GRID:
    tag = f"k{str(k).replace('.', '')}"
    sub = d1[d1[f"{tag}_ret"].notna()]
    print(f"  k={k:.2f}", end="")
    stat("", sub[f"{tag}_ret"], n_total=len(d1))

# ── F3 温和高开追强（T+1 开盘买）────────────────────────────────────────
print("\n=== F3 T+1 开盘买 → T+2 开盘卖，按 T+1 高开幅度分桶 ===")
f3: list[dict] = []
for r in c.itertuples():
    sub = by.get(r.code)
    if sub is None:
        continue
    i = bar(sub, r.date)
    if i is None or i + 2 >= len(sub):
        continue
    t0, t1, t2 = sub.iloc[i], sub.iloc[i + 1], sub.iloc[i + 2]
    if not usable(t0, t1, t2):
        continue
    c0 = float(t0["close"])
    gap = (float(t1["open"]) / c0 - 1) * 100
    if float(t1["open"]) >= c0 * 1.098:      # 开盘即涨停 → 买不到
        continue
    f3.append({"date": r.date, "code": r.code, "phase": r.phase, "tier": tier_of(r),
               "gap": gap,
               "ret": (float(t2["open"]) / float(t1["open"]) - 1) * 100 - FEE * 100})
d3 = pd.DataFrame(f3)
d3.to_csv("_audit/b_alt_f3.csv", index=False)
edges = [-100, -2, 0, 2, 5, 8, 100]
d3["bucket"] = pd.cut(d3.gap, bins=edges, right=False,
                      labels=["<-2%", "-2~0%", "0~2%", "2~5%", "5~8%", ">8%"])
for b, g in d3.groupby("bucket", observed=True):
    stat(str(b), g.ret)
stat("全部", d3.ret)
print(" -- 2~5% 桶按年度 --")
for y, g in d3[(d3.gap >= 2) & (d3.gap < 5)].groupby(d3.date.dt.year):
    stat(str(y), g.ret)
print(" -- 2~5% 桶按相位 --")
for p, g in d3[(d3.gap >= 2) & (d3.gap < 5)].groupby("phase"):
    stat(p, g.ret)

# ── F2 超跌反转 ─────────────────────────────────────────────────────────
print("\n=== F2 超跌反转（T 日跌幅<=-7% 且非跌停）→ T+1 开盘买 ===")
u = query_df(
    "SELECT code, date, close, pre_close, is_limit_down FROM daily_bar b "
    "JOIN derived_bar d USING (code, date) "
    "WHERE d.date >= '2024-01-01' AND b.close > 0 AND b.pre_close > 0 "
    "AND (b.close / b.pre_close - 1) <= -0.07 AND NOT d.is_limit_down")
u["date"] = pd.to_datetime(u.date)
u["code"] = u.code.astype(str).str.zfill(6)
print(f"超跌信号 {len(u)}")
by2 = load_px(u.code.unique(), u.date.min() - pd.Timedelta(days=10),
              u.date.max() + pd.Timedelta(days=30))
f2: list[dict] = []
for r in u.itertuples():
    sub = by2.get(r.code)
    if sub is None:
        continue
    i = bar(sub, r.date)
    if i is None or i + 3 >= len(sub):
        continue
    t0, t1 = sub.iloc[i], sub.iloc[i + 1]
    t2, t3 = sub.iloc[i + 2], sub.iloc[i + 3]
    if not usable(t0, t1, t2):
        continue
    entry = float(t1["open"])
    rec = {"date": r.date, "code": r.code, "drop": (float(r.close) / float(r.pre_close) - 1) * 100,
           "t2": (float(t2["open"]) / entry - 1) * 100 - FEE * 100}
    rec["t3"] = ((float(t3["open"]) / entry - 1) * 100 - FEE * 100) if usable(t3) else None
    f2.append(rec)

d2 = pd.DataFrame(f2)
d2.to_csv("_audit/b_alt_f2.csv", index=False)
stat("T+2 开盘卖", d2.t2)
stat("T+3 开盘卖", d2.t3)
print(" -- T+2 开盘卖 按跌幅档 --")
for lo_, hi_, lab in ((-100, -9, "<=-9%"), (-9, -8, "-9~-8%"), (-8, -7.01, "-8~-7%")):
    g = d2[(d2.drop > lo_) & (d2.drop <= hi_)]
    stat(lab, g.t2)
print(" -- T+2 开盘卖 按年度 --")
for y, g in d2.groupby(d2.date.dt.year):
    stat(str(y), g.t2)
