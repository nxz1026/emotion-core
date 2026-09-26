"""L2 周度校准提案（奎爷拍板：只提案不落库，改参须人工批准）。

数据源全部现成表（market_stat/signal/signal_outcome），全期回放仅一次，
近端样本按日期切片复用。产出 reports/calib-<date>.md：
①滚动窗口质量 ②阈值敏感度 ③实测收益 ④规则化提案。

语义逐字照搬 lkl/services/calibrate.py。差异仅 IO 适配：
- `from lkl import config` + `config.X` →
  `from emotion_core.utils.config import CONFIG` + `CONFIG.X`；
- `from lkl.utils import db` + `db.query_df` →
  `from emotion_core.utils.db import query_df`；
- `from lkl.services import evaluate` →
  `from emotion_core.algorithms import evaluate`（replay/forward_stats 同签名）；
- `from lkl.utils.dates import trading_days` →
  `from emotion_core.utils.dates import trading_days`；
- `config.CLIMAX_COUNT` → `CONFIG.CLIMAX_ZT`（lkl CLIMAX_COUNT 与
  emotion_core CLIMAX_ZT 同值异名，均为涨停家数高潮阈值 80）。
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from emotion_core.algorithms import evaluate
from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df
from emotion_core.utils.dates import trading_days

log = logging.getLogger("emotion_core.calibrate")


def _quality_stats(end: date, days: int) -> tuple[pd.DataFrame, pd.DataFrame,
                                                  date]:
    """全期回放一次 → (全期stats, 近days日stats切片, 近端起始日)。"""
    all_days = trading_days(CONFIG.DATA_START, end)
    recent_start = all_days[-days] if len(all_days) > days else all_days[0]
    sigs = evaluate.replay(all_days[0], end)
    sigs = sigs[sigs["passed"]] if not sigs.empty else sigs  # 只评五条件全过
    full = evaluate.forward_stats(sigs)
    recent = full[full["date"] >= recent_start] if not full.empty else full
    return full, recent, recent_start


def _agg(st: pd.DataFrame) -> dict:
    if st.empty:
        return {"n": 0, "promote": None, "gap": None}
    return {"n": len(st), "promote": st["t1_promote"].mean(),
            "gap": st["t1_gap"].mean()}


def _rolling_quality(full: pd.DataFrame, recent: pd.DataFrame,
                     days: int, end: date, recent_start: date) -> str:
    a, r = _agg(full), _agg(recent)
    fmt = lambda x: "-" if x is None else f"{x:.2f}"  # noqa: E731
    return (f"| 区间 | 信号数 | 晋级率 | 平均高开 |\n|---|---|---|---|\n"
            f"| 全期 {CONFIG.DATA_START}~{end} | {a['n']} | {fmt(a['promote'])} "
            f"| {fmt(a['gap'])} |\n"
            f"| 近{days}日 {recent_start}~{end} | {r['n']} | {fmt(r['promote'])} "
            f"| {fmt(r['gap'])} |")


def _threshold_sensitivity() -> str:
    """现行阈值在 market_stat 上的命中统计（自适应带/amp/zt/冰点放宽）。"""
    df = query_df(
        "SELECT bomb_rate, bomb_threshold, top_amplitude, limit_up_count,"
        "       reason FROM market_stat")
    band = (f"{df['bomb_threshold'].min():.3f}~{df['bomb_threshold'].max():.3f}"
            if df["bomb_threshold"].notna().any() else "预热中")
    return (f"- 自适应炸板阈值带：{band}；命中 "
            f"{int((df['bomb_rate'] > df['bomb_threshold']).sum())}/{len(df)}\n"
            f"- amp>{CONFIG.CLIMAX_AMPLITUDE:.0f} 命中 "
            f"{int((df['top_amplitude'] > CONFIG.CLIMAX_AMPLITUDE).sum())}/{len(df)}\n"
            f"- zt>{CONFIG.CLIMAX_ZT} 命中 "
            f"{int((df['limit_up_count'] > CONFIG.CLIMAX_ZT).sum())}/{len(df)}\n"
            f"- 冰点·无候选放宽天数：{int(df['reason'].str.contains('无候选放宽').sum())}")


def _outcome_summary() -> str:
    """signal_outcome 实测（开盘价代理口径）。"""
    df = query_df("SELECT * FROM signal_outcome WHERE complete")
    if df.empty:
        return "_前向5日齐的信号还没有（积累中）_"
    g = df.groupby("action").agg(
        n=("code", "count"), 开盘高开=("t1_gap", "mean"),
        晋级率=("t1_promote", "mean"), 开盘买入T5=("t5_close_ret", "mean"),
        五日最大涨=("max_up5", "mean"), 五日最大回撤=("max_dd5", "mean"))
    return g.round(2).to_markdown()


def _proposals(full: pd.DataFrame, recent: pd.DataFrame, days: int) -> list[str]:
    """规则化提案：只有证据触发才建议调参，否则明示无需动作。"""
    out = []
    if len(recent) >= 8 and not full.empty:
        rp, fp = recent["t1_promote"].mean(), full["t1_promote"].mean()
        if rp < fp - 0.15:
            out.append(f"近{days}日晋级率 {rp:.2f} 显著低于全期 {fp:.2f}："
                       f"建议复核高潮阈值与窗口映射")
    thr = query_df("SELECT min(bomb_threshold) lo, max(bomb_threshold) hi"
                   " FROM market_stat")
    if pd.notna(thr["lo"].iloc[0]) and (thr["lo"].iloc[0] < 0.25
                                        or thr["hi"].iloc[0] > 0.55):
        out.append(f"自适应阈值带 {thr['lo'].iloc[0]:.2f}~{thr['hi'].iloc[0]:.2f} "
                   f"越出 0.25~0.55：建议复核 BOMB_WINDOW=30/σ 系数")
    if not out:
        out.append(f"本周无参数触发异常（近{days}日样本 {len(recent)} 条），"
                   f"维持现行参数")
    return out


def proposal(end: date | None = None, days: int = 60) -> str:
    """生成周度校准提案报告，返回文件路径。"""
    end = end or trading_days(CONFIG.DATA_START, date.today())[-1]
    full, recent, recent_start = _quality_stats(end, days)
    md = "\n".join([
        f"# 校准提案 {end}（频率：周）\n",
        "> 只提案不落库；改 config 须奎爷批准并重跑 emotion+evaluate。\n",
        "## ① 滚动信号质量\n\n" + _rolling_quality(full, recent, days, end,
                                                 recent_start),
        "## ② 阈值敏感度\n\n" + _threshold_sensitivity(),
        "## ③ 信号实测（开盘价代理）\n\n" + _outcome_summary(),
        "## ④ 提案\n\n" + "\n".join(f"- {p}"
                                    for p in _proposals(full, recent, days)),
    ])
    out = Path("reports") / f"calib-{end}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(md + "\n", encoding="utf-8")
    log.info("校准提案 -> %s", out)
    return str(out)
