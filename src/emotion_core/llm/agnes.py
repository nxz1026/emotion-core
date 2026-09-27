"""Agnes LLM 实现：复用 lkl 的 agnes-3.0-flash 端点与参数。

参数表与围栏剥离复用 emotion_core.services.llm（lkl T11 E4 搬运版）：
agnes profile 原值 base_url=https://apihub.agnes-ai.com/v1、model=agnes-3.0-flash、
timeout=180、max_retry=2；本文件只做 LLM 抽象层到该传输的适配。

密钥解析顺序：显式入参 → AGNES_API_KEY → lkl 密钥链
（LKL_LLM_API_KEY_AGNES / LKL_LLM_API_KEY / 密钥文件，见 llm_backend.resolve_key）。
解析在 __init__ 一次性完成；取不到密钥时 complete() 抛 LLMNotEnabledError，
主链据此跳过 LLM 增强（docs/12 §7）。

温度与输出长度以调用方传入值为准（抽象层默认 0.0 / 2048），profile 里的
temperature / max_tokens 不参与；端点、模型、timeout、重试次数、top_p 取 profile。

单后端语义：不做多后端回退——回退是 services.llm.LLMClient 的职责，
llm 层要的是"填入 prompt、输出信息"的可插拔实现。
"""
from __future__ import annotations

import os
import time
from dataclasses import replace
from typing import Any

import httpx

from emotion_core.llm.base import LLMNotEnabledError
from emotion_core.services import llm as _llm
from emotion_core.services.llm_backend import LLMNotConfigured, resolve_key


def _resolve_key(profile: str) -> str:
    """AGNES_API_KEY 优先，其次 lkl 密钥链；都取不到返回空串。"""
    env = os.environ.get("AGNES_API_KEY", "").strip()
    if env:
        return env
    try:
        return resolve_key(profile)
    except LLMNotConfigured:
        return ""


class AgnesClient:
    """Agnes LLM 客户端（OpenAI 兼容协议）。"""

    def __init__(self, *, profile: str = "agnes",
                 api_key: str | None = None) -> None:
        self.profile = profile
        self.api_key = (api_key or _resolve_key(profile)).strip()
        self.params: _llm.LLMParams = _llm.resolve_params(profile)
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
        return _llm.strip_fence(content)

    def _post(self, params: _llm.LLMParams,
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
