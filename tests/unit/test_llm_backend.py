"""LLM 多后端回退链测试：全程 mock httpx，不访问真实网络。"""

import httpx
import pytest

from emotion_core.services import llm
from emotion_core.services import llm_backend
from emotion_core.services.llm_backend import resolve_chain, resolve_key


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("http error", request=None, response=self)


OK_FAST = {"model": "fast-model", "choices": [{"message": {"content": "fast"}}]}
OK_DEFAULT = {"model": "default-model", "choices": [{"message": {"content": "default"}}]}


@pytest.fixture(autouse=True)
def _shared_key(monkeypatch):
    monkeypatch.setenv("LKL_LLM_API_KEY", "shared-secret")


def _post_mock(monkeypatch, responses):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers, timeout))
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    return calls


def _capture_logs(monkeypatch):
    logged = []
    monkeypatch.setattr(
        llm.LLMClient,
        "_log_call",
        lambda self, reply, purpose, latency, status, err="": logged.append(
            status + (f":{err}" if err else "")),
    )
    return logged


def test_429_retries_then_falls_back_and_logs(monkeypatch):
    monkeypatch.setenv("LKL_LLM_CHAIN", "fast,default")
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    calls = _post_mock(monkeypatch, [FakeResp({}, 429), FakeResp({}, 429), FakeResp(OK_DEFAULT)])
    logged = _capture_logs(monkeypatch)

    reply = llm.LLMClient("fast", max_retry=1).chat([{"role": "user", "content": "ping"}])

    assert reply.text == "default"
    assert len(calls) == 3
    assert logged[0].startswith("FAILOVER:") and logged[-1] == "OK"
    assert calls[-1][1]["model"] == "glm-5.2"


def test_400_does_not_try_next_backend(monkeypatch):
    monkeypatch.setenv("LKL_LLM_CHAIN", "fast,default")
    calls = _post_mock(monkeypatch, [FakeResp({}, 400)])
    _capture_logs(monkeypatch)

    with pytest.raises(httpx.HTTPStatusError):
        llm.LLMClient("fast", max_retry=2).chat([{"role": "user", "content": "bad"}])
    assert len(calls) == 1


def test_all_backends_failed_raises_without_secret(monkeypatch):
    monkeypatch.setenv("LKL_LLM_CHAIN", "fast,default")
    monkeypatch.setenv("LKL_LLM_API_KEY_FAST", "fast-secret")
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    _post_mock(monkeypatch, [FakeResp({}, 401), FakeResp({}, 401)])
    _capture_logs(monkeypatch)

    with pytest.raises(llm.LLMChainExhausted) as caught:
        llm.LLMClient("fast", max_retry=0).chat([{"role": "user", "content": "x"}])
    assert "fast-secret" not in str(caught.value)
    assert "shared-secret" not in str(caught.value)


def test_chain_unset_preserves_single_backend_payload(monkeypatch):
    monkeypatch.delenv("LKL_LLM_CHAIN", raising=False)
    calls = _post_mock(monkeypatch, [FakeResp(OK_FAST)])
    _capture_logs(monkeypatch)
    messages = [{"role": "user", "content": "ping"}]

    reply = llm.LLMClient("fast").chat(messages, json_mode=True)

    assert reply.text == "fast"
    assert len(calls) == 1
    assert calls[0][1] == {
        "model": "deepseek-v4-flash",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 2048,
        "top_p": 1.0,
        "response_format": {"type": "json_object"},
    }


def test_profile_key_precedence_over_shared_and_file(monkeypatch, tmp_path):
    key_file = tmp_path / "llmkey"
    key_file.write_text("api_key=file-secret\n", encoding="utf-8")
    monkeypatch.setattr(llm_backend, "LLM_KEY_FILES", [str(key_file)])
    monkeypatch.delenv("LKL_LLM_API_KEY_FAST", raising=False)
    assert resolve_key("fast") == "shared-secret"

    monkeypatch.setenv("LKL_LLM_API_KEY_FAST", "fast-secret")
    assert resolve_key("fast") == "fast-secret"
    monkeypatch.delenv("LKL_LLM_API_KEY", raising=False)
    assert resolve_key("fast") == "fast-secret"
    monkeypatch.delenv("LKL_LLM_API_KEY_FAST", raising=False)
    assert resolve_key("fast") == "file-secret"
