# voxelmill implementation plan

## Objective and stage gates
Build a Linux, single-part resin preparation application in this order: (0) foundation and full-resolution fixture inventory, (1) geometry/placement/cavities, (2) validated CLI and supported STL export, (3) complete GUI, (4) GOO slicing/export, (5) printer integration. Python orchestrates; PySide6/VTK renders; pybind11 C++ handles expensive geometry/raster work. Manifold handles valid-solid booleans; OpenVDB is reserved for explicitly permitted volumetric repair. Preserve every original fixture. Preview simplification must never replace full-resolution final validation. Default budget: 32 GiB, bounded workers, disk-backed intermediates; never allocate a dense printer-resolution build volume.

## Interfaces and defaults
- Initial CLI: `inspect`, `prepare`, `validate`, `gui`; add `slice`/`printer` at their stage gates.
- STL units default to millimetres. Origin is plate centre, Z=0 plate, positive Z layer growth. Gravity points toward increasing model Z with the plate hanging above the vat.
- `--rotate RX RY RZ`: extrinsic X, Y, Z degrees about original bounding-box centre; `auto` deterministic search; omitted preserves orientation.
- `--center-offset X Y`: final rotated model bounding-box centre relative to plate centre. `--model-lift-mm` defaults to 5. Preserve scale; never shrink/cut for fit. Failed searches say “no feasible placement found”. Fit includes supports and raft.
- Default assembly tries boolean union; geometric rejection falls back to a reported grouped raster union and soup STL carrier. Reopened-STL parity is required on that path. Exact-only assembly remains available; component STLs and JSON evidence are optional.
- Versioned human-readable TOML `.ptr` and `.res`. Printer owns physical dimensions, pixels, orientation, layer limits, capabilities and machine motion. Resin contains identity and printer-specific process settings, never physical overrides. Precedence: defaults → printer → matching resin process → explicit CLI.
- Versioned `.chop` archive stores resolved settings, source hashes, transforms, edits, support graph and validation.
- Mars 5 Ultra: 153.36 × 77.76 × 165 mm; 8520 × 4320 pixels; 0.018 mm XY pitch; 2 mm edge clearance.
- Sunlu ABS-like gray: layer 0.05 mm; bottom/normal exposure 35/3.5 s; bottom layers 4; five linear intermediate transition layers; rest after exposure 1 s; settle before exposure 0.5 s; additional wait after lift 0 s. Bottom and normal waits independently configurable, initially equal.
- Reference GOO is immutable compatibility evidence. Its differing exposures/layer counts must never replace requested profile. Verify machine fields and tilt semantics at stage 4.

Example:
```sh
voxelmill prepare inputstl/left_temporal_bone_mars5_oriented.stl --printer profiles/mars5-ultra.ptr --resin profiles/sunlu-abs-like-gray.res --rotate auto --center-offset 0 0 --support-spacing-mm 3 --seal-voids --min-orifice-area-mm2 1 --output output/left-supported.stl
```

## Geometry and support policy
Surface contact spacing target 3 mm; islands/span/load may require denser contacts and every override is reported. Medium support starting dimensions: contact diameter 0.4, penetration 0.15, pillar diameter 1.2, tapered tip length 2 mm. Connected bevelled raft: 1 mm thick, 2 mm expansion; check connectivity, drainage and clearance. Prefer vertical pillars, then angled tips/branches, then reachable/removable model-to-model contacts. Primary model anchors are opt-in; downward braces default to 45° shoulder-origin branches at 15 mm vertical spacing with a 30 mm complete length limit and never anchor on model parts. The 0.5.4 update exposes destination modes, per-node connection limits, angle, and single/alternating/X patterns in the Bracing editor tab. Bracing and stronger anchors depend on slenderness and load limits; physical strength requires calibration.

Fill enclosed cavities on the exact solid path by default; raster fallback reports cavity filling as not_run and retains enclosed-void failures. Inadequate drainage remains a reported failure. Default effective drainage-path bottleneck threshold 1 mm², equality passes. Separate enclosed cavities, inadequate drainage, and transient cups/pooling during layer growth; future openings are not present drainage. Preserve adequate anatomical passages. Independent options: sealing; repair `none|conservative|aggressive`; maximum deviation; tiny-feature removal (off); drilled drains (off). Record all edits and added/removed volume. Aggressive voxel repair and holes require explicit options.

Export requires passing validation unless explicit `--allow-unresolved`; warned exports preserve failed validation and diagnostics. This never permits corrupt/unencodable serialization. The GUI and GOO later share this policy.

