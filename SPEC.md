# SPEC — specfreeze 0.1

Decision-driven LLM code generation framework.

## Goal
LLM code is unreliable when requirements are vague. Specfreeze forces decisions before coding:

1. For each function or module, a decision model answers a catalog of up to ~100 closed design questions, then refined follow-ups.
2. Answers are distilled into a specification with a frozen interface contract (signatures, types, error behaviour, examples).
3. Interchangeable code models (any OpenAI-compatible endpoint) implement against the contract.
4. A test-driven fix loop with conflict detection repairs failures without breaking the contract.

## Shared project rules
- Code, comments and README in English. Short German summary section at the end of the README.
- MIT license, copyright "Wasaf Abbasi". Include CONTRIBUTING.md, CHANGELOG.md and SECURITY.md.
- Write from scratch. No employer or customer code/data, no real tenant/host data.
- Before commit: no API keys, tokens, hostnames or personal paths.

## Scope v0.1
- CLI `specfreeze` with subcommands including ask / contract / implement / repair / demo (as delivered).
- Offline demo that needs no network and no API key.
- Pytest suite; package data for catalogs and demo fixtures.
- Neutral public names (no leftover internal tool codenames).

## Positioning
Related tools: Aider, GPT-Engineer, Spec Kit, MetaGPT, Kiro specs. Specfreeze's differentiator is the closed-question catalog plus a frozen contract that the repair loop must not break.

## Acceptance
- `pytest` green.
- `specfreeze demo` runs offline and ends with all demo tests passing after repair.
