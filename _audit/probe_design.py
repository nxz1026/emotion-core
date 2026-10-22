"""定档所需两组数据：① 新插队高标(in_y_top=0) 的隔夜收益；② 退潮日对前一日持仓的影响。"""
import pandas as pd
from emotion_core.utils.db import query_df

SQL = """
SELECT l.date, l.code, l.cont_days, l.y_top_group_count::int g,
       l.y_top_survivor_count::int s, ms.phase, ms.buy_window,
       CASE WHEN y.code IS NULL THEN 0 ELSE 1 END in_y_top
FROM ladder_day l JOIN market_stat ms ON ms.date = l.date
LEFT JOIN LATERAL (
  SELECT l2.code FROM ladder_day l2
  WHERE l2.date = (SELECT MAX(d.date) FROM ladder_day d WHERE d.date < l.date) AND l2.is_top
) y ON y.code = l.code
WHERE l.is_sole_top AND l.is_exchange AND l.cont_days >= 4
ORDER BY l.date
"""
c = query_df(SQL)
c["date"] = pd.to_datetime(c.date); c["code"] = c.code.astype(str).str.zfill(6)

lo = c.date.min(); hi = c.date.max() + pd.Timedelta(days=40)
px = query_df("SELECT code, date, open, close FROM daily_bar WHERE date >= %s AND date <= %s", (lo, hi))
px["date"] = pd.to_datetime(px.date)
px["open"] = px.open.astype(float); px["close"] = px.close.astype(float)
px = px.sort_values(["code", "date"])
FEE = 0.0015

rows = []
for r in c.itertuples():
    sub = px[(px.code == r.code) & (px.date >= r.date)].head(4).reset_index(drop=True)
    if len(sub) < 2: continue
    entry = float(sub.iloc[0]["close"])
    rows.append({"date": r.date, "code": r.code, "cont_days": r.cont_days,
                 "phase": r.phase, "window": r.buy_window, "g": r.g, "s": r.s,
                 "in_y_top": r.in_y_top,
                 "t1": (float(sub.iloc[1]["open"]) / entry - 1) * 100 - FEE * 100})
d = pd.DataFrame(rows)

def blk(name, s):
    s = pd.Series(s).dropna()
    if not len(s): print(f"  {name}: n=0"); return
    print(f"  {name:30s} n={len(s):3d} mean={s.mean():+6.2f}% med={s.median():+6.2f}% win={100*(s>0).mean():4.0f}%")

print("=== ① 身位延续 vs 新插队高标（隔夜打板）===")
for k, sub in d.groupby("in_y_top"):
    blk(("昨日最高组延续" if k == 1 else "新插队高标"), sub.t1)
print("\n--- 新插队高标再按 g（昨日组规模）切 ---")
n0 = d[d.in_y_top == 0]
for k, sub in n0.groupby("g"):
    blk(f"新插队 g={k}", sub.t1)
print("\n=== ② 退潮日：前一日买入的持仓，隔夜收益如何 ===")
d2 = d.sort_values("date").copy()
d2["next_phase"] = d2["phase"].shift(-1)
d2["next_t1"] = d2["t1"].shift(-1)
print("  今日相位 → 明日隔夜收益（持仓视角）")
for p, sub in d2.groupby("phase"):
    blk(f"{p}→次日", sub.next_t1)
print("\n=== ③ 退潮日(明日)的持仓明细：退潮清仓信号是否合理 ===")
ebb = d2[d2.next_phase == "退潮"]
blk("退潮日持仓次日隔夜", ebb.next_t1)
print(f"  样本 {len(ebb)} 天，胜率 {100*(ebb.next_t1>0).mean():.0f}%，中位 {ebb.next_t1.median():+.2f}%")
d.to_csv("_audit/design_cohorts.csv", index=False)