## Stage 0 — Foundation and baseline evidence
Package/build setup, native extension, documentation, immutable fixture manifest, reproducible commands. Memory-mapped STL ingestion and bounded scheduling. Inventory all seven meshes: hashes, triangles, bounds, components, invalid triangles, boundary edges, nonmanifold regions, self-intersections. Preserve GOO and record observed metadata. Shared typed MeshAsset, Placement, SupportGraph, LayerStream, ValidationReport, PrinterAdapter, units, cancellation, progress and structured failures.
Acceptance: reproducible installation and inspection of all seven supplied STLs within budget.

## Stage 1 — Geometry, placement, cavity analysis
Transforms, conservative repair, collision acceleration, configurable cavity filling; connectivity/bottlenecks across build sequence. Deterministic coarse-to-fine orientation search ranks feasible results by cavities/cups, accessibility, stability/peel proxies, support volume, print height. Reassess complete envelope after supports change. Controlled cavity/orifice fixtures and reproducible sample placement outcomes.

## Stage 2 — Supports, STL export, independent post-check
Native-resolution internal rasterizer now (GOO later): spatial index, active triangles, tiled masks, disk-backed records. Detect island births and overhang growth; contacts must cover spacing/load requirements. Collision-free support routing with already printable paths to plate. Check coverage, span, load, slenderness, bracing, anchors; distinguish heuristic mechanics from exact raster connectivity. Exact union or grouped raster fallback, then reopen and independently reslice exported STL; compare fresh grouped occupancy pixel-for-pixel on the fallback path. Validate all layers for islands, corner-only connections, overlap and growth; slab-based void/drain/cup analysis retains boundary connectivity. Up to five targeted correction passes; stop on success/no progress/limit. Never silently ignore one-pixel islands or delete features. Acceptance: zero unresolved raster islands/prohibited cavities/configured support-rule violations or explicit failure with optional warned export.

## Stage 3 — Complete single-part Linux GUI
3D placement, profiles, auto/manual supports (add/delete/move), repair controls, visibility; layer scrubbing/selectable diagnostics; save/reopen, undo/redo, STL export, progress/cancellation/warned overrides. Heavy jobs off UI thread; reject stale results. Same core settings/services as CLI. Acceptance: supplied large model preparation and corrections/project round trip without blocking UI, CLI/GUI equivalent.

## Stage 4 — GOO
Published ELEGOO spec and independently compare UVtools, pin revisions. Full slicing, compression, previews, dimensions, mirroring, exposure transitions, delays, machine fields, integrity and bounded decode. Start binary masks; optional antialiasing validated after decode using documented occupancy. Reopen every generated file, compare decoded pixels/settings independently, asymmetric orientation/timing/tilt fixtures. Hardware calibration required to call exporter verified. Do not claim byte equality with reference is required.

## Stage 5 — Printer
Documented SDCP adapter verified against actual firmware: discovery/address/capabilities, upload, separate explicit start, pause/resume/cancel, status/watch and RTSP camera discovery. Handle disconnects/failures/busy/no-camera/ambiguous acknowledgements; reconcile before retrying start. Warned output retains status. Simulator before hardware; then transfer/start/progress/video/recovery acceptance.

## Verification and documentation
Fast synthetic tests + separate full-resolution sample suite: transforms/units/invalid meshes/thin walls/failed fits; pixel islands/overhangs/growth/tall pillars/obstructions/model contacts/support-born islands; enclosed cavities/orifices below-at-above threshold/necks/multiple openings/future drainage/blocked drains; STL/project/GOO round trips/orientation/corruption/transitions; cancellation/memory/scratch/determinism/GUI/transfer/video. Run all originals without silently simplifying. Record failures accurately, runtime, peak RSS, scratch, supports, added resin and diagnostics. Maintain quickstart, CLI/config reference, architecture/coordinates, algorithms/calibration/troubleshooting/examples, pinned dependencies/references/notices. Large generated artifacts stay outside source control.

## Coordination
Coordinator owns shared contracts, geometry policy, gates, integration and this plan. Up to three agents receive bounded ownership/interfaces/tests/prohibited scope. No concurrent edits to shared contracts. Verify every handoff. Preserve stage order. Multi-part packing, cutting and wider CHITUBOX parity deferred.

## Sources
- https://manifoldcad.org/docs/html/
- https://www.openvdb.org/documentation/doxygen/overview.html
- https://www.elegoo.com/products/mars-5-ultra-9k-7inch-monochrome-lcd-resin-3d-printer
- https://docs.chitubox.com/en-US/chitubox-pro/latest/support/support-configuration
- https://github.com/elegooofficial/GOO
- https://github.com/sn4k3/UVtools/blob/master/UVtools.Core/FileFormats/GooFile.cs
- https://github.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0/blob/main/SDCP%28Smart%20Device%20Control%20Protocol%29_V3.0.0_EN.md

