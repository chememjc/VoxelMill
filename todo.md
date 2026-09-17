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

Record a new row after every Phase 2 item so the curve is visible.

## Where the time actually goes (cProfile, 85.5 s under profiler)

| Cost | Where | Calls |
| --- | --- | --- |
| 83.1 s (97 %) | `validation.analyze_layers` (validation.py:268) | 2 |
| 74.5 s | `_consume` per-layer body (validation.py:291) | 1800 |
| 31.6 s | `_growth_pixels` (validation.py:247) | 1798 |
| 29.7 s | `VoidTracker.add` (validation.py:122) | 1800 |
| 27.4 s tottime | `numpy.ufunc.reduce` — dense `.any()` | 46167 |
| 24.7 s | via `block_any` (validation.py:47) | 32617 |
| 8.2 s | `np.unique` border labels (validation.py:124) | 25184 |
| 7.7 s | `scipy.ndimage.label` | 1804 |
| 5.7 s | thread-lock acquire — prefetch pool idle-waiting | 760 |
| 5.4 s | `distance_transform_edt` | 1879 |
| 1.3 s | `raster.slice_coverage` — the actual rasterizer | 2700 |

Rasterization is 1.5 % of the run. `validation.py` is the target.

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
- [ ] Freeze the equivalence baseline: pytest pass/skip counts,
      `scripts/benchmark.py --output reports/bench/v020-baseline.json`, and
      golden `prepare`/`slice` outputs for all 16 `fixtures/shapes/*.stl`
      under `reports/golden/v010/`.

## Phase 1 — instrument

- [ ] `--timing` flag emitting a unified per-stage breakdown into the report.
      Unify the scattered `seconds` fields (pipeline.py:653, hollow.py:571,
      supports.py:947, validation.py:378, goo.py:1335) behind one stage-timer
      context manager in `contracts.py`.
- [ ] `scripts/equivalence.py`: run a command under v0.1.0 and v0.2.0, diff the
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

- [ ] 1a. Widen the prefetch stage in `analyze_layers` to cover everything that
      is order-independent. Measured: raising `--workers` from 2 to 4 changes
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
- [ ] 1b. Cheap NumPy wins in `validation.py`: `np.bincount` + boolean mask
      instead of `np.unique(np.concatenate(border))` (validation.py:124); hoist
      `block_any`'s padding allocation out of the per-layer loop
      (validation.py:47); stop re-deriving `occupancy_mask` where the caller
      already holds it.
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
- [ ] 5. Stop running `analyze_layers` twice inside `prepare` — share one
      analysis between `pipeline._reslice` and `island_guard.scan_assembly_islands`.
      Keep GOO's three independent passes (gotchas.md: the verify pass must
      re-raster from source).
- [ ] 6. Persist the Z-interval structure on the `Rasterizer` across passes
      instead of rebuilding it (native/raster.cpp:53-66).
- [ ] 7. Fold `UnionLayerStream`'s per-group slices into one native call
      (assembly.py:239-268).
- [ ] 8. Port the pure-Python voxel loops: `hollow._bottom_open`,
      `hollow._infill_mask` hex branch.
- [ ] 9. Cache the support KD-tree across island-guard replan passes.

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

- [ ] Add `--brace-spacing-mm`, `--brace-start-height-mm`,
      `--brace-diameter-mm`, `--brace-max-distance-mm` alongside
      `--auto-bracing` (cli.py:884), wired through `_overrides`. The GUI needs
      no change — both its surfaces are generated from `config.DEFAULTS`.
- [ ] CLI tests for the four new flags; update docs/cli.md, docs/configuration.md.
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
