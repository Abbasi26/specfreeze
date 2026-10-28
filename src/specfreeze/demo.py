"""Offline demo. The replies are recorded in this file. Nothing here opens a socket."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from specfreeze.config import Settings
from specfreeze.errors import PipelineError, ProviderError
from specfreeze.pipeline import run_all
from specfreeze.provider import FakeProvider
from specfreeze.resources import read_text
from specfreeze.session import Session
from specfreeze.store import RunStore
from specfreeze.textutil import parse_json_payload, stage_tag

_ANSWERS = {
    "module:integers_only": {"answer": "yes", "confidence": 0.96, "rationale": "The specification forbids floats."},
    "split_cents:negative_raises": {"answer": "yes", "confidence": 0.93, "rationale": "A negative total is not a share."},
    "split_cents:people_raises": {"answer": "yes", "confidence": 0.94, "rationale": "Zero people cannot receive a share."},
    "split_cents:remainder": {"answer": "front", "confidence": 0.91, "rationale": "The specification front-loads the remainder."},
    "split_cents:zero_total": {"answer": "yes", "confidence": 0.9, "rationale": "Zero cents is an even split."},
    "split_cents:docstring_style": {"answer": "none", "confidence": 0.42, "rationale": "The recording is unsure; the catalog default applies."},
    "split_cents:docstring_mentions_errors": {"answer": "yes", "confidence": 0.84, "rationale": "Callers need the ValueError cases."},
    "format_shares:empty_raises": {"answer": "yes", "confidence": 0.92, "rationale": "An empty receipt line is not a format."},
    "format_shares:negative_share_raises": {"answer": "yes", "confidence": 0.88, "rationale": "A negative share is a bad input."},
    "format_shares:separator": {"answer": "comma_space", "confidence": 0.86, "rationale": "The constant is a comma and a space."},
    "format_shares:separator_space": {"answer": "yes", "confidence": 0.95, "rationale": "SEPARATOR is exactly that string."},
    "format_shares:trailing_separator": {"answer": "no", "confidence": 0.9, "rationale": "The example has no trailing comma."},
    "module:public_docstring": {"answer": "yes", "confidence": 0.8, "rationale": "A one-line docstring is enough."},
}

_FOLLOWUPS = [
    {
        "id": "public_docstring",
        "function": None,
        "category": "output",
        "type": "yes_no",
        "ask": "Should every public function have a docstring?",
    }
]

_IMPLEMENT = {
    "split_cents": '''
def split_cents(total: int, people: int) -> list[int]:
    """Split total cents across people."""
    if total < 0 or people < 1:
        raise ValueError("total must be >= 0 and people must be >= 1")
    base = total // people
    return [base] * people
''',
    "format_shares": '''
def format_shares(shares: list[int]) -> str:
    """Format cent shares on one line."""
    if not shares or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in shares):
        raise ValueError("shares must be a non-empty list of non-negative integers")
    return SEPARATOR.join(str(item) for item in shares)
''',
}

# First repair widens the signature. The contract check must reject it.
_REPAIR_FIRST = {
    "split_cents": '''
def split_cents(total: int, people: int, *, mode: str = "front") -> list[int]:
    """Split total cents across people."""
    if total < 0 or people < 1:
        raise ValueError("total must be >= 0 and people must be >= 1")
    base, extra = divmod(total, people)
    return [base + (1 if i < extra else 0) for i in range(people)]
''',
}

_REPAIR_OK = {
    "split_cents": '''
def split_cents(total: int, people: int) -> list[int]:
    """Split cents; extra cents go to the earliest shares. Raises ValueError on a bad total or people."""
    if total < 0 or people < 1:
        raise ValueError("total must be >= 0 and people must be >= 1")
    base, extra = divmod(total, people)
    return [base + (1 if i < extra else 0) for i in range(people)]
''',
}


def recorded_reply(messages: list[dict[str, str]], model: str) -> str:
    """Return the checked-in reply for a tagged prompt. ``model`` is ignored."""
    del model
    user = messages[-1]["content"] if messages else ""
    stage = stage_tag(user, "stage")
    if stage == "answer":
        return _answer_json(user)
    if stage == "followups":
        return json.dumps(_FOLLOWUPS)
    if stage == "implement":
        return _fence(_IMPLEMENT[_need(user, "function")])
    if stage == "repair":
        name = _need(user, "function")
        if "signature deviates" in user:
            return _fence(_REPAIR_OK[name])
        if name not in _REPAIR_FIRST:
            raise ProviderError(f"no recorded repair for {name}")
        return _fence(_REPAIR_FIRST[name])
    raise ProviderError(f"no recorded reply for stage {stage or 'unknown'}")


def run_demo(out: Path, *, apply: bool = True, quiet: bool = False) -> int:
    """Run every stage offline into ``out``.

    The directory is created if needed. A non-empty directory that is not a
    previous specfreeze run is refused, so a demo cannot clobber unrelated files.
    """
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        marker = out / "run.json"
        if not marker.exists():
            raise PipelineError(
                f"{out} is not empty and is not a specfreeze run. "
                "Choose another --out directory."
            )
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PipelineError(f"{marker} is not valid JSON") from exc
        if not isinstance(data, dict) or data.get("tool") != "specfreeze":
            raise PipelineError(f"{out} is not a specfreeze run.")
    if not quiet:
        print(f"specfreeze demo writes only under {out}")
        print("Offline recorded provider. No network, no API key.")
    run = RunStore.open(out, force=True)
    settings = Settings(
        base_url="",
        api_key="",
        decision_model="recorded",
        spec_model="recorded",
        code_model="recorded",
        provider="fake",
    )
    provider = FakeProvider(recorded_reply, model="recorded")
    session = Session(settings, run, {role: provider for role in ("decision", "spec", "code")}, quiet=quiet)
    with tempfile.TemporaryDirectory(prefix="specfreeze-demo-src-") as tmp:
        root = Path(tmp)
        (root / "SPEC.md").write_text(read_text("data/demo/SPEC.md"), encoding="utf-8")
        (root / "catalog.yaml").write_text(read_text("data/demo/catalog.yaml"), encoding="utf-8")
        tests = root / "tests"
        tests.mkdir()
        (tests / "test_portion.py").write_text(read_text("data/demo/test_portion.py"), encoding="utf-8")
        code = run_all(
            session,
            spec_path=root / "SPEC.md",
            catalog_path=root / "catalog.yaml",
            tests_path=tests,
            rounds=2,
            fix_rounds=2,
            threshold=0.6,
            max_questions=100,
            model_followups=True,
            force=True,
            apply=apply,
            test_cmd=None,
            write_path=None,
        )
    if not quiet:
        _summary(run, code)
    return code


def _answer_json(user: str) -> str:
    section = user.split("Questions JSON:", 1)[-1]
    payload = parse_json_payload(section)
    rows = payload if isinstance(payload, list) else payload.get("questions") or payload.get("answers") or []
    answers = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id"))
        if key.startswith("header_"):
            recorded = {"answer": "function", "confidence": 0.9, "rationale": "Keep the frozen header."}
        elif key.startswith("coupling_"):
            recorded = {"answer": "both", "confidence": 0.9, "rationale": "Keep both signatures and fix the bodies."}
        elif key not in _ANSWERS:
            raise ProviderError(f"no recorded answer for {key}")
        else:
            recorded = _ANSWERS[key]
        answers.append({"id": key, **recorded})
    return json.dumps({"answers": answers})


def _need(user: str, name: str) -> str:
    value = stage_tag(user, name)
    if not value:
        raise ProviderError(f"recorded prompt is missing [{name}:...]")
    return value


def _fence(code: str) -> str:
    return "```python\n" + code.strip() + "\n```\n"


def _summary(run: RunStore, code: int) -> None:
    report = json.loads(run.read("report.json")) if run.exists("report.json") else {}
    fix = report.get("fix") or {}
    initial = (fix.get("initial") or {})
    final = (fix.get("final") or {})
    rejected = fix.get("rejected") or []
    contract_breaks = [item for item in rejected if item.get("reason") == "contract"]
    print("")
    print(f"module:    {run.path / 'portion.py'}")
    print(f"contract:  {run.path / 'contract.md'}")
    print(f"answers:   {run.path / 'answers.md'}")
    print(f"digest:    {report.get('digest', '')}")
    print(f"questions: {report.get('questions')}")
    if contract_breaks:
        print(f"repair:    rejected {len(contract_breaks)} candidate(s) that broke the frozen contract")
    if initial:
        print(
            "tests:     "
            f"{initial.get('passed')} passed / {initial.get('failed')} failed"
            f" -> {final.get('passed')} passed / {final.get('failed')} failed"
        )
    print("result:    " + ("tests passed" if code == 0 else "tests still failing"))
