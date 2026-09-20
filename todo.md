# VoxelMill — implementation ledger

Live task list. Items are removed when done and added as they arise.
Rationale, measurements and design live in the phase notes below; verified
lessons go in `gotchas.md`.

## Now

Headline **2.32 s / 350 MB**. Stay on `master`; no release branch until a later stable.

- [x] Sanitize public tree (no machine-absolute paths; vestigial C-CLI cancelled).
- [x] Linux suite: 744+326 passed; golden 13/13.
- [x] FreeCAD path in editor.json; Import STEP disabled until found.
- [x] AppImage 0.3.0 (`output/appimage/VoxelMill-x86_64.AppImage`, 406 MiB, CUDA off).
- [x] GitHub Actions green for Linux AppImage + Mac DMGs + Windows zip (`11ae424` run 35348077136).
- [x] Smoke Intel DMG on iMac: `--version` 0.5.0, `prepare` cube, empty argv opens GUI, Finder `open` is LaunchServices Foreground (`LSBackgroundOnly=false`).
- [x] macOS GUI: resolve `FreeCAD.app` to `Contents/MacOS/FreeCADCmd`; show the window before VTK start / Locate prompt (otherwise the prompt is the last thing that appears).
- [x] Double-click / empty argv on PyInstaller Mac+Windows rewrites to `gui` (AppRun already did this on Linux).
- [x] Alpha GitHub Release `v0.5.1` (prerelease). Artifacts from run 35458532771 (`636142a`), smoked on Linux and Intel iMac. Tag-triggered rebuild 35459387477 cancelled so the published bits stay the ones that were tested. The mistyped `v5.0.1` tag/release was deleted.

## Headline number

`prepare fixtures/shapes/overhang_bracket.stl --max-passes 1 --allow-unresolved`

| Stage | Wall | Peak RSS | Note |
| --- | --- | --- | --- |
| v0.1.0 baseline | 73.7 s | 636 MB | measured 2026-09-17 |
| + CPU pinning fix (workers=2) | 61.3 s | 656 MB | -17%, affinity change only |
| + workers=4 | 61.2 s | 731 MB | no gain; consumer loop is serial |
| + workers=8 / 16 / 24 | 61.6 / 61.0 / 60.5 s | 0.9-1.1 GB | flat; only RSS grows |
| + parallel `_analyze_layer`, w=2 | 52.1 s | 609 MB | serial path, np.unique removed |
| + parallel `_analyze_layer`, w=8 | **27.8 s** | 782 MB | **2.65x vs v0.1.0**, less RSS than v0.1.0 at w=8 |
| + parallel `_analyze_layer`, w=24 | 28.4 s | 1.95 GB | past the knee; 2.5x RSS for nothing |
| + `workers=0` derives 8 (shipping default) | **26.9 s** | 784 MB | **2.74x vs v0.1.0**, no flags needed |
| + scatter dedup in `VoidForest.merge` | **18.3 s** | 780 MB | **4.02x vs v0.1.0**, 1.38x on top of the above
| + island counts computed per diagnostic | **12.9 s** | 824 MB | **5.71x vs v0.1.0**, 1.43x on top of the above
| + RLE per-layer analysis in `validation.py` | **3.45 s** | 545 MB | **21.4x vs v0.1.0**, 3.7x on top of 12.9 s; validation 0.93 s |
| + UnionLayerStream `slice_into` OR | **2.84 s** | — | island_guard 1.24 → 0.83 s |
| + native 3D EDT | **2.32 s** | 350 MB | **31.8x vs v0.1.0**; drainage 0.75 → 0.23 s |

Record a new row after every Phase 2 item so the curve is visible.

## Where the time actually goes — re-measured 2026-09-17 after the 2.74x

The v0.1.0 profile that used to sit here was stale and was steering decisions
wrong. Fresh numbers, machine otherwise idle:

