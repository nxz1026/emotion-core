"""只读探针3：信号到达率 + 前瞻赔率（批量 SQL 算，不走逐条 replay）。"""
from __future__ import annotations
import sys
from emotion_core.utils.db import query_df

sigs = query_df("SELECT confirm_date AS date, code, action, buy_window FROM signal ORDER BY date")
print("signal 表 n =", len(sigs), "| action:", sigs["action"].value_counts().to_dict())
fwd = query_df("""
WITH s AS (SELECT confirm_date AS date, code, action, buy_window FROM signal)
SELECT s.date, s.code, s.action, s.buy_window,
       (b1.open / b0.close - 1) * 100 AS t1_gap,
       (b1.close / b0.close - 1) * 100 AS t1_close,
       (b3.high / b0.close - 1) * 100 AS t3_max_up,
       (b5.low  / b0.close - 1) * 100 AS t5_max_dd,
       (b5.close/ b0.close - 1) * 100 AS t5_close
FROM s
JOIN daily_bar b0 ON b0.code = s.code AND b0.date = s.date
LEFT JOIN daily_bar b1 ON b1.code = s.code AND b1.date = (
    SELECT min(n.date) FROM daily_bar n WHERE n.code = s.code AND n.date > s.date)
LEFT JOIN daily_bar b3 ON b3.code = s.code AND b3.date = (
    SELECT n.date FROM daily_bar n WHERE n.code = s.code AND n.date > s.date ORDER BY n.date LIMIT 1 OFFSET 2)
LEFT JOIN daily_bar b5 ON b5.code = s.code AND b5.date = (
    SELECT n.date FROM daily_bar n WHERE n.code = s.code AND n.date > s.date ORDER BY n.date LIMIT 1 OFFSET 4)
""")
print("可结算 n =", len(fwd))
def rep(name, s):
    s = s.dropna()
    if len(s) == 0:
        print(f"  {name}: n=0"); return
    print(f"  {name}: n={len(s)} mean={s.mean():+.2f}% median={s.median():+.2f}% win={100*(s>0).mean():.1f}%")
print("\n[BUY 全样本]")
for c in ["t1_gap", "t1_close", "t3_max_up", "t5_max_dd", "t5_close"]:
    rep(c, fwd[c])
print("\n[BUY × buy_window]")
for w, sub in fwd[fwd.action=="BUY"].groupby("buy_window"):
    print(f" window={w} n={len(sub)}")
    for c in ["t1_gap", "t1_close", "t5_close", "t5_max_dd"]:
        rep(c, sub[c])
print("\n[SECONDARY 全样本]")
sub = fwd[fwd.action=="SECONDARY"]
for c in ["t1_gap", "t1_close", "t5_close"]:
    rep(c, sub[c])
# 次日继续涨停率
prom = query_df("""
WITH s AS (SELECT confirm_date AS date, code, action FROM signal),
     n1 AS (SELECT s.date, s.code, s.action, (SELECT min(n.date) FROM daily_bar n WHERE n.code=s.code AND n.date>s.date) nd
            FROM s)
SELECT n1.action, count(*) n, count(*) FILTER (WHERE d.is_limit_up) zt
FROM n1 LEFT JOIN derived_bar d ON d.code=n1.code AND d.date=n1.nd GROUP BY n1.action""")
print("\n[次日继续涨停率]")
for r in prom.itertuples():
    print(f"  {r.action}: {r.zt}/{r.n} = {100*r.zt/r.n:.1f}%")
