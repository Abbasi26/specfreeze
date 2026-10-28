"""Stage orchestration: ask, spec, build, fix, run.

Writes stay inside the run directory. ``fix`` replaces ``module.py`` only when
``apply`` is true, and only with a candidate that still matches the contract
and does not make the tests worse. ``--write`` is the explicit copy out of the
run directory.
"""

from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path

from specfreeze import __version__
from specfreeze.assemble import assemble
from specfreeze.catalog import expand_followups, instantiate, load_catalog, questions_from_model
from specfreeze.checks import (
    call_violations,
    header_bindings,
    missing_constants,
    module_signature_violations,
    part_check,
)
from specfreeze.config import Settings, load_settings
from specfreeze.contract import (
    assert_header_importable,
    build_contract,
    dump_contract,
    fingerprint,
    load_contract,
    render_contract_md,
    signatures_of,
    constant_names,
)
from specfreeze.decision import answer_questions, render_answers, uncertain
from specfreeze.distill import distill
from specfreeze.errors import PipelineError, ProviderError, SpecfreezeError
from specfreeze.models import Asked, Answer, FunctionSpec, SpecDocument
from specfreeze.provider import OpenAICompatibleProvider, loads_json_reply
from specfreeze.repair import (
    copy_tests,
    is_improvement,
    load_test_files,
    parse_test_command,
    repair,
)
from specfreeze.session import Session
from specfreeze.specdoc import normalize_signature, parse_spec
from specfreeze.store import RunStore
from specfreeze.textutil import extract_code

_DECISION_FOLLOWUP_SYSTEM = (
    "You write closed follow-up design questions. Reply with JSON only. Do not write code."
)
_DECOMPOSE_SYSTEM = (
    "You decompose a specification into Python functions. Reply with JSON only. Do not write code."
)
_CODE_SYSTEM = (
    "You implement one Python function against a frozen interface contract. "
    "Reply with one python code block. Keep the signature exactly. "
    "Do not define the other functions of the module."
)


def build_providers(settings: Settings) -> dict[str, OpenAICompatibleProvider]:
    """Construct a live provider per role. Refuses to invent a key or a URL."""
    missing = []
    if not any(settings.url_for(role) for role in ("decision", "spec", "code")):
        missing.append("SPECFREEZE_BASE_URL")
    if not any(settings.key_for(role) for role in ("decision", "spec", "code")):
        missing.append("SPECFREEZE_API_KEY")
    if not settings.decision_model:
        missing.append("SPECFREEZE_DECISION_MODEL")
    if not settings.code_model:
        missing.append("SPECFREEZE_CODE_MODEL")
    if missing:
        raise ProviderError(
            "missing " + ", ".join(missing) + ". "
            "specfreeze does not ship an API key. Run `specfreeze demo` for the offline example."
        )
    providers = {}
    for role in ("decision", "spec", "code"):
        url = settings.url_for(role)
        key = settings.key_for(role)
        if not url or not key:
            raise ProviderError(f"role {role} needs a base URL and an API key")
        if not settings.model_for(role):
            raise ProviderError(f"role {role} needs a model name")
        providers[role] = OpenAICompatibleProvider(
            base_url=url,
            api_key=key,
            fallbacks=settings.fallbacks,
            timeout=settings.timeout,
            deadline=settings.deadline,
            stream=settings.stream,
            extra_body=settings.extra_body,
            secrets=settings.secrets(),
        )
    return providers


def open_session(settings: Settings, run: RunStore, providers, *, quiet: bool) -> Session:
    return Session(settings, run, providers, quiet=quiet)


