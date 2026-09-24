# VoxelMill — local recovery ledger

This file only records **the task in flight**, so work can
resume after a context reset or an interrupted session. Everything else lives
elsewhere:

- Open work, priorities and status → [ISSUES.md](ISSUES.md) (single source of truth)
- Verified lessons → [gotchas.md](gotchas.md)
- Benchmarks and refuted ideas → [docs/performance.md](docs/performance.md)

Rules: name the ISSUES.md IDs being worked, the exact next command or step, and
any uncommitted state. When an item finishes, update ISSUES.md and delete it
here. Keep this file under a screen.

- Commit rule: messages describe only the diff since the last commit, with no tool or session references and no attribution trailers.
- Verification habits: byte-identical output checks before/after every perf or refactor change; mark ISSUES.md status in both table and detail (`Status:` line), re-sort table by ease×benefit.

## In flight

Nothing. v0.6.0 is tagged and published, and the published assets were re-tested (`reports/releases/v0.6.0.md`). Next work comes from ISSUES.md: VM-097, VM-096, then the `deferred (post-beta)` items.

Done for the beta: VM-095, VM-090, VM-094, VM-091, VM-092, VM-093, VM-081, A7, G2, G3 (beta scope), G8, G12 pixel inspection.

Deferred past the beta: VM-042, VM-043, VM-015, VM-014, VM-083, B3, E3, I1, F4, C8, D1, G13, C10, A8, G12 A/B diff, B8, VM-082, VM-085, F7, C11, A9, E6, E2, A10, A4, A5.

Release — v0.6.0 beta:
1. Local: `pytest -m "not samples"`, `scripts/equivalence.py --jobs 4`, ruff, mypy, the AppImage build, `scripts/appimage_acceptance.py --support-options`.
2. Push; wait for Linux CI green.
3. `gh workflow run release -f platforms=mac+windows`, debug until green.
4. Verify the DMG on the Intel iMac and the zip in the Win11 guest, CLI and GUI, with the `reports/releases/v0.5.5.md` battery plus a two-part branching plate (zero collisions) and a `--brace-model-pillars` prepare.
5. Bump `pyproject.toml`/`__init__.py` to 0.6.0, update `packaging/release-notes.md`, write `reports/releases/v0.6.0.md`.
6. Ask the user before pushing the tag — tagging publishes.

VM-041 and VM-049 stay `partial`; their remaining scope is reserved for the 1.0 tag, not this beta.

## Deferred past beta

- Everything listed above as deferred is `deferred (post-beta)` in ISSUES.md.
- Hardware-bound (`open (hardware)`, no printer available here): N12, B5, E7, C5, D4, D6, H4, I3, VM-084.
