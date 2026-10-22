"""口径B 事后对照：把观察记录按「当时真打板买入」算一遍结果。

口径B（ADR 0002 D1；2026-10-22 被 `_audit/probe_fill.py` 证伪后**只作复盘度量**）：
- 买点 = T 日收盘 × 板比（即 T+1 涨停价，`utils.price.limit_up_price_cents` 唯一实现）；
- 成交 = T+1 盘中**触及即成交**（`high >= 涨停价`），不是「收盘还封着」——后者是事后视角；
- 卖点 = T+2 开盘；T+2 开盘与收盘都在跌停（封死卖不掉）则顺延 T+3 开盘；
- 费 = 双边 0.15%；未成交按 0 计入「每信号」，同时单列成交率；
- T+2 缺失（样本右截断）→ 该条不计入任何口径，不猜价。

本模块只做度量：不产生信号、不进买卖判定、不改任何阈值。数字须与 Lab 基线
（`lab/score.py` 打出的 n=375 / 每信号 −1.16%）同口径 —— 两处改了要一起对。

用到的字段全是**成交时刻之前**已知的（T 收盘、T+1 的 high），没有任何收盘后
状态量参与成交判定（A/B/C 三轮踩过同一个坑：`is_limit_up` / `is_exchange` /
`cont_days` 都造出过 100% 胜率幻觉）。
"""

from __future__ import annotations

import json
import re
from datetime import date

import pandas as pd

from emotion_core.utils.db import query_df
from emotion_core.utils.price import limit_down_price_cents, limit_up_price_cents

FEE_PCT = 0.15
"""双边手续费（%），与 `lab/metrics.FEE` 同值。"""

AUTO_FILL_EPS = 1e-9
"""价格比较容差（元）。"""

_TIER_RE = re.compile(r"^身份\s*([^：:]+)[：:]")
"""从 checklist 的 c3 行说明里取身份档（`身份 新插队：…`）。"""

_COLS = ["confirm_date", "code", "name", "phase", "diverge", "tier",
         "entry_px", "exit_px", "rolled", "filled", "ret_pct", "reason_null"]


def _tier_of(checklist: object) -> str | None:
    """从 checklist（jsonb 反序列化后的行列表）里取 c3 身份档；取不到给 None。"""
    if checklist is None:
        return None
    rows = checklist
    if isinstance(rows, (str, bytes)):
        try:
            rows = json.loads(rows)
        except (TypeError, ValueError):
            return None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        label, note = str(row[0]), str(row[2])
        if not label.startswith("c3"):
            continue
        m = _TIER_RE.match(note.strip())
        if m:
            return m.group(1).strip()
    return None


def _signals(days: int | None) -> pd.DataFrame:
    """BUY 观察记录（含相位与分歧标记）。顺序按确认日升序。"""
    sql = ("SELECT s.confirm_date, s.code, s.checklist, m.phase, m.diverge,"
           "       b.name"
           "  FROM signal s"
           "  LEFT JOIN market_stat m ON m.date = s.confirm_date"
           "  LEFT JOIN stock_basic b ON b.code = s.code"
           " WHERE s.action = 'BUY'")
    if days:
        # days = 最近 N 条观察记录（不是最近 N 个自然日——信号本身很稀）
        df = query_df(sql + " ORDER BY s.confirm_date DESC, s.code LIMIT %s",
                      (int(days),))
    else:
        df = query_df(sql + " ORDER BY s.confirm_date DESC, s.code")
    if df.empty:
        return df
    out = pd.DataFrame({
        "confirm_date": pd.to_datetime(df["confirm_date"]).dt.date,
        "code": df["code"].astype(str).str.zfill(6),
        "name": df["name"],
        "phase": df["phase"],
        "diverge": df["diverge"].fillna(False).astype(bool),
        "tier": [_tier_of(c) for c in df["checklist"]],
    })
    return out


