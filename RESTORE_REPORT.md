# Restore report — specfreeze (project name: specfreeze)

## Mapping
- GitHub empty repo target: `specfreeze`
- Project name inside the tree: **specfreeze** (unchanged; no rename in source)

## Method
1. Replayed all `search_replace` edit diffs from the transcript in order (68 edits, 0 parse failures).
2. Recreated `examples/portion/` by copying the packaged demo fixtures (same as the original agent shell step).
3. Wrote a clean `SPEC.md` from the original task (stripped of forbidden internal codenames so the hygiene test stays green).
4. No model regeneration was required: the reconstructed tree already satisfied the original acceptance tests.

## Transcript vs generated
| Area | Source |
|------|--------|
| `src/specfreeze/**`, `tests/**`, packaging, docs, CI | Restored from transcript diffs |
| `examples/portion/**` | Recreated from `src/specfreeze/data/demo/**` (transcript shell copy) |
| `SPEC.md` | Written at restore time from the task prompt (sanitized) |
| Code written by dual-provider pipeline this run | **None** (not needed) |

## Tests
- `pytest`: **41 passed**
- Offline `specfreeze demo`: repair loop rejected one contract-breaking candidate, then **10/10** demo tests passed
- Hygiene: green after sanitize of restore-time `SPEC.md`

## Ready to push
**Yes** (local `main` commit prepared; push left to parent).

## Gaps
- None for v0.1 acceptance. Empty remote `specfreeze` still needs the push.
- Package was developed against Python 3.12 in the cloud agent; verified here on 3.13.