| Configuration | Wall (3 runs) | Peak RSS |
| --- | --- | --- |
| default 8 workers, `--max-passes 1` | 25.21 / 25.04 / 25.13 s | 810 MB |
| `--workers 1`, `--max-passes 1` | 72.53 / 73.03 / 72.80 s | 513 MB |
| default `--max-passes` (5) | 24.90 s | — |

**The parallel speedup is 2.90x on 8 workers, not 8x.** That gap is the finding.
Default `--max-passes` measures the same as `--max-passes 1` because the island
guard converges on the first pass for this fixture, so the benchmark is not
exercising the retry loop at all.

Measuring this needed care: `cProfile` installs only on the calling thread, so a
naive profile of the 8-worker run undercounts the pool badly. The numbers below
come from `--workers 1` for honest CPU attribution plus a per-thread harness
that gives each pool thread its own profiler and merges the stats.

| Cost | Where | Note |
| --- | --- | --- |
| **~61 % of wall** | `VoidForest.merge` (validation.py:131-166) | **100 % serial, main thread** |
| 8.455 s tottime | └ `{ndarray.sort}` via `np.unique` per 64-row chunk | the single biggest lever |
| 6.934 s tottime | └ merge's own masking/indexing bookkeeping | |
| 22.1 s CPU / 2700 calls | `ndi.label` (occupancy + void) | parallel, hidden by the pool |
| 110.9 s of 190.2 thread-seconds | `_queue.SimpleQueue.get` — pool **idle** | starved, not the constraint |
| 9.35 s (workers=1) | `_growth_pixels` | |
| 1.888 s | `raster.slice_coverage` | still ~2 % |

Gone since v0.1.0: the 8.2 s border-label `np.unique` sort. Shrunk and now
parallel: `ndi.label`, the dense `.any()` reductions, `block_any`,
`distance_transform_edt`. Renamed and now dominant: the old `VoidTracker.add`
split into a cheap parallel `void_components` and a serial `VoidForest.merge`,
and the merge is where the time went.

**Refuted:** the ledger's `block_any` / `np.pad` concern. Its allocation is
0.4-0.7 s total, under 1 %, and it is called about twice per layer now, not 18.

**Done since:** `VoidForest.merge` no longer sorts (commit `36fd699`), which
took the benchmark to 18.3 s. `merge` is still the serial bottleneck at roughly
9-10 s of that 18.3 s: about 4.4 s in the dedup loop itself, now close to
memory-bandwidth-bound, and about 5 s elsewhere in `merge` that nobody has
chased yet. Going further probably means restructuring rather than
micro-optimizing.

### Second re-measure, after the scatter fix (18.4 s run)

Line-by-line `perf_counter` fences inside `merge`, verified non-perturbing
(they reproduced the baseline report exactly, `2.0886070650760757e-15` residue
included):

| statement | `--workers 1` | 8 workers |
| --- | --- | --- |
| `np.multiply` / `np.add` / `seen[chunk] = True` | **4.504 s** | **9.871 s (97.6 % of merge)** |
| everything else in `merge` | 0.19 s | 0.19 s |

**There was no "~5 s elsewhere in `merge`" — that guess was wrong.** Outside the
three dedup passes `merge` is 1.9 % of itself. The 4.4-vs-9.9 s gap is
*environmental*: the identical loop costs 4.50 s solo and 9.87 s while eight
pool threads compete for memory. Reproduced standalone at 4.40 s idle, 5.20 s
under compute-only load, 9.68 s under memory streamers.

**The union-find is free.** 24,234 `union` calls, 104,010 `find` calls,
0.053 s — 0.5 % of `merge`, 0.3 % of the run. The cost here was always bytes,
never the algorithm. Do not "optimize" the union-find.

