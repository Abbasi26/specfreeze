"""Catalog loading, caps, and follow-up triggers."""

from specfreeze.catalog import expand_followups, instantiate, load_catalog, parse_catalog
from specfreeze.errors import CatalogError
from specfreeze.models import Answer, FunctionSpec
from specfreeze.resources import read_text

import pytest


def test_shipped_catalogs_load():
    general = parse_catalog(read_text("data/catalogs/general.yaml"), source="general")
    demo = parse_catalog(read_text("data/demo/catalog.yaml"), source="demo")
    assert general.version == 1
    assert len(general.questions) >= 30
    assert demo.name == "portion-demo"


def test_instantiate_caps_at_100(tmp_path):
    questions = "\n".join(
        f"  - id: q{i:03d}\n    category: behaviour\n    type: yes_no\n    ask: Question {i} for {{function}}?"
        for i in range(120)
    )
    path = tmp_path / "big.yaml"
    path.write_text(
        "version: 1\nname: big\ndescription: big\nmax_per_function: 100\nquestions:\n" + questions,
        encoding="utf-8",
    )
    catalog = load_catalog(path)
    functions = [FunctionSpec(name="alpha", signature="", purpose=""), FunctionSpec(name="beta", signature="", purpose="")]
    asked, truncated = instantiate(catalog, functions, "mod", max_questions=100)
    assert truncated
    assert len(asked) == 100


def test_followups_respect_and_trigger():
    catalog = parse_catalog(read_text("data/demo/catalog.yaml"))
    functions = [
        FunctionSpec("split_cents", "def split_cents(total: int, people: int) -> list[int]:", "split"),
        FunctionSpec("format_shares", "def format_shares(shares: list[int]) -> str:", "format"),
    ]
    asked, truncated = instantiate(catalog, functions, "portion", max_questions=100)
    assert not truncated
    by_key = {item.key: item for item in asked}
    answers = {
        "format_shares:separator": _answer("format_shares:separator", "comma_space", 0.9),
        "split_cents:docstring_style": _answer("split_cents:docstring_style", "none", 0.42),
        "split_cents:remainder": _answer("split_cents:remainder", "front", 0.91),
    }
    fresh = expand_followups(
        asked,
        answers,
        round_no=1,
        module="portion",
        signatures={fn.name: fn.signature for fn in functions},
        remaining=10,
    )
    keys = {item.key for item in fresh}
    assert "format_shares:separator_space" in keys
    assert "split_cents:docstring_mentions_errors" in keys
    assert "split_cents:remainder" not in keys
    assert by_key["split_cents:docstring_style"].conservative == "one_line"


def test_bad_catalog_is_rejected():
    with pytest.raises(CatalogError):
        parse_catalog("version: 1\nname: x\ndescription: x\nquestions: []\n")


def test_examples_match_the_packaged_demo():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pairs = {
        "examples/portion/SPEC.md": "data/demo/SPEC.md",
        "examples/portion/catalog.yaml": "data/demo/catalog.yaml",
        "examples/portion/tests/test_portion.py": "data/demo/test_portion.py",
    }
    for relative, packaged in pairs.items():
        assert (root / relative).read_text(encoding="utf-8") == read_text(packaged)


def _answer(key: str, value: str, confidence: float) -> Answer:
    return Answer(
        key=key,
        id=key.split(":", 1)[1],
        function=key.split(":", 1)[0],
        category="output",
        type="choice",
        ask="?",
        answer=value,
        confidence=confidence,
        rationale="",
        round=0,
        effective=value,
        effective_source="model",
    )


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("duplicate", [False, True])
def test_followup_sibling_ids(nested, duplicate):
    import json

    children = [
        {"id": "first", "type": "yes_no", "ask": "First?", "when_answer": "yes"},
        {
            "id": "first" if duplicate else "second",
            "type": "yes_no",
            "ask": "Second?",
            "when_answer": "yes",
        },
    ]
    question = {"id": "root", "type": "yes_no", "ask": "Root?"}
    if nested:
        question["followups"] = [
            {
                "id": "branch",
                "type": "yes_no",
                "ask": "Branch?",
                "when_answer": "yes",
                "followups": children,
            }
        ]
    else:
        question["followups"] = children
    text = json.dumps({"version": 1, "name": "siblings", "questions": [question]})

    if duplicate:
        parent = "branch" if nested else "root"
        with pytest.raises(CatalogError) as exc:
            parse_catalog(text, source="siblings.yaml")
        assert str(exc.value) == (
            f"siblings.yaml: duplicate follow-up first under {parent}"
        )
    else:
        catalog = parse_catalog(text, source="siblings.yaml")
        followups = catalog.questions[0].followups
        if nested:
            followups = followups[0].followups
        assert [follow.id for follow in followups] == ["first", "second"]


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("qtype", ["yes_no", "noul", "bool", "yesno"])
@pytest.mark.parametrize(
    "fields",
    [{}, {"options": {}, "levels": []},
     {"options": {"yes": "Yes", "no": "No"}}, {"levels": ["low", "high"]}],
)
def test_yes_no_followup_rejects_options_or_levels(nested, qtype, fields):
    import json

    followup = {
        "id": "child",
        "type": qtype,
        "ask": "Continue?",
        "when_answer": "yes",
        **fields,
    }
    question = {"id": "root", "type": "yes_no", "ask": "Start?"}
    if nested:
        question["followups"] = [{
            "id": "branch",
            "type": "yes_no",
            "ask": "Branch?",
            "when_answer": "yes",
            "followups": [followup],
        }]
    else:
        question["followups"] = [followup]
    text = json.dumps({
        "version": 1,
        "name": "validation",
        "questions": [question],
    })

    if fields.get("options") or fields.get("levels"):
        parent = "branch" if nested else "root"
        with pytest.raises(CatalogError) as exc:
            parse_catalog(text, source="validation.yaml")
        assert str(exc.value) == (
            f"validation.yaml: yes/no follow-up {parent}.child "
            "cannot set options or levels"
        )
    else:
        catalog = parse_catalog(text, source="validation.yaml")
        child = catalog.questions[0].followups[0]
        if nested:
            child = child.followups[0]
        assert child.type == "yes_no"


# Skipped unrelated parser hardening and rename work; no files changed or tests run.
# Risk: previously accepted yes/no follow-ups with non-empty options or levels will fail validation.
