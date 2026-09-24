# ISSUES — work before the first stable release

The single source of truth for open work: bugs, performance, architecture, tooling, release and the
feature backlog. It replaces `plan.md`, `plan2.md`, `nextsteps.md` and the task half of `platforms.md`
(now [docs/platforms.md](docs/platforms.md)). Those files were last current at commit `92ea91a`; read
them with `git show 92ea91a:nextsteps.md` and so on.

Related, and **not** trackers:

- [gotchas.md](gotchas.md) holds verified lessons. Read it before touching an area it covers.
- [docs/performance.md](docs/performance.md) holds the benchmark history and the refuted optimization ideas.
- [docs/competitor-research.md](docs/competitor-research.md) explains where the feature backlog came from.
- `todo.md` is a short recovery ledger for the task in flight. It names the ISSUES.md
  IDs being worked on and the exact next step, so work can resume after a context reset. When an item
  finishes, update it **here** and remove it from `todo.md`.

## Conventions

- **IDs.** `VM-0xx` items come from the 2026-09-23 architecture and performance audit. Feature items keep
  their original roadmap IDs (A–I, N) so older commits and reports still resolve.
- **Ease** runs 1–5, where 5 means an afternoon and 1 means a multi-week restructure. **Benefit** runs 1–5, where 5 is
  correctness or release-critical. **Score** = ease × benefit. For feature items, ease and benefit are
  mapped from the old `Diff`/`Imp` planning scores (Diff ≤14→5, ≤24→4, ≤34→3, ≤49→2, else 1; Imp
  ≥75→5, ≥60→4, ≥45→3, ≥30→2, else 1).
- **Confidence**: *sure* means reproduced or read in code; *likely* means strong evidence, confirm first;
  *measure* means decide only after a benchmark (see `docs/performance.md`: benchmark the candidate,
  not the theory).
- **Status**: `open`, `partial`, `open (hardware)` (needs a physical printer or prints),
  `deferred (decision)` (rejected for now by an explicit scope decision).
- The schema, CLI and file formats may change freely until 1.0 (see VM-049). Prefer clean redesigns
  over compatibility shims.
- License rule: VoxelMill source is MIT. New dependencies must be permissive (MIT/BSD/Apache).
  The existing LGPL PySide6/Qt use is accepted, see `licenses/THIRD-PARTY.md`. TBB and CUDA stay optional.

## Recommended sequencing

The score favours small wins. Some large items still have to land before 1.0, because they change
formats or public contracts that stable will freeze. A suggested order:

1. **Quick fixes and a safety net:** VM-001, VM-002, VM-003, VM-010, VM-060, VM-061, VM-011, then cut a
   release for VM-080.
2. **Measure before optimizing:** VM-014 (retry/hollow fixtures), VM-019 (16K scale check), then pick
   from VM-012, VM-013, VM-015 and VM-018 by what the profile shows.
3. **Redesign while nothing is frozen:** VM-040 (typed settings) → VM-041 (versioned envelope) →
   VM-044/VM-045 (registries) → VM-043 and VM-042 (splitting the god functions and `window.py`) → G3.
4. **Freeze:** VM-049, then 1.0.
5. **Hardware campaign** (whenever the printer is free): N12, B5, E7, C5, D4, D6, H4, I3.

## Priority table

Sorted from easiest and most significant to hardest and least valuable.

