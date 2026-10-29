"""Trade API: oracle-side HTTP service for LKL-Trade decisions/results exchange.

Integrated into emotion-core presentation layer (port 8098).
Mounted under /api/trade/ by nginx proxy.

Endpoints:
  GET  /api/trade/decisions?date=YYYY-MM-DD  → {batch_id, for_date, actions[]}
  POST /api/trade/results                    → {status: "ok", batch_id: "..."}
  GET  /api/trade/results?date=YYYY-MM-DD    → latest results JSON
  GET  /api/trade/health                     → {ok: true}
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Lock

from emotion_core.utils.dates import today_sh
from emotion_core.utils.db import query_df as _query_df

# POST 请求体上限：Trade API 只收 batch 结果，单批远小于此；封顶防止内存被打满。
MAX_BODY_BYTES = 64 * 1024
# 仅允许 ISO 日期（YYYY-MM-DD），避免把任意字符串拼进 glob/文件名。
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid_date(s: str) -> bool:
    return bool(_DATE_RE.match(s))


log = logging.getLogger("emotion_core.trade_api")

# ── paths ────────────────────────────────────────────────────
TRADE_DIR = Path("/home/ubuntu/trade")
STATE_FILE = TRADE_DIR / "state.json"

# ── state management (thread-safe) ───────────────────────────
_state_lock = Lock()


def _load_state() -> dict:
    """读取 state.json；任何损坏/缺键都回落到干净默认，不让一次坏文件拖垮整个接口。"""
    if not STATE_FILE.exists():
        return {"decisions": {}, "processed": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        log.warning("state.json 损坏，回落到默认：%s", e)
        return {"decisions": {}, "processed": []}
    if not isinstance(data, dict):
        log.warning("state.json 不是对象，回落到默认")
        return {"decisions": {}, "processed": []}
    data.setdefault("decisions", {})
    data.setdefault("processed", [])
    return data


def _save_state(state: dict) -> None:
    """原子写：先写同目录临时文件再 os.replace，避免进程崩溃留下半截 JSON。

    临时文件与 STATE_FILE 同目录（os.replace 要求同文件系统），STATE_FILE 被
    测试或部署改到别处时也能正确原子替换。
    """
    parent = STATE_FILE.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".state-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        log.error("写 state.json 失败：%s", e)
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


def _query_signals(for_date: date) -> list[dict]:
    """Query strategy_signal BUY signals for a given date — **每只票只出一条**。

    2026-10-09 日志巡检（用户拍板「修」）：``strategy_signal`` 的唯一键是
    ``(trade_date, code, strategy, prompt_hash)``，同一 ``(code, strategy)``
    在上下文变化后重跑会**追加新版本行**（版本行本身是设计），同一只票还可能
    有多条策略同时给 BUY ⇒ 原来直接按 ``score DESC`` 取全部行，会让同一 code
    出现多次，被 LKL-Trade 当成多笔 ``OPEN_POS``（2026-10-09 的缓存里就是
    13 条 action / 5 个 code）。

    这里按 **code 去重**：同一只票只取它当日分数最高的一条 BUY；同分取
    ``created_at DESC`` 的最新版本，再同则用 ``prompt_hash DESC`` 兜底，保证
    重复查询结果确定。外层再按 ``score DESC, code`` 排序。
    """
    df = _query_df(
        "SELECT code, strategy, action, score, confidence, reason FROM ("
        "  SELECT DISTINCT ON (code) code, strategy, action, score, confidence, reason,"
        "         created_at, prompt_hash "
        "  FROM strategy_signal WHERE trade_date=%s AND action=%s "
        "  ORDER BY code, score DESC, created_at DESC, prompt_hash DESC"
        ") AS best ORDER BY score DESC, code",
        (for_date, "BUY"),
    )
    return df.to_dict("records")


def handle_trade_decisions(query_str: str) -> tuple[dict, int]:
    """GET /api/trade/decisions?date=YYYY-MM-DD"""
    from urllib.parse import parse_qs

    q = parse_qs(query_str)
    for_date_str = q.get("date", [today_sh().isoformat()])[0]

    try:
        for_date = date.fromisoformat(for_date_str)
    except ValueError:
        return {"error": f"invalid date: {for_date_str}"}, 400

    with _state_lock:
        state = _load_state()

        # Return cached decisions if available
        if for_date_str in state["decisions"]:
            d = state["decisions"][for_date_str]
            log.info("serving cached decisions for %s (batch=%s)", for_date_str, d["batch_id"])
            return d, 200

        # Generate fresh decisions from strategy_signal table
        signals = _query_signals(for_date)

        if not signals:
            log.info("no BUY signals for %s", for_date_str)
            return {"batch_id": "", "for_date": for_date_str, "actions": []}, 200

        actions = []
        seen_codes: set[str] = set()
        for row in signals:
            code = str(row["code"]).zfill(6)
            if code in seen_codes:
                # 兜底：_query_signals 已按 code 去重；这里再拦一层，
                # 防止将来有人改查询时同一只票又变成两笔 OPEN_POS。
                log.warning(
                    "duplicate BUY signal for %s on %s, keeping the first", code, for_date_str
                )
                continue
            seen_codes.add(code)
            actions.append(
                {
                    "code": code,
                    "action": "BUY",
                    "exec": "OPEN_POS",
                    "volume": 100,
                    "reason": row.get("reason", ""),
                }
            )

        batch_id = str(uuid.uuid4())
        payload = {"batch_id": batch_id, "for_date": for_date_str, "actions": actions}
        state["decisions"][for_date_str] = payload
        _save_state(state)

        log.info(
            "generated decisions for %s (batch=%s, %d actions)",
            for_date_str,
            batch_id,
            len(actions),
        )
        return payload, 200


def handle_trade_results_post(body: bytes) -> tuple[dict, int]:
    """POST /api/trade/results"""
    log.info("POST /api/trade/results: body_len=%d, body_prefix=%r", len(body), body[:200])
    if len(body) > MAX_BODY_BYTES:
        return {"error": f"body too large: {len(body)} > {MAX_BODY_BYTES}"}, 413
    try:
        data = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        log.warning("JSON decode failed: %s (body_len=%d)", e, len(body))
        return {"error": f"invalid JSON: {e}"}, 400

    if not isinstance(data, dict) or not data:
        return {"error": "empty body"}, 400

    batch_id = data.get("batch_id", "")
    for_date = data.get("for_date", "")
    trades = data.get("trades", [])

    if not batch_id or not for_date:
        return {"error": "missing batch_id or for_date"}, 400
    if not _valid_date(for_date):
        return {"error": f"invalid for_date: {for_date}"}, 400

    with _state_lock:
        state = _load_state()

        if batch_id in state.get("processed", []):
            log.info("batch %s already processed, idempotent ok", batch_id)
            return {"status": "ok", "batch_id": batch_id, "note": "idempotent"}, 200

        # Write results file
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        results_file = TRADE_DIR / f"results_{for_date}_{ts}.json"
        results_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        # Mark processed
        state.setdefault("processed", []).append(batch_id)
        _save_state(state)

    log.info("results accepted: batch=%s, %d trades → %s", batch_id, len(trades), results_file.name)
    return {"status": "ok", "batch_id": batch_id}, 200


def handle_trade_results_get(query_str: str) -> tuple[dict, int]:
    """GET /api/trade/results?date=YYYY-MM-DD"""
    from urllib.parse import parse_qs

    q = parse_qs(query_str)
    for_date = q.get("date", [today_sh().isoformat()])[0]
    if not _valid_date(for_date):
        return {"error": f"invalid date: {for_date}"}, 400
    results = sorted(TRADE_DIR.glob(f"results_{for_date}_*.json"), reverse=True)
    if not results:
        return {"for_date": for_date, "trades": []}, 200
    data = json.loads(results[0].read_text(encoding="utf-8"))
    return data, 200


def handle_trade_health() -> tuple[dict, int]:
    """GET /api/trade/health"""
    return {"ok": True, "service": "emotion_core_trade_api"}, 200
