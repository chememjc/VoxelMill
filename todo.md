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

- VM-043 partial: next is route_contacts per-contact geometry emission + metrics block, then `_brace` (425 lines). Verify every routing change with the scratch harness idea: record plan_supports graph/metrics/solid-hash for 4 shapes x 8 support scenarios before, compare after (identical required); plus `scripts/equivalence.py --jobs 4` (18/18) and the full suite.
- Then VM-042 (split gui/window.py into controllers), VM-015 (incremental routing), VM-014 CI perf A/B, VM-083, VM-085, feature backlog.
- v0.5.5 alpha is published and verified (Linux, Windows guest, Intel iMac); CI is green on `master`.
- Commit rule from the user: messages describe only the diff since the last commit, with no tool or session references and no attribution trailers.
- Verification habits: byte-identical output checks before/after every perf or refactor change; mark ISSUES.md status in both table and detail (`Status:` line), re-sort table by ease×benefit.

## Done this session (2026-09-23), newest first

- `3600ab8` VM-043 partial (route_contacts split, small_pillar.mode metric bug); `482993b` prepare split into stages
- `22d69c9`, `f8a6274` VM-040 partial: settings_schema.FIELDS (fixed 3 bad GUI enums + 57 out-of-range controls), VM-071; `b97a527` VM-049 partial (stability.md, exit 64, CLI snapshot)
- `a863263` VM-041 partial (versioning.py); `18753cd` VM-045 base registry; `03af9ef` VM-044 format registry
- `4fabfd3`…`26e1a83` VM-022, VM-020/028/018/017/027/F5 closed by measurement, VM-065 (Windows offsets bug), VM-016, VM-072, VM-063, VM-024/025 decided
- `a08ded7`…`38ea010` VM-019 16K check (slab prepare 96→36 s, slice 46→34 s), VM-013 capsule index, drainage lockstep, growth tiles, VM-015 partial (one exact union per search), VM-029 explained
- `bb28f88` VM-030 slicing from the crop (24.6 → 1.3 s), VM-014 partial; `ff15229` VM-029 opened (bisected bracing drift); `197a2dd` VM-026 closed
- `d3514d5` VM-070; `f507eca` VM-062
- `3a44595`…`8951c9a` VM-021, VM-023, VM-048, VM-047, VM-046 (won't fix, typed), VM-062; VM-012 retired by measurement
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
