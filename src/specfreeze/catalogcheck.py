"""Validate question catalogs without a model."""

from __future__ import annotations

from pathlib import Path

from specfreeze.catalog import load_catalog
from specfreeze.errors import CatalogError
from specfreeze.models import Catalog


def count_followups(items) -> int:
    total = 0
    for item in items:
        total += len(item.followups) + count_followups(item.followups)
    return total


def summarize(catalog: Catalog) -> dict:
    by_type: dict[str, int] = {}
    categories: set[str] = set()
    for question in catalog.questions:
        by_type[question.type] = by_type.get(question.type, 0) + 1
        categories.add(question.category)
    return {
        "name": catalog.name,
        "questions": len(catalog.questions),
        "followups": count_followups(catalog.questions),
        "module_scope": sum(1 for q in catalog.questions if q.scope == "module"),
        "types": dict(sorted(by_type.items())),
        "categories": sorted(categories),
        "with_conservative": sum(1 for q in catalog.questions if q.conservative),
        "max_per_function": catalog.max_per_function,
    }


def check_catalogs(paths: list[Path]) -> tuple[list[str], int]:
    """Return printable lines and the number of failing files."""
    lines: list[str] = []
    failed = 0
    for path in paths:
        try:
            info = summarize(load_catalog(path))
        except (CatalogError, OSError, ValueError, TypeError) as exc:
            failed += 1
            lines.append(f"FAIL {path}: {exc}")
            continue
        types = ", ".join(f"{k} {v}" for k, v in info["types"].items())
        lines.append(
            f"ok   {path}: {info['name']}: {info['questions']} questions ({types}), "
            f"{info['followups']} follow-ups, {info['with_conservative']} with a conservative default"
        )
        if info["with_conservative"] < info["questions"]:
            lines.append(
                f"     note: {info['questions'] - info['with_conservative']} question(s) have no `conservative` value; "
                "a low-confidence answer is then used as given"
            )
    return lines, failed
