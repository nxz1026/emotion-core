"""llm/agnes.py AgnesClient + 密钥解析 + 输出清洗测试。"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from emotion_core.llm.agnes import (
    LLMNotConfigured,
    LLMParams,
    _read_file_key,
    _read_env_file_key,
    _resolve_key,
    AgnesClient,
    LLM_PROFILES,
    resolve_key,
    resolve_params,
    strip_fence,
)
from emotion_core.llm.base import LLMNotEnabledError


class TestStripFence:
    def test_json_fence(self):
        assert strip_fence('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_plain_fence(self):
        assert strip_fence('```\nhello\n```') == 'hello'

    def test_no_fence(self):
        assert strip_fence("plain text") == "plain text"

    def test_empty_string(self):
        assert strip_fence("") == ""


class TestResolveParams:
    def test_returns_params(self):
        params = resolve_params("agnes")
        assert isinstance(params, LLMParams)
        assert params.model == "agnes-3.0-flash"
        assert params.base_url == "https://apihub.agnes-ai.com/v1"

    def test_unknown_profile_uses_default(self):
        params = resolve_params("unknown")
        assert params.model == "agnes-3.0-flash"


class TestResolveKey:
    @patch.dict(os.environ, {"LKL_LLM_API_KEY": "shared_key"})
    def test_shared_env(self):
        key = resolve_key("agnes")
        assert key == "shared_key"

    @patch.dict(os.environ, {"LKL_LLM_API_KEY_AGNES": "specific_key"})
    def test_specific_env(self):
        key = resolve_key("agnes")
        assert key == "specific_key"

    @patch.dict(os.environ, {"AGNES_API_KEY": "direct_key"})
    def test_direct_profile_key(self):
        key = resolve_key("agnes")
        assert key == "direct_key"

    @patch.dict(os.environ, {}, clear=True)
    @patch("emotion_core.llm.agnes._read_file_key", return_value="")
    @patch("emotion_core.llm.agnes._read_env_file_key", return_value="")
    def test_not_configured(self, _env, _file):
        with pytest.raises(LLMNotConfigured):
            resolve_key("unknown_profile")


class TestResolveKeyInternal:
    @patch.dict(os.environ, {"AGNES_API_KEY": "direct_agnes"})
    def test_agnes_env_priority(self):
        assert _resolve_key("agnes") == "direct_agnes"

    @patch.dict(os.environ, {}, clear=True)
    @patch("emotion_core.llm.agnes.resolve_key", side_effect=LLMNotConfigured("nope"))
    def test_returns_empty_on_failure(self, _mock):
        assert _resolve_key("agnes") == ""


class TestReadFileKey:
    @patch("emotion_core.llm.agnes.Path")
    def test_reads_from_file(self, mock_path_cls):
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "api_key = test_key_123\nother = foo"
        mock_path_cls.return_value = mock_path
        mock_path_cls.return_value.expanduser.return_value = mock_path
        result = _read_file_key()
        assert result == "test_key_123"

    @patch("emotion_core.llm.agnes.Path")
    def test_no_file_returns_empty(self, mock_path_cls):
        mock_path = MagicMock()
        mock_path.exists.return_value = False
        mock_path_cls.return_value = mock_path
        mock_path_cls.return_value.expanduser.return_value = mock_path
        assert _read_file_key() == ""


class TestReadEnvFileKey:
    @patch("emotion_core.llm.agnes.Path")
    def test_reads_from_env(self, mock_path_cls):
        mock_path = MagicMock()
        mock_path.is_file.return_value = True
        mock_path.read_text.return_value = 'AGNES_API_KEY="env_key"'
        mock_path_cls.return_value = mock_path
        mock_path_cls.return_value.expanduser.return_value = mock_path
        result = _read_env_file_key("AGNES_API_KEY")
        assert result == "env_key"

    @patch("emotion_core.llm.agnes.Path")
    def test_no_file_returns_empty(self, mock_path_cls):
        mock_path = MagicMock()
        mock_path.is_file.return_value = False
        mock_path_cls.return_value = mock_path
        mock_path_cls.return_value.expanduser.return_value = mock_path
        assert _read_env_file_key("AGNES_API_KEY") == ""


class TestAgnesClient:
    def test_init_with_key(self):
        client = AgnesClient(api_key="test_key")
        assert client.api_key == "test_key"
        assert client.profile == "agnes"

    @patch.dict(os.environ, {}, clear=True)
    @patch("emotion_core.llm.agnes._resolve_key", return_value="")
    def test_init_no_key(self, _mock):
        client = AgnesClient()
        assert client.api_key == ""

    def test_complete_success(self):
        client = AgnesClient(api_key="test_key")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": [{"message": {"content": "hello"}}]}
        with patch("httpx.post", return_value=mock_response):
            result = client.complete("prompt")
            assert result == "hello"

    def test_complete_strips_fence(self):
        client = AgnesClient(api_key="test_key")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": [{"message": {"content": '```json\n{"x": 1}\n```'}}]}
        with patch("httpx.post", return_value=mock_response):
            result = client.complete("prompt")
            assert result == '{"x": 1}'

    @patch("emotion_core.llm.agnes._resolve_key", return_value="")
    def test_complete_no_key_raises(self, _mock):
        client = AgnesClient(api_key="")
        with pytest.raises(LLMNotEnabledError):
            client.complete("prompt")

    def test_complete_429_retries(self):
        client = AgnesClient(api_key="test_key")
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_resp_429.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429", request=MagicMock(), response=mock_resp_429
        )
        with patch("httpx.post", return_value=mock_resp_429):
            with pytest.raises(httpx.HTTPStatusError):
                client.complete("prompt", max_tokens=100)

    def test_complete_400_no_retry(self):
        client = AgnesClient(api_key="test_key")
        mock_resp_400 = MagicMock()
        mock_resp_400.status_code = 400
        mock_resp_400.raise_for_status.side_effect = httpx.HTTPStatusError(
            "400", request=MagicMock(), response=mock_resp_400
        )
        with patch("httpx.post", return_value=mock_resp_400):
            with pytest.raises(httpx.HTTPStatusError):
                client.complete("prompt")

    def test_complete_malformed_response(self):
        client = AgnesClient(api_key="test_key")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"invalid": "response"}
        with patch("httpx.post", return_value=mock_response):
            with pytest.raises(RuntimeError, match="缺 choices/message"):
                client.complete("prompt")
