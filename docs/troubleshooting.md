# Troubleshooting

Every failure below is a reported outcome, not a crash. Exit code `2` means the
command ran and the answer was no; the report is still written and is the thing
to read. Exit `3` is a structured error on stderr with a stable `code`.

## `stl_count_mismatch`, `stl_trailing_garbage`, `stl_truncated`

These are **warning**-severity load diagnostics from `mesh.open_stl`, not
refusals. They mean the binary STL was usable after a mechanical recovery:

| code | what happened |
| --- | --- |
| `stl_count_mismatch` | File length is exactly `84 + 50n` but the header count is not `n`. The reader uses `n`. |
| `stl_trailing_garbage` | Bytes remain after the declared `84 + 50 * count` prefix. The tail is ignored; the declared triangles are kept. |
| `stl_truncated` | The file is shorter than `84 + 50 * declared`. Complete 50-byte records are kept; a partial final record is dropped. |

Zero complete triangles after truncation still raise `empty_stl`. ASCII inputs
and nonfinite export coordinates are unchanged. Details carry `bytes`,
`declared_triangles`, and either `used_triangles` or `trailing_bytes`. See
[geometry.md](geometry.md#stl-loading).

## `exact_union_unavailable` and `union_raster_parity`

A warning means ingestion selected grouped raster assembly. Inspect
`stages.assembly.exact_union_blocked_by` for all available findings and skipped
checks. `--repair none` deliberately takes this path without a solid scan.
`--set assembly.union=exact` makes this a structured refusal instead.

A raster export is a triangle soup, not a certified boolean solid. Its reopened
pixels must match the fresh grouped union. `union_raster_parity: fail` records
mismatched pixel counts and positions; reversed overlapping shells can cancel
against supports in the STL. Preserve that failure rather than treating a zero
negative-crossing count as proof of equivalence.

On this path `seal_voids` is explicitly `not_run`: there is no solid to decompose.
Enclosed voids still fail the layer checks. Use an accepted exact model or an
explicit aggressive repair that passes the requested deviation check when
filling is required. Turning parity off also records `not_run`; it does not
certify an export.

Successful ingestion does not imply supports can be routed. The float-valve
body's original pose still has hundreds of unreachable contacts with the
current planner; its report remains a failed validation. See the reproducible
measurements and next steps in `todo.md`.

## `no_feasible_placement`

The finite orientation search found nothing that fits. The details carry
`smallest_overflow_mm` and the available envelope.

The four skull quadrants overflow by about 13.6 mm and the full slab by 51 mm in
every orientation the search evaluated, against an available 144.2 × 68.6 ×
165 mm. That margin is far larger than search error, so those parts genuinely do
not fit the Mars 5 Ultra. A small overflow is a different matter: the search is
finite and is **not** an impossibility proof. Try an explicit `--rotate`, or
raise `finalists` and lower `min_separation_deg` if you are calling the API.

Nothing is ever scaled to make a part fit. Cutting is possible, but only when
explicitly requested: with `assembly.clip_to_build_volume` at its default
`false`, this is a refusal like any other, not a silent trim. With it `true`,
`prepare` still searches for a fitting orientation first; only when the search
finds nothing does it keep the unrotated pose and record
`stages.build_volume.mode: auto_search_exhausted` rather than raising. See
`goo_envelope` and `clipped_geometry` below for what happens at export.

## `invalid_scale` and `invalid_mirror`

`geometry.scale_matrix` rejects a `--scale`/`--mirror` request before any
triangle is transformed. `invalid_scale` covers a nonpositive factor (use
`--mirror` to flip an axis, never a negative scale), a factor outside
`[MIN_SCALE, MAX_SCALE]` (0.01 to 100.0 — see
[geometry.md](geometry.md#scale-and-mirror) for why that range and not
"finite"), or a value that is not one or three finite numbers.
`invalid_mirror` covers anything other than three booleans, one per axis;
the CLI's own `--mirror {x,y,z}` choices make an unparseable value
unreachable from the command line, so this is mostly an API-caller concern.

A factor outside the guardrail is nearly always a units mistake — a
meter-to-millimeter slip most commonly — rather than a genuine intention to
resize a part 200-fold. A caller that actually wants that scales twice.

## `invalid_placement`: `--rotate auto` with a transform

`prepare` refuses to combine `--rotate auto` with a nonidentity
`--scale`/`--mirror`. The automatic orientation search (see
[geometry.md](geometry.md)) ranks candidates on the unscaled, unmirrored
part; running it while quietly applying a different scale or mirror would
search on a shape that is not the one being exported. This is reported as a
current limitation, not silently worked around: give explicit `--rotate RX
RY RZ` angles when resizing or mirroring a part.

## `goo_envelope`

`slice_stl` raises this when the placed mesh's bounds fall outside the usable
build envelope and `assembly.clip_to_build_volume` is `false` (the default).
The error's details carry `overflow_mm` — the per-axis millimeters by which
the bounds exceed the envelope, from `geometry.envelope_overflow_mm` — and an
`enable_with` hint pointing at `assembly.clip_to_build_volume = true`. Turning
that setting on does not make the part fit; it makes the export proceed
against the geometry the printer can actually reach, discarding the rest. See
`clipped_geometry`.

## `clipped_geometry`

Appears only when `assembly.clip_to_build_volume` is `true` and the mesh does
not fit. It is an **error**-severity diagnostic, so `slice_stl`'s
`build_volume_clip` check is `fail` and the export is still withheld unless
`--allow-unresolved` (or the GUI's **Allow warned export**) is also given —
enabling clipping makes an oversized export possible, not automatic.

The diagnostic's details are the report's `metrics.clipped` object:
`overflow_mm` and `clipped_to` state what was measured against — the
overflow is against the smaller *usable* envelope (which additionally
reserves `printer.edge_clearance_mm`), while what is actually clipped is the
physical LCD panel and machine height, and `clipped_to` records that
distinction in words. `clipped_triangles` counts source triangles with any
vertex outside the usable envelope. `clipped_layers`, `source_pixels`,
`retained_pixels`, `clipped_pixels`, and `clipped_pixels_above_machine_z` come
from one extra raster pass on a grid extended past the panel; see
[algorithms.md](algorithms.md) for exactly how that pass is measured. Nothing
is scaled or repositioned to reduce these numbers — they describe what was
left out, not a target to hit.

## `goo_elephant_foot`

`slice_stl` raises this when `process.elephant_foot_compensation_mm` shrinks
a bottom layer's mask down to nothing — the erosion radius removed every
pixel of a layer that had material before compensation. The error's details
carry `layer`, `radius_px`, and `pixels_before`. No file is written: the
staged export is discarded and, unlike a validation-failure export, this is
not something `--allow-unresolved` can override — it is raised while writing
the layers, not returned as a warned report. Any previous file at the output
path is left exactly as it was.

This means the compensation radius is larger than the bottom layer's own
footprint at that point in the ramp. Reduce
`process.elephant_foot_compensation_mm`, shorten `process.elephant_foot_layers`
so the full radius applies to fewer layers, or check whether the bottom
layer in question is meant to be that thin — a raft or a small contact point
can be eroded away well before a 1.0 mm compensation request would be.
`validate_settings` already refuses any `elephant_foot_compensation_mm`
above 1.0 mm for the same reason, before a single triangle is rasterized;
this error is the case where a smaller, in-range request still erases a
layer that happens to be narrower than the radius. See
[algorithms.md](algorithms.md#elephant-foot-first-layer-compensation) for how
the ramp and the erosion are computed.

## `goo_orientation_unverified`

Every `slice_stl` export appends this diagnostic while
`printer.image_mirror_verified` is `false` (the default), and `voxelmill slice`
also prints a one-line stderr banner. It is **warning**-severity, so
`validation.checks['image_orientation']` reads `warn` and the export is not
blocked — the report cannot judge which physical orientation is correct, so it
states the uncertainty instead of refusing. Its details carry the profile's
own `image_mirror_x`/`image_mirror_y`, plus `REFERENCE_MIRRORS`, the two
reference GOO files' own flags, which disagree: CHITUBOX Basic v2.3.1
(`mirror_x=1, mirror_y=0`) and ELEGOO SatelLite (`mirror_x=0, mirror_y=1`). A
mirrored threaded or keyed part is scrap and looks correct until it is
assembled.

It clears only by setting `printer.image_mirror_verified = true` after
checking a printed part against the profile's mirror flags; there is no
automatic check that can set it.

`scripts/goo_orientation_check.py GOO STL --output REPORT.json` was run
against both reference files, using each one's own source STL
(`right_temporal_bone_mars5_oriented.stl`), and came back **inconclusive**
(`conclusive: false` in both reports). The source mesh is shared, but each
slicer posed the model itself, and the check has no way to recover an unknown
rotation about Z — centroid alignment removes translation only. Best mean IoU
across the four sampled layers was 0.21 for the CHITUBOX file and 0.13 for the
SatelLite file; the four layers picked three different flips in each file, and
widening the Z search window from 1 mm to 3 mm changed the CHITUBOX winner.
Our slices held 1.0–2.1 M candidate pixels against references of 1.1–5.3 M, so
real material was being sliced in every case — the shapes just do not align,
which is a pose mismatch, not a small Z error. See
`reports/plan2/mirror-chitubox.json` and `reports/plan2/mirror-satellite.json`
for the full per-layer results.

## `drainage_bottlenecks: not_run`

The occupancy grid could not be closed, so drainage cannot be certified. Check
`unclosed_rows` and `unclosed_slices` in `metrics.drainage`, and
`checks.closed_surface`.

This is the expected result for an open surface. Four of the seven supplied
originals are open, with 5,500 to 9,400 boundary edges, and they produce roughly
90,000 unclosed scanline rows each. They are not printable until a repair closes
their cut faces — `--repair aggressive` with an explicit `--max-deviation-mm`.

A `not_run` is never upgraded to a pass by rerunning.

## `drainage_bottlenecks: fail` on a part with no cavities

Look at `bottleneck_examples`. Volumes in the hundredths of a cubic millimeter
with roughly 0.67 mm² of clearance are tip/model crevices (or, on dense real
parts, base-dependent pockets), not a defect in the solid model alone. Options:

- `--support-void-policy ignore` — records support-class bottlenecks under
  `ignored_support_bottlenecks` and does not fail that check; model-class
  findings still fail.
- Change tip geometry — on the synthetic sphere, `contact_diameter_mm=0.9` or
  `tip_base_diameter_mm=0.4` (equal to contact, no taper) closed the crevice;
  longer tips were not proven.
- Set `repair.min_void_volume_mm3` deliberately — the ignored population is
  reported as `ignored_bottlenecked_components` rather than dropped. Do not
  raise it silently to obtain a pass.

`--support-void-policy fill` seals enclosed shells after an exact union; it does
**not** fill drainage necks. A raster union reports `support_cavity_fill` as
`not_run` and must not be read as a successful seal.

A bore sized at exactly `min_orifice_area_mm2` also fails: sampling a round bore
at cell centers understates it by about 11% at the default resolution. Widen the
bore by roughly 15%, or raise `voxels_per_radius` when calling the API directly.
Do not widen the threshold to obtain a pass.

## `support_unroutable` and `incomplete_support_routes`

`support_unroutable` is the diagnostic on one contact `route_contacts` could
not route; `incomplete_support_routes` is the export-blocking summary
(`support_routes: fail`) once any contact failed or landed in a sealed cavity.
A contact now anchors on already-printed model material through a shortened
tip whenever the gap to the material below it is at least
`support.min_tip_length_mm` (default 0.30 mm), which is what routes most of
what previously failed on the float-valve parts — see
[algorithms.md](algorithms.md#supports) for the measured before/after counts.

The failures that remain are a different class: the contact lands in a column
the 0.15 mm analysis grid reports as solid at that layer, so there is neither a
vertical route nor material below to anchor on. Widening the branch search does
not recover these — measured at zero recovered contacts across all three
float-valve parts when the search was widened from 8 to 64 candidates.

At printer pitch those contacts turn out to be attached anyway: nearly every one
has occupied material within its own 3x3 pixel neighbourhood one layer below.
They are samples on near-vertical walls, not free-floating overhangs, and
whether such a face needs a support is a mechanical question this program does
not answer. `--exact-attachment` on the probe reports the counts. Nothing is
dropped from the coverage basis on that evidence.

Run `scripts/routing_probe.py SOURCE --output REPORT.json` to classify why each
contact failed. It reproduces `route_contacts`'s own decision sequence per
contact and records the deciding quantity (gap to material below, tip length
required, branch search radius, nearest free neighbour), and its routed/failed
counts match the router exactly. It exports nothing and changes nothing.

## `support_coverage: fail`

Downward faces sit farther from a contact than `max_contact_gap_mm`. With
`--no-auto-supports` this usually means the manual contact set has a gap; the
check still runs in manual mode precisely for this case. Read
`max_contact_gap_mm` and `uncovered_fraction` in `metrics.supports`.

## `support_anchor_load: warn`

A contact carries more downward area than `max_contact_load_mm2` under nearest
assignment. It warns rather than fails because a share of area is not a strength
result. Add contacts near the overloaded region, or reduce `spacing_mm`.

## `repair` rejects a mesh that "looks fine"

Bidirectional surface verification failed, or could not certify the limit. The
usual cause is a thin feature lost at the voxel pitch, or a hole closure that
moved the surface farther than `--max-deviation-mm`. Lower `--repair-voxel-mm`
if the budget allows. The application will not increase the pitch or the allowed
deviation on its own, and a failure to certify is treated as a failure.

## The whole test session stops with no summary

A fatal X error kills the process rather than one test: VTK opens a real X
window, and under the offscreen Qt platform the server rejects the window id.
The real-render test therefore runs in a subprocess under `xvfb-run`. If a new
GUI test drives a render window directly, do the same, or the session dies part
way through and reads like a hang.

## `SIGBUS` during a long run

The native library was replaced underneath a running process. Use
`scripts/rebuild.sh`, which writes a temporary file and renames it atomically;
never `cp` onto the installed `.so`. Existing processes keep their original
inode. This is distinct from real memory pressure.

## A code change appears to have no effect

The editable install does not rebuild C++. Run `scripts/rebuild.sh -j2` and
confirm the imported module path:

```sh
.venv/bin/python -c "from voxelmill import _native; print(_native.__file__)"
```

A stale `.so` in site-packages, not a code defect, has caused at least one
mysterious test failure in this project.

## `drainage_budget`

The analysis grid exceeds the memory budget. The error carries
`smallest_affordable_pitch_mm`. Raise `--memory-gib` or accept a coarser
analysis explicitly; the pitch is never coarsened automatically to fit.

## `goo_settings_differ`

`voxelmill verify` (and the GUI's **Verification → Verify GOO or CTB**) read panel
resolution, physical build size, pixel pitch, mirroring, layer height,
bottom/transition layer counts and bottom/normal exposure from the file
itself, not from the `--printer`/`--resin` profile supplied on the command
line. This warning means the two disagree: the profile describes a different
machine or process than the one that actually produced the file. Read
`settings_mismatches` in the report for the specific fields and their
`profile` vs. `file` values. The file's values were used for the check either
way; supplying a matching profile only removes the warning, it does not change
the result.

## A `verify` failure is a real result

`voxelmill verify` exits `2` when the decoded layers fail the configured layer
topology rules — an unclosed layer, an enclosed void, a transient trap. This is
not a bug in the check: it means the file that was opened has those layers.
The JSON report is written regardless (to stdout, or to `--report PATH`), and
it is the thing to read, the same as any other `2` in this project. Remember
what `verify` cannot tell you either way: it never establishes that the
decoded pixels came from a particular source mesh, so a failing `verify` on a
file produced by `voxelmill slice` is evidence about that specific file, not
about the STL that was sliced.

## Cancellation

`Canceled` is not an analysis warning and is never caught as a recoverable
error that lets a run continue into export. Interrupting yields exit `130`.

## Segfault on Preview → Layers

The former pybind11 3.0.1 build could retain a deleted Python thread state when
first imported in a Qt worker. The next native callback crashed while acquiring
the GIL. `Latch_fat_finger.stl` reproduced this in a fresh process; test sessions
that had imported the native extension on the main thread hid it. The build now
pins pybind11 3.0.4, including the [upstream fix](https://github.com/pybind/pybind11/pull/5870).

For an existing editable installation, rebuild, then restart the editor:

```sh
.venv/bin/python -m pip install pybind11==3.0.4
scripts/rebuild.sh -j2
```

Installing the newer headers alone does not update the compiled extension.
The rebuild script installs atomically; a running editor retains the old library
until restarted. Fresh-process and real VTK regressions cover the corrected path.
