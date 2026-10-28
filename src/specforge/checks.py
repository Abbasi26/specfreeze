"""Static checks against a frozen contract.

These checks reject code that would change a signature, redefine a frozen
constant, or call a module attribute the frozen header's import does not have.
They do not execute the candidate.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import re
import sys
from dataclasses import dataclass

from specforge.errors import ContractError

_ATTR = re.compile(r"module '([\w.]+)' has no attribute '(\w+)'")
_MARKER = re.compile(r"^# ---- (\S+) \(specforge\)\s*$")
_BUILTIN = set(dir(builtins))


@dataclass(frozen=True)
class Params:
    name: str
    pos: tuple[str, ...]
    required: int
    kwonly: tuple[str, ...]
    kwonly_required: tuple[str, ...]
    vararg: bool
    kwarg: bool


@dataclass
class HeaderConflict:
    local: str
    module: str
    attrs: list[str]
    functions: list[str]
    lines: list[int]


def parse_params(signature: str) -> Params:
    text = signature.strip()
    if not text.endswith(":"):
        text += ":"
    if not text.startswith("def "):
        text = "def " + text
    try:
        fn = ast.parse(text + "\n    pass\n").body[0]
    except SyntaxError as exc:
        raise ContractError(f"signature does not parse: {signature}") from exc
    if not isinstance(fn, ast.FunctionDef):
        raise ContractError(f"signature is not a function: {signature}")
    return _params(fn)


def signature_key(signature: str) -> str:
    """Stable comparison key for a signature, including annotations and defaults."""
    text = signature.strip()
    if not text.endswith(":"):
        text += ":"
    if not text.startswith("def "):
        text = "def " + text
    fn = ast.parse(text + "\n    pass\n").body[0]
    assert isinstance(fn, ast.FunctionDef)
    returns = ast.unparse(fn.returns) if fn.returns else ""
    return f"{fn.name}|{ast.unparse(fn.args)}|{returns}"


def function_key(fn: ast.FunctionDef) -> str:
    returns = ast.unparse(fn.returns) if fn.returns else ""
    return f"{fn.name}|{ast.unparse(fn.args)}|{returns}"


def header_bindings(header: str) -> dict[str, str]:
    """Map local import names to modules, applying rebindings in source order."""
    try:
        tree = ast.parse(header)
    except SyntaxError:
        return {}
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                found[local] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                found.pop(alias.asname or alias.name, None)
    return found


def owner_map(source: str) -> dict[int, str]:
    """Line number to the part marker that owns it. Lines before the first marker are ``_header``."""
    owners: dict[int, str] = {}
    current = "_header"
    for index, line in enumerate(source.splitlines(), 1):
        match = _MARKER.match(line)
        if match:
            current = match.group(1)
        owners[index] = current
    return owners


def part_check(
    name: str,
    code: str,
    signatures: dict[str, str],
    constants: set[str],
    allowed_names: set[str],
) -> list[str]:
    """Violations for one function part. An empty list means the part may be assembled."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]
    violations: list[str] = []
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    names = [fn.name for fn in functions]
    if name not in names:
        violations.append(f"function `{name}` is missing from the part")
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, ast.FunctionDef):
            if node.name == name:
                expected = signatures.get(name)
                if expected and function_key(node) != signature_key(expected):
                    violations.append(
                        "signature deviates from the frozen contract: "
                        f"expected `{expected}`, got `{_source_signature(node)}`"
                    )
            elif node.name in signatures:
                violations.append(f"part defines `{node.name}`, which belongs to another function")
            elif not node.name.startswith(f"_{name}_"):
                violations.append(f"helper `{node.name}` must be named `_{name}_...`")
            continue
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in constants:
                    violations.append(f"frozen constant `{target.id}` is reassigned")
            violations.append("top-level assignment is not allowed in a function part")
            continue
        violations.append(f"top-level {type(node).__name__} is not allowed in a function part")
    violations.extend(_undefined(tree, allowed_names | set(names)))
    return violations


