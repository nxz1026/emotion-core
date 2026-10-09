"""DSA 策略观察运行器：独立调用 LLM，结果只写 strategy_signal。

Native emotion-core implementation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import query_df

from .context import build_stock_context
from .loader import Skill, load_strategies
from .universe import build_universe, filter_codes

log = logging.getLogger(__name__)

STRATEGY_DIR = Path(__file__).resolve().parents[2] / "llm" / "strategies"

# ── 调用侧定速 / 退避 / 熔断旋钮 ───────────────────────────────
# 刻意放模块级常量而非 utils/config.py 的 Config：config_hash() 哈希
# asdict(CONFIG) 的全字段，往 Config 里加键会让 pipeline_state / signal /
# eval_result 的策略指纹平白换代。env 同名 EC_STRATEGY_* 可覆盖。
# 另：agnes 单后端时 llm_backend 不重试 429（allow_status_retry 需链长>1），
# 客户端自带的 max_retry 对限流失效，故这一层防护必须落在调用侧。
CALL_INTERVAL: float = float(os.environ.get("EC_STRATEGY_CALL_INTERVAL", "1.5"))
MAX_ATTEMPTS: int = int(os.environ.get("EC_STRATEGY_MAX_ATTEMPTS", "3"))
RETRY_BASE_DELAY: float = float(os.environ.get("EC_STRATEGY_RETRY_BASE_DELAY", "2.0"))
# 2026-10-09 日志巡检 D：主循环结束后的「失败补跑」退避基数与轮间隔。
# 瞬时故障（尤其限流）在第一轮退避后往往需要更长冷却，故补跑用更大的基数。
RETRY_ROUND_DELAY: float = float(os.environ.get("EC_STRATEGY_RETRY_DELAY", "10.0"))
MAX_CONSECUTIVE_FAILURES: int = int(os.environ.get("EC_STRATEGY_MAX_CONSECUTIVE_FAILURES", "8"))
_CONTRACT = (
    '只输出 JSON：{"action":"BUY|WATCH|PASS","score":0-100,'
    '"confidence":0-1,"reason":"≤80字中文","evidence":{"k1":"v1"}}。'
)
_COLS = [
    "trade_date",
    "code",
    "strategy",
    "prompt_hash",
    "name",
    "action",
    "score",
    "confidence",
    "reason",
    "evidence",
    "model",
]


def _existing(trade_date: date, code: str, strategy: str, prompt_hash: str) -> bool:
    frame = query_df(
        "SELECT 1 FROM strategy_signal WHERE trade_date=%s AND code=%s "
        "AND strategy=%s AND prompt_hash=%s LIMIT 1",
        (trade_date, code, strategy, prompt_hash),
    )
    return not frame.empty


def _retryable(exc: Exception) -> bool:
    """限流与瞬时故障可退避重试；鉴权失败与输出契约错误不重试。"""
    import httpx

    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


def _chat_with_retry(client: Any, messages: list[dict], base_delay: float | None = None) -> Any:
    """调用 LLM，对 429 与 5xx 做指数退避。

    agnes 单后端时 `llm_backend._chat_locked` 传入的
    `allow_status_retry=len(chain) > 1` 为假，客户端自带的 max_retry 对
    429 不生效，故限流防护落在调用侧。

    ``base_delay`` 为 ``None`` 时用 ``RETRY_BASE_DELAY``；失败补跑一轮
    传入更长的 ``RETRY_ROUND_DELAY``（2026-10-09 日志巡检 D）。
    """
    attempts = max(1, MAX_ATTEMPTS)
    delay_base = RETRY_BASE_DELAY if base_delay is None else base_delay
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return client.chat(messages, purpose="strategy", json_mode=True)
        except Exception as exc:
            last = exc
            if attempt + 1 >= attempts or not _retryable(exc):
                raise
            delay = delay_base * (2**attempt)
            log.warning(
                "策略 LLM 限流，%.1fs 后重试（%d/%d）：%s", delay, attempt + 1, attempts, exc
            )
            time.sleep(delay)
    assert last is not None
    raise last


def _parse(text: str) -> dict | None:
    try:
        value = json.loads(text.strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("action") not in {"BUY", "WATCH", "PASS"}:
        return None
    score, confidence, reason, evidence = (
        value.get(key) for key in ("score", "confidence", "reason", "evidence")
    )
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
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        return None
    if not isinstance(reason, str) or len(reason) > 80 or not isinstance(evidence, dict):
        return None
    return {
        "action": value["action"],
        "score": score,
        "confidence": confidence,
        "reason": reason,
        "evidence": evidence,
    }


def _prompt(skill: Skill, code: str, context: str) -> tuple[list[dict], str]:
    text = (
        f"策略：{skill.display_name}（{skill.name}）\n{skill.instructions}\n股票：{code}\n{context}"
    )
    messages = [{"role": "system", "content": _CONTRACT}, {"role": "user", "content": text}]
    return messages, text


def _prepare_call(trade_date: date, skill: Skill, code: str) -> tuple[list[dict], str]:
    """为一个 ``(skill, code)`` 组合建上下文与 prompt，返回 (messages, prompt_hash)。

    抛出的异常由调用方转成 skipped 原因（首轮为「上下文构建失败」，补跑轮
    只记日志、保留首轮 skipped）。
    """
    context = build_stock_context(trade_date, code)
    messages, prompt = _prompt(skill, code, context)
    return messages, hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _drop_skipped(skipped: list[dict], strategy: str, code: str) -> None:
    """补跑成功后移除首轮为该组合留下的 skipped 记录（「仍失败保持 skipped」）。"""
    for index in range(len(skipped) - 1, -1, -1):
        entry = skipped[index]
        if entry.get("strategy") == strategy and entry.get("code") == code:
            del skipped[index]
            return


def _reason_distribution(skipped: list[dict]) -> str:
    """把 skipped 的原因按**基数**聚合，按次数降序，供汇总行可观测。

    原因形如 ``"LLM调用失败:429 ..."`` / ``"上下文构建失败:..."``，取冒号前的
    基数归组（否则每个异常文本各自成一类，看不出分布）。
    """
    counts = Counter(str(entry.get("reason", "")).split(":", 1)[0] for entry in skipped)
    return ", ".join(f"{reason} {count}" for reason, count in counts.most_common()) or "无"


def _pair_key(entry: dict) -> tuple[str, str]:
    """快照条目的组合键 ``(code, strategy)``。"""
    return (str(entry.get("code", "")), str(entry.get("strategy", "")))


def _merge_snapshot(
    output: Path, items: list[dict], skipped: list[dict]
) -> tuple[list[dict], list[dict]]:
    """把旧快照里本次没跑到的组合并回来（``--codes`` 补跑专用）。

    补跑是**局部**运行：它只知道本次那几个 code 的结果。若直接覆盖
    ``reports/strategy_<date>.json``，当日其余 code 的 items 与 skipped
    分布（配额截断、上游失败的分布）就一起没了。这里以 ``(code, strategy)``
    为键：本次跑到的组合（无论成功还是仍失败）以本次为准，其余原样保留。

    读不出旧快照（缺文件 / 坏 JSON）时按「没有旧快照」处理，不抛。
    """
    try:
        old = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return items, skipped
    if not isinstance(old, dict):
        return items, skipped
    fresh = {_pair_key(entry) for entry in [*items, *skipped]}
    old_items = [it for it in old.get("items", []) if _pair_key(it) not in fresh]
    old_skipped = [entry for entry in old.get("skipped", []) if _pair_key(entry) not in fresh]
    return old_items + items, old_skipped + skipped


def _snapshot(
    trade_date: date, items: list[dict], skipped: list[dict], *, merge: bool = False
) -> Path:
    """写策略快照 JSON 到 reports/strategy_<date>.json。

    ``merge=True``（显式 ``codes`` 补跑）时先与旧快照合并，见
    :func:`_merge_snapshot`；日更（``codes=None``）仍是整日覆盖写。
    """
    reports_dir = Path(CONFIG.STRATEGY_REPORTS_DIR)
    reports_dir.mkdir(parents=True, exist_ok=True)
    output = reports_dir / f"strategy_{trade_date.isoformat()}.json"
    if merge:
        items, skipped = _merge_snapshot(output, items, skipped)
    payload = {
        "date": trade_date.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy_version": CONFIG.STRATEGY_VERSION,
        "items": sorted(items, key=lambda x: x["score"], reverse=True),
        "skipped": skipped,
    }
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


def _item(skill: Skill, code: str, parsed: dict, reply: Any, prompt_hash: str) -> dict:
    """组装一条待入库信号（首轮与补跑轮同构）。"""
    return {
        "strategy": skill.name,
        "code": code,
        "stock_name": _stock_name(code),
        "name": skill.display_name,
        **parsed,
        "model": reply.model,
        "prompt_hash": prompt_hash,
    }


def run_for_date(trade_date: date, codes: list[str] | None = None) -> int:
    """执行当日策略观察；关闭时零成本返回 0。

    2026-10-09 日志巡检 D：显式传 ``codes`` 时跳过 ``build_universe``，直接
    以该清单为候选（仍按 ``BOARD_PREFIXES`` 过滤、zfill(6)、去重），用于
    补跑历史缺口；``codes=None`` 才走「手动自选 + 热门池」。

    主循环结束后自动补跑一轮：把本轮 LLM 失败 / 输出契约解析失败的
    ``(skill, code)`` 收集起来，配额与熔断仍有空间时以更长的退避
    （``RETRY_ROUND_DELAY``）重跑；成功则正常入库并计入配额，仍失败保持
    首轮的 skipped 记录。
    """
    if not CONFIG.STRATEGY_ENABLED:
        log.info("策略观察已关闭：%s", trade_date)
        return 0

    skills = [s for s in load_strategies(STRATEGY_DIR) if s.enabled]
    explicit_codes = codes is not None
    if codes is None:
        codes = build_universe(trade_date)
    else:
        codes = filter_codes(codes)

    if not skills or not codes:
        log.info("策略观察 %s: skills=%d codes=%d, 跳过", trade_date, len(skills), len(codes))
        _snapshot(trade_date, [], [], merge=explicit_codes)
        return 0

    from emotion_core.services.llm_backend import LLMClient

    client = LLMClient(CONFIG.STRATEGY_PROFILE, max_tokens=CONFIG.STRATEGY_MAX_TOKENS)

    items, skipped, calls = [], [], 0
    failed_attempts = 0  # 仅用于调用间定速；最终「失败」按 skipped 统计
    consecutive_failures = 0
    retry_pairs: list[tuple[Skill, str]] = []
    # 交错遍历（代码优先、策略轮转）：原 `for skill: for code:` 会让首个策略
    # 独吞 STRATEGY_MAX_LLM 配额，其余策略一次都轮不到。
    pairs = [(skill, code) for code in codes for skill in skills]
    for skill, code in pairs:
        if calls >= CONFIG.STRATEGY_MAX_LLM:
            skipped.append({"strategy": skill.name, "code": code, "reason": "达到LLM调用上限"})
            continue
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            skipped.append({"strategy": skill.name, "code": code, "reason": "连续调用失败熔断"})
            continue
        try:
            messages, prompt_hash = _prepare_call(trade_date, skill, code)
        except Exception as exc:
            skipped.append(
                {"strategy": skill.name, "code": code, "reason": f"上下文构建失败:{str(exc)[:80]}"}
            )
            continue
        if _existing(trade_date, code, skill.name, prompt_hash):
            continue
        if calls or failed_attempts:  # 调用间定速，避免触发上游限流
            time.sleep(CALL_INTERVAL)
        try:
            reply = _chat_with_retry(client, messages)
        except Exception as exc:
            failed_attempts += 1
            consecutive_failures += 1
            reason = f"LLM调用失败:{str(exc)[:100]}"
            log.warning("策略 LLM 调用失败：%s/%s：%s", skill.name, code, exc)
            skipped.append({"strategy": skill.name, "code": code, "reason": reason})
            retry_pairs.append((skill, code))  # 失败不占配额，留待补跑
            continue
        calls += 1  # 只有成功的调用才计入配额
        consecutive_failures = 0
        parsed = _parse(reply.text)
        if parsed is None:
            log.warning("策略解析失败：%s/%s", skill.name, code)
            skipped.append({"strategy": skill.name, "code": code, "reason": "返回不符合输出契约"})
            retry_pairs.append((skill, code))
            continue
        items.append(_item(skill, code, parsed, reply, prompt_hash))

    # ── 失败补跑（2026-10-09 日志巡检 D）─────────────────────────────
    # 仅在「有可补组合 + 配额未满 + 熔断未开」时执行；已入库组合不重复。
    if (
        retry_pairs
        and calls < CONFIG.STRATEGY_MAX_LLM
        and consecutive_failures < MAX_CONSECUTIVE_FAILURES
    ):
        log.info(
            "策略观察 %s: 失败补跑 %d 组（退避基数 %.1fs）",
            trade_date,
            len(retry_pairs),
            RETRY_ROUND_DELAY,
        )
        time.sleep(RETRY_ROUND_DELAY)
        for skill, code in retry_pairs:
            if calls >= CONFIG.STRATEGY_MAX_LLM:
                log.info("策略观察 %s: 配额已满，失败补跑提前结束", trade_date)
                break
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.warning("策略观察 %s: 连续失败熔断，失败补跑提前结束", trade_date)
                break
            try:
                messages, prompt_hash = _prepare_call(trade_date, skill, code)
            except Exception as exc:
                log.warning("策略补跑上下文构建失败：%s/%s：%s", skill.name, code, exc)
                continue
            if _existing(trade_date, code, skill.name, prompt_hash):
                _drop_skipped(skipped, skill.name, code)
                continue
            time.sleep(CALL_INTERVAL)
            try:
                reply = _chat_with_retry(client, messages, base_delay=RETRY_ROUND_DELAY)
            except Exception as exc:
                failed_attempts += 1
                consecutive_failures += 1
                log.warning("策略补跑仍失败：%s/%s：%s", skill.name, code, exc)
                continue  # 首轮 skipped 保留
            calls += 1
            consecutive_failures = 0
            parsed = _parse(reply.text)
            if parsed is None:
                log.warning("策略补跑解析仍失败：%s/%s", skill.name, code)
                continue  # 首轮 skipped 保留
            items.append(_item(skill, code, parsed, reply, prompt_hash))
            _drop_skipped(skipped, skill.name, code)

    # Upsert to strategy_signal
    if items:
        _save_batch(trade_date, items)

    # 「失败」= 最终仍未覆盖的 LLM/契约失败组合（补跑成功者已移出 skipped）。
    failed = sum(
        1
        for entry in skipped
        if str(entry["reason"]).startswith(("LLM调用失败", "返回不符合输出契约"))
    )
    snap_path = _snapshot(trade_date, items, skipped, merge=explicit_codes)
    log.info(
        "策略观察 %s: 调用 %d, 入库 %d, 失败 %d, 跳过 %d（%s）, 快照 %s",
        trade_date,
        calls,
        len(items),
        failed,
        len(skipped),
        _reason_distribution(skipped),
        snap_path,
    )
    return len(items)


def _signal_row(trade_date: date, it: dict) -> tuple:
    """构造 strategy_signal 一行。

    prompt_hash 直接取自调用侧（sha256(prompt 文本)），与 `_existing` 去重
    同源；入库另算一套 sha256(strategy:code:date) 会让去重恒不命中。
    """
    return (
        trade_date,
        it["code"],
        it["strategy"],
        it["prompt_hash"],
        it.get("name", ""),
        it["action"],
        it["score"],
        it["confidence"],
        it.get("reason", ""),
        json.dumps(it.get("evidence", {}), ensure_ascii=False),
        it.get("model", ""),
    )


def _save_batch(trade_date: date, items: list[dict]) -> int:
    """批量 UPSERT 策略信号到 strategy_signal。"""
    rows = [_signal_row(trade_date, it) for it in items]
    # Use individual upserts via execute
    sql = (
        "INSERT INTO strategy_signal (trade_date,code,strategy,prompt_hash,name,"
        "action,score,confidence,reason,evidence,model) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (trade_date,code,strategy,prompt_hash) DO UPDATE SET "
        "action=EXCLUDED.action, score=EXCLUDED.score, confidence=EXCLUDED.confidence,"
        "reason=EXCLUDED.reason, evidence=EXCLUDED.evidence, model=EXCLUDED.model,"
        "created_at=now()"
    )
    from emotion_core.utils.db import execute

    for row in rows:
        execute(sql, row)
    return len(rows)
