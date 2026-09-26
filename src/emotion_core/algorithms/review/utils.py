"""review 格式化工具函数：纯函数，无 DB / 服务依赖。

lkl/services/review/utils.py 的算法层（docs/07 §3.5）：16 个格式化 / 脱敏 / 序列化
工具函数，判定规则、文案、格式串逐字照搬，未增删任何条件、未调任何阈值。

与 lkl 的差异（仅契约适配，不涉判定）：

1. `from lkl import config` → `from emotion_core.utils.config import CONFIG`。本文件
   唯一读的键 `EMOTION_PERF_BASIS` 尚未收录进 CONFIG（emotion.py §5 / accelerate.py
   同先例），故取 lkl config.py 原值 "median"；CONFIG 收录后删 `_EMOTION_PERF_BASIS`
   改为直接引用 CONFIG。
2. logger 名 `lkl.review` → `__name__`。
3. 本模块不 import DB / 服务：`_CHECKS`（人工验证点模板）、`_LLM_SAFE_SECTIONS`
   （白名单脱敏）等常量原样保留，落库 / 落盘编排不在此层。
"""
from __future__ import annotations

import dataclasses
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from emotion_core.utils.config import CONFIG

log = logging.getLogger(__name__)

# CONFIG 未收录（emotion.py §5 / accelerate.py 同先例）：取 lkl config.py 原值
_EMOTION_PERF_BASIS: str = getattr(CONFIG, "EMOTION_PERF_BASIS", "median")


# ── 数值格式化 ──────────────────────────────────────────────

def _pct(v) -> str:
    """比率 → 百分数；None/NaN → '—'（F6 不显示 0%）。"""
    return "—" if v is None or pd.isna(v) else f"{float(v) * 100:.1f}%"


def _num(v, prec: int = 2) -> str:
    """数值格式化；None/NaN → '—'。"""
    return "—" if v is None or pd.isna(v) else f"{float(v):.{prec}f}"


def _pctv(v, prec: int = 2) -> str:
    """数值带百分号；None → '—'（不带号，避免"无数据"看起来像"0%"）。"""
    return "—" if v is None or pd.isna(v) else f"{float(v):.{prec}f}%"


# ── 变化 / 指标表 ──────────────────────────────────────────

def _delta_of(cur, prev, label: str, suffix: str = "") -> str:
    """单指标较昨日变化行；None/缺昨日 → '—'（不显示假变化）。"""
    if cur is None:
        return ""
    if prev is None or str(prev) == "nan":
        return f"- {label}：{cur}{suffix}（昨日无数据）"
    if str(cur) == str(prev):
        return f"- {label}：{cur}{suffix}（持平）"
    return f"- {label}：{prev}{suffix} → {cur}{suffix}"


def _fmt_stat(cur: dict | None, prev: dict | None) -> str:
    """指标表：双口径高度并列 + 均值/中位数对照，★ 标出当前判据列（§2.2/§4.2）。"""
    star = lambda name: " ★判据" if _EMOTION_PERF_BASIS == name else ""
    keys = [("limit_up_count", "涨停家数"), ("bomb_rate", "炸板率"),
            ("zt_performance_mean", "昨涨停表现·均值%" + star("mean")),
            ("zt_performance_median", "昨涨停表现·中位%" + star("median")),
            ("max_limit_days", "名义最高板"), ("tradable_max_days", "可交易最高板"),
            ("oneword_ratio", "一字占比"), ("limit_down_count", "跌停家数")]
    lines = ["| 指标 | 今日 | 昨日 |", "|---|---|---|"]
    for k, label in keys:
        c = cur.get(k) if cur else None
        p = prev.get(k) if prev else None
        lines.append(f"| {label} | {c if c is not None else '—'} "
                     f"| {p if p is not None else '—'} |")
    return "\n".join(lines)


# ── 生态评级 ────────────────────────────────────────────────

_ENV_MEANING = {"FAVORABLE": "核心条件成立，生态适合龙空龙",
                "NEUTRAL": "生态一般/证据不足",
                "UNFAVORABLE": "生态不适合，倾向空仓观察"}
_MARK = lambda s: "?" if s is None else ("✓" if s else "✗")


def _env_caveat(de: dict) -> str:
    """B 方案诚实性条款：FAVORABLE 若系增强条件 UNKNOWN 放行，必须显式标注。

    核心 G1/G4 恒可算；增强 G2/G3 依赖 theme_group 积累，未到位时为 None，
    不参与否决也不静默当作成立——在此把缺口摊开给读者。
    """
    if de["rating"] != "FAVORABLE":
        return ""
    un = [str(i.get("cond", "")).split(" ")[0] for i in de["goods"]
          if isinstance(i, dict) and i.get("ok") is None]
    if not un:
        return ""
    return (f"（注意：增强条件 {'、'.join(un)} 尚无数据未验证，"
            "本评级仅基于核心条件 G1/G4）")


_CONFLICT_PHASE = ("退潮", "冰点")


