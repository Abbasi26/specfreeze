"""The tree that ships must not contain secrets, personal paths, or old internal names."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", ".specfreeze", "__pycache__", "uploads", "agent-tools", ".pytest_cache"}
def _pattern(*parts: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile("".join(parts), flags)


# Fragments stay split so this file does not contain the strings it forbids.
PATTERNS = [
    _pattern("/ho", "me/"),
    _pattern("type", "safe", flags=re.I),
    _pattern("api.", "nvidia", ".com", flags=re.I),
    _pattern("integrate.", "api", flags=re.I),
    _pattern(r"\bnv", r"dev\b", flags=re.I),
    _pattern(r"\bnv", r"repair\b", flags=re.I),
    _pattern(r"\bnv", r"dev[0-9]\b", flags=re.I),
    _pattern("TYPE", "SAFE_"),
    _pattern("NVIDIA_", "API_KEY"),
    _pattern(r"\bj", r"[e]v\b", flags=re.I),
    _pattern("AKIA", r"[0-9A-Z]{16}"),
    _pattern("sk-", r"[A-Za-z0-9]{20,}"),
    _pattern("BEGIN ", r"(?:RSA |OPENSSH )?PRIVATE KEY"),
]


def test_tree_has_no_secrets_or_internal_names():
    hits = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix in {".pyc", ".png", ".gz"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in PATTERNS:
            if pattern.search(text):
                hits.append(f"{path.relative_to(ROOT)}: {pattern.pattern}")
    assert hits == []
