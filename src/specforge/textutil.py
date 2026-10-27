"""Small text helpers shared by the provider and the pipeline."""

from __future__ import annotations

import json
import re


_THINK = re.compile(r"(?s)<think>.*?</think>")
_FENCE = re.compile(r"```(?:json)?[ \t]*\n(.*?)```", re.S)
_PY_FENCE = re.compile(r"```(?:python|py)?[ \t]*\n", re.I)


def strip_think(text: str) -> str:
    """Drop reasoning blocks some models inline into the visible reply."""
    cleaned = _THINK.sub("", text or "")
    if "<think>" in cleaned:
        cleaned = cleaned.split("<think>", 1)[0]
    return cleaned.strip()


def parse_json_payload(text: str):
    """Parse the JSON object or array from a model reply.

    Accepts a raw JSON document or a fenced block. Raises ValueError when
    nothing in the reply parses.
    """
    cleaned = strip_think(text)
    fenced = _FENCE.search(cleaned)
    cand = fenced.group(1).strip() if fenced else cleaned.strip()
    try:
        return json.loads(cand)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = cand.find(opener), cand.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cand[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("model response did not contain JSON")


def extract_code(text: str) -> str:
    """Return the python block from a reply.

    Uses the first opening fence and the last closing fence so a string that
    itself contains backticks is not cut in half.
    """
    cleaned = strip_think(text)
    match = _PY_FENCE.search(cleaned)
    if not match:
        body = cleaned.strip()
        return (body + "\n") if body else ""
    rest = cleaned[match.end() :]
    end = rest.rfind("\n```")
    body = rest[:end] if end != -1 else rest
    return body.strip() + "\n"


def message_text(content) -> str:
    """Normalize OpenAI-style message content to a string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return ""


def redact(text: str, secrets: list[str]) -> str:
    """Replace known secret values with a fixed mask."""
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, "***")
    return out


def stage_tag(text: str, name: str) -> str | None:
    match = re.search(rf"\[{name}:([^\]]+)\]", text)
    return match.group(1).strip() if match else None


def chunks(items: list, size: int):
    size = max(1, size)
    for index in range(0, len(items), size):
        yield items[index : index + size]