| ID | Item | Area | Ease | Benefit | Score | Status |
| --- | --- | --- | ---: | ---: | ---: | --- |
| VM-001 | `hollow.infill = "hex"` crashes | Bug | 5 | 4 | 20 | done |
| VM-002 | Editor job pool runs one job at a time by default | Bug | 5 | 4 | 20 | done |
| VM-080 | Release the line-actor fix | Release | 5 | 4 | 20 | open |
| N12 | Resolve GOO mirroring against both references | Feature | 4 | 5 | 20 | open (hardware) |
| VM-060 | CI workflow that runs the tests | Test/CI | 4 | 5 | 20 | done (goldens advisory until green on GitHub) |
| B3 | Printer database beyond the Mars 5 Ultra | Feature | 4 | 4 | 16 | open |
| VM-011 | Release builds ship without TBB (confirmed) | Perf | 4 | 4 | 16 | done |
| VM-003 | Editor leaks a scratch directory on every reload | Bug | 5 | 3 | 15 | done |
| VM-004 | Wall-thickness analysis could refine past the memory budget | Bug | 5 | 3 | 15 | done |
| VM-010 | Vectorize `hollow._bottom_open` | Perf | 5 | 3 | 15 | done |
| B5 | Print-time estimation: physical calibration | Feature | 3 | 5 | 15 | open (hardware) |
| D4 | Raft adhesion / removal-force calibration | Feature | 4 | 3 | 12 | open (hardware) |
| G2 | Fuzzy, mode-aware settings search | Feature | 4 | 3 | 12 | partial |
| VM-061 | Lint and type-check configuration | Test/CI | 4 | 3 | 12 | done |
| A7 | GUI profile manager: dirty-state save/discard | Feature | 3 | 4 | 12 | partial |
| E7 | TSMC: define, validate and document all 18 motion fields | Feature | 3 | 4 | 12 | partial |
| VM-013 | Spatial index for routed-capsule collision checks | Perf | 3 | 4 | 12 | open |
| VM-014 | Retry/hollow benchmark fixture and CI perf gate | Perf | 3 | 4 | 12 | open |
| VM-049 | Public-contract freeze checklist for 1.0 | Arch | 3 | 4 | 12 | open |
| A8 | Presets embedded in profiles | Feature | 5 | 2 | 10 | partial |
| VM-023 | Cheap boolean pre-checks for added models | Perf | 5 | 2 | 10 | done |
| VM-064 | Goldens for the invalid-mesh fixtures | Test/CI | 5 | 2 | 10 | done |
| VM-041 | One versioned envelope and migration registry for every file format | Arch | 2 | 5 | 10 | open |
| A4 | Profile inheritance with delta storage | Feature | 3 | 3 | 9 | open |
| A5 | Profile compatibility conditions | Feature | 3 | 3 | 9 | open |
| B2 | CTB v4/v5 reader | Feature | 3 | 3 | 9 | deferred (decision) |
| B8 | 3MF / OBJ / PLY import | Feature | 3 | 3 | 9 | open |
| C10 | Orientation weight calibration + real per-candidate support volume | Feature | 3 | 3 | 9 | partial |
| F6 | Disk-backed tiled layer records | Feature | 3 | 3 | 9 | open |
| G12 | Layer viewer: pixel inspection, A/B layer diff | Feature | 3 | 3 | 9 | partial |
| H4 | SDCP upload and print-control acceptance | Feature | 3 | 3 | 9 | open (hardware) |
| I1 | Persist and replay analysis artifacts | Feature | 3 | 3 | 9 | open |
| VM-016 | Keep VTK actors and update their input | Perf | 3 | 3 | 9 | open |
| VM-017 | Optional single-raster fast path for `slice` | Perf | 3 | 3 | 9 | open |
| VM-018 | Persist the rasterizer Z-interval structure across passes (F3) | Perf | 3 | 3 | 9 | open |
| VM-019 | Scale check at 12K–16K panels | Perf | 3 | 3 | 9 | open |
| VM-044 | Output-format registry | Arch | 3 | 3 | 9 | open |
| VM-081 | Test the Apple Silicon build | Release | 3 | 3 | 9 | open |
| G8 | Keyboard shortcut editor (theme shipped) | Feature | 4 | 2 | 8 | partial |
| I3 | Print-time auto-calibration from measured prints | Feature | 4 | 2 | 8 | open (hardware) |
| VM-020 | Cache the support KD-tree across island passes | Perf | 4 | 2 | 8 | open |
| VM-022 | Cheaper per-override setting validation | Perf | 4 | 2 | 8 | open |
| VM-065 | Platform-honest affinity tests | Test/CI | 4 | 2 | 8 | open |
| C8 | Cap non-planar open cuts | Feature | 2 | 4 | 8 | open |
| G3 | Typed settings pages replace the raw JSON box | Feature | 2 | 4 | 8 | partial |
| E2 | Per-Z-band / per-object slice overrides | Feature | 3 | 2 | 6 | open |
| E3 | Cross-sectional-area-driven exposure | Feature | 3 | 2 | 6 | open |
| E6 | LED uniformity mask compensation | Feature | 3 | 2 | 6 | open |
| F5 | SIMD in the rasterizer inner loop | Feature | 3 | 2 | 6 | open |
| VM-021 | Vectorize contour/boundary sampling | Perf | 3 | 2 | 6 | done |
| VM-024 | Only one of the three `analyze_layers` calls in `prepare` can be shared | Perf | 3 | 2 | 6 | open |
| VM-025 | Fold `UnionLayerStream` per-group slices into one native call | Perf | 3 | 2 | 6 | open |
| VM-063 | Direct tests for `gui/services.py` and camera math | Test/CI | 3 | 2 | 6 | open |
| VM-083 | Memory ceiling on macOS and Windows | Release | 3 | 2 | 6 | open |
| VM-085 | PyPI wheels / Flatpak (H5) | Release | 3 | 2 | 6 | open |
| A10 | Per-Z-band overrides (per-object support overrides shipped) | Feature | 2 | 3 | 6 | partial |
| C5 | Suction-cup / peel force calibration | Feature | 2 | 3 | 6 | open (hardware) |
| F4 | Incremental re-slice after a local edit | Feature | 2 | 3 | 6 | open |
| G13 | Direct-manipulation gizmos for supports, holes and cut planes | Feature | 2 | 3 | 6 | partial |
| VM-043 | Break up the god functions in routing and orchestration | Arch | 2 | 3 | 6 | open |
| VM-045 | Strategy registry for bases, tips and anchors | Arch | 2 | 3 | 6 | open |
| VM-082 | macOS signing and notarization | Release | 2 | 3 | 6 | open |
| VM-026 | Link-time optimization for `_native` | Perf | 5 | 1 | 5 | open |
| VM-046 | One structured error helper | Arch | 5 | 1 | 5 | won't fix (typed instead) |
| VM-047 | Deduplicate voxel-size bisection | Arch | 5 | 1 | 5 | done |
| VM-048 | Consistent dtype contract at the pybind boundary | Arch | 5 | 1 | 5 | done |
| VM-062 | Shared `tests/conftest.py` | Test/CI | 5 | 1 | 5 | open |
| VM-070 | Docstrings for the largest undocumented functions | Docs | 5 | 1 | 5 | open |
| VM-040 | Typed settings model as the single source of truth | Arch | 1 | 5 | 5 | open |
| VM-012 | Stop re-sampling downward faces for the overhang check | Perf | 4 | 1 | 4 | won't fix (measured) |
| VM-071 | Section-aware help for repeated field names | Docs | 4 | 1 | 4 | open |
| VM-084 | Windows topology on real hybrid hardware | Release | 4 | 1 | 4 | open |
| F7 | GPU orientation search (CPU fallback mandatory) | Feature | 2 | 2 | 4 | open |
| VM-027 | x86-64-v3 kernels with runtime dispatch | Perf | 2 | 2 | 4 | open |
| B1 | Encrypted CTB writer | Feature | 1 | 4 | 4 | deferred (decision) |
| VM-015 | Incremental island-guard passes | Perf | 1 | 4 | 4 | open |
| VM-042 | Split `gui/window.py` (3,874 lines) into controllers | Arch | 1 | 4 | 4 | open |
| A9 | Import CHITUBOX / Lychee profiles | Feature | 3 | 1 | 3 | open |
| C11 | Text / serial embossing | Feature | 3 | 1 | 3 | open |
| VM-028 | Minor: CUDA morphology allocation, MST, lock polling, undo copies | Perf | 3 | 1 | 3 | open |
| VM-072 | The historical `part-to-part` support example routes nothing | Docs | 3 | 1 | 3 | open |
| D1 | Joint support type | Feature | 1 | 3 | 3 | partial |
| D6 | Support mechanics calibration; promote anchor-load warn→fail | Feature | 1 | 3 | 3 | open (hardware) |

## Bugs

### VM-001 — `hollow.infill = "hex"` crashes

Ease 5 · Benefit 4 · Confidence: sure · Status: done

**Problem.** Config validation accepts `hex`, but `_infill_mask` raises `IndexError` for any grid wider than 1×1: `struts[(kk % period) == 0] = True` indexes a (nz, ny, nx) array with an ogrid mask of shape (nz, 1, 1). Nothing tests it. The same branch also builds struts in a pure-Python O(nx·ny) loop.

