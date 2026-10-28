# specfreeze

[![CI](https://github.com/Abbasi26/specfreeze/actions/workflows/ci.yml/badge.svg)](https://github.com/Abbasi26/specfreeze/actions/workflows/ci.yml)

Decide the design in closed questions, freeze the interface, then let a code model implement it.

Large language models write plausible code for vague requirements, and the plausible choice is often one you did not make. specfreeze is a local Python command-line tool that asks those decisions up front, freezes them as a contract, and refuses implementations and repairs that drift from it.

## The problem

Ask a model to "split the bill fairly" and it has to invent the policy. For 100 cents and 3 people it might return `[33, 33, 33]` and drop a cent, put the leftover on the last share, or add a `mode` parameter nobody requested.

specfreeze asks first, with closed questions:

- Where do leftover cents go, the front of the list or the back?
- Does a negative total raise `ValueError`?
- Does `people < 1` raise `ValueError`?

The answers freeze `split_cents(total: int, people: int) -> list[int]`, the error behaviour, and the examples. A later repair that adds `*, mode: str = "front"` is rejected because the signature moved. A repair that uses integer division and puts the extra cents on the first shares is kept, because the tests improve and the signature stays.

The offline demo runs that story with a recorded provider. The first implementation drops the remainder. The first repair adds a parameter and is discarded. The second repair matches the contract, and 10 tests pass.

## Quickstart

Python 3.11 or newer.

```bash
python -m pip install -e ".[dev]"
python -m specfreeze demo --out .specfreeze/demo
```

`specfreeze demo` needs no API key and opens no network connection. It writes only under the output directory (default `.specfreeze/demo`) and refuses a non-empty directory that is not already a specfreeze run. The finished module is `.specfreeze/demo/portion.py`. A preview of the repair, including the rejected signature change, is in `generation.log`, `fix.json`, and `prompts/`.

### Gate any edit on the frozen contract

Once a contract is frozen, any later change to the module can be checked against it, whoever made the change: you, a teammate, Aider, Copilot, or a second specfreeze run.

```bash
specfreeze verify .specfreeze/demo/portion.py --contract .specfreeze/demo
# .specfreeze/demo/portion.py: contract `portion` (digest 3553ade96946)
# OK: 2 function(s) and 1 constant(s) match the frozen contract.
```

Add a `sep` parameter to `format_shares`, change `SEPARATOR`, and add a public `helper()`, and the same command exits 1:

```text
3 problem(s):
  [signature] signature deviates from the frozen contract: expected `def format_shares(shares: list[int]) -> str:`, got `def format_shares(shares: list[int], sep: str=',') -> str:`
  [constant] frozen constant `SEPARATOR` has a different value than the contract
  [extra_public] public function `helper` is not in the contract; make it private (`_helper`) or add it to the spec
```

`verify` parses the file with `ast`. It never imports or runs it, needs no API key, and opens no network connection. It checks signatures, missing, changed, or reassigned frozen constants, calls that cannot match a frozen signature, async drift, and public names that are not in the contract. A contract whose digest no longer matches is refused (exit 2). `--json` prints a machine-readable result. Exit codes: 0 clean, 1 drift, 2 usage or contract error.

To run it in CI, commit `contract.json` next to the module and copy [examples/github-actions/contract-gate.yml](examples/github-actions/contract-gate.yml).

## Real usage

Point specfreeze at any OpenAI-compatible chat-completions endpoint. A gateway such as NVIDIA NIM is one example. There is no default URL, key, or model.

```bash
export SPECFREEZE_BASE_URL="https://your-endpoint.example/v1"
export SPECFREEZE_API_KEY="your-key"
export SPECFREEZE_DECISION_MODEL="your-decision-model"
export SPECFREEZE_CODE_MODEL="your-code-model"
# optional; defaults to SPECFREEZE_CODE_MODEL
export SPECFREEZE_SPEC_MODEL="your-spec-model"

python -m specfreeze run \
  --spec examples/portion/SPEC.md \
  --catalog examples/portion/catalog.yaml \
  --tests examples/portion/tests \
  --out .specfreeze/portion
```

`run` asks, freezes the contract, generates, and repairs. Without `--apply` an accepted repair is written to `preview/` and `preview.diff`, and the module in the run directory stays as first generated. Pass `--apply` to replace it. `--write PATH` prints a unified diff and then copies the module to that path.

The same stages are separate commands: `ask`, `spec`, `build`, `fix`. `ask` and `run` take `--rounds` (default 2), `--threshold` (default 0.6), `--max-questions` (default 100, hard cap 100), and `--model-followups` / `--no-model-followups`. `fix` takes `--rounds` (default 4) and `--test-cmd`. A missing live setting exits with status 2 before any socket is opened. `SPECFREEZE_PROVIDER=fake` is rejected on live commands; the offline path is `specfreeze demo`.

| Variable | Role |
| --- | --- |
| `SPECFREEZE_BASE_URL` | Chat-completions base URL, required for live commands |
| `SPECFREEZE_API_KEY` | Bearer token, required for live commands |
| `SPECFREEZE_DECISION_MODEL` | Model that answers the catalog |
| `SPECFREEZE_CODE_MODEL` | Model that implements and repairs |
| `SPECFREEZE_SPEC_MODEL` | Model that decomposes a vague spec and drafts missing signatures. Falls back to the code model |
| `SPECFREEZE_DECISION_BASE_URL`, `SPECFREEZE_SPEC_BASE_URL`, `SPECFREEZE_CODE_BASE_URL` | Optional per-role URL |
| `SPECFREEZE_DECISION_API_KEY`, `SPECFREEZE_SPEC_API_KEY`, `SPECFREEZE_CODE_API_KEY` | Optional per-role key |
| `SPECFREEZE_FALLBACK_MODELS` | Comma-separated models tried after a 404 or 410 |
| `SPECFREEZE_TIMEOUT` | HTTP timeout in seconds (default 120) |
| `SPECFREEZE_DEADLINE` | Per-request deadline in seconds (default 300) |
| `SPECFREEZE_STREAM` | Set to `1` to read a server-sent stream |
| `SPECFREEZE_TEST_TIMEOUT` | Pytest timeout in seconds (default 60) |
| `SPECFREEZE_EXTRA_BODY` | JSON object merged into the chat request |

Each role sends `POST {base}/chat/completions`. Retries cover 408, 409, 429, 500, 502, 503, and 504. A 401 or 403 stops the run. Keys are redacted from the generation log and from saved prompts. Values of environment variables whose names end in `_API_KEY`, `_TOKEN`, `_SECRET`, or `_PASSWORD` (length 8 or more) are redacted too.

## What a run writes

Everything for one run lives in `--out`:

| File | Contents |
| --- | --- |
| `run.json` | Marker that this directory is a specfreeze run |
| `SPEC.md` | Copy of the input specification |
| `catalog.yaml` | Catalog used for the run |
| `decomposition.json` | Module, constants, and functions |
| `answers.json`, `answers.md` | Closed answers, confidence, and the effective answer |
| `spec.md` | Decision specification distilled from the answers |
| `contract.json`, `contract.md`, `header.py`, `FROZEN` | Frozen contract and its digest |
| `parts/*.py` | One generated function per file |
| `generated/initial.py`, `module.py`, `{module}.py` | Assembled module |
| `generation.log` | JSONL log of model calls |
| `prompts/` | Redacted prompt and response for each call |
| `fix.json`, `preview.diff`, `preview/` | Repair report and the unapplied repair |
| `tests/` | Tests copied in with `--tests` |
| `report.json` | Short summary of the run |

The frozen digest covers the module name, constants, and each function's signature, errors, examples, and calls. Conflict notes appended during repair do not change it. Loading a contract whose digest does not match the frozen fields is an error.

## Question catalogs

Catalogs are YAML, version 1. The shipped general catalog is `src/specfreeze/data/catalogs/general.yaml` (used when `--catalog` is omitted; a copy is written into the run). The portion demo catalog is `examples/portion/catalog.yaml`.

Question types are `yes_no`, `choice` (2 to 6 options), and `score` (2 to 5 levels). Placeholders are `{function}`, `{module}`, and `{signature}`. A follow-up fires when `when_answer` matches and, if you set both, when confidence is below `when_confidence_below`. The ask stage stops at 100 questions, including follow-ups. If any answer is uncertain, one extra round can ask the spec model for up to five new questions.

Check a catalog before a paid run, or start your own from the shipped one:

```bash
specfreeze catalog print > my-catalog.yaml
specfreeze catalog check my-catalog.yaml examples/portion/catalog.yaml
# ok   my-catalog.yaml: general-python-function: 41 questions (choice 11, score 3, yes_no 27), 3 follow-ups, 41 with a conservative default
```

`catalog check` exits 1 if any file fails and notes questions without a `conservative` value.

Confidence is whatever the model reports. It is not a calibrated probability. At or above 0.8 is high, at or above the threshold is medium, and below the threshold is low. A low-confidence answer uses the catalog's `conservative` value when one is set. The words `yes` and `no` in a catalog are answer tokens. specfreeze does not treat them as YAML booleans.

## What the repair loop will and will not change

`fix` runs pytest in a temporary directory with the candidate module on `PYTHONPATH`. The test command is split with `shlex` and run with `shell=False`. A candidate is kept only when the number of failures goes down and the number of passes does not.

Signature changes, new public functions, and reassignment of frozen constants are rejected before the tests run. When one traceback names two or more contract functions and the error is `TypeError`, `AttributeError`, `KeyError`, or `ValueError`, specfreeze asks whether to repair them together. That detection is the traceback span only.

If a failure says the module has no such attribute, specfreeze asks whether the function or the frozen header should change. Choosing the header records the decision and leaves the header as it was. v0.1 does not edit a frozen header. The repair has to match it.

The header itself may contain a module docstring, imports, and literal assignments of `UPPER_CASE` names. It is checked in a short subprocess before code generation continues.

## Permissions

specfreeze is read-only toward your project until you opt in.

- It reads the specification, the catalog, and the tests you pass.
- It writes only inside `--out`, plus the single path given to `--write`.
- `fix` and `run` show a diff and write `preview/` until you pass `--apply`.
- `demo` writes only its output directory.
- A live command talks only to the endpoint you configured. `demo` does not.
- Generated code runs only when tests run, in a temporary directory.
- There is no telemetry and no phone-home.

## Tests

```bash
python -m pip install -e ".[dev]"
pytest -q
```

The suite uses a fake provider. It does not use the network. GitHub Actions runs the same suite on Python 3.11, 3.12, and 3.13, then runs the offline demo and `specfreeze verify` against its output.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The license is MIT, copyright Wasaf Abbasi.

## Roadmap

See [ROADMAP.md](ROADMAP.md), including issues that are a good first contribution.

## How this differs from other tools

These tools solve neighbouring problems. specfreeze is the one that forces a closed-question catalog and then checks generated Python against a frozen signature.

- [Aider](https://aider.chat/) ([source](https://github.com/Aider-AI/aider), Apache-2.0) is a terminal pair programmer with git and a lint/test loop across many languages. It is the better fit when the repository already exists and you want edits in place. It does not make you answer a catalog or freeze signatures before the first edit.
- [GPT-Engineer](https://github.com/gpt-engineer-org/gpt-engineer) turns a prompt into a project and asks a few open clarifying questions. It is the better fit for a first sketch you expect to edit by hand.
- [GitHub Spec Kit](https://github.com/github/spec-kit) ([docs](https://github.github.com/spec-kit/), MIT) walks constitution, specify, plan, tasks, and implement as markdown that many coding agents can follow. Martin Fowler's [survey of spec-driven tools](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html) describes that family. Spec Kit is a process and a document stack. specfreeze is a checker: the signature is frozen, and a repair that moves it is discarded.
- [MetaGPT](https://github.com/FoundationAgents/MetaGPT) assigns a software task to several role agents and produces a heavier stack of documents. specfreeze uses two or three model roles against one contract, and the contract is enforced in process.
- [Kiro](https://kiro.dev/) is an IDE that takes requirements through design to tasks. specfreeze is a local Python CLI. The contract is enforced by the checker in this repository, not by an editor.

specfreeze is overhead for a one-line change. It earns its keep when the failure mode is "the model picked a reasonable design you did not ask for."

## Kurzfassung (Deutsch)

specfreeze ist ein lokales Kommandozeilenwerkzeug für Python 3.11+. Bevor ein Sprachmodell Code schreibt, beantwortet ein Entscheidungsmodell einen Katalog geschlossener Fragen (ja/nein, Auswahl, Skala) und bei Bedarf Nachfragefragen. Daraus wird eine Spezifikation mit einem eingefrorenen Vertrag: Signaturen, Typen, Fehlerverhalten und Beispiele. Ein austauschbares Code-Modell an einem OpenAI-kompatiblen Endpunkt implementiert dagegen. Ein testgetriebener Reparaturzyklus übernimmt nur Änderungen, die weniger Tests scheitern lassen und den Vertrag nicht verletzen. `specfreeze demo` läuft offline, ohne Schlüssel und ohne Netzwerk, und erzeugt ein kleines Beispielmodul. `specfreeze verify` prüft jede spätere Änderung eines Moduls statisch gegen den eingefrorenen Vertrag, auch Änderungen von Hand oder durch andere Werkzeuge, und eignet sich als CI-Gate. `specfreeze catalog check` prüft Fragenkataloge vor einem Lauf. Version 0.1 bearbeitet ein einzelnes Python-Modul. Der Vertragskopf wird nicht automatisch geändert. Angegebenes Vertrauen ist eine Selbstauskunft des Modells, keine kalibrierte Wahrscheinlichkeit. Es gibt keine Telemetrie. Schreiben ausserhalb des Laufverzeichnisses braucht `--apply` oder `--write`, und davor wird ein Diff angezeigt.

## Status

Version 0.2.0, early but usable. Python package `specfreeze` (CLI `specfreeze`). MIT licensed. Every push runs the test suite in GitHub Actions (`.github/workflows/ci.yml`).

## Install

```sh
pip install .
```

## License

MIT. Copyright (c) 2026 Wasaf Abbasi. See [LICENSE](LICENSE).
