"""Environment configuration.

Nothing in here has a default endpoint or a default key. A real run must set
SPECFREEZE_BASE_URL, SPECFREEZE_API_KEY, and a model name for each role it uses.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


@dataclass
class Settings:
    """Resolved provider settings for one process."""

    base_url: str
    api_key: str
    decision_model: str
    spec_model: str
    code_model: str
    decision_base_url: str = ""
    spec_base_url: str = ""
    code_base_url: str = ""
    decision_api_key: str = ""
    spec_api_key: str = ""
    code_api_key: str = ""
    fallbacks: list[str] = field(default_factory=list)
    timeout: float = 120.0
    deadline: float = 300.0
    stream: bool = False
    test_timeout: int = 60
    extra_body: dict = field(default_factory=dict)
    provider: str = "openai"

    def url_for(self, role: str) -> str:
        specific = getattr(self, f"{role}_base_url")
        return specific or self.base_url

    def key_for(self, role: str) -> str:
        specific = getattr(self, f"{role}_api_key")
        return specific or self.api_key

    def model_for(self, role: str) -> str:
        if role == "decision":
            return self.decision_model
        if role == "spec":
            return self.spec_model or self.code_model
        if role == "code":
            return self.code_model
        raise KeyError(role)

    def secrets(self) -> list[str]:
        """Values that must never appear in logs or error text."""
        found = [
            self.api_key,
            self.decision_api_key,
            self.spec_api_key,
            self.code_api_key,
        ]
        for name, value in os.environ.items():
            upper = name.upper()
            if not value or len(value) < 8:
                continue
            if upper.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")):
                found.append(value)
        # Longest first so a key that contains another key is fully masked.
        unique = [item for item in dict.fromkeys(found) if item]
        unique.sort(key=len, reverse=True)
        return unique


def load_settings() -> Settings:
    """Read SPECFREEZE_* variables. Missing values stay empty; callers decide."""
    extra_raw = _env("SPECFREEZE_EXTRA_BODY")
    extra: dict = {}
    if extra_raw:
        try:
            parsed = json.loads(extra_raw)
        except json.JSONDecodeError as exc:
            raise ValueError("SPECFREEZE_EXTRA_BODY must be a JSON object") from exc
        if not isinstance(parsed, dict):
            raise ValueError("SPECFREEZE_EXTRA_BODY must be a JSON object")
        extra = parsed
    fallbacks = [item.strip() for item in _env("SPECFREEZE_FALLBACK_MODELS").split(",") if item.strip()]
    provider = _env("SPECFREEZE_PROVIDER") or "openai"
    return Settings(
        base_url=_env("SPECFREEZE_BASE_URL"),
        api_key=_env("SPECFREEZE_API_KEY"),
        decision_model=_env("SPECFREEZE_DECISION_MODEL"),
        spec_model=_env("SPECFREEZE_SPEC_MODEL"),
        code_model=_env("SPECFREEZE_CODE_MODEL"),
        decision_base_url=_env("SPECFREEZE_DECISION_BASE_URL"),
        spec_base_url=_env("SPECFREEZE_SPEC_BASE_URL"),
        code_base_url=_env("SPECFREEZE_CODE_BASE_URL"),
        decision_api_key=_env("SPECFREEZE_DECISION_API_KEY"),
        spec_api_key=_env("SPECFREEZE_SPEC_API_KEY"),
        code_api_key=_env("SPECFREEZE_CODE_API_KEY"),
        fallbacks=fallbacks,
        timeout=_env_float("SPECFREEZE_TIMEOUT", 120.0),
        deadline=_env_float("SPECFREEZE_DEADLINE", 300.0),
        stream=_env("SPECFREEZE_STREAM") == "1",
        test_timeout=_env_int("SPECFREEZE_TEST_TIMEOUT", 60),
        extra_body=extra,
        provider=provider,
    )