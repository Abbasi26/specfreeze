"""Specification parsing and the frozen contract."""

from specfreeze.contract import (
    assert_header_safe,
    build_contract,
    dump_contract,
    fingerprint,
    load_contract,
    render_header,
)
from specfreeze.decision import build_answer
from specfreeze.errors import ContractError, SpecError
from specfreeze.models import Asked
from specfreeze.resources import read_text
from specfreeze.specdoc import parse_spec

import pytest


def test_portion_spec_parses():
    spec = parse_spec(read_text("data/demo/SPEC.md"))
    assert spec.module == "portion"
    assert [fn.name for fn in spec.functions] == ["split_cents", "format_shares"]
    assert spec.functions[0].signature.startswith("def split_cents(")
    assert spec.constants[0]["name"] == "SEPARATOR"
    assert "split_cents(100, 3) == [34, 33, 33]" in spec.functions[0].examples


def test_vague_spec_has_no_functions():
    spec = parse_spec("# adder\n\nModule: `adder`\n\nAdd two integers.\n")
    assert spec.module == "adder"
    assert spec.functions == []


def test_bad_signature_rejected():
    with pytest.raises(SpecError):
        parse_spec("# m\n\nModule: `m`\n\n## Functions\n\n### `def broken(`\n")


def test_contract_fingerprint_is_stable_and_catches_edits():
    spec = parse_spec(read_text("data/demo/SPEC.md"))
    question = Asked(
        key="split_cents:negative_raises",
        id="negative_raises",
        function="split_cents",
        category="errors",
        type="yes_no",
        ask="Should it raise?",
        scope="function",
        conservative="yes",
        contract_note={"yes": "`split_cents` raises ValueError when total < 0."},
    )
    answer = build_answer(question, {"answer": "yes", "confidence": 0.9}, threshold=0.6)
    contract = build_contract(spec, [answer])
    assert contract["digest"] == fingerprint(contract)
    assert contract["functions"][0]["signature"].startswith("def split_cents(")
    assert any("ValueError" in line for line in contract["functions"][0]["errors"])
    again = fingerprint(contract)
    assert again == contract["digest"]
    contract["functions"][0]["signature"] = "def split_cents(total: int, people: int, mode: str = 'front') -> list[int]:"
    assert fingerprint(contract) != again
    contract["functions"][0]["signature"] = spec.functions[0].signature if spec.functions[0].signature.endswith(":") else spec.functions[0].signature
    # Restore via a fresh build and confirm addenda do not move the digest.
    fresh = build_contract(spec, [answer])
    fresh["frozen"] = True
    fresh["addenda"].append({"note": "later"})
    assert fingerprint(fresh) == fresh["digest"]
    loaded = load_contract(dump_contract(fresh))
    assert loaded["digest"] == fresh["digest"]
    fresh["digest"] = "0" * 64
    with pytest.raises(ContractError):
        load_contract(dump_contract(fresh))


def test_header_rejects_functions():
    with pytest.raises(ContractError):
        assert_header_safe('"""m: x"""\nfrom __future__ import annotations\n\ndef f():\n    return 1\n')


def test_low_confidence_uses_conservative_default():
    question = Asked(
        key="split_cents:docstring_style",
        id="docstring_style",
        function="split_cents",
        category="output",
        type="choice",
        ask="style?",
        scope="function",
        options={"one_line": "one", "none": "none"},
        conservative="one_line",
        contract_note={"one_line": "one line", "none": "none"},
    )
    answer = build_answer(question, {"answer": "none", "confidence": 0.42}, threshold=0.6)
    assert answer.answer == "none"
    assert answer.effective == "one_line"
    assert answer.effective_source == "conservative"


def test_p_yes_sets_confidence():
    question = Asked(
        key="module:integers_only",
        id="integers_only",
        function=None,
        category="behaviour",
        type="yes_no",
        ask="integers?",
        scope="module",
    )
    answer = build_answer(question, {"p_yes": 0.5}, threshold=0.6)
    assert answer.answer == "no" or answer.answer == "yes"
    assert answer.confidence == 0.0
    sure = build_answer(question, {"p_yes": 0.95}, threshold=0.6)
    assert sure.answer == "yes"
    assert sure.confidence == 0.9


def test_header_renders_constant():
    text = render_header("portion", "Split cents.", [], [{"name": "SEPARATOR", "code": 'SEPARATOR = ", "'}])
    assert 'SEPARATOR = ", "' in text
    assert_header_safe(text)