def _env_conflict(d: dict) -> tuple:
    """阶段与生态口径冲突的解读（警告不否决；风险登记13 要求两口径不合并）。

    两个方向都必须提示，缺一即留隐患：
      生态 FAVORABLE × 阶段退潮/冰点 —— 结构好但不给买点，防误读成"可以干"；
      生态 UNFAVORABLE × 窗口给买 —— 给买点但土壤坏，防在坏生态里下单。
    """
    de, st = d["dragon_env"], (d["stat"] or {})
    if not de:
        return ()
    rating, phase, win = de["rating"], st.get("phase", ""), d["window"]
    if rating == "FAVORABLE" and phase in _CONFLICT_PHASE:
        msg = (f"> ⚠ **口径冲突提示**：生态结构 FAVORABLE 但当前阶段为 **{phase}**"
               f"（`buy_window={win}`）。生态评「梯队结构是否具备龙空龙土壤」，"
               f"阶段评「当下给不给买点」；退潮/冰点期结构完好，含义是**下一轮"
               f"周期的候选池已备好**而非今日可干。**操作以 buy_window 为准**。")
        return (msg, "")
    if rating == "UNFAVORABLE" and win in ("ENHANCED", "STANDARD"):
        un = "、".join(str(i.get("cond", "")).split(" ")[0]
                       for i in de["bads"] if isinstance(i, dict) and i.get("ok"))
        msg = (f"> ⚠ **口径冲突提示**：今日 `buy_window={win}` 给买点，但生态评级 "
               f"UNFAVORABLE{f'（不利成立：{un}）' if un else ''}。窗口只看阶段与"
               f"候选可成交性，不含梯队结构判断；**在坏土壤里下单是龙空龙的主要"
               f"亏损来源**，本段列出的是降仓/放弃的理由，按惯例只警告不否决信号。")
        return (msg, "")
    return ()


# ── 梯队标签 ────────────────────────────────────────────────

def _ladder_tag(r) -> str:
    """单票标签（②段）。R1：「炸N」改「开板N(东财)」——zbc 为东财逐笔
    微观开板计数，非分时可见的开板阶段数，避免误读为 N 次完整炸板。"""
    exch = "" if r.is_exchange else " 一字"
    bomb = f" 开板{r.bomb_times}(东财)" if r.bomb_times else ""
    return f"{r.name}({r.code}{exch}{bomb})"


def _seal_notes(rows, seal_map: dict) -> list[str]:
    """R1：开板票回封时间附注（限 2 板以上，防刷屏）。"""
    out = []
    for r in rows:
        if r.bomb_times and r.cont_days >= 2 and r.code in seal_map:
            first, last = seal_map[r.code]
            out.append(f"{r.name}：首封{first or '—'} 末次回封{last or '—'}"
                       f"（东财 zbc={r.bomb_times}）")
    return out


# ── 题材格式化 ──────────────────────────────────────────────

def _fmt_catalyst(tags: pd.DataFrame) -> str:
    """催化证据子表：有事件催化或≥4板的高标个股。"""
    sel = tags[(tags["catalyst_source"] == "announcement")
               | (tags["cont_days"] >= 4)]
    if sel.empty:
        return ""
    lines = ["", "**催化剂证据（P2 裁决）**", "",
             "| 股票 | 板数 | 裁决题材 | 催化摘录 | 证据日期 | 置信 |",
             "|---|---|---|---|---|---|"]
    for r in sel.itertuples():
        cat = (str(r.catalyst).replace("|", "\\|")
               if pd.notna(r.catalyst) else "（无事件催化，概念映射）")
        ev = r.evidence_date if pd.notna(r.evidence_date) else "—"
        conf = r.confidence if pd.notna(r.confidence) else "—"
        lines.append(f"| {r.name}({r.code}) | {r.cont_days} | {r.primary_theme}"
                     f" | {cat} | {ev} | {conf} |")
    return "\n".join(lines)


# ── 口径 / 可用性 / 反证 ──────────────────────────────────

def _fmt_caliber(c: dict) -> str:
    """⑤段口径渲染。A2（审计 P1-3，奎爷拍板标注局限）：ST/股本为当前口径。"""
    return ("\n**统计口径**：" + f"universe={c['universe']} 剔ST/剔次新 | "
            f"include_bj={str(c['include_bj']).lower()} | "
            f"include_20cm={str(c['include_20cm']).lower()} | "
            f"snapshot={c['snapshot_time']} | limit_method={c['limit_method']}\n"
            f"**双源计数**：自算涨停 {c['counts_self']['zt']} / "
            f"东财池 {c['counts_em_pool']['zt']}；自算跌停 "
            f"{c['counts_self']['dt']} / 东财池 {c['counts_em_pool']['dt']}\n"
            "> ⚠ **ST 与流通股本为当前口径（非 point-in-time）**：ST 戴帽/摘帽"
            "与增发解禁会使历史样本漂移、换手率失真（A2）——本报告结论用于"
            "横向相对比较，不外推绝对收益\n"
            f"> {c['diff_expectation']}")