def ask_stage(
    session: Session,
    spec_path: Path,
    catalog_path: Path,
    *,
    rounds: int,
    threshold: float,
    max_questions: int,
    model_followups: bool,
    force: bool,
) -> list[Answer]:
    if session.run.exists("answers.json") and not force:
        session.say("Reusing answers.json (pass --force to ask again).")
        return _load_answers(session.run.read("answers.json"))
    spec = _load_or_decompose(session, spec_path, force=force)
    session.run.write("SPEC.md", spec.raw if spec.raw else spec_path.read_text(encoding="utf-8"))
    session.run.write_json("decomposition.json", spec.to_dict())
    catalog = load_catalog(catalog_path)
    session.run.write("catalog.yaml", catalog_path.read_text(encoding="utf-8"))
    limit = min(max_questions, 100)
    asked, truncated = instantiate(catalog, spec.functions, spec.module, max_questions=limit)
    if not asked:
        raise PipelineError("the catalog produced no questions for this specification")
    session.say(f"Asking {len(asked)} closed question(s) for {spec.module}.")
    answers = answer_questions(session, asked, spec.raw or spec.summary, threshold=threshold)
    all_asked = list(asked)
    signatures = {fn.name: fn.signature for fn in spec.functions}
    for round_no in range(1, rounds + 1):
        by_key = {item.key: item for item in answers}
        remaining = limit - len(answers)
        fresh = expand_followups(
            all_asked,
            by_key,
            round_no=round_no,
            module=spec.module,
            signatures=signatures,
            remaining=max(0, remaining),
        )
        if round_no == 1 and model_followups and any(uncertain(item, threshold) for item in answers):
            proposed = _model_followups(session, answers, spec, threshold, remaining=max(0, remaining - len(fresh)))
            known = {item.key for item in answers} | {item.key for item in fresh}
            for question in proposed:
                if question.key in known:
                    continue
                fresh.append(question)
                known.add(question.key)
                if len(answers) + len(fresh) >= limit:
                    break
        if not fresh:
            session.say(f"Follow-up round {round_no}: no new questions.")
            break
        session.say(f"Follow-up round {round_no}: {len(fresh)} question(s).")
        answers.extend(answer_questions(session, fresh, spec.raw or spec.summary, threshold=threshold))
        all_asked.extend(fresh)
    payload = {
        "schema_version": 1,
        "module": spec.module,
        "threshold": threshold,
        "rounds": rounds,
        "truncated": truncated,
        "question_count": len(answers),
        "answers": [item.to_dict() for item in answers],
    }
    session.run.write_json("answers.json", payload)
    session.run.write("answers.md", render_answers(spec.module, answers, threshold))
    session.say(f"Wrote {len(answers)} answers to {session.run.path / 'answers.md'}")
    return answers


def spec_stage(session: Session, *, threshold: float, force: bool) -> dict:
    if session.run.exists("contract.json") and session.run.exists("FROZEN") and not force:
        session.say("Reusing the frozen contract (pass --force to distill again).")
        return load_contract(session.run.read("contract.json"))
    spec = _spec_from_run(session.run)
    answers = _load_answers(session.run.read("answers.json"))
    spec = _fill_signatures(session, spec)
    session.run.write_json("decomposition.json", spec.to_dict())
    session.run.write("spec.md", distill(spec, answers, threshold=threshold))
    contract = build_contract(spec, answers)
    assert_header_importable(contract["header"])
    contract["frozen"] = True
    contract["digest"] = fingerprint(contract)
    _write_contract(session.run, contract)
    session.say(f"Froze contract {contract['digest'][:12]} ({len(contract['functions'])} signatures).")
    return contract


def build_stage(session: Session, *, force: bool, write_path: Path | None) -> str:
    contract = load_contract(session.run.read("contract.json"))
    if not contract.get("frozen"):
        raise PipelineError("build requires a frozen contract; run `specfreeze spec` first")
    if session.run.exists("module.py") and session.run.exists("generated/initial.py") and not force:
        session.say("Reusing module.py (pass --force to generate again).")
        source = session.run.read("module.py")
    else:
        source = _generate(session, contract)
        session.run.write("generated/initial.py", source)
        session.run.write("module.py", source)
        session.run.write(f"{contract['module']}.py", source)
    if write_path is not None:
        _export(source, write_path, session)
    session.say(f"Generated module: {session.run.path / (contract['module'] + '.py')}")
    return source


