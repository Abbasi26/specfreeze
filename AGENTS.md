# AGENTS.md

Notes for coding agents (and humans) working in `specfreeze`. README.md, SPEC.md and CONTRIBUTING.md describe the project; this file only covers how to work in it.

## Before you open a pull request

```
python -m pip install -e ".[dev]"
pytest -q
ruff check .  (config in ruff.toml)
pre-commit run --all-files
```

All of these must pass. A pull request with failing CI is not ready for review.

## Rules

- Keep each change small and on one topic. Do not open a second pull request for a topic that already has an open one.
- Tests and the demo run offline. Do not add tests that call live services.
- Never commit secrets, tokens, real hostnames, real user names or other personal data. Use example data (`*.example`, `example.test`).
- Write temporary files inside the working tree (for example `./.tmp/`, which is ignored), not to `/tmp`.
- GitHub Actions: pin every action to a full commit SHA with the version as a comment, set `persist-credentials: false` on checkout, and keep `permissions` at the minimum (`contents: read`). `zizmor .` must report no findings.
- Do not change versions, tags, releases or repository settings from a pull request.
