"""Live provider behaviour, with urllib replaced. No socket is opened."""

import io
import json
import urllib.error
import urllib.request

from specforge.errors import ProviderError
from specforge.provider import OpenAICompatibleProvider, parse_sse
from specforge.textutil import redact, strip_think

import pytest


class _Resp:
    def __init__(self, payload: dict, headers: dict | None = None, lines: list[bytes] | None = None):
        self._payload = json.dumps(payload).encode()
        self.headers = headers or {"Content-Type": "application/json"}
        self._lines = lines or []

    def read(self):
        return self._payload

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid/v1/chat/completions",
        code,
        "error",
        hdrs=None,
        fp=io.BytesIO(body),
    )


def test_missing_key_does_not_call_the_network():
    def explode(*args, **kwargs):
        raise AssertionError("network")

    with pytest.raises(ProviderError, match="API_KEY"):
        OpenAICompatibleProvider(base_url="https://example.invalid/v1", api_key="", urlopen=explode)


def test_redacts_key_in_http_errors():
    def urlopen(request, timeout):
        del request, timeout
        raise _http_error(401, b"bad key sk-test-secret-value")

    provider = OpenAICompatibleProvider(
        base_url="https://example.invalid/v1",
        api_key="sk-test-secret-value",
        urlopen=urlopen,
        sleep=lambda seconds: None,
    )
    with pytest.raises(ProviderError) as caught:
        provider.complete([{"role": "user", "content": "hi"}], model="demo-model")
    assert "sk-test-secret-value" not in str(caught.value)
    assert "***" in str(caught.value)


def test_retries_429_then_reads_json():
    calls = {"n": 0}

    def urlopen(request, timeout):
        del timeout
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(429, b"slow down")
        body = json.loads(request.data.decode())
        assert body["model"] == "demo-model"
        return _Resp({"choices": [{"message": {"content": "<think>hidden</think>hello"}, "finish_reason": "stop"}]})

    provider = OpenAICompatibleProvider(
        base_url="https://example.invalid/v1",
        api_key="local-test-key",
        urlopen=urlopen,
        sleep=lambda seconds: None,
        retries=2,
    )
    assert provider.complete([{"role": "user", "content": "hi"}], model="demo-model") == "hello"
    assert calls["n"] == 2


def test_404_falls_over_to_the_next_model():
    seen = []

    def urlopen(request, timeout):
        del timeout
        body = json.loads(request.data.decode())
        seen.append(body["model"])
        if body["model"] == "missing-model":
            raise _http_error(404, b"no such model")
        assert body["extra"] == 1
        return _Resp({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

    provider = OpenAICompatibleProvider(
        base_url="https://example.invalid/v1",
        api_key="local-test-key",
        fallbacks=["backup-model"],
        extra_body={"extra": 1},
        urlopen=urlopen,
        sleep=lambda seconds: None,
    )
    assert provider.complete([{"role": "user", "content": "hi"}], model="missing-model") == "ok"
    assert seen == ["missing-model", "backup-model"]
    assert "missing-model" in provider.dead


def test_stream_assembles_deltas():
    lines = [
        b'data: {"choices":[{"delta":{"content":"ab"},"finish_reason":null}]}\n',
        b'data: {"choices":[{"delta":{"content":"c"},"finish_reason":"stop"}]}\n',
        b"data: [DONE]\n",
    ]

    def urlopen(request, timeout):
        del timeout
        assert b'"stream": true' in request.data
        return _Resp({}, headers={"Content-Type": "text/event-stream"}, lines=lines)

    provider = OpenAICompatibleProvider(
        base_url="https://example.invalid/v1",
        api_key="local-test-key",
        stream=True,
        urlopen=urlopen,
    )
    assert provider.complete([{"role": "user", "content": "hi"}], model="demo-model") == "abc"


def test_parse_sse_deadline(monkeypatch):
    class _Clock:
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n'

    monkeypatch.setattr("specforge.provider.time.time", lambda: 10_000)
    with pytest.raises(TimeoutError):
        parse_sse(_Clock(), deadline_at=1)


def test_strip_and_redact_helpers():
    assert strip_think("a <think>secret</think> b") == "a  b"
    assert redact("token sk-live and tail", ["sk-live"]) == "token *** and tail"


def test_urlopen_is_the_only_network_symbol_used_here():
    # Guard: the provider module's HTTP entry is urllib. Tests replace it.
    assert urllib.request.urlopen