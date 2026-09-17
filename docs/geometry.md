# Geometry and coordinates

Distances are millimeters. The plate center is X=Y=0; the plate is Z=0 and positive Z follows layer growth. With the plate hanging above the vat, gravity points toward increasing model Z.

`placement_for_triangles` rotates about the original bounding-box center using extrinsic X, then Y, then Z (matrix `Rz @ Ry @ Rx`). It translates the rotated bounding-box XY center to the requested offset and the lowest point to the model lift. The input array is never mutated. All final bounds use every original triangle, scanned in chunks of 65,536 triangles using float64 arithmetic. A homogeneous matrix applies to column vectors. Row-oriented NumPy arrays use `vertices @ matrix[:3,:3].T + matrix[:3,3]`.

Fit includes a conservative XY reserve equal to pillar radius plus raft expansion, alongside plate-edge clearance. This reserve applies to vertical support feet under the model. The caller must check the generated complete model/support/raft bounds again: angled routing can exceed this envelope. There is no scaling or cutting. Failure says “no feasible placement found” because a finite search is not an impossibility proof.

`envelope_overflow_mm(bounds, settings, reserve_mm=0.)` returns `[x, y, z]` millimeters: on each axis, the larger of the two single-side excursions past the usable envelope, not their sum, so it answers "how far past the edge does this reach" rather than "how much total width is missing." X and Y are measured against the edge-clearance-reduced half extents; Z against the plate at zero and the machine height (`printer.build_mm[2]`). An axis that fits reads zero.

`placement_or_overflow` is the non-raising counterpart to `placement_for_triangles`: instead of raising `no_feasible_placement`, it always returns `(placement, fits, overflow_mm)`, with a pose identical to what the raising variant would produce from the same inputs. `pipeline.prepare` uses it when `assembly.clip_to_build_volume` is `true`, and the editor's `gui/services.load_and_place` uses it unconditionally, so a part that does not fit is still placed and can be inspected, rotated, or exported rather than refused outright.

Placement and this module never cut a mesh — `envelope_overflow_mm` only measures, and `placement_or_overflow` only reports. Clipping geometry to what the printer can reach happens later, in `goo.slice_stl`, and only when `assembly.clip_to_build_volume` is `true`: it clips to the physical LCD panel in X/Y and the machine height in Z, which is a **larger** volume than the usable envelope this module checks against (the usable envelope additionally reserves `printer.edge_clearance_mm`). The overflow reported here is always against the smaller usable envelope; what actually gets discarded at export is measured separately and is documented in [algorithms.md](algorithms.md). Nothing is ever scaled, with clipping on or off.

## Scale and mirror

`placement_for_triangles` and `placement_or_overflow` both take a `scale`
(one or three per-axis factors) and a `mirror` (three booleans) argument,
defaulting to the identity, so nothing is ever resized or flipped unless a
caller asks. Both are model decisions, not printer settings: they are
carried on `Placement.scale`/`Placement.mirror`, not in a `.ptr` profile
(see [configuration.md](configuration.md)), because they describe the part,
not the machine.

`scale_matrix(scale, mirror)` builds the linear part of the transform —
per-axis scale with mirrored axes folded in as negative factors on the
diagonal — and enforces the guardrails. Factors must be positive; mirroring
an axis is the separate `mirror` argument, never a negative scale. Factors
must also lie within `[MIN_SCALE, MAX_SCALE]` (`0.01` to `100.0`): a factor
outside that range is far more likely a unit mistake — a meter-to-millimeter
slip — than an intention, and it would silently produce a part no build
volume can hold or one that quantises to nothing at printer pitch. A caller
that genuinely wants more scales twice and says so. `_placement` applies
scale and mirror about the model's own original bounding-box center, then
rotates, then centers and lifts using the *transformed* bounds, so a scaled
or mirrored part still lands on the plate at the requested offset and lift
however it was resized.