**Fix.** Build the XY strut mask from the `ii`/`jj` ogrids (`phase = (jj // row_pitch) % 2`, `(ii - phase * (col_pitch // 2)) % col_pitch == 0 | jj % row_pitch == 0`), then OR in the Z decks with `struts |= (kk % period) == 0`. Add tests for all three lattices that check shape, a nonempty result, and struts confined to `cavity`.

**Where.** `src/voxelmill/hollow.py:300-311`, `src/voxelmill/config.py:544`

### VM-002 — Editor job pool runs one job at a time by default

Ease 5 · Benefit 4 · Confidence: sure · Status: done

**Problem.** `JobRunner(max_threads=max(1, settings.resources.workers))` treats the default `workers = 0` (auto) as 1, so every background job in the editor (placement, routing, island checks, layer scrubs) waits behind the job before it.

**Fix.** Resolve auto with `topology.default_workers()`, the helper the CLI uses, and cap it at a small number (2–4) because each job already runs its own internal pool. The generation counter in `jobs.py` already drops stale results, so running jobs in parallel is safe for the document.

**Where.** `src/voxelmill/gui/window.py:162`, `src/voxelmill/contracts.py:56-58`

### VM-003 — Editor leaks a scratch directory on every reload

Ease 5 · Benefit 3 · Confidence: sure · Status: done

**Problem.** `services.load_and_place` creates a new `mkdtemp()` holding a memmapped `placed.f32` (36 bytes per triangle) on every reload: open, pose edit, undo/redo, profile apply. `MainWindow` overwrites `self.scratch` and never removes the old one, and nothing cleans them up on exit. `Document.reset` also swallows `extract.cleanup()` errors silently.

**Fix.** Give `MainWindow` a single owned `TemporaryDirectory` per session, or delete the previous directory after the new placement lands and on `closeEvent`, with an `atexit` backstop. Log cleanup failures instead of dropping them.

**Where.** `src/voxelmill/gui/services.py:66`, `src/voxelmill/gui/window.py:3167`, `src/voxelmill/gui/document.py:635-638`

### VM-004 — Wall-thickness analysis could refine past the memory budget

Ease 5 · Benefit 3 · Confidence: sure · Status: done

**Problem.** Found while fixing the VM-061 lint warnings (an unused `needed`). `analyze_wall_thickness` refines the pitch toward `threshold / 4` after `choose_hollow_voxel_size` has fitted it to the budget, and never re-checks the refined grid. A 20 mm cube with a 0.2 mm threshold asked for 67 M voxels against a 40 k ceiling, before an 8-byte-per-voxel EDT on top.

**Fix.** Refine only as far as the same ceiling allows, through a `_finest_fitting_pitch` bisection shared with `choose_hollow_voxel_size`. There is a regression test.

**Where.** `src/voxelmill/hollow.py` (`analyze_wall_thickness`, `_finest_fitting_pitch`)

## Performance

### VM-010 — Vectorize `hollow._bottom_open`

Ease 5 · Benefit 3 · Confidence: sure · Status: done

**Problem.** A Python triple loop over every XY column and Z voxel took 1.08 s on a 200×300×300 grid. It grows with the cube of the voxel count, so fine hollowing walls cost seconds to minutes.

**Fix.** `has = core.any(0)`; `top = nz - 1 - argmax(core[::-1], 0)`; `removal = core | (occupancy & (arange(nz)[:, None, None] <= top) & has)`. This expected to cost about 50 ms. Compare against the old output on `hollow_cup`.

**Where.** `src/voxelmill/hollow.py:270-286`

### VM-011 — Release builds ship without TBB (confirmed)

Ease 4 · Benefit 4 · Confidence: likely · Status: done

**Problem.** The release workflow installs no TBB, and `find_package(TBB QUIET)` fails silently. Released binaries then run the native 3D EDT (drainage) single-threaded and lack `_native.WorkerLimit`. Local development builds do have TBB, so benchmarks overstate what users get.

**Fix.** Check first: `python -c "import voxelmill._native as n; print(hasattr(n, 'WorkerLimit'))"` on a CI artifact. Then install oneTBB (Apache-2.0) in all three CI jobs, add `message(STATUS "TBB: …")`, and expose `_native.HAS_TBB` in the report `resources` block. Better still, give `parallel_n` in `edt.cpp` a `std::thread` fallback so builds without TBB stay parallel.

**Where.** `CMakeLists.txt:22-26`, `native/edt.cpp:26-35`, `.github/workflows/release.yml:79-97`

### VM-012 — Stop re-sampling downward faces for the overhang check

Ease 4 · Benefit 1 · Confidence: sure · Status: won't fix (measured)

**Problem.** `apply_overhang_check` calls `downward_contacts` again after routing already computed the identical samples. That doubles the sampling cost, including the Python-loop perimeter and boundary samplers, on every `prepare` and every GUI print check.

**Fix.** Return the samples from `plan_supports` (they already feed `contact_coverage`) and pass them into `analyze_overhangs`. Alternatively, memoize `downward_contacts` on (triangles id, relevant support keys). The comment requires the same sampler, and reuse keeps that guarantee.

**Where.** `src/voxelmill/overhangs.py:67`, `src/voxelmill/supports.py:512,551`, `src/voxelmill/pipeline.py:553`

**Measured (2026-09-23).** With default settings the sampler takes 24 ms on a 245k-triangle sphere, so the duplicate call is noise. With `contour_supports` on it took 0.57 s, nearly all of it in `_perimeter_samples`. VM-021 vectorized that (4.06 s → 0.45 s on all 245k faces, bit-identical), which removes what was worth saving here.

### VM-013 — Spatial index for routed-capsule collision checks

Ease 3 · Benefit 4 · Confidence: sure · Status: open

**Problem.** `_hits_occupied` scans every previously routed capsule for each candidate route, so `route_contacts` is O(k²) in contacts, and the island guard repeats it for each pass. Plates with thousands of contacts will be dominated by this.

**Fix.** Keep a uniform XY bucket grid (cell ≈ 2 × max pillar radius + clearance) of capsule AABBs on `ColumnField`, and test only the capsules in overlapping cells. Vectorize the five sample points per capsule pair with NumPy. Benchmark on `pin_array` and a dense plate first.

**Where.** `src/voxelmill/supports.py:423-455`

