"""Parse a specification markdown file into functions, constants, and examples.

The supported shape is documented in the README. Specs that do not declare
functions are left empty so the spec model can propose a decomposition.
"""

from __future__ import annotations

import ast
import re

from specforge.errors import SpecError
from specforge.models import FunctionSpec, SpecDocument

_HEADING_FN = re.compile(r"^#{2,4}\s+`([^`]+)`\s*$")
_CONST = re.compile(r"^-\s+`([A-Z][A-Z0-9_]*)\s*=\s*(.+?)`\s*(?:[—–-]\s*(.*))?$")
_IMPORT = re.compile(r"^-\s+`((?:import|from)\s+.+)`\s*$")
_EXAMPLE = re.compile(r"^-\s+`(.+)`\s*$")
_MODULE = re.compile(r"^Module:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?\s*$")
_CALLS = re.compile(r"^Calls:\s*(.+?)\s*$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_spec(text: str) -> SpecDocument:
    """Parse ``text``. Raises SpecError on a declared-but-invalid signature."""
    lines = text.splitlines()
    title = ""
    module = ""
    summary: list[str] = []
    constants: list[dict] = []
    imports: list[str] = []
    functions: list[FunctionSpec] = []
    section = "preamble"
    current: dict | None = None

    def close() -> None:
        nonlocal current
        if current is None:
            return
        functions.append(_finish_function(current))
        current = None

    for line in lines:
        if not title and line.startswith("# "):
            title = line[2:].strip()
            section = "summary"
            continue
        module_match = _MODULE.match(line.strip())
        if module_match and section in ("summary", "preamble") and current is None:
            module = module_match.group(1)
            continue
        if re.match(r"^##\s+Constants\s*$", line):
            close()
            section = "constants"
            continue
        if re.match(r"^##\s+Imports\s*$", line):
            close()
            section = "imports"
            continue
        if re.match(r"^##\s+Functions\s*$", line):
            close()
            section = "functions"
            continue
        heading = _HEADING_FN.match(line.strip())
        if heading and not line.startswith("# "):
            close()
            name, signature = _split_heading(heading.group(1))
            current = {"name": name, "signature": signature, "lines": []}
            section = "function"
            continue
        if line.startswith("## "):
            close()
            section = "other"
            continue
        if section == "summary":
            summary.append(line)
        elif section == "constants":
            matched = _CONST.match(line.strip())
            if matched:
                constants.append(_constant(matched.group(1), matched.group(2).strip(), (matched.group(3) or "").strip()))
        elif section == "imports":
            matched = _IMPORT.match(line.strip())
            if matched:
                imports.append(matched.group(1).strip())
        elif section == "function" and current is not None:
            current["lines"].append(line)

    close()
    names = [fn.name for fn in functions]
    if len(names) != len(set(names)):
        raise SpecError("the specification repeats a function name")
    if not module:
        module = _slug_module(title or "module")
    summary_text = "\n".join(summary).strip()
    return SpecDocument(
        title=title or module,
        module=module,
        summary=summary_text,
        constants=constants,
        imports=imports,
        functions=functions,
        raw=text,
    )


def normalize_signature(raw: str, expected_name: str | None = None) -> str:
    """Return a ``def ...:`` signature, or raise SpecError."""
    text = raw.strip()
    if not text:
        raise SpecError("empty signature")
    if not text.startswith("def "):
        text = "def " + text
    if not text.endswith(":"):
        text += ":"
    try:
        tree = ast.parse(text + "\n    pass\n")
    except SyntaxError as exc:
        raise SpecError(f"signature does not parse: {raw}") from exc
    fn = tree.body[0]
    if not isinstance(fn, ast.FunctionDef):
        raise SpecError(f"signature is not a function: {raw}")
    if expected_name and fn.name != expected_name:
        raise SpecError(f"signature name {fn.name} does not match {expected_name}")
    return text


def _split_heading(raw: str) -> tuple[str, str]:
    text = raw.strip()
    if text.startswith("def "):
        signature = normalize_signature(text)
        name = ast.parse(signature + "\n    pass\n").body[0].name  # type: ignore[attr-defined]
        return name, signature
    match = re.match(r"([A-Za-z_]\w*)\s*(\(.*)$", text)
    if match:
        return match.group(1), normalize_signature(text, match.group(1))
    if _IDENT.match(text):
        return text, ""
    raise SpecError(f"cannot read a function heading from {raw!r}")


def _constant(name: str, expr: str, purpose: str) -> dict:
    code = f"{name} = {expr}"
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise SpecError(f"constant {name} does not parse") from exc
    node = tree.body[0]
    if not isinstance(node, ast.Assign) or not _literal(node.value):
        raise SpecError(f"constant {name} must be a literal value")
    return {"name": name, "code": code, "purpose": purpose}


def _literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return isinstance(node.operand, ast.Constant)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_literal(elt) for elt in node.elts)
    if isinstance(node, ast.Dict):
        return all(_literal(k) and _literal(v) for k, v in zip(node.keys, node.values) if k is not None)
    return False


def _finish_function(draft: dict) -> FunctionSpec:
    purpose: list[str] = []
    examples: list[str] = []
    calls: list[str] = []
    mode = "purpose"
    for line in draft["lines"]:
        calls_match = _CALLS.match(line.strip())
        if calls_match and mode != "examples":
            calls = _parse_calls(calls_match.group(1))
            mode = "calls"
            continue
        if line.strip() == "Examples:":
            mode = "examples"
            continue
        if mode == "examples":
            example = _EXAMPLE.match(line.strip())
            if example:
                examples.append(example.group(1).strip())
            continue
        if mode == "purpose":
            purpose.append(line)
    return FunctionSpec(
        name=draft["name"],
        signature=draft["signature"],
        purpose="\n".join(purpose).strip(),
        calls=calls,
        examples=examples,
    )


def _parse_calls(text: str) -> list[str]:
    lowered = text.strip().lower()
    if lowered in {"none", "-", "n/a", "no"}:
        return []
    names = []
    for part in text.split(","):
        name = part.strip().strip("`")
        if not name:
            continue
        if not _IDENT.match(name):
            raise SpecError(f"Calls entry {name!r} is not a function name")
        names.append(name)
    return names


def _slug_module(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_").lower()
    if not slug or not slug[0].isalpha():
        slug = "module_" + slug
    return slug[:40] or "module"