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


def check_catalogs(paths: list[Path]) -> tuple[list[str], int, list[dict]]:
    """Return printable lines, the number of failing files, and a JSON-capable summary per file.

    Each summary dict has ``path``, ``ok``, ``name``, ``questions``, ``followups``,
    ``types``, ``categories``, ``with_conservative`` and ``missing_conservative``.
    A failing file instead carries ``ok: false`` and an ``error`` string.
    """
    lines: list[str] = []
    summaries: list[dict] = []
    failed = 0
    for path in paths:
        try:
            info = summarize(load_catalog(path))
        except (CatalogError, OSError, ValueError, TypeError) as exc:
            failed += 1
            lines.append(f"FAIL {path}: {exc}")
            summaries.append(
                {
                    "path": str(path),
                    "ok": False,
                    "error": str(exc),
                }
            )
            continue
        missing = info["questions"] - info["with_conservative"]
        types = ", ".join(f"{k} {v}" for k, v in info["types"].items())
        lines.append(
            f"ok   {path}: {info['name']}: {info['questions']} questions ({types}), "
            f"{info['followups']} follow-ups, {info['with_conservative']} with a conservative default"
        )
        if missing:
            lines.append(
                f"     note: {missing} question(s) have no `conservative` value; "
                "a low-confidence answer is then used as given"
            )
        summaries.append(
            {
                "path": str(path),
                "ok": True,
                "name": info["name"],
                "questions": info["questions"],
                "followups": info["followups"],
                "types": info["types"],
                "categories": info["categories"],
                "with_conservative": info["with_conservative"],
                "missing_conservative": missing,
            }
        )
    return lines, failed, summaries