### VM-014 — Retry/hollow benchmark fixture and CI perf gate

Ease 3 · Benefit 4 · Confidence: sure · Status: open

**Problem.** The canonical bracket benchmark converges in one island pass and never hollows, so VM-010, VM-013, VM-015 and VM-020 cannot be measured. The benchmark harness exists (F10), but nothing runs it automatically.

**Fix.** Add fixtures that need ≥3 island passes and a hollow run to `scripts/benchmark.py`. Record the baselines in `reports/bench/`. Add an opt-in CI job that fails on a >15 % regression against the stored baseline.

**Where.** `scripts/benchmark.py`, `reports/bench/baseline.json`, `docs/performance.md`

### VM-015 — Incremental island-guard passes

Ease 1 · Benefit 4 · Confidence: sure · Status: open

**Problem.** Each island-guard pass re-runs `replan` over all contacts (full routing) and a full Manifold `assemble` of every support solid. Only the raster scan is cropped. On plates that retry, this is passes × (routing + boolean union). `point not in extra` is also a list scan.

**Fix.** Route only the new contacts against the existing `ColumnField`/occupied capsules, then union the new solids onto the previous union, or keep supports as a separate raster group, which the grouped raster path already supports. Use a set for `extra`. Depends on VM-013 and VM-014.

**Where.** `src/voxelmill/island_guard.py:122-203`, `src/voxelmill/pipeline.py:458-464`

### VM-016 — Keep VTK actors and update their input

Ease 3 · Benefit 3 · Confidence: likely · Status: open

**Problem.** `Scene.set_mesh` tears down and rebuilds mapper, actor and polydata (deep-copying through `numpy_to_vtk`) on the UI thread after every model, support or attachment job. It also runs `out_of_bounds_mask` over the full array before decimation. Near the 1.5M-triangle display cap this causes visible stalls after each edit.

**Fix.** Reuse the actor and mapper per key and swap `SetInputData`. Build the polydata in the worker job (pure VTK data objects are thread-safe to construct). Decimate before computing the mask.

**Where.** `src/voxelmill/gui/viewport.py:712-755`, `src/voxelmill/gui/window.py:3186-3229`

### VM-017 — Optional single-raster fast path for `slice`

Ease 3 · Benefit 3 · Confidence: sure · Status: open

**Problem.** `slice_stl` rasterizes the source three times: once to validate, once to write, and once to verify decoded pixels. The independence is deliberate, but at 12K–16K panels slicing is the dominant cost.

**Fix.** Keep the default. Add `--verify=independent|reuse` (in config: `slice.verification`), where `reuse` writes from the validation raster and still decodes and verifies the written file. The report must record the mode.

**Where.** `src/voxelmill/goo.py:1193` (`slice_stl`)

### VM-018 — Persist the rasterizer Z-interval structure across passes (F3)

Ease 3 · Benefit 3 · Confidence: measure · Status: open

**Problem.** The rasterizer rebuilds its per-triangle Z ordering each time it is constructed. The island guard and the reslice build several of them for mostly identical geometry.

**Fix.** Cache the sorted Z-interval index on the `Rasterizer` and reuse it per mesh identity. Consider an interval tree so each layer tests only its active set. Measure on a retrying fixture (VM-014) first.

**Where.** `native/raster.cpp:53-66`

### VM-019 — Scale check at 12K–16K panels

Ease 3 · Benefit 3 · Confidence: measure · Status: open

**Problem.** Validation keeps dense per-layer masks (a 15360×8640 panel is 133 MB per uint8 mask) and several label images per worker. With eight workers, peak RSS may exceed typical 16 GB desktops. The disk-backed tiled records (F6) were never built.

**Fix.** Add a synthetic 16K printer profile to the benchmark and record peak RSS against worker count. If it is over budget, make `ResourceBudget` cap in-flight layers, then consider F6.

**Where.** `src/voxelmill/validation.py:706-810`, `src/voxelmill/resources.py`

### VM-020 — Cache the support KD-tree across island passes

Ease 4 · Benefit 2 · Confidence: measure · Status: open

**Problem.** Each replan rebuilds spatial structures over unchanged model geometry.

**Fix.** Build them once per `ColumnField` and pass them through `replan`.

**Where.** `src/voxelmill/supports.py`, `src/voxelmill/island_guard.py`

### VM-021 — Vectorize contour/boundary sampling

Ease 3 · Benefit 2 · Confidence: likely · Status: done

**Problem.** `_perimeter_samples` builds a Python dict of rounded vertex-pair tuples one triangle at a time, which dominates `downward_contacts` when contour or boundary supports are on.

**Fix.** Use the `np.unique(..., axis=0, return_counts=True)` edge-dedup that `_open_boundary_samples` already uses.

**Where.** `src/voxelmill/supports.py:290-311`

**Done (2026-09-23).** `_perimeter_samples` and `_open_boundary_samples` now build and sample every edge in NumPy through `_sample_segments`. Output is bit-identical to the loops (pinned by a reference test), and 9× faster on 245k faces.

### VM-022 — Cheaper per-override setting validation

Ease 4 · Benefit 2 · Confidence: likely · Status: open

**Problem.** `normalize_contact_parameters` runs a full `resolve_settings` (deepcopy of DEFAULTS plus the 249-line validator) for every per-contact override record, on every island pass.

**Fix.** Validate only the merged `support` subsection, or memoize by the canonical JSON of the override. This becomes free once the settings are typed (VM-040).

**Where.** `src/voxelmill/contact_parameters.py:51`

### VM-023 — Cheap boolean pre-checks for added models

Ease 5 · Benefit 2 · Confidence: sure · Status: done

**Problem.** `_append_extra_models` runs an exact Manifold intersection between every pair of parts.

**Fix.** Skip pairs whose AABBs do not overlap before the exact boolean.

**Where.** `src/voxelmill/pipeline.py:188-198`

### VM-024 — Only one of the three `analyze_layers` calls in `prepare` can be shared

Ease 3 · Benefit 2 · Confidence: likely · Status: open

**Problem.** Measured: the island-guard scan and the reslice analyze different data and must stay separate (see `docs/performance.md`). The third call, which runs when `repair.support_void_policy != "fail"`, analyzes the same pre-export group as the island guard.

**Fix.** Reuse the island guard's final full scan for the support-void check when the geometry is identical. Keep the reslice independent.

