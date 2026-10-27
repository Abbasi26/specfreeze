"""Repair keeps the contract and accepts only a better test result."""

import json

from specforge.assemble import assemble
from specforge.checks import part_check
from specforge.config import Settings
from specforge.contract import build_contract, fingerprint
from specforge.errors import ProviderError
from specforge.models import Answer
from specforge.provider import FakeProvider
from specforge.repair import is_improvement, parse_pytest, repair
from specforge.session import Session
from specforge.specdoc import parse_spec
from specforge.store import RunStore
from specforge.textutil import stage_tag

PAIR = """
# pair

Module: `pair`

Parse a cents field and total it.

## Functions

### `parse_item(text: str) -> dict`

Read one cents value.

Calls: none

Examples:

- `parse_item("10") == {"cents": 10}`

### `total(text: str) -> int`

Total one item.

Calls: parse_item

Examples:

- `total("10") == 10`
"""

LOAD = """
# loader

Module: `loader`

Load one JSON object.

## Imports

- `import json`

## Functions

### `load_config(raw: str) -> dict`

Parse a JSON object.

Calls: none

Examples:

- `load_config("{}") == {}`
"""

BROKEN_PARSE = "def parse_item(text: str) -> dict:\n    raise KeyError('cents')\n"
BROKEN_TOTAL = "def total(text: str) -> int:\n    return int(parse_item(text)['cents'])\n"
FIXED_GROUP = (
    "def parse_item(text: str) -> dict:\n"
    "    return {'cents': int(text)}\n\n"
    "def total(text: str) -> int:\n"
    "    return int(parse_item(text)['cents'])\n"
)
BROKEN_LOAD = "def load_config(raw: str) -> dict:\n    return json.loads_nope(raw)\n"
FIXED_LOAD = "def load_config(raw: str) -> dict:\n    return json.loads(raw)\n"


def test_improvement_rule():
    red = parse_pytest("2 failed, 8 passed in 0.02s\nFAILED tests/test_a.py::test_one\n", 1)
    green = parse_pytest("10 passed in 0.02s\n", 0)
    same = parse_pytest("2 failed, 8 passed in 0.02s\n", 1)
    assert is_improvement(green, red)
    assert not is_improvement(same, red)
    assert red.failed == 2 and red.passed == 8


def test_coupled_repair_keeps_signatures(tmp_path):
    spec = parse_spec(PAIR)
    contract = _freeze(spec)
    source = assemble(contract["header"], [("parse_item", BROKEN_PARSE), ("total", BROKEN_TOTAL)])
    tests = [(
        "tests/test_pair.py",
        "from pair import total\n\ndef test_total():\n    assert total('10') == 10\n",
    )]
    session = _session(tmp_path, {"parse_item+total": FIXED_GROUP})
    before = contract["digest"]
    report = repair(session, contract, source, tests, command=None, rounds=2, attempts=2)
    assert report.final.green
    assert "def parse_item(text: str) -> dict:" in report.source
    assert "def total(text: str) -> int:" in report.source
    assert fingerprint(contract) == before
    assert contract["addenda"]
    assert "both" in contract["addenda"][0]["note"]


def test_header_conflict_is_recorded_and_not_applied(tmp_path):
    spec = parse_spec(LOAD)
    contract = _freeze(spec)
    source = assemble(contract["header"], [("load_config", BROKEN_LOAD)])
    tests = [(
        "tests/test_loader.py",
        "from loader import load_config\n\ndef test_loads_object():\n    assert load_config('{\"a\": 1}') == {'a': 1}\n",
    )]
    session = _session(tmp_path, {"load_config": FIXED_LOAD})
    before = contract["digest"]
    header = contract["header"]
    report = repair(session, contract, source, tests, command=None, rounds=2, attempts=2)
    assert report.final.green
    assert "json.loads(" in report.source
    assert "loads_nope" not in report.source
    assert contract["header"] == header
    assert "import json" in contract["header"]
    assert fingerprint(contract) == before
    assert any(item["choice"] == "header" for item in contract["addenda"])
    assert any("does not edit a frozen header" in item["note"] for item in contract["addenda"])


def test_part_check_message_is_what_the_demo_keys_on():
    problems = part_check(
        "split_cents",
        "def split_cents(total: int, people: int, *, mode: str = 'front') -> list[int]:\n    return []\n",
        {"split_cents": "def split_cents(total: int, people: int) -> list[int]:"},
        set(),
        set(),
    )
    assert any("signature deviates" in item for item in problems)


def _freeze(spec):
    contract = build_contract(spec, [])
    contract["frozen"] = True
    contract["digest"] = fingerprint(contract)
    return contract


def _session(tmp_path, repairs: dict[str, str]) -> Session:
    run = RunStore.open(tmp_path / "run")

    def handler(messages, model):
        del model
        user = messages[-1]["content"]
        stage = stage_tag(user, "stage")
        if stage == "answer":
            payload = json.loads(user.split("```json", 1)[1].split("```", 1)[0])
            rows = []
            for item in payload:
                qid = item["id"]
                if str(qid).startswith("header_"):
                    choice = "header"
                elif str(qid).startswith("coupling_"):
                    choice = "both"
                else:
                    choice = "yes"
                rows.append({"id": qid, "answer": choice, "confidence": 0.9, "rationale": "test"})
            return json.dumps({"answers": rows})
        if stage == "repair":
            name = stage_tag(user, "function")
            code = repairs.get(name)
            if code is None and "+" in name:
                code = repairs.get("+".join(reversed(name.split("+"))))
            if code is None:
                raise ProviderError(f"unexpected repair {name}")
            return "```python\n" + code + "\n```\n"
        raise ProviderError(f"unexpected stage {stage}")

    settings = Settings(
        base_url="",
        api_key="",
        decision_model="recorded",
        spec_model="recorded",
        code_model="recorded",
        provider="fake",
        test_timeout=30,
    )
    provider = FakeProvider(handler)
    return Session(settings, run, {role: provider for role in ("decision", "spec", "code")}, quiet=True)


def test_answer_import_is_used():
    # Keep the Answer import honest if a future assertion wants one.
    assert Answer is not None
