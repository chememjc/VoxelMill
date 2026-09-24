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

- Pushed through `61c3f4b`; Linux CI green (goldens 16/18 on the runner, the known cross-CPU drift). Release run 35989690340 (mac+windows) green; VM-081 done. Next: VM-092's remainder, then Phase 3.
- See `handoff.md` (untracked) for session state and the CHITUBOX automation recipe.

## Beta work list

Phase 1 — done: VM-095 (defaults pass), VM-090 (collision audit), VM-094 (multi-part and edge-case tests).

Phase 2 — supports like CHITUBOX Light:
- VM-091 — done (`4a716cc`).
- VM-092 — partial — done when: model-standing pillars can be braced (decide), crowded rows get a fallback, and a test holds max slenderness under a target (about 15) on the fixtures — next: `supports._brace` grounding walk (model_anchor exclusion) and the `supports.unbraced` metric.

Phase 3 — stability and release tooling:
- VM-093, VM-081 — done.
- VM-083 — memory ceiling works on macOS/Windows — done when: a Windows Job-object commit limit is wired and the macOS budget is marked advisory in the report — next: implement in `resources.py`.
- VM-014 — CI perf gate beyond local fixtures — done when: an opt-in job does a base-vs-head A/B on the same runner and fails on a >15% regression — next: add the workflow job.
- B3 — printer database beyond the Mars 5 Ultra — done when: new printers are added with verified specs — next: extract specs from the installed CHITUBOX machine configs and the vendor spec.
- E3 — area-driven exposure mechanism — done when: the mechanism exists with the policy off by default — next: implement and wire the setting.

Phase 4 — speed:
- VM-015 — island-guard passes route only new contacts — done when: routing reuses the existing `ColumnField`/occupied capsules across passes with byte-identical graphs — next: implement incremental routing in `island_guard.py`.
- VM-043 — finish breaking up the god functions — done when: `route_contacts` geometry emission + metrics block are split, and `_brace` (425 lines) is split — next: continue the `supports.py` split.
- I1 — persist and replay analysis artifacts — done when: threshold-only edits skip the re-slice — next: implement artifact persistence.
- F4 — incremental re-slice after a local edit — done when: only the edited Z band re-slices — next: implement, pairing with VM-015/I1.

Phase 5 — GUI and features (VM-042 first to avoid conflicts):
- VM-042 — split `gui/window.py` into controllers — done when: setup_page/menus/pose_controller/pipeline_controller/layers_controller/export_controller are extracted and GUI tests stay green — next: extract `setup_page.py` first.
- G3 — typed settings pages replace the raw JSON box — done when: remaining pages are generated from `settings_schema.FIELDS` — next: generate the next page.
- G2 — fuzzy, mode-aware settings search — done when: search is no longer a plain substring filter — next: implement ranking.
- A7 — dirty-state save/discard in the profile manager — done when: dirty tracking and discard prompts are confirmed complete — next: finish `ProfileLibraryDialog` dirty tracking.
- A8 — presets embedded in profiles — done when: support/process presets can be embedded, not only standalone files — next: wire embedding into `profiles.py`.
- G12 — layer viewer pixel inspection + A/B diff — done when: the remaining scope beyond the issue strip/overlays ships — next: implement pixel inspection.
- G8 — keyboard shortcut editor — done when: the editor UI ships (theme already shipped) — next: build the editor.
- G13 — gizmos for supports, holes, cut planes — done when: direct-manipulation gizmos ship for all three (transform gizmo already shipped) — next: implement the support gizmo.
- C10 — real per-candidate support volume — done when: orientation weights are calibrated against measured volume, not guesses — next: implement the volume calculation.
- D1 — joint support type — done when: joint joins branch/tree/contour/face/boundary as implemented — next: implement in `supports.py`.
- C8 — cap non-planar open cuts — done when: `voxelmill cap` handles non-planar loops (constrained triangulation or best-fit patch) and refuses self-intersecting ones — next: implement in `ops.cap_open_cuts`.

Phase 6 — v0.5.6 alpha release:
1. Local: `pytest -m "not samples"`, `scripts/equivalence.py --jobs 4`, ruff, mypy, the AppImage build, `scripts/appimage_acceptance.py --support-options`.
2. Push; wait for Linux CI green.
3. `gh workflow run release --field platforms=mac+windows`, debug until green.
4. Verify the DMG on the Intel iMac and the zip in the Win11 guest with the `reports/releases/v0.5.5.md` battery plus a two-part branching-plate collision report of zero.
5. Bump `pyproject.toml`/`__init__.py` to 0.5.6, update `packaging/release-notes.md`, write `reports/releases/v0.5.6.md`.
6. Ask the user before pushing the tag — tagging publishes.

VM-041 and VM-049 stay `partial`; their remaining scope is reserved for the 1.0 tag, not this beta.

## Deferred past beta

- `deferred (post-beta)` in ISSUES.md: B8, VM-082, VM-085, F7, C11, A9, E6, E2, A10, A4, A5.
- Hardware-bound (`open (hardware)`, no printer available here): N12, B5, E7, C5, D4, D6, H4, I3, VM-084.
