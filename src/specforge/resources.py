"""Read files shipped inside the package."""

from __future__ import annotations

from importlib.resources import files


def read_text(relative: str) -> str:
    """Read a UTF-8 file under the specforge package, e.g. ``data/demo/SPEC.md``."""
    return (files("specforge") / relative).read_text(encoding="utf-8")