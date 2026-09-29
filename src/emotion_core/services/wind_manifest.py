"""Wind 调用原始响应留存 + 配额台账。

将 WindClient 的每次调用落库到 ops_raw_manifest + ops_quota_ledger，
用于事后溯源与配额核对。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Mapping

from emotion_core.utils.db import connect
from emotion_core.services.wind_client import WindCall

logger = logging.getLogger(__name__)


def record_call(call: WindCall) -> None:
    """将一次 Wind 调用落库到 ops_raw_manifest 和 ops_quota_ledger。"""
    import json

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ops_raw_manifest
                   (server_type, tool_name, params, raw_response, elapsed_ms, ok, code)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    call.server_type,
                    call.tool_name,
                    json.dumps(call.params, ensure_ascii=False),
                    call.raw_response,
                    call.elapsed_ms,
                    call.ok,
                    call.code,
                ),
            )
            cur.execute(
                """INSERT INTO ops_quota_ledger
                   (server_type, tool_name, params_hash, ok, code, elapsed_ms)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    call.server_type,
                    call.tool_name,
                    _hash_params(call.params),
                    call.ok,
                    call.code,
                    call.elapsed_ms,
                ),
            )


def _hash_params(params: Mapping[str, Any]) -> str:
    import hashlib
    canonical = json.dumps(params, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