def fix_stage(
    session: Session,
    *,
    test_cmd: str | None,
    rounds: int,
    apply: bool,
    threshold: float,
    write_path: Path | None,
) -> int:
    contract = load_contract(session.run.read("contract.json"))
    module_name = contract["module"]
    source_path = session.run.path / f"{module_name}.py"
    if not source_path.exists():
        source_path = session.run.path / "module.py"
    if not source_path.exists():
        raise PipelineError("fix requires a generated module; run `specfreeze build` first")
    source = source_path.read_text(encoding="utf-8")
    tests = load_test_files(session.run.path)
    if not tests:
        raise PipelineError("fix needs tests in the run directory; pass --tests on run, or copy tests/ into the run")
    spec_text = session.run.read("SPEC.md") if session.run.exists("SPEC.md") else ""
    decisions = session.run.read("spec.md") if session.run.exists("spec.md") else ""
    before = source
    report = repair(
        session,
        contract,
        source,
        tests,
        command=parse_test_command(test_cmd),
        rounds=rounds,
        threshold=threshold,
        spec_text=spec_text,
        decisions_text=decisions,
    )
    # Conflict addenda are part of the decision record. They do not change the digest.
    if fingerprint(contract) != load_contract(session.run.read("contract.json")).get("digest"):
        raise PipelineError("fix changed the frozen digest")
    _write_contract(session.run, contract)
    session.run.write_json("fix.json", report.to_dict())
    diff = _diff(before, report.source, f"{module_name}.py")
    session.run.write("preview.diff", diff or "(no change)\n")
    improved = report.source != before and (report.accepted or is_improvement(report.final, report.initial))
    if improved:
        session.run.write("preview/module.py", report.source)
        session.run.write(f"preview/{module_name}.py", report.source)
    if apply and improved:
        session.run.write("module.py", report.source)
        session.run.write(f"{module_name}.py", report.source)
        report.applied = True
        session.run.write_json("fix.json", report.to_dict())
        session.say(f"Applied the repair to {module_name}.py.")
        if write_path is not None:
            _export(report.source, write_path, session)
    elif apply and not improved:
        session.say("No repair improved the tests. module.py was left unchanged.")
    else:
        session.say("Preview only. module.py was not changed. Re-run with --apply to keep an accepted repair.")
        if diff.strip():
            _print_preview(diff)
    _write_report(session, contract, report)
    if report.final.green and (apply or report.initial.green):
        return 0
    if not apply and improved and report.final.green:
        return 0
    if report.final.green and apply:
        return 0
    if report.initial.green:
        return 0
    return 1


def run_all(
    session: Session,
    *,
    spec_path: Path,
    catalog_path: Path,
    tests_path: Path | None,
    rounds: int,
    fix_rounds: int,
    threshold: float,
    max_questions: int,
    model_followups: bool,
    force: bool,
    apply: bool,
    test_cmd: str | None,
    write_path: Path | None,
) -> int:
    if tests_path is not None:
        count = copy_tests(tests_path, session.run.path)
        session.say(f"Copied {count} test file(s) into the run directory.")
    ask_stage(
        session,
        spec_path,
        catalog_path,
        rounds=rounds,
        threshold=threshold,
        max_questions=max_questions,
        model_followups=model_followups,
        force=force,
    )
    spec_stage(session, threshold=threshold, force=force)
    build_stage(session, force=force, write_path=None)
    if not load_test_files(session.run.path):
        session.say("No tests were provided, so the fix stage was skipped.")
        _write_report(session, load_contract(session.run.read("contract.json")), None)
        if write_path is not None:
            name = json.loads(session.run.read("contract.json"))["module"]
            _export(session.run.read(f"{name}.py"), write_path, session)
        return 0
    return fix_stage(
        session,
        test_cmd=test_cmd,
        rounds=fix_rounds,
        apply=apply,
        threshold=threshold,
        write_path=write_path,
    )


