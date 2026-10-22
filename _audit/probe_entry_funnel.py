"""只读探针2：统计 buy_window 非 NONE 的日子中，五条件逐节点漏斗（哪一条卡死）。"""
from __future__ import annotations
from collections import Counter
from datetime import date
from emotion_core.algorithms import entry, ladder
from emotion_core.utils.db import query_df
from emotion_core.utils.dates import trading_days

ms = query_df("SELECT date, phase FROM market_stat WHERE buy_window <> 'NONE' ORDER BY date")
days = [r.date for r in ms.itertuples()]
print("可买日数:", len(days), flush=True)
c = Counter()
fails = Counter()
samples = []
for i, d in enumerate(days):
    if i and i % 50 == 0:
        print("progress", i, flush=True)
    b = ladder.build(d)
    if not b:
        c["无梯队行"] += 1
        continue
    c["有梯队行"] += 1
    sole = ladder.sole_top(b, min_days=entry.MIN_LEADER_DAYS)
    if sole is None:
        c["无唯一换手高标(" + f"cont>={entry.MIN_LEADER_DAYS})"] += 1
        continue
    c["有唯一候选"] += 1
    window = entry.current_window(d)
    try:
        cl, rows = entry._evaluate(sole, window, min_days=entry.MIN_LEADER_DAYS,
                                   diverge_min=entry.DIVERGE_MIN_TURNOVER, as_of=d)
    except Exception as e:
        c["_evaluate异常"] += 1
        continue
    for name, ok, note in rows:
        if name.startswith("W"):
            continue
        if ok is False:
            fails[name] += 1
        elif ok is None:
            fails[name + "(UNKNOWN)"] += 1
    samples.append((d, sole.code, entry.passed_of(cl)))
print("\n漏斗:", dict(c))
print("\n首个失败条件计数:", dict(fails))
ok = sum(1 for s in samples if s[2])
print(f"\n通过五条件 {ok} / {len(samples)}")
print("样本(前10):", [(str(a), b, p) for a, b, p in samples[:10]])