def module_signature_violations(source: str, signatures: dict[str, str]) -> list[str]:
    """Every contract function must appear once, with the frozen signature."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"module does not parse: {exc}"]
    found: dict[str, list[ast.FunctionDef]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            found.setdefault(node.name, []).append(node)
    violations = []
    for name, signature in signatures.items():
        defs = found.get(name, [])
        if len(defs) != 1:
            violations.append(f"`{name}` is defined {len(defs)} times; the contract requires one")
            continue
        if function_key(defs[0]) != signature_key(signature):
            violations.append(
                "signature deviates from the frozen contract: "
                f"expected `{signature}`, got `{_source_signature(defs[0])}`"
            )
    return violations


def call_violations(source: str, signatures: dict[str, str]) -> list[str]:
    """Flag calls whose arguments cannot match the frozen signature."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    wanted = {name: parse_params(sig) for name, sig in signatures.items()}
    owners = owner_map(source)
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        params = wanted.get(node.func.id)
        if params is None:
            continue
        if any(isinstance(arg, ast.Starred) for arg in node.args) or any(kw.arg is None for kw in node.keywords):
            continue
        problems = []
        positional = len(node.args)
        keywords = [kw.arg for kw in node.keywords if kw.arg]
        if positional > len(params.pos) and not params.vararg:
            problems.append(f"{positional} positional arguments, signature allows {len(params.pos)}")
        for keyword in keywords:
            if keyword not in params.pos and keyword not in params.kwonly and not params.kwarg:
                problems.append(f"unknown keyword `{keyword}`")
        covered = set(params.pos[:positional]) | set(keywords)
        missing = [name for name in list(params.pos[: params.required]) + list(params.kwonly_required) if name not in covered]
        if missing:
            problems.append(f"missing required argument(s) {missing}")
        if problems:
            owner = owners.get(getattr(node, "lineno", 0), "?")
            violations.append(
                f"{owner}: call `{node.func.id}(...)` does not match `{signatures[node.func.id]}`: "
                + "; ".join(problems)
            )
    return violations


def missing_constants(source: str, constants: set[str]) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    present = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    present.add(target.id)
    return [f"frozen constant `{name}` is missing from the module" for name in sorted(constants) if name not in present]


def static_header_conflicts(source: str, header: str) -> list[HeaderConflict]:
    """Attribute uses that the frozen import cannot provide. Stdlib modules only."""
    bindings = header_bindings(header)
    if not bindings:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    owners = owner_map(source)
    grouped: dict[str, HeaderConflict] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        local = node.value.id
        module = bindings.get(local)
        if module is None:
            continue
        exists = module_has_attr(module, node.attr)
        if exists is not False:
            continue
        conflict = grouped.setdefault(
            local,
            HeaderConflict(local=local, module=module, attrs=[], functions=[], lines=[]),
        )
        if node.attr not in conflict.attrs:
            conflict.attrs.append(node.attr)
        line = getattr(node, "lineno", 0)
        conflict.lines.append(line)
        owner = owners.get(line)
        if owner and owner not in ("_header",) and owner not in conflict.functions:
            conflict.functions.append(owner)
    return list(grouped.values())


def header_conflicts_in_output(output: str, source: str, header: str) -> list[HeaderConflict]:
    """Merge runtime ``module 'x' has no attribute 'y'`` lines with the static scan."""
    by_key = {(item.local, item.module): item for item in static_header_conflicts(source, header)}
    bindings = header_bindings(header)
    owners = owner_map(source)
    for match in _ATTR.finditer(output or ""):
        module, attr = match.group(1), match.group(2)
        local = _local_for(bindings, module)
        if local is None:
            continue
        key = (local, bindings.get(local, module))
        conflict = by_key.get(key)
        if conflict is None:
            conflict = HeaderConflict(local=local, module=bindings.get(local, module), attrs=[], functions=[], lines=[])
            by_key[key] = conflict
        if attr not in conflict.attrs:
            conflict.attrs.append(attr)
    # Attribute the runtime message to functions already known, else to owners of static lines.
    del owners
    return list(by_key.values())


def module_has_attr(module: str, attr: str) -> bool | None:
    """True, False, or None when the module is not a known stdlib module.

    Non-stdlib modules are not imported here: importing them could run arbitrary code.
    """
    root = module.split(".")[0]
    if root not in sys.stdlib_module_names:
        return None
    try:
        imported = importlib.import_module(module)
    except Exception:
        return None
    if hasattr(imported, attr):
        return True
    try:
        importlib.import_module(f"{module}.{attr}")
        return True
    except Exception:
        return False