def test_agnes_profile_params_key_and_unknown_fallback(monkeypatch):
    params = llm.resolve_params("agnes")
    assert params.base_url == "https://apihub.agnes-ai.com/v1"
    assert params.model == "agnes-3.0-flash"

    monkeypatch.setenv("LKL_LLM_API_KEY_AGNES", "agnes-secret")
    assert resolve_key("agnes") == "agnes-secret"

    unknown = llm.resolve_params("unknown")
    default = llm.resolve_params("default")
    assert unknown == default


def test_401_falls_back_and_unknown_profile_warns(monkeypatch, caplog):
    monkeypatch.setenv("LKL_LLM_CHAIN", "unknown,fast,default")
    assert resolve_chain("fast") == ["fast", "default"]
    assert "未知" in caplog.text
    calls = _post_mock(monkeypatch, [FakeResp({}, 401), FakeResp(OK_DEFAULT)])
    _capture_logs(monkeypatch)

    assert llm.LLMClient("fast", max_retry=0).chat([]).text == "default"
    assert len(calls) == 2
def test_fallback_rebuilds_payload_for_each_profile(monkeypatch):
    monkeypatch.setenv("LKL_LLM_CHAIN", "smart,fast")
    calls = _post_mock(monkeypatch, [FakeResp({}, 401), FakeResp(OK_FAST)])
    _capture_logs(monkeypatch)

    assert llm.LLMClient("smart", max_retry=0).chat([]).text == "fast"
    assert calls[0][1]["max_tokens"] == 4096
    assert calls[1][1]["max_tokens"] == 2048
    assert calls[0][1]["temperature"] == calls[1][1]["temperature"]


def test_missing_fallback_key_is_skipped(monkeypatch):
    monkeypatch.setenv("LKL_LLM_CHAIN", "fast,default")
    client = llm.LLMClient("default")
    original = llm.resolve_key

    def only_default(profile):
        if profile == "fast":
            raise llm.LLMNotConfigured("missing")
        return original(profile)

    monkeypatch.setattr(llm, "resolve_key", only_default)
    calls = _post_mock(monkeypatch, [FakeResp(OK_DEFAULT)])
    logged = _capture_logs(monkeypatch)

    assert client.chat([]).text == "default"
    assert len(calls) == 1
    assert logged[0].startswith("FAILOVER:LLMNotConfigured")


def test_latency_is_recorded_for_success_and_failure(monkeypatch):
    monkeypatch.setenv("LKL_LLM_CHAIN", "fast,default")
    moments = iter([1.0, 1.125, 2.0, 2.250])
    monkeypatch.setattr(llm.time, "monotonic", lambda: next(moments))
    calls = _post_mock(monkeypatch, [FakeResp({}, 401), FakeResp(OK_DEFAULT)])
    logged = []
    monkeypatch.setattr(
        llm.LLMClient, "_log_call",
        lambda self, reply, purpose, latency, status, err="":
        logged.append((status, latency)),
    )

    assert llm.LLMClient("fast", max_retry=0).chat([]).text == "default"
    assert calls and [latency for _, latency in logged] == [125, 250]


def test_same_client_concurrent_calls_do_not_mix_payloads(monkeypatch):
    import threading

    monkeypatch.delenv("LKL_LLM_CHAIN", raising=False)
    calls = _post_mock(monkeypatch, [FakeResp(OK_FAST), FakeResp(OK_FAST)])
    _capture_logs(monkeypatch)
    client = llm.LLMClient("fast")
    errors = []

    def run():
        try:
            client.chat([])
        except Exception as exc:  # noqa: BLE001 —— 测试线程收集异常
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert {call[1]["model"] for call in calls} == {"deepseek-v4-flash"}
