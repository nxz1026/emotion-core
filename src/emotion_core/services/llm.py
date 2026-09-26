"""T11 LLM 层（E4）：OpenAI 兼容协议薄封装 + profile 参数 + 调用审计日志。

架构红线：信号链路（emotion/ladder/entry/exit）禁止 import 本模块（test_arch 守卫）。
参数三级覆盖：LLM_PROFILES[profile] ← 环境变量 LKL_LLM_* ← 调用 kwargs。
端点实测坑适配（PLAN §E4.1）：json 围栏剥离 / timeout 按档 30~180s /
仅超时与5xx重试（4xx 直抛）/ 失败也写 llm_call_log(status=ERROR)。

语义逐字照搬 lkl/services/llm.py。差异仅 IO 适配：
- `from lkl import config` → `from emotion_core.utils.config import CONFIG`；
  CONFIG 尚未收录 LLM_PROFILES / LLM_CHAIN / LLM_ENABLED / LLM_KEY_ENV /
  LLM_KEY_FILES，按 notify.py 同先例用 _cfg(name, default) 回退到
  lkl config.py 原值（默认关闭，同 lkl 拍板③）。
- `from lkl.utils import db` → `from emotion_core.utils.db import execute`；
  SQL 与参数顺序逐字不变。
- `from lkl.services.llm_backend import …` → 内联为本文件尾部（单文件约束，
  不新建 llm_backend 模块）。
- `comment()` 内 `from lkl.services import review` →
  `from emotion_core.algorithms.review.utils import …`（_strip_position_block /
  _atomic_write 已随 review 迁移）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from emotion_core.utils.config import CONFIG
from emotion_core.utils.db import execute


def _cfg(name, default):
    """读 CONFIG 项；emotion-core 尚未收录的键取 lkl config.py 原值。"""
    return getattr(CONFIG, name, default)


# ── lkl config.py LLM 段原值（CONFIG 尚未收录时回退）──────────────
LLM_PROFILES = _cfg("LLM_PROFILES", {
    "default": {
        "base_url": "https://token.sensenova.cn/v1",
        "model": "glm-5.2",
        "temperature": 0.3, "max_tokens": 2048, "top_p": 1.0,
        "timeout": 120, "max_retry": 2,
    },
    "fast": {
        "base_url": "https://token.sensenova.cn/v1",
        "model": "deepseek-v4-flash",
        "temperature": 0.3, "max_tokens": 2048, "top_p": 1.0,
        "timeout": 30, "max_retry": 2,
    },
    "smart": {
        "base_url": "https://token.sensenova.cn/v1",
        "model": "sensenova-6.8-flash-lite",
        "temperature": 0.3, "max_tokens": 4096, "top_p": 1.0,
        "timeout": 180, "max_retry": 2,
    },
    # DSA 策略观察首选，密钥 LKL_LLM_API_KEY_AGNES。
    "agnes": {
        "base_url": "https://apihub.agnes-ai.com/v1",
        "model": "agnes-3.0-flash",
        "temperature": 0.2, "max_tokens": 8192, "top_p": 1.0,
        "timeout": 180, "max_retry": 2,
    },
})
LLM_CHAIN = _cfg("LLM_CHAIN", [name.strip() for name in os.environ.get(
    "LKL_LLM_CHAIN", "").split(",") if name.strip()])
LLM_ENABLED = _cfg("LLM_ENABLED", os.environ.get(
    "LKL_LLM_ENABLED", "").lower() in ("1", "true", "yes"))
LLM_KEY_ENV = _cfg("LLM_KEY_ENV", "LKL_LLM_API_KEY")
LLM_KEY_FILES = _cfg("LLM_KEY_FILES", ["~/.llmkey", ".secrets/llmkey"])


# ── 回退链助手（原 lkl/services/llm_backend.py，单文件约束内联）────
class LLMNotConfigured(RuntimeError):
    """未配置密钥/未开启时抛出，调用方自行降级。"""


class LLMChainExhausted(RuntimeError):
    """所有可回退后端均失败。"""


def _configured_chain() -> list[str]:
    raw = os.environ.get("LKL_LLM_CHAIN")
    if raw is None:
        return list(LLM_CHAIN)
    return [name.strip() for name in raw.split(",") if name.strip()]


def resolve_chain(profile: str) -> list[str]:
    """解析 profile 回退链，过滤未知项并保持调用 profile 在首位。"""
    configured = _configured_chain()
    if not configured:
        return [profile]
    names: list[str] = []
    for name in configured:
        if name not in LLM_PROFILES:
            continue
        if name not in names:
            names.append(name)
    if profile not in names:
        names.insert(0, profile)
    return names or [profile]


def _read_file_key() -> str:
    for fname in LLM_KEY_FILES:
        path = Path(fname).expanduser()
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "api_key" and value.strip():
                return value.strip()
    return ""


def resolve_key(profile: str) -> str:
    """按 profile 专属环境变量、共享环境变量、密钥文件顺序取密钥。"""
    specific = os.environ.get(f"LKL_LLM_API_KEY_{profile.upper()}", "").strip()
    if specific:
        return specific
    shared = os.environ.get(LLM_KEY_ENV, "").strip()
    if shared:
        return shared
    key = _read_file_key()
    if key:
        return key
    raise LLMNotConfigured(
        f"未找到密钥：设 {LLM_KEY_ENV} 或放置 {LLM_KEY_FILES}")


def fallback_error(exc: Exception) -> bool:
    """判断异常是否值得切换后端。"""
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        return bool(response and (response.status_code in (401, 403, 429)
                                  or response.status_code >= 500))
    return False


def short_error(exc: Exception, secrets: tuple[str, ...] = ()) -> str:
    """生成不泄露密钥的短错误文本。"""
    text = str(exc) or exc.__class__.__name__
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[:160]


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
    """OpenAI 兼容客户端，按 profile 链逐后端回退。

    链式回退期间持锁，同实例并发调用串行化。
    """

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
            except Exception as exc:  # noqa: BLE001 —— 失败后按策略回退
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
    """V9 异步点评：读已落盘报告 → 脱敏 → LLM 点评 → 追加写回文件。

    LLM_ENABLED 默认关（拍板③：功能实现但默认不使用）。发布路径不再等
    第三方 API；手动 `lkl llm-comment <date>` 或 daily.sh 追加异步执行。
    返回追加的段文本（未开启/失败返回空串并记日志）。
    """
    if not LLM_ENABLED:
        return ""
    from emotion_core.algorithms.review.utils import (_atomic_write,
                                                      _strip_position_block)
    out = Path("reports") / f"{trade_date}.md"
    if not out.exists():
        return ""
    md = out.read_text(encoding="utf-8")
    if "⑪ LLM 点评" in md:                # 幂等：已有点评不重复调用
        return ""
    try:
        sanitized = _strip_position_block(md)
        reply = get_client("default").chat(
            [{"role": "system", "content": "你是A股龙空龙策略复盘助手，"
             "对复盘报告给≤120字要点点评，不构成投资建议。"},
             {"role": "user", "content": sanitized}], purpose="llm-comment")
        section = f"\n## ⑪ LLM 点评（{reply.model}，仅供参考）\n\n{reply.text}\n"
        _atomic_write(out, md + section)     # P2：点评追加也原子
        return section
    except Exception as exc:  # noqa: BLE001 —— 增强面失败不影响主报告
        return f"\n> ⚠ LLM 点评失败（{exc}），报告主体不受影响\n"
