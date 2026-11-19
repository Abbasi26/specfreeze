from pathlib import Path

from specfreeze.catalog import load_catalog
from specfreeze.catalogcheck import check_catalogs, summarize


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "parser" / "catalog.yaml"


def test_parser_example_catalog_is_valid():
    catalog = load_catalog(EXAMPLE)
    assert catalog.version == 1
    assert catalog.name == "parser-example"
    assert len(catalog.questions) > 0
    assert {question.type for question in catalog.questions} <= {"yes_no", "choice", "score"}
    assert len({question.id for question in catalog.questions}) == len(catalog.questions)
    assert all(question.conservative for question in catalog.questions)

    info = summarize(catalog)
    assert info["questions"] == len(catalog.questions)
    assert info["questions"] > 0
    assert sum(info["types"].values()) == info["questions"]
    assert set(info["types"]) <= {"yes_no", "choice", "score"}

    lines, failed = check_catalogs([EXAMPLE])
    assert failed == 0
    assert any("ok   " in line for line in lines)