**Where.** `src/voxelmill/pipeline.py:556-559`

### VM-025 — Fold `UnionLayerStream` per-group slices into one native call

Ease 3 · Benefit 2 · Confidence: measure · Status: open

**Problem.** Each layer slices every group separately and ORs them in Python.

**Fix.** Add a native multi-rasterizer OR (`slice_into` already exists), and measure against VM-014.

**Where.** `src/voxelmill/assembly.py:239-270`

### VM-026 — Link-time optimization for `_native`

Ease 5 · Benefit 1 · Confidence: measure · Status: open

**Problem.** No `INTERPROCEDURAL_OPTIMIZATION` is set. The gain is probably small, because the hot loops live inside single translation units.

**Fix.** `set_property(TARGET _native PROPERTY INTERPROCEDURAL_OPTIMIZATION TRUE)` when `check_ipo_supported` passes. Keep it only if the benchmark improves.

**Where.** `CMakeLists.txt`

### VM-027 — x86-64-v3 kernels with runtime dispatch

Ease 2 · Benefit 2 · Confidence: measure · Status: open

**Problem.** Portable wheels compile for baseline x86-64. The RLE, GOO/CTB encode and EDT loops might gain from AVX2, but the regime is memory-bound, and the ledger deprioritized SIMD (F5).

**Fix.** Use `target_clones` or a small manual dispatch for two or three kernels, only if a profile shows them compute-bound. Never ship `-march=native`.

**Where.** `native/runs.cpp`, `native/goo.cpp`, `native/edt.cpp`

### VM-028 — Minor: CUDA morphology allocation, MST, lock polling, undo copies

Ease 3 · Benefit 1 · Confidence: sure · Status: open

**Problem.** The CUDA morphology does `cudaMalloc`/`cudaMemcpy`/`cudaFree` on every call. `minimum_spanning_edges` is O(n²) (capped at 8192 feet). `LayerSlicer` polls its lock every 50 ms. Undo deep-copies the whole settings dict on every edit.

**Fix.** Handle each when a profile shows it matters: persistent device buffers; `scipy.sparse.csgraph.minimum_spanning_tree` over a k-NN graph; a condition variable; diff-based undo records.

**Where.** `native/cuda_morphology.cu:9-13`, `src/voxelmill/bases.py:22-52`, `src/voxelmill/gui/services.py:301-343`, `src/voxelmill/gui/document.py:161-194`

## Architecture and extensibility

### VM-040 — Typed settings model as the single source of truth

Ease 1 · Benefit 5 · Confidence: sure · Status: open

**Problem.** Settings are a nested dict. Defaults live in `config.DEFAULTS`, and rules live in a 249-line procedural `validate_settings`. There are 157 `settings['section']['key']` accesses and separate descriptor tables for GUI help, CLI flags and legacy fill. Adding a setting touches 3–5 places, and nothing checks key names statically.

**Fix.** One frozen dataclass per section (`PrinterSettings`, `ProcessSettings`, `SupportSettings`, …). Each field carries its default, units, bounds, help text, CLI flag, GUI tier and a `validate()` for cross-field rules. Generate DEFAULTS, the JSON schema, `--set` parsing, the GUI descriptor table, shell completion and `docs/configuration.md` from it. Use the standard library (dataclasses) with no new dependency. Migrate module by module behind a `Settings.from_dict()` adapter.

**Where.** `src/voxelmill/config.py:26-550`, `src/voxelmill/gui/settings_table.py`

### VM-041 — One versioned envelope and migration registry for every file format

Ease 2 · Benefit 5 · Confidence: sure · Status: open

**Problem.** Profiles, presets, projects and reports each carry their own `schema_version` handling (`1` literals in `profiles.py`, `PRESET_SCHEMA_VERSION`, project `SCHEMA_VERSION`) and ad-hoc legacy shims (`fill_legacy_settings`). After the first stable release, every mismatch here becomes a compatibility promise.

**Fix.** A shared `{"kind": ..., "schema_version": N, ...}` envelope in `contracts.py`, with a `MIGRATIONS[kind][N] -> N+1` registry and a single `load_versioned(kind, data)` entry point. Reset all versions to 1 at stable and delete the pre-stable shims. Document the stability policy (what may change in a minor release) in ISSUES.md or `docs/`.

**Where.** `src/voxelmill/config.py:219-250`, `src/voxelmill/presets.py:20`, `src/voxelmill/profiles.py:245`, `src/voxelmill/project.py`

### VM-042 — Split `gui/window.py` (3,874 lines) into controllers

Ease 1 · Benefit 4 · Confidence: sure · Status: open

**Problem.** One class owns menu construction, the Setup form, pose editing, paint, orientation candidates, island/print checks, project I/O, export and every job-completion handler. Adding a panel or tool means touching scattered dock and tab indices.

**Fix.** Extract `setup_page.py` (settings form, tiers, modified markers), `menus.py` (actions), `pose_controller.py` (pose, motion, arrange), `pipeline_controller.py` (reload/rebuild/assemble/attachments and the `_finish_*` handlers), `layers_controller.py` (layer/fault/GOO tabs) and `export_controller.py` (export, validate, autosave). Each takes `document`, `jobs` and `scene` and emits signals, leaving `MainWindow` to assemble them. `services.py`/`jobs.py` are already Qt-clean, so this is relocation, not redesign. Do it after VM-040 so the Setup page is generated.

**Where.** `src/voxelmill/gui/window.py`

### VM-043 — Break up the god functions in routing and orchestration

Ease 2 · Benefit 3 · Confidence: sure · Status: open

**Problem.** `supports.route_contacts` is 515 lines, with around 15 `global_*` shadow variables saved and restored around a per-contact loop. `supports._brace` is 425 lines, `pipeline.prepare` 391, and `bases.build_base` 150. They are hard to test piecemeal and hard to extend.

**Fix.** Make a `ContactRouter` class with per-contact parameter resolution, candidate search, tip/anchor sizing and graph emission as methods. Turn `prepare` into a list of named stage functions sharing a `PrepareContext` dataclass, which also gives `stage_timing` its stage names for free. The golden reports (`scripts/equivalence.py`) guard behavior.

**Where.** `src/voxelmill/supports.py:661,1529`, `src/voxelmill/pipeline.py:304`, `src/voxelmill/bases.py:220`

