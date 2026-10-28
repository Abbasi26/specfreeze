"""One logged conversation with the role providers."""

from __future__ import annotations

import hashlib
import time

from specfreeze.config import Settings
from specfreeze.provider import Provider
from specfreeze.store import RunStore
from specfreeze.textutil import redact, stage_tag


class Session:
    """Routes a stage to the provider for that role and appends the generation log."""

    def __init__(self, settings: Settings, run: RunStore, providers: dict[str, Provider], *, quiet: bool = False):
        self.settings = settings
        self.run = run
        self.providers = providers
        self.quiet = quiet
        self._index = 0
        self._secrets = settings.secrets()

    def say(self, message: str) -> None:
        if not self.quiet:
            print(message, flush=True)

    def complete(
        self,
        role: str,
        stage: str,
        user: str,
        *,
        system: str,
        function: str | None = None,
        attempt: int | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        tags = [f"[stage:{stage}]"]
        if function:
            tags.append(f"[function:{function}]")
        if attempt is not None:
            tags.append(f"[attempt:{attempt}]")
        full_user = " ".join(tags) + "\n\n" + user
        full_user = redact(full_user, self._secrets)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": full_user},
        ]
        model = self.settings.model_for(role) or "recorded"
        provider = self.providers[role]
        started = time.perf_counter()
        error = ""
        text = ""
        try:
            text = provider.complete(messages, model=model, temperature=temperature, max_tokens=max_tokens)
            text = redact(text, self._secrets)
            return text
        except Exception as exc:
            error = redact(str(exc), self._secrets)
            raise
        finally:
            elapsed = int((time.perf_counter() - started) * 1000)
            self._index += 1
            self.run.log(
                {
                    "stage": stage_tag(full_user, "stage") or stage,
                    "role": role,
                    "model": model,
                    "function": function,
                    "attempt": attempt,
                    "elapsed_ms": elapsed,
                    "prompt_sha256": hashlib.sha256(full_user.encode("utf-8")).hexdigest()[:16],
                    "prompt_chars": len(full_user),
                    "response_chars": len(text),
                    "error": error,
                }
            )
            self.run.save_exchange(self._index, stage, full_user, text or error)