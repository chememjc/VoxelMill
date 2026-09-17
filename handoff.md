# VoxelMill v0.2.0 — handoff

State as of 2026-09-17 after the RLE validation path. Read `todo.md` for the
task ledger and `gotchas.md` for verified lessons. The approved plan lives
outside the repo at `~/.claude/plans/twinkly-orbiting-grove.md`.

**Headline:** `prepare fixtures/shapes/overhang_bracket.stl --max-passes 1
--allow-unresolved` is **3.45 s / 545 MB** (was 73.7 s / 636 MB in v0.1.0,
12.9 s / 824 MB before RLE). `peak_present_trapped_volume_mm3` is still
`2.0886070650760757e-15`. Equivalence vs `/home3/noisecancelingcodex` holds
on the 13 golden shapes and a live sample (cube, pin_array, overhang_bracket,
torus, open_box) including STL and GOO.

**Kill-switch:** `VOXELMILL_NATIVE_RUNS=0` forces the dense per-layer path.

Next: re-profile (the 10 s floor is gone), then Phase 1 `--timing` or Phase 2
items 6–9 only if the new profile says they matter. Phases 3–5 need a rescope.

## Where things stand

Commits on `master`:

1. `b7d68ee` — pristine import of v0.1.0's committed tree, so every later diff
   shows exactly what changed relative to the working version.
2. `a0f9fdc` — version 0.2.0, CPU affinity fix, `topology.py`, `--workers auto`,
   `--worker-policy`.
3. `35bb4b4` — parallel `analyze_layers`, the border-sort removal, the derived
   worker default, and `scripts/equivalence.py`.
4. `72b5d79` — equivalence confirmed on all 13 shape fixtures; ledger updates.
5. `93c27a7` — the harness compares `prepared.stl` and keeps slicing when
   `prepare` reports a validation failure.
6. `083265b` — gotchas from the equivalence work.
7. `3be0dd9` — v0.1.0 golden reports for all 13 fixtures.
8. `7fd41a1` — `check_growth`, skipping the growth transform on the island-guard
   path that discards it.
9. `640d11f` — the four named brace sizing flags.

Working tree is clean. Suite: **1031 passed, 13 skipped** (1029 plus the two
new tests).

**`prepare fixtures/shapes/overhang_bracket.stl --max-passes 1 --allow-unresolved`
went from 73.7 s to 12.9 s — 5.71x — with no C written yet.** The equivalence
gate that guards it is now trustworthy too, which it was not this morning.

| Stage | Wall | Peak RSS |
| --- | --- | --- |
| v0.1.0 | 73.7 s | 636 MB |
| + affinity fix | 61.3 s | 656 MB |
| + parallel analysis, 2 workers | 52.1 s | 609 MB |
| + derived default (8 workers) | **26.9 s** | 784 MB |
| + `check_growth` on the island guard | within noise of 26.9 s | 767 MB |
| + scatter dedup in `VoidForest.merge` | **18.3 s** | 780 MB |
| + island counts per diagnostic | **12.9 s** | 824 MB |

## The finding that should shape the rest of the work

**The original premise — that rewriting the CLI in C would deliver the
speedup — did not survive measurement.** Three facts, all reproducible:

- Python startup is 0.10 s (`import voxelmill.cli`), 0.20 s with numpy, scipy
  and manifold3d. Heavy imports are already lazy. A standalone binary saves
  about 0.3 s per run against runs measured in tens of seconds.
- The hot kernels are already C++: `native/raster.cpp`, `mesh.cpp`, `voxel.cpp`,
  `goo.cpp`, `ctb.cpp`, `distance.cpp`, `intersections.cpp`, with exact
  double-double predicates and a BVH in `geom.hpp`.
- cProfile put **97% of `prepare` in `validation.py`** and 1.5% in the
  rasterizer. The cost was dense-NumPy per-layer analysis, not scan conversion
  and not the interpreter.

Everything gained so far came from algorithm structure. A C rewrite that
faithfully reproduced the old dense-array algorithm would have been only
modestly faster. The native core still earns its place — it is what makes an
RLE layer representation and real cross-layer TBB parallelism tractable without
fighting the GIL — but it is the vehicle, not the source, of the speedup.
**Sequence it after the remaining structural wins, and re-profile between each
one.** The profile in `todo.md` is already stale after 2.74x.

## What changed, and why

### Affinity (commit 2)
`resources.execution_limits` picked cpus with
`sorted(sched_getaffinity(0))[:workers]`. Cpu numbering places SMT siblings
adjacently, so the default two workers landed on cpu0 and cpu1 — the two
hyperthreads of one physical core, with 23 cores idle on a 24C/32T part.
`topology.py` now detects the performance/efficiency split (Linux hybrid PMU
nodes, then per-core clocks, then SMT asymmetry; macOS perflevels; Windows
EfficiencyClass) and spreads the mask across distinct physical cores. It
degrades to one uniform group when nothing is detectable, which is the path
Mac, Windows and container hosts will take until someone tests them.

### Parallel layer analysis (commit 3)
`_consume` looked sequential but its only cross-layer input is the immediately
preceding layer's occupancy — a sliding *pair*, not a prefix. So `_analyze_layer`
now runs both labelings, the overlap bincount and the growth distance transform
on the worker pool, and only the accumulators, the bounded diagnostics and the
`VoidForest` union-find stay in layer order. `VoidForest.add` split into a pure
`void_components` and an ordered `merge`. The border `np.unique` is gone —
scattering `True` through the border indices is identical without the sort, and
that sort was the single largest NumPy cost in the profile.

