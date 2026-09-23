# Architecture

## Overview

VoxelMill is a Linux single-part resin 3D-print preparation tool that transforms STL meshes into evidence-driven sliced output. Python orchestrates the pipeline (CLI and optional PySide6+VTK GUI), calling pybind11-bound C++ in `voxelmill._native` for expensive geometry and raster operations. A standalone C CLI was measured and cancelled: it would duplicate argument parsing, settings resolution and report contracts without shrinking the remaining Python surface. The primary product is a JSON-structured validation report documenting placement, support routing, and resliced closure. Every exported file is independently re-opened and validated at printer pitch to prove the written geometry rather than the in-memory solid.

## File Layout

| Path | Purpose |
|------|---------|
| `src/voxelmill/__init__.py` | Package entry point; exposes public API |
| `src/voxelmill/cli.py` | Command-line interface: `voxelmill prepare`, `inspect`, `validate`, `slice`, `goo-info`, `profile`, `resin`, `gui`, `batch`, `completion`, `manpage` |
| `src/voxelmill/shellhelp.py` | Shell completion (bash, zsh, fish) and man page text, generated from the live `build_parser()`; nothing here is checked in |
| `src/voxelmill/config.py` | Profile loading and settings resolution; built-in defaults, printer/resin profiles, process overrides |
| `src/voxelmill/profiles.py` | Profile library: layered search-path discovery, path-vs-identifier resolution for `--printer`/`--resin`, provenance, settings diff, and the TOML writer behind `profile save`/`resin bind`. See [profiles.md](profiles.md) |
| `src/voxelmill/topology.py` | CPU topology detection: performance/efficiency core split, SMT siblings and the affinity mask, per OS (Linux sysfs, macOS perflevels, Windows EfficiencyClass) with a uniform fallback. Feeds `resources.execution_limits` and the derived worker count |
| `src/voxelmill/contracts.py` | Core data types: `VoxelMillError`, `CancellationToken`, `ValidationReport`, `Placement`, `SupportGraph`, `Diagnostic` |
| `src/voxelmill/mesh.py` | STL I/O: binary and ASCII parsing with memory mapping; full-resolution mesh inspection via native module |
| `src/voxelmill/geometry.py` | Rigid placement, orientation search, manifold construction; convex hull and coarse occupancy assessment |
| `src/voxelmill/supports.py` | Support detection and routing: column-field analysis, downward-face sampling, island birth detection, pillar routing |
| `src/voxelmill/island_guard.py` | `route_without_islands`: the shared route → assemble → scan → add-contacts-under-islands loop used by both `pipeline.prepare` and the editor's Compute attachments, so the two cannot settle for different plates. See [algorithms.md](algorithms.md#island-correction-passes) |
| `src/voxelmill/overhangs.py` | `unsupported_overhangs`: matches downward-face samples against routed contacts by `support.spacing_mm`, an advisory (warning-only) check. See [algorithms.md](algorithms.md#unsupported-overhangs) |
| `src/voxelmill/support_example.py` | Small fixed-contact fixture built by the production router for CLI illustration export and GUI parameter previews; no print validation |
| `src/voxelmill/raster.py` | Pixel-center scan conversion via native module; layer streaming with even/odd closure checks |
| `src/voxelmill/validation.py` | Layer connectivity, empty-space and drainage analysis; void tracking across the build |
| `src/voxelmill/repair.py` | Occupancy voxel repair: grid selection, well-composedness, surface extraction, deviation verification |
| `src/voxelmill/assembly.py` | Shared CLI/GUI model policy, exact/raster assembly, grouped layer stream and reopened-STL parity |
| `src/voxelmill/pipeline.py` | End-to-end orchestration: placement → ingestion/repair → exact or raster union → export → reslice → validation |
| `src/voxelmill/project.py` | Project file I/O and metadata persistence |
| `src/voxelmill/printer.py` | Printer discovery, connection, status/attributes telemetry, print history and time-lapse downloads, camera URL access, upload and printer state machine |
| `src/voxelmill/goo.py` | GOO format layer encoding/decoding and file operations |
| `src/voxelmill/arrange.py` | Deterministic bottom-left shelf packer: `arrange_footprints(footprints, envelope, *, clearance_mm=0.0, fixed=())` returns one plate-center offset per footprint, refusing rather than returning an overlapping layout when something does not fit. Pure XY geometry; no mesh handling |
| `src/voxelmill/gui/` | Optional GUI: window, viewport, layer view, document state, job queue |
| `src/voxelmill/gui/printer_monitor.py` | Independent read-only SDCP monitor: status/attributes telemetry, RTSP camera playback, print history, and time-lapse downloads |
| `src/voxelmill/gui/profiles.py` | `ProfileLibraryDialog`: the editor's view onto the profile library, calling the same `voxelmill.profiles` functions as `voxelmill profile`/`voxelmill resin` |
| `src/voxelmill/gui/editors.py` | Dedicated printer, resin/process and support draft editors; shared validation and portable saves, undoable Apply, cancellable VTK support examples |
| `src/voxelmill/gui/objects.py` | Object panel: the plate's part list and per-part move/rotate/scale/mirror controls with multi-select and arrow-key nudge (`ObjectPanel`), per-part attachment overrides (`AttachmentSettings`), and the 3D-view tool strip for Select/Add point/Remove point/Paint enforced/Paint blocked (`ToolSelector`) |
| `src/voxelmill/gui/gizmo.py` | `TransformGizmo`: the FreeCAD-style translate/rotate manipulator (axis arrows, rotation rings) built from plain VTK actors, replacing `vtkBoxWidget`; pick/drag math is free functions testable without a render window |
| `src/voxelmill/gui/appprefs.py` | Editor-only preferences that change nothing about the output — the rotation snap increment, the arrow-key translate step, motion mode (relative/absolute), and the remembered window geometry/dock layout — persisted to `~/.config/voxelmill/editor.json`, never to a printer profile or a `.voxmil` project |
| `native/mesh.cpp` | Exact-coordinate topology: triangle welding, edge manifold inspection |
| `native/raster.cpp` | Pixel-center even/odd and nonzero scan conversion with winding-rule tolerance |
| `native/runs.cpp` | Row-RLE occupancy kernels (`extract_runs`, CCL, overlap, border) used inside validation |
| `native/intersections.cpp` | Exact-predicate self-intersection detection over a BVH broad phase |
| `native/voxel.cpp` | Occupancy grid, well-composedness repair, surface extraction |
| `native/distance.cpp` | Adaptive triangle covering and nearest-surface distance verification |
| `native/goo.cpp` | GOO v3 layer blob encoding and decoding with checksum |
| `native/module.cpp` | pybind11 module entry point; registers all bindings |
| `native/parallel.hpp` | `parallel_n` index loops (oneTBB when found, `std::thread` otherwise) and the `WorkerLimit` ceiling |

## Data Flow

The `voxelmill prepare` command follows this sequence:

1. **Load**: `mesh.open_stl()` opens the source, detects ASCII vs. binary, computes bounds and SHA256.
2. **Place**: `geometry.placement_for_triangles()` or `geometry.auto_placement()` orients and positions the model, checking envelope fit and (for auto mode) ranking finalists on a coarse occupancy grid.
3. **Ingestion**: `assembly.prepare_model()` tries strict solid conversion under conservative policy, uses authored triangles directly under `none`, or performs explicit aggressive voxel repair. Geometric rejection falls back to raster assembly unless `assembly.union="exact"`. Cavities can be filled only when a solid exists.
4. **Support**: `supports.plan_supports()` builds a column-field raster, samples downward faces, routes contacts to the plate or model via `supports.route_contacts()`, generating a support graph and optional raft.
5. **Union**: `assembly.assemble()` tries a Manifold boolean union, or retains independent model and generated-support/raft groups for occupancy OR. GUI services call the same implementation.
6. **Stage**: `mesh.write_stl()` streams exact triangles or a sequence of soup groups to a **scratch** path, never to the caller's destination. Nothing the user already has is touched yet.
7. **Reslice**: On the raster path, `RasterParity` compares fresh grouped occupancy with the reopened carrier on its exact grid. `pipeline._reslice()` reopens that staged STL and validates it independently — `validation.analyze_layers()` for layer connectivity and island births, `validation.analyze_drainage()` for void escape — so the checks run against the written bytes rather than the in-memory solid.
8. **Correction**: When automatic contacts are enabled, `island_guard.route_without_islands` (shared with the editor's Compute attachments) repeats steps 4 to 7: route, assemble, scan for islands, and if any are found, add contacts under them and route again — up to `max_passes` (`support.max_island_passes`, default 5) and stopping early on success or on no progress. See [algorithms.md](algorithms.md#island-correction-passes).
9. **Publish**: Only a passing validation, or an explicit `--allow-unresolved`, copies the staged file to the destination with `atomic_copy`. Otherwise the geometry is withheld and `export.written` is `false` with the failed check names. **A failing run never overwrites or deletes an existing output file.**
10. **Report**: The evidence JSON is written either way. The report is the product; the STL is the by-product. `prepare` always records a per-stage wall-time map in `report['timing']`; CLI `--timing` prints that table to stderr. Equivalence treats `timing` as volatile (alongside `seconds` and similar run-cost fields), so golden diffs stay geometry-focused.

## Python Modules

### cli.py
Command-line argument parsing and entry points. One function per subcommand.

| Name | Parameters | Returns | Purpose |
|------|-----------|---------|---------|
| `cmd_prepare(args)` | `args` from argparse | Exit code | Main entry: place, repair, support, export, validate |
| `cmd_measure(args)` | `args` | Exit code | Sizes before/after a proposed pose, scale and mirror; writes nothing |
| `cmd_inspect(args)` | `args` | Exit code | Full-resolution mesh topology and self-intersection inspection |
| `cmd_validate(args)` | `args` | Exit code | Reslice and validate a prepared STL without re-exporting |
| `cmd_slice(args)` | `args` | Exit code | Reslice and emit layer masks as PNG or GOO |
| `cmd_goo_info(args)` | `args` | Exit code | Decode and summarize a GOO file |
| `cmd_profile(args)` | `args` | Exit code | Show resolved printer and resin profiles |
| `cmd_gui(args)` | `args` | Exit code | Launch the PySide6 viewport GUI |
| `cmd_batch(args)` | `args` | Exit code | Run one operation over many inputs (`BATCH_OUTPUTS`), one report and geometry output per item plus a manifest |
| `cmd_completion(args)` | `args` | Exit code | Print a shell completion script from `build_parser()` via `shellhelp.completion()` |
| `cmd_manpage(args)` | `args` | Exit code | Print a roff man page from `build_parser()` via `shellhelp.manpage()` |
| `build_parser()` | None | `ArgumentParser` | Construct the full argument parser |
| `main(argv=None)` | Optional command-line arguments | Exit code | Entry point; parses args and dispatches |

### config.py
Profile loading, settings merging, and validation.

| Name | Parameters | Returns | Purpose |
|------|-----------|---------|---------|
| `resolve_settings(printer_path=None, resin_path=None, overrides=None)` | Paths to printer/resin TOML files, dict of overrides | `dict` | Merge defaults, profiles, and CLI overrides into final settings |
| `validate_settings(settings)` | `dict` settings | None or raises | Check all required keys, bounds, and consistency |
| `layer_exposure(settings, index)` | Settings, layer index | `float` | Interpolate exposure time for a given layer (bottom vs. normal) |

### contracts.py
Core data types and exceptions.

| Name | Purpose |
|------|---------|
| `VoxelMillError(code, message, details=None)` | Structured exception with code, message, and optional detail dict |
| `CancellationToken()` | Thread-safe cancellation flag; checked by long operations |
| `ResourceBudget(memory_gib, workers, scratch_dir)` | Validates and enforces memory and worker limits |
| `MeshAsset(path, sha256, triangle_count, bounds, ...)` | Input file metadata |
| `Placement(matrix, rotation_deg, center_offset_mm, model_lift_mm, bounds, search, scale=[1,1,1], mirror=[False,False,False])` | Affine placement plus search metadata. `scale`/`mirror` are the resolved per-axis factors and flips; defaulting to the identity keeps every pre-existing caller and archive resolving unchanged |
| `ValidationReport(diagnostics, checks, metrics, ...)` | Evidence summary; `.passed` property checks all checks are run and none failed |
| `SupportNode(id, position_mm, kind)` | Support graph vertex (foot, junction, contact, elbow, model_anchor, brace_junction) |
| `SupportEdge(start, end, radius_mm, kind)` | Support graph edge (pillar, branched, tip, model_anchor, tree_trunk, tree_branch, brace, brace_foot) |
| `SupportGraph(nodes, edges, overrides, diagnostics)` | Support tree and rejection list |
| `Diagnostic(code, message, severity, layer, position_mm, details)` | Single evidence finding |

### mesh.py
STL loading, hashing, and writing; full-resolution mesh queries.

| Name | Parameters | Returns | Purpose |
|------|-----------|---------|---------|
| `open_stl(path, budget=None, cancel=None, progress=no_progress)` | Path, optional budget/cancel/progress | `STLMesh` context manager | Open an STL file (auto-detects ASCII or binary; recovers count mismatch, trailing garbage, truncation with warnings) |
| `STLMesh.__init__(path, budget, cancel, progress)` | (same) | Instance | Constructor; validates file, memory-maps or converts ASCII to binary |
| `STLMesh.triangles` | — | `ndarray` (n,3,3) float32 | Read-only memory-mapped triangle array |
| `STLMesh.asset` | — | `MeshAsset` | File metadata (path, SHA256, count, bounds) |
| `STLMesh.diagnostics` | — | `list[Diagnostic]` | Warning-severity load recoveries (`stl_count_mismatch`, `stl_trailing_garbage`, `stl_truncated`) |
| `inspect_mesh(mesh, budget=None, cancel=None, progress=no_progress, self_intersections=True)` | Mesh or path, optional budget/cancel/progress, bool | `dict` | Topology inspection: unique vertices, boundaries, manifold edges, component count, winding, intersections |
| `weld_mesh(triangles, budget=None, cancel=None, progress=no_progress)` | Triangles array, optional budget/cancel/progress | `(vertices, faces)` tuple | Exact-coordinate vertex deduplication; raises if invalid |
| `write_stl(path, triangles, cancel=None, progress=no_progress)` | Output path, triangles array | None | Atomic binary STL export; normals computed; raises on nonfinite |

### geometry.py
Full-resolution placement search, orientation ranking, and manifold construction.

| Name | Parameters | Returns | Purpose |
|------|-----------|---------|---------|
| `triangle_bounds(triangles, matrix=None, cancel=None)` | Triangles, optional 4×4 matrix, cancel | `(2, 3)` array | Bounding box of triangles, optionally transformed |
| `iter_transformed_triangles(triangles, matrix=None, chunk_size=CHUNK_SIZE, cancel=None)` | Triangles, optional 4×4 matrix, chunk size | Generator of float64 triangle chunks | Apply 4×4 affine transform in bounded chunks |
| `rotation_matrix(rotation_deg)` | Tuple of (x, y, z) rotation degrees | `(3, 3)` array | Compute Rz @ Ry @ Rx rotation matrix |
| `envelope_fits(bounds, settings, reserve_mm=0.0)` | Bounds (2,3) array, settings dict, reserve | `bool` | Check if bounds fit within printer build envelope minus clearance |
| `placement_for_triangles(triangles, settings, rotation_deg=(0,0,0), center_offset=(0,0), lift_mm=5.0, cancel=None, scale=(1,1,1), mirror=(False,False,False))` | Triangles, settings, rotation, offset, lift, cancel, scale, mirror | `Placement` or raises | Fixed rotation, scale and mirror; checks envelope; raises if infeasible |
| `placement_or_overflow(triangles, settings, rotation_deg=(0,0,0), center_offset=(0,0), lift_mm=5.0, cancel=None, scale=(1,1,1), mirror=(False,False,False))` | Same as `placement_for_triangles` | `(Placement, fits, overflow_mm)` | Non-raising counterpart: always places, whether or not it fits |
| `auto_placement(triangles, settings, center_offset=(0,0), lift_mm=5.0, cancel=None, directions=192, spins=12, refinements=3, assess=True, finalists=5, min_separation_deg=20.0, max_verifications=128, progress=None)` | Triangles, settings, offset, lift, cancel, plus search tuning | `Placement` or raises | Multi-resolution search over 192 directions × 12 spins, refined 3×, re-ranked on coarse grid. Does not accept scale/mirror; the search always ranks the unscaled, unmirrored part |
| `scale_matrix(scale=(1,1,1), mirror=(False,False,False))` | Per-axis scale, per-axis mirror flags | `(matrix, factors, flips)` | Linear part of the transform, mirror folded in as a negative factor; rejects a nonpositive or out-of-`[MIN_SCALE, MAX_SCALE]` factor |
| `scale_note(factors, flips)` | Resolved scale factors, mirror flags | `str` or `None` | One-line hazard summary for reports/banners; `None` at the identity |
| `matrix_to_euler_deg(rotation)` | `(3, 3)` rotation matrix | List of 3 degrees | Extrinsic X, Y, Z angles for matrix built as Rz @ Ry @ Rx |
| `_rank_finalists(triangles, accepted, settings, assess, cancel, progress)` | Triangles, feasible placements, settings, bool, cancel, progress | `Placement` | Order finalists by void terms and accessibility from coarse occupancy grid; returns best |
| `_attach_assessment(triangles, placement, settings, cancel, progress)` | Triangles, placement, settings, cancel, progress | `dict` or None | Perform occupancy-grid assessment of one placement; internal |
| `mesh_to_manifold(triangles, budget=None, repair=None, cancel=None)` | Triangles, optional budget/repair dict/cancel | `(solid, report)` tuple | Exact vertex dedup only; rejects invalid topology; raises on zero-area or self-intersections |
| `manifold_triangles(solid)` | manifold3d solid object | `ndarray` (n,3,3) float64 | Extract triangle mesh from Manifold solid |
| `cylinder_between(start, end, radius_start, radius_end=None, segments=24)` | Start and end [x,y,z], radii, segments | manifold3d solid | Create a tapered or cylindrical support pillar |
| `raft_from_feet(feet_xy, pillar_radius=0.6, expansion_mm=2.0, thickness_mm=1.0, bevel_mm=0.25)` | Feet [(x,y), …], radius, expansion, thickness, bevel | manifold3d solid | Create a connected convex raft with top-edge bevel |
| `fill_enclosed_cavities(solid)` | manifold3d solid | `(solid, report)` tuple | Fill inward-closed shells in an already-valid solid |

### supports.py
Support detection, routing, and geometry.

| Name | Parameters | Returns | Purpose |
|------|-----------|---------|---------|
| `ColumnField` | — | Class | CSR-style per-column run occupancy from raster analysis |
| `ColumnField.index_of(x, y)` | World X, Y coords | Column index or None | Map world coordinates to grid column |
| `ColumnField.layer_of(z)` | World Z | Layer index | Map Z to layer index |
| `ColumnField.reachable(column, layer_index)` | Column, layer index | `bool` | True if gap below layer drains to exterior |
| `ColumnField.blocked(column, lo_index, hi_index)` | Column, half-open layer range | `bool` | True if any run overlaps the span |
| `ColumnField.top_below(column, index)` | Column, layer index | Layer index or None | Highest run top at or below index; None if free to plate |
| `build_column_field(triangles, bounds, settings, pitch_mm=None, budget=None, cancel=None, progress=no_progress)` | Triangles, bounds, settings, optional pitch, budget/cancel/progress | `ColumnField` | Raster the placed model once; extract per-column occupancy and islands |
| `downward_contacts(triangles, settings, cancel=None, progress=no_progress, max_lattice_faces=200000)` | Triangles, settings, optional cancel/progress/lattice limit | `(points, area)` tuple | Sample downward faces; optional contour perimeter and open-boundary edges |
| `select_contacts(triangles, field, settings, cancel=None, progress=no_progress, extra_contacts=(), removed_contacts=(), removal_radius_mm=None, object_groups=None)` | Triangles, column field, settings, optional lists/groups, cancel/progress | `(contacts, metrics)` tuple | Choose contact points from downward faces and raster islands; thin by spacing; apply removals; optional per-object overlays |
| `contact_coverage(samples, contacts, settings, downward_area_mm2)` | Sample points, contact points, settings, total area | `dict` | Measure distance from every sampled point to nearest contact; check load limits |
| `route_contacts(contacts, field, settings, cancel=None, branch_attempts=8, max_diagnostics=256)` | Contacts array, column field, settings, optional params | `(SupportPlan, raft_or_None)` tuple | Route each contact to plate, angled branch, or model anchor; build solids and graph |
| `plan_supports(triangles, bounds, settings, field=None, budget=None, cancel=None, progress=no_progress, extra_contacts=(), removed_contacts=(), branch_attempts=8, max_diagnostics=256)` | Triangles, bounds, settings, optional field/budget/cancel/progress/lists/params | `(SupportPlan, raft_or_None)` tuple | Convenience: builds field if needed, then calls select and route |
| `apply_support_validation(report, plan, settings)` | ValidationReport, SupportPlan, settings | ValidationReport | Merge support routing evidence into validation checks |
| `SupportPlan` | — | Class | Result: graph, feet, solids, diagnostics, metrics |

### raster.py
Pixel-center scan conversion.

| Name | Parameters | Returns | Purpose |
|------|-----------|---------|---------|
| `RasterGrid(width, height, x0, y0, dx, dy, column_offset=0, row_offset=0)` | Grid dimensions and origin, cell size, optional crop offset | Instance | Frozen dataclass; pixel and world coordinate mapping |
| `RasterGrid.xy(row, column)` | Row, column integers | `[x, y]` list | Center of a pixel in world coordinates |
| `RasterGrid.for_bounds(bounds, settings, crop=True)` | Bounds (2,3) array, settings, crop bool | `RasterGrid` | Construct grid aligned to printer pixels, optionally cropped to model bounds |
| `MeshLayerStream(triangles, bounds, settings, budget=None, cancel=None, progress=no_progress, crop=True, rule='nonzero', strict=False)` | Triangles, bounds, settings, optional params | Instance | Construct a layer iterator |
| `MeshLayerStream.__iter__()` | — | Generator of `Layer` objects | Slice the mesh at each layer height; yields (index, z, mask) |
| `MeshLayerStream.diagnostics()` | — | List of `Diagnostic` | Empty unless open contours were found |

### validation.py
Layer connectivity, void analysis, and drainage checking.

Layer i depends only on layer i-1, never on the whole prefix, so `_analyze_layer`
carries almost all the work and runs concurrently across layers; only the
accumulators, the bounded diagnostics and the `VoidForest` union-find stay in
order. Worker count comes from `resources.workers` (0 derives it) and is capped
by `_layer_worker_cap` so the in-flight full-panel buffers fit the memory budget.

Per-layer analysis may take a row-RLE path through `_native.extract_runs` and
the other `runs.cpp` kernels when density clears `RUN_DENSITY_FLOOR` and
`VOXELMILL_NATIVE_RUNS` is not off. That path is internal: `Layer.mask` stays a
dense occupancy panel, and `VoidForest.add` still consumes dense empty-space
labels for callers such as support routing.

| Name | Parameters | Returns | Purpose |
|------|-----------|---------|---------|
| `block_any(mask, factor)` | Boolean mask, decimation factor | Boolean mask | Block-maximum decimate (never stride) to keep features |
| `VoidForest` | — | Class | Union-find over per-layer empty components; tracks volume, exterior reach, birth layer |
| `void_components(occupied)` | Occupancy mask | `(labels, total, counts, outside)` | Label one layer's empty space. Pure, so it runs on a worker thread; the ordered merge is separate |
| `VoidForest.add(mask, index, cancel)` | Empty-space mask, layer index, cancel | `(labels, ids)` tuple | Label and merge in one step; the entry point for callers outside `analyze_layers` |
| `VoidForest.merge(components, index, cancel)` | `void_components` result, layer index, cancel | `(labels, ids)` tuple | Union one layer's components into the forest. Order-dependent: void identity is temporal |
| `VoidForest.finish(min_volume_mm3=0.0)` | Min volume threshold | `dict` | Finalize and return void summary (count, volume, examples, peak trapped) |
| `_analyze_layer(previous, mask, grid, settings, decimate, min_overlap, track_voids, cancel)` | Previous and current occupancy, plus context | evidence tuple | Everything about one layer that does not depend on layer order: both labelings, the overlap bincount, the growth transform. Runs on the worker pool |
| `analyze_layers(layers, grid, settings, cancel=None, budget=None, progress=no_progress, growth_decimation=8, max_examples=128, track_voids=True)` | Layer stream, raster grid, settings, optional params | `ValidationReport` | Check per-layer connectivity (islands, overlaps, growth), and optionally track enclosed voids and transient traps |
| `analyze_drainage(triangles, bounds, settings, budget=None, cancel=None, progress=no_progress, voxels_per_radius=3.0)` | Triangles, bounds, settings, optional params | `dict` | Erosion-based effective-drainage check on a fine analysis grid; reports bottlenecks |
| `drainage_check(result)` | Drainage analysis dict | `'pass'`, `'fail'`, or `'not_run'` | Map drainage result to check state |
| `orientation_assessment(triangles, bounds, settings, pitch_mm=1.0, cancel=None, progress=no_progress)` | Triangles, bounds, settings, coarse pitch, cancel/progress | `dict` | Coarse occupancy assessment: trapped resin, cavities, accessibility, stability (used by auto-placement) |

### repair.py
Occupancy voxel repair pipeline.

| Name | Parameters | Returns | Purpose |
|------|-----------|---------|---------|
| `choose_voxel_size(bounds, settings, budget)` | Bounds, settings, ResourceBudget | `(size, dims, bytes)` tuple | Select largest voxel size within memory budget that meets deviation tolerance |
| `voxel_repair(triangles, bounds, settings, budget=None, cancel=None, progress=no_progress, source_volume_mm3=None)` | Triangles, bounds, settings, optional budget/cancel/progress/source volume | `(solid, report)` tuple | Rasterize, well-compose, extract surface, optionally smooth, verify deviation, return Manifold solid |

### pipeline.py
End-to-end orchestration.

| Name | Parameters | Returns | Purpose |
|------|-----------|---------|---------|
| `prepare(source, settings, rotate=None, center_offset=(0.0, 0.0), lift_mm=5.0, output=None, components=False, budget=None, cancel=None, progress=no_progress, allow_unresolved=False, max_passes=None, drainage=True, track_voids=True, overhang_check=True, project=None, manual_contacts=(), removed_contacts=(), scale=(1.0,1.0,1.0), mirror=(False,False,False), candidates=None, candidate_rank=None, contact_parameters=(), paint=None, extra_models=())` | Source STL path, settings dict, optional placement/output/component flags/budget/cancel/progress/policy flags/correction limit/void tracking/overhang-check flag/project path/manual and removed contacts/scale/mirror/orientation candidates/per-contact parameters/paint/extra models | `dict` | Main entry point; orchestrates place → repair → support → union → export → reslice → validation; returns JSON-serializable evidence report. `max_passes=None` defaults to `support.max_island_passes` (default 5, range 1-10) and drives the shared `island_guard.route_without_islands` loop. Refuses `rotate='auto'` combined with a nonidentity scale/mirror. A saved `--project` records `extra_models`, and `save_project` embeds each added mesh as `source/models/N.stl` |
| `measure_stl(source, settings, rotate=None, center_offset=(0.0, 0.0), lift_mm=5.0, scale=(1.0,1.0,1.0), mirror=(False,False,False), target_mm=None, budget=None, cancel=None, progress=no_progress)` | Source STL path, settings dict, optional pose/scale/mirror, optional target size, budget/cancel/progress | `dict` | Sizes a part has and would have under a proposed pose; writes nothing. Refuses `rotate='auto'`. `target_mm` solves in reverse for the scale factor(s) that reach a wanted size |
| `validate_stl(source, settings, *, budget=None, cancel=None, progress=no_progress, drainage=True, track_voids=True)` | Source STL path, settings dict, optional budget/cancel/progress/drainage/void tracking | `ValidationReport` | Reslice an existing STL and check it, with no placement and no export. Shared by `cli.cmd_validate` and `gui/services.validate_file` so the CLI and the editor cannot answer the same question about the same file differently |
| `inspect_stl(source, settings, *, budget=None, cancel=None, progress=no_progress, self_intersections=True)` | Source STL path, settings dict, optional budget/cancel/progress/self-intersection flag | `dict` | Full-resolution mesh inventory via `mesh.inspect_mesh`. Shared by `cli.cmd_inspect` and `gui/services.inspect_file` for the same reason |
| `atomic_copy(source, destination, cancel=None)` | Source and destination paths, optional cancel | None | Copy via temporary file and atomic rename; safe against partial writes |

### project.py
`.voxmil` project I/O. A project is a **version 2 ZIP** holding `manifest.json`
and optionally `source/original.stl` — not a bespoke binary format. Limits and
the reader's rejection rules are in [configuration.md](configuration.md).

Schema 2 moved paint from plate-coordinate centroids to one record per plate
object, each in that object's own mesh frame, so paint travels with the part
instead of staying where the part used to be. A schema-1 project is **refused,
not converted**: its marks carry no part attribution, so calling them the
primary's would be a guess presented as a fact. `SCHEMA_VERSION` in
`project.py` is the single source of the number; both the pipeline and
`Document.manifest` import it.

| Name | Parameters | Returns | Purpose |
|------|-----------|---------|---------|
| `save_project(path, manifest, source_path=None)` | Destination, JSON-able manifest dict, optional source STL to embed | `dict` | Write the archive atomically. Embeds the source bytes unmodified, hashes in 1 MiB chunks, and accepts dataclasses and `Path` values. Returns the normalized manifest. |
| `load_project(path, extract_dir=None)` | Archive path, optional extraction directory | `dict` | Verify the archive and embedded hash on every load. Without `extract_dir` it returns state without following external source paths; with it, writes a hash-named STL atomically and adds `source.extracted_path`. Symlinked directories and destinations are rejected. |

Reopening a project returns its stored validation **as history**. It does not
establish that the geometry still passes; revalidate before exporting.

### printer.py
Offline SDCP adapter. No device traffic has occurred; see [calibration.md](calibration.md).

| Name | Parameters | Returns | Purpose |
|------|-----------|---------|---------|
| `discover(*, timeout=1.0, broadcast_address='255.255.255.255', ...)` | Timeout, broadcast address | `list[Discovery]` | Explicit, opt-in LAN broadcast. Never called implicitly by construction or connect. |
| `Discovery` | — | Dataclass | One discovered machine: address, id, attributes. |
| `SDCPPrinterAdapter` | — | Class | The adapter itself; methods below. |
| `SDCPPrinterAdapter.connect(target=None)` | `Discovery`, mapping, address string, or `None` | `dict` | Open the transport and read attributes. Does not discover. |
| `SDCPPrinterAdapter.status(*, refresh=True)` | Refresh flag | `dict` | Machine status. `refresh` forces a new generation; a cached status is not a refresh. |
| `SDCPPrinterAdapter.upload(path, cancel=None, progress=None, ...)` | File path, cancel, progress | `dict` | Upload a file. Completion is not verification: the protocol has no positive MD5-complete response. |
| `SDCPPrinterAdapter.pause()` / `.resume()` / `.cancel()` | — | `None` | Job control, each a separate explicit command. |
| `SDCPPrinterAdapter.ping(*, timeout=None)` | Timeout | `None` | Liveness check. |
| `SDCPPrinterAdapter.close()` | — | `None` | Close the transport. |
| `SDCPPrinterAdapter.connected` / `.attributes` | — | `bool` / `dict\|None` | Read-only properties. |
| `WebSocketTransport`, `LoopbackTransport` | — | Classes | Real and in-process transports. |
| `LoopbackPrinterSimulator` | — | Class | Offline firmware stand-in used by the tests. |
| `create_loopback_adapter(**kwargs)` | — | `(SDCPPrinterAdapter, LoopbackPrinterSimulator)` | Wire an adapter to a simulator with no network. |

### goo.py
GOO v3 writing, reading and verification. Behavior is in [algorithms.md](algorithms.md)
and the format itself in [goo-format.md](goo-format.md).

| Name | Parameters | Returns | Purpose |
|------|-----------|---------|---------|
| `slice_stl(source, output, settings, *, allow_unresolved=False, cancel=None, progress=no_progress, scratch_dir=None, previews=None, print_time_s=0)` | Prepared STL path, output path, resolved settings | `dict` report | The whole stage-4 entry point: validate the source, stream a candidate, then reopen it and compare every decoded LCD pixel and per-layer timing against a freshly sliced source before the final rename. `allow_unresolved` permits only a warned *source* validation; it never bypasses framing, timing or pixel checks. |
| `GooWriter(path, settings, layer_count, *, volume_mm3=0.0, print_time_s=0, previews=None, cancel=None, progress=no_progress)` | Destination, settings, layer count | Context manager | Streams to a scratch path and renames atomically, so a failed write never destroys an existing file. |
| `GooReader(path)` | GOO path | Instance | Bounded reader; every framing rule is checked before any data is trusted. |
| `GooLayer` | — | Dataclass | `index`, `offset`, `values`, `data_length`, `blob_offset`. |
| `GooLayerStream(reader, *, cancel=None, budget=None, progress=no_progress, crop=None)` | GOO reader, optional cancel/budget/progress, optional `(r0, r1, c0, c1)` crop window | Instance | Decoded GOO frames as `Layer` records, unmirrored to plate orientation. `crop=None` builds the grid on the full LCD lattice; a crop window builds a `RasterGrid` over just that window (origin moved, pitch unchanged) and every yielded frame is decoded then sliced to it. |
| `occupied_crop(reader, *, cancel=None, progress=no_progress, margin=1)` | GOO reader, optional cancel/progress, border width in pixels | `(r0, r1, c0, c1)` tuple or `None` | One extra decode pass to find the window every exposed pixel falls inside across every layer, plus a `margin`-pixel border. Returns `None` if the file exposes nothing anywhere. Used by `verify_goo` to crop the analysis window unless `full_panel=True`. |
| `header_from_settings(settings, layer_count, *, volume_mm3=0.0, print_time_s=0, ...)` | Resolved settings, layer count | `dict` | Build the header. Requires all 18 `printer.motion` values; raises `goo_motion` rather than inventing one. |
| `preview_from_heightmap(heights, size)` | Heightmap, size | Image array | Preview thumbnail. |
| `rgb565(image)` / `unpack_rgb565(data, width, height)` | Image / bytes | bytes / image | Preview pixel packing. |

### gui/window.py, gui/viewport.py, gui/gizmo.py, gui/layerview.py, gui/document.py, gui/services.py, gui/jobs.py
Optional PySide6 GUI components: the main window and its menus/docks, the VTK
viewport and translate/rotate gizmo, the layer scrubber with per-issue-code
markers, document state and undo, background jobs, and `services.py`'s
Qt-free wrappers around the same core the CLI calls (including
`run_print_checks` behind the Verification menu and `route_attachments` behind
Parts → Compute attachments).

`viewport.line_actors` is the only way lines are drawn: one actor per
*segment*, each a two-point cell, because under a virtual machine's generic
OpenGL a polydata holding several cells renders only its first and a polyline
of more than three segments renders not at all. It backs both the build volume
and the navigation cube's facet outlines. `gui/helptext.py` is the single table of
per-field explanations that the generated Setup rows and the dedicated
editors both read, looked up through `help_for('section.field')`.

### gui/profiles.py
`ProfileLibraryDialog`, opened from **Configuration → Profile library…** in
`gui/window.py`. Lists every profile `voxelmill.profiles.discover` finds, and
puts `config.resolve_settings`, `profiles.diff_settings`,
`profiles.provenance`, `profiles.save_printer_profile`, and
`profiles.bind_resin_process` behind buttons, so the editor and the CLI
cannot resolve the same printer/resin reference to different settings. See
[profiles.md](profiles.md).

## Native Extension Module: voxelmill._native

All functions are bound via pybind11 in `native/module.cpp` and called from Python. The module provides:

| Symbol | Signature | Returns | Purpose |
|--------|-----------|---------|---------|
| `inspect_mesh(triangles, callback=None)` | `(n,3,3)` float32/float64 array, optional callback | `dict` | Topology: unique_vertices, valid/nonfinite/degenerate counts, boundary/nonmanifold/winding edges, components, bounds, signed_volume_mm3 |
| `weld_mesh(triangles, callback=None)` | Triangles array, optional callback | `(vertices, faces)` tuple | Exact-coordinate deduplication; vertices (m,3) float64, faces (n,3) uint32 |
| `inspect_intersections(triangles, callback=None)` | `(n,3,3)` float32 array, optional callback | `dict` | Self-intersections: count, candidate pairs, examples (list of (i,j) tuples), method ("exact double-double predicates") |
| `Rasterizer(triangles, callback)` | Triangles array, callback function | Instance | Scan-conversion rasterizer for a triangle mesh |
| `Rasterizer.slice(z, width, height, x0, y0, dx, dy, callback=None, rule='nonzero')` | Z height, grid dimensions/origin/pitch, optional callback, rule ('nonzero' or 'evenodd') | `dict` with keys `mask` (uint8 [height,width]), `odd_rows`, `segments`, `active_triangles`, `filled_pixels`, `spans`, `negative_winding_crossings`, `rule`, `crossings` | Scan-convert at height z. `odd_rows` counts rows whose winding never closed, which means the mask has holes; callers must not treat such a mask as solid evidence. |
| `VoxelVolume(nx, ny, nz, x0, y0, z0, dx, dy, dz)` | Grid dimensions and origin, cell size | Instance | Occupancy grid for repair |
| `VoxelVolume.set_slice(index, mask)` | Layer index, uint8 mask array | None | Set occupancy for one Z layer |
| `VoxelVolume.occupied()` | — | uint8 occupancy array | Full 3D occupancy grid |
| `VoxelVolume.voxel_count` | — | Integer (read-only) | Total number of voxels |
| `VoxelVolume.make_well_composed(max_passes=64, callback=None)` | Max iterations, optional callback | `dict` (added_voxels, passes, boundary_blocked, converged) | Apply well-composedness repair |
| `VoxelVolume.extract_surface(callback=None)` | Optional callback | `(vertices, faces)` tuple | Extract triangulated surface from repaired occupancy |
| `verify_surface_deviation(original, repaired, tolerance_mm, callback=None)` | Original triangles (n,3,3), repaired triangles (m,3,3), tolerance float, optional callback | `dict` with `passed`, `status`, `certified_upper_bound_mm`, `sampled_lower_bound_mm`, `distance_queries`, `target_degenerate_triangles_skipped`, `method` | Bidirectional surface-distance check. A triangle's center distance plus its covering radius bounds every point on it, so `certified_upper_bound_mm` is a proof, not a sample. Failing to certify counts as failing. |
| `goo_encode_layer(image)` | uint8 (height,width) image | `bytes` | GOO v3 layer blob with magic and checksum |
| `goo_decode_layer(blob, width, height)` | GOO v3 blob bytes, width, height integers | uint8 (height,width) image | Decode layer; raises on checksum or framing error |
| `extract_runs(mask, want=1, cap=RUN_TABLE_CAP)` | uint8 occupancy panel, want 0/1, optional cap | `(starts, ends, row_offsets)` | Row-RLE of a binary panel; raises when the run table would exceed the cap. Dense fallback is the caller's decision |
| `WorkerLimit(n)` | Worker count (1–32) | Instance | Ceiling on native parallel loops while the object lives: `tbb::global_control` with TBB, the `std::thread` fallback's limit without |
| `HAS_TBB` | — | `bool` | Whether this build links oneTBB (otherwise `native/parallel.hpp` uses `std::thread`) |

## Where to Change What

| Task | File(s) | Notes |
|------|---------|-------|
| Add a CLI option | `cli.py` `config.py` | Register argument in `build_parser()`, add to `_overrides()`, add to `DEFAULTS` and validation |
| Change a validation rule | `validation.py` | Modify `analyze_layers()` check thresholds, diagnostic criteria; update test expectations |
| Change scan conversion algorithm | `native/raster.cpp` | Rasterizer fill rule or contour handling; requires rebuild via `scripts/rebuild.sh` |
| Add a new repair mode | `repair.py` | Implement a function returning `(solid, report)` tuple; wire into `pipeline._solid_from()` |
| Change support routing logic | `supports.py` | Modify `route_contacts()`, `_free_to_plate()`, branching attempt limits, or bracing heuristic |
| Change orientation search strategy | `geometry.py` | Adjust `auto_placement()` direction count, refinement iterations, separator distances, or scoring weights |
| Change drainage analysis grid | `validation.py` | Modify `analyze_drainage()` pitch calculation or `voxels_per_radius` parameter |
| Expose a new native function | `native/*.cpp` + `native/module.cpp` | Write C++ function, bind via pybind11 in `bind_*()`, call from Python; rebuild |
| Add a GUI panel | `gui/` | Create new `.py` file in `gui/`, register in `gui/window.py` |
| Change printer profiles | `profiles/` and `src/voxelmill/data/` | `profiles/` holds the editable examples; `src/voxelmill/data/` holds the copies that ship in the package. Keep both in step, and remember `config.DEFAULTS` is a third source. |
| Add a new check to reports | `contracts.py` `validation.py` | Add key to `ValidationReport.checks` dict; populate in analysis functions |


## CLI/GUI operation and project parity (2026-09-07)

`gui/operations.py` builds forms from the CLI parser and invokes
`python -m voxelmill.cli` through `QProcess` with an argument vector. This exposes
all noninteractive command options without duplicating preparation, correction,
profile or analysis logic, and isolates CLI affinity/address-space limits from
the editor. Per-field overrides take precedence over applied document defaults.

The CLI accepts `.voxmil` preparation inputs via the Qt-free `project` module;
temporary source extraction is verified and cleaned up, while authored settings,
pose and contact lists provide defaults. `pipeline.prepare` records those same
decisions in newly written projects, including suppression edits and zero lift.

`presets.py` owns versioned support-only preset validation, built-ins and atomic
portable JSON writes. CLI and GUI share this module. Preset values remain
geometric starting points; process presets and profile discovery are separate
unfinished features.

`gui.services.LayerSlicer` retains native indices for one assembly, serializes
sweeps and resets on reverse seeks or interrupted slices. `DerivedState` drops
it when the union changes. `JobRunner` tracks both document generation and
individual request identity. The visible layer additionally checks source and
index, since a cache hit supersedes an in-flight request without submitting a
new job. Native builds require pybind11 3.0.4 to avoid a stale Python thread state
when a Qt worker performs the first import.
