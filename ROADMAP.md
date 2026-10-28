# Roadmap

specfreeze stays a small, local checker: closed questions first, a frozen contract, and code that has to match it. Items move when someone needs them. Open an issue before a large change.

## v0.2 (this release)

- `specfreeze verify`: check any module against a frozen contract, offline, as a CI gate.
- `specfreeze catalog check` and `catalog print`.
- CI on Python 3.11 to 3.13 with a demo and verify smoke step.

## Next

- `verify --format markdown` for pull request comments and job summaries.
- `specfreeze show RUN_DIR`: one-page summary of a run (low-confidence answers, digest, repair outcome).
- A published composite GitHub Action for the contract gate.
- Applying a recorded "change the header" decision, with a new digest and a visible diff.
- Multi-module runs: one contract per module, checked together.

## Later

- Other languages, starting with a TypeScript signature checker.
- Coupling detection beyond a shared traceback.
- Record and replay for a live provider.
- Calibrated confidence. Today the number is the model's own report.

## Good first issues

- `verify`: report the line number of each problem.
- `verify`: accept several modules and contracts in one call (`--pair module.py=contract.json`).
- `catalog check`: warn when two questions have the same `ask` text.
- `catalog check --json` for editors and CI.
- A second example catalog, for example for small parsing functions, with a test that it passes `catalog check`.
- README: a short screencast or asciinema of `demo` followed by `verify`.
