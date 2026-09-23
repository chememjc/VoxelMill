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

## In flight

- (none) — the 2026-09-23 audit wrote ISSUES.md and retired `plan.md`,
  `plan2.md`, `nextsteps.md`, and `platforms.md`. The previous 439-line ledger was
  folded into ISSUES.md and `docs/performance.md`.

## Next up (from ISSUES.md "Recommended sequencing")

1. VM-001 hex infill crash, VM-002 editor job pool, VM-003 scratch leak
2. VM-010 vectorize `_bottom_open`
3. VM-060 CI workflow, VM-061 lint config, VM-011 TBB in release builds
4. Cut a release for VM-080