**The dedup loop is saturated for the bytes it touches.** 41 B/px touched, 8 of
which must leave cache; 29.0 GB/s solo against this machine's measured
single-core ceilings (copy 54.4, scale 27.3, add 34.4, pure read 26.1,
int32->int64 widen 18.5 GB/s). Reshaping the NumPy passes is pointless — int32
keys measured 18 % *slower*, 2-D scatter and bincount dedup worse. Only a fused
native kernel that reads 8 B/px once beats it (measured 21.8 GB/s, 84 % of the
read ceiling, 3.8x, byte-identical keys on 18 dumped layers).

**Whole-run split at 18.2 s:** `merge` 10.15 s (55.8 %, serial),
`slice_coverage` 2.59 s (14.2 %, serial), main blocked on the pool 1.32 s,
`UnionLayerStream` 1.10 s, `occupancy_mask` 0.86 s, drainage 0.71 s. Pool: 88.8
thread-seconds of compute against 44.7 idle.

**More threads do not pay, and this retires Phase 2 item 4.** Parallel speedup
is 3.52x (64.76 s at `--workers 1`), up from 2.90x. Worker sweep: 22.61 / 19.06
/ **18.38** / 19.44 / 19.60 / 19.33 s at 4 / 6 / 8 / 12 / 16 / 24 — and still
flat at 8 even with `merge` made nearly free (11.63 / 11.88 / 11.67 at 8 / 12 /
16). **The pool is bandwidth-limited, not thread-limited**, so
`tbb::parallel_for` across layers has nothing left to win. Touch fewer pixels
instead.

**A cProfile line number is not a diagnosis.** The profile put
`{ndarray.sort}` at the top and it was the right line, but three different
sort-free rewrites of it measured 4.40 s, 7.94 s and 20.80 s on the same real
data. The compaction approach that looked obviously right on paper was 33 %
slower end to end than the code it replaced, and only benchmarking the variants
against dumped label arrays found that. Benchmark the candidate, not the theory.

**Not exercised by this fixture at all**, so unmeasurable here: Rasterizer
Z-interval persistence across passes, the `hollow.py` loops, and support KD-tree
caching — this fixture runs one pass and never hollows. Benchmark those on a
shape that actually retries or hollows before spending effort on them.

## Phase 0 — fork, baseline, ledgers

- [x] Fork the committed v0.1.0 tree to this repo root, `git init`.
- [x] Link `inputstl/` to the immutable originals (gitignored).
- [x] Build venv and native module for the new tree.
- [x] Version 0.1.0 -> 0.2.0 in `pyproject.toml` and `src/voxelmill/__init__.py`.
- [x] Start this ledger and carry `gotchas.md` forward.
- [x] Fix the CPU pinning bug in `resources.py`: it took
      `sorted(sched_getaffinity(0))[:workers]`, so the default `workers=2`
      pinned every thread to cpu0+cpu1 — the two SMT siblings of one P-core on
      a 24C/32T i9-13900HX. New `topology.py` detects the P/E split on Linux,
      macOS and Windows and `select_cpus` spreads the mask across distinct
      physical cores, fastest class first. 73.7 s -> 61.3 s.
      Added `--workers auto` and `--worker-policy performance|efficiency|all`,
      the matching `resources.worker_policy` setting, a GUI dropdown for it,
      docs, and `tests/test_topology.py` (20 tests).
      Default left at `workers=2`: the sweep shows no gain from raising it.
- [x] Freeze the equivalence baseline. v0.1.0 is **1005 passed, 13 skipped**;
      v0.2.0 is 1028 passed, 13 skipped (+23 new tests, same skips once the
      gitignored reference GOO at the repo root is symlinked in like
      `inputstl/` — without it `test_goo.py` silently skips).
      Still to do: `scripts/benchmark.py --output reports/bench/v020-baseline.json`,
      `scripts/benchmark.py --output reports/bench/v020-baseline.json`, and
      golden `prepare`/`slice` outputs for all 16 `fixtures/shapes/*.stl`
      under `reports/golden/v010/`.

## Phase 1 — instrument

