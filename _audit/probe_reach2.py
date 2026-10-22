"""只读探针4：BUY vs SECONDARY 分层赔率 + 持有期的「断板卖」规则化收益。"""
from __future__ import annotations
from emotion_core.utils.db import query_df

base_sql = """
WITH s AS (SELECT confirm_date AS date, code, action, buy_window FROM signal WHERE action = '{act}'),
     n1 AS (SELECT s.*, (SELECT min(n.date) FROM daily_bar n WHERE n.code=s.code AND n.date>s.date) nd FROM s)
SELECT n1.date, n1.code, n1.buy_window,
       (b1.open / b0.close - 1) * 100 AS t1_gap,
       (b1.close / b0.close - 1) * 100 AS t1_close,
       (b3.high / b0.close - 1) * 100 AS t3_max_up,
       (b5.low / b0.close - 1) * 100 AS t5_max_dd,
       (b5.close / b0.close - 1) * 100 AS t5_close,
       d1.is_limit_up AS zt1, d3.is_limit_up AS zt3
FROM n1
JOIN daily_bar b0 ON b0.code = n1.code AND b0.date = n1.date
LEFT JOIN daily_bar b1 ON b1.code = n1.code AND b1.date = n1.nd
LEFT JOIN daily_bar b3 ON b3.code = n1.code AND b3.date = (
    SELECT n.date FROM daily_bar n WHERE n.code = n1.code AND n.date > n1.date ORDER BY n.date LIMIT 1 OFFSET 2)
LEFT JOIN daily_bar b5 ON b5.code = n1.code AND b5.date = (
    SELECT n.date FROM daily_bar n WHERE n.code = n1.code AND n.date > n1.date ORDER BY n.date LIMIT 1 OFFSET 4)
LEFT JOIN derived_bar d1 ON d1.code = n1.code AND d1.date = n1.nd
LEFT JOIN derived_bar d3 ON d3.code = n1.code AND d3.date = b3.date
"""

def rep(name, s):
    s = s.dropna()
    if len(s) == 0:
        print(f"  {name}: n=0"); return
    print(f"  {name}: n={len(s)} mean={s.mean():+.2f}% median={s.median():+.2f}% win={100*(s>0).mean():.1f}%")

for act in ("BUY", "SECONDARY"):
    df = query_df(base_sql.format(act=act))
    print(f"\n===== {act} (n={len(df)}) =====")
    for c in ["t1_gap", "t1_close", "t3_max_up", "t5_max_dd", "t5_close"]:
        rep(c, df[c])
    d = df.dropna(subset=["zt1"])
    print(f"  次日涨停率 {int(d.zt1.sum())}/{len(d)} = {100*d.zt1.mean():.1f}%")
    d3 = df.dropna(subset=["zt3"])
    print(f"  T+3涨停率  {int(d3.zt3.sum())}/{len(d3)} = {100*d3.zt3.mean():.1f}%（条件化：仅看 T+1 是否继续封板）")
    if act == "BUY":
        # 规则化收益：T+1 开盘买入，持有到首个非涨停日收盘卖出（简化：T+3 收盘 if T+3 未封板 else T+5 收盘）
        d = df.dropna(subset=["zt3", "t5_close"])
        print("  分组（T+3 是否仍封板）:")
        for k, sub in d.groupby(d.zt3.fillna(False).astype(bool)):
            print(f"    zt3={k} n={len(sub)}")
            rep("t5_close", sub["t5_close"]); rep("t5_max_dd", sub["t5_max_dd"])