def module_frames(text: str, filename: str) -> list[int]:
    """Line numbers in ``filename`` mentioned by a traceback, in reading order."""
    base = re.escape(filename)
    hits: list[tuple[int, int]] = []
    for match in re.finditer(rf"(?:^|[/\s\"']){base}:(\d+)", text, re.M):
        hits.append((match.start(), int(match.group(1))))
    for match in re.finditer(rf'File "[^"]*{base}", line (\d+)', text):
        hits.append((match.start(), int(match.group(1))))
    hits.sort()
    return [line for _, line in hits]


def index_tests(files: list[tuple[str, str]]) -> dict[str, dict]:
    """Map a test function name to its source and the calls it makes, in order."""
    index: dict[str, dict] = {}
    for _path, source in files:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            calls: list[str] = []

            class _Visit(ast.NodeVisitor):
                def visit_Call(self, call: ast.Call) -> None:
                    if isinstance(call.func, ast.Name):
                        calls.append(call.func.id)
                    elif isinstance(call.func, ast.Attribute):
                        calls.append(call.func.attr)
                    self.generic_visit(call)

            _Visit().visit(node)
            index[node.name] = {
                "source": ast.get_source_segment(source, node) or "",
                "calls": calls,
            }
    return index


def blame(title: str, text: str, owners: dict[int, str], filename: str, functions: list[str], tests: dict) -> str | None:
    """Pick the function a failure belongs to.

    Prefer the deepest frame inside the module. Otherwise use the first contract
    function called by the failing test.
    """
    for line in reversed(module_frames(text, filename)):
        owner = owners.get(line)
        if owner in functions:
            return owner
    test_name = re.sub(r"\[.*$", "", title.split("::")[-1]).strip()
    info = tests.get(test_name) or {}
    for called in info.get("calls") or []:
        if called in functions:
            return called
    if "collecting" in title.lower() or "import" in title.lower():
        return "_header"
    return None


def detect_coupling(text: str, owners: dict[int, str], filename: str, functions: list[str]) -> tuple[list[str], str] | None:
    """Two or more contract functions in one traceback, with a type-shaped error.

    v0.1 uses the traceback span only. It does not guess a producer from a type name.
    """
    seen: list[str] = []
    for line in module_frames(text, filename):
        owner = owners.get(line)
        if owner in functions and owner not in seen:
            seen.append(owner)
    if len(seen) < 2:
        return None
    if not re.search(r"\b(TypeError|AttributeError|KeyError|ValueError)\b", text):
        return None
    return seen, f"traceback spans {', '.join(seen)}"


def fail_blocks(output: str) -> list[tuple[str, str]]:
    """Split a pytest short traceback into ``(title, body)`` pairs."""
    parts = re.split(r"^_{3,} (.+?) _{3,}$", output, flags=re.M)
    blocks = []
    for index in range(1, len(parts) - 1, 2):
        body = re.split(r"^={5,}", parts[index + 1], maxsplit=1, flags=re.M)[0]
        blocks.append((parts[index].strip(), body.strip()))
    return blocks


def _params(fn: ast.FunctionDef) -> Params:
    args = fn.args
    pos = tuple(arg.arg for arg in list(args.posonlyargs) + list(args.args))
    required = len(pos) - len(args.defaults)
    kwonly = tuple(arg.arg for arg in args.kwonlyargs)
    kwonly_required = tuple(
        arg.arg for arg, default in zip(args.kwonlyargs, args.kw_defaults) if default is None
    )
    return Params(
        name=fn.name,
        pos=pos,
        required=required,
        kwonly=kwonly,
        kwonly_required=kwonly_required,
        vararg=args.vararg is not None,
        kwarg=args.kwarg is not None,
    )


def _source_signature(fn: ast.FunctionDef) -> str:
    returns = f" -> {ast.unparse(fn.returns)}" if fn.returns else ""
    return f"def {fn.name}({ast.unparse(fn.args)}){returns}:"


def _undefined(tree: ast.AST, allowed: set[str]) -> list[str]:
    defined = set(allowed) | set(_BUILTIN)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                defined.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in defined:
            missing.append(f"undefined name `{node.id}` on line {getattr(node, 'lineno', '?')}")
    return missing


def _local_for(bindings: dict[str, str], module: str) -> str | None:
    for local, target in bindings.items():
        if target == module or local == module or target.split(".")[0] == module.split(".")[0] and local == module.split(".")[0]:
            return local
    return None