- [ ] `--timing` flag emitting a unified per-stage breakdown into the report.
      Unify the scattered `seconds` fields (pipeline.py:653, hollow.py:571,
      supports.py:947, validation.py:378, goo.py:1335) behind one stage-timer
      context manager in `contracts.py`.
- [x] `scripts/equivalence.py` (527 lines): runs a command under v0.1.0 and v0.2.0, diffs the
      JSON reports structurally, byte-compare `.goo`/`.ctb` layer payloads.
      This is the "behaves identically" gate for every later phase.
      The volatile fields to normalize, measured by diffing two real runs, are
      wider than just a top-level `seconds`:
        * `seconds` at every depth (`passes[].supports.seconds`,
          `passes[].validation.metrics.seconds`, `validation.metrics.drainage.seconds`,
          `supports.analysis.seconds`, `inspection_seconds`, ...)
        * `peak_rss_bytes`, `scratch_bytes`, `analysis_workers`
        * every `path` value: the output path and the per-run scratch directory
          (`/tmp/voxelmill-prepare-<random>/prepared.stl`)
        * `settings.resources.*`, which just echoes the invocation
        * in `.goo`/`.ctb` headers: `file_create_time` and `software_version`
      Everything else compared exactly. Verified: runs at workers 4, 8 and 16
      differ in none of the substantive fields and produce byte-identical STL
      output, so the determinism gate is meaningful today.

## Phase 2 — architecture (still Python-orchestrated)

Strictly in this order. Sparse layers before threading, or the threading gets
written twice.

- [x] 1a. Widen the prefetch stage in `analyze_layers` to cover everything that
      is order-independent. DONE: `_analyze_layer` now runs both labelings, the
      overlap bincount and the growth distance transform on the pool;
      `VoidForest.add` split into pure `void_components` plus an ordered
      `merge`; the border `np.unique` replaced with a scatter into a flags
      array. `resources.workers = 0` derives `min(8, physical cores)` where 8
      is the measured plateau (`topology.PARALLEL_PLATEAU`). 61.3 s -> 26.9 s.
      Original note kept for the record: Measured: raising `--workers` from 2 to 4 changes
      nothing (61.3 s vs 61.2 s), because the pool only prefetches
      `_label_occupancy` (7.7 s of 85 s) while `_consume` runs serially on the
      main thread and carries `_growth_pixels` (31.6 s), `forest.add` (29.7 s)
      and the dense `.any()` reductions (27.4 s).
      The dependency in `_consume` is only *pairwise* — layer i needs layer
      i-1's mask, not the whole prefix — so per-layer work splits cleanly:
        * parallel, per layer: `occupancy_mask`, `label(occupied)`,
          `label(~occupied)` for the void forest, `block_any` decimation,
          overlap bincount against the previous mask, `_growth_pixels`.
        * serial, in layer order: counter accumulation, diagnostic append,
          and the `VoidForest` union-find merge.
      The scipy calls do release the GIL, measured on this machine with 8
      threads: `distance_transform_edt` 4.3x, `binary_erosion` 5.0x,
      `ndi.label` 3.3x. NumPy's `.any()` does not scale. So the 31.6 s of
      `_growth_pixels` is reachable from plain Python threads right now — this
      item does not have to wait for the native port, and should land before it.
- [-] 1b. Remaining cheap NumPy wins in `validation.py`. **Retired by
      measurement, do not do this as a standalone item.** Two of the three
      were already done as part of 1a. The leftover `block_any` pad was
      re-profiled: 0.4-0.7 s total, under 1 %, and it is called about twice
      per layer now, not 18. Item 2's `run_block_any` supersedes it on the
      RLE path anyway.
