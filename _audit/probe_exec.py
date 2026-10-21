"""决定性探针：可执行买卖规则赔率对比（只读）。

候选宇宙：ladder_day 里 is_sole_top AND is_exchange AND cont_days>=4 的
「唯一换手高标」日（不限 buy_window —— 新方案允许退潮日出信号）。

比较四种买卖口径：
  V-A  T日收盘价买 → T+1开盘卖            （参考：隔夜溢价，但 T 日盘后才出报告，不可执行）
  V-B  T+1开盘买   → T+2开盘卖            （盘后可执行，但高开追涨风险）
  V-C  T+1封板价买 → T+2开盘卖            （盘后可执行 + 口径B 绝对价格；未封板=空仓不动）
  V-D  T+1封板价买 → T+2开盘卖，T+1一字板排除（可成交性更严）

顺延：T+2 开盘跌停封死（卖不掉）→ 顺延 T+3 开盘（强制了结）。
费后双边 0.15%。
"""
from __future__ import annotations

import pandas as pd

from emotion_core.utils.db import query_df

FEE = 0.0015  # 双边合计（佣金+印花+滑点）

CAND_SQL = """
SELECT l.date, l.code, l.cont_days, l.is_exchange, l.y_top_group_count::int g,
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

c = query_df(CAND_SQL)
c["date"] = pd.to_datetime(c.date)
c["code"] = c.code.astype(str).str.zfill(6)
print(f"候选宇宙 {len(c)} 天  {c.date.min().date()} ~ {c.date.max().date()}", flush=True)

lo = c.date.min() - pd.Timedelta(days=10)
hi = c.date.max() + pd.Timedelta(days=40)
codes = tuple(sorted(c.code.unique()))
px = query_df(
    "SELECT code, date, open, high, low, close FROM daily_bar "
    "WHERE date >= %s AND date <= %s AND code = ANY(%s)", (lo, hi, list(codes)))
zt = query_df(
    "SELECT code, date, is_limit_up, is_exchange, is_one_word FROM derived_bar "
    "WHERE date >= %s AND date <= %s AND code = ANY(%s)", (lo, hi, list(codes)))
px["date"] = pd.to_datetime(px.date)
zt["date"] = pd.to_datetime(zt.date)
px = px.merge(zt, on=["code", "date"], how="left")
for k in ("open", "high", "low", "close"):
    px[k] = px[k].astype(float)
px = px.sort_values(["code", "date"])
print(f"行情行数 {len(px)}", flush=True)

by_code = {k: v.reset_index(drop=True) for k, v in px.groupby("code")}
# 每只票的下一个交易日索引表
next_idx: dict[str, pd.Series] = {}
for code, sub in by_code.items():
    nxt = pd.Series(sub.index.values[1:], index=sub.index.values[:-1])
    next_idx[code] = nxt


def row_on(code: str, d: pd.Timestamp, step: int):
    """返回 code 在 d 之后第 step 个交易日的行（不存在则 None）。"""
    sub = by_code.get(code)
    if sub is None:
        return None
    # 定位 d 所在行
    hit = sub.index[sub["date"] == d]
    if not len(hit):
        return None
    i = int(hit[0])
    j = i + step
    if j >= len(sub):
        return None
    return sub.iloc[j]


def stunned_open(r) -> bool:
    """开盘跌停且全天未开板 → 卖不掉。"""
    if r is None:
        return True
    o, low, pc = float(r["open"]), float(r["low"]), float(r["close"])
    prev = r["_prev_close"]
    return bool(o <= prev * 0.902 and low >= o * 0.999)


rows = []
for r in c.itertuples():
    code, d = r.code, r.date
    sub = by_code[code]
    hit = sub.index[sub["date"] == d]
    if not len(hit):
        continue
    i0 = int(hit[0])
    t0 = sub.iloc[i0]
    t1 = sub.iloc[i0 + 1] if i0 + 1 < len(sub) else None
    t2 = sub.iloc[i0 + 2] if i0 + 2 < len(sub) else None
    t3 = sub.iloc[i0 + 3] if i0 + 3 < len(sub) else None
    if t1 is None or t2 is None:
        continue
    prev_close_t1 = float(t0["close"])
    t1 = t1.copy(); t1["_prev_close"] = prev_close_t1
    t2 = t2.copy(); t2["_prev_close"] = float(t1["close"])
    if t3 is not None:
        t3 = t3.copy(); t3["_prev_close"] = float(t2["close"])

    rec = {
        "date": d, "code": code, "cont_days": int(r.cont_days), "phase": r.phase,
        "window": r.buy_window, "diverge": bool(r.diverge),
        "g": int(r.g), "s": int(r.s), "in_y_top": int(r.in_y_top),
        "t0_close": float(t0["close"]),
        "t1_open": float(t1["open"]), "t1_close": float(t1["close"]),
        "t1_zt": bool(t1["is_limit_up"]), "t1_ex": bool(t1["is_exchange"]),
        "t1_oneword": bool(t1["is_one_word"]),
        "t2_open": float(t2["open"]), "t3_open": float(t3["open"]) if t3 is not None else None,
    }

    def exit_at_open(t_next, t_after):
        """在 t_next 开盘卖；若跌停封死顺延到 t_after 开盘。"""
        if t_next is None:
            return None, None
        if not stunned_open(t_next):
            return float(t_next["open"]), 1
        if t_after is not None and not stunned_open(t_after):
            return float(t_after["open"]), 2
        return None, 3

    # V-A：T 日收盘买 → T+1 开盘卖
    ex, hold = exit_at_open(t1, t2)
    rec["vA"] = (ex / rec["t0_close"] - 1) * 100 - FEE * 100 if ex else None
    rec["vA_hold"] = hold

    # V-B：T+1 开盘买 → T+2 开盘卖
    ex, hold = exit_at_open(t2, t3)
    rec["vB"] = (ex / rec["t1_open"] - 1) * 100 - FEE * 100 if ex else None
    rec["vB_hold"] = hold

    # V-C：T+1 封板价买（收盘价即涨停价）→ T+2 开盘卖；未封板 = 不动
    if rec["t1_zt"]:
        ex, hold = exit_at_open(t2, t3)
        rec["vC"] = (ex / rec["t1_close"] - 1) * 100 - FEE * 100 if ex else None
        rec["vC_hold"] = hold
    else:
        rec["vC"] = None
        rec["vC_hold"] = None
    # V-D：同 V-C 但排除一字板（不可成交）
    if rec["t1_zt"] and not rec["t1_oneword"]:
        ex, hold = exit_at_open(t2, t3)
        rec["vD"] = (ex / rec["t1_close"] - 1) * 100 - FEE * 100 if ex else None
    else:
        rec["vD"] = None
    rows.append(rec)

d = pd.DataFrame(rows)
d.to_csv("_audit/exec_cohorts.csv", index=False)
print(f"模拟 {len(d)} 天", flush=True)


def blk(name, s):
    s = pd.Series(s).dropna()
    if not len(s):
        print(f"  {name:34s} n=0")
        return
    print(f"  {name:34s} n={len(s):3d} mean={s.mean():+6.2f}% med={s.median():+6.2f}% "
          f"win={100*(s>0).mean():4.0f}% 最差={s.min():+7.2f}% 最好={s.max():+7.2f}%")


print("\n=== 四口径全样本（费后）===")
blk("V-A T收盘买→T+1开盘卖", d.vA)
blk("V-B T+1开盘买→T+2开盘卖", d.vB)
blk("V-C T+1封板买→T+2开盘卖", d.vC)
blk("V-D V-C排除一字板", d.vD)
print(f"  T+1 封板率 {100*d.t1_zt.mean():.1f}% | 封板且换手 {100*(d.t1_zt & d.t1_ex).mean():.1f}% "
      f"| 封板且一字 {100*(d.t1_zt & d.t1_oneword).mean():.1f}%")
print(f"  V-C 参与率（封板率）{100*d.t1_zt.mean():.1f}% → 每信号期望 "
      f"{(d.vC.fillna(0)).mean():+.2f}%（未封板不动=0）")
print(f"  V-D 参与率 {100*(d.t1_zt & ~d.t1_oneword).mean():.1f}% → 每信号期望 "
      f"{(d.vD.fillna(0)).mean():+.2f}%")
print("  顺延(卖不掉)占比 V-A:", f"{100*(d.vA_hold > 1).mean():.1f}%",
      "V-B:", f"{100*(d.vB_hold > 1).mean():.1f}%",
      "V-C:", f"{100*(d.vC_hold > 1).fillna(0).mean():.1f}%")

print("\n=== V-C（盘后可执行 + 绝对价格口径B）分层 ===")
for key, sub in (("phase", "phase"), ("window", "window")):
    print(f" -- 按 {key} --")
    for k, g2 in d.groupby(sub):
        blk(f"{k} (封板率{100*g2.t1_zt.mean():.0f}%)", g2.vC)
        print(f"      每信号期望 {g2.vC.fillna(0).mean():+.2f}%")

print("\n -- c3 档（严格胜出 / 身位延续 / 新插队）--")


def c3tier(r) -> str:
    if r.g >= 2 and r.s == 1 and r.in_y_top == 1:
        return "严格胜出"
    return "身位延续" if r.in_y_top == 1 else "新插队"


d["c3tier"] = d.apply(c3tier, axis=1)
for k, g2 in d.groupby("c3tier"):
    blk(f"{k} n={len(g2)}", g2.vC)
    print(f"      每信号期望 {g2.vC.fillna(0).mean():+.2f}% | 封板率 {100*g2.t1_zt.mean():.0f}%")

print("\n -- 退潮日 V-C（禁买解禁的核心）--")
ebb = d[d.phase == "退潮"]
blk("退潮·V-C", ebb.vC)
print(f"      每信号期望 {ebb.vC.fillna(0).mean():+.2f}% | 封板率 {100*ebb.t1_zt.mean():.0f}%")
for k, g2 in ebb.groupby("c3tier"):
    blk(f"  退潮·{k}", g2.vC)
    print(f"      每信号期望 {g2.vC.fillna(0).mean():+.2f}%")

print("\n -- 高潮分歧降级日 V-C（应降仓）--")
deg = d[(d.phase == "高潮") & (d.window == "NONE")]
blk("高潮·分歧降级", deg.vC)
print(f"      每信号期望 {deg.vC.fillna(0).mean():+.2f}% | 封板率 {100*deg.t1_zt.mean():.0f}%")
for k, g2 in deg.groupby("c3tier"):
    blk(f"  降级·{k}", g2.vC)
    print(f"      每信号期望 {g2.vC.fillna(0).mean():+.2f}%")

print("\n=== 对照：V-A（不可执行的老口径）按 c3 档 ===")
for k, g2 in d.groupby("c3tier"):
    blk(f"V-A {k}", g2.vA)
print("\n=== V-B 按 c3 档 ===")
for k, g2 in d.groupby("c3tier"):
    blk(f"V-B {k}", g2.vB)
print("\n=== 板数分层（V-C）===")
for k, g2 in d[d.cont_days.between(4, 10)].groupby("cont_days"):
    blk(f"{k}板", g2.vC)
    print(f"      每信号期望 {g2.vC.fillna(0).mean():+.2f}%")
print("\n已存 _audit/exec_cohorts.csv")
