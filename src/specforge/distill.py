"""Turn answers into a decision specification.

The distill step is deterministic. The same answers always produce the same
markdown, which is what a later code model (and a reviewer) can diff.
"""

from __future__ import annotations

from specforge.decision import _band
from specforge.models import Answer, SpecDocument

_ORDER = ["behaviour", "api", "input", "edge", "errors", "output", "security", "performance", "testing"]


def distill(spec: SpecDocument, answers: list[Answer], *, threshold: float) -> str:
    lines = [
        f"# Decision specification: {spec.module}",
        "",
        "Each bullet is a closed question that was answered before code was written.",
        "If a bullet disagrees with the written specification or the tests, the specification and the tests win.",
        "A low-confidence answer uses the catalog's conservative default when that catalog sets one.",
        "",
        f"Written summary: {spec.summary or '(none)'}",
        "",
    ]
    by_function: dict[str | None, list[Answer]] = {}
    for answer in answers:
        by_function.setdefault(answer.function, []).append(answer)
    if None in by_function:
        lines.append("## Module")
        lines.append("")
        lines.extend(_bullets(by_function[None], threshold))
        lines.append("")
    for fn in spec.functions:
        lines.append(f"## `{fn.name}`")
        lines.append("")
        if fn.signature:
            lines.append(f"**Signature:** `{fn.signature}`")
            lines.append("")
        if fn.purpose:
            lines.append(fn.purpose)
            lines.append("")
        lines.append("**Decisions:**")
        lines.append("")
        lines.extend(_bullets(by_function.get(fn.name, []), threshold))
        lines.append("")
        if fn.examples:
            lines.append("**Examples:**")
            lines.append("")
            for example in fn.examples:
                lines.append(f"- `{example}`")
            lines.append("")
        if fn.calls:
            lines.append("**Calls:** " + ", ".join(f"`{name}`" for name in fn.calls))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def binding_lines(answers: list[Answer]) -> list[str]:
    """Contract notes for answers that have one, using the effective answer."""
    lines = []
    for answer in answers:
        if not answer.effective:
            continue
        note = answer.contract_note.get(answer.effective)
        if note:
            lines.append(note)
    return lines


def _bullets(answers: list[Answer], threshold: float) -> list[str]:
    if not answers:
        return ["- (no questions for this section)"]
    ordered = sorted(answers, key=lambda item: (_ORDER.index(item.category) if item.category in _ORDER else 50, item.round, item.id))
    rows = []
    for answer in ordered:
        band = _band(answer.confidence, threshold)
        note = ""
        if answer.effective_source == "conservative":
            note = f"; conservative default used because confidence {answer.confidence} is below {threshold} (model said {answer.answer})"
        elif answer.effective_source == "unanswered":
            note = "; unanswered"
        rows.append(
            f"- `{answer.id}` ({answer.category}, {band}): {answer.ask} "
            f"**{answer.effective}** (confidence {answer.confidence}{note})"
        )
    return rows