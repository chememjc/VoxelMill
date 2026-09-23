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

- VM-060 CI test workflow, then VM-061 lint config, then VM-011 TBB in CI.

## Done this session (2026-09-23), newest first

- `1394be1` VM-002 editor job pool, VM-003 scratch leak
- `aab35cc` VM-001 hex infill crash, VM-010 `_bottom_open` vectorized
- `e45b359` audit → ISSUES.md; trackers retired

## Next up (ISSUES.md "Recommended sequencing")

1. VM-060, VM-061, VM-011, then a release for VM-080 (needs the user: tagging publishes)
2. VM-014 fixtures, VM-019 16K scale check, then VM-012/013/015/018 by profile
3. VM-040 → VM-041 → VM-044/045 → VM-043/042 → G3, then VM-049
4. Hardware-bound items (N12, B5, E7, C5, D4, D6, H4, I3) cannot close here