This works in plain Python threads because `scipy.ndimage` releases the GIL:
measured 4.3x on `distance_transform_edt`, 5.0x on `binary_erosion`, 3.3x on
`ndi.label`. NumPy reductions do **not** (0.28x) — that is why the dense
`.any()` calls have to be removed by making layers sparse, not by threading.

`resources.workers = 0` now derives `min(PARALLEL_PLATEAU, physical cores)`,
using the project's existing "0 means derive" convention. The plateau is 8,
measured: 52.1 / 30.7 / 28.8 / 27.8 / 28.5 / 28.4 s at 2 / 4 / 6 / 8 / 12 / 24
workers, with RSS climbing 609 MB to 1.95 GB. The ordered merge stage is the
Amdahl limit.

## Verification status — read this before trusting the above

- **Test suite: green.** v0.1.0 is 1005 passed / 13 skipped; v0.2.0 is 1029 /
  13. The delta is exactly the new tests. Getting the skip counts to match
  needs the gitignored reference GOO symlinked at the repo root (see Setup);
  without it `test_goo.py` silently skips and the comparison is wrong.
- **Determinism: verified.** Reports are field-for-field identical at 4, 8 and
  16 workers, and the output STL hashes identically before and after the
  refactor (`69172ca4...`).
- **Full equivalence vs v0.1.0: COMPLETE and now genuinely meaningful.**
  All 13 top-level shape fixtures match on all four comparison columns —
  `inspect`, `prepare`, `prepared_stl` and `slice` — as of commits `93c27a7`
  (harness) and re-verified after `7fd41a1` and `640d11f`.

  This was weaker than it looked until `93c27a7`. The harness stopped a
  scenario at the first nonzero returncode, and `prepare` exits 2 on many
  fixtures because `--allow-unresolved` waives *unresolved islands only* --
  `torus` still fails `enclosed_voids` and `drainage_bottlenecks` while
  writing a perfectly good `prepared.stl`. Six of thirteen therefore never
  reached `slice`, so the GOO encoder was compared on two shapes while the
  summary printed "13/13 scenarios matching". Now `prepared.stl` is hashed
  whenever both sides wrote one, and `slice` runs as long as both sides exited
  `prepare` the same way.

  Runs get killed by the background-task memory guard when the machine is
  otherwise busy, so run three at a time with `--scenario`, one batch after
  another. A driver script that chains the batches is more reliable than
  polling by hand:

      .venv/bin/python -u scripts/equivalence.py --workers 2 \
          --scenario torus --scenario sphere --scenario pin_array

  Exit 0 means every scenario matched. `find_shapes` globs only the top level,
  so the five error fixtures in `fixtures/shapes/invalid/` are not covered yet.

- **Goldens recorded, but not yet usable.** `reports/golden/v010/` holds the
  v0.1.0 normalized reports for all 13 scenarios (39 files). They cannot save
  the slow side yet: `evaluate_scenario` treats a missing old root as an error
  before the golden diff block can run. `todo.md` tracks the fix.

## Setup for a fresh session

```sh
cd /home3/voxelmill
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install pybind11==3.0.4 scikit-build-core==0.11.6 manifold3d==3.3.2 tomli==2.2.1
PYBIND=$(.venv/bin/python -c 'import pybind11;print(pybind11.get_cmake_dir())')
SKBUILD_CMAKE_DEFINE="pybind11_DIR=$PYBIND" .venv/bin/python -m pip install --no-build-isolation -e '.[test,gui]'
```

Two symlinks are required and are deliberately gitignored — they point at
immutable originals that must not be copied:

```sh
ln -sfn /home3/noisecancelingcodex/inputstl inputstl
for f in /home3/noisecancelingcodex/*.goo; do ln -sfn "$f" "./$(basename "$f")"; done
```

Check: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` → 1029 passed,
13 skipped. A 14th skip means the reference GOO link is missing.

## Immediate next steps

1. Widen `scripts/equivalence.py` so a nonzero `prepare` exit no longer hides the
   slice/GOO path: compare the written `prepared.stl` whenever both sides wrote
   one, and run `slice` on it even when `prepare` exited nonzero, as long as both
   sides exited the same way. Today six of the thirteen scenarios stop at
   `prepare`, so the GOO encoder is only covered by `pin_array` and `thin_wall`.
2. Record v0.1.0 goldens once (`--new-root /home3/noisecancelingcodex
   --update-golden --golden reports/golden/v010`) so later checks stop paying
   for the slow side on every pass. Do this after step 1, so the goldens carry
   the fuller step set.
3. Re-profile. The profile in `todo.md` predates the 2.74x and is stale.
4. Then the ledger's Phase 2 items, re-ordered by what the fresh profile shows.
   Note that item 5 (share one `analyze_layers` between `pipeline._reslice` and
   `island_guard.scan_assembly_islands`) is now believed unsafe -- see the
   ledger entry for the evidence.

## Traps that will cost you time if you rediscover them

`gotchas.md` has the full list with evidence. The ones most likely to bite next:

- Do not edit `src/` while `scripts/equivalence.py` is running. Both sides are
  subprocesses importing the live tree, so a mid-run edit silently invalidates
  the comparison.
- `.goo` files are never byte-identical between runs: the header carries
  `file_create_time` and `software_version`. Compare decoded layer payloads.
- A settings default of 0 meaning "derive" has to be admitted by the validator
  *and* by every GUI widget bound to it. The Preferences spin box was
  `setRange(1, 32)`, which clamped the new default to 1 and would have written
  single-worker mode into the document for anyone who opened the dialog.
- Layer streams currently allocate a fresh mask per layer, which is the only
  reason a sliding window may hold references to previous layers. Re-check this
  before adding any stream that recycles output buffers.
