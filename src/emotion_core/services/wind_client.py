"""Wind MCP CLI 适配器（emotion-core 原生封装）。

复用 Chan_Pattern_Trader 的 wind_source.py 设计，但裁剪为 emotion-core 需求：
- 窄接口：仅封装 CLI 调用 + 配额记账 + 原始响应留存
- 不复权（emotion-core 不复权体系）
- 配额纪律：导入/构造不发请求；每次调用追加台账行

CLI 路径：``/home/ubuntu/.agents/skills/wind-mcp-skill/scripts/cli.mjs``
API Key：``~/.wind-aifinmarket/config`` 的 ``WIND_API_KEY=xxx``
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone  # noqa: F401 — kept for future timestamp needs, timezone
from pathlib import Path
from typing import Any, Callable, Final, Mapping, Sequence

DEFAULT_CLI_SCRIPT: Final[Path] = Path(
    "/home/ubuntu/.agents/skills/wind-mcp-skill/scripts/cli.mjs"
)
DEFAULT_CONFIG_PATH: Final[Path] = Path("~/.wind-aifinmarket/config").expanduser()
DEFAULT_TIMEOUT_SECONDS: Final[float] = 90.0

_QUOTA_CODES: frozenset[str] = frozenset({"RATE_LIMIT_ERROR"})
_QUOTA_MARKERS: tuple[str, ...] = (
    "额度", "积分", "配额", "余额不足", "试用已", "到期",
    "insufficient", "quota",
)


class WindUnavailableError(RuntimeError):
    """Wind 通道不可用（CLI 脚本或 API Key 缺失）。"""


class WindQuotaError(RuntimeError):
    """Wind 额度不足/被限流。"""


class WindSourceError(RuntimeError):
    """其他 Wind 错误（参数、后端、网络、解析）。"""


@dataclass(frozen=True)
class WindCall:
    """一次 CLI 调用的回执。"""
    server_type: str
    tool_name: str
    params: Mapping[str, Any]
    ok: bool
    code: str
    message: str
    data: Any
    raw_response: str
    elapsed_ms: int


Runner = Callable[[Sequence[str], Path, float], "subprocess.CompletedProcess[str]"]


def _default_runner(
    argv: Sequence[str], cwd: Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _read_api_key(config_path: Path) -> str | None:
    """从 ``WIND_API_KEY=xxx`` 形式的配置文件里读密钥。"""
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "WIND_API_KEY" and value.strip():
            return value.strip()
    return None


class WindClient:
    """Wind CLI 客户端（窄接口：通用调用 + 配额记账 + 原始响应留存）。

    Args:
        cli_script: ``cli.mjs`` 路径。
        config_path: 含 ``WIND_API_KEY`` 的配置文件。
        timeout: 单次调用超时（秒）。
        runner: 注入式执行器，测试用。
    """

    def __init__(
        self,
        *,
        cli_script: Path | None = None,
        config_path: Path | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        runner: Runner | None = None,
    ) -> None:
        self._cli_script = Path(cli_script) if cli_script is not None else DEFAULT_CLI_SCRIPT
        self._config_path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
        self._timeout = timeout
        self._runner: Runner = runner if runner is not None else _default_runner
        self._calls = 0

    @property
    def call_count(self) -> int:
        """本实例已发起的调用次数（含失败）。"""
        return self._calls

    def availability(self) -> tuple[bool, str]:
        """``(是否可用, 原因)``；不联网。"""
        if not self._cli_script.is_file():
            return False, f"wind_cli_missing:{self._cli_script}"
        if _read_api_key(self._config_path) is None:
            return False, f"wind_api_key_missing:{self._config_path}"
        return True, ""

    def call(
        self,
        server_type: str,
        tool_name: str,
        params: Mapping[str, Any],
    ) -> WindCall:
        """执行一次 CLI 调用。

        Raises:
            WindUnavailableError: CLI 或密钥缺失。
            WindQuotaError: 额度/限流。
            WindSourceError: 其他失败（含超时、stdout 非 JSON、``ok:false``）。
        """
        available, reason = self.availability()
        if not available:
            raise WindUnavailableError(reason)

        argv = [
            "node",
            str(self._cli_script),
            "call",
            server_type,
            tool_name,
            json.dumps(params, ensure_ascii=False),
        ]
        self._calls += 1
        started = time.time()
        try:
            completed = self._runner(argv, self._cli_script.parent, self._timeout)
        except subprocess.TimeoutExpired as exc:
            raise WindSourceError(
                f"Wind 调用超时（{self._timeout}s）：{server_type}.{tool_name}"
            ) from exc
        except OSError as exc:
            raise WindUnavailableError(f"无法执行 node：{exc}") from exc

        elapsed = int((time.time() - started) * 1000)
        stdout = completed.stdout or ""
        payload = self._parse_stdout(stdout, server_type=server_type, tool_name=tool_name)
        ok = payload.get("ok", True) is not False and payload.get("isError") is not True
        code = str(payload.get("code") or ("OK" if ok else "UNKNOWN"))
        message = str(payload.get("message") or "")

        if not ok:
            if not message:
                message = self._extract_error_text(payload)
            if code in _QUOTA_CODES or any(marker in message for marker in _QUOTA_MARKERS):
                raise WindQuotaError(f"Wind 额度/限流（{code}）：{message}")
            if code == "AUTH_ERROR":
                raise WindUnavailableError(f"Wind 鉴权失败：{message}")
            raise WindSourceError(f"Wind 调用失败（{code}）：{message}")

        return WindCall(
            server_type=server_type,
            tool_name=tool_name,
            params=dict(params),
            ok=True,
            code=code,
            message=message,
            data=self._extract_data(payload),
            raw_response=stdout,
            elapsed_ms=elapsed,
        )

    @staticmethod
    def _parse_stdout(stdout: str, *, server_type: str, tool_name: str) -> Mapping[str, Any]:
        text = stdout.strip()
        if not text:
            raise WindSourceError(f"Wind 无输出：{server_type}.{tool_name}")
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise WindSourceError(f"Wind 输出不是 JSON：{text[:200]!r}")
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise WindSourceError(f"Wind 输出 JSON 解析失败：{text[:200]!r}") from exc

    @staticmethod
    def _extract_data(payload: Mapping[str, Any]) -> Any:
        content = payload.get("content")
        if isinstance(content, list) and content:
            text = content[0].get("text", "")
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
        return payload

    @staticmethod
    def _extract_error_text(payload: Mapping[str, Any]) -> str:
        content = payload.get("content")
        if isinstance(content, list) and content:
            text = content[0].get("text", "")
            if text:
                return text
        return str(payload)


def get_wind_client() -> WindClient:
    """工厂函数：返回默认配置的 WindClient 实例。"""
    return WindClient()
