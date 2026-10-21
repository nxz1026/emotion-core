"""第二层验证（只读）：
① 进场后最优卖点（T+1封板价买 → 各种卖点对比）
② 年度稳定性（V-C 逐年）
③ 仓位矩阵（phase × c3档 → 每信号期望/胜率），用于定仓位档
④ T+3 强制了结规则的影响
"""
from __future__ import annotations

import pandas as pd

from emotion_core.utils.db import query_df

FEE = 0.0015

# c = 全候选宇宙（427 天），已含 vA/vB/vC/vD/t1_zt/c3 相关列
c = pd.read_csv("_audit/exec_cohorts.csv", parse_dates=["date"])
c["code"] = c.code.astype(str).str.zfill(6)
c["year"] = c.date.dt.year
c["exp"] = c.vC.fillna(0)          # 每信号期望：未封板不动 = 0
print(f"候选 {len(c)} 天  封板 {int(c.t1_zt.sum())} 天", flush=True)


def c3tier(r) -> str:
    if r.g >= 2 and r.s == 1 and r.in_y_top == 1:
        return "严格胜出"
    return "身位延续" if r.in_y_top == 1 else "新插队"


c["c3tier"] = c.apply(c3tier, axis=1)

# ── 进场后的卖点对比：只取封板样本，重载行情算各卖点 ──
lo = c.date.min() - pd.Timedelta(days=10)
hi = c.date.max() + pd.Timedelta(days=40)
codes = sorted(c.code.unique())
px = query_df("SELECT code, date, open, high, low, close FROM daily_bar "
               "WHERE date >= %s AND date <= %s AND code = ANY(%s)", (lo, hi, list(codes)))
px["date"] = pd.to_datetime(px.date)
for k in ("open", "high", "low", "close"):
    px[k] = px[k].astype(float)
px = px.sort_values(["code", "date"])
by_code = {k: v.reset_index(drop=True) for k, v in px.groupby("code")}

rows = []
for r in c[c.t1_zt].itertuples():
    sub = by_code[r.code]
    hit = sub.index[sub["date"] == r.date]
    if not len(hit):
        continue
    i0 = int(hit[0])
    if i0 + 1 >= len(sub):
        continue
    entry = float(sub.iloc[i0 + 1]["close"])      # T+1 封板价 = T+1 收盘价（涨停）
    rec = {"date": r.date, "code": r.code, "phase": r.phase, "window": r.window,
           "cont_days": int(r.cont_days), "entry": entry}
    for step, tag in ((2, "t2"), (3, "t3"), (4, "t4")):
        j = i0 + step
        if j < len(sub):
            rec[f"open_{tag}"] = float(sub.iloc[j]["open"])
            rec[f"close_{tag}"] = float(sub.iloc[j]["close"])
        else:
            rec[f"open_{tag}"] = rec[f"close_{tag}"] = None
    rows.append(rec)

d = pd.DataFrame(rows)
print(f"封板进场样本 {len(d)}", flush=True)


def ret(entry, p):
    return None if p is None else (p / entry - 1) * 100 - FEE * 100


def blk(name, s):
    s = pd.Series(s).dropna()
    if not len(s):
        print(f"  {name:26s} n=0"); return
    print(f"  {name:26s} n={len(s):3d} mean={s.mean():+6.2f}% med={s.median():+6.2f}% "
          f"win={100*(s>0).mean():4.0f}% 最差={s.min():+7.2f}%")


print("\n=== ① 进场(T+1封板价)后的卖点对比 ===")
for name, col in (("T+2开盘", "open_t2"), ("T+2收盘", "close_t2"),
                   ("T+3开盘", "open_t3"), ("T+3收盘", "close_t3"),
                   ("T+4开盘", "open_t4"), ("T+4收盘", "close_t4")):
    blk(name, d.apply(lambda x: ret(x.entry, x[col]), axis=1))

print("\n=== ② V-C(T+2开盘卖) 年度稳定性 ===")
d["vC"] = d.apply(lambda x: ret(x.entry, x.open_t2), axis=1)
d["year"] = d.date.dt.year
for y, g2 in d.groupby("year"):
    blk(f"{y} 封板样本", g2.vC)
