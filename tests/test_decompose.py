"""A specification with no function headings is decomposed by the spec model."""

import json

from specfreeze.config import Settings
from specfreeze.pipeline import _fill_signatures, _load_or_decompose
from specfreeze.provider import FakeProvider
from specfreeze.session import Session
from specfreeze.store import RunStore
from specfreeze.textutil import stage_tag


SPEC = "# adder\n\nModule: `adder`\n\nAdd two integers and reject bool.\n"


def test_decompose_and_fill_signatures(tmp_path):
    run = RunStore.open(tmp_path / "run")
    run.write("SPEC.md", SPEC)

    def handler(messages, model):
        del model
        user = messages[-1]["content"]
        stage = stage_tag(user, "stage")
        if stage == "decompose":
            return json.dumps(
                {
                    "module": "adder",
                    "summary": "Add two integers.",
                    "constants": [],
                    "imports": [],
                    "functions": [
                        {
                            "name": "add",
                            "signature": "",
                            "purpose": "Return the sum.",
                            "calls": [],
                            "examples": ["add(1, 2) == 3"],
                        }
                    ],
                }
            )
        if stage == "contract":
            return json.dumps(
                {"functions": [{"name": "add", "signature": "def add(left: int, right: int) -> int:"}]}
            )
        raise AssertionError(stage)

    settings = Settings(
        base_url="",
        api_key="",
        decision_model="recorded",
        spec_model="recorded",
        code_model="recorded",
        provider="fake",
    )
    session = Session(
        settings,
        run,
        {role: FakeProvider(handler) for role in ("decision", "spec", "code")},
        quiet=True,
    )
    (tmp_path / "SPEC.md").write_text(SPEC, encoding="utf-8")
    spec = _load_or_decompose(session, tmp_path / "SPEC.md", force=True)
    assert spec.functions[0].name == "add"
    assert spec.functions[0].signature == ""
    filled = _fill_signatures(session, spec)
    assert filled.functions[0].signature == "def add(left: int, right: int) -> int:"