def _bars(codes: list[str], lo: date, hi: date) -> pd.DataFrame:
    """窗口内日线（含涨停/跌停标记），供逐票取 T/T+1/T+2/T+3。"""
    df = query_df(
        "SELECT b.code, b.date, b.open, b.high, b.low, b.close, b.pre_close,"
        "       b.volume, d.is_limit_down"
        "  FROM daily_bar b"
        "  LEFT JOIN derived_bar d ON d.code = b.code AND d.date = b.date"
        " WHERE b.code = ANY(%s) AND b.date BETWEEN %s AND %s"
        " ORDER BY b.code, b.date", (codes, lo, hi))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for col in ("open", "high", "low", "close", "pre_close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _num(v: object) -> float | None:
    if v is None or pd.isna(v):
        return None
    return float(v)


def _at_limit_down(bar: pd.Series, code: str) -> bool:
    """该 bar 是否跌停封死（开盘=收盘=跌停价）——盘中撬开就有成交，保守按未封算。"""
    open_px, close_px, pre = _num(bar["open"]), _num(bar["close"]), _num(bar["pre_close"])
    if open_px is None or close_px is None or pre is None or pre <= 0:
        return False
    ld = limit_down_price_cents(int(round(pre * 100)), code) / 100
    return open_px <= ld + AUTO_FILL_EPS and close_px <= ld + AUTO_FILL_EPS


def _one(sig: pd.Series, bars: dict[str, pd.DataFrame]) -> dict | None:
    """单条观察记录的口径B 结果；T 或 T+1 缺数返回 None（不计入统计）。"""
    code = sig["code"]
    g = bars.get(code)
    if g is None:
        return None
    dates = g.index.tolist()
    i = g.index.get_loc(sig["confirm_date"]) if sig["confirm_date"] in g.index else -1
    if i < 0 or i + 1 >= len(dates):
        return None

    base = _num(g.iloc[i]["close"])
    n1 = g.iloc[i + 1]
    if base is None or base <= 0:
        return None
    entry = limit_up_price_cents(int(round(base * 100)), code) / 100
    t1_high = _num(n1["high"])
    filled = (t1_high is not None and t1_high >= entry - AUTO_FILL_EPS
              and (_num(n1["volume"]) or 0) > 0)

    row = {"entry_px": round(entry, 2), "exit_px": None, "rolled": False,
           "filled": bool(filled), "ret_pct": None, "reason_null": None}
    if not filled:
        row["ret_pct"] = 0.0      # 未成交按 0 计（同时单列成交率）
        row["reason_null"] = "未触及涨停价"
        return row

    if i + 2 >= len(dates):
        row["filled"] = None      # 右截断：不计入任何口径
        row["ret_pct"] = None
        row["reason_null"] = "T+2 缺失（样本右截断）"
        return row

    n2 = g.iloc[i + 2]
    exit_px, rolled = _num(n2["open"]), False
    if exit_px is None:
        row["filled"] = None
        row["reason_null"] = "T+2 开盘缺失"
        return row
    if _at_limit_down(n2, code):
        if i + 3 < len(dates):
            exit_px, rolled = _num(g.iloc[i + 3]["open"]), True
            if exit_px is None:
                row["filled"] = None
                row["reason_null"] = "T+3 开盘缺失"
                return row
        else:
            row["filled"] = None
            row["reason_null"] = "T+2 跌停封死且 T+3 缺失"
            return row

    row["exit_px"] = round(exit_px, 2)
    row["rolled"] = bool(rolled)
    row["ret_pct"] = round((exit_px / entry - 1) * 100 - FEE_PCT, 3)
    return row


def evaluate(days: int | None = 30) -> pd.DataFrame:
    """最近 `days` 条 BUY 观察记录（`None` = 全部）的口径B 逐条结果。

    返回列见 `_COLS`；`filled` 为 None 的行是右截断/缺数，调用方应剔除。
    """
    sigs = _signals(days)
    if sigs.empty:
        return pd.DataFrame(columns=_COLS)
    codes = sorted(sigs["code"].unique().tolist())
    bars = _bars(codes, min(sigs["confirm_date"]), max(sigs["confirm_date"]))
    if bars.empty:
        return pd.DataFrame(columns=_COLS)
    by_code = {code: g.set_index("date") for code, g in bars.groupby("code",
                                                                    sort=False)}
    rows = []
    for _, sig in sigs.iterrows():
        got = _one(sig, by_code)
        if got is None:
            continue
        rows.append({**sig.to_dict(), **got})
    if not rows:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(rows)[_COLS]


def _stats(rets: pd.Series) -> dict:
    """一组收益的统计；n=0 时全给 None（缺数不填 0）。"""
    n = int(len(rets))
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    return {"n": n,
            "mean": round(float(rets.mean()), 3),
            "median": round(float(rets.median()), 3),
            "win_rate": round(float((rets > 0).mean()) * 100, 1)}


def summarize(df: pd.DataFrame) -> dict:
    """整体 + 按相位 + 按身份档 + 按相位×身份档。

    每条信号先算「每信号」（未成交记 0），再单独给「成交后」（只算成交的）。
    """
    if df is None or df.empty:
        return {"n": 0, "groups": []}
    ok = df[df["filled"].notna()].copy()
    if ok.empty:
        return {"n": 0, "groups": []}
    ok["shot"] = ok["ret_pct"].fillna(0.0)

    groups = []

    def add(name: str, sub: pd.DataFrame) -> None:
        # 成交后只算真的成交了那部分（未成交的 ret_pct 是 0，不能混进来）
        filled = sub.loc[sub["filled"] == True, "ret_pct"].dropna()  # noqa: E712
        st = _stats(sub["shot"])
        fs = _stats(filled)
        if st["n"] == 0:
            return
        groups.append({
            "group": name, "n": st["n"],
            "fill_rate": round(len(filled) / st["n"] * 100, 1),
            "mean": st["mean"], "median": st["median"],
            "win_rate": st["win_rate"],
            "n_filled": fs["n"], "mean_filled": fs["mean"],
            "median_filled": fs["median"], "win_rate_filled": fs["win_rate"],
        })

    add("全部", ok)
    for phase, sub in ok.groupby("phase", dropna=False):
        add(f"{phase}" if phase else "相位缺失", sub)
    for tier, sub in ok.groupby("tier", dropna=False):
        add(f"{tier}" if tier else "身份缺失", sub)
    for (phase, tier), sub in ok.groupby(["phase", "tier"], dropna=False):
        if sub.shape[0] >= 10:      # 样本太少的组合不单列，避免误读
            add(f"{phase}×{tier}" if tier else f"{phase}×身份缺失", sub)
    return {"n": int(ok.shape[0]), "groups": groups}