print("  —— 每信号期望（全样本，未封板=0）——")
for y, g2 in c.groupby("year"):
    blk(f"{y} 每信号期望", g2.exp)
print("  —— 年度 × 相位 每信号期望 ——")
for (y, ph), g2 in c.groupby(["year", "phase"]):
    print(f"    {y}·{ph:4s} n={len(g2):3d} 封板率={100*g2.t1_zt.mean():4.0f}% 期望={g2.exp.mean():+6.2f}%")

print("\n=== ③ 仓位矩阵：phase × c3档（V-C）===")
for (ph, tier), g2 in c.groupby(["phase", "c3tier"]):
    z = g2[g2.t1_zt]
    win = 100 * (z.vC.dropna() > 0).mean() if len(z) else float("nan")
    print(f"  {ph:4s}·{tier:6s} n={len(g2):3d} 封板率={100*g2.t1_zt.mean():4.0f}% "
          f"每信号期望={g2.exp.mean():+6.2f}% 封板后mean={(z.vC.mean() if len(z) else float('nan')):+6.2f}% "
          f"win={win:4.0f}%")

print("\n=== ④ 相位 × 窗口 ===")
for (ph, w), g2 in c.groupby(["phase", "window"]):
    z = g2[g2.t1_zt]
    win = 100 * (z.vC.dropna() > 0).mean() if len(z) else float("nan")
    print(f"  {ph:4s}·{w:9s} n={len(g2):3d} 封板率={100*g2.t1_zt.mean():4.0f}% "
          f"每信号期望={g2.exp.mean():+6.2f}% win={win:4.0f}%")

print("\n=== ⑤ 高潮分歧降级日（phase=高潮 & window=NONE）明细 ===")
deg = c[(c.phase == "高潮") & (c.window == "NONE")]
z = deg[deg.t1_zt]
print(f"  n={len(deg)} 封板率={100*deg.t1_zt.mean():.0f}% 每信号期望={deg.exp.mean():+.2f}% "
      f"封板后mean={z.vC.mean():+.2f}% win={100*(z.vC.dropna()>0).mean():.0f}%")
for tier, g2 in deg.groupby("c3tier"):
    zz = g2[g2.t1_zt]
    print(f"    ·{tier:6s} n={len(g2):3d} 封板率={100*g2.t1_zt.mean():4.0f}% "
          f"期望={g2.exp.mean():+6.2f}% 封板后win={100*(zz.vC.dropna()>0).mean():4.0f}%")

print("\n=== ⑥ 退潮日明细 ===")
ebb = c[c.phase == "退潮"]
z = ebb[ebb.t1_zt]
print(f"  n={len(ebb)} 封板率={100*ebb.t1_zt.mean():.0f}% 每信号期望={ebb.exp.mean():+.2f}% "
      f"封板后mean={z.vC.mean():+.2f}% win={100*(z.vC.dropna()>0).mean():.0f}%")
for tier, g2 in ebb.groupby("c3tier"):
    zz = g2[g2.t1_zt]
    print(f"    ·{tier:6s} n={len(g2):3d} 封板率={100*g2.t1_zt.mean():4.0f}% "
          f"期望={g2.exp.mean():+6.2f}% 封板后win={100*(zz.vC.dropna()>0).mean():4.0f}%")

print("\n=== ⑦ 板数分层（V-C 每信号期望）===")
for k, g2 in c[c.cont_days.between(4, 12)].groupby("cont_days"):
    z = g2[g2.t1_zt]
    print(f"  {k:2d}板 n={len(g2):3d} 封板率={100*g2.t1_zt.mean():4.0f}% "
          f"期望={g2.exp.mean():+6.2f}% 封板后win={100*(z.vC.dropna()>0).mean():4.0f}%")

print("\n=== ⑧ 封板样本 vC 最差 8 例（风险画像）===")
w = d.sort_values("vC").head(8)
for r in w.itertuples():
    print(f"  {r.date.date()} {r.code} {r.cont_days}板 {r.phase:4s} {r.window:9s} vC={r.vC:+.2f}%")

d.to_csv("_audit/exec_exits.csv", index=False)
print("\n已存 _audit/exec_exits.csv")