Mirroring reverses triangle winding, and this is a correctness requirement,
not a display concern. A mirror has a negative determinant, and a
negative-determinant linear map turns every outward-facing normal inward.
`iter_transformed_triangles` computes `det(matrix[:3,:3])` once per call and,
whenever it is negative, reverses each triangle's vertex order after
applying the transform, which restores outward winding. Without this, a
mirrored part's triangles would be wound the wrong way: the nonzero-winding
rasterizer (`native/raster.cpp`; see [algorithms.md](algorithms.md)) would
read the whole part as empty air, and the exact Manifold union would read it
as a hole cut from whatever it overlaps, rather than as solid material.
Mirroring two axes composes to a proper rotation — the product of two
negative determinants is positive — so nothing is reversed in that case;
the check is on the determinant of the whole matrix, not on individual axis
flips, so this composition is handled correctly with no special case. A
mirrored threaded or keyed part is still geometrically sound after this
correction — it stays solid — but it will not assemble: winding is
preserved, handedness is not.

`scale_note(factors, flips)` turns a scale/mirror pair into the one-line
warning `prepare`, `measure`, and the editor's measurement row all use.
It returns `None` at the identity, so an untransformed part carries no note
at all rather than a note saying nothing happened. A non-uniform scale gets
an explicit callout that it changes every fit, thread pitch and clearance in
the part, since a single number cannot carry that warning on its own.

