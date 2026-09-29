"""T11 LLM 层（E4）：OpenAI 兼容协议薄封装 + profile 参数 + 调用审计日志。

架构红线：信号链路（emotion/ladder/entry/exit）禁止 import 本模块（test_arch 守卫）。
参数三级覆盖：LLM_PROFILES[profile] ← 环境变量 LKL_LLM_* ← 调用 kwargs。
端点实测坑适配（PLAN §E4.1）：json 围栏剥离 / timeout 按档 30~180s /
仅超时与5xx重试（4xx 直抛）/ 失败也写 llm_call_log(status=ERROR)。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass

import httpx

from emotion_core.services.llm_backend import (
    LLM_PROFILES,
    LLMNotConfigured,
    LLMChainExhausted,
    resolve_chain,
    resolve_key,
    fallback_error,
    short_error,
)
from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import execute


def _cfg(name, default):
    """读 CONFIG 项；emotion-core 尚未收录的键取 lkl config.py 原值。"""
    return getattr(CONFIG, name, default)


LLM_ENABLED = _cfg("LLM_ENABLED", os.environ.get(
    "LKL_LLM_ENABLED", "").lower() in ("1", "true", "yes"))


@dataclass(frozen=True)
class LLMParams:
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    top_p: float
    timeout: float
    max_retry: int


@dataclass
class LLMReply:
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    model: str


_ENV_MAP = {"LKL_LLM_MODEL": "model", "LKL_LLM_TEMPERATURE": "temperature",
            "LKL_LLM_MAX_TOKENS": "max_tokens", "LKL_LLM_TOP_P": "top_p",
            "LKL_LLM_TIMEOUT": "timeout", "LKL_LLM_BASE_URL": "base_url"}


def _cast(value: str, like):
    if isinstance(like, bool):
        return value.lower() in ("1", "true")
    if isinstance(like, int):
        return int(value)
    if isinstance(like, float):
        return float(value)
    return value


def resolve_params(profile: str = "default", overrides: dict | None = None) -> LLMParams:
    """三级参数合并：profile ← env ← kwargs。"""
    base = dict(LLM_PROFILES.get(profile, LLM_PROFILES["default"]))
    for env, key in _ENV_MAP.items():
        raw = os.environ.get(env)
        if raw is not None and key in base:
            base[key] = _cast(raw, base[key])
    base.update({k: v for k, v in (overrides or {}).items() if k in base})
    return LLMParams(**base)


def resolve_api_key() -> str:
    """兼容旧入口：解析 default profile 的密钥。"""
    return resolve_key("default")


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def strip_fence(text: str) -> str:
    """剥离 ```json 围栏（sensenova 端点实测行为）。"""
    m = _FENCE.match(text or "")
    return m.group(1) if m else text


class LLMClient:
    """OpenAI 兼容客户端，按 profile 链逐后端回退。"""

    def __init__(self, profile: str = "default", **overrides) -> None:
        self.profile = profile
        self._overrides = overrides
        self._lock = threading.Lock()
        self.params = resolve_params(profile, overrides)
        self.api_key = resolve_key(profile)

    def complete(self, prompt: str, system: str | None = None,
                 purpose: str = "complete") -> str:
        msgs = ([{"role": "system", "content": system}] if system else []) \
            + [{"role": "user", "content": prompt}]
        return self.chat(msgs, purpose=purpose).text

    def chat(self, messages: list[dict], purpose: str = "chat",
             json_mode: bool = False) -> LLMReply:
        with self._lock:
            original = (self.profile, self.params, self.api_key)
            try:
                return self._chat_locked(messages, purpose, json_mode)
            finally:
                self.profile, self.params, self.api_key = original

    def _chat_locked(self, messages: list[dict], purpose: str,
                     json_mode: bool) -> LLMReply:
        chain = resolve_chain(self.profile)
        self._request_profile = self.profile
        failures: list[str] = []
        for backend in chain:
            t0 = time.monotonic()
            try:
                self._select_backend(backend, backend == self._request_profile)
                payload = self._payload(messages, json_mode)
                data = self._post(payload, allow_status_retry=len(chain) > 1)
                return self._reply(data, purpose, self._latency_ms(t0))
            except LLMNotConfigured:
                err = "LLMNotConfigured"
                failures.append(f"{backend}: {err}")
                self._log_call(None, purpose, self._latency_ms(t0),
                               status="FAILOVER", err=err)
            except Exception as exc:  # noqa: BLE001
                err = short_error(exc, (self.api_key,))
                failures.append(f"{backend}: {err}")
                self._log_call(None, purpose, self._latency_ms(t0),
                               status="ERROR" if len(chain) == 1 else "FAILOVER",
                               err=err)
                if len(chain) == 1 or not fallback_error(exc):
                    raise
        raise LLMChainExhausted("LLM 回退链耗尽：" + "；".join(failures))

    @staticmethod
    def _latency_ms(t0: float) -> int:
        return int((time.monotonic() - t0) * 1000)

    def _payload(self, messages: list[dict], json_mode: bool) -> dict:
        p = self.params
        payload = {"model": p.model, "messages": messages,
                   "temperature": p.temperature, "max_tokens": p.max_tokens,
                   "top_p": p.top_p}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _select_backend(self, profile: str, is_request: bool = True) -> None:
        self.profile = profile
        overrides = self._overrides if is_request else {
            "max_retry": self._overrides["max_retry"]
        } if "max_retry" in self._overrides else {}
        self.params = resolve_params(profile, overrides)
        self.api_key = resolve_key(profile)

    def _reply(self, data: dict, purpose: str, latency_ms: int) -> LLMReply:
        msg = data["choices"][0]["message"]
        usage = data.get("usage") or {}
        reply = LLMReply(strip_fence(msg.get("content") or ""),
                         int(usage.get("prompt_tokens", 0)),
                         int(usage.get("completion_tokens", 0)), latency_ms,
                         data.get("model", self.params.model))
        self._log_call(reply, purpose, reply.latency_ms, status="OK")
        return reply

    def _post(self, payload: dict, allow_status_retry: bool = True) -> dict:
        """POST /chat/completions；可回退状态按当前后端重试。"""
        url = self.params.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last: Exception = RuntimeError("unreachable")
        for attempt in range(self.params.max_retry + 1):
            try:
                resp = httpx.post(url, json=payload, headers=headers,
                                  timeout=self.params.timeout)
                if resp.status_code >= 500:
                    raise httpx.TransportError(f"5xx {resp.status_code}")
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if not allow_status_retry or not fallback_error(exc):
                    raise
                last = exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
            if attempt < self.params.max_retry:
                time.sleep(2 ** attempt)
        raise last

    def _log_call(self, reply: LLMReply | None, purpose: str, latency_ms: int,
                  status: str, err: str = "") -> None:
        """写 llm_call_log；DB 故障静默降级，绝不影响主流程。"""
        try:
            r = reply
            execute(
                "INSERT INTO llm_call_log (profile, model, purpose,"
                " prompt_tokens, completion_tokens, latency_ms, status)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (self.profile, self.params.model, purpose,
                 r.prompt_tokens if r else None,
                 r.completion_tokens if r else None,
                 latency_ms,
                 (status + (f":{err}" if err else ""))[:200]))
        except Exception:  # noqa: BLE001
            pass


_CACHE: dict[tuple, LLMClient] = {}


def get_client(profile: str = "default", **overrides) -> LLMClient:
    """工厂 + 按 (profile, overrides) 缓存。"""
    key = (profile, json.dumps(overrides, sort_keys=True, default=str))
    if key not in _CACHE:
        _CACHE[key] = LLMClient(profile, **overrides)
    return _CACHE[key]


def comment(trade_date) -> str:
    """V9 异步点评：读已落盘报告 → 脱敏 → LLM 点评 → 追加写回文件。"""
    if not LLM_ENABLED:
        return ""
    from emotion_core.algorithms.review.utils import (_atomic_write,
                                                      _strip_position_block)
    from pathlib import Path
    out = Path("reports") / f"{trade_date}.md"
    if not out.exists():
        return ""
    md = out.read_text(encoding="utf-8")
    if "⑪ LLM 点评" in md:
        return ""
    try:
        sanitized = _strip_position_block(md)
        reply = get_client("default").chat(
            [{"role": "system", "content": "你是A股龙空龙策略复盘助手，"
             "对复盘报告给≤120字要点点评，不构成投资建议。"},
             {"role": "user", "content": sanitized}], purpose="llm-comment")
        section = f"\n## ⑪ LLM 点评（{reply.model}，仅供参考）\n\n{reply.text}\n"
        _atomic_write(out, md + section)
        return section
    except Exception as exc:  # noqa: BLE001
        return f"\n> ⚠ LLM 点评失败（{exc}），报告主体不受影响\n"
