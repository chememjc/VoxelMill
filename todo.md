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

## In flight — v0.6.1 beta (VM-098…VM-104)

Test project: `deleteme.voxmil` (gitignored, repo root). Subagents edit, the orchestrator commits.

- [ ] VM-100 macOS dock-resize: code done (root cause: deferred paint called QVTK `Render()` = `update()`, an endless repaint loop). Left: verify on the iMac with the DMG that idle renders/s ≈ 0 and drags are smooth (the packaged app can't run `scripts/resize_benchmark.py`; drive window resizes with osascript and watch CPU)
- [ ] Goldens re-recorded after review; AppImage acceptance at 0.6.1
- [ ] Docs: gui, cli, configuration, algorithms, troubleshooting, README
- [ ] Release 0.6.1: version bump, release notes, CI, mac+windows workflow, iMac/Win11 battery, report, ask before tag, re-test published assets

## Deferred past beta

- Open in ISSUES.md: VM-096. `deferred (post-beta)`: VM-042, VM-043, VM-015, VM-014, VM-083, B3, E3, I1, F4, C8, D1, G13, C10, A8, G12 A/B diff, B8, VM-082, VM-085, F7, C11, A9, E6, E2, A10, A4, A5.
- Hardware-bound (`open (hardware)`, no printer available here): N12, B5, E7, C5, D4, D6, H4, I3, VM-084.
