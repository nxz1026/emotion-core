"""相位持续性：当前相位已经连了几天、历史上同样「第几天」时次日延续的概率。

只做展示：不改判据、不参与买卖判定。数据来自 `market_stat`（逐交易日一行，
相邻两行即相邻交易日），因此「连续第 k 天」按行序计算即可，不需要另查日历。

用它的理由：675 天里「高潮↔退潮」来回翻 182 次，单看今天的相位会让人以为
明天要变天；把「这次已经第几天、历史上第几天通常还延续多久」并排摆出来，
读者自己就能判断这个相位标签该信几分。
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from emotion_core.utils.db import query_df

_COLS = ["phase", "streak", "n", "rate", "same_next_n"]


def _series() -> pd.DataFrame:
    """逐交易日相位序列（升序）+ 连续天数（同一相位连续第几天，从 1 起）。"""
    df = query_df("SELECT date, phase FROM market_stat ORDER BY date")
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    streaks: list[int] = []
    prev: str | None = None
    k = 0
    for phase in df["phase"].tolist():
        k = k + 1 if phase == prev else 1
        prev = phase
        streaks.append(k)
    df["streak"] = streaks
    # 次日是否仍是同一相位（最后一天没有次日 → False 且标记 NaN 由样本剔除处理）
    same_next = df["phase"].shift(-1) == df["phase"]
    df["same_next"] = same_next.where(df["phase"].shift(-1).notna())
    return df


def persistence(trade_date: date) -> dict:
    """`trade_date` 当天的相位持续性。

    返回 `{"phase","streak","n","rate","same_next_n"}`：`n` 是历史上「同一相位 +
    同一连续天数」的样本数，`rate` 是其中次日仍延续同相位的比例（%）。样本不足
    或当天不在 `market_stat` 里时返回空字典（缺数不填 0）。
    """
    df = _series()
    if df.empty:
        return {}
    hit = df[df["date"] == trade_date]
    if hit.empty:
        return {}
    phase = str(hit.iloc[0]["phase"])
    streak = int(hit.iloc[0]["streak"])
    hist = df[(df["phase"] == phase) & (df["streak"] == streak) & df["same_next"].notna()]
    n = int(hist.shape[0])
    if n == 0:
        return {"phase": phase, "streak": streak, "n": 0, "rate": None, "same_next_n": 0}
    same = int(hist["same_next"].astype(bool).sum())
    return {
        "phase": phase,
        "streak": streak,
        "n": n,
        "rate": round(same / n * 100, 0),
        "same_next_n": same,
    }


def line(res: dict) -> str:
    """一句人话：`退潮已连续 3 天；历史上第 3 天的退潮，次日仍退潮 13/20 次（65%）`。"""
    if not res:
        return ""
    head = f"{res['phase']}已连续 {res['streak']} 天"
    if not res.get("n"):
        return head + f"；历史上没有「第 {res['streak']} 天」的同类样本，不给延续率"
    return (
        head + f"；历史上第 {res['streak']} 天的{res['phase']}，"
        f"次日仍{res['phase']} {res['same_next_n']}/{res['n']} 次"
        f"（{res['rate']:.0f}%）"
    )
