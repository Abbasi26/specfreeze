"""Editable YAML question catalogs.

A catalog lists closed questions. Placeholders ``{function}``, ``{module}`` and
``{signature}`` are filled per function. Follow-ups fire when the recorded
answer matches ``when_answer`` and, if set, when confidence is below
``when_confidence_below`` (both conditions are required when both are set).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from specforge.errors import CatalogError
from specforge.models import Asked, Catalog, Followup, FunctionSpec, QuestionTemplate

_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
_TYPES = {"yes_no": "yes_no", "noul": "yes_no", "bool": "yes_no", "yesno": "yes_no",
          "choice": "choice", "select": "choice", "score": "score", "scale": "score"}
_YES = {"yes", "no"}


def load_catalog(path: Path | str) -> Catalog:
    text = Path(path).read_text(encoding="utf-8")
    return parse_catalog(text, source=str(path))


def _catalog_loader() -> type:
    """YAML loader that keeps yes/no/on/off as strings.

    PyYAML's default loader treats those words as booleans. In a catalog they
    are answer tokens (``yes``, ``no``), including mapping keys under
    ``contract_note``.
    """
    class Loader(yaml.SafeLoader):
        pass

    bool_tag = "tag:yaml.org,2002:bool"
    resolvers = {
        key: [(tag, regexp) for tag, regexp in entries if tag != bool_tag]
        for key, entries in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    Loader.yaml_implicit_resolvers = resolvers
    return Loader


def parse_catalog(text: str, source: str = "<catalog>") -> Catalog:
    try:
        data = yaml.load(text, Loader=_catalog_loader())
    except yaml.YAMLError as exc:
        raise CatalogError(f"{source}: invalid YAML ({exc})") from exc
    if not isinstance(data, dict):
        raise CatalogError(f"{source}: catalog must be a mapping")
    if int(data.get("version") or 0) != 1:
        raise CatalogError(f"{source}: version must be 1")
    name = str(data.get("name") or "").strip()
    if not name:
        raise CatalogError(f"{source}: name is required")
    questions_raw = data.get("questions")
    if not isinstance(questions_raw, list) or not questions_raw:
        raise CatalogError(f"{source}: questions must be a non-empty list")
    seen: set[str] = set()
    questions = []
    for item in questions_raw:
        question = _question(item, source, seen)
        questions.append(question)
    cap = int(data.get("max_per_function") or 100)
    if cap < 1 or cap > 100:
        raise CatalogError(f"{source}: max_per_function must be from 1 to 100")
    return Catalog(
        name=name,
        description=str(data.get("description") or "").strip(),
        questions=questions,
        max_per_function=cap,
    )


def instantiate(
    catalog: Catalog,
    functions: list[FunctionSpec],
    module: str,
    *,
    max_questions: int = 100,
) -> tuple[list[Asked], bool]:
    """Expand templates into concrete questions, capped at ``max_questions``."""
    limit = min(max_questions, catalog.max_per_function, 100)
    asked: list[Asked] = []
    for template in catalog.questions:
        if template.scope == "module":
            asked.append(_asked_from(template, function=None, module=module, signature="", round_no=0, parent=None))
            continue
        for fn in functions:
            if template.applies_to and "*" not in template.applies_to and fn.name not in template.applies_to:
                continue
            asked.append(
                _asked_from(template, function=fn.name, module=module, signature=fn.signature, round_no=0, parent=None)
            )
    truncated = len(asked) > limit
    return asked[:limit], truncated


def expand_followups(
    asked: list[Asked],
    answers_by_key: dict,
    *,
    round_no: int,
    module: str,
    signatures: dict[str, str],
    remaining: int,
) -> list[Asked]:
    """Catalog follow-ups whose trigger matches an answer already on record."""
    if remaining <= 0:
        return []
    existing = set(answers_by_key)
    fresh: list[Asked] = []
    for question in asked:
        answer = answers_by_key.get(question.key)
        if answer is None:
            continue
        for follow in question.followups:
            if not _triggered(follow, answer):
                continue
            key = _key(question.function, follow.id)
            if key in existing:
                continue
            signature = signatures.get(question.function or "", "")
            concrete = _asked_followup(follow, question.function, module, signature, round_no, question.key)
            fresh.append(concrete)
            existing.add(key)
            if len(fresh) >= remaining:
                return fresh
    return fresh


def questions_from_model(payload, *, round_no: int, module: str) -> list[Asked]:
    """Normalize a model-proposed follow-up list into concrete questions."""
    if isinstance(payload, dict):
        payload = payload.get("questions") or payload.get("followups") or payload.get("items") or []
    if not isinstance(payload, list):
        raise CatalogError("model follow-ups must be a JSON list")
    asked: list[Asked] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        text = str(item.get("ask") or item.get("question") or item.get("frage") or "").strip()
        if not text:
            continue
        qtype = _TYPES.get(str(item.get("type") or item.get("typ") or "").lower(), "")
        options = _options(item.get("options") if "options" in item else item.get("optionen"))
        levels = _levels(item.get("levels") if "levels" in item else item.get("stufen") or item.get("scale"))
        if qtype not in ("yes_no", "choice", "score"):
            qtype = "choice" if options else ("score" if levels else "yes_no")
        if qtype == "choice" and not options:
            continue
        if qtype == "score" and not levels:
            continue
        qid = str(item.get("id") or f"model_{index:03d}")
        qid = re.sub(r"\W+", "_", qid).strip("_").lower()[:40] or f"model_{index:03d}"
        function = item.get("function") or None
        if function is not None:
            function = str(function)
        category = str(item.get("category") or item.get("kategorie") or "behaviour")
        if not _SLUG.match(category):
            category = "behaviour"
        note = item.get("contract_note") if isinstance(item.get("contract_note"), dict) else {}
        asked.append(
            Asked(
                key=_key(function, qid),
                id=qid,
                function=function,
                category=category,
                type=qtype,
                ask=_render(text, function or module, module, ""),
                scope="module" if function is None else "function",
                options=options,
                levels=levels,
                contract_note={str(k): str(v) for k, v in note.items()},
                round=round_no,
                parent=None,
            )
        )
    return asked


def _question(item, source: str, seen: set[str]) -> QuestionTemplate:
    if not isinstance(item, dict):
        raise CatalogError(f"{source}: each question must be a mapping")
    qid = str(item.get("id") or "").strip()
    if not _SLUG.match(qid):
        raise CatalogError(f"{source}: question id {qid!r} must be a short lowercase slug")
    if qid in seen:
        raise CatalogError(f"{source}: duplicate question id {qid}")
    seen.add(qid)
    qtype = _TYPES.get(str(item.get("type") or "").lower())
    if not qtype:
        raise CatalogError(f"{source}: question {qid} has an unknown type")
    ask = str(item.get("ask") or "").strip()
    if not ask:
        raise CatalogError(f"{source}: question {qid} needs ask")
    scope = str(item.get("scope") or "function")
    if scope not in ("function", "module"):
        raise CatalogError(f"{source}: question {qid} scope must be function or module")
    category = str(item.get("category") or "behaviour")
    if not _SLUG.match(category):
        raise CatalogError(f"{source}: question {qid} category must be a slug")
    options = _options(item.get("options"))
    levels = _levels(item.get("levels"))
    if qtype == "choice" and not (2 <= len(options) <= 6):
        raise CatalogError(f"{source}: choice {qid} needs 2 to 6 options")
    if qtype == "score" and not (2 <= len(levels) <= 5):
        raise CatalogError(f"{source}: score {qid} needs 2 to 5 levels")
    if qtype == "yes_no" and (options or levels):
        raise CatalogError(f"{source}: yes/no question {qid} cannot set options or levels")
    applies = [str(x) for x in (item.get("applies_to") or [])]
    conservative = item.get("conservative")
    if conservative is not None:
        conservative = str(conservative)
        _check_answer_token(qtype, conservative, options, levels, source, qid)
    notes = item.get("contract_note") or {}
    if not isinstance(notes, dict):
        raise CatalogError(f"{source}: question {qid} contract_note must be a mapping")
    for key in notes:
        _check_answer_token(qtype, str(key), options, levels, source, qid)
    followups = [_followup(raw, source, qid, set()) for raw in (item.get("followups") or [])]
    return QuestionTemplate(
        id=qid,
        category=category,
        type=qtype,
        ask=ask,
        scope=scope,
        applies_to=applies,
        options=options,
        levels=levels,
        conservative=conservative,
        contract_note={str(k): str(v) for k, v in notes.items()},
        followups=followups,
    )


def _followup(item, source: str, parent: str, seen: set[str]) -> Followup:
    if not isinstance(item, dict):
        raise CatalogError(f"{source}: follow-up of {parent} must be a mapping")
    qid = str(item.get("id") or "").strip()
    if not _SLUG.match(qid):
        raise CatalogError(f"{source}: follow-up id {qid!r} under {parent} must be a slug")
    if qid in seen:
        raise CatalogError(f"{source}: duplicate follow-up {qid} under {parent}")
    seen.add(qid)
    qtype = _TYPES.get(str(item.get("type") or "").lower())
    if not qtype:
        raise CatalogError(f"{source}: follow-up {parent}.{qid} has an unknown type")
    ask = str(item.get("ask") or "").strip()
    if not ask:
        raise CatalogError(f"{source}: follow-up {parent}.{qid} needs ask")
    options = _options(item.get("options"))
    levels = _levels(item.get("levels"))
    if qtype == "choice" and not (2 <= len(options) <= 6):
        raise CatalogError(f"{source}: choice follow-up {parent}.{qid} needs 2 to 6 options")
    if qtype == "score" and not (2 <= len(levels) <= 5):
        raise CatalogError(f"{source}: score follow-up {parent}.{qid} needs 2 to 5 levels")
    when = item.get("when_answer")
    when_list: list[str] = []
    if when is not None:
        # when_answer is compared to the parent answer, so it is not checked against this follow-up's options.
        when_list = [str(when)] if not isinstance(when, list) else [str(x) for x in when]
        if not when_list:
            raise CatalogError(f"{source}: follow-up {parent}.{qid} has an empty when_answer")
    below = item.get("when_confidence_below")
    if below is not None:
        below = float(below)
        if not 0 <= below <= 1:
            raise CatalogError(f"{source}: follow-up {parent}.{qid} confidence trigger must be between 0 and 1")
    if not when_list and below is None:
        raise CatalogError(f"{source}: follow-up {parent}.{qid} needs when_answer or when_confidence_below")
    category = str(item.get("category") or "behaviour")
    if not _SLUG.match(category):
        raise CatalogError(f"{source}: follow-up {parent}.{qid} category must be a slug")
    conservative = item.get("conservative")
    if conservative is not None:
        conservative = str(conservative)
        _check_answer_token(qtype, conservative, options, levels, source, qid)
    notes = item.get("contract_note") or {}
    if notes and not isinstance(notes, dict):
        raise CatalogError(f"{source}: follow-up {parent}.{qid} contract_note must be a mapping")
    nested = [_followup(raw, source, qid, set()) for raw in (item.get("followups") or [])]
    return Followup(
        id=qid,
        category=category,
        type=qtype,
        ask=ask,
        options=options,
        levels=levels,
        when_answer=when_list,
        when_confidence_below=below,
        conservative=conservative,
        contract_note={str(k): str(v) for k, v in (notes or {}).items()},
        followups=nested,
    )


def _options(value) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, list):
        out = {}
        for index, item in enumerate(value):
            label = str(item)
            key = re.sub(r"\W+", "_", label).strip("_").lower()[:40] or f"o{index}"
            out[key] = label
        return out
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    raise CatalogError("options must be a mapping or a list")


def _levels(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    raise CatalogError("levels must be a list")


def _check_answer_token(qtype: str, token: str, options: dict, levels: list, source: str, qid: str) -> None:
    if qtype == "yes_no" and token not in _YES:
        raise CatalogError(f"{source}: {qid} answer {token!r} must be yes or no")
    if qtype == "choice" and token not in options:
        raise CatalogError(f"{source}: {qid} answer {token!r} is not an option")
    if qtype == "score" and token not in levels:
        raise CatalogError(f"{source}: {qid} answer {token!r} is not a level")


def _key(function: str | None, qid: str) -> str:
    return f"{function or 'module'}:{qid}"


def _render(text: str, function: str, module: str, signature: str) -> str:
    return (
        text.replace("{function}", function)
        .replace("{module}", module)
        .replace("{signature}", signature or "(signature not set yet)")
    )


def _asked_from(template: QuestionTemplate, *, function, module, signature, round_no, parent) -> Asked:
    return Asked(
        key=_key(function, template.id),
        id=template.id,
        function=function,
        category=template.category,
        type=template.type,
        ask=_render(template.ask, function or module, module, signature),
        scope=template.scope,
        options=dict(template.options),
        levels=list(template.levels),
        conservative=template.conservative,
        contract_note={k: _render(v, function or module, module, signature) for k, v in template.contract_note.items()},
        followups=template.followups,
        round=round_no,
        parent=parent,
    )


def _asked_followup(follow: Followup, function, module, signature, round_no, parent) -> Asked:
    target = function or module
    return Asked(
        key=_key(function, follow.id),
        id=follow.id,
        function=function,
        category=follow.category,
        type=follow.type,
        ask=_render(follow.ask, target, module, signature),
        scope="module" if function is None else "function",
        options=dict(follow.options),
        levels=list(follow.levels),
        conservative=follow.conservative,
        contract_note={k: _render(v, target, module, signature) for k, v in follow.contract_note.items()},
        followups=follow.followups,
        round=round_no,
        parent=parent,
    )


def _triggered(follow: Followup, answer) -> bool:
    matched = True
    if follow.when_answer:
        matched = (answer.answer in follow.when_answer) or (answer.effective in follow.when_answer)
    if follow.when_confidence_below is not None:
        matched = matched and answer.confidence < follow.when_confidence_below
    return matched
