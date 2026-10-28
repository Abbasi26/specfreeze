"""Check any Python module against a frozen contract, without a model and without running it.

This is the same static gate the repair loop uses, exposed for code that was
edited by hand or by another tool. It parses the module; it never imports or
executes it.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path

from specfreeze.checks import call_violations, header_bindings, missing_constants, module_signature_violations
from specfreeze.contract import constant_names, load_contract, signatures_of
from specfreeze.errors import ContractError


@dataclass
class Problem:
    kind: str
    message: str


@dataclass
class VerifyResult:
    module: str
    digest: str
    frozen: bool
    functions: int
    constants: int
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict:
        return {
            "tool": "specfreeze",
            "check": "verify",
            "schema": 1,
            "module": self.module,
            "digest": self.digest,
            "frozen": self.frozen,
            "functions": self.functions,
            "constants": self.constants,
            "ok": self.ok,
            "problems": [{"kind": p.kind, "message": p.message} for p in self.problems],
        }


def load_frozen(path: Path) -> dict:
    """Load ``contract.json`` from a run directory or a file path. The digest is checked."""
    path = Path(path)
    target = path / "contract.json" if path.is_dir() else path
    if not target.is_file():
        raise ContractError(f"no contract at {target}")
    try:
        return load_contract(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"{target} is not valid JSON: {exc}") from exc
    except KeyError as exc:
        raise ContractError(f"{target} is missing the field {exc}") from exc


def verify_source(source: str, contract: dict) -> VerifyResult:
    signatures = signatures_of(contract)
    constants = constant_names(contract)
    result = VerifyResult(
        module=str(contract.get("module") or ""),
        digest=str(contract.get("digest") or ""),
        frozen=bool(contract.get("frozen")),
        functions=len(signatures),
        constants=len(constants),
    )
    if not result.frozen:
        result.problems.append(Problem("not_frozen", "the contract is not frozen; run `specfreeze spec` first"))

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        result.problems.append(Problem("syntax", f"module does not parse: {exc}"))
        return result

    for message in module_signature_violations(source, signatures):
        result.problems.append(Problem("signature", message))
    for message in missing_constants(source, constants):
        result.problems.append(Problem("constant", message))
    result.problems.extend(_constant_changes(tree, contract))
    for message in call_violations(source, signatures):
        result.problems.append(Problem("call", message))

    allowed = set(signatures) | set(header_bindings(contract.get("header") or ""))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith("_") or node.name in allowed:
                continue
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            result.problems.append(
                Problem("extra_public", f"public {kind} `{node.name}` is not in the contract; make it private (`_{node.name}`) or add it to the spec")
            )
        if isinstance(node, ast.AsyncFunctionDef) and node.name in signatures:
            result.problems.append(Problem("signature", f"`{node.name}` is async; the contract freezes a plain function"))
    return result


def verify_file(module_path: Path, contract_path: Path) -> VerifyResult:
    contract = load_frozen(contract_path)
    module_path = Path(module_path)
    if not module_path.is_file():
        raise ContractError(f"module not found: {module_path}")
    return verify_source(module_path.read_text(encoding="utf-8"), contract)


def render_text(result: VerifyResult, module_path: str) -> str:
    head = f"{module_path}: contract `{result.module}`"
    if result.digest:
        head += f" (digest {result.digest[:12]})"
    lines = [head]
    if result.ok:
        lines.append(
            f"OK: {result.functions} function(s) and {result.constants} constant(s) match the frozen contract."
        )
    else:
        lines.append(f"{len(result.problems)} problem(s):")
        for problem in result.problems:
            lines.append(f"  [{problem.kind}] {problem.message}")
    return "\n".join(lines)


def render_json(result: VerifyResult) -> str:
    return json.dumps(result.as_dict(), indent=2, ensure_ascii=False) + "\n"


def _constant_changes(tree: ast.Module, contract: dict) -> list[Problem]:
    frozen: dict[str, str] = {}
    for item in contract.get("constants") or []:
        code = str(item.get("code") or "")
        try:
            parsed = ast.parse(code)
        except SyntaxError:
            continue
        for node in parsed.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                frozen[item["name"]] = ast.dump(node.value)
    problems = []
    seen: dict[str, int] = {}
    for node in tree.body:
        targets = []
        value = None
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value = node.value
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        for name in targets:
            if name not in frozen:
                continue
            seen[name] = seen.get(name, 0) + 1
            if value is None:
                problems.append(Problem("constant", f"frozen constant `{name}` is modified in place"))
            elif ast.dump(value) != frozen[name]:
                problems.append(Problem("constant", f"frozen constant `{name}` has a different value than the contract"))
    for name, count in seen.items():
        if count > 1:
            problems.append(Problem("constant", f"frozen constant `{name}` is assigned {count} times"))
    return problems