def _usability(d: dict) -> dict:
    """产品 A1-1a：本报告可用于决策的总判（三态，绝不把缺数判绿）。

    OK=质检无差异无警示；PARTIAL=有对账差异或缺数警示（可用于参考，
    结论须人工复核）；UNKNOWN=东财池缺失无法对账（不显示绿色结论）。
    """
    q = d.get("quality", {})
    diff, warns = q.get("diff"), q.get("warns", [])
    if diff is None:
        state, note = "UNKNOWN", "东财池缺失，无法对账——本报告不可用于决策"
    elif not diff.empty:
        state = "PARTIAL"
        note = f"对账差异 {len(diff)} 行——结论须人工复核"
    elif warns:
        state, note = "PARTIAL", "；".join(warns)
    else:
        state, note = "OK", "质检通过：无对账差异、无缺数警示"
    return {"state": state, "note": note}


def _counter_items(d: dict) -> list[str]:
    """收集反证与不确定性条目（引擎 v1 §8.7）；每条须可追溯到具体数值。"""
    s = d["stat"] or {}
    out = []
    mean, med = s.get("zt_performance_mean"), s.get("zt_performance_median")
    if mean is not None and med is not None and (mean > 0) != (med > 0):
        out.append(f"昨涨停表现均值 {_num(mean)}% 与中位数 {_num(med)}% **方向相反**"
                   "——赚钱效应呈偏态分布，仅看均值的结论不可靠")
    nh, th = s.get("max_limit_days"), s.get("tradable_max_days")
    if nh is not None and th is not None and nh > th:
        out.append(f"名义 {nh}板 高于可交易 {th}板：最高标不可成交，"
                   "高度对龙空龙策略的示范意义大于参与意义")
    if s.get("top_broke") is None:
        out.append("昨日无 ≥2 板组或无前日数据：断板类判据本轮无输入（非「未断板」）")
    de = d["dragon_env"] or {}
    for i in de.get("goods", []) + de.get("bads", []):
        if isinstance(i, dict) and i.get("ok") is None:
            out.append(f"评级条件 {i.get('cond')} 证据不足（{i.get('note')}），未计入判定")
    for warn in d["quality"].get("warns", []):
        out.append(f"数据质量：{warn}")
    return out


# ── 验证点模板 ──────────────────────────────────────────────

_CHECKS = {
    "发酵": ["换手口径晋级率能否延续今日水平（低位先暖是否扩散到高位）",
             "名义最高板与可交易最高板差距是否维持 ≤1（差距拉大＝转加速）",
             "涨停家数能否继续放大且不伴随炸板率抬升"],
    "高潮": ["炸板率是否突破自适应阈值（是＝分歧扩大，向退潮观察）",
             "最高板股振幅是否继续放大（>15% 为一致性松动）",
             "昨日涨停表现中位数能否守住正值"],
    "退潮": ["跌停家数是否收敛（回落至 15 以下为负反馈减弱信号）",
             "首板与二板晋级率是否先于高位回暖（修复早期特征）",
             "是否存在个股反包（反包＝退潮未确认，可能只是分歧）"],
    "冰点": ["是否出现 ≥4 板换手高标（高度打开＝新周期启动候选）",
             "涨停家数能否站上 40（脱离冰点计数区）",
             "昨日涨停表现中位数是否转正（负反馈实质收敛）"],
}


# ── LLM 安全 ────────────────────────────────────────────────

_LLM_SAFE_SECTIONS = ("速览", "情绪", "梯队", "题材", "淘汰赛", "生态",
                     "反证", "验证点", "数据质检")


def _strip_position_block(md: str) -> str:
    """V5→审计三轮修复：白名单脱敏——LLM 只见市场面段。

    旧版猜标题含「仓」（前12字）——实际持仓写在「⑥ 明日参考」
    标题不含仓字，持仓明细发给了第三方 LLM。改为显式白名单：
    只有 _LLM_SAFE_SECTIONS 中的段输出，其余（明日参考/持仓对账/
    LLM 自评段）整段跳过。
    """
    out, keep = [], False
    for line in md.splitlines():
        if line.startswith("## "):
            keep = any(k in line for k in _LLM_SAFE_SECTIONS)
        if keep:
            out.append(line)
    return "\n".join(out)


# ── I/O 与序列化 ────────────────────────────────────────────

def _jsonable(o):
    """json.dumps default 钩子：DataFrame/dataclass/date/Decimal/numpy → 原生类型。"""
    if isinstance(o, pd.DataFrame):
        return json.loads(o.to_json(orient="records", date_format="iso",
                                    force_ascii=False))
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return dataclasses.asdict(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    if hasattr(o, "item"):  # numpy 标量（int64/float64/bool_）
        return o.item()
    raise TypeError(f"不可序列化: {type(o).__name__}")


def _atomic_write(out: Path, text: str) -> None:
    """P2：tmp+rename 原子落盘——进程中断时读者永不拿半文件。"""
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.rename(out)
