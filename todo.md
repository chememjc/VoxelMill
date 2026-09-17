# VoxelMill v0.2.0 — implementation ledger

Live task list. Items are removed when done and added as they arise.
Rationale, measurements and design live in the phase notes below; verified
lessons go in `gotchas.md`.

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

**Not exercised by this fixture at all**, so unmeasurable here: Rasterizer
Z-interval persistence across passes, the `hollow.py` loops, and support KD-tree
caching — this fixture runs one pass and never hollows. Benchmark those on a
shape that actually retries or hollows before spending effort on them.

## Phase 0 — fork, baseline, ledgers

- [x] Fork the committed v0.1.0 tree to `/home3/voxelmill`, `git init`.
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
- [ ] 1b. Remaining cheap NumPy wins in `validation.py`. Two of the three are
      already done as part of 1a: the border `np.unique` is gone (scatter into
      a flags array instead), and `occupancy_mask` is now derived once per
      layer and passed to both the island labeling and the void labeling
      rather than recomputed inside `VoidForest.add`.
      Left: `block_any` (validation.py:47) still allocates a fresh padded
      array per call through `np.pad`, and it is called ~18 times per layer.
      Re-profile first — after the 2.74x the shape of the run has changed and
      this may no longer be worth doing.
- [ ] 2. Sparse/RLE layer representation end-to-end, from `Rasterizer::slice`
      through validation, union and GOO encode. Kills the dense `.any()`
      reductions — emptiness becomes O(1). Touches native/raster.cpp, raster.py,
      validation.py, assembly.py, goo.py.
- [ ] 3. Port the per-layer analysis kernels to native code over RLE layers:
      CCL, the cross-layer union-find `VoidTracker`, block-max decimation, the
      growth/EDT check. One kernel at a time, each A/B-tested against scipy.
- [ ] 4. Parallelize across layers with `tbb::parallel_for` per the threading
      design: topology from `topology.py` via `vm_set_topology`, dynamic
      work-stealing across P/E cores, worker count capped by the memory budget,
      deterministic cross-layer `VoidTracker` merge.
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

- [ ] Extend `scripts/equivalence.py` coverage: `find_shapes` globs
      `fixtures/shapes/*.stl`, which is 13 scenarios and misses the five error
      fixtures in `fixtures/shapes/invalid/` (degenerate, flipped_winding,
      nonmanifold_edge, open_box, self_intersecting). Those exercise the
      failure paths and diagnostic payloads, which are exactly the parts a
      rewrite is most likely to get subtly wrong. The harness already treats
      "both sides failed the same way" as a match, so they just need globbing.

- [x] Record v0.1.0 goldens once. DONE: `reports/golden/v010/` holds
      normalized inspect/prepare/slice reports for all 13 scenarios (39 files,
      1.7 MB), committed.

- [ ] Make the golden-only comparison actually work. `main` guards for a
      missing old root (equivalence.py:543) and there is a new-vs-golden diff
      block (equivalence.py:465), but `evaluate_scenario` returns
      `'shape missing under old root'` with `ok=False` before either can
      matter, because `old_steps is None` is unconditionally an error. Until
      this is fixed the goldens save nothing and every check still pays for
      the slow tree.

Run `scripts/equivalence.py` after each item. Commit each item separately.

## Phase 3 — extract `libvoxelmill_core` (C++17 + C ABI)

- [ ] CMake restructure: logic into `libvoxelmill_core`, `_native` becomes
      bindings only, add a `voxelmill` executable target.
- [ ] Vendor Manifold's C++ library via `FetchContent`, pinned to match the
      `manifold3d==3.3.2` wheel. Highest-risk item: ~38 call sites depend on it
      and only the Python wheel exists on this machine. Do not reimplement CSG.
- [ ] Port stages behind `VOXELMILL_NATIVE_<STAGE>=0/1` env switches so both
      paths can be A/B'd in one build. Replace in order: `ndimage.label` ->
      native CCL; `distance_transform_edt` -> Felzenszwalb-Huttenlocher EDT;
      `cKDTree` -> native KD-tree; `ConvexHull`/`Delaunay` -> Qhull;
      `csgraph.connected_components` -> adjacency-list CCL.
- [ ] Remove each switch once its native path has been green for a full run.

## Phase 4 — standalone native CLI

- [ ] `vm_cli_main(argc, argv)` in the core; the binary is a one-line `main`,
      and `voxelmill.cli:main` becomes a thin binding onto the same symbol so
      the 29 in-process test files keep working unchanged.
- [ ] Option table (~40 shared flags, 27 subcommands, ~150-180 flags total),
      `--set section.key=value` merge, config/profile precedence and provenance,
      TOML reader, JSON writer preserving `allow_nan=False` and key order,
      `.voxmil` ZIP with `project.py`'s bomb/member-count preflight checks.
- [ ] Completions and man pages from a static option table; output must stay
      byte-identical (`tests/test_shellhelp.py` runs a real subprocess).
- [ ] Point `gui/operations.py:210` at the binary instead of
      `sys.executable -m voxelmill.cli`.

## Phase 5 — optional CUDA

Re-profile first; phases 2-3 reshape the distribution. Every path opt-in via the
existing `--acceleration auto|cpu|cuda` contract, identical results to CPU, and
the build and tests must pass with no GPU and no CUDA compiler.

- [ ] Per-layer connected-component labeling on GPU (best fit; batches across
      layers).
- [ ] Batched EDT (PBA+).
- [ ] Make `native/cuda_morphology.cu` separable and shared-memory aware; it is
      currently one thread per pixel with a brute-force footprint scan.
- [ ] Share one device-resident batched-layer buffer between the CCL and EDT
      kernels so PCIe transfer does not eat the win.
- [ ] Probably skip: GPU rasterization (1.5 % of runtime), GPU orientation
      search (not the bottleneck).

## Phase 6 — bracing flags, docs, release

- [x] Add `--brace-spacing-mm`, `--brace-start-height-mm`,
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
  `brace_spacing_mm`, `brace_start_height_mm`, `brace_diameter_mm`,
  `brace_max_distance_mm` (config.py:71,118,119; `supports._brace`
  supports.py:1258). The GUI exposes all five by construction — both
  `settings_table.build_descriptors()` and `ConfigurationEditor` are generated
  from `config.DEFAULTS`, and tests assert 1:1 coverage. The CLI has a named
  flag only for `auto_bracing`; the four sizing parameters are reachable only
  through the generic `--set`. That asymmetry is the one real gap. -> Phase 6.
- Python startup is not a bottleneck: `import voxelmill.cli` is 0.10 s,
  `numpy+scipy+manifold3d` 0.20 s. A standalone binary saves ~0.3 s per run.
- The hot kernels are already C++ (native/raster.cpp, mesh.cpp, voxel.cpp,
  goo.cpp, ctb.cpp, distance.cpp, intersections.cpp). Rewriting them in C gains
  nothing; the win is architectural.
- `native/module.cpp:29-35` binds `tbb::global_control` as a worker *ceiling*.
  No kernel anywhere calls `tbb::parallel_for`. Nothing is actually parallel.