Automatic orientation evaluates 192 distributed directions with 12 spins, then three rounds of refinements from eight best candidates, including initially infeasible candidates. That first pass ranks on a convex hull of a vertex sample and on sampled face normals: downward projected area, a support-volume proxy, height and XY footprint. Full original triangles verify each accepted placement. A support reserve may be relaxed explicitly in the report; the final assembly envelope remains authoritative. The search does not consider scale or mirror at all — it always ranks the unscaled, unmirrored part — so `prepare` refuses `--rotate auto` together with a nonidentity `--scale`/`--mirror` rather than silently searching on the wrong shape; see [cli.md](cli.md#prepare).

The hull score cannot see whether an orientation traps resin, seals a cavity or leaves a face no pillar can reach, so the feasible candidates are re-ranked. Refinement clusters candidates around the same seeds, so accepted finalists must differ by at least `min_separation_deg` (20 by default) of geodesic rotation; that test runs before the full-resolution bounds pass, which is what the loop actually spends its time on. Each finalist is then measured on its own coarse occupancy grid, whose pitch keeps the long axis near 150 cells and is reported:

- `trapped_resin_mm3` — air that cannot shed toward +Z. Gravity points toward increasing Z, so resin leaves along a path whose Z never decreases; a sweep from the far Z boundary back marks everything that can drain, and the rest is resin the part keeps when it comes off the plate. Sealed cavities are excluded here and counted separately, because sealing fixes those and rotating does not.
- `enclosed_cavity_count` / `enclosed_cavity_mm3` — air that never reaches the exterior.
- `support_accessible_fraction` — of the downward-facing cells, the share with nothing but air beneath them in the same column, which is the only kind a vertical pillar from the plate can reach.
- `center_of_mass_overhang` — the lever arm from the center of mass to the centroid of the supportable area, over the part's XY half-extent. It is not normalized by the model's own first-layer footprint: the model hangs from supports rather than standing on the plate, so that footprint is often a single cell.

Finalists are ordered by the cheap composite plus a weighted penalty over those terms. **A finalist whose occupancy grid could not be closed forfeits the two void terms**, keeping only accessibility and stability: holes let the flood escape, so an unclosed grid understates trapped resin and would otherwise let the orientation win on the strength of its own defect. The weights are uncalibrated starting points and every term is reported alongside the decision.

This grid is a ranking aid, not a validation result. It samples one slice per cell, so it can miss features thinner than its pitch, and it only orders candidates that already passed the full-resolution envelope check. Removability and physical stability remain unestablished; inspect the final diagnostics and use manual rotation when needed.

# STEP import

`voxelmill.importers.tessellate_step` converts a STEP / STP solid into an STL
mesh by shelling out to FreeCAD headlessly (`MeshPart.meshFromShape`). The
public function is the seam a native reader can replace later. Linear
deflection is `repair.step_linear_deflection_mm` (default `0.1` mm); angular
deflection is fixed at 15 degrees. Tessellation fidelity is a slicing input —
the CLI report records engine path, version, deflection, triangle count and
bounds. Coordinates remain millimeters. `repair.weld_tolerance_mm` (default
`0`, cap `0.05` mm) optionally welds near-duplicate tessellation vertices
through a spatial hash before the STL is finalized. `inspect_mesh` stays
exact-coordinate; only this explicit weld uses the tolerance.

# Solid operations and repairs

`mesh_to_manifold` deduplicates exactly coincident vertices and constructs a Manifold solid. Nonfinite coordinates, zero-area faces, invalid topology, nonpositive volume and detected self-intersections fail explicitly. There is no implicit tolerance weld or loss of original triangles.

The shared `assembly.prepare_model` service tries that strict conversion under
`repair.aggressiveness="conservative"`. With `assembly.union="auto"` (default),
a geometric rejection selects a raster union instead of aborting ingestion.
`repair.aggressiveness="none"` skips solid conversion entirely and preserves the
authored triangles. `assembly.union="exact"` requires a solid and rejects either
fallback case with `exact_union_unavailable`. Cancellation, memory limits and
explicit aggressive-repair failures remain errors.

The raster path ORs model occupancy separately from closed, positively oriented
support/raft occupancy. Its exported STL streams these triangle groups as a
soup. It has no certified solid volume or genus; the report uses null for those
values and reports printer-lattice resin volume separately. Exact conversion
counts degenerate faces across every chunk and reports skipped intersection
inspection explicitly, rather than paying for the scan after an earlier rejection.

`seal_voids` cannot fill a soup: its stage is `not_run` with a reason, and layer
analysis still reports enclosed voids. A cavity-containing model therefore
continues to fail validation unless a valid exact path can fill it. The raster
fallback changes neither authored coordinates nor the support-validation bar.

Explicit `repair.aggressiveness="aggressive"` selects native voxel repair. Nonzero-winding slices populate an occupancy grid; additive well-composedness repair makes its boundary manifold. Voxel pitch is explicit or derived from the requested deviation. The app never increases pitch or allowed deviation to fit a resource limit. Open scanlines underfill; repair does not extrapolate arbitrary missing walls.

Half a voxel diagonal is only a quantisation scale. Actual output is checked against the original surface in **both directions**, using BVH nearest-surface queries and adaptive triangle subdivision. Distance to a surface is 1-Lipschitz: the center distance plus a triangle's covering radius bounds every point on that triangle. Passing requires every covering bound within the configured limit (numerical epsilon 1e-10 mm). A sampled violation or inability to certify the limit rejects repair. Optional capped Taubin smoothing also receives a self-intersection check. Small missing features can therefore block an otherwise valid solid. Net differences against an invalid source's signed volume are labeled estimates, not exact added/removed resin.

`fill_enclosed_cavities` decomposes a valid oriented solid into connected shells and reverses inward shell orientation to union in their enclosed volumes. It reports added volume and shell count. Through passages remain intact. This operation does not determine effective drainage-path bottlenecks or transient cups, and its report says those checks were not run. Independent layer analysis tracks enclosed voids and transient cups; the clearance-grid analysis detects neck bottlenecks. Inadequately drained open passages currently fail validation rather than being silently filled. This preserves potentially intentional anatomical channels for review.

`ops.boolean_mesh` and `ops.trim_mesh` expose Manifold union, intersection,
difference, and planar cuts. Both prepare each input with
`assembly.prepare_model` first. Trim keeps `dot(x - point, normal) >= 0` (or
the opposite / both sides) by intersecting with a large half-space cube so the
cut face is capped and the result stays a closed solid. Reports record added
and removed volume relative to the primary solid. Empty or unrepaired inputs
raise `VoxelMillError` rather than writing an open mesh.

`ops.cap_open_cuts` closes meshes that are already open along cut faces. It
welds with exact coordinates, groups boundary edges into loops, and fills a
loop only when every vertex lies within `repair.max_deviation_mm` of that
loop's best-fit plane. Caps use the reverse of the directed boundary so
winding stays consistent with the existing surface. Non-planar loops raise
`VoxelMillError` (`cap_incomplete`) with capped versus remaining counts; voxel
repair is never applied on this path. When every loop caps,
`inspect_mesh` reports `boundary_edges == 0`.

The implementation uses the installed Manifold Python binding's documented `Mesh64`, `Manifold`, `decompose`, `volume`, `hull_points`, and cylinder/transform APIs. Upstream describes input validity requirements and notes that exporting STL discards topology; final STL reopening is therefore required. [Manifold documentation](https://manifoldcad.org/docs/html/) (consulted 2026-09-06).

# Support primitives

`cylinder_between` creates a 24-sided pillar or tapered tip between any finite, distinct endpoints. The local Z axis maps to the endpoint direction with a right-handed rigid transform. The caller owns routing, contacts, model collision checks, bracing and printable connectivity.

`raft_from_feet` builds one convex connected raft around 32-sided foot rings, extending by pillar radius plus the requested expansion. Its maximum XY extent matches the placement reserve. Rings are inscribed and deviate inward from an exact circle by at most `radius * (1-cos(pi/32))`. The bottom and lower wall retain this footprint; the top ring moves inward by the bevel amount. Default thickness is 1 mm and bevel is 0.25 mm. A convex raft contains no geometric enclosed cavities, but its effect on model drainage still needs final analysis. Convex hulls may bridge large distances between feet; final plate-fit and raster checks remain mandatory.

Selectable support bases are implemented in `bases.py`. `none` uses 24-gon
foot sections; pads and skate capsules use the configured touch diameter and
thickness. Skeleton and grid struts use the configured width (or nominal
pillar diameter), with grid pitch measured center-to-center. Grid strips are
clipped to the expanded hull and include a perimeter rim. The placement
reserve accounts for configured skate, pad and strut extents, then the final
complete assembly is checked again because routed geometry can exceed the
estimate.

# STL loading

`mesh.open_stl` accepts binary STLs that are not byte-perfect. A file whose
length is exactly `84 + 50n` uses the size-implied triangle count when the
header count disagrees, with warning `stl_count_mismatch`. Trailing bytes after
a valid `84 + 50 * declared` prefix are ignored (`stl_trailing_garbage`). A
file shorter than the declared span is read for as many complete 50-byte
records as exist (`stl_truncated`); zero complete triangles still fail as
`empty_stl`. Clean `84 + 50n` payloads remain binary even when the 80-byte
header begins with `solid`; messy solid-headed files still take the ASCII path.
Nonfinite coordinates are still rejected on write and counted on inspect — load
recovery does not invent or repair vertex data. Warnings are attached to
`STLMesh.diagnostics` and appear in `inspect_mesh`, `validate_stl`, and
`prepare` reports.

# Resources and verification

The geometry conversion reserves an estimated 600 bytes per input triangle through `ResourceBudget.require` before indexing. This is a conservative admission estimate, not an allocator-enforced memory cap. Manifold may use its own parallel backend; its Python bindings expose no thread-count setter. Bounds/transforms support chunk-boundary cancellation. Boolean operations must run outside the UI thread; subprocess resource enforcement and cancellation of a running Manifold operation require orchestration support.

Run:

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_geometry.py
```

Tests cover asymmetric extrinsic rotation, exact lift/offset, source immutability, support/raft edge reserves, deterministic automatic placement, finite validation and cancellation, valid-solid round trips, rejected boundary defects, inward cavities versus through passages, connected boolean support/raft/model construction, angled pillars, and bevel geometry. These synthetic checks do not establish full sample-suite or physical print acceptance.


Ranked orientation evidence is retained under `placement.search.ranked_candidates`.
Even a single finalist is assessed. Unavailable assessments have null totals
and follow assessed poses, ordered by the cheap composite. The CLI's
`--rotate auto --candidates N --candidate-rank R` selects exact saved angles;
the editor shows the same term evidence and applies an undoable pose. Actual
routed support volume remains unmeasured. The separate `peel_risk` surface
advisory does not silently change the automatic orientation weights.
