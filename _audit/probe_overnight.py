"""打板策略完整模拟：T日收盘涨停价买入 → T+1开盘卖；开盘跌停/涨停封死则顺延。"""
import pandas as pd
from emotion_core.utils.db import query_df

m = pd.read_csv("_audit/fwd_181.csv", parse_dates=["date"])
m["code"] = m.code.astype(str).str.zfill(6)
lo = m.date.min(); hi = m.date.max() + pd.Timedelta(days=40)
px = query_df("SELECT code, date, open, high, low, close FROM daily_bar"
              " WHERE date >= %s AND date <= %s", (lo, hi))
zt = query_df("SELECT code, date, is_limit_up FROM derived_bar"
              " WHERE date >= %s AND date <= %s", (lo, hi))
px["date"] = pd.to_datetime(px.date); zt["date"] = pd.to_datetime(zt.date)
px = px.merge(zt, on=["code", "date"], how="left")
for c in ("open", "high", "low", "close"):
    px[c] = px[c].astype(float)
px = px.sort_values(["code", "date"])

FEE = 0.0015
rows = []
for r in m.itertuples():
    sub = px[(px.code == r.code) & (px.date >= r.date)].head(8).reset_index(drop=True)
    if len(sub) < 3:
        continue
    entry = float(sub.iloc[0]["close"])        # T日收盘涨停价买入
    i = 1
    exit_px, how = None, None
    # 逐日尝试开盘卖：开盘跌停/一字跌停则卖不掉，顺延
    while i < len(sub):
        o = float(sub.iloc[i]["open"])
        low = float(sub.iloc[i]["low"])
        prev_close = float(sub.iloc[i - 1]["close"])
        # 开盘跌停（<= -9.8%）且全天未开板 → 卖不掉
        stunned = (o <= prev_close * 0.902) and (low >= o * 0.999)
        if not stunned:
            exit_px, how = o, "T+%d开盘" % i
            break
        i += 1
    if exit_px is None:
        exit_px, how = float(sub.iloc[-1]["open"]), "顺延上限"
        i = len(sub) - 1
    rows.append({"date": r.date, "code": r.code, "cont_days": r.cont_days,
                 "window": r.window, "hold": i, "how": how,
                 "ret": (exit_px / entry - 1) * 100 - FEE * 100})
d = pd.DataFrame(rows)


def blk(name, s):
    s = pd.Series(s).dropna()
    if not len(s): print(f"  {name}: n=0"); return
    print(f"  {name:22s} n={len(s):3d} mean={s.mean():+6.2f}% med={s.median():+6.2f}% "
          f"win={100*(s>0).mean():4.0f}% 最差={s.min():+6.2f}% 最好={s.max():+6.2f}%")


print("=== 打板 T日收盘 → 次日开盘卖（跌停封死则顺延）费后 %.2f%% ===" % (FEE*100))
blk("全部", d["ret"])
print("  顺延占比:", f"{100*(d.hold > 1).mean():.1f}%", "| 持有分布:", d.hold.value_counts().sort_index().to_dict())
blk("持有1日(正常)", d[d.hold == 1]["ret"])
blk("顺延>1日", d[d.hold > 1]["ret"])
print("\n=== 按窗口 ===")
for w, sub in d.groupby("window"):
    blk(w, sub["ret"])
print("\n=== 按板数 ===")
for h, sub in d[d.cont_days.between(4, 10)].groupby("cont_days"):
    blk(f"{h}板", sub["ret"])
print("\n=== 与持有到断板对比（前次模拟 mean -1.90%）===")
print(f"  隔夜卖 mean={d.ret.mean():+.2f}% vs 断板出 mean=-1.90%")
d.to_csv("_audit/overnight.csv", index=False)
