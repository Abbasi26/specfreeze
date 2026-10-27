# Security

specforge 0.1 keeps data on the machine that runs it.

- There is no telemetry and no default endpoint.
- API keys are read from the environment. They are redacted from `generation.log` and from files under `prompts/`. Do not commit a `.env` file or a run directory that you populated with a live key before checking the redaction.
- Live commands require `SPECFORGE_BASE_URL`, `SPECFORGE_API_KEY`, `SPECFORGE_DECISION_MODEL`, and `SPECFORGE_CODE_MODEL`. If one is missing, the process exits before it opens a socket.
- `specforge demo` does not take a key and does not connect to a network.
- Generated code is executed only when you run tests (`build` does not execute the module; `fix`, `run`, and `demo` do, via pytest in a temporary directory). Review the module before you point `--test-cmd` at anything other than the default pytest invocation. The command is split without a shell.
- The repair loop will not change a frozen signature to make tests pass. It also will not edit the frozen header in this version, even if a conflict question records that the header should change.

## Reporting a vulnerability

If the repository host supports private security advisories, use that. Otherwise contact the maintainer out of band. Do not include a live API key, and do not post a working exploit in a public issue.