- [x] 2. **RLE per-layer analysis — scoped to `validation.py` internals.**
      DONE. Native kernels in `native/runs.cpp` (`b43a452`); wired through
      `_analyze_layer` / `void_components` / `VoidForest.merge` as one
      change. Dense fallback below 6 px/run and via `VOXELMILL_NATIVE_RUNS=0`.
      Bracket 12.9 s -> 3.45 s, RSS 824 -> 545 MB. Trapped-volume residue
      unchanged. Original assessment follows.
      Assessed in depth; `scratchpad/rle_assessment.md` has the data. The
      plan's "end-to-end, from `Rasterizer::slice` through validation, union and
      GOO encode" framing is **rejected**: that is most of the files for about a
      tenth of the win (~0.5 s of a ~7 s prize), and it changes the `Layer`
      contract, so all 8 external call sites, the GUI and every test that builds
      a `Layer` would move. Keep `analyze_layers` taking a `Layer` with a dense
      `.mask` and convert only what happens inside. **External call sites that
      change: zero.**

      Measured on real dumped layers: mean 8,640 binary runs per 3.54 Mpx layer
      = **410x compression**, worst layer 235x; other fixtures 434-1,296 px/run.
      The "material may be sparse where air is not" worry is a category error --
      material and void runs are the same partition of each row, so the void
      fraction changes run *lengths*, never run *counts*, and run count is what
      run-space algorithms cost. Confirmed on 18 real layers. Antialiasing does
      not affect validation at all, because `occupancy_mask` is `!= 0`
      (validation.py:45-52) and sees only the binary structure.

      Prototype kernels in C, benchmarked against the code they replace, all
      verified exact on 18 real layers (and 200 random fields for CCL):
      material CCL 228x, void CCL 310x, void bincount 594x, overlap bincount
      182x, `merge` pair extraction 193x **with an identical union-call
      sequence including the 64-row chunking and its cross-chunk duplicates**.
      Nothing is a blocker; only the EDT stays dense, and it already runs on a
      64x-decimated panel costing 1.07 s over the build.

      Byte-exactness is demonstrated rather than argued: an RLE rewrite does
      **not** inherently reorder the float accumulation the VoidForest gotcha
      warns about -- reordering would be a choice. The load-bearing constraint
      is that the 64-row chunking (validation.py:185-189) is carried forward
      verbatim, or the cross-chunk duplicate unions are lost and
      `peak_present_trapped_volume_mm3` moves in its last bits.

      **Do not take a partial conversion.** A spike that computes in run space
      but materializes dense labels at the boundary measured only **1.06x** --
      the materialization costs 15.6 s/900 layers, against 0.11 s/900 for all
      the run kernels put together. The unit of value is the whole per-layer
      path as one coherent change, behind a switch with the dense path retained
      as the permanent fallback for pathological density (run space loses below
      about 6 px/run; real fixtures sit two orders clear).

      Scope: ~10 functions and ~300 lines in validation.py plus
      `native/runs.cpp` (589 lines written, CMake/module.cpp wired, not yet
      rebuilt or called from Python). The `scratchpad/rle/` draft is gone;
      it was promoted into `native/runs.cpp`. Estimated 2-4 days; kernels
      are the first half.

- [x] 3. Port the per-layer analysis kernels to native code over RLE layers.
      Folded into item 2; not a separate change.

- [-] 4. Parallelize across layers with `tbb::parallel_for` — **retired by
      measurement, do not do this.** The pool is bandwidth-limited, not
      thread-limited: the worker sweep is flat past 8 (22.61 / 19.06 / 18.38 /
      19.44 / 19.60 / 19.33 s at 4 / 6 / 8 / 12 / 16 / 24) and stays flat at 8
      even with `merge` made nearly free. 33 % of pool thread-time is already
      idle waiting. More workers move no more bytes per second. The remaining
      win is touching fewer pixels (item 2), not scheduling the same pixels
      harder. Revisit only if the RLE work makes the per-layer data small
      enough to fit cache, which would change the regime.
