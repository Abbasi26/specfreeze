"""Model providers.

The live provider speaks the OpenAI chat-completions protocol, which is what
hosted endpoints such as NVIDIA NIM expose. The fake provider never opens a
socket; tests and ``specforge demo`` use it.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol

from specforge.errors import ProviderError
from specforge.textutil import message_text, parse_json_payload, redact, strip_think

# Codes worth another try on the same model. Auth failures are not in this set.
_RETRYABLE = {408, 409, 429, 500, 502, 503, 504}
_DEAD = {404, 410}


class Provider(Protocol):
    """One chat completion. ``model`` is the role's model name."""

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str: ...


class FakeProvider:
    """Offline provider. ``handler`` returns the assistant text for a prompt.

    The handler is the recording. This class does not read the environment and
    does not touch the network.
    """

    def __init__(self, handler: Callable[[list[dict[str, str]], str], str], model: str = "recorded"):
        self.handler = handler
        self.model_name = model
        self.calls: list[str] = []

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "",
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        del temperature, max_tokens
        chosen = model or self.model_name
        self.calls.append(chosen)
        try:
            return self.handler(messages, chosen)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"recorded provider failed: {exc}") from exc


class OpenAICompatibleProvider:
    """Chat completions over HTTP. Keys come from the caller, never from literals."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        fallbacks: list[str] | None = None,
        timeout: float = 120.0,
        deadline: float = 300.0,
        stream: bool = False,
        extra_body: dict | None = None,
        retries: int = 2,
        max_tokens_cap: int = 16384,
        secrets: list[str] | None = None,
        sleep=time.sleep,
        urlopen=urllib.request.urlopen,
    ):
        if not base_url:
            raise ProviderError("SPECFORGE_BASE_URL is not set")
        if not api_key:
            raise ProviderError("SPECFORGE_API_KEY is not set")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.fallbacks = list(fallbacks or [])
        self.timeout = timeout
        self.deadline = deadline
        self.stream = stream
        self.extra_body = dict(extra_body or {})
        self.retries = retries
        self.max_tokens_cap = max_tokens_cap
        self._secrets = secrets if secrets is not None else [api_key]
        self._sleep = sleep
        self._urlopen = urlopen
        self.dead: set[str] = set()

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        if not model:
            raise ProviderError("no model name for this role (set SPECFORGE_DECISION_MODEL / SPECFORGE_CODE_MODEL)")
        chain = [model] + [item for item in self.fallbacks if item != model]
        last_error = "no model was called"
        for current in chain:
            if current in self.dead:
                continue
            try:
                return self._one_model(messages, current, temperature, max_tokens)
            except ProviderError as exc:
                last_error = str(exc)
                if getattr(exc, "fatal", False):
                    raise
        raise ProviderError(f"every model failed. Last error: {last_error}")

    def _one_model(self, messages, model: str, temperature: float, max_tokens: int) -> str:
        tokens = max_tokens
        attempt = 0
        while True:
            body = {
                "model": model,
                "messages": messages,
                "max_tokens": tokens,
                "temperature": temperature,
            }
            body.update(self.extra_body)
            try:
                payload = self._post(body)
            except ProviderError as exc:
                if getattr(exc, "dead_model", False):
                    self.dead.add(model)
                    raise
                if getattr(exc, "retryable", False) and attempt < self.retries:
                    attempt += 1
                    self._sleep(min(2**attempt, 8) + random.random() * 0.01)
                    continue
                raise
            content, finish = _choice_text(payload)
            content = strip_think(content)
            if content and finish != "length":
                return content
            if finish == "length" and tokens < self.max_tokens_cap:
                tokens = min(tokens * 2, self.max_tokens_cap)
                continue
            if content:
                return content
            reasoning = _has_reasoning(payload)
            if reasoning:
                raise _fatal(
                    f"{model} returned reasoning and no content. "
                    "Disable thinking on the endpoint or set SPECFORGE_EXTRA_BODY."
                )
            raise ProviderError(f"{model} returned an empty reply (finish={finish})")

    def _post(self, body: dict) -> dict:
        url = _chat_url(self.base_url)
        use_stream = self.stream
        sent = dict(body)
        if use_stream:
            sent["stream"] = True
        data = json.dumps(sent).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
        }
        if use_stream:
            headers["Accept"] = "text/event-stream"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with self._urlopen(request, timeout=self.timeout) as response:
                ctype = response.headers.get("Content-Type", "")
                if use_stream and "text/event-stream" in ctype:
                    return parse_sse(response, time.time() + self.deadline)
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                detail = ""
            detail = redact(detail, self._secrets)
            err = ProviderError(f"HTTP {exc.code} from the model endpoint: {detail}")
            if exc.code in _DEAD:
                err.dead_model = True  # type: ignore[attr-defined]
            elif exc.code in (401, 403):
                err.fatal = True  # type: ignore[attr-defined]
            elif exc.code in _RETRYABLE:
                err.retryable = True  # type: ignore[attr-defined]
            raise err from exc
        except TimeoutError as exc:
            err = ProviderError(redact(str(exc), self._secrets))
            err.retryable = True  # type: ignore[attr-defined]
            raise err from exc
        except ProviderError:
            raise
        except Exception as exc:
            err = ProviderError(redact(f"{type(exc).__name__}: {exc}", self._secrets))
            err.retryable = True  # type: ignore[attr-defined]
            raise err from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ProviderError("model endpoint returned non-JSON") from exc


def _chat_url(base: str) -> str:
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _fatal(message: str) -> ProviderError:
    err = ProviderError(message)
    err.fatal = True  # type: ignore[attr-defined]
    return err


def _choice_text(payload: dict) -> tuple[str, str | None]:
    choices = payload.get("choices") or []
    if not choices:
        return "", None
    choice = choices[0]
    message = choice.get("message") or {}
    return message_text(message.get("content")), choice.get("finish_reason")


def _has_reasoning(payload: dict) -> bool:
    choices = payload.get("choices") or []
    if not choices:
        return False
    message = (choices[0].get("message") or {})
    return bool(message.get("reasoning_content") or message.get("reasoning") or payload.get("_reasoning_chunks"))


def parse_sse(response, deadline_at: float) -> dict:
    """Read an OpenAI-style event stream into one non-streaming payload."""
    content: list[str] = []
    finish = None
    reasoning = 0
    for raw in response:
        if time.time() > deadline_at:
            raise TimeoutError(f"stream exceeded the deadline ({deadline_at})")
        line = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else str(raw).strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        for choice in event.get("choices") or []:
            delta = choice.get("delta") or {}
            piece = message_text(delta.get("content"))
            if piece:
                content.append(piece)
            if delta.get("reasoning_content") or delta.get("reasoning"):
                reasoning += 1
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
    return {
        "choices": [{"message": {"content": "".join(content)}, "finish_reason": finish}],
        "_reasoning_chunks": reasoning,
    }


def loads_json_reply(text: str):
    """Parse JSON from a provider reply, wrapping parser errors as ProviderError."""
    try:
        return parse_json_payload(text)
    except ValueError as exc:
        raise ProviderError(str(exc)) from exc