def _generate(session: Session, contract: dict) -> str:
    spec_text = session.run.read("SPEC.md") if session.run.exists("SPEC.md") else ""
    decisions = session.run.read("spec.md") if session.run.exists("spec.md") else ""
    tests = "\n\n".join(content for _path, content in load_test_files(session.run.path))
    sigs = signatures_of(contract)
    constants = constant_names(contract)
    allowed = set(sigs) | set(constants) | set(header_bindings(contract.get("header") or ""))
    parts: dict[str, str] = {}
    for fn in contract["functions"]:
        parts[fn["name"]] = _implement(
            session,
            fn["name"],
            contract,
            spec_text,
            decisions,
            tests,
            sigs,
            constants,
            allowed,
            extra="",
        )
        session.run.write(f"parts/{fn['name']}.py", parts[fn["name"]])
    ordered = [(fn["name"], parts[fn["name"]]) for fn in contract["functions"]]
    source = assemble(contract["header"], ordered)
    problems = _module_problems(source, sigs, constants)
    if problems:
        session.say(f"Consistency check found {len(problems)} issue(s); regenerating those parts once.")
        blamed = _blamed_functions(problems, sigs)
        for name in blamed:
            parts[name] = _implement(
                session,
                name,
                contract,
                spec_text,
                decisions,
                tests,
                sigs,
                constants,
                allowed,
                extra="\n\nThe previous version failed the contract check:\n- " + "\n- ".join(problems),
            )
            session.run.write(f"parts/{name}.py", parts[name])
        source = assemble(contract["header"], [(fn["name"], parts[fn["name"]]) for fn in contract["functions"]])
        problems = _module_problems(source, sigs, constants)
    if any("signature deviates" in item or "defined" in item for item in problems):
        raise PipelineError("generated module does not match the frozen contract:\n- " + "\n- ".join(problems))
    if problems:
        session.run.write("consistency.txt", "\n".join(problems) + "\n")
        session.say("Module still has non-signature issues. They are listed in consistency.txt for the fix stage.")
    else:
        session.run.write("consistency.txt", "ok\n")
    compile(source, contract["module"] + ".py", "exec")
    return source


def _implement(session, name, contract, spec_text, decisions, tests, sigs, constants, allowed, extra: str) -> str:
    signature = sigs[name]
    user = (
        f"Implement only `{name}` for module `{contract['module']}`.\n"
        f"Signature, exactly:\n`{signature}`\n"
        f"Helpers must be named `_{name}_...`.\n"
        "Do not repeat the header. Do not define other module functions. "
        "Do not reassign frozen constants.\n\n"
        f"Frozen header:\n```python\n{contract['header']}\n```\n\n"
        f"Contract:\n{session.run.read('contract.md')[:8000]}\n\n"
        f"Decision specification:\n{decisions[:6000]}\n\n"
        f"Tests that must pass:\n```python\n{tests[:6000]}\n```\n\n"
        f"Written specification:\n{spec_text[:6000]}\n"
        f"{extra}"
    )
    violations: list[str] = []
    last = ""
    for attempt in range(1, 4):
        prompt = user
        if violations:
            prompt += "\n\nThe previous attempt was rejected:\n- " + "\n- ".join(violations)
        raw = session.complete(
            "code",
            "implement",
            prompt,
            system=_CODE_SYSTEM,
            function=name,
            attempt=attempt,
            temperature=0.2,
            max_tokens=4096,
        )
        last = extract_code(raw)
        violations = part_check(name, last, sigs, constants, allowed)
        if not violations:
            return last
        session.say(f"{name} attempt {attempt} rejected: {violations[0]}")
    raise PipelineError(f"could not implement `{name}` inside the contract:\n- " + "\n- ".join(violations))


