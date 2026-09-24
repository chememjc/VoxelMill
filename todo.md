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

- VM-012 overhang sample reuse, then VM-023, VM-062, VM-046/047/048, VM-070.
- Needs the user: VM-080 (tagging publishes a release), pushing `master` (CI has never run on GitHub).

## Done this session (2026-09-23), newest first

- `4ad96f1` VM-061 ruff+mypy in CI; VM-004 thickness budget bug (found by lint)
- `6932254` VM-060 CI workflow, VM-064 goldens re-recorded at HEAD
- `8ea38c4` VM-011 std::thread fallback (release build confirmed TBB-less)
- `1394be1` VM-002 editor job pool, VM-003 scratch leak
- `aab35cc` VM-001 hex infill crash, VM-010 `_bottom_open` vectorized
- `e45b359` audit → ISSUES.md; trackers retired

## Next up (ISSUES.md "Recommended sequencing")

1. VM-060, VM-061, VM-011, then a release for VM-080 (needs the user: tagging publishes)
2. VM-014 fixtures, VM-019 16K scale check, then VM-012/013/015/018 by profile
3. VM-040 → VM-041 → VM-044/045 → VM-043/042 → G3, then VM-049
4. Hardware-bound items (N12, B5, E7, C5, D4, D6, H4, I3) cannot close here
