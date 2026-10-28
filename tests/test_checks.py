"""Static contract checks, blame, and conflict detection."""

import sys
from types import ModuleType

import pytest

from specforge.assemble import assemble, replace_part, split_group
from specforge.checks import (
    blame,
    call_violations,
    detect_coupling,
    header_bindings,
    header_conflicts_in_output,
    index_tests,
    module_frames,
    module_has_attr,
    owner_map,
    part_check,
    signature_key,
    static_header_conflicts,
)

HEADER = '"""demo: x"""\nfrom __future__ import annotations\n\nSEPARATOR = ", "\n'
SIGS = {
    "split_cents": "def split_cents(total: int, people: int) -> list[int]:",
    "format_shares": "def format_shares(shares: list[int]) -> str:",
}


def test_signature_drift_is_rejected():
    bad = (
        "def split_cents(total: int, people: int, *, mode: str = 'front') -> list[int]:\n"
        "    return [0]\n"
    )
    problems = part_check("split_cents", bad, SIGS, {"SEPARATOR"}, {"SEPARATOR", "format_shares"})
    assert any("signature deviates" in item for item in problems)
    good = (
        "def split_cents(total: int, people: int) -> list[int]:\n"
        "    if total < 0 or people < 1:\n"
        "        raise ValueError('bad')\n"
        "    base, extra = divmod(total, people)\n"
        "    return [base + (1 if i < extra else 0) for i in range(people)]\n"
    )
    assert part_check("split_cents", good, SIGS, {"SEPARATOR"}, {"SEPARATOR", "format_shares"}) == []


def test_frozen_constant_and_foreign_function():
    code = "SEPARATOR = ','\n\ndef format_shares(shares: list[int]) -> str:\n    return ''\n"
    problems = part_check("format_shares", code, SIGS, {"SEPARATOR"}, {"SEPARATOR", "split_cents"})
    assert any("SEPARATOR" in item for item in problems)


def test_call_arity():
    source = assemble(
        HEADER,
        [
            ("split_cents", "def split_cents(total: int, people: int) -> list[int]:\n    return [total]\n"),
            ("format_shares", "def format_shares(shares: list[int]) -> str:\n    return split_cents(shares)\n"),
        ],
    )
    problems = call_violations(source, SIGS)
    assert problems
    assert "format_shares" in problems[0]


def test_blame_uses_the_test_when_the_traceback_stays_in_the_test():
    source = assemble(
        HEADER,
        [("split_cents", "def split_cents(total: int, people: int) -> list[int]:\n    return [total // people]\n")],
    )
    owners = owner_map(source)
    tests = index_tests([("tests/test_it.py", "def test_remainder_front():\n    assert split_cents(100, 3) == [34, 33, 33]\n")])
    output = "tests/test_it.py:2: in test_remainder_front\n    assert split_cents(100, 3) == [34, 33, 33]\n"
    assert blame("test_remainder_front", output, owners, "portion.py", ["split_cents"], tests) == "split_cents"


def test_coupling_from_traceback_span():
    text = (
        'File "/tmp/pair.py", line 12, in total\n'
        'File "/tmp/pair.py", line 8, in parse_item\n'
        "KeyError: 'cents'\n"
    )
    owners = {8: "parse_item", 12: "total"}
    found = detect_coupling(text, owners, "pair.py", ["parse_item", "total"])
    assert found is not None
    names, reason = found
    assert names == ["total", "parse_item"]
    assert "spans" in reason
    assert module_frames(text, "pair.py") == [12, 8]


def test_header_attribute_conflict():
    header = '"""cfg: x"""\nfrom __future__ import annotations\nimport json\n'
    source = header + "\n# ---- load_config (specforge)\ndef load_config(raw: str) -> dict:\n    return json.loads_nope(raw)\n"
    output = "AttributeError: module 'json' has no attribute 'loads_nope'\n"
    conflicts = header_conflicts_in_output(output, source, header)
    assert conflicts
    assert conflicts[0].local == "json"
    assert "loads_nope" in conflicts[0].attrs
    assert "load_config" in conflicts[0].functions