def _model_followups(session, answers: list[Answer], spec: SpecDocument, threshold: float, *, remaining: int) -> list[Asked]:
    if remaining <= 0:
        return []
    lines = []
    for answer in answers:
        if uncertain(answer, threshold):
            lines.append(f"- {answer.key}: {answer.ask} -> {answer.answer} (confidence {answer.confidence})")
    if not lines:
        return []
    limit = min(5, remaining)
    user = (
        "[task:followups]\n\n"
        f"These answers are uncertain (confidence below {threshold}). "
        f"Write up to {limit} NEW closed follow-up questions that would make the decision unambiguous. "
        "Do not repeat a question. Types are yes_no, choice (2-6 options), or score (2-5 levels).\n"
        "Return a JSON list only:\n"
        '[{"id": "short_slug", "function": "name or null", "category": "behaviour", '
        '"type": "yes_no", "ask": "...", "options": {"key": "label"}}]\n\n'
        "Uncertain answers:\n" + "\n".join(lines) + "\n\n"
        f"Specification:\n{(spec.raw or spec.summary)[:8000]}\n"
    )
    raw = session.complete(
        "spec",
        "followups",
        user,
        system=_DECISION_FOLLOWUP_SYSTEM,
        temperature=0.2,
        max_tokens=2500,
    )
    try:
        payload = loads_json_reply(raw)
    except ProviderError:
        session.say("Model follow-ups were not valid JSON; continuing with the catalog only.")
        return []
    try:
        return questions_from_model(payload, round_no=1, module=spec.module)[:limit]
    except SpecfreezeError as exc:
        session.say(f"Ignoring model follow-ups: {exc}")
        return []


def _load_or_decompose(session: Session, spec_path: Path, *, force: bool) -> SpecDocument:
    if not spec_path.exists():
        raise PipelineError(f"specification not found: {spec_path}")
    raw = spec_path.read_text(encoding="utf-8")
    if session.run.exists("decomposition.json") and not force:
        data = json.loads(session.run.read("decomposition.json"))
        spec = SpecDocument.from_dict(data, raw=raw)
        if spec.functions:
            return spec
    spec = parse_spec(raw)
    if spec.functions:
        return spec
    session.say("The specification does not list functions. Asking the spec model to decompose it.")
    user = (
        "[task:decompose]\n\n"
        "Decompose the specification into Python functions. Return JSON only:\n"
        '{"module": "name", "summary": "...", "constants": [{"name": "NAME", "code": "NAME = 1", "purpose": "..."}], '
        '"imports": [], "functions": [{"name": "...", "signature": "def name(...) -> ...:", '
        '"purpose": "...", "calls": [], "examples": ["name(1) == 1"]}]}\n'
        "Every signature must parse as Python. Public names must come from the specification "
        "when it names them.\n\n"
        f"{raw}"
    )
    text = session.complete("spec", "decompose", user, system=_DECOMPOSE_SYSTEM, temperature=0.0, max_tokens=4000)
    try:
        data = loads_json_reply(text)
    except ProviderError as exc:
        raise PipelineError(f"the spec model did not return a decomposition: {exc}") from exc
    if not isinstance(data, dict) or not data.get("functions"):
        raise PipelineError("the decomposition did not list any functions")
    functions = []
    for item in data["functions"]:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        signature = ""
        if item.get("signature"):
            signature = normalize_signature(str(item["signature"]), str(item["name"]))
        functions.append(
            FunctionSpec(
                name=str(item["name"]),
                signature=signature,
                purpose=str(item.get("purpose") or ""),
                calls=[str(x) for x in item.get("calls") or []],
                examples=[str(x) for x in item.get("examples") or []],
            )
        )
    if not functions:
        raise PipelineError("the decomposition did not list any functions")
    module = str(data.get("module") or spec.module)
    return SpecDocument(
        title=spec.title or module,
        module=module,
        summary=str(data.get("summary") or spec.summary),
        constants=list(data.get("constants") or []),
        imports=list(data.get("imports") or []),
        functions=functions,
        raw=raw,
    )


