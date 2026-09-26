"""回测—实盘一致性对比（产品 A2-5）：compare <date>。

语义逐字照搬 lkl/services/compare.py（docs/07-算法层搬运方案.md §3.4，82 行），
判定与口径零改动：
- 实盘侧 = signal 表当日 confirm_date、action='BUY' 的 (code, status)，
  按 code 升序（status 原样，不折算成 passed）；
- 回放侧 = evaluate.replay(d, d, as_of=d)（当日唯一候选 + 五条件判定）的
  (code, bool(passed))；
- drift = 两侧**代码集**对称差（live_set ^ replay_set）后 sorted；
  两侧行数/通过状态/候选代码集完全一致（即 drift 为空）才报一致；
- 有 drift 即「规则漂移」：写 alert（level=WARN，source=compare），不静默；
- 返回 dict 键固定 date/live/replay/consistent/drift；render 为 MD 并排两栏。

与 lkl 的差异（全部是契约/IO 适配，不涉判定规则）：
1. db 入口：lkl.utils.db → emotion_core.utils.db（query_df / execute 同名同形，
   SQL 文本、参数顺序、返回值语义逐字保留）。
2. 回放侧延迟导入改指 emotion_core.algorithms.evaluate；其 replay 签名
   （start, end, min_days=None, as_of=None）与返回列（含 code / passed）与
   lkl.services.evaluate.replay 一致，故调用 `evaluate.replay(d, d, as_of=d)`
   原样，不加参数、不换口径。
3. logger 名 lkl.compare → emotion_core.compare。
4. 逐字保留、勿当遗漏的 lkl 行为：_drift_alert 直接
   `INSERT INTO alert (level, source, detail) VALUES ('WARN','compare',%s)`，
   不经 emotion_core.algorithms.alerts.record——后者会把 level 折成枚举并经
   record_dedup 逻辑，写入路径与文本不同；本模块口径是「与原实现逐字一致」，
   故保留直插（只把 db 入口换成本仓 utils.db.execute）。
5. 无缺失依赖降级：db 入口与 evaluate 均已在本仓就位，本模块无降级分支、
   无静默跳过路径（lkl 同：漂移必落 alert）。
"""
from __future__ import annotations

import logging
from datetime import date

from emotion_core.utils.db import execute, query_df

log = logging.getLogger("emotion_core.compare")


def _live_codes(d: date) -> list[tuple[str, str]]:
    """实盘侧：当日 BUY 信号（code, status），按 code 升序。"""
    df = query_df(
        "SELECT code, status FROM signal WHERE confirm_date=%s"
        " AND action='BUY' ORDER BY code", (d,))
    return [(r.code, r.status) for r in df.itertuples()]


def _replay_codes(d: date) -> list[tuple[str, bool]]:
    """回放侧：当日唯一候选+五条件判定（code, passed）。"""
    from emotion_core.algorithms import evaluate
    df = evaluate.replay(d, d, as_of=d)
    return [(r.code, bool(r.passed)) for r in df.itertuples()]


def _drift_alert(d: date, detail: str) -> None:
    """规则漂移 → alert 表（level=WARN，可 ack 追踪，不静默）。"""
    execute("INSERT INTO alert (level, source, detail) VALUES"
            " ('WARN','compare',%s)",
            (f"{d} 规则漂移：{detail}",))


def compare(d: date) -> dict:
    """单日对比：live/replay 代码集 + 状态差 + 漂移明细。"""
    live = _live_codes(d)
    replay = _replay_codes(d)
    live_set = {c for c, _ in live}
    replay_set = {c for c, _ in replay}
    drift = sorted(live_set ^ replay_set)
    passed_map = {c: p for c, p in replay}
    ret = {"date": d.isoformat(),
           "live": [{"code": c, "status": s} for c, s in live],
           "replay": [{"code": c, "passed": p} for c, p in replay],
           "consistent": not drift,
           "drift": drift}
    if drift:
        _drift_alert(d, f"实盘={sorted(live_set)} 回放={sorted(replay_set)}；"
                    f"差异={drift}；回放passed="
                    f"{ {c: passed_map.get(c) for c in drift} }")
    return ret


def render(rep: dict) -> str:
    """MD 渲染：并排两栏 + 漂移报警行。"""
    d = rep["date"]
    lines = [f"## 回测—实盘一致性 {d}", ""]
    lines.append("**实盘当日记录（signal 表）**")
    if rep["live"]:
        lines += [f"- {s['code']}（{s['status']}）" for s in rep["live"]]
    else:
        lines.append("- （无信号）")
    lines.append("")
    lines.append("**今日代码+数据重放（replay as_of=当日）**")
    if rep["replay"]:
        lines += [f"- {s['code']}（passed={s['passed']}）"
                  for s in rep["replay"]]
    else:
        lines.append("- （无候选/窗口 NONE）")
    lines.append("")
    if rep["consistent"]:
        lines.append("✅ 两侧一致：无规则漂移")
    else:
        lines.append(f"⚠ **规则漂移报警**：{rep['drift']}"
                     "——已插入 alert（lkl alerts 查看 / alert-ack 确认）")
    return "\n".join(lines) + "\n"