### VM-044 — Output-format registry

Ease 3 · Benefit 3 · Confidence: sure · Status: open

**Problem.** GOO and CTB are wired separately through `slice`, `*-info`, `convert`, `verify` and the GUI layer viewer. A third format means editing every one of those.

**Fix.** A `PrinterFormat` protocol (`suffixes`, `encode_layer`, `decode_layer`, `read_header`, `write`, `verify`) and a `FORMATS` registry keyed by `printer.output_formats`. Collapse `goo-info`/`ctb-info` into `info`. The CLI can break freely before stable.

**Where.** `src/voxelmill/cli.py:482-500`, `src/voxelmill/goo.py`, `src/voxelmill/ctb.py`

### VM-045 — Strategy registry for bases, tips and anchors

Ease 2 · Benefit 3 · Confidence: likely · Status: open

**Problem.** Adding a base type or tip shape needs a new tuple entry in `config.py`, an enum branch in `validate_settings`, and a branch inside `build_base`/`route_contacts`.

**Fix.** Register strategies as `{name: Strategy}` objects that declare their parameters (fed into VM-040) and a `build()` method.

**Where.** `src/voxelmill/config.py:14-24,393-411`, `src/voxelmill/bases.py:220`

### VM-046 — One structured error helper

Ease 5 · Benefit 1 · Confidence: sure · Status: won't fix (typed instead)

**Problem.** `config._error`, `presets._fail`, `profiles._fail` and `project._fail` are four copies of the same helper.

**Fix.** `VoxelMillError.invalid(code, message, **detail)` in `contracts.py`.

**Where.** `src/voxelmill/config.py:253`, `src/voxelmill/presets.py:141`, `src/voxelmill/profiles.py:39`, `src/voxelmill/project.py:32`

**Decision (2026-09-23).** Each helper is a two-line binding of its module's error code, so a shared factory would save nothing. They are now typed `-> NoReturn`, which is what lets mypy follow control flow past them. The real consolidation of error handling comes with VM-041's single loader.

### VM-047 — Deduplicate voxel-size bisection

Ease 5 · Benefit 1 · Confidence: sure · Status: done

**Problem.** `repair.choose_voxel_size` and `hollow.choose_hollow_voxel_size` share a near-identical 48-step bisection with the same budget math.

**Fix.** Extract `_bisect_pitch(bounds, budget, floor, cap)` in `repair.py`.

**Where.** `src/voxelmill/repair.py:33-83`, `src/voxelmill/hollow.py:25-65`

### VM-048 — Consistent dtype contract at the pybind boundary

Ease 5 · Benefit 1 · Confidence: sure · Status: done

**Problem.** `inspect_intersections` accepts only float32, while `inspect_mesh` and the distance kernels also accept float64. `repair.py` works around this with a cast at the call site.

**Fix.** Accept both (convert to float32 in the binding) and document dtype and contiguity in every binding docstring.

**Where.** `native/intersections.cpp:30`, `src/voxelmill/repair.py:182`

### VM-049 — Public-contract freeze checklist for 1.0

Ease 3 · Benefit 4 · Confidence: sure · Status: open

**Problem.** Pre-stable, anything can change. At stable, the CLI flags and exit codes, report schema, project and profile formats and the `--set` key paths become promises. Pinning `manifold3d==3.3.2` and `tomli==2.2.1` exactly will also block downstream packagers.

**Fix.** Before tagging 1.0: rename anything awkward (VM-040/041/044 first), freeze `schemas/voxelmill-report.schema.json` at v1, document exit codes, relax exact pins to compatible ranges, and add a test that fails when a CLI flag disappears.

**Where.** `pyproject.toml:11`, `schemas/`, `docs/cli.md`

## Testing, CI and tooling

### VM-060 — CI workflow that runs the tests

Ease 4 · Benefit 5 · Confidence: sure · Status: done (goldens advisory until green on GitHub)

**Problem.** The only workflow is `release.yml` (manual or tag). It never runs pytest. The ~800-test suite and the golden equivalence check only run when someone remembers.

**Fix.** Add `.github/workflows/ci.yml` on push/PR: Linux, Python 3.10 and 3.12, with TBB installed. Build `_native`, run `pytest -m "not samples"` with `QT_QPA_PLATFORM=offscreen`, then `scripts/equivalence.py` against `reports/golden/`. Add a macOS/Windows smoke subset (non-GUI) weekly.

**Where.** `.github/workflows/release.yml`

### VM-061 — Lint and type-check configuration

Ease 4 · Benefit 3 · Confidence: sure · Status: done

**Problem.** No ruff, mypy or pre-commit config exists, so style and simple bugs (unused variables, dead code like the palette line removed in this audit) go unnoticed.

**Fix.** Add `[tool.ruff]` (pyflakes, bugbear and import sort; start permissive) and `[tool.mypy]` with `check_untyped_defs` on `contracts.py`/`config.py` first, tightening per module. Run both in CI. Both tools are MIT-licensed and dev-only.

**Where.** `pyproject.toml`

### VM-062 — Shared `tests/conftest.py`

Ease 5 · Benefit 1 · Confidence: sure · Status: open

**Problem.** Every GUI test module sets `QT_QPA_PLATFORM` itself, and the fixture paths are rebuilt per file.

**Fix.** Centralize the offscreen Qt setup, fixture paths and a `tmp_settings` fixture.

**Where.** `tests/test_gui_*.py`

### VM-063 — Direct tests for `gui/services.py` and camera math

Ease 3 · Benefit 2 · Confidence: likely · Status: open

**Problem.** `route_attachments`, `export_and_validate` and `run_print_checks` are only exercised through window wiring tests, and `camera.py` transitions have no direct tests.

**Fix.** Add Qt-free unit tests. `services.py` is already Qt-free by design.

**Where.** `src/voxelmill/gui/services.py`, `src/voxelmill/gui/camera.py`

### VM-064 — Goldens for the invalid-mesh fixtures

Ease 5 · Benefit 2 · Confidence: sure · Status: done

**Problem.** The five `fixtures/shapes/invalid/` meshes only have old-versus-new equivalence, with no committed goldens.

**Fix.** Record them with `scripts/equivalence.py` into `reports/golden/`.

**Where.** `fixtures/shapes/invalid/`, `reports/golden/`

### VM-065 — Platform-honest affinity tests

