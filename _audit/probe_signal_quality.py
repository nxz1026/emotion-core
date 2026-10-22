"""只读探针：replay 全史 → 五条件信号的到达率与前向赔率（不改任何库表）。"""
from __future__ import annotations
import sys
from datetime import date
import pandas as pd
from emotion_core.algorithms import evaluate
from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df
from emotion_core.utils.dates import trading_days

END = date.today()
START = CONFIG.DATA_START
days = trading_days(START, END)
ms = query_df("SELECT date, phase, buy_window, has_candidate FROM market_stat ORDER BY date")

print(f"区间 {days[0]} ~ {days[-1]}  交易日 {len(days)}")
print("phase 分布:", ms["phase"].value_counts().to_dict())
print("buy_window 分布:", ms["buy_window"].value_counts().to_dict())
print("has_candidate 真:", int(ms["has_candidate"].sum()), "/", len(ms))

print("replay 中 ...", flush=True)
sigs = evaluate.replay(days[0], days[-1])
print("replay 完成: 窗口非NONE 且 有唯一最高板的日数 =", len(sigs))
if sigs.empty:
    sys.exit(0)
print("按窗口:", sigs["window"].value_counts().to_dict())
print("五条件全过:", int(sigs["passed"].sum()), f"({sigs['passed'].mean():.1%})")
print("五条件未过的原因(前8):")
from collections import Counter
c = Counter()
for _, r in sigs[~sigs["passed"]].iterrows():
    c.update(r["fails"])
for k, v in c.most_common(8):
    print(f"   {k}: {v}")

fwd = evaluate.forward_stats(sigs)
print("forward_stats 列:", list(fwd.columns))
cols = [x for x in ["t1_gap", "t1_promote", "t1_close_premium", "rule_ret", "t3_max_up", "t5_max_dd"] if x in fwd.columns]
print("\n全样本(n=%d):" % len(fwd))
for c in cols:
    s = fwd[c].dropna()
    if len(s):
        print(f"  {c}: n={len(s)} mean={s.mean():.2f} median={s.median():.2f} win={100*(s>0).mean():.1f}%")
g = fwd.merge(sigs[["date", "code", "passed", "window"]], on=["date", "code"], how="left")
print("\n分层（passed × window）:")
for name, sub in g.groupby(["passed", "window"]):
    print(f"  passed={name[0]} window={name[1]} n={len(sub)}")
    for c in cols:
        s = sub[c].dropna()
        if len(s) >= 5:
            print(f"     {c}: n={len(s)} mean={s.mean():.2f} median={s.median():.2f} win={100*(s>0).mean():.1f}%")
    print("     rule_ret<0 比例: %.1f%%" % (100 * (sub["rule_ret"].dropna() < 0).mean()) if "rule_ret" in sub else "")
