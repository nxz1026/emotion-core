"""测试 WindClient（CLI 调用 + 错误处理）。"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import patch

import pytest

from emotion_core.services.wind_client import (
    WindClient,
    WindQuotaError,
    WindSourceError,
    WindUnavailableError,
    _read_api_key,
)


class FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def _make_runner(stdout: str):
    def runner(argv: Sequence[str], cwd: Path, timeout: float):
        return FakeCompleted(stdout)
    return runner


def _success_response(data: dict) -> str:
    return json.dumps({
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
        "isError": False,
    })


def test_availability_cli_missing():
    """CLI 脚本缺失时不可用。"""
    client = WindClient(cli_script=Path("/nonexistent/cli.mjs"))
    ok, reason = client.availability()
    assert not ok
    assert "wind_cli_missing" in reason


def test_availability_key_missing(tmp_path):
    """API Key 缺失时不可用。

    ⚠️ 2026-10-07 修：原实现只覆盖 `config_path`，让 `cli_script` 落到
    `DEFAULT_CLI_SCRIPT`（开发机上是一个 `/home/ubuntu/...` 绝对路径）。
    而 `availability()` **先**判 CLI 文件存在再判密钥，所以只要这台机器没有那个
    wind CLI，就先返回 `wind_cli_missing:` —— 断言 `wind_api_key_missing` 必然失败。
    也就是说这条测试能不能过，取决于**跑测试的机器上装没装 wind**，
    在 CI runner 上必红（实测已红）。
    修法与 `test_availability_ok` 同形：两个路径都显式给。
    """
    cli = tmp_path / "cli.mjs"
    cli.write_text("// fake", encoding="utf-8")
    # 写一个不含 WIND_API_KEY 的配置
    cfg = tmp_path / "config"
    cfg.write_text("# empty config\n", encoding="utf-8")
    client = WindClient(cli_script=cli, config_path=cfg)
    ok, reason = client.availability()
    assert not ok
    assert "wind_api_key_missing" in reason


def test_availability_ok(tmp_path, monkeypatch):
    """CLI + Key 都存在时可用。"""
    cli = tmp_path / "cli.mjs"
    cli.write_text("// fake", encoding="utf-8")
    cfg = tmp_path / "config"
    cfg.write_text("WIND_API_KEY=test-key-123\n", encoding="utf-8")
    client = WindClient(cli_script=cli, config_path=cfg)
    ok, reason = client.availability()
    assert ok
    assert reason == ""


def test_call_success():
    """成功调用返回 WindCall。"""
    data = {"data": {"rows": [["600519.SH", "贵州茅台"]]}}
    client = WindClient(runner=_make_runner(_success_response(data)))
    # 绕过 availability 检查
    client._cli_script = Path("/home/ubuntu/.agents/skills/wind-mcp-skill/scripts/cli.mjs")
    # mock availability to always return True
    with patch.object(client, "availability", return_value=(True, "")):
        call = client.call("stock_data", "get_stock_basicinfo", {"question": "test"})
    assert call.ok
    assert call.code == "OK"
    assert call.data == data
    assert call.server_type == "stock_data"
    assert call.tool_name == "get_stock_basicinfo"


def test_call_quota_error():
    """额度耗尽时抛 WindQuotaError。"""
    err = json.dumps({"ok": False, "code": "RATE_LIMIT_ERROR", "message": "额度不足"})
    client = WindClient(runner=_make_runner(err))
    with patch.object(client, "availability", return_value=(True, "")):
        with pytest.raises(WindQuotaError):
            client.call("stock_data", "get_stock_basicinfo", {"question": "test"})


def test_call_auth_error():
    """鉴权失败时抛 WindUnavailableError。"""
    err = json.dumps({"ok": False, "code": "AUTH_ERROR", "message": "key invalid"})
    client = WindClient(runner=_make_runner(err))
    with patch.object(client, "availability", return_value=(True, "")):
        with pytest.raises(WindUnavailableError):
            client.call("stock_data", "get_stock_basicinfo", {"question": "test"})


def test_call_param_error():
    """参数错误时抛 WindSourceError。"""
    err = json.dumps({"ok": False, "code": "PARAM_VALIDATION_ERROR", "message": "missing field"})
    client = WindClient(runner=_make_runner(err))
    with patch.object(client, "availability", return_value=(True, "")):
        with pytest.raises(WindSourceError):
            client.call("stock_data", "get_stock_basicinfo", {"question": "test"})


def test_call_timeout():
    """超时时抛 WindSourceError。"""
    def runner(argv, cwd, timeout):
        raise subprocess.TimeoutExpired(cmd="node", timeout=timeout)
    client = WindClient(runner=runner)
    with patch.object(client, "availability", return_value=(True, "")):
        with pytest.raises(WindSourceError):
            client.call("stock_data", "get_stock_basicinfo", {"question": "test"})


def test_call_empty_output():
    """空输出时抛 WindSourceError。"""
    client = WindClient(runner=_make_runner(""))
    with patch.object(client, "availability", return_value=(True, "")):
        with pytest.raises(WindSourceError):
            client.call("stock_data", "get_stock_basicinfo", {"question": "test"})


def test_call_count_incremented():
    """调用计数递增。"""
    data = {"data": {"rows": []}}
    client = WindClient(runner=_make_runner(_success_response(data)))
    assert client.call_count == 0
    with patch.object(client, "availability", return_value=(True, "")):
        client.call("stock_data", "get_stock_basicinfo", {"question": "test"})
    assert client.call_count == 1


def test_read_api_key_missing_file(tmp_path):
    """配置文件不存在时返回 None。"""
    assert _read_api_key(tmp_path / "nonexistent") is None


def test_read_api_key_empty(tmp_path):
    """配置文件无 WIND_API_KEY 时返回 None。"""
    cfg = tmp_path / "config"
    cfg.write_text("# comment only\n", encoding="utf-8")
    assert _read_api_key(cfg) is None


def test_read_api_key_found(tmp_path):
    """正常读取 WIND_API_KEY。"""
    cfg = tmp_path / "config"
    cfg.write_text("OTHER=foo\nWIND_API_KEY=ak_test123\nBAR=baz\n", encoding="utf-8")
    assert _read_api_key(cfg) == "ak_test123"
