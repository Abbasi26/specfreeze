"""Ask a decision model the closed questions and record confidence.

Yes/no questions may be answered with ``yes``/``no`` or with ``p_yes`` (the
probability of yes). Confidence from ``p_yes`` is ``abs(p_yes - 0.5) * 2``,
so 0.5 is uncertain and 0 or 1 is certain. Answers below the threshold fall
back to the catalog's conservative default when one is set.
"""

from __future__ import annotations

import json

from specforge.errors import ProviderError
from specforge.models import Answer, Asked
from specforge.provider import loads_json_reply
from specforge.textutil import chunks

_SYSTEM = (
    "You answer closed design questions before any code is written. "
    "Reply with JSON only. Do not write code. "
    "If the written specification already settles a question, follow the specification."
)

_YES = {"yes": "yes", "true": "yes", "y": "yes", "no": "no", "false": "no", "n": "no"}


def answer_questions(session, questions: list[Asked], spec_text: str, *, threshold: float, batch_size: int = 8) -> list[Answer]:
    """Answer ``questions`` in batches. ``session`` is a :class:`specforge.session.Session`."""
    answers: list[Answer] = []
    context = spec_text.strip()[:12000]
    for batch in chunks(questions, batch_size):
        payload = [
            {
                "id": item.key,
                "function": item.function,
                "category": item.category,
                "type": item.type,
                "ask": item.ask,
                "options": item.options,
                "levels": item.levels,
            }
            for item in batch
        ]
        user = (
            "[task:answer-closed-questions]\n\n"
            "Answer every question. Return JSON only, in this shape:\n"
            '{"answers": [{"id": "<the id>", "answer": "<yes|no|option key|level>", '
            '"confidence": 0.0, "rationale": "one sentence"}]}\n\n'
            "For a yes/no question you may send p_yes between 0 and 1 instead of answer. "
            "Confidence is then abs(p_yes - 0.5) * 2.\n"
            "confidence is required and must be between 0 and 1.\n\n"
            f"Specification:\n{context}\n\n"
            "Questions JSON:\n```json\n"
            + json.dumps(payload, ensure_ascii=False, indent=1)
            + "\n```\n"
        )
        raw = session.complete(
            "decision",
            "answer",
            user,
            system=_SYSTEM,
            temperature=0.0,
            max_tokens=2500,
        )
        try:
            parsed = loads_json_reply(raw)
        except ProviderError as exc:
            raise ProviderError(f"decision model did not return JSON: {exc}") from exc
        rows = _rows(parsed)
        by_id = {}
        for row in rows:
            if isinstance(row, dict) and row.get("id"):
                by_id[str(row["id"])] = row
        for question in batch:
            answers.append(build_answer(question, by_id.get(question.key), threshold=threshold))
    return answers


def build_answer(question: Asked, row: dict | None, *, threshold: float) -> Answer:
    """Turn one model row into an Answer, applying the conservative default."""
    answer, confidence, rationale, p_yes = _interpret(question, row or {})
    if answer is not None and not _valid(question, answer):
        rationale = (rationale + " " if rationale else "") + "Rejected: answer is not in the allowed set."
        answer, confidence = None, 0.0
    effective, source = _effective(answer, confidence, question.conservative, threshold)
    return Answer(
        key=question.key,
        id=question.id,
        function=question.function,
        category=question.category,
        type=question.type,
        ask=question.ask,
        answer=answer,
        confidence=confidence,
        rationale=rationale,
        round=question.round,
        effective=effective,
        effective_source=source,
        p_yes=p_yes,
        options=dict(question.options),
        levels=list(question.levels),
        conservative=question.conservative,
        contract_note=dict(question.contract_note),
    )


def uncertain(answer: Answer, threshold: float) -> bool:
    return answer.answer is None or answer.confidence < threshold


def _rows(parsed) -> list:
    if isinstance(parsed, dict):
        for key in ("answers", "questions"):
            if isinstance(parsed.get(key), list):
                return parsed[key]
        return [parsed]
    if isinstance(parsed, list):
        return parsed
    return []


def _interpret(question: Asked, row: dict) -> tuple[str | None, float, str, float | None]:
    rationale = str(row.get("rationale") or row.get("reason") or "").strip()
    p_yes = row.get("p_yes", row.get("p_ja"))
    if question.type == "yes_no" and p_yes is not None:
        try:
            probability = float(p_yes)
        except (TypeError, ValueError):
            probability = None
        if probability is not None and 0.0 <= probability <= 1.0:
            chosen = "yes" if probability >= 0.5 else "no"
            confidence = round(abs(probability - 0.5) * 2, 3)
            return chosen, confidence, rationale, probability
    raw_answer = row.get("answer", row.get("choice", row.get("value")))
    if raw_answer is None:
        return None, 0.0, rationale, None
    text = str(raw_answer).strip()
    if question.type == "yes_no":
        mapped = _YES.get(text.lower())
        if not mapped:
            return None, 0.0, rationale, None
        text = mapped
    confidence = _confidence(row.get("confidence"))
    return text, confidence, rationale, None


def _confidence(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number < 0:
        return 0.0
    if number > 1:
        return 1.0
    return round(number, 3)


def _valid(question: Asked, answer: str) -> bool:
    if question.type == "yes_no":
        return answer in ("yes", "no")
    if question.type == "choice":
        return answer in question.options
    if question.type == "score":
        return answer in question.levels
    return False


def _effective(answer: str | None, confidence: float, conservative: str | None, threshold: float):
    if answer is not None and confidence >= threshold:
        return answer, "model"
    if conservative:
        return conservative, "conservative"
    if answer is None:
        return None, "unanswered"
    return answer, "low-confidence"


def render_answers(module: str, answers: list[Answer], threshold: float) -> str:
    """Human-readable answer artifact."""
    lines = [
        f"# Answers for `{module}`",
        "",
        f"Threshold for a confident answer: {threshold}.",
        "Low-confidence answers use the catalog conservative default when one is set.",
        "If an answer disagrees with the written specification, the specification wins.",
        "",
    ]
    groups: dict[str, list[Answer]] = {}
    for answer in answers:
        groups.setdefault(answer.function or "module", []).append(answer)
    for name, rows in groups.items():
        lines.append(f"## `{name}`")
        lines.append("")
        for row in rows:
            band = _band(row.confidence, threshold)
            extra = ""
            if row.effective_source == "conservative":
                extra = f"; model said {row.answer}, conservative default **{row.effective}** applied"
            elif row.p_yes is not None:
                extra = f"; p_yes={row.p_yes}"
            lines.append(
                f"- `{row.id}` (round {row.round}, {row.category}, {row.type}, {band}): "
                f"{row.ask} **{row.effective}** "
                f"(confidence {row.confidence}{extra})"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _band(confidence: float, threshold: float) -> str:
    if confidence >= 0.8:
        return "high"
    if confidence >= threshold:
        return "medium"
    return "low"