Ease 4 · Benefit 2 · Confidence: sure · Status: open

**Problem.** Affinity tests assume Linux `sched_setaffinity`. On macOS/Windows they need a skip or a test double rather than passing vacuously.

**Fix.** Mark them per platform and add a Windows `EfficiencyClass` double.

**Where.** `tests/test_topology.py`, `docs/platforms.md`

## Documentation

### VM-070 — Docstrings for the largest undocumented functions

Ease 5 · Benefit 1 · Confidence: sure · Status: open

**Problem.** `bases.build_base` has no per-strategy geometric contract. `ops.cap_open_cuts` (141 lines) has no docstring. `printer.py` does not say that a dropped connection is not reconnected automatically.

**Fix.** Add the docstrings, including the reconnect contract in the `printer.py` module docstring.

**Where.** `src/voxelmill/bases.py:220`, `src/voxelmill/ops.py:386`, `src/voxelmill/printer.py:420-486`

### VM-071 — Section-aware help for repeated field names

Ease 4 · Benefit 1 · Confidence: sure · Status: open

**Problem.** `id`, `name`, `enabled` and `voxel_size_mm` repeat across settings sections, and the help table keys them by path rather than being section-aware. This resolves itself with VM-040.

**Fix.** Fold into VM-040.

**Where.** `src/voxelmill/gui/helptext.py`

### VM-072 — The historical `part-to-part` support example routes nothing

Ease 3 · Benefit 1 · Confidence: sure · Status: open

**Problem.** Under default settings the `part-to-part` example layout produces no routes, which is why `showcase` forces its own.

**Fix.** Fix the layout or drop the example.

**Where.** `src/voxelmill/support_example.py`

## Release and platforms

### VM-080 — Release the line-actor fix

Ease 5 · Benefit 4 · Confidence: sure · Status: open

**Problem.** The build-volume and navigation-cube outline fix (one two-point actor per line) is verified on `master` and in the workflow's Windows build, but the published v0.5.4 assets predate it.

**Fix.** Cut v0.5.5 after VM-001–VM-003 land.

**Where.** `gotchas.md`, `reports/releases/v0.5.4.md`

### VM-081 — Test the Apple Silicon build

Ease 3 · Benefit 3 · Confidence: sure · Status: open

**Problem.** The arm64 DMG is built but has never run. Wheel availability of `manifold3d` and VTK on arm64 is also unconfirmed.

**Fix.** Smoke-test it on an M-series host (for example a CI `macos-14` runner running `--version` and a `prepare` of a cube).

**Where.** `docs/platforms.md`

### VM-082 — macOS signing and notarization

Ease 2 · Benefit 3 · Confidence: sure · Status: open

**Problem.** The DMGs are unsigned, so users must bypass Gatekeeper by hand.

**Fix.** Get a Developer ID, then run `codesign` and `notarytool` in `release.yml`.

**Where.** `docs/platforms.md`, `docs/packaging.md`

### VM-083 — Memory ceiling on macOS and Windows

Ease 3 · Benefit 2 · Confidence: sure · Status: open

**Problem.** `RLIMIT_AS` does not exist there. Windows could use a Job-object commit limit, which is not wired; macOS has no equivalent.

**Fix.** Implement a Windows Job object. On macOS, make the budget advisory and have the report say so.

**Where.** `src/voxelmill/resources.py`, `docs/platforms.md`

### VM-084 — Windows topology on real hybrid hardware

Ease 4 · Benefit 1 · Confidence: sure · Status: open

**Problem.** The `EfficiencyClass` detection is untested on a real P/E-core Windows machine.

**Fix.** On one, run `python -c "from voxelmill import topology; print(topology.detect())"` and record the P/E split it reports.

**Where.** `src/voxelmill/topology.py`

### VM-085 — PyPI wheels / Flatpak (H5)

Ease 3 · Benefit 2 · Confidence: sure · Status: open

**Problem.** Distribution is the AppImage, DMGs and zip only. There are no `pip install voxelmill` wheels.

**Fix.** cibuildwheel for Linux, macOS and Windows (with TBB via VM-011). Flatpak is optional.

**Where.** `pyproject.toml`, `docs/packaging.md`

## Feature backlog

Open items from the Lychee/CHITUBOX gap analysis and the plan2 investigation, which were reconciled against
the code on 2026-09-23. Items marked `open (hardware)` cannot close without physical prints.

