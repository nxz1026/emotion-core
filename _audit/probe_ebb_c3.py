"""退潮日内部再切：c3 严格通过 vs 未通过，各自的隔夜收益与顺延风险。"""
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
"""
c = query_df(SQL)
c["date"] = pd.to_datetime(c.date); c["code"] = c.code.astype(str).str.zfill(6)
lo = c.date.min(); hi = c.date.max() + pd.Timedelta(days=40)
px = query_df("SELECT code, date, open, high, low, close FROM daily_bar WHERE date >= %s AND date <= %s", (lo, hi))
px["date"] = pd.to_datetime(px.date)
for c_ in ("open", "high", "low", "close"):
    px[c_] = px[c_].astype(float)
px = px.sort_values(["code", "date"])
FEE = 0.0015
rows = []
for r in c.itertuples():
    sub = px[(px.code == r.code) & (px.date >= r.date)].head(8).reset_index(drop=True)
    if len(sub) < 2: continue
    entry = float(sub.iloc[0]["close"])
    i, exit_px = 1, None
    while i < len(sub):
        o, low = float(sub.iloc[i]["open"]), float(sub.iloc[i]["low"])
        pc = float(sub.iloc[i-1]["close"])
        if not ((o <= pc * 0.902) and (low >= o * 0.999)):
            exit_px = o; break
        i += 1
    if exit_px is None:
        exit_px = float(sub.iloc[-1]["open"]); i = len(sub) - 1
    rows.append({"date": r.date, "code": r.code, "phase": r.phase, "window": r.buy_window,
                 "g": r.g, "s": r.s, "in_y_top": r.in_y_top, "hold": i,
                 "ret": (exit_px/entry-1)*100 - FEE*100,
                 "c3strict": int(r.g >= 2 and r.s == 1 and r.in_y_top == 1)})
d = pd.DataFrame(rows)

def blk(name, s):
    s = pd.Series(s).dropna()
    if not len(s): print(f"  {name:28s} n=0"); return
    print(f"  {name:28s} n={len(s):3d} mean={s.mean():+6.2f}% med={s.median():+6.2f}% win={100*(s>0).mean():4.0f}% 顺延={100*(d.loc[s.index,'hold']>1).mean():4.0f}%")

print("=== 退潮日：c3 严格 vs 未过 ===")
ebb = d[d.phase == "退潮"]
blk("退潮·c3严格过", ebb[ebb.c3strict == 1].ret)
blk("退潮·c3未过", ebb[ebb.c3strict == 0].ret)
print("\n=== 退潮日：身位延续 vs 新插队 ===")
blk("退潮·身位延续", ebb[ebb.in_y_top == 1].ret)
blk("退潮·新插队", ebb[ebb.in_y_top == 0].ret)
print("\n=== 高潮分歧降级日 (phase=高潮, window=NONE) ===")
dn = d[(d.phase == "高潮") & (d.window == "NONE")]
blk("降级·c3严格过", dn[dn.c3strict == 1].ret)
blk("降级·c3未过", dn[dn.c3strict == 0].ret)
print("\n=== 对照：可买日 ===")
blk("ENHANCED", d[d.window == "ENHANCED"].ret)
blk("STANDARD", d[d.window == "STANDARD"].ret)
d.to_csv("_audit/ebb_c3.csv", index=False)
