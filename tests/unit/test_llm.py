"""LLM 层单测：mock httpx 不发真请求（三级参数/重试/围栏/密钥）。"""
import httpx
import pytest

from emotion_core.services import llm
from emotion_core.services import llm_backend


class FakeResp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)


OK_PAYLOAD = {"model": "m1", "choices": [{"message": {"content": "pong"}}],
              "usage": {"prompt_tokens": 5, "completion_tokens": 2}}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("LKL_LLM_API_KEY", "test-key")


def _mock_post(monkeypatch, seq):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(llm.httpx, "post", fake_post)
    return calls


def test_resolve_params_three_levels(monkeypatch):
    p = llm.resolve_params("fast")
    assert p.model == "deepseek-v4-flash" and p.timeout == 30
    monkeypatch.setenv("LKL_LLM_MODEL", "env-model")
    assert llm.resolve_params("fast").model == "env-model"
    assert llm.resolve_params("fast", {"model": "kw"}).model == "kw"


def test_strip_fence():
    assert llm.strip_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert llm.strip_fence("plain") == "plain"


def test_chat_success_and_log(monkeypatch):
    calls = _mock_post(monkeypatch, [FakeResp(OK_PAYLOAD)])
    logged = []
    monkeypatch.setattr(llm.LLMClient, "_log_call",
                        lambda self, r, pur, lat, status, err="": logged.append(status))
    reply = llm.LLMClient("fast").chat([{"role": "user", "content": "ping"}])
    assert reply.text == "pong" and reply.prompt_tokens == 5
    assert calls[0]["url"].endswith("/chat/completions")
    assert calls[0]["timeout"] == 30
    assert logged == ["OK"]


def test_retry_on_timeout_only(monkeypatch):
    calls = _mock_post(monkeypatch,
                       [httpx.ConnectTimeout("t"), FakeResp(OK_PAYLOAD)])
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm.LLMClient, "_log_call", lambda *a, **k: None)
    reply = llm.LLMClient("fast", max_retry=2).chat([{"role": "user", "content": "x"}])
    assert reply.text == "pong" and len(calls) == 2


def test_4xx_no_retry(monkeypatch):
    calls = _mock_post(monkeypatch, [FakeResp({}, status=401)])
    monkeypatch.setattr(llm.LLMClient, "_log_call", lambda *a, **k: None)
    with pytest.raises(httpx.HTTPStatusError):
        llm.LLMClient("fast").chat([{"role": "user", "content": "x"}])
    assert len(calls) == 1


def test_missing_key_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("LKL_LLM_API_KEY")
    monkeypatch.delenv("SENSEN_API_KEY", raising=False)
    monkeypatch.setattr(llm_backend, "LLM_KEY_FILES", ["~/.nonexistent-llmkey"])
    # ~/.env 也是密钥来源（2026-09-28 接入），这里必须隔离，否则本机真密钥会命中
    monkeypatch.setattr(llm_backend, "LLM_ENV_FILES", [str(tmp_path / "none")])
    with pytest.raises(llm.LLMNotConfigured):
        llm.resolve_api_key()


def test_get_client_cached():
    assert llm.get_client("fast") is llm.get_client("fast")
