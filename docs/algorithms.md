# Algorithms

What each analysis actually computes, what it assumes, and what it cannot
conclude. Coordinates and placement are in [geometry.md](geometry.md); settings
are in [configuration.md](configuration.md).

## Scan conversion

`native/raster.cpp` converts one Z plane at a time. Triangles enter and leave an
active set sorted by their Z extent, so a layer costs work proportional to the
triangles crossing it rather than to the mesh. Only one `uint8` layer exists at a
time; no dense build volume is ever allocated.

A triangle contributes a crossing when an edge spans the plane under the
half-open rule `za <= z < zc`, which yields exactly zero or two points for a
closed triangle. Rows are filled between contour crossings under one of two
rules. `nonzero` accumulates the signed crossing direction and fills where the
winding is not zero; `evenodd` counts crossings. They agree exactly for a
closed, consistently oriented, non-self-intersecting solid, and `nonzero`
additionally tolerates inverted components, overlapping shells and
self-intersections — which every supplied original contains.

Two details keep the mask honest:

- **Canonical edge interpolation.** Two triangles sharing an edge traverse it in
  opposite directions, and interpolating each way rounds to different doubles.
  When the shared crossing lands exactly on a sample row center, the half-open
  row rule can drop that row from both segments and punch a hole through solid
  material. Endpoints are therefore ordered by coordinate before interpolating,
  so both triangles produce bit-identical crossings. This assumes welded
  vertices; unwelded near-duplicates still diverge.
- **`odd_rows`.** A row whose winding never returns to zero means the
  cross-section is not closed there. The row is reported and the last span is
  never extended to the crop edge, so an open mesh underfills rather than
  inventing material. Callers must treat a nonzero count as evidence that the
  mask has holes — see drainage below.

## Measuring what a build-volume clip removes

XY clipping to the panel needs no dedicated code: `RasterGrid.for_bounds`
already clamps its crop to the panel, and in `native/raster.cpp`,
`clamped_row` clamps an edge's row range and `clamped_col` clamps a crossing's
column, so a span that falls off the panel simply contributes no pixels rather
than wrapping or erroring. Only Z needed new handling, in
`goo._check_export_bounds`, because the GOO layer count is derived from the
mesh's own top bound rather than from the panel: with `clip_to_build_volume`
on, the top is clamped to the machine height before the layer count is
computed, so no layer above the reachable height is ever rasterized.

