"""Read-only, compact summaries of existing run directories."""

from __future__ import annotations

import json
from pathlib import Path

from specfreeze.errors import PipelineError

_ARTIFACTS = ("contract.json", "answers.json", "fix.json", "report.json", "decomposition.json")
_LOW_LIMIT = 5


def render_run(path: Path) -> str:
    """Load and render one run without creating or changing any artifact."""
    root = path.expanduser().resolve()
    if not root.exists():
        raise PipelineError(f"run directory does not exist: {root}")
    if not root.is_dir():
        raise PipelineError(f"run path is not a directory: {root}")

    marker = _read_object(root / "run.json", required=True)
    if marker.get("tool") != "specfreeze":
        raise PipelineError(f"{root} is not a specfreeze run")
    artifacts = {name: _read_object(root / name) for name in _ARTIFACTS}
    contract = artifacts["contract.json"]
    answers = artifacts["answers.json"]
    report = artifacts["report.json"]
    decomposition = artifacts["decomposition.json"]

    module = _first_text((contract, "module"), (report, "module"), (decomposition, "module"))
    digest = _first_text((contract, "digest"), (report, "digest"))
    question_count, uncertain, threshold = _answer_summary(answers, report)
    repair, explicit_none = _repair_source(artifacts["fix.json"], report)

    lines = [
        "specfreeze run summary",
        f"Run: {root}",
        f"Module: {module or 'not available'}",
        f"Contract digest: {digest or 'not available'}",
        f"Questions: {question_count if question_count is not None else 'not available'}",
    ]
    if answers is None:
        lines.append("Low-confidence answers: not available")
    else:
        lines.append(f"Low-confidence answers: {len(uncertain)} (threshold {threshold:g})")
        for key, confidence, unanswered in uncertain[:_LOW_LIMIT]:
            detail = "unanswered" if unanswered else f"confidence {confidence:g}"
            lines.append(f"  - {key}: {detail}")
        if len(uncertain) > _LOW_LIMIT:
            lines.append(f"  ... and {len(uncertain) - _LOW_LIMIT} more")
    lines.extend(_repair_lines(repair, explicit_none))
    return "\n".join(lines) + "\n"


def _read_object(path: Path, *, required: bool = False) -> dict | None:
    if not path.exists():
        if required:
            raise PipelineError(f"missing run marker: {path}")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PipelineError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineError(f"{path} must contain a JSON object")
    return value


def _first_text(*sources: tuple[dict | None, str]) -> str | None:
    for artifact, key in sources:
        if artifact is None or key not in artifact or artifact[key] is None:
            continue
        value = artifact[key]
        if not isinstance(value, str):
            raise PipelineError(f"{key} must be a string")
        return value
    return None


def _answer_summary(answers: dict | None, report: dict | None):
    if answers is None:
        return _optional_count(report, "questions"), [], 0.6
    threshold = answers.get("threshold", 0.6)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
        raise PipelineError("answers.json threshold must be a number from 0 to 1")
    rows = answers.get("answers")
    if not isinstance(rows, list):
        raise PipelineError("answers.json answers must be a list")
    count = _optional_count(answers, "question_count")
    if count is None:
        count = len(rows)
    uncertain = []
    for row in rows:
        if not isinstance(row, dict):
            raise PipelineError("each answers.json answer must be an object")
        key = row.get("key", row.get("id"))
        confidence = row.get("confidence")
        if not isinstance(key, str):
            raise PipelineError("each answers.json answer needs a string key or id")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise PipelineError(f"answer {key} confidence must be a number from 0 to 1")
        unanswered = row.get("answer") is None
        if unanswered or confidence < threshold:
            uncertain.append((key, float(confidence), unanswered))
    return count, uncertain, float(threshold)


def _optional_count(artifact: dict | None, key: str) -> int | None:
    if artifact is None or key not in artifact or artifact[key] is None:
        return None
    value = artifact[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PipelineError(f"{key} must be a non-negative integer")
    return value


def _repair_source(fix: dict | None, report: dict | None) -> tuple[dict | None, bool]:
    if fix is not None:
        return fix, False
    if report is None or "fix" not in report:
        return None, False
    value = report["fix"]
    if value is None:
        return None, True
    if not isinstance(value, dict):
        raise PipelineError("report.json fix must be an object or null")
    return value, False


def _repair_lines(repair: dict | None, explicit_none: bool) -> list[str]:
    if repair is None:
        return [f"Repair: {'not run' if explicit_none else 'not available'}"]
    green = repair.get("green")
    applied = repair.get("applied")
    accepted = repair.get("accepted")
    rejected = repair.get("rejected")
    if not isinstance(green, bool) or not isinstance(applied, bool):
        raise PipelineError("repair green and applied fields must be booleans")
    if not isinstance(accepted, list) or not isinstance(rejected, list):
        raise PipelineError("repair accepted and rejected fields must be lists")
    disposition = "applied" if applied else "preview only" if accepted else "not applied"
    lines = [f"Repair: {'green' if green else 'failed'}, {disposition}"]
    lines.append(f"Initial tests: {_test_counts(repair.get('initial'), 'initial')}")
    lines.append(f"Final tests: {_test_counts(repair.get('final'), 'final')}")
    lines.append(f"Accepted: {len(accepted)}; rejected: {len(rejected)}")
    return lines


def _test_counts(result, label: str) -> str:
    if not isinstance(result, dict):
        raise PipelineError(f"repair {label} must be an object")
    values = []
    for key in ("passed", "failed", "errors"):
        value = result.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PipelineError(f"repair {label}.{key} must be a non-negative integer")
        values.append(value)
    return f"{values[0]} passed, {values[1]} failed, {values[2]} errors"
