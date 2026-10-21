"""C 轮决定性探针：把「盘中打板」按真实成交口径重算。

与 A/B 轮的差别：入场价 = 涨停价（touched 即成交，能成交因为对手盘在涨停价卖），
样本不再是「事后封住」的子集 —— 东财 ZT 池（收盘封住）与 ZB 池（炸板）都算，
因为两者都有 first_seal（首次涨停时刻），也都能在涨停价成交。

判据：
- 样本 = limit_pool_em 的 ZT ∪ ZB（2026-09-04 起，26 个交易日）；
- 剔除一字板（T 日 low == 涨停价，封死后无对手盘可成交）；
- 入场价 = T 日涨停价 = round(pre_close × 板比)（utils/price.limit_up_price_cents）；
- 卖点 = T+1 开盘（跌停封死则顺延 T+2 开盘，强制了结）；
- 费 = 双边 0.15%；
- 分层 = 首次封板时刻 first_seal（HHMMSS 文本）、当日炸板次数、T 日板数。

输出：_audit/c_seal_quality.csv，以及按 first_seal 分档的期望表。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from emotion_core.utils.db import query_df
from emotion_core.utils.price import limit_down_price_cents, limit_up_price_cents

OUT = Path("/home/ubuntu/DSH/emotion-core/_audit/c_seal_quality.csv")
FEE = 0.0015


def main() -> int:
    pool = query_df(
        """
        SELECT date, code, pool_type, name, cont_days_em, bomb_times,
               first_seal, last_seal
        FROM limit_pool_em
        WHERE pool_type IN ('ZT','ZB') AND code NOT LIKE '3%%' AND code NOT LIKE '68%%'
        ORDER BY date, code
        """
    )
    pool["date"] = pd.to_datetime(pool["date"])
    pool["code"] = pool["code"].astype(str).str.zfill(6)
    seal_min = (
        pd.to_numeric(pool["first_seal"].str.slice(0, 2), errors="coerce") * 60
        + pd.to_numeric(pool["first_seal"].str.slice(2, 4), errors="coerce")
    )
    pool["seal_min"] = seal_min

    bars = query_df(
        """
        SELECT code, date, open, high, low, close, pre_close, volume
        FROM daily_bar
        WHERE date >= '2026-08-28' AND date <= '2026-10-22'
          AND close > 0 AND volume > 0
        """
    )
    bars["date"] = pd.to_datetime(bars["date"])
    bars["code"] = bars["code"].astype(str).str.zfill(6)
    for c in ("open", "high", "low", "close", "pre_close"):
        bars[c] = bars[c].astype(float)
    bars = bars.sort_values(["code", "date"])
    g = bars.groupby("code", sort=False)
    for k in (1, 2):
        bars[f"open_{k}"] = g["open"].shift(-k)
        bars[f"low_{k}"] = g["low"].shift(-k)
        bars[f"pre_{k}"] = g["pre_close"].shift(-k)

    m = pool.merge(
        bars[["code", "date", "open", "low", "close", "pre_close", "open_1", "open_2", "pre_1"]],
        on=["code", "date"],
        how="left",
    )

    rows = []
    for r in m.itertuples(index=False):
        if pd.isna(r.pre_close) or pd.isna(r.low):
            continue
        pre_cents = int(round(r.pre_close * 100))
        lim = limit_up_price_cents(pre_cents, r.code) / 100.0
        if pd.isna(r.open_1):
            continue
        one_word = r.low >= lim - 1e-9  # 一字板：全天没低于涨停价，买不到
        # 顺延：T+1 开盘即跌停封死（开盘≈跌停），则顺延到 T+2 开盘
        exit_px, rolled = r.open_1, False
        if pd.notna(r.pre_1):
            ld1 = limit_down_price_cents(int(round(r.pre_1 * 100)), r.code) / 100.0
            if r.open_1 <= ld1 + 1e-9 and pd.notna(r.open_2) and r.open_2 > 0:
                exit_px, rolled = r.open_2, True
        ret = (exit_px / lim - 1.0 - FEE) * 100.0
        rows.append(
            {
                "date": r.date,
                "code": r.code,
                "name": r.name,
                "pool": r.pool_type,
                "sealed_close": r.pool_type == "ZT",
                "one_word": bool(one_word),
                "seal_min": r.seal_min,
                "bomb_times": r.bomb_times,
                "cont_days": r.cont_days_em,
                "limit_px": lim,
                "t_close": r.close,
                "t1_open": exit_px,
                "rolled": rolled,
                "ret": ret,
            }
        )

    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)
    tradable = df[~df["one_word"]].copy()
    print(f"样本 {len(df)} 行（一字 {int(df.one_word.sum())} 剔除后 {len(tradable)}）")
    print(f"封住占比 {100 * tradable.sealed_close.mean():.1f}% | 顺延 {int(tradable.rolled.sum())}")

    def blk(label: str, s: pd.Series) -> None:
        v = s.dropna()
        if len(v) < 5:
            print(f"  {label:20s} n={len(v):4d} （样本不足）")
            return
        print(
            f"  {label:20s} n={len(v):4d} 每笔{v.mean():+6.2f}% 中位{v.median():+6.2f}% "
            f"胜率{100 * (v > 0).mean():4.0f}% 最差{v.min():+6.2f}%"
        )

    print("\n=== 全部可成交样本（涨停价买 → T+1 开盘卖，费后）===")
    blk("全样本", tradable.ret)
    print("\n=== 按首次封板时刻 ===")
    bins = [0, 570, 600, 660, 780, 870, 1000]
    labels = ["<=09:30", "09:30-10:00", "10:00-11:00", "11:00-13:00", "13:00-14:30", ">14:30"]
    tradable["seal_bucket"] = pd.cut(tradable.seal_min, bins, labels=labels)
    for b, x in tradable.groupby("seal_bucket", observed=True):
        blk(str(b), x.ret)
    print("\n=== 时刻 × 是否封住 ===")
    for b, x in tradable.groupby(["seal_bucket", "sealed_close"], observed=True):
        blk(f"{b[0]}/{'封住' if b[1] else '炸板'}", x.ret)
    print("\n=== 按炸板次数 ===")
    for b, x in tradable.groupby(tradable.bomb_times.fillna(0).clip(upper=3)):
        blk(f"炸板{int(b)}次", x.ret)
    print("\n=== 按 T 日板数 ===")
    for b, x in tradable.groupby(tradable.cont_days.clip(upper=6)):
        blk(f"{int(b)}板", x.ret)

    print("\n=== 只看早盘封板（<=10:00）===")
    early = tradable[tradable.seal_min <= 600]
    blk("early all", early.ret)
    for b, x in early.groupby(early.cont_days.clip(upper=5)):
        blk(f"{int(b)}板", x.ret)
    return 0


if __name__ == "__main__":
    sys.exit(main())
