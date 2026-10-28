"""Build and freeze an interface contract.

The fingerprint covers module name, constants, signatures, error lines, examples,
and calls. Addenda written during repair are not part of the fingerprint, and
repair is not allowed to change the fingerprinted fields.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from specfreeze.distill import binding_lines
from specfreeze.errors import ContractError
from specfreeze.models import Answer, SpecDocument
from specfreeze.specdoc import normalize_signature

_FROZEN_NOTE = (
    "The signatures, constants, error behaviour, examples, and calls in this contract are frozen. "
    "A later repair may change a function body. It may not change this contract. "
    "If a decision disagrees with the written specification or the tests, the specification and the tests win."
)


def build_contract(spec: SpecDocument, answers: list[Answer]) -> dict:
    """Create a contract dict. It is not frozen until :func:`freeze` writes it."""
    if not spec.functions:
        raise ContractError("the contract needs at least one function")
    functions = []
    for fn in spec.functions:
        if not fn.signature:
            raise ContractError(f"{fn.name} has no signature to freeze")
        signature = normalize_signature(fn.signature, fn.name)
        own = [answer for answer in answers if answer.function == fn.name]
        notes = binding_lines(own)
        error_lines = [answer.contract_note[answer.effective] for answer in own if answer.category == "errors" and answer.effective and answer.effective in answer.contract_note]
        if not error_lines:
            error_lines = [line for line in notes if "raise" in line.lower() or "error" in line.lower()]
        functions.append(
            {
                "name": fn.name,
                "signature": signature,
                "purpose": fn.purpose,
                "inputs": _inputs(signature),
                "returns": _returns(signature),
                "errors": error_lines,
                "calls": list(fn.calls),
                "examples": list(fn.examples),
                "decisions": notes or [_short(answer) for answer in own if answer.effective],
            }
        )
    module_answers = [answer for answer in answers if answer.function is None]
    error_policy = binding_lines([answer for answer in answers if answer.category == "errors"])
    if not error_policy:
        error_policy = [
            "Preconditions in the decision specification are enforced. "
            "Functions raise on those violations instead of returning a sentinel, unless a decision says otherwise."
        ]
    header = render_header(spec.module, spec.summary, spec.imports, spec.constants)
    assert_header_safe(header)
    contract = {
        "schema_version": 1,
        "module": spec.module,
        "summary": spec.summary,
        "imports": list(spec.imports),
        "constants": list(spec.constants),
        "functions": functions,
        "error_policy": error_policy,
        "module_decisions": binding_lines(module_answers),
        "addenda": [],
        "header": header,
        "frozen": False,
        "digest": "",
    }
    contract["digest"] = fingerprint(contract)
    return contract


def fingerprint(contract: dict) -> str:
    """SHA-256 of the frozen fields. Addenda are excluded on purpose."""
    payload = {
        "module": contract["module"],
        "constants": contract.get("constants") or [],
        "functions": [
            {
                "name": fn["name"],
                "signature": fn["signature"],
                "errors": fn.get("errors") or [],
                "examples": fn.get("examples") or [],
                "calls": fn.get("calls") or [],
            }
            for fn in contract["functions"]
        ],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def signatures_of(contract: dict) -> dict[str, str]:
    return {fn["name"]: fn["signature"] for fn in contract["functions"]}


def constant_names(contract: dict) -> set[str]:
    return {item["name"] for item in contract.get("constants") or []}


def render_contract_md(contract: dict) -> str:
    lines = [
        f"# Interface contract: {contract['module']}",
        "",
        f"Frozen: {'yes' if contract.get('frozen') else 'no'}",
        f"Digest: `{contract.get('digest', '')}`",
        "",
        _FROZEN_NOTE,
        "",
        "## Error policy",
        "",
    ]
    for item in contract.get("error_policy") or []:
        lines.append(f"- {item}")
    lines.append("")
    if contract.get("constants"):
        lines.append("## Constants")
        lines.append("")
        for item in contract["constants"]:
            purpose = f" — {item['purpose']}" if item.get("purpose") else ""
            lines.append(f"- `{item['code']}`{purpose}")
        lines.append("")
    if contract.get("module_decisions"):
        lines.append("## Module decisions")
        lines.append("")
        for item in contract["module_decisions"]:
            lines.append(f"- {item}")
        lines.append("")
    for fn in contract["functions"]:
        lines.append(f"## `{fn['name']}`")
        lines.append("")
        lines.append(f"**Signature:** `{fn['signature']}`")
        lines.append("")
        if fn.get("purpose"):
            lines.append(fn["purpose"])
            lines.append("")
        lines.append(f"**Inputs:** {fn.get('inputs') or '(see signature)'}")
        lines.append("")
        lines.append(f"**Returns:** {fn.get('returns') or '(see signature)'}")
        lines.append("")
        lines.append("**Errors:**")
        lines.append("")
        for item in fn.get("errors") or ["(none recorded)"]:
            lines.append(f"- {item}")
        lines.append("")
        if fn.get("calls"):
            lines.append("**Calls:** " + ", ".join(f"`{name}`" for name in fn["calls"]))
            lines.append("")
        lines.append("**Examples:**")
        lines.append("")
        for example in fn.get("examples") or ["(none)"]:
            lines.append(f"- `{example}`")
        lines.append("")
        if fn.get("decisions"):
            lines.append("**Decisions:**")
            lines.append("")
            for item in fn["decisions"]:
                lines.append(f"- {item}")
            lines.append("")
    if contract.get("addenda"):
        lines.append("## Addenda")
        lines.append("")
        lines.append("Addenda record conflict decisions. They do not change the digest.")
        lines.append("")
        for item in contract["addenda"]:
            lines.append(f"- Round {item.get('round')}: {item.get('note')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_header(module: str, summary: str, imports: list[str], constants: list[dict]) -> str:
    first = " ".join((summary or module).split())
    if len(first) > 240:
        first = first[:237] + "..."
    first = first.replace('"""', "'''")
    lines = [
        "#!/usr/bin/env python3",
        f'"""{module}: {first}"""',
        "from __future__ import annotations",
    ]
    extra = [item for item in imports if item.strip() != "from __future__ import annotations"]
    if extra:
        lines.append("")
        lines.extend(extra)
    if constants:
        lines.append("")
        for item in constants:
            lines.append(item["code"])
    lines.append("")
    return "\n".join(lines)