| ID | Item | Old scores | Status | Note |
| --- | --- | --- | --- | --- |
| N12 | Resolve GOO mirroring against both references | Diff 16 · Imp 78 | open (hardware) | Measured against both references, and the result was inconclusive. It ships as a per-export warning. A test print with an asymmetric glyph settles it. |
| B3 | Printer database beyond the Mars 5 Ultra | Diff 16 · Imp 62 | open | This is a data change. Add only machines with verified specs. |
| B5 | Print-time estimation: physical calibration | Diff 26 · Imp 76 | open (hardware) | The schedule math is shipped. It needs E7 semantics and measured prints. |
| D4 | Raft adhesion / removal-force calibration | Diff 24 · Imp 48 | open (hardware) | Eight base strategies shipped. Adhesion is unquantified. |
| G2 | Fuzzy, mode-aware settings search | Diff 20 · Imp 56 | partial | The current search is a substring filter on the JSON box. |
| A7 | GUI profile manager: dirty-state save/discard | Diff 30 · Imp 62 | partial | `ProfileLibraryDialog` exists. Confirm the remaining scope (dirty tracking, discard prompts). |
| E7 | TSMC: define, validate and document all 18 motion fields | Diff 30 · Imp 66 | partial | The retract-sum validation shipped. Units and semantics are still unverified on hardware. |
| A8 | Presets embedded in profiles | Diff 14 · Imp 44 | partial | Support and process presets shipped as standalone files. |
| A4 | Profile inheritance with delta storage | Diff 30 · Imp 55 | open | Not started. Design it inside VM-041. |
| A5 | Profile compatibility conditions | Diff 26 · Imp 48 | open | Not started. |
| B2 | CTB v4/v5 reader | Diff 34 · Imp 55 | deferred (decision) | v3 shipped. v4/v5 are rejected by current decision. |
| B8 | 3MF / OBJ / PLY import | Diff 30 · Imp 46 | open | Use the `importers.py` interface. Needs an MIT-compatible parser (stdlib zip+xml for 3MF). |
| C10 | Orientation weight calibration + real per-candidate support volume | Diff 34 · Imp 58 | partial | The ranking UI shipped (2026-09-08). The weights are uncalibrated guesses. |
| F6 | Disk-backed tiled layer records | Diff 30 · Imp 46 | open | Only if VM-019 shows memory pressure. |
| G12 | Layer viewer: pixel inspection, A/B layer diff | Diff 26 · Imp 48 | partial | The issue strip and overlays shipped. Confirm the remainder. |
| H4 | SDCP upload and print-control acceptance | Diff 30 · Imp 50 | open (hardware) | Discovery, status, telemetry, history and time-lapse are verified. Upload, print and motion commands have never been sent. |
| I1 | Persist and replay analysis artifacts | Diff 26 · Imp 52 | open | Skip the re-slice on threshold-only edits. |
| G8 | Keyboard shortcut editor (theme shipped) | Diff 22 · Imp 34 | partial |  |
| I3 | Print-time auto-calibration from measured prints | Diff 22 · Imp 36 | open (hardware) | Needs B5 data. |
| C8 | Cap non-planar open cuts | Diff 40 · Imp 72 | open | `voxelmill cap` handles near-planar loops only and refuses the rest. |
| G3 | Typed settings pages replace the raw JSON box | Diff 40 · Imp 70 | partial | The descriptor table and tooltips (G4) shipped, but `settings_json` is still the catch-all editor. Generate the remaining pages from VM-040. |
| E2 | Per-Z-band / per-object slice overrides | Diff 34 · Imp 44 | open | Needs A10. |
| E3 | Cross-sectional-area-driven exposure | Diff 30 · Imp 30 | open | Build the mechanism with the policy off by default. |
| E6 | LED uniformity mask compensation | Diff 34 · Imp 34 | open | Needs a measured uniformity map. |
| F5 | SIMD in the rasterizer inner loop | Diff 26 · Imp 32 | open | Deprioritized, because the rasterizer is memory-bound. See VM-027. |
| A10 | Per-Z-band overrides (per-object support overrides shipped) | Diff 42 · Imp 50 | partial | Depends on E2. |
| C5 | Suction-cup / peel force calibration | Diff 44 · Imp 58 | open (hardware) | The advisory shipped. Force and tilt calibration need prints. |
| F4 | Incremental re-slice after a local edit | Diff 40 · Imp 54 | open | Pairs with VM-015 and I1. |
| G13 | Direct-manipulation gizmos for supports, holes and cut planes | Diff 40 · Imp 54 | partial | The part transform gizmo shipped. Supports, holes and cut planes remain. |
| F7 | GPU orientation search (CPU fallback mandatory) | Diff 44 · Imp 34 | open | The one GPU workload the research supports. |
| B1 | Encrypted CTB writer | Diff 62 · Imp 70 | deferred (decision) | Unencrypted v3 shipped. Encrypted variants are rejected by current decision. |
| A9 | Import CHITUBOX / Lychee profiles | Diff 34 · Imp 20 | open | Deliberately low priority. |
| C11 | Text / serial embossing | Diff 30 · Imp 26 | open |  |
| D1 | Joint support type | Diff 52 · Imp 55 | partial | Branch, tree, contour, face and boundary are implemented. Joint is not. |
| D6 | Support mechanics calibration; promote anchor-load warn→fail | Diff 55 · Imp 58 | open (hardware) | Deferred to the hardware campaign. |

## Appendix A — closed, retired or verified during consolidation

Reconciled against the code on 2026-09-23. The old `nextsteps.md` still listed these as open:

- **Shipped** (subcommand or setting exists): B4 greyscale AA, B7 `convert`, C1 multi-part plates
  (extra models, arrange, object panel), C3 infill lattices (except the hex bug, VM-001), C4 drain/vent
  holes, C6 `boolean`, C7 `trim`/`cap`, C9 `thickness`, D2, D3, D5, D7–D9, E4 XY shrink/tolerance, E5,
  E8 `calibrate`, G1 visibility tiers, G4, G5 object panel, G6 History menu, G7 notifications, G9 wizard,
  G10 autosave/recovery, G11 dockable panels with persisted layout, H1, H2 post-slice hook, H3
  `schemas/voxelmill-report.schema.json`, H6, I2, I4 `report-html`, F10 benchmark harness, `--timing`,
  N1–N11 and N13–N20 (N19 superseded).
- **Retired by measurement** (see `docs/performance.md`): F1 layer-parallel TBB, F2 as "end-to-end RLE"
  (a narrower, validation-scoped version shipped), merging the island-guard scan with the reslice.
- **Not recommended**: F8 GPU rasterization and F9 GPU SDF repair. No evidence says they beat the CPU
  path.
- **Cancelled**: the `libvoxelmill_core` extraction, the standalone C CLI, and the dedicated CUDA kernels
  beyond morphology. The old todo item "update README/architecture for the core/binary split; tag v0.2.0"
  lapsed with them.
- **Deliberately not pursued**: automatic nesting, alignment keys and dowels, sculpting, adaptive layer height,
  multi-material, cloud profile libraries, per-pixel editing of sliced output, AI model generation.

## Appendix B — where the retired documents went

| Retired | Content | Now |
| --- | --- | --- |
| `plan.md` | Stage gates, original interfaces and defaults, development journal to 2026-09-07 | Superseded. Contract details live in `docs/`. History: `git show 92ea91a:plan.md`, `docs/implementation-history-2026-09-09.md` |
| `plan2.md` | Tiered plan and the float-valve investigation | Open items are above. CHITUBOX reference → `docs/support-presets.md`. Voxel-union rationale → `docs/algorithms.md`. Evidence stays in `reports/plan2/` |
| `nextsteps.md` | Competitor gap analysis and scored roadmap | Open items → Feature backlog. Research → `docs/competitor-research.md`. Performance research → `docs/performance.md` |
| `platforms.md` | OS matrix, license notes, portability checklist | Moved to `docs/platforms.md`. Open items → VM-081…VM-085, VM-065 |
| `todo.md` (trimmed, kept) | Perf ledger and restart notes | Ledger → `docs/performance.md`. Open items → VM-010, VM-014, VM-018, VM-020, VM-024, VM-025. The file stays as the short in-flight ledger |
