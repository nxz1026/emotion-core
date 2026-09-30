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
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock

from emotion_core.utils.db import query_df as _query_df

log = logging.getLogger("emotion_core.trade_api")

# ── paths ────────────────────────────────────────────────────
TRADE_DIR = Path("/home/ubuntu/trade")
STATE_FILE = TRADE_DIR / "state.json"

# ── state management (thread-safe) ───────────────────────────
_state_lock = Lock()


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"decisions": {}, "processed": []}


def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _query_signals(for_date: date) -> list[dict]:
    """Query strategy_signal BUY signals for a given date."""
    df = _query_df(
        "SELECT code, strategy, action, score, confidence, reason "
        "FROM strategy_signal "
        "WHERE trade_date=%s AND action=%s "
        "ORDER BY score DESC",
        (for_date, "BUY"),
    )
    return df.to_dict("records")


def handle_trade_decisions(query_str: str) -> tuple[dict, int]:
    """GET /api/trade/decisions?date=YYYY-MM-DD"""
    from urllib.parse import parse_qs
    q = parse_qs(query_str)
    for_date_str = q.get("date", [date.today().isoformat()])[0]

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
        for row in signals:
            actions.append({
                "code": str(row["code"]).zfill(6),
                "action": "BUY",
                "exec": "OPEN_POS",
                "volume": 100,
                "reason": row.get("reason", ""),
            })

        batch_id = str(uuid.uuid4())
        payload = {"batch_id": batch_id, "for_date": for_date_str, "actions": actions}
        state["decisions"][for_date_str] = payload
        _save_state(state)

        log.info("generated decisions for %s (batch=%s, %d actions)",
                 for_date_str, batch_id, len(actions))
        return payload, 200


def handle_trade_results_post(body: bytes) -> tuple[dict, int]:
    """POST /api/trade/results"""
    log.info("POST /api/trade/results: body_len=%d, body_prefix=%r", len(body), body[:200])
    try:
        data = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        log.warning("JSON decode failed: %s (body_len=%d)", e, len(body))
        return {"error": f"invalid JSON: {e}"}, 400

    if not data:
        return {"error": "empty body"}, 400

    batch_id = data.get("batch_id", "")
    for_date = data.get("for_date", "")
    trades = data.get("trades", [])

    if not batch_id or not for_date:
        return {"error": "missing batch_id or for_date"}, 400

    with _state_lock:
        state = _load_state()

        if batch_id in state.get("processed", []):
            log.info("batch %s already processed, idempotent ok", batch_id)
            return {"status": "ok", "batch_id": batch_id, "note": "idempotent"}, 200

        # Write results file
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        results_file = TRADE_DIR / f"results_{for_date}_{ts}.json"
        results_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Mark processed
        state.setdefault("processed", []).append(batch_id)
        _save_state(state)

    log.info("results accepted: batch=%s, %d trades → %s", batch_id, len(trades), results_file.name)
    return {"status": "ok", "batch_id": batch_id}, 200


def handle_trade_results_get(query_str: str) -> tuple[dict, int]:
    """GET /api/trade/results?date=YYYY-MM-DD"""
    from urllib.parse import parse_qs
    q = parse_qs(query_str)
    for_date = q.get("date", [date.today().isoformat()])[0]
    results = sorted(TRADE_DIR.glob(f"results_{for_date}_*.json"), reverse=True)
    if not results:
        return {"for_date": for_date, "trades": []}, 200
    data = json.loads(results[0].read_text(encoding="utf-8"))
    return data, 200


def handle_trade_health() -> tuple[dict, int]:
    """GET /api/trade/health"""
    return {"ok": True, "service": "emotion_core_trade_api"}, 200