def _fill_signatures(session: Session, spec: SpecDocument) -> SpecDocument:
    missing = [fn for fn in spec.functions if not fn.signature]
    if not missing:
        return spec
    session.say("Some functions have no signature. Asking the spec model to draft them.")
    user = (
        "[task:contract]\n\n"
        "Draft the missing Python signatures. Return JSON only:\n"
        '{"functions": [{"name": "...", "signature": "def name(...) -> ...:"}]}\n'
        "Do not change names. Signatures must parse.\n\n"
        + json.dumps(spec.to_dict(), ensure_ascii=False, indent=1)
    )
    text = session.complete("spec", "contract", user, system=_DECOMPOSE_SYSTEM, temperature=0.0, max_tokens=3000)
    try:
        data = loads_json_reply(text)
    except ProviderError as exc:
        raise PipelineError(f"the spec model did not return signatures: {exc}") from exc
    by_name = {str(item.get("name")): item for item in (data.get("functions") or []) if isinstance(item, dict)}
    for fn in missing:
        item = by_name.get(fn.name)
        if not item or not item.get("signature"):
            raise PipelineError(f"no signature was drafted for {fn.name}")
        fn.signature = normalize_signature(str(item["signature"]), fn.name)
    return spec


def _spec_from_run(run: RunStore) -> SpecDocument:
    if not run.exists("decomposition.json"):
        raise PipelineError("spec stage needs decomposition.json; run `specfreeze ask` first")
    raw = run.read("SPEC.md") if run.exists("SPEC.md") else ""
    return SpecDocument.from_dict(json.loads(run.read("decomposition.json")), raw=raw)


def _load_answers(text: str) -> list[Answer]:
    data = json.loads(text)
    return [Answer.from_dict(item) for item in data.get("answers") or []]


def _write_contract(run: RunStore, contract: dict) -> None:
    contract["digest"] = fingerprint(contract)
    run.write("header.py", contract["header"])
    run.write("contract.md", render_contract_md(contract))
    run.write("contract.json", dump_contract(contract))
    run.write("FROZEN", contract["digest"] + "\n")


def _write_report(session: Session, contract: dict, report) -> None:
    payload = {
        "tool": "specfreeze",
        "version": __version__,
        "module": contract["module"],
        "digest": contract.get("digest"),
        "functions": [fn["name"] for fn in contract["functions"]],
        "fix": None if report is None else report.to_dict(),
    }
    if session.run.exists("answers.json"):
        answers = json.loads(session.run.read("answers.json"))
        payload["questions"] = answers.get("question_count")
    session.run.write_json("report.json", payload)


def _module_problems(source: str, signatures: dict[str, str], constants: set[str]) -> list[str]:
    return (
        module_signature_violations(source, signatures)
        + missing_constants(source, constants)
        + call_violations(source, signatures)
    )


def _blamed_functions(problems: list[str], signatures: dict[str, str]) -> list[str]:
    found = []
    for name in signatures:
        if any(name in item for item in problems):
            found.append(name)
    return found or list(signatures)


def _diff(before: str, after: str, name: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
        )
    )


def _print_preview(diff: str) -> None:
    lines = diff.splitlines()
    preview = lines[:120]
    print("\n".join(preview))
    if len(lines) > 120:
        print(f"... {len(lines) - 120} more diff lines in preview.diff")


def _export(source: str, destination: Path, session: Session) -> None:
    destination = destination.expanduser()
    previous = destination.read_text(encoding="utf-8") if destination.exists() else ""
    diff = _diff(previous, source, destination.name)
    session.say(f"Preview of --write {destination}:")
    if diff.strip():
        _print_preview(diff)
    else:
        session.say("(file already matches)")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source, encoding="utf-8")
    session.say(f"Wrote {destination}.")


def make_live_session(out: Path, *, force: bool, quiet: bool) -> Session:
    settings = load_settings()
    if settings.provider == "fake":
        raise ProviderError("SPECFREEZE_PROVIDER=fake has no recording. Use `specfreeze demo` for the offline run.")
    run = RunStore.open(out, force=force)
    return open_session(settings, run, build_providers(settings), quiet=quiet)


def copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