- [~] 5. Stop running `analyze_layers` twice inside `prepare` -- **evidence says
      the two calls are NOT interchangeable; do not cache one into the other.**
      `island_guard.scan_assembly_islands` (island_guard.py:70-84) analyzes the
      in-memory `UnionLayerStream`, which rasterizes each group separately and
      ORs them (assembly.py:270). `pipeline._reslice` (pipeline.py:211-219)
      analyzes the STL after it has been written and reopened, as one flat soup
      under the nonzero winding rule, where overlapping shells can cancel
      (raster.py:125-127). That divergence is the whole reason `RasterParity`
      (assembly.py:288-332) exists, `write_stl` downcasts every vertex to
      float32 on the way out (mesh.py:293), and the two callers ask for
      different work anyway (`track_voids=False` vs `True`). pipeline.py:3-9
      states the reslice exists to describe "the file that was actually written
      rather than the in-memory solid", which sharing would defeat.
      Left open because the *cost* is still real -- the fix has to be making
      each call cheaper, not merging them. Verify this analysis independently
      before closing the item.
- [x] 5a. Skip the growth distance transform where its result is discarded.
      DONE: `analyze_layers`/`_analyze_layer` take `check_growth` (default
      True); `island_guard.scan_assembly_islands` passes False. The skipped
      check reports `growth_span = 'not_run'` and omits
      `growth_violation_pixels` rather than reading as a verified pass.
      Saving on `overhang_bracket` was within run-to-run noise on a busy
      machine (29.8 s -> 28.2 s over two runs each); the fixture holds the
      island guard to one pass, so re-measure on a shape that actually
      retries before claiming a number.
- [ ] 5b. Note for whoever rescopes item 5: there is a *third* `analyze_layers`
      inside `prepare` at pipeline.py:556-559, gated on
      `repair.support_void_policy != 'fail'` (non-default). It analyzes the
      pre-export model group, so it shares a data source with the island guard
      and is a more plausible sharing candidate than the reslice.
- [x] 5c. Stop computing per-layer island pixel counts eagerly. DONE
      (`6e3802d`), and the fix was better than the plan: the work is *deleted*,
      not deferred. The diagnostic site already held `labels` and was already
      paying a full-panel `labels == component` pass for the island's position,
      so `_island_extent` takes the count off that same mask. No lazy proxy, no
      cache, no lifetime question. `argmax` on the boolean also replaced
      `argwhere(...)[0]`, which had been materializing an index array for the
      whole component to read one row (4.81 ms -> 0.45 ms on a 3.54 Mpx panel).
      New worst case is 128 calls at 57 ms against 1,800 bincounts at 13.9 s --
      a strict reduction on every input. **18.39 s -> 12.88 s (-30 %)**, user
      CPU 82.0 -> 63.7 s. RSS unchanged: the prototype's 795 -> 660 MB was
      noise, the removed vector was per component, not per pixel.
- [-] 5d. Fused native kernel for `merge`'s overlap-pair extraction.
      **Superseded by item 2.** `run_pairs` is the 193x byte-identical
      extraction; the 3.8x dense fused kernel was never shipped.

**The ~10 s floor is gone.** Item 2 removed work from both the serial merge
and the pool; the bracket run is 3.45 s with validation at 0.93 s. Re-profile
before spending on items 6–9. The old argument that every single-sided
candidate was capped at 10 s no longer applies.

- [ ] 6. Persist the Z-interval structure on the `Rasterizer` across passes
      instead of rebuilding it (native/raster.cpp:53-66).
- [ ] 7. Fold `UnionLayerStream`'s per-group slices into one native call
      (assembly.py:239-268).
- [ ] 8. Port the pure-Python voxel loops: `hollow._bottom_open`,
      `hollow._infill_mask` hex branch.
- [ ] 9. Cache the support KD-tree across island-guard replan passes.

- [ ] **Re-profile before picking the next item.** After 2.74x the shape of the
      run has changed and the original profile is stale. `_reslice` and
      `scan_assembly_islands` (item 5) were 49 s and 34 s of the old 85 s, so
      collapsing the duplicate `analyze_layers` is probably the largest
      remaining win — but confirm with cProfile rather than assuming it.

