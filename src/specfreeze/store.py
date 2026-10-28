"""A run directory. Stages write only inside it."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from specfreeze import __version__
from specfreeze.errors import PipelineError

_MARKER = "run.json"


class RunStore:
    """Artifacts for one specfreeze run."""

    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def open(cls, path: Path | str, *, force: bool = False) -> RunStore:
        # `force` redoes stage work. It does not allow writing into an unrelated directory.
        del force
        root = Path(path).resolve()
        if root.exists() and not root.is_dir():
            raise PipelineError(f"{root} is not a directory")
        if root.exists() and any(root.iterdir()):
            marker = root / _MARKER
            if not marker.exists():
                raise PipelineError(
                    f"{root} is not empty and is not a specfreeze run. Choose another --out directory."
                )
            try:
                data = json.loads(marker.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise PipelineError(f"{marker} is not valid JSON") from exc
            if data.get("tool") != "specfreeze":
                raise PipelineError(f"{root} is not a specfreeze run. Choose another --out directory.")
        root.mkdir(parents=True, exist_ok=True)
        store = cls(root)
        if not (root / _MARKER).exists():
            store.write_json(
                _MARKER,
                {
                    "tool": "specfreeze",
                    "version": __version__,
                    "created": _now(),
                },
            )
        return store

    def exists(self, relative: str) -> bool:
        return self._path(relative).exists()

    def read(self, relative: str) -> str:
        return self._path(relative).read_text(encoding="utf-8")

    def write(self, relative: str, text: str) -> Path:
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_json(self, relative: str, data: dict) -> Path:
        return self.write(relative, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    def log(self, event: dict) -> None:
        path = self._path("generation.log")
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": _now(), **event}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def save_exchange(self, index: int, stage: str, prompt: str, response: str) -> None:
        slug = f"{index:04d}-{stage}"
        self.write(f"prompts/{slug}.prompt.txt", prompt)
        self.write(f"prompts/{slug}.response.txt", response)

    def _path(self, relative: str) -> Path:
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise PipelineError(f"refusing to write outside the run directory: {relative}")
        path = (self.path / relative).resolve()
        if self.path != path and self.path not in path.parents:
            raise PipelineError(f"refusing to write outside the run directory: {relative}")
        return path


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