`goo._measure_clip` reports exactly how many exposed pixels that removes.
It rasterizes once more, on a grid the same pitch as the printer's own but
extended past the panel on all four sides by enough padding to cover the
mesh's full XY bounds, with cell centers aligned to the panel grid's — so the
in-panel window is an exact contiguous sub-array (`[pad_bottom:pad_bottom +
height, pad_left:pad_left + width]`) of the extended grid rather than a
resampling of it. For every layer up to the mesh's unclamped top, it counts
total exposed pixels (`source_pixels`) and, for layers within the retained
(clamped) layer count, the pixels that fall inside that in-panel window
(`retained_pixels`); layers above the retained count contribute their whole
count to `clipped_pixels_above_machine_z`. `clipped_pixels` is
`source_pixels - retained_pixels`. The result also carries `source_layers`,
`measurement_grid` (the extended grid's width and height), and
`panel_origin_px` (`[pad_left, pad_bottom]`, where the real panel starts
inside the extended grid).

This pass runs only when clipping was explicitly requested — losing geometry
is not something to estimate — and it is a genuine second full rasterization
of the mesh, not a cheap derivation from the first.

`goo._clipped_triangles` separately counts source triangles with **any**
vertex outside the usable envelope (vertex-wise, not centroid-wise, so a
triangle straddling the edge counts even though part of it survives): this is
a triangle-level headline count, independent of the pixel-level measurement
above, and the two are not expected to agree numerically.

## Grouped raster assembly and parity

The model is an untrusted triangle soup; generated supports and raft are closed,
positive shells. `UnionLayerStream` rasters these two groups independently and
ORs their occupancy masks. Combining their signed crossings directly can cancel
material where a reversed model meets a positive support. A zero count of
negative crossings does not prove that cancellation is absent.

On the raster assembly path, independent STL validation additionally compares
every reopened layer against a freshly computed group OR. Both streams use the
**reopened** grid and layer count, so float32 round-trip changes to crop bounds
cannot move one stream onto a different lattice. Mismatches fail
`union_raster_parity`, with total differing pixels/layers and bounded position
examples. Open rows in either the carrier or any reference group fail
`closed_surface`, even if another group fills the missing pixels. Exact-path
reports omit the parity check because their carrier is already boolean-resolved.
Disabling parity records `not_run` and prevents an unqualified passing export.

This proves equality at the sampled printer lattice; it does not prove a valid
solid between sampled planes or eliminate cancellation within the untrusted
model group. Coarse drainage still checks the serialized soup representation.
Raw internal faces can also generate unreachable support contacts: the measured
body probe and outstanding routing work are recorded in `todo.md`.

## Layer analysis

`analyze_layers` walks the layer stream once and keeps only the current layer
plus label images. Connectivity and island findings are exact for the documented
binary masks; grayscale anti-aliased inputs treat every nonzero sample as
material, so low-intensity edge pixels cannot punch topology holes. Three
empty-space questions are deliberately separate because they
fail for different reasons and are fixed differently:

| Check | Question |
| --- | --- |
| `enclosed_voids` | Components that never reach the exterior over the whole build. Trapped resin. |
| `transient_traps` | Components enclosed by already-printed material at some layer that only open later. A future opening is not present drainage, so these carry the volume and the layer span over which the trap is live. |
| `drainage_bottlenecks` | Exterior-connected voids whose effective connection is narrower than the configured orifice area. |

`repair.support_void_policy` classifies those findings when the assembly still
has distinct `model` and `supports_and_raft` groups. A union finding that is
absent from a model-only analysis is support-class; findings that remain on the
model alone are model-class. Tip/model crevices are drainage necks in that
support class, not closed shells.

| Policy | Behavior |
| --- | --- |
| `fail` (default) | Any enclosed void or drainage bottleneck fails export. |
| `ignore` | Support-class findings are recorded (`ignored_support_voids`, `ignored_support_bottlenecks`) as warnings and do not fail those checks; model-class still fails. |
| `fill` | After an exact union, `fill_enclosed_cavities` seals enclosed shells on the union solid and the export is re-validated. The raster path reports `support_cavity_fill.status=not_run` and must not claim those voids passed. Drainage necks are not shells — fill is not a crevice fix; prefer less tip taper / wider contact, then re-measure. |

Growth and span use conservative coarse upper bounds with an exact
pixel-distance fallback where the bound is inconclusive. The configured distance
rule is a printability heuristic, not a strength proof. The exact fallback is
computed in 256×256 tiles with an anisotropic threshold-sized halo, retaining
equality pixels at the configured distance. This avoids allocating a full
8520×4320 distance field while preserving the full-panel result. Empty halos
are handled explicitly rather than relying on the distance transform's implicit
outside pixel.

Layer analysis runs concurrently across layers. Layer i depends only on layer
i-1, not on the whole prefix, so both connected-component labelings, the
overlap count and this growth transform are computed on a worker pool; only
the accumulators, the bounded diagnostics and the enclosed-void union-find run
in layer order, because void identity is temporal. Results do not depend on the
worker count. Workers are capped from a conservative memory estimate that
includes previous masks, labels, void labels, EDT scratch, component counts and
in-flight copies; reducing `resources.workers` or increasing
`resources.memory_gib` is the appropriate response to a `memory_budget`
refusal. `resources.workers = 0`, the default, derives one worker per physical
core capped at the point where the ordered stage stops the speedup scaling.

## Elephant-foot (first-layer) compensation

Bottom layers are overexposed so the part sticks to the plate, and an
overexposed layer cures wider than the model that produced it.
`goo.compensation_radius_px` computes an erosion radius, in pixels, per
bottom layer to shrink the exported frame back toward the model's own
footprint; `goo.compensate_mask` applies it to one layer's mask.

The radius ramps linearly to zero over `process.elephant_foot_layers`
layers: layer 0 gets the full `process.elephant_foot_compensation_mm`
radius, and the layer at index `elephant_foot_layers` (and every layer above
it) gets none. `elephant_foot_layers=0` derives the ramp length from
`process.bottom_layers` instead. The millimeter radius is converted to
pixels separately for X and Y from `printer.pixel_pitch_mm`, each rounded
independently — an anisotropic pitch therefore gets a different radius per
axis, not one radius applied to a square footprint.

That per-layer rounding means the ramp is quantised to whole pixels, so
adjacent layers can land on the same radius rather than a strictly
decreasing sequence: 0.2 mm compensation over three layers on a 0.1 mm pitch
rounds to 2, 1, 1 pixels, not three distinct rings — the last two layers are
eroded by the identical footprint. A request whose millimeter radius rounds
to zero pixels at the configured pitch is reported as `applied: false` with
a `reason` naming why (`"rounds to zero pixels at this pixel pitch, so
nothing is removed"`), rather than silently doing nothing.

Erosion uses an elliptical structuring element sized `(radius_x, radius_y)`
(`goo._elliptical_footprint`) and runs on the layer's already-cropped mask
with a zero-value border, not on the full LCD frame. That is the same
answer as eroding the full frame: everything outside the crop is empty air,
which is exactly what a zero border represents, so nothing at the crop
boundary can erode differently. It is also far cheaper — the crop is a small
fraction of the Mars 5 Ultra's 36.8 Mpx panel, and eroding the full frame on
every bottom layer would cost accordingly.

`goo.slice_stl` applies compensation in **both** the write pass and the
post-write verification pass: `add_layer` writes `compensate_mask(...)`'s
output, and the independent reopen-and-decode pass compares the decoded GOO
pixels against a freshly recomputed `compensate_mask(...)` of the same
layer, not against the uncompensated source mask. This is why
`verification.decoded_pixels` still reads `pass` on a compensated export:
that check proves the file matches the intended *exposure* — compensated
geometry included — not the raw uncompensated geometry the mesh describes.

If the requested radius would erase an entire bottom layer
(`pixels_after == 0` on a nonempty layer), `slice_stl` raises
`goo_elephant_foot` before that layer is written, and no file is written at
all: the staged export is discarded and any previous file at the output path
is left untouched. See
[troubleshooting.md](troubleshooting.md#goo_elephant_foot).

The layer-topology analysis (`raster_connectivity`, `overlap`, `growth_span`,
voids) and the reported cured `raster_volume_mm3`/`resin_usage` both describe
the **uncompensated** raster measured from the source mesh — compensation is
applied afterward, only to the bottom layers actually written to the GOO
file. The sliced payload's `elephant_foot.per_layer` is the authoritative
record of exactly which pixels compensation removed from the exposure; it is
not reflected in `validation` or in `resin_usage`.

## XY shrinkage and tolerance compensation

Dimensional compensation is a slice-time correction for resin cure shrinkage
and systematic over-cure, applied to each layer mask **after** rasterization
and **before** elephant-foot erosion. It does not resize the source mesh, so
the model stays dimensionally truthful and the compensation stays attached to
the resin/process block where it belongs.

- **`process.shrink_percent_xy`** — percentage scale about the plate center
  (crop center when the grid origin is unknown), then crop/pad back to the
  original mask shape. Positive values enlarge the exposure
  (`scale = 1 + percent/100`) so a part that would otherwise shrink lands
  closer to the designed size. `0` is a no-op.
- **`process.shrink_percent_z`** — accepted in settings and echoed in the
  report, but **not applied**. Scaling Z honestly would change which geometry
  lands on which layer and the layer count itself; the export leaves layers
  unchanged and records `applied: false` with that reason.
- **`process.tolerance_offset_mm`** — morphological radius in millimeters.
  Positive erodes (compensate over-cure / light bleed); negative dilates.
  Millimeters convert to per-axis pixel radii from `printer.pixel_pitch_mm`
  the same way elephant-foot does, including the "rounds to zero pixels"
  report when the request is smaller than one pixel.
- **`process.bottom_tolerance_offset_mm`** — when nonzero, replaces
  `tolerance_offset_mm` on layers below `process.bottom_layers`; otherwise
  every layer uses `tolerance_offset_mm`.

`goo.apply_dimensional_compensation` runs shrink then tolerance;
`goo.export_mask` then applies elephant-foot. `goo.slice_stl` uses
`export_mask` in **both** the write pass and the post-write verification
pass, so `verification.decoded_pixels` still proves the file matches the
intended *compensated* exposure. A request that would erase an entire
nonempty layer raises `goo_compensation` before any file is written.

Defaults are `0.0` and uncalibrated. Any nonzero dimensional setting is
flagged `uncalibrated: true` in the sliced payload's
`dimensional_compensation` block, which also reports `pixels_changed` and
`per_layer` records. Pair with a calibration workflow before treating the
numbers as measured.

## Standalone `.goo` verification

`verify_goo` runs the same `analyze_layers` used by the STL path, over decoded
frames from an already-written file rather than over a rasterized mesh.
`GooLayerStream` decodes every layer, undoes `mirror_x`/`mirror_y`, and yields
`Layer(index, z_mm, frame)` on a grid built from the header's
`resolution_x`/`resolution_y` and `display_width`/`display_height`; `z_mm` is
each layer's own `position_z`. Unmirroring is required because layer
topology — islands, growth spans, voids — survives an axis flip but a
diagnostic's `position_mm` does not, and a diagnostic pointing at the wrong
side of the plate is worse than none.

The header supplies geometry and process values, but no overlap, growth-span,
or minimum-void-volume thresholds. The verification payload records these
resolved policy values under `settings_from_caller`, separately from
`settings_from_file`, and records `analysis_options.track_voids`. A verdict
therefore identifies both the file geometry and the caller's topology policy.

By default that grid is not the full panel. `occupied_crop` makes one extra
decode pass before the analysis pass, finds the window every exposed pixel
falls inside across every layer, and adds a one-pixel border — the same
border `RasterGrid.for_bounds` leaves on the mesh path, so outside air stays
connected all the way around the geometry and the void analysis sees the same
connectivity it would on the full panel. `GooLayerStream`'s `crop` parameter
then decodes and crops every frame to that window before `analyze_layers` ever
sees it. Labeling a full 36.8-megapixel frame dominates decoding it, and no
supplied part fills the panel, so the extra decode pass is cheaper than the
labeling it removes. Measured on a 500-layer supported sphere: 450 s and
1,280 MiB on the full panel against 15.8 s and 130 MiB cropped, which is 28.5
times faster on 9.9 times less memory for an identical result
(`reports/bench/before-verify-crop.json` and `after-verify-crop.json`). The window's origin moves with the crop (`RasterGrid`'s
`column_offset`/`row_offset`), not its pixel pitch or its zero point in plate
millimeters, so `grid.xy` returns the same plate coordinates either way and
every diagnostic's `position_mm` is identical to the uncropped run — a test
(`test_verify_crops_to_the_exposed_pixels_without_changing_the_answer`) asserts
checks, the listed metrics, the enclosed-void count and every diagnostic
position all agree between the cropped and full-panel runs on the same file.
A file that exposes nothing anywhere makes `occupied_crop` return `None`,
which falls back to the full panel and fails `empty_raster` there, since
there is no window to define. `verify_goo(..., full_panel=True)` — and
`voxelmill verify --full-panel` — skips `occupied_crop` and analyzes the whole
LCD lattice instead, for comparison or when the crop itself is suspected.

Settings the file itself carries — panel resolution, physical build size, pixel
pitch, image mirroring, layer height, bottom/transition layer counts, and
bottom/normal exposure — are taken from the file rather than from the supplied
profile, since a profile chosen on the command line may describe a different
machine than the one that produced the file. Where file and profile disagree,
the difference is recorded as a `goo_settings_differ` warning rather than
reconciled, and the file's values are the ones used for the check.

This establishes that the file opens, frames and decodes, and that the decoded
layers satisfy the configured layer topology rules. It does **not** establish
that the pixels came from any particular source mesh, or that machine motion or
exposure values are calibrated — the former is `slice_stl`'s job, and the
latter needs a real print.

`slice_stl` never has to run this analysis on its own output. Its report
records `verification.layer_topology = 'equivalent_to_source'`: the
pixel-comparison step already proves every decoded LCD pixel equal to the
source raster, and that raster was analyzed earlier in the same report, so
repeating the analysis over full-panel decoded frames would only recompute an
answer already known to be identical. `voxelmill verify` covers the
complementary case — holding only the file, with no source raster to lean on —
by running that same analysis there.

## Drainage

`analyze_drainage` builds its own occupancy grid at `radius / 3`, where
`pi * radius^2` is `repair.min_orifice_area_mm2`. A chamber drains when a path
with clearance `radius` reaches the exterior.

Labeling raw air is not enough: a chamber behind a narrow neck shares one label
with outside air and would read as drained. The analysis instead erodes by the
clearance radius and labels the surviving *cores*. A core that does not touch the
grid border, but whose seed lies in air that does, is a chamber behind a
constriction. Its bottleneck is then bracketed by eight bisections of the
erosion threshold, reported as `bottleneck_area_mm2` (passes) and
`bottleneck_area_upper_mm2` (fails).

Limits worth stating plainly:

- The quantity is **circular-equivalent path clearance**, not the cross-sectional
  area of an arbitrary slot. A long thin slot of ample area still fails.
- XY samples are cell centers, so sub-grid walls can be missed.
- A curved bore under-measures. At exactly the 1 mm² threshold it reads
  0.886 mm² at the default three voxels per radius, 0.938 at six and 0.969 at
  ten. The error always understates the opening, so marginal parts fail rather
  than pass.
- **An unclosed grid cannot certify drainage.** If any slice reported
  `odd_rows`, every unfilled cell inside solid material is a leak that joins a
  chamber to outside air, and the analysis then finds no bottleneck at all.
  `drainage_check` returns `not_run` rather than `pass` in that case. Four of the
  seven supplied originals are open surfaces, so this is the normal path.
- A fully enclosed chamber has no bottleneck, so `drainage_check` fails on
  `enclosed_components` too. Otherwise it would contradict `enclosed_voids`.

## Orientation ranking

Two stages, described in full in [geometry.md](geometry.md). The broad sweep
scores thousands of candidates on a sampled convex hull; the feasible finalists,
kept at least 20 degrees apart, are then measured on a coarse occupancy grid for
trapped resin, sealed cavities, support accessibility and a stability lever arm.

Reports retain all accepted finalists, including a lone feasible pose, with
exact transforms and term values, weights and contributions. Assessed poses
sort by total score. Failed or disabled assessments remain visible after them,
ordered by the cheap composite, with a null total rather than a fabricated
zero. Unreliable void terms on an open occupancy grid are explicitly excluded.
The CLI and editor can select these poses. Bounds use the full mesh; sampled
normal scores and the coarse grid are approximations, and actual support
volume and physical weight calibration remain outstanding.

Trapped resin is a gravity sweep. Gravity points toward increasing Z, so resin
leaves along a path whose Z never decreases. Drainage propagates from the far Z
boundary back: a cell inherits it from the air directly beneath it in index
order, then spreads sideways through any air component on one level, since a
level-wise step changes no height. What remains is what the part keeps.

## Supports

Contacts come from downward faces and from raster island births, thinned to
one automatic contact per XY `spacing_mm` cell (lowest Z wins); islands,
manual/paint enforcers, and correction extras bypass the density cap.
`route_contacts` tries three strategies in order: a vertical pillar to the
plate, then an angled or branched route to a free column within
`2 * spacing_mm`, then a contact onto already-printed model material below it.
Shaft clearance is a capsule of radius plus `support_clearance_mm`, not
centerline samples; the occupancy overlay skips overlapping shafts. Every
failed route is reported with a capped diagnostic count and reaches the export
decision: exact raster connectivity alone does not prove the planned contacts
were placed or are removable.

Each routed contact is three segments — tip, pillar, and whatever it
lands on — and `route_contacts` computes each one's geometry from
`support` settings described in
[configuration.md](configuration.md#support-geometry-three-segments-each-with-its-own-setting).
Per-contact records (`contact_parameters.py`) overlay a subset of those
keys onto one contact by rounded plate coordinates; unmatched records are
reported and never merged onto a neighbour. `tip_shape` is `cone` or
`cylinder`; `break_point_diameter_mm` defaults to `0.8` and unions a sphere
onto the top contact so the snap-off is one closed solid; `0` emits no ball.
A ball that no longer fits a shortened tip fails that contact rather than
clipping the sphere.
`tip_base_r` falls back to the pillar radius only when
`tip_base_diameter_mm` is `0`; `branch_tangent` is
`tan(radians(pillar_angle_deg))`, and a candidate branch column at lateral
distance `lateral` from the contact is only usable when its computed
`drop = lateral * branch_tangent` is less than the available height above
the plate (or above the model anchor) — a steeper angle shrinks that
usable set. Which pillar radius (`run_r`) a routed run actually gets —
`pillar_diameter_mm` or the thinner `small_pillar_diameter_mm` — is
decided once, from that run's total length (elbow included), before any
of its cylinders or graph edges are built, so a small-pillar run is thin
along its whole length rather than only near the tip. Elbows get a union
sphere. A lower-hemisphere shoulder blend and small axial collar close the
notch where an angled or tree shaft meets the horizontal tip base; the blend
stays within the checked endpoint capsule. Downward brace origins start at the
full-width shoulder below each
tip taper and are processed from highest to lowest at `brace_spacing_mm`
(default 15 mm). Each branch uses `brace_angle_deg` (strictly between 0° and
90°, default 45°) and is limited by its complete diagonal
`brace_max_length_mm` (default 30 mm); the independent
`brace_max_distance_mm` controls which neighbors are considered. The
`brace_destination` policy chooses grounded supports, new base feet, or both;
the latter tries grounded supports first. The router chooses the shortest valid
destination, merges at the first support intersection, and suppresses
duplicate connections inside the spacing interval. Model parts can never be
brace destinations, even when primary part-to-part supports are enabled.
Branches that collide with model material, leave the build volume, or cannot
reach a valid landing are rejected and reported. Base landings use fan spokes
in every pattern and are checked with the configured base footprint.

**Unreleased pattern and density controls:** `brace_pattern="single"` emits unpaired diagonals toward the shortest eligible neighbors.
`"alternating"` changes the direction by shoulder-derived vertical level.
`"x"` requires two reciprocal vertical shaft spans and emits paired diagonals;
if either reciprocal branch is unavailable or collides, the pair is rejected.
Single spacing uses the minimum shared-height interval. Alternating and X
patterns count shared connections in their shoulder-derived level bands;
this prevents incoming diagonal ends from suppressing every second alternating
level. An X pair
uses one neighbor slot in each touched band and overlapping endpoint bands count
once. `brace_branches_per_node` limits distinct connections per interval from
1 to 8 and counts incoming connections. `brace_min_height_mm` excludes lower
origins, and `brace_azimuth_deg` rotates base fans and the alternating axis.
Each strut's radius is scaled to the thinner of the two pillars it connects,
so a small-pillar run is braced with a strut sized to itself rather than to the
nominal diameter.

A new plate landing uses a short vertical foot stem below the diagonal so the
tilted end cap stays above Z=0. The length limit applies to the diagonal axis;
the foot uses the selected base style. Full foot envelopes are checked before
acceptance. Both attempted destinations and descending origins obey the work
limit, including origins with no reachable candidate.

`route_contacts`'s `metrics` records the resolved values actually used —
`brace_spacing_mm`, `brace_max_length_mm`, `tip_base_diameter_mm`,
`pillar_angle_deg`, `small_pillars` (how many routed runs used the thin
class) — alongside `base`, `build_base`'s own record (see below).
Branch evidence includes `braces`, `brace_new_feet`,
`brace_candidates_examined`, `brace_origins_examined`, `braces_capped`, and
`brace_rejections` by collision, bounds, foot, length, spacing, grounding and
missing destination. Accepted junctions split the support graph's edges.

Unroutable contacts are collected even when diagnostics are capped. If
`support.drop_attached_unroutable` is on (the default),
`apply_attached_unroutable_drops` rasters the printer lattice at the layer
below each failed contact and drops those whose 3×3 neighbourhood is occupied.
Those are near-vertical walls sampled on both sides of the surface; they are
reported as `support_dropped_attached` and do not fail `support_routes`.
Island births and `extra_contacts` (manual edits and correction passes) are
never dropped. A true free overhang that will not fit still fails. Coverage
is left against the original selection so the drop does not invent uncovered
samples. This does not certify that the wall will not sag.

When a model anchor is available, `allow_part_to_part` gates it. With the
default avoidance of `1`, the router keeps the historical plate-first choice;
at `0`, model and plate candidates compete by total centerline length. Values
between them require the model route to be shorter by the configured ratio.
Contacts blocked by this policy are counted separately, while contacts with no
permitted route remain failures and retain their export gate.

Ordinary model routes reserve a separate bottom cone or cylinder using
`model_anchor_length_mm` (default 2 mm) with matching diameter and penetration
defaults. That length and `min_tip_length_mm` must both fit; only the top tip
shortens. Part-to-part is point-to-point (balls both ends); `_fit_anchor_tips`
keeps short gaps from swelling to `pillar_diameter_mm`. A small collar buried
inside both adjoining solids joins the new bottom in volume without changing
its outer dimensions. Zero bottom length retains the direct attachment. New
bottom footprints are checked conservatively on the existing columns, and
central column depth checks prevent extending through the lower material run.

`small_pillar_mode="middle"` keeps the thin-middle rule above. In `"model"`
mode, an eligible short gap instead gets one complete connector between model
surfaces; `small_pillar_max_length_mm` limits the whole gap and zero disables
selection. `support_segments.small_model_pillar` builds a single closed mesh
with a cylindrical shaft and either conical buried ends or cylindrical
extensions. Each end has its own depth. Both central-column penetrations and
the complete shaft envelope must fit the model lattice. A rejected candidate
retains the route failure unless an available plate route can be used.
`small_model_pillars` counts this distinct geometry; `small_pillar` and
`model_anchor` metrics state dimensions, selection basis, and sampled limits.

Braces use `brace_diameter_mm` when set, otherwise the smaller radius of the
two pillars, and only connect neighbours within `brace_max_distance_mm` (zero
derives `1.5 * spacing_mm`). Before adding a candidate, `_brace_clear` samples
the analysis grid through the strut's capsule, including clearance and the
layers it spans. Occupied columns reject the candidate and increment
`braces_collision_rejected`; this bounded grid check is geometric evidence, not
a proof of mechanical strength. The 20,000-candidate cap counts rejected as
well as emitted braces (`brace_candidates_examined`, `braces_capped`), and an
interval too small to advance the floating-point height fails explicitly.

`build_base` decides what every routed foot lands on. `grid` (default) is the
porous lattice. `plate` retains the legacy `raft_from_feet` geometry
bit-for-bit. `none` unions
actual 24-sided bare-foot sections and removes overlap from its measured
area; `nominal_disc_area_mm2` preserves the ideal pi-sum for comparison.
`pad` emits circular per-foot pads. `skate` emits a capsule whose total
length includes its rounded ends; zero length derives a circle of the touch
diameter. With `base_edge_slope_deg` set, that capsule becomes a frustum
(circular or elongated): widest at the plate, top inset by
`thickness / tan(slope)` where slope is the wall angle from the plate, matching
CHITUBOX's raft-slope definition. Slope `0` keeps the vertical capsule. The
taper is the same printed-layer staircase every other added base uses.
`skeleton` emits rounded pads and one deterministic Euclidean
minimum-spanning tree edge between each pair of connected feet. `grid` adds
the same tree, orthogonal strips clipped to the expanded rotated foot hull,
and a perimeter rim, leaving vertical openings. `hex` replaces those strips
with a honeycomb: hexagonal openings on the triangular lattice whose six
neighbours are all `base_cell_size_mm` apart, so pitch means the same
center-to-center distance for both lattices, and each opening is a hexagon of
flat-to-flat `base_cell_size_mm - base_strut_width_mm`. Lattice pitch is
`base_cell_size_mm` center-to-center; rotation affects the local lattice and
the layout is centered on the hull in rotated coordinates.

`triangle` emits the rounded pads, every edge in the lexicographically ordered
Delaunay triangulation, and a perimeter rim, leaving open triangular bays.
Degenerate foot sets with fewer than three unique points or collinear points
fall back to the deterministic minimum-spanning tree, so they remain one
connected base without inventing a zero-area triangle.

At equal pitch and wall width the two lattices open the same fraction of their
frame — `((pitch - width) / pitch)^2` for both, because the honeycomb's 15.5%
higher cell density exactly cancels its 13.4% smaller cells. Choosing `hex`
over `grid` changes wall orientation and cell shape, not resin. Prefer `grid`
when less resin and less suction matter; keep `plate` when a solid slab is
required.

`plate` keeps the legacy `raft_from_feet` bevel: `raft_thickness_mm` (default
1 mm) and a fixed top-edge bevel of `min(0.25, thickness/4)` mm (~45° over that
band). There is no separate raft-slope key, and `base_edge_slope_deg` remains
invalid on `plate` because that bevel already owns the edge. For a ~1 mm /
30° putty-knife edge on per-foot feet, use `skate`/`pad`/`grid`/… with
`raft_thickness_mm` or `base_thickness_mm` ≈ 1 and `base_edge_slope_deg=30`.

`base_edge_slope_deg` tapers the wall of any added base inward from the plate,
so the base is widest where it touches and the measured contact area is
unchanged. `0` keeps the vertical wall. The taper is a stack of whole printed
layers at the resolved `process.layer_height_mm`, not a smooth ramp: a smooth
cone in the STL would be resampled to those same steps at slice time, so the
emitted geometry is what the machine can make and `edge_steps`,
`edge_step_mm` and `edge_setback_mm` say exactly what was emitted. Each band
is extruded from the plate rather than from its own step, because bands stacked
face-to-face union into a solid that decomposes into several bodies once they
are thin; overlap, not contact, is what makes a union connected. A slope that consumes the footprint before reaching the configured
thickness fails with the height it reached rather than emitting a short base.

Every strategy records the emitted footprint area, convex envelope, open area
and open fraction, connected components, volume and removal description.
These establish geometry only: adhesion, removal force and print stability
require physical calibration. Geometry work checks cancellation and emits no
partial base. It refuses more than 8192 unique feet or 2048 candidate grid
lines with `base_complexity`.

In the model-anchor case, if the gap between the contact and the material below
it exceeds `tip_length_mm` the tip is a full-length cone with a cylindrical
pillar section above it, as before. If the gap is between `min_tip_length_mm`
and `tip_length_mm`, the tip cone spans the whole gap instead and no cylindrical
pillar section is emitted; `contacts_with_shortened_tip` and `min_tip_used_mm`
in the support plan's metrics record how many contacts used this and the
shortest tip actually used. A gap shorter than `min_tip_length_mm` is still an
unroutable contact.

This exists because, measured on the three float-valve parts (`reports/plan2/routing-summary.md`),
roughly half of every part's requested contacts failed to route before it, on
both the exact and raster assembly paths: the blocked contacts sat 1.39–1.48 mm
above the material below them, close enough to need a support but too far for
the router's old full-tip-only anchor. Routed counts: nut 110→195 of 229, cover
389→722 of 773, body 516→994 of 1,085. `scripts/routing_probe.py` reproduces
`route_contacts`'s own decision sequence contact by contact and records the
quantity that decided each one; its routed/failed counts match the router
exactly, and it exports nothing and changes nothing.

Widening the branch search from 8 to 64 candidates recovered **zero** of the
remaining failures on all three parts — that was measured, not assumed, so
search breadth is ruled out as their cause. The remaining failures (nut 34,
cover 51, body 91) are a single different class: the contact lands in a column
the 0.15 mm analysis grid reports as solid at that layer, so there is neither a
vertical route nor material below to anchor on.

Asked again on the printer's own lattice
(`scripts/routing_probe.py --exact-attachment`), every one of those contacts is
already attached: all 34 of the nut's, all 91 of the body's and 50 of the
cover's 51 have occupied material within their own 3x3 pixel neighbourhood one
layer below, a 0.054 mm window. The own-pixel count varies widely between parts
(9, 54, 37) because the samples sit on near-vertical walls and fall on either
side of the surface boundary; the 3x3 count does not. Occupied material one
layer below is the same attachment `raster_connectivity` certifies, and that
check passes on all three parts, so these are not islands. What is unresolved is
mechanical -- whether a face attached along its length still sags --- and
`downward_contacts` selects on face angle alone, which cannot tell that apart
from a free-floating overhang. No sample is dropped on this evidence.

A routed contact is a geometric route, not a proof that the pillar holds or
that it can be removed. Support mechanics remain uncalibrated by this change,
and the float-valve parts still fail validation.

Two checks measure the result geometrically:

- `support_coverage` — the distance from every sampled downward face to its
  nearest contact, against `max_contact_gap_mm`. Exceeding it fails and blocks
  an export. The downward faces are sampled even when automatic selection is
  off, because a manual-only run is exactly when an overhang gets missed.
- `support_anchor_load` — the downward area assigned to each contact by nearest
  point, against `max_contact_load_mm2`. Exceeding it warns rather than fails: a
  share of area is not a strength result.

Slenderness, span and bracing remain mechanical heuristics keyed to configured
limits. None of them has been calibrated against a real print.

## Repair

Conservative ingestion tries an exact vertex weld and a Manifold solid.
Geometric rejection selects the reported raster assembly path by default;
`assembly.union="exact"` keeps that rejection fatal. Explicit `repair=none`
skips conversion and goes directly to raster assembly.

Aggressive repair is opt-in native voxel repair: nonzero-winding slices fill an
occupancy grid, and an additive well-composedness pass makes its boundary
manifold. Half a voxel diagonal describes only a quantisation scale, so the
result is checked against the original surface **in both directions** using BVH
nearest-surface queries with adaptive triangle subdivision. Distance to a closed
set is 1-Lipschitz, so a triangle's center distance plus its covering radius
bounds every point on it. Passing requires every covering bound within the
configured limit. Failure to certify rejects the repair; the pitch and the
deviation limit are never relaxed to make a mesh work.

## What none of this establishes

Every number above is geometric. Exposure, adhesion, peel force, pillar strength,
removability and print duration require calibration against real prints on the
actual machine, and none has been done. `PrintTime` stays zero unless a caller
supplies a measured estimate.


## Surface peel-risk advisory

`peel_risk` complements enclosed-void and drainage checks. It groups full-resolution
STL triangles whose oriented normals lie within `peel.max_angle_deg` of downward,
using exact shared edges; touching at a vertex does not join regions. Each region
has surface and projected area, bounds and a physical layer span. Regions whose
projected area reaches `peel.area_threshold_mm2` produce an advisory warning.
This can identify a broad flat underside even when drainage passes.

The score is a dimensionless, uncalibrated heuristic: projected area divided by
the threshold, multiplied by one plus nonnegative area-weighted mean Z divided by machine
build height, multiplied by active lift speed divided by
`peel.reference_lift_speed`. Bottom and normal lift stages are selected from the
region's physical layer span; only stages with positive travel contribute.
Speeds retain the profile's native field units. No conversion to physical force,
failure probability, or a verified Mars tilt-release model is claimed. The
reference speed and screening thresholds are starting assumptions, not a
measured safe process. A zero speed does not suppress the geometric warning.

This check assumes consistent triangle winding and shared vertices. It does not
repair open, overlapping, duplicated or nonmanifold surfaces, and independent
mesh/raster checks remain authoritative for those defects. Summed projected
surface area is not an instantaneous peel contact area. Region details are
bounded while aggregate counts and maxima include the complete scan; resource
failure reports `not_run`, never an implicit pass. No geometry, vent holes,
orientation or printer motion is changed by this advisory.

Reopened STL validation (including prepare and editor exports) and STL-to-GOO
source validation run this check. Standalone GOO verification has masks rather
than oriented surface triangles and explicitly records that the surface check
was not run; its raster checks cannot substitute for this evidence.


### Contour, face and boundary contacts

Face sampling is the default: `downward_contacts` places a centroid on every
downward face shallower than `overhang_angle_deg`, and a lattice on faces
wider than `spacing_mm`. That is the face-support type.

`support.contour_supports` adds samples along edges that belong to only one
downward face — the outer perimeter of an overhang cluster. Interior shared
edges are skipped.

`support.boundary_supports` adds samples along open mesh boundary edges that
sit above the plate (`z >= 0.2` mm). Closed solids have no open boundary, so
they gain nothing. This is the crop-cut case: a segmented surface whose
boundary is a cut plane still needs contacts along that rim.

Both flags default off. They change where automatic contacts are proposed;
routing, coverage and the whole-plate collision field are unchanged.

Per-object `overrides.support` on an added model samples that part with the
overlay (spacing, pillar diameter, contour flags) and then routes on the
shared field. Geometry keys in the overlay become per-contact parameters for
contacts that came from that part.

### Tree supports

`support.tree_supports` groups nearby plate routes into a shared trunk. The
trunk junction is lowered so every branch rises at least `pillar_angle_deg`
from horizontal. Trunk and branch capsules include their emitted radii plus
`support_clearance_mm` when checking the occupancy field. Insufficient height
or obstructed capsules retain the original independent routes. The exported
support graph records the actual shared foot, trunk and branches. These are
conservative analysis-grid checks, not a calibrated strength result.

## Island correction passes

`island_guard.route_without_islands` is one loop shared by `pipeline.prepare`
and the editor's Compute attachments: route contacts, assemble, scan the
assembly for islands, and — if the scan finds any — feed their positions back
in as extra contacts and route again. Both callers go through this same
function, so a plate prepared from the command line and the identical plate
routed in the editor cannot land on different attachments.

The first pass and the last pass always scan the whole build. A pass in
between scans only a padded crop around the pillars the previous pass just
added, because relabeling the entire panel on every pass is far more raster
work than a targeted correction needs. A cropped scan cannot tell a
component that is genuinely small and unsupported from one that simply runs
off the edge of the crop — both look identical to a scan that cannot see
past that edge — so any island component that touches the crop boundary is
discarded as unknown rather than counted. This is what makes the cropped
scan usable at all, and exactly why it is never allowed to be the final
word: the verdict (`islands_remaining`, `resolved`) always comes from a full
scan. If a cropped pass happens to find nothing, one more whole-build scan
confirms that before the loop reports success, rather than trusting a scan
that only looked at part of the geometry.

The loop stops early, before `max_passes`, the moment a pass makes no
progress — its island count (compared against the last scan of the same
kind, local or full, since the two are not directly comparable) does not
improve on the one before it. When that happens with islands still present,
the result reports `stopped: 'no_progress'` (or `'max_passes'` if the cap
was reached first) and `islands_remaining` with the actual count and
positions, rather than being presented as a routing that worked. Each pass
is a full route-and-assemble, so `support.max_island_passes` (default 5,
range 1-10) is a time limit as much as a policy; `prepare`'s `--max-passes`
overrides it for that run.

## Unsupported overhangs

`overhangs.analyze_overhangs` (the `unsupported_overhangs` check,
`--no-overhang-check` on `prepare`, and **Verification → Overhangs** in the
editor) compares two things the pipeline already computes separately: the
downward-face samples `supports.downward_contacts` says need support, and
the contacts routing actually placed. A sample counts as reached when a
routed contact lies within `support.spacing_mm` of it — the same distance
the sampler used to space those samples in the first place, so a gap here is
a gap the router itself saw and did not fill, never a difference of
definition. A sample resting within one layer height of the plate is
excluded: it is already on the build plate, and nothing can be routed under
it.

Because the match radius is the *configured* spacing rather than a fixed
distance, a sparse support field (a large `support.spacing_mm`) makes this
check more forgiving, not stricter: fewer, farther-apart contacts still
"reach" every sample within that same wider radius. This check tells the
difference between a plate that was routed and one whose overhangs were
quietly left unrouted; it does not by itself judge whether the configured
spacing was a good choice.

The finding is a **warning**, never an export gate: `unsupported_overhangs`
can read `warn` and the export still proceeds. A gap means no contact was
placed within reach of that sample — it does not prove the part will fail
there, and a covered sample does not prove the pillar covering it is strong
enough. Both are geometry, not mechanics.