- [x] Run every top-level scenario through `scripts/equivalence.py`. All 13
      match: cone, cube, cube_ascii, cylinder, drained_cup, hollow_cup,
      overhang_bracket, pin_array, sphere, stepped_pyramid, tetrahedron,
      thin_wall, torus.

- [x] Widen `scripts/equivalence.py` past the first failing step. DONE:
      `prepared.stl` is hashed whenever both sides wrote one and reported in
      its own column, and `slice` runs as long as both sides exited `prepare`
      the same way. All 13 scenarios now match on all four columns, where
      before six never reached `slice` at all.

- [x] Extend `scripts/equivalence.py` coverage: `find_shapes` now globs
      top-level shapes and `fixtures/shapes/invalid/` (degenerate,
      flipped_winding, nonmanifold_edge, open_box, self_intersecting).
      DONE in `de1cb64`; all five match old-vs-new. No goldens under
      `reports/golden/v010/` for those five (old-vs-new only).

- [x] Record v0.1.0 goldens once. DONE: `reports/golden/v010/` holds
      normalized inspect/prepare/slice reports for all 13 scenarios (39 files,
      1.7 MB), committed.

- [x] Make the golden-only comparison actually work. DONE in `de1cb64`:
      when `old_steps is None` and the old root is absent,
      `evaluate_scenario` calls `_evaluate_golden_only`. Default golden
      dir for that path is `reports/golden/v010`.

Run `scripts/equivalence.py` after each item. Commit each item separately.

## Phase 3 — extract `libvoxelmill_core` (C++17 + C ABI)

- [-] Cancelled: `libvoxelmill_core` extraction not pursued for the packaging plan.

## Phase 4 — standalone native CLI

- [-] Cancelled: `vm_cli_main` / standalone C CLI not pursued.

## Phase 5 — optional CUDA

- [-] Cancelled: CUDA kernels / GPU path not pursued for the packaging plan.

## Phase 6 — bracing flags, docs, release

- [x] Add `--brace-spacing-mm`, `--brace-max-length-mm`,
      `--brace-diameter-mm`, `--brace-max-distance-mm` alongside
      `--auto-bracing`, wired through `_overrides`, with CLI tests and
      docs/cli.md + docs/configuration.md updates. DONE. Completions and the
      man page are generated by introspecting the parser (shellhelp.py has no
      static option table), so they needed no change. The GUI needed none
      either, being generated from `config.DEFAULTS`.
- [ ] Update README.md, docs/architecture.md, docs/packaging.md for the
      core/binary split. Tag v0.2.0.

## Verified findings (do not re-derive)

- Support "bridging" is called **bracing** here: `support.auto_bracing` plus
  `brace_spacing_mm`, `brace_max_length_mm`, `brace_diameter_mm`,
  `brace_max_distance_mm` (`config.DEFAULTS` and `supports._brace`).
  Version 0.5.3 removes the former bottom-up start-height setting.
  The GUI exposes all five by construction — both
  `settings_table.build_descriptors()` and `ConfigurationEditor` are generated
  from `config.DEFAULTS`, and tests assert 1:1 coverage. The CLI has dedicated
  flags for the toggle and all four dimensions, as well as generic `--set`.
- Python startup is not a bottleneck: `import voxelmill.cli` is 0.10 s,
  `numpy+scipy+manifold3d` 0.20 s. A standalone binary saves ~0.3 s per run.
- The hot kernels are already C++ (native/raster.cpp, mesh.cpp, voxel.cpp,
  goo.cpp, ctb.cpp, distance.cpp, intersections.cpp). Rewriting them in C gains
  nothing; the win is architectural.
- `native/module.cpp:29-35` binds `tbb::global_control` as a worker *ceiling*.
  No kernel anywhere calls `tbb::parallel_for`. Nothing is actually parallel.
