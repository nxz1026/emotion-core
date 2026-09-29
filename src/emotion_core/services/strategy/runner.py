"""DSA 策略观察运行器：独立调用 LLM，结果只写 strategy_signal。

Native emotion-core implementation.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df

from .context import build_stock_context
from .loader import Skill, load_strategies
from .universe import build_universe

log = logging.getLogger(__name__)

STRATEGY_DIR = Path(__file__).resolve().parents[2] / "llm" / "strategies"
_CONTRACT = ('只输出 JSON：{"action":"BUY|WATCH|PASS","score":0-100,'
             '"confidence":0-1,"reason":"≤80字中文","evidence":{"k1":"v1"}}。')
_COLS = ["trade_date", "code", "strategy", "prompt_hash", "name", "action",
         "score", "confidence", "reason", "evidence", "model"]


def _existing(trade_date: date, code: str, strategy: str, prompt_hash: str) -> bool:
    frame = query_df(
        "SELECT 1 FROM strategy_signal WHERE trade_date=%s AND code=%s "
        "AND strategy=%s AND prompt_hash=%s LIMIT 1",
        (trade_date, code, strategy, prompt_hash))
    return not frame.empty


def _parse(text: str) -> dict | None:
    try:
        value = json.loads(text.strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("action") not in {"BUY", "WATCH", "PASS"}:
        return None
    score, confidence, reason, evidence = (value.get(key) for key in
                                            ("score", "confidence", "reason", "evidence"))
    if isinstance(score, bool):
        return None
    if isinstance(score, float):
        if not score.is_integer():
            return None
        score = int(score)
    elif not isinstance(score, int):
        return None
    if not 0 <= score <= 100:
        return None
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) \
            or not 0 <= confidence <= 1:
        return None
    if not isinstance(reason, str) or len(reason) > 80 or not isinstance(evidence, dict):
        return None
    return {"action": value["action"], "score": score, "confidence": confidence,
            "reason": reason, "evidence": evidence}


def _prompt(skill: Skill, code: str, context: str) -> tuple[list[dict], str]:
    text = (f"策略：{skill.display_name}（{skill.name}）\n{skill.instructions}\n"
            f"股票：{code}\n{context}")
    messages = [{"role": "system", "content": _CONTRACT}, {"role": "user", "content": text}]
    return messages, text


def _snapshot(trade_date: date, items: list[dict], skipped: list[dict]) -> Path:
    """写策略快照 JSON 到 reports/strategy_<date>.json。"""
    reports_dir = Path(CONFIG.STRATEGY_REPORTS_DIR)
    reports_dir.mkdir(parents=True, exist_ok=True)
    payload = {"date": trade_date.isoformat(),
               "generated_at": datetime.now(timezone.utc).isoformat(),
               "strategy_version": CONFIG.STRATEGY_VERSION,
               "items": sorted(items, key=lambda x: x["score"], reverse=True),
               "skipped": skipped}
    output = reports_dir / f"strategy_{trade_date.isoformat()}.json"
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(output)
    return output


def _stock_name(code: str) -> str | None:
    frame = query_df("SELECT name FROM stock_basic WHERE code=%s LIMIT 1", (code,))
    if frame.empty:
        return None
    value = frame.iloc[0].get("name")
    return str(value) if value is not None else None


def run_for_date(trade_date: date) -> int:
    """执行当日策略观察；关闭时零成本返回 0。"""
    if not CONFIG.STRATEGY_ENABLED:
        log.info("策略观察已关闭：%s", trade_date)
        return 0

    skills = [s for s in load_strategies(STRATEGY_DIR) if s.enabled]
    codes = build_universe(trade_date)

    if not skills or not codes:
        log.info("策略观察 %s: skills=%d codes=%d, 跳过", trade_date, len(skills), len(codes))
        _snapshot(trade_date, [], [])
        return 0

    from emotion_core.services.llm_backend import LLMClient
    client = LLMClient(CONFIG.STRATEGY_PROFILE,
                       max_tokens=CONFIG.STRATEGY_MAX_TOKENS)

    items, skipped, calls = [], [], 0
    for skill in skills:
        for code in codes:
            if calls >= CONFIG.STRATEGY_MAX_LLM:
                skipped.append({"strategy": skill.name, "code": code,
                                "reason": "达到LLM调用上限"})
                continue
            try:
                context = build_stock_context(trade_date, code)
            except Exception as exc:
                skipped.append({"strategy": skill.name, "code": code,
                                "reason": f"上下文构建失败:{str(exc)[:80]}"})
                continue
            messages, prompt = _prompt(skill, code, context)
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            if _existing(trade_date, code, skill.name, prompt_hash):
                continue
            calls += 1
            try:
                reply = client.chat(messages, purpose="strategy", json_mode=True)
            except Exception as exc:
                reason = f"LLM调用失败:{str(exc)[:100]}"
                log.warning("策略 LLM 调用失败：%s/%s：%s", skill.name, code, exc)
                skipped.append({"strategy": skill.name, "code": code, "reason": reason})
                continue
            parsed = _parse(reply.text)
            if parsed is None:
                reason = "返回不符合输出契约"
                log.warning("策略解析失败：%s/%s", skill.name, code)
                skipped.append({"strategy": skill.name, "code": code, "reason": reason})
                continue
            items.append({
                "strategy": skill.name, "code": code,
                "stock_name": _stock_name(code),
                "name": skill.display_name, **parsed, "model": reply.model})

    # Upsert to strategy_signal
    if items:
        _save_batch(trade_date, items)

    snap_path = _snapshot(trade_date, items, skipped)
    log.info("策略观察 %s: 调用 %d, 入库 %d, 跳过 %d, 快照 %s",
             trade_date, calls, len(items), len(skipped), snap_path)
    return len(items)


def _save_batch(trade_date: date, items: list[dict]) -> int:
    """批量 UPSERT 策略信号到 strategy_signal。"""
    import psycopg
    rows = []
    for it in items:
        evidence = json.dumps(it.get("evidence", {}), ensure_ascii=False)
        prompt_hash = hashlib.sha256(
            f"{it['strategy']}:{it['code']}:{trade_date.isoformat()}".encode()
        ).hexdigest()
        rows.append(
            (trade_date, it["code"], it["strategy"], prompt_hash,
             it.get("name", ""), it["action"], it["score"],
             it["confidence"], it.get("reason", ""),
             evidence, it.get("model", "")))
    # Use individual upserts via execute
    sql = ("INSERT INTO strategy_signal (trade_date,code,strategy,prompt_hash,name,"
           "action,score,confidence,reason,evidence,model) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
           "ON CONFLICT (trade_date,code,strategy,prompt_hash) DO UPDATE SET "
           "action=EXCLUDED.action, score=EXCLUDED.score, confidence=EXCLUDED.confidence,"
           "reason=EXCLUDED.reason, evidence=EXCLUDED.evidence, model=EXCLUDED.model,"
           "created_at=now()")
    from emotion_core.utils.db import execute
    for row in rows:
        execute(sql, row)
    return len(rows)
