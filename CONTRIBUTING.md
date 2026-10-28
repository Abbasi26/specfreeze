# Contributing

specfreeze is MIT licensed, copyright Wasaf Abbasi.

## Setup

Python 3.11 or newer.

```bash
python -m pip install -e ".[dev]"
pytest -q
python -m specfreeze demo --out .specfreeze/demo
```

The test suite and `specfreeze demo` stay offline. Do not add a test that calls a live model endpoint. A fake provider belongs in the test, and a recorded reply belongs in `src/specfreeze/demo.py` when the demo itself needs a new turn.

## What to send

- Code, comments, and docs in English.
- A changelog entry under `## Unreleased` or the version you are cutting, in [CHANGELOG.md](CHANGELOG.md).
- Tests for behaviour you add or change. The demo and the files in `examples/portion/` are the same bytes as `src/specfreeze/data/demo/`. Update both, or the catalog test will fail.
- No API keys, tokens, hostnames from a private environment, or personal paths. Configuration comes from `SPECFREEZE_*` environment variables.
- No telemetry.

## Behaviour that should stay

- Live commands exit before opening a socket when the required settings are missing.
- Writes stay inside `--out` unless the user passed `--apply` or `--write`. Both of those show a diff first.
- A repair that changes a frozen signature, adds a public function, or reassigns a frozen constant is rejected.
- Confidence numbers are stored as the model reported them. Do not describe them as calibrated probabilities.
- v0.1 records a "change the header" decision and does not edit the header. Say so if you touch that path.

## Reporting vulnerabilities

See [SECURITY.md](SECURITY.md). Do not open a public issue that contains a secret or a working exploit.