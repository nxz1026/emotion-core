"""只读探针5：对 replay 的 181 个「有唯一最高板」日子做前向赔率，按是否过五条件分组。
用一次 SQL 拉全市场 T+1..T+5 行情与涨停标记，避免逐条查库。"""
from __future__ import annotations
from datetime import date
from emotion_core.algorithms import evaluate
from emotion_core.utils.config import CONFIG
from emotion_core.utils.dates import trading_days
from emotion_core.utils.db import query_df
import pandas as pd

days = trading_days(CONFIG.DATA_START, date.today())
sigs = evaluate.replay(days[0], days[-1])
print("信号日 n =", len(sigs), "| 五条件全过:", int(sigs["passed"].sum()), flush=True)
sigs[["date", "code", "window", "passed"]].to_csv("/tmp/sigs.csv", index=False)

lo, hi = sigs["date"].min(), sigs["date"].max()
px = query_df("""
SELECT code, date, open, high, low, close, volume FROM daily_bar WHERE date >= %s AND date <= %s + 10
""", (lo, hi))
px = px.sort_values(["code", "date"])
px["r"] = px.groupby("code").cumcount()
zt = query_df("SELECT code, date, is_limit_up FROM derived_bar WHERE date >= %s AND date <= %s + 10", (lo, hi))
px = px.merge(zt, on=["code", "date"], how="left")

rows = []
for s in sigs.itertuples():
    g = px[(px.code == s.code) & (px.date >= s.date)].head(6)
    if g.empty:
        continue
    base = float(g.iloc[0]["close"])
    r = {"date": s.date, "code": s.code, "window": s.window, "passed": bool(s.passed), "cont_days": s.cont_days}
    if len(g) >= 2:
        r["t1_gap"] = (g.iloc[1]["open"] / base - 1) * 100
        r["t1_close"] = (g.iloc[1]["close"] / base - 1) * 100
        r["zt1"] = g.iloc[1]["is_limit_up"]
    if len(g) >= 4:
        r["up3"] = (g.iloc[1:4]["high"].max() / base - 1) * 100
        r["dd3"] = (g.iloc[1:4]["low"].min() / base - 1) * 100
        r["c3"] = (g.iloc[3]["close"] / base - 1) * 100
        r["zt3"] = g.iloc[3]["is_limit_up"]
    if len(g) >= 6:
        r["up5"] = (g.iloc[1:6]["high"].max() / base - 1) * 100
        r["dd5"] = (g.iloc[1:6]["low"].min() / base - 1) * 100
        r["c5"] = (g.iloc[5]["close"] / base - 1) * 100
    rows.append(r)
df = pd.DataFrame(rows)
df.to_csv("/tmp/fwd.csv", index=False)

def rep(name, s):
    s = pd.Series(s).dropna()
    if len(s) == 0:
        print(f"    {name}: n=0"); return
    print(f"    {name}: n={len(s)} mean={s.mean():+.2f}% med={s.median():+.2f}% win={100*(s>0).mean():.1f}%")

for key, grp in [("五条件全过 passed=True", df[df.passed]), ("未过 passed=False", df[~df.passed])]:
    print(f"\n[{key}] n={len(grp)}")
    for c in ["t1_gap", "t1_close", "up3", "dd3", "c3", "up5", "dd5", "c5"]:
        rep(c, grp[c])
    z = grp["zt1"].dropna()
    if len(z): print(f"    次日涨停率 {100*z.mean():.1f}%")
print("\n[窗口分层]")
for w, sub in df.groupby("window"):
    print(f"  window={w} n={len(sub)} passed={int(sub.passed.sum())}")
    rep("t1_gap", sub["t1_gap"]); rep("c3", sub["c3"]); rep("c5", sub["c5"]); rep("dd5", sub["dd5"])
df.to_csv("/home/ubuntu/DSH/emotion-core/_audit/fwd_181.csv", index=False)
print("\n已存 _audit/fwd_181.csv")
