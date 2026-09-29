"""Agnes LLM 实现：复用 lkl 的 agnes-3.0-flash 端点与参数。

本模块自持密钥解析与参数定义，不依赖 services/llm 或 services/llm_backend，
避免 llm → services 反向依赖（架构守护 test_signal_chain_does_not_import_llm）。

agnes profile 原值 base_url=https://apihub.agnes-ai.com/v1、model=agnes-3.0-flash、
timeout=180、max_retry=2；本文件只做 LLM 抽象层到该传输的适配。

密钥解析顺序：显式入参 → AGNES_API_KEY → lkl 密钥链
（LKL_LLM_API_KEY_AGNES / LKL_LLM_API_KEY / 密钥文件）。
解析在 __init__ 一次性完成；取不到密钥时 complete() 抛 LLMNotEnabledError，
主链据此跳过 LLM 增强（docs/12 §7）。

温度与输出长度以调用方传入值为准（抽象层默认 0.0 / 2048），profile 里的
temperature / max_tokens 不参与；端点、模型、timeout、重试次数、top_p 取 profile。

单后端语义：不做多后端回退——回退是 services.llm.LLMClient 的职责，
llm 层要的是"填入 prompt、输出信息"的可插拔实现。
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx

from emotion_core.llm.base import LLMNotEnabledError


# ── lkl 原值（emotion-core CONFIG 尚未收录，就地定义作 fallback）─────────────

LLM_PROFILES: dict[str, dict] = {
    "agnes": {
        "base_url": "https://apihub.agnes-ai.com/v1",
        "model": "agnes-3.0-flash",
        "temperature": 0.2, "max_tokens": 8192, "top_p": 1.0,
        "timeout": 180, "max_retry": 2,
    },
}
LLM_KEY_ENV = "LKL_LLM_API_KEY"
LLM_KEY_FILES = ["~/.llmkey", ".secrets/llmkey"]
LLM_PROFILE_KEY_ENV: dict[str, str] = {"agnes": "AGNES_API_KEY"}
LLM_ENV_FILES = ["~/.env"]


class LLMNotConfigured(RuntimeError):
    """未配置密钥/未开启时抛出，调用方自行降级。"""


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


def _read_env_file_key(name: str) -> str:
    """从 ~/.env 按键名取值。"""
    for fname in LLM_ENV_FILES:
        path = Path(fname).expanduser()
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == name:
                value = value.strip().strip('"').strip("'")
                if value:
                    return value
    return ""


def resolve_key(profile: str) -> str:
    """按 profile 专属环境变量、共享环境变量、~/.env、密钥文件顺序取密钥。"""
    specific = os.environ.get(f"LKL_LLM_API_KEY_{profile.upper()}", "").strip()
    if specific:
        return specific
    shared = os.environ.get(LLM_KEY_ENV, "").strip()
    if shared:
        return shared
    env_name = LLM_PROFILE_KEY_ENV.get(profile, "")
    if env_name:
        direct = os.environ.get(env_name, "").strip()
        if direct:
            return direct
        from_env_file = _read_env_file_key(env_name)
        if from_env_file:
            return from_env_file
    key = _read_file_key()
    if key:
        return key
    raise LLMNotConfigured(
        f"未找到密钥：设 {LLM_KEY_ENV} 或 "
        f"{LLM_PROFILE_KEY_ENV.get(profile, '')}"
        f"（可放 {LLM_ENV_FILES}）或放置密钥文件 {LLM_KEY_FILES}")


def _resolve_key(profile: str) -> str:
    """AGNES_API_KEY 优先，其次 lkl 密钥链；都取不到返回空串。"""
    env = os.environ.get("AGNES_API_KEY", "").strip()
    if env:
        return env
    try:
        return resolve_key(profile)
    except LLMNotConfigured:
        return ""


# ── 参数定义 ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LLMParams:
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    top_p: float
    timeout: float
    max_retry: int


def resolve_params(profile: str = "agnes") -> LLMParams:
    """取 profile 参数。"""
    base = LLM_PROFILES.get(profile, LLM_PROFILES["agnes"])
    return LLMParams(**base)


# ── 输出清洗 ────────────────────────────────────────────────────────────────

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def strip_fence(text: str) -> str:
    """剥离 ```json 围栏。"""
    m = _FENCE_RE.match(text or "")
    return m.group(1) if m else text


# ── Agnes 客户端 ────────────────────────────────────────────────────────────

class AgnesClient:
    """Agnes LLM 客户端（OpenAI 兼容协议）。"""

    def __init__(self, *, profile: str = "agnes",
                 api_key: str | None = None) -> None:
        self.profile = profile
        self.api_key = (api_key or _resolve_key(profile)).strip()
        self.params: LLMParams = resolve_params(profile)
        self.model = self.params.model

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str:
        """填入 prompt，输出信息。

        Args:
            prompt: 用户 prompt（可含模板变量）。
            system: 系统提示（可选，作为首条 system 消息）。
            temperature: 温度，覆盖 profile 取值。
            max_tokens: 最大输出 token 数，覆盖 profile 取值。
        Returns:
            LLM 输出的文本（已剥离 ```json 围栏）。
        Raises:
            LLMNotEnabledError: 未配置密钥。
            httpx.HTTPStatusError: 4xx 且不可重试（除 429）。
            RuntimeError: 返回体缺 choices/message。
        """
        if not self.api_key:
            raise LLMNotEnabledError(
                "Agnes 未启用：设 AGNES_API_KEY 或 LKL_LLM_API_KEY_AGNES")
        params = replace(self.params, temperature=temperature,
                         max_tokens=max_tokens)
        messages = ([{"role": "system", "content": system}] if system else []) \
            + [{"role": "user", "content": prompt}]
        payload = {"model": params.model, "messages": messages,
                   "temperature": params.temperature,
                   "max_tokens": params.max_tokens, "top_p": params.top_p}
        data = self._post(params, payload)
        try:
            content = data["choices"][0]["message"].get("content") or ""
        except (AttributeError, IndexError, KeyError, TypeError) as exc:
            raise RuntimeError(
                f"Agnes 返回体缺 choices/message：{str(data)[:160]}") from exc
        return strip_fence(content)

    def _post(self, params: LLMParams,
              payload: dict[str, Any]) -> dict[str, Any]:
        """POST /chat/completions。

        重试判定：超时、传输错、5xx、429 退避重试；其余 4xx（400/401/403…）
        重试无意义，直抛。
        """
        url = params.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last: Exception = RuntimeError("unreachable")
        for attempt in range(params.max_retry + 1):
            try:
                resp = httpx.post(url, json=payload, headers=headers,
                                  timeout=params.timeout)
                if resp.status_code >= 500:
                    raise httpx.TransportError(f"5xx {resp.status_code}")
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 429:
                    raise
                last = exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
            if attempt < params.max_retry:
                time.sleep(2 ** attempt)
        raise last
