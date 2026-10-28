# Changelog

## 0.2.0 - 2026-10-28

- `specfreeze verify MODULE --contract RUN_DIR|contract.json`: static, offline check of any module against a frozen contract (signatures, frozen constants, calls, async drift, extra public names). `--json`; exit 0 clean, 1 drift, 2 error. Tampered contracts are refused.
- `specfreeze catalog check FILE...` and `specfreeze catalog print`: validate catalogs and print the shipped general catalog as a starting point.
- Example GitHub Actions contract gate in `examples/github-actions/contract-gate.yml`.
- CI runs Python 3.11 to 3.13 and a demo plus verify smoke step.
- ROADMAP.md, issue forms, and a pull request template.
- Renamed the project from `specforge` to `specfreeze`: package, CLI, environment variables, files and docs. Entries below use the old name.

## 0.1.0 - 2026-10-21

- Command-line tool `specforge` with `ask`, `spec`, `build`, `fix`, `run`, and offline `demo`.
- Editable YAML question catalogs, including follow-ups and a cap of 100 questions.
- Distilled specification and a frozen interface contract (signatures, constants, errors, examples, calls) with a digest.
- OpenAI-compatible chat client configured only through `SPECFORGE_*` environment variables.
- Test-driven repair loop that keeps a candidate only when failures drop and passes do not, and that rejects contract drift.
- Conflict questions for a shared traceback and for a missing attribute on a frozen import. A "change the header" answer is recorded and not applied.
- Artifacts per run: answers, spec, contract, prompts, and a generation log.
- Offline demo that builds the fictional `portion` module with a recorded provider.
- pytest suite with a fake provider, and a GitHub Actions workflow for Python 3.11 and 3.12. No network in CI.