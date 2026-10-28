"""Test-driven repair that cannot move a frozen signature.

A candidate is kept only when it still matches the contract and the test
result is strictly better: fewer failures, and not fewer passes. A candidate
that changes a signature is rejected before it is tested.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from specfreeze.assemble import replace_part, split_group
from specfreeze.checks import (
    blame,
    call_violations,
    detect_coupling,
    fail_blocks,
    header_bindings,
    header_conflicts_in_output,
    index_tests,
    missing_constants,
    module_signature_violations,
    owner_map,
    part_check,
)
from specfreeze.contract import constant_names, fingerprint, signatures_of
from specfreeze.decision import answer_questions
from specfreeze.errors import PipelineError
from specfreeze.models import Asked
from specfreeze.textutil import extract_code

_SYSTEM = (
    "You repair one Python function so its tests pass. "
    "The interface contract is frozen: keep the signature exactly, do not redefine frozen constants, "
    "and do not add parameters. "
    "Reply with one python code block and no other functions from the module."
)

_GROUP_SYSTEM = (
    "You repair a small group of Python functions that fail together. "
    "Keep every frozen signature exactly. Reply with one python code block containing only those functions."
)


@dataclass
class TestResult:
    passed: int
    failed: int
    errors: int
    failed_ids: list[str]
    output: str
    returncode: int

    @property
    def failure_count(self) -> int:
        return self.failed + self.errors

    @property
    def green(self) -> bool:
        return self.returncode == 0 and self.failed == 0 and self.errors == 0 and self.passed > 0

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "failed_ids": self.failed_ids,
            "returncode": self.returncode,
        }


@dataclass
class RepairReport:
    initial: TestResult
    final: TestResult
    rounds: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    accepted: list[dict] = field(default_factory=list)
    source: str = ""
    applied: bool = False

    def to_dict(self) -> dict:
        return {
            "initial": self.initial.to_dict(),
            "final": self.final.to_dict(),
            "rounds": self.rounds,
            "rejected": self.rejected,
            "accepted": self.accepted,
            "applied": self.applied,
            "green": self.final.green,
        }


def is_improvement(new: TestResult, old: TestResult) -> bool:
    """True when failures dropped and passes did not drop."""
    if new.failure_count < old.failure_count and new.passed >= old.passed:
        return True
    return False


def default_test_command() -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        "--no-header",
        "-p",
        "no:cacheprovider",
        "--noconftest",
        "tests",
    ]


def parse_test_command(command: str | None) -> list[str]:
    if not command:
        return default_test_command()
    parts = shlex.split(command)
    if not parts:
        raise PipelineError("--test-cmd is empty")
    return parts


def run_tests(
    module_name: str,
    source: str,
    tests: list[tuple[str, str]],
    command: list[str],
    *,
    timeout: int,
) -> TestResult:
    """Execute ``command`` in a scratch directory that contains the candidate module and the tests."""
    if not tests:
        raise PipelineError("fix needs tests inside the run directory (pass --tests)")
    with tempfile.TemporaryDirectory(prefix="specfreeze-tests-") as tmp:
        root = Path(tmp)
        (root / "pytest.ini").write_text("[pytest]\naddopts =\n", encoding="utf-8")
        (root / f"{module_name}.py").write_text(source, encoding="utf-8")
        for relative, content in tests:
            dest = root / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        env = os.environ.copy()
        env.pop("PYTEST_ADDOPTS", None)
        env["PYTHONPATH"] = str(root)
        try:
            proc = subprocess.run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            output = (proc.stdout or "") + "\n" + (proc.stderr or "")
            returncode = proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            output = stdout + "\n" + stderr + "\nTIMEOUT"
            returncode = 124
    return parse_pytest(output, returncode)


def _count(summary: str, word: str) -> int:
    match = re.search(rf"(\d+) {word}", summary)
    return int(match.group(1)) if match else 0


def parse_pytest(output: str, returncode: int) -> TestResult:
    summary = ""
    for line in output.splitlines():
        if re.search(r"\b\d+ (passed|failed|errors?)\b.* in [\d.]+s", line):
            summary = line
    passed = _count(summary, "passed")
    failed = _count(summary, "failed")
    errors = _count(summary, r"errors?")
    failed_ids = re.findall(r"^(?:FAILED|ERROR) (\S+)", output, re.M)
    if not summary:
        if returncode == 0:
            passed, failed, errors = 1, 0, 0
        else:
            passed, failed, errors = 0, 0, max(1, len(failed_ids))
    return TestResult(passed, failed, errors, failed_ids, output, returncode)


def load_test_files(run_path: Path) -> list[tuple[str, str]]:
    root = run_path / "tests"
    if not root.exists():
        return []
    files = []
    for path in sorted(root.rglob("*.py")):
        files.append((str(path.relative_to(run_path)), path.read_text(encoding="utf-8")))
    return files


def repair(
    session,
    contract: dict,
    source: str,
    tests: list[tuple[str, str]],
    *,
    command: list[str] | None = None,
    rounds: int = 4,
    attempts: int = 3,
    threshold: float = 0.6,
    spec_text: str = "",
    decisions_text: str = "",
) -> RepairReport:
    """Run the repair loop in memory. The caller decides whether to write the result."""
    if not command:
        command = default_test_command()
    module = contract["module"]
    filename = f"{module}.py"
    sigs = signatures_of(contract)
    constants = constant_names(contract)
    allowed = _allowed(contract)
    original_digest = contract.get("digest") or fingerprint(contract)
    result = run_tests(module, source, tests, command, timeout=session.settings.test_timeout)
    report = RepairReport(initial=result, final=result, source=source)
    session.say(
        f"tests before repair: {result.passed} passed, {result.failed} failed, {result.errors} errors"
    )
    if result.green or rounds <= 0:
        return report
    best_source = source
    best = result
    for round_no in range(1, rounds + 1):
        if best.green:
            break
        owners = owner_map(best_source)
        blocks = fail_blocks(best.output) or [("output", best.output[-4000:])]
        tests_index = index_tests(tests)
        groups: dict[str, list[tuple[str, str]]] = {}
        for title, body in blocks:
            owner = blame(title, body, owners, filename, list(sigs), tests_index)
            groups.setdefault(owner or "?", []).append((title, body))
        coupled = _coupled_groups(blocks, owners, filename, list(sigs))
        header_hits = header_conflicts_in_output(best.output, best_source, contract.get("header") or "")
        notes = []
        if coupled or header_hits:
            notes = _resolve_conflicts(
                session,
                contract,
                coupled,
                header_hits,
                blocks,
                round_no,
                threshold,
                spec_text,
            )
            if fingerprint(contract) != original_digest:
                raise PipelineError("repair changed the frozen contract digest; this is a bug")
        round_row = {"round": round_no, "before": best.to_dict(), "accepted": [], "rejected": []}
        progress = False
        handled: set[str] = set()
        for names, reason in coupled:
            for name in names:
                handled.add(name)
                groups.pop(name, None)
            code, rejections = _propose_group(
                session,
                names,
                reason,
                best_source,
                sigs,
                constants,
                allowed,
                contract,
                blocks,
                tests_index,
                spec_text,
                decisions_text,
                notes,
                attempts,
            )
            report.rejected.extend(rejections)
            round_row["rejected"].extend(rejections)
            if not code:
                continue
            trial = best_source
            try:
                parts = split_group(code, names)
                for name, part in parts.items():
                    part_problems = part_check(name, part, sigs, constants, allowed)
                    if part_problems:
                        rejection = {"function": "+".join(names), "reason": "contract", "violations": part_problems}
                        report.rejected.append(rejection)
                        round_row["rejected"].append(rejection)
                        trial = ""
                        break
                    trial = replace_part(trial, name, part)
            except PipelineError as exc:
                rejection = {"function": "+".join(names), "reason": "splice", "violations": [str(exc)]}
                report.rejected.append(rejection)
                round_row["rejected"].append(rejection)
                continue
            if not trial:
                continue
            if not _contract_holds(trial, sigs, constants):
                rejection = {"function": "+".join(names), "reason": "contract", "violations": ["spliced module left the contract"]}
                report.rejected.append(rejection)
                round_row["rejected"].append(rejection)
                continue
            trial_result = run_tests(module, trial, tests, command, timeout=session.settings.test_timeout)
            if is_improvement(trial_result, best):
                best_source, best = trial, trial_result
                progress = True
                accepted = {"function": "+".join(names), "tests": trial_result.to_dict()}
                report.accepted.append(accepted)
                round_row["accepted"].append(accepted)
                session.say(f"accepted coupled repair {names}: {trial_result.passed} passed, {trial_result.failure_count} failing")
            else:
                rejection = {"function": "+".join(names), "reason": "tests", "tests": trial_result.to_dict()}
                report.rejected.append(rejection)
                round_row["rejected"].append(rejection)
        for name, items in list(groups.items()):
            if name not in sigs or name in handled:
                continue
            hint = _header_hint(name, header_hits)
            code, rejections = _propose_one(
                session,
                name,
                best_source,
                sigs,
                constants,
                allowed,
                contract,
                items,
                tests_index,
                spec_text,
                decisions_text,
                notes,
                hint,
                attempts,
            )
            report.rejected.extend(rejections)
            round_row["rejected"].extend(rejections)
            if not code:
                continue
            try:
                trial = replace_part(best_source, name, code)
            except PipelineError as exc:
                rejection = {"function": name, "reason": "splice", "violations": [str(exc)]}
                report.rejected.append(rejection)
                round_row["rejected"].append(rejection)
                continue
            if not _contract_holds(trial, sigs, constants):
                rejection = {"function": name, "reason": "contract", "violations": module_signature_violations(trial, sigs)}
                report.rejected.append(rejection)
                round_row["rejected"].append(rejection)
                continue
            trial_result = run_tests(module, trial, tests, command, timeout=session.settings.test_timeout)
            if is_improvement(trial_result, best):
                best_source, best = trial, trial_result
                progress = True
                accepted = {"function": name, "tests": trial_result.to_dict()}
                report.accepted.append(accepted)
                round_row["accepted"].append(accepted)
                session.say(
                    f"accepted repair {name}: {best.passed} passed, {best.failed} failed, {best.errors} errors"
                )
            else:
                rejection = {"function": name, "reason": "tests", "tests": trial_result.to_dict()}
                report.rejected.append(rejection)
                round_row["rejected"].append(rejection)
                session.say(f"rejected repair {name}: tests did not improve")
        round_row["after"] = best.to_dict()
        report.rounds.append(round_row)
        if not progress:
            session.say(f"repair round {round_no} made no improvement; stopping")
            break
    report.source = best_source
    report.final = best
    return report


def _propose_one(session, name, source, sigs, constants, allowed, contract, failures, tests_index, spec_text, decisions_text, notes, hint, attempts):
    rejections = []
    violations: list[str] = []
    part = _part_source(source, name)
    base = _repair_prompt(name, sigs[name], part, contract, failures, tests_index, spec_text, decisions_text, notes, hint)
    for attempt in range(1, attempts + 1):
        user = base
        if violations:
            user += "\n\nThe previous attempt was rejected:\n- " + "\n- ".join(violations)
        raw = session.complete(
            "code",
            "repair",
            user,
            system=_SYSTEM,
            function=name,
            attempt=attempt,
            temperature=0.2,
            max_tokens=4096,
        )
        code = extract_code(raw)
        violations = part_check(name, code, sigs, constants, allowed)
        if not violations:
            return code, rejections
        rejection = {"function": name, "attempt": attempt, "reason": "contract", "violations": violations}
        rejections.append(rejection)
        session.say(f"rejected {name} attempt {attempt}: {violations[0]}")
    return None, rejections


def _propose_group(session, names, reason, source, sigs, constants, allowed, contract, blocks, tests_index, spec_text, decisions_text, notes, attempts):
    del tests_index
    rejections = []
    parts = "\n\n".join(_part_source(source, name) for name in names)
    sig_lines = "\n".join(f"- `{sigs[name]}`" for name in names)
    user = (
        f"Repair these functions together: {', '.join(names)}.\n"
        f"Why they are coupled: {reason}\n"
        "Return every function in one python block. Do not change signatures.\n\n"
        f"Frozen signatures:\n{sig_lines}\n\n"
        f"Current code:\n```python\n{parts}\n```\n\n"
        f"Frozen header:\n```python\n{contract.get('header', '')}\n```\n\n"
        f"Failing tests:\n```\n{_failure_text(blocks)[:6000]}\n```\n\n"
        f"Decision specification:\n{decisions_text[:4000]}\n\n"
        f"Written specification:\n{spec_text[:4000]}\n"
    )
    if notes:
        user += "\nConflict decisions (the frozen signatures still win):\n- " + "\n- ".join(notes) + "\n"
    violations: list[str] = []
    for attempt in range(1, attempts + 1):
        prompt = user
        if violations:
            prompt += "\n\nThe previous attempt was rejected:\n- " + "\n- ".join(violations)
        raw = session.complete(
            "code",
            "repair",
            prompt,
            system=_GROUP_SYSTEM,
            function="+".join(names),
            attempt=attempt,
            temperature=0.2,
            max_tokens=4096,
        )
        code = extract_code(raw)
        try:
            grouped = split_group(code, names)
        except PipelineError as exc:
            violations = [str(exc)]
            rejections.append({"function": "+".join(names), "attempt": attempt, "reason": "contract", "violations": violations})
            continue
        violations = []
        for name, part in grouped.items():
            violations.extend(part_check(name, part, sigs, constants, allowed))
        if not violations:
            return code, rejections
        rejections.append({"function": "+".join(names), "attempt": attempt, "reason": "contract", "violations": violations})
    return None, rejections


def _resolve_conflicts(session, contract, coupled, header_hits, blocks, round_no, threshold, spec_text) -> list[str]:
    notes = []
    questions = []
    if header_hits:
        for hit in header_hits:
            qid = f"header_{hit.local}"
            attrs = ", ".join(f"`{hit.local}.{attr}`" for attr in hit.attrs)
            questions.append(
                Asked(
                    key=qid,
                    id=qid,
                    function=None,
                    category="api",
                    type="choice",
                    ask=(
                        f"The frozen header binds `{hit.local}` to the module `{hit.module}`, "
                        f"and the failure mentions {attrs}, which that module does not have. "
                        "How should this be resolved?"
                    ),
                    scope="module",
                    options={
                        "function": "Keep the frozen header and fix the functions so they use a real attribute.",
                        "header": "The header binding itself is wrong and should change.",
                    },
                    round=round_no,
                )
            )
    for names, reason in coupled:
        qid = "coupling_" + "_".join(names)
        questions.append(
            Asked(
                key=qid,
                id=qid,
                function=None,
                category="api",
                type="choice",
                ask=(
                    f"Functions {', '.join(names)} appear in one failure ({reason}). "
                    "Their signatures are frozen. Which repair should be applied?"
                ),
                scope="module",
                options={
                    "both": "Change the bodies so they agree, and keep every frozen signature.",
                    "stop": "Do not guess; leave this failure for a person.",
                },
                round=round_no,
            )
        )
    if not questions:
        return notes
    # Tag the prompt so a recorded provider can tell conflict questions from the catalog.
    answers = answer_questions(session, questions, spec_text + "\n[conflict:decision]\n", threshold=threshold)
    for answer in answers:
        if answer.id.startswith("header_") and answer.effective == "header":
            note = (
                f"{answer.ask} Decision: header. "
                "v0.1 does not edit a frozen header, because that would change the contract for every function. "
                "The functions will be repaired to match the header instead."
            )
        elif answer.effective == "stop":
            note = f"{answer.ask} Decision: stop. No automatic edit for this conflict."
        else:
            note = f"{answer.ask} Decision: {answer.effective} (confidence {answer.confidence}). The frozen signatures stay."
        notes.append(note)
        contract.setdefault("addenda", []).append(
            {
                "round": round_no,
                "id": answer.id,
                "choice": answer.effective,
                "confidence": answer.confidence,
                "note": note,
            }
        )
    return notes


def _repair_prompt(name, signature, part, contract, failures, tests_index, spec_text, decisions_text, notes, hint) -> str:
    titles = []
    bodies = []
    for title, body in failures:
        titles.append(title.split("::")[-1].strip())
        bodies.append(f"### {title}\n{body}")
    test_src = []
    for title in titles:
        info = tests_index.get(re.sub(r"\[.*$", "", title).strip())
        if info and info.get("source"):
            test_src.append(info["source"])
    hint_block = f"\nHeader conflict: {hint}\n" if hint else ""
    note_block = ""
    if notes:
        note_block = "\nConflict decisions (frozen signatures win):\n- " + "\n- ".join(notes) + "\n"
    return (
        f"Repair only `{name}`.\n"
        f"Signature, exactly:\n`{signature}`\n"
        "Do not define other module functions. Helpers must be named "
        f"`_{name}_...`. Do not repeat the header.\n"
        f"{hint_block}{note_block}\n"
        f"Current part:\n```python\n{part}\n```\n\n"
        f"Frozen header:\n```python\n{contract.get('header', '')}\n```\n\n"
        f"Failing tests:\n```\n{chr(10).join(bodies)[:6000]}\n```\n\n"
        f"Test source:\n```python\n{chr(10).join(test_src)[:4000]}\n```\n\n"
        f"Decision specification:\n{decisions_text[:4000]}\n\n"
        f"Written specification:\n{spec_text[:4000]}\n"
    )


def _part_source(source: str, name: str) -> str:
    lines = source.splitlines()
    marker = f"# ---- {name} (specfreeze)"
    start = None
    end = len(lines)
    for index, line in enumerate(lines):
        if line.strip() == marker and start is None:
            start = index + 1
            continue
        if start is not None and line.startswith("# ---- ") and "(specfreeze)" in line:
            end = index
            break
    if start is None:
        return ""
    return "\n".join(lines[start:end]).strip() + "\n"


def _failure_text(blocks) -> str:
    return "\n\n".join(f"### {title}\n{body}" for title, body in blocks)


def _header_hint(name: str, hits) -> str:
    relevant = [hit for hit in hits if not hit.functions or name in hit.functions]
    if not relevant:
        return ""
    pieces = []
    for hit in relevant:
        attrs = ", ".join(hit.attrs)
        pieces.append(
            f"`{hit.local}` is the module `{hit.module}` in the frozen header; "
            f"`{hit.local}.{attrs}` does not exist. Qualify a real attribute. Do not change the header."
        )
    return " ".join(pieces)


def _coupled_groups(blocks, owners, filename, functions) -> list[tuple[list[str], str]]:
    found: list[tuple[list[str], str]] = []
    for _title, body in blocks:
        detected = detect_coupling(body, owners, filename, functions)
        if not detected:
            continue
        names, reason = detected
        merged = False
        for index, (existing, existing_reason) in enumerate(found):
            if set(existing) & set(names):
                union = list(dict.fromkeys(existing + names))
                found[index] = (union, existing_reason)
                merged = True
                break
        if not merged:
            found.append((names, reason))
    return found


def _allowed(contract: dict) -> set[str]:
    names = {fn["name"] for fn in contract["functions"]}
    names |= constant_names(contract)
    names |= set(header_bindings(contract.get("header") or ""))
    return names


def _contract_holds(source: str, signatures: dict[str, str], constants: set[str]) -> bool:
    if module_signature_violations(source, signatures):
        return False
    if missing_constants(source, constants):
        return False
    if call_violations(source, signatures):
        return False
    return True


def copy_tests(source: Path, run_path: Path) -> int:
    """Copy test files into the run directory. Returns how many files were copied."""
    files = _collect_tests(source)
    for relative, content in files:
        dest = run_path / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    return len(files)


def _collect_tests(source: Path) -> list[tuple[str, str]]:
    if source.is_file():
        name = source.name
        relative = name if name.startswith("tests/") else f"tests/{name}"
        return [(relative, source.read_text(encoding="utf-8"))]
    if not source.exists():
        raise PipelineError(f"tests path does not exist: {source}")
    files = []
    for path in sorted(source.rglob("*.py")):
        relative = Path("tests") / path.relative_to(source)
        files.append((str(relative), path.read_text(encoding="utf-8")))
    if not files:
        raise PipelineError(f"no Python tests under {source}")
    return files