def assert_header_safe(header: str) -> None:
    """The frozen header may only contain a docstring, imports, and literal constants."""
    try:
        tree = ast.parse(header)
    except SyntaxError as exc:
        raise ContractError(f"header does not parse: {exc}") from exc
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Assign) and all(isinstance(target, ast.Name) for target in node.targets):
            if not _literal(node.value):
                raise ContractError("header constants must be literals")
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.isupper():
                    raise ContractError(f"header constant `{target.id}` must be UPPER_CASE")
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            raise ContractError("header must not contain functions or classes")
        raise ContractError(f"header contains {type(node).__name__}, which is not allowed")
    compile(header, "<header>", "exec")


def assert_header_importable(header: str) -> None:
    """Import the header in a scratch file so a bad import fails before freeze."""
    assert_header_safe(header)
    with tempfile.TemporaryDirectory(prefix="specfreeze-header-") as tmp:
        path = Path(tmp) / "_specfreeze_header.py"
        path.write_text(header, encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib.util, sys; "
                "spec = importlib.util.spec_from_file_location('specfreeze_header', sys.argv[1]); "
                "module = importlib.util.module_from_spec(spec); "
                "spec.loader.exec_module(module)",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        raise ContractError("header is not importable: " + (tail[-1] if tail else "unknown error"))


def _inputs(signature: str) -> str:
    fn = ast.parse(signature + "\n    pass\n").body[0]
    assert isinstance(fn, ast.FunctionDef)
    return ast.unparse(fn.args)


def _returns(signature: str) -> str:
    fn = ast.parse(signature + "\n    pass\n").body[0]
    assert isinstance(fn, ast.FunctionDef)
    return ast.unparse(fn.returns) if fn.returns else ""


def _short(answer: Answer) -> str:
    return f"{answer.ask} -> {answer.effective} (confidence {answer.confidence})"


def _literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)) and isinstance(node.operand, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_literal(elt) for elt in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is not None and _literal(key) and _literal(value) for key, value in zip(node.keys, node.values))
    return False


def dump_contract(contract: dict) -> str:
    return json.dumps(contract, indent=2, ensure_ascii=False) + "\n"


def load_contract(text: str) -> dict:
    data = json.loads(text)
    if int(data.get("schema_version") or 0) != 1:
        raise ContractError("contract schema_version must be 1")
    digest = fingerprint(data)
    if data.get("frozen") and data.get("digest") and data["digest"] != digest:
        raise ContractError(
            "frozen contract digest does not match its signatures. "
            "The file was edited after freeze; specfreeze will not build against it."
        )
    return data