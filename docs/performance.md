# Performance record

Measured performance history, profiling method, and the optimization ideas that
were **tried and refuted**. This is reference material, not a task list: open
performance work lives in [ISSUES.md](../ISSUES.md). Read this before you start any
optimization, so you don't repeat an experiment that was already measured.

The canonical benchmark is:

```sh
voxelmill prepare fixtures/shapes/overhang_bracket.stl --max-passes 1 --allow-unresolved
```

It converges on the first island pass, so it exercises neither the island-guard
retry loop, hollowing, nor support KD-tree reuse. Benchmark those on a shape that
actually retries or hollows before you claim a win there.

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
| `78cb860` re-measured 2026-09-23 (wall, best of 2) | 3.15 s | — | Same commit as the row above, timed as process wall time on today's machine state; use this as the comparable baseline |
| + default grounded bracing (`ad1186c`, 0.5.3) | 4.25 s | 477 MB | **+35 %, bisected.** 186 → 394 support edges and +21 % triangles, but island_guard +62 %. With `support.auto_bracing=false` HEAD runs 3.23 s. See ISSUES.md VM-029 |

Record a new row after every performance change so the curve stays visible.

## Slicing (2026-09-23)

`slice` of the benchmark sphere at 9K (`voxelmill slice small.stl`), wall seconds:

| Configuration | Before | After | What changed |
| --- | --- | --- | --- |
| default | 24.6 | 1.3 | layers encoded and verified from the part's crop, not the 36.8 Mpx panel |
| `image_mirror_x` | 37.7 | 1.6 | mirroring moves the crop's offset instead of copying a flipped frame |
| elephant-foot 0.1 mm | 26.6 | 3.5 | same |
| `antialias_levels=4` | 151 | 13.2 | box average via strided integer adds; tolerant verify in run space |

Written layer bytes (`layer_blob_sha256`) are identical before and after in all six measured configurations. The remaining AA time is the 16× supersampled raster itself. A native box-average kernel was tried and measured no faster than NumPy (4.9 against 4.7 ms per 15.8 Mpx layer), because the pass is bound by memory reads.

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

**More threads do not pay, and this retires layer-parallel `tbb::parallel_for`.** Parallel speedup
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


## Standing findings (do not re-derive)


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
- `_native.WorkerLimit` is a worker *ceiling* and creates no work of its own.
  Only `native/edt.cpp` runs a parallel loop (`parallel_n` in
  `native/parallel.hpp`); the other native files are still serial.
- Release builds did not find TBB, so until 2026-09-23 the shipped EDT ran on one
  core. `parallel.hpp` now falls back to `std::thread`. On a 160×300×300
  volume without TBB, that took the EDT from 0.81 s at one worker to 0.18 s at
  eight, with output bit-identical to scipy at every worker count.
  `_native.HAS_TBB` says which build is loaded.

- **Per-layer validation cost follows the crop's area, not the triangle count.** The crop is the whole assembly's bounding box, so one outlying brace or foot widens every layer. Default bracing widened the bracket's crop by 39 % and cost 35–46 % in each dense pass (VM-029). When a change moves the headline, compare `reopened.grid` before blaming the geometry code.

## Retired ideas (measured, do not redo)

- **Layer-parallel `tbb::parallel_for` over validation.** The pool is limited by
  bandwidth, not by thread count. The worker sweep is flat past 8 (22.61 / 19.06
  / 18.38 / 19.44 / 19.60 / 19.33 s at 4 / 6 / 8 / 12 / 16 / 24 workers), and a third
  of pool thread-time already sits idle. Revisit this only if per-layer data
  becomes small enough to fit in cache.
- **Trimming `block_any` / `np.pad` allocation.** It costs 0.4–0.7 s in total, under
  1 % of the run, and the RLE path supersedes it.
- **Optimizing the `VoidForest` union-find.** It is 0.5 % of `merge` and 0.3 % of
  the run.
- **Reshaping the dense NumPy dedup passes** (int32 keys, 2-D scatter, bincount
  dedup). Each measured slower. `native/runs.cpp` `run_pairs` replaced the whole
  path.
- **End-to-end RLE through `Rasterizer`, union and GOO encode.** That covers most of
  the files for about a tenth of the win (~0.5 s of a ~7 s prize), and it changes the
  `Layer` contract at 8 call sites. The shipped RLE work is scoped to
  `validation.py` internals.
- **Merging the island-guard scan with the post-export reslice.** They analyze
  different data (the in-memory grouped OR versus the reopened STL under the
  nonzero rule), and they ask for different work (`track_voids`). Make each call
  cheaper instead. The third `analyze_layers` call, gated on
  `repair.support_void_policy != 'fail'`, shares its data source with the island
  guard and is the plausible sharing candidate.
- **A standalone C CLI or a `libvoxelmill_core` extraction.** Python startup is
  about 0.3 s per run, and the hot kernels are already C++.
- **A cProfile line number is not a diagnosis.** Three sort-free rewrites of the
  top `np.unique` line measured 4.40 s, 7.94 s and 20.80 s on the same data.
  Benchmark the candidate, not the theory.

## Research: where the speed comes from

Compiled 2026-09-07 from competitor and open-source slicer research (see
[competitor-research.md](competitor-research.md) for sources and caveats).

### The headline finding, and it is not what was expected

**GPU acceleration is not the big win here. CPU architecture is.**

The evidence, in order of weight:

1. **No production slicer uses the GPU for slicing math.** CHITUBOX lets you
   pick Direct3D/OpenGL/Metal — for the *viewport*. PrusaSlicer's SLA
   rasterizer is the AGG software scanline rasterizer, CPU-only. Lychee is
   closed but community reports are consistently CPU/RAM-bound.
2. **Every GPU slicer found is a research prototype**, all using the same
   3-pass OpenGL stencil-buffer trick (Matt Keeter's, Formlabs' WebGL one,
   brettlajzer's, a PySLM prototype). The best documented number — 5 M
   triangles into ~4,000 layers in ~3 minutes on a GTX 1080 — is measured
   against unspecified "previous software," not a tuned CPU baseline.
3. **The fastest actively maintained open-source MSLA slicer uses no GPU at
   all.** `mslicer` (Rust, 2026) claims 20–120× over competing slicers from
   three CPU-side choices: multithreading, spatial acceleration structures, and
   **slicing directly into RLE-compressed output** instead of materializing
   dense bitmaps.
4. `OpenSLAice` (2026) is CPU-only and gets its wins from adaptive slicing —
   doing less work, not doing the same work faster.

So: **parallelize, then restructure the representation, then prune, and only
then consider offloading.** The one workload where the GPU is clearly the right
answer is orientation search (F7), because it is embarrassingly parallel across
candidates and multiplies a per-candidate cost by tens of trials.

CUDA is permitted here with a mandatory CPU fallback, which is the right policy —
but note that on this machine (i9-13900HX, 24 cores / 32 threads, AVX2 + VNNI,
62 GB RAM) the CPU items below will likely deliver more than the GPU items, for
less risk, and will benefit every user rather than only NVIDIA ones.
