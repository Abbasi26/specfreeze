"""Assemble function parts under a frozen header, and splice a repair into a module."""

from __future__ import annotations

import ast

from specforge.errors import PipelineError

_MARKER = "# ---- {name} (specforge)"


def split_imports(code: str) -> tuple[list[str], str]:
    """Return import statements and the rest of ``code``."""
    tree = ast.parse(code)
    imports: list[str] = []
    pieces: list[str] = []
    for node in tree.body:
        segment = ast.get_source_segment(code, node)
        if not segment:
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(segment.strip())
        else:
            pieces.append(segment.strip())
    body = "\n\n".join(pieces)
    return imports, (body + "\n") if body else ""


def assemble(header: str, parts: list[tuple[str, str]]) -> str:
    """Build a module: frozen header, lifted imports, then one marked part per function."""
    seen = set(_import_lines(header))
    lifted: list[str] = []
    bodies: list[str] = []
    for name, code in parts:
        imports, body = split_imports(code)
        for statement in imports:
            if statement not in seen:
                seen.add(statement)
                lifted.append(statement)
        bodies.append(f"# ---- {name} (specforge)\n{body.strip()}\n")
    middle = ""
    if lifted:
        middle = "\n# imports lifted from function parts\n" + "\n".join(lifted) + "\n"
    return header.rstrip() + "\n" + middle + "\n" + "\n".join(bodies)


def replace_part(source: str, name: str, code: str) -> str:
    """Replace the marked part ``name`` and lift any new imports. The header stays put."""
    lines = source.splitlines()
    marker = f"# ---- {name} (specforge)"
    start = None
    end = len(lines)
    for index, line in enumerate(lines):
        if line.strip() == marker and start is None:
            start = index
            continue
        if start is not None and line.startswith("# ---- ") and line.rstrip().endswith("(specforge)"):
            end = index
            break
    if start is None:
        raise PipelineError(f"module has no part marker for {name}")
    imports, body = split_imports(code)
    replacement = [marker, *body.strip().splitlines(), ""]
    updated = lines[:start] + replacement + lines[end:]
    text = "\n".join(updated).rstrip() + "\n"
    text = _lift(text, imports)
    try:
        compile(text, name, "exec")
    except SyntaxError as exc:
        raise PipelineError(f"spliced module does not compile: {exc}") from exc
    return text


def split_group(code: str, names: list[str]) -> dict[str, str]:
    """Split a multi-function block into one part per contract function.

    A helper is attached to the contract function whose ``_<name>_`` prefix matches.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise PipelineError(f"repair did not parse: {exc}") from exc
    imports: list[str] = []
    buckets: dict[str, list[str]] = {name: [] for name in names}
    for node in tree.body:
        segment = ast.get_source_segment(code, node)
        if not segment:
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(segment.strip())
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, ast.FunctionDef):
            owner = node.name if node.name in buckets else None
            if owner is None:
                matches = [name for name in names if node.name.startswith(f"_{name}_")]
                owner = max(matches, key=len) if matches else None
            if owner is None:
                raise PipelineError(f"repair defined `{node.name}`, which is outside the coupled group")
            buckets[owner].append(segment.strip())
            continue
        raise PipelineError(f"repair contains a top-level {type(node).__name__}, which is not allowed")
    missing = [name for name in names if not any(part.startswith(f"def {name}(") or part.startswith(f"def {name} ") for part in buckets[name])]
    # The startswith check is brittle for `def name(`. Confirm via AST names instead.
    del missing
    for name in names:
        defined = False
        for part in buckets[name]:
            try:
                part_tree = ast.parse(part)
            except SyntaxError:
                continue
            if any(isinstance(node, ast.FunctionDef) and node.name == name for node in part_tree.body):
                defined = True
        if not defined:
            raise PipelineError(f"repair is missing `{name}`")
    prefix = ("\n".join(imports) + "\n\n") if imports else ""
    return {name: prefix + "\n\n".join(buckets[name]) + "\n" for name in names}


def _import_lines(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            segment = ast.get_source_segment(source, node)
            if segment:
                lines.append(segment.strip())
    return lines


def _lift(source: str, imports: list[str]) -> str:
    if not imports:
        return source
    present = set(_import_lines(source))
    fresh = [item for item in imports if item not in present]
    if not fresh:
        return source
    lines = source.splitlines()
    last = 0
    for index, line in enumerate(lines[:80]):
        if line.startswith("import ") or line.startswith("from "):
            last = index + 1
    if last == 0:
        # After the module docstring.
        last = 1
    lines[last:last] = ["# imports lifted from function parts", *fresh]
    return "\n".join(lines).rstrip() + "\n"