def test_replace_part_keeps_the_other_function():
    source = assemble(
        HEADER,
        [
            ("split_cents", "def split_cents(total: int, people: int) -> list[int]:\n    return [0]\n"),
            ("format_shares", "def format_shares(shares: list[int]) -> str:\n    return SEPARATOR.join('1')\n"),
        ],
    )
    updated = replace_part(
        source,
        "split_cents",
        "def split_cents(total: int, people: int) -> list[int]:\n    return [total]\n",
    )
    assert "return [total]" in updated
    assert "def format_shares" in updated
    assert signature_key(SIGS["format_shares"])
    grouped = split_group(
        "def split_cents(total: int, people: int) -> list[int]:\n    return [1]\n\n"
        "def format_shares(shares: list[int]) -> str:\n    return ''\n",
        ["split_cents", "format_shares"],
    )
    assert "def split_cents" in grouped["split_cents"]
    assert "def format_shares" not in grouped["split_cents"]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("from json import loads as json\nimport json\n", {"json": "json"}),
        ("import json\nfrom json import loads as json\n", {}),
        ("from json import loads as codec\nimport json as codec\n", {"codec": "json"}),
        ("import json as codec\nfrom json import loads as codec\n", {}),
        ("from json import loads as xml\nimport xml.etree\n", {"xml": "xml"}),
        (
            "from json import loads as codec\nimport xml.etree as codec\n",
            {"codec": "xml.etree"},
        ),
        ("import json\nfrom math import sqrt\n", {"json": "json"}),
        ("import json\nfrom\n", {}),
    ],
)
def test_header_bindings_follow_source_order(header, expected):
    assert header_bindings(header) == expected


def test_header_conflict_after_module_binding_is_restored():
    header = "from json import loads as json\nimport json\n"
    source = (
        header
        + "\n# ---- load_config (specforge)\n"
        + "def load_config(raw: str) -> dict:\n"
        + "    return json.loads_nope(raw)\n"
    )

    conflicts = static_header_conflicts(source, header)

    assert len(conflicts) == 1
    assert conflicts[0].local == "json"
    assert conflicts[0].module == "json"
    assert conflicts[0].attrs == ["loads_nope"]
    assert conflicts[0].functions == ["load_config"]


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("suffix", ["", ".child"])
def test_module_has_attr_skips_nonstdlib_modules(monkeypatch, cached, suffix):
    root = "_specforge_nonstdlib_probe"
    assert root not in sys.stdlib_module_names
    probes = []

    def attribute_probe(name):
        probes.append(("attribute", name))
        raise AttributeError(name)

    def import_probe(name):
        probes.append(("import", name))
        raise ImportError(name)

    monkeypatch.delitem(sys.modules, root, raising=False)
    if cached:
        module = ModuleType(root)
        module.__getattr__ = attribute_probe
        monkeypatch.setitem(sys.modules, root, module)

    monkeypatch.setattr(
        "specforge.checks.importlib.import_module", import_probe
    )

    assert module_has_attr(root + suffix, "missing") is None
    assert probes == []


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, ["function `target` is missing from the part"]),
        (1, []),
        (2, ["`target` is defined 2 times; the contract requires one"]),
        (3, ["`target` is defined 3 times; the contract requires one"]),
    ],
)
def test_part_check_requires_one_top_level_target(count, expected):
    definition = (
        "def target(value: int) -> int:\n"
        "    def target(value: int) -> int:\n"
        "        return value\n"
        "    return target(value)\n"
    )
    code = definition * count + (
        "def _target_helper(value: int) -> int:\n"
        "    return value\n"
    )

    assert part_check(
        "target",
        code,
        {"target": "def target(value: int) -> int:"},
        set(),
        set(),
    ) == expected

