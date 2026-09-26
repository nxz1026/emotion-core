"""LLM 多后端回退链：链路解析、密钥解析与失败分类。

语义逐字照搬 lkl/services/llm_backend.py。与 lkl 的差异仅 IO 适配：
- `from lkl import config` → `from emotion_core.utils.config import CONFIG`；
- lkl config.LLM_CHAIN / LLM_PROFILES / LLM_KEY_ENV / LLM_KEY_FILES 尚未收录进
  emotion-core CONFIG（仅 LLM_PROFILE），经 `_cfg(name, default)` 取 lkl config.py
  原值，与 notify.py / doctor.py 同先例。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from emotion_core.utils.config import CONFIG

_LOG = logging.getLogger(__name__)

# lkl config.py LLM 层原值（emotion-core CONFIG 尚未收录，就地定义作 fallback）。
LLM_CHAIN: list[str] = [name.strip() for name in os.environ.get(
    "LKL_LLM_CHAIN", "").split(",") if name.strip()]
LLM_PROFILES: dict[str, dict] = {
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
}
LLM_KEY_ENV = "LKL_LLM_API_KEY"
LLM_KEY_FILES = ["~/.llmkey", ".secrets/llmkey"]


def _cfg(name: str, default):
    """读 CONFIG 项；emotion-core 尚未收录的键取 lkl config.py 原值。"""
    return getattr(CONFIG, name, default)


class LLMNotConfigured(RuntimeError):
    """未配置密钥/未开启时抛出，调用方自行降级。"""


class LLMChainExhausted(RuntimeError):
    """所有可回退后端均失败。"""


def _configured_chain() -> list[str]:
    raw = os.environ.get("LKL_LLM_CHAIN")
    if raw is None:
        return list(_cfg("LLM_CHAIN", LLM_CHAIN))
    return [name.strip() for name in raw.split(",") if name.strip()]


def resolve_chain(profile: str) -> list[str]:
    """解析 profile 回退链，过滤未知项并保持调用 profile 在首位。"""
    configured = _configured_chain()
    if not configured:
        return [profile]
    names: list[str] = []
    for name in configured:
        if name not in _cfg("LLM_PROFILES", LLM_PROFILES):
            _LOG.warning("忽略未知 LLM profile：%s", name)
            continue
        if name not in names:
            names.append(name)
    if profile not in names:
        names.insert(0, profile)
    return names or [profile]


def _read_file_key() -> str:
    for fname in _cfg("LLM_KEY_FILES", LLM_KEY_FILES):
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
    shared = os.environ.get(_cfg("LLM_KEY_ENV", LLM_KEY_ENV), "").strip()
    if shared:
        return shared
    key = _read_file_key()
    if key:
        return key
    raise LLMNotConfigured(
        f"未找到密钥：设 {_cfg('LLM_KEY_ENV', LLM_KEY_ENV)} 或放置 "
        f"{_cfg('LLM_KEY_FILES', LLM_KEY_FILES)}")


def fallback_error(exc: Exception) -> bool:
    """判断异常是否值得切换后端。"""
    import httpx

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