## Development journal
### 2026-09-06 — Initial workspace
- No existing application or repository instructions. Seven STL originals (~1.2 GiB total) and one 183 MiB reference GOO are present.
- Python 3.10, CMake, GCC, NumPy, SciPy, PySide6, VTK and pytest are available. Manifold and pybind11 need installation.
- All stages initially pending. Hardware acceptance requires access to actual printer and physical calibration; no such evidence yet.
- Next: freeze contracts; foundation native mesh inventory; profiles/projects; core raster/validation. Later stages remain gated on evidence.

### 2026-09-07 — Continuation audit
- Supersedes the earlier LAN permission: the user reports an active print and forbids direct device connections for now. All stage-5 work is offline/simulated; hardware acceptance is deferred.
- Baseline is 110 passing tests. The previous TODO status lagged implementation: GUI and GOO existed, but GOO had no tests and GUI coverage bypassed the real render window.
- Found and correcting: destructive failed-export publication, false voxel deviation bounds, outside-air drainage false passes, omitted routing diagnostics, unsupported no-op settings, and missing manual/automatic toggles.
- Work is divided between a coordinator for geometry/validation/integration and bounded agents for GUI, GOO, and offline SDCP. No large fixture jobs run concurrently.
- `gotchas.md` records verified lessons. User priority is output quality and resolution; no automatic scale reduction, coarser repair, or relaxed validation to achieve a pass.

### 2026-09-07 — Correctness pass over the audit's output
- The test session had stopped aborting silently: a real-VTK-render GUI test opened an X window under the offscreen Qt platform, and Xlib answered `BadWindow` by calling `exit()`, killing the run after 62 of 148 tests with no summary. That test now runs in a subprocess under `xvfb-run` and asserts on the render window's own buffer, because Qt's `grab()` cannot capture VTK's native child window and the old assertion passed with a black viewport. Suite: 154 passed, 7 skipped, plus 8 opt-in full-resolution sample tests.
- Found and fixed a rasterizer defect that punched a full raster row through solid material wherever a shared triangulation edge's crossing landed exactly on a sample row centre. Two triangles sharing an edge traverse it in opposite directions and round differently, so the half-open row rule dropped the row from both. A sealed 9 mm hollow cube consequently reported as a drained chamber rather than an enclosed one. Fixed by interpolating along a canonically ordered edge.
- `analyze_drainage` was discarding the `odd_rows` the rasterizer already reported, so an open mesh produced a holed occupancy grid and a confident false pass. Drainage now reports `not_run` when the grid is not closed, fails on enclosed chambers as well as bottlenecks, and honours `repair.min_void_volume_mm3` while counting the ignored population. Four of the seven originals are open surfaces, so this was the normal path, not an edge case.
- Orientation ranking no longer lists cavities, cups, accessibility and stability as unassessed. Feasible finalists, kept at least 20 degrees apart, are measured on a coarse occupancy grid for trapped resin (a gravity sweep toward +Z), sealed cavities, support accessibility and a centre-of-mass lever arm. A finalist whose grid could not be closed forfeits the void terms, since its own holes understate trapped resin. Testing the separation before the full-resolution bounds pass took a temporal bone from 38.3 s to about 7 s of search, faster than the 16.5 s it cost before the assessment existed; the recorded per-mesh figure of 7.9 s includes opening the STL.
- Added explicit `support_coverage` and `support_anchor_load` checks. Downward faces are sampled even when automatic contacts are off, because a manual-only run is when an overhang is most likely to be missed. Coverage failure blocks an export; load exceedance warns, because a share of area is not a strength result.
- Documentation: new `docs/cli.md`, `docs/algorithms.md`, `docs/calibration.md`, `docs/troubleshooting.md`, `docs/examples.md` and `docs/architecture.md`; `docs/geometry.md` and `docs/configuration.md` brought up to date; `README.md` indexes them. `gotchas.md` gained thirteen verified entries.
- Placement evidence over all seven originals is recorded in `output/sample-placements.json`. Both temporal bones place in about 7.9 s at 1.15 GiB peak RSS; the slab and its four quadrants correctly report `no_feasible_placement`.
- Still uncalibrated and explicitly so: every support dimension and limit, the orientation weights, the drainage threshold, and all machine motion and tilt semantics. No printer traffic occurred.
