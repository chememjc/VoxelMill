# Command reference

Every command reads the same settings stack and writes the same JSON report
shape. Reports go to stdout unless `--report PATH` is given, so `--progress`
writes to stderr instead and the two can be redirected separately.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The command succeeded and, where it validates, validation passed. |
| `2` | The command ran and the answer is no: validation failed, or an export was withheld or warned. The report is still written. |
| `3` | A structured `VoxelMillError`. A JSON `{"error": …}` object goes to stderr with a stable `code` field. |
| `64` | The command line could not be parsed (unknown option, missing argument). Usage goes to stderr; nothing ran. |
| `130` | Canceled with SIGINT. |

A `2` is a result, not a crash: the evidence is the product, and it is written
whether or not the geometry file was. Only `3` and `64` mean the question went
unanswered. These codes are part of the stable interface.

## Settings options

These are accepted by every command except `info`.

| Option | Effect |
| --- | --- |
| `--printer PATH_OR_ID` | Printer `.ptr` profile. Owns physical size, pixels, orientation and machine motion. Accepts an explicit path or a bare library identifier; see [profiles.md](profiles.md). |
| `--resin PATH_OR_ID` | Resin `.res` profile. Owns identity and process settings, never physical overrides. Accepts an explicit path or a bare library identifier; see [profiles.md](profiles.md). |
| `--set SECTION.KEY=VALUE` | Override any resolved setting. `VALUE` is parsed as JSON when it parses, else kept as a string. Repeatable. Dimensional compensation uses `--set process.shrink_percent_xy=...`, `process.shrink_percent_z=...`, `process.tolerance_offset_mm=...`, and `process.bottom_tolerance_offset_mm=...` (all default `0`, uncalibrated). |
| `--layer-height-mm`, `--support-spacing-mm`, `--overhang-angle-deg` | Common process shortcuts. |
| `--pillar-angle-deg DEG` | Steepest-to-shallowest angle from horizontal an angled support branch may run at, `support.pillar_angle_deg` (default 45). Reaching sideways by `lateral` costs `lateral * tan(angle)` of vertical drop, so a steeper angle buys stiffness and shrinks how many contacts have enough drop available to branch at all. |
| `--elephant-foot-mm` | Sets `process.elephant_foot_compensation_mm`: shrinks the exported bottom layers by this radius in mm, ramping linearly to zero. `0` disables it. Rejected above `1.0` mm. |
| `--elephant-foot-layers` | Sets `process.elephant_foot_layers`, the layer count the ramp runs over. `0` derives it from `process.bottom_layers`. |
| `--drop-attached-unroutable` / `--no-drop-attached-unroutable` | After routing, drop contacts that will not fit if they already have material one printer layer below. Island and manual contacts are never dropped. On by default. |
| `--support-void-policy {fail,ignore,fill}` | `fail` (default) keeps support-generated voids as export failures. `ignore` records support-class voids without failing; model-class still fails. `fill` seals enclosed shells after an exact union. |
| `--auto-supports` / `--no-auto-supports` | Automatic contact selection. Turning it off leaves only manual contacts; it does not disable supports. |
| `--tree-supports` / `--no-tree-supports` | Cluster nearby vertical plate supports onto one trunk. Off by default. |
| `--contour-supports` / `--no-contour-supports` | Also sample the outer perimeter of downward-face clusters. Off by default. |
| `--boundary-supports` / `--no-boundary-supports` | Also sample open mesh boundary edges (crop cuts). Closed solids add none. Off by default. |
| `--auto-bracing` / `--no-auto-bracing` | Automatic bracing, switched independently of contacts. |
| `--brace-spacing-mm` | Vertical spacing between downward brace origins, measured from each support shoulder, `support.brace_spacing_mm` (default 15 mm). |
| `--brace-diameter-mm` | Cross-brace diameter, `support.brace_diameter_mm`. `0` (default) derives it from the thinner of the two connected pillars. |
| `--brace-max-distance-mm` | Farthest a pillar neighbour may be and still be braced, `support.brace_max_distance_mm`. `0` (default) derives `1.5 * spacing_mm`. |
| `--brace-max-length-mm` | Maximum complete downward diagonal brace length, `support.brace_max_length_mm` (default 30 mm). Candidates that cannot reach a valid configured destination are omitted. |
| `--brace-destination {supports,base,both}` | Restrict brace destinations to grounded supports, new base feet, or both. `both` prefers supports and is the default. |
| `--brace-pattern {single,alternating,x}` | Use single diagonals, alternate direction by vertical level, or paired X diagonals between reciprocal vertical shaft spans. Base landings fan in every pattern. |
| `--brace-branches-per-node N` | Maximum distinct connections per vertical spacing interval, from 1 to 8. Shared incoming connections count; an X pair uses one neighbor slot. |
| `--brace-angle-deg DEG` | Downward branch angle from horizontal, strictly between 0° and 90°; default 45° gives equal horizontal travel and vertical drop. |
| `--brace-min-height-mm` | Minimum brace origin height above the plate, `support.brace_min_height_mm`; default 0 allows every shoulder-derived level. |
| `--brace-azimuth-deg` | Rotate base landing fans and the alternating direction axis around Z, `support.brace_azimuth_deg`; default 0°. |
| `--part-to-part-supports` / `--no-part-to-part-supports` | Allow or forbid primary support anchors on model material. Braces always require a support-only path to the plate or generated base. |
| `--part-to-part-avoidance VALUE` | Route preference from `0` (equal length competition) to `1` (historical plate preference); intermediate values require a proportionally shorter model route. |
| `--peel-analysis` / `--no-peel-analysis` | Enable or skip the uncalibrated downward-surface peel advisory. Skipping reports `not_run`. Thresholds use `--set peel.KEY=VALUE`. |
| `--seal-voids` / `--no-seal-voids` | Fill enclosed cavities. On by default. |
| `--min-orifice-area-mm2` | Drainage bottleneck threshold. Equality passes. `0` disables the check. |
| `--repair {none,conservative,aggressive}` | Repair strategy. `aggressive` is voxel repair and must be asked for. |
| `--max-deviation-mm` | Allowed repair deviation, verified in both directions against the original surface. |
| `--repair-voxel-mm` | Explicit voxel pitch for aggressive repair; `0` derives it from the deviation limit. |
| `--clip-to-build-volume` / `--no-clip-to-build-volume` | Sets `assembly.clip_to_build_volume`. Off by default: a part outside the usable envelope is refused. On: place and export it anyway, discarding geometry the printer cannot reach and recording exactly how much. Nothing is ever scaled. |
| `--memory-gib`, `--workers`, `--scratch-dir` | Resource budget. Exceeding it is an error, never a silent coarsening. `--workers auto`, or `resources.workers = 0`, derives the count from the machine: one per physical core, capped at the measured parallel plateau of 8. That is the default. |
| `--worker-policy {performance,efficiency,all}` | Which class of core to pin workers to on a hybrid CPU. `performance` (default) keeps a foreground run off the efficiency cores; `efficiency` leaves the fast cores free for an interactive session; `all` declines to pin. Workers are always spread across distinct physical cores, never stacked onto one core's SMT siblings. |
| `--acceleration {auto,cpu,cuda}` | Raster morphology backend. `auto` (default) uses CUDA after a successful runtime and device probe, otherwise CPU. `cuda` is refused when no device is available rather than silently falling back. |
| `--cuda-device N` | Zero-based CUDA device used when acceleration selects CUDA. |
| `--report PATH`, `--progress` | Report destination, and progress on stderr. |

The destination, pattern, per-node limit, angle, minimum-height, and azimuth
brace options are additions in **0.5.4**. The 0.5.3 release record covers the
earlier shoulder-branch controls only.

Precedence runs defaults → printer → matching resin process → explicit options,
so a flag always beats a profile. `voxelmill profile` prints the result of that
resolution without touching a mesh, which is the quickest way to see what a
combination actually does.

## `import-step`

    voxelmill import-step part.step --output part.stl \
      --set repair.step_linear_deflection_mm=0.1 --report output/step.json

Tessellates a STEP (`.step` / `.stp`) solid to STL through headless FreeCAD.
VoxelMill shells out to the AppImage; it never imports FreeCAD into the
process. Linear deflection comes from
`repair.step_linear_deflection_mm` (default `0.1` mm). Angular deflection is
fixed at 15 degrees. The JSON report records the engine path, FreeCAD version
when available, both deflections, triangle count and axis-aligned bounds in
millimeters. Failures raise `VoxelMillError` with code `step_import`. Requires a
local FreeCAD AppImage (see `gotchas.md`).

## `inspect`

    voxelmill inspect inputstl/*.stl --report output/inventory.json

Full-resolution inventory of one or more meshes: hash, triangle count, bounds,
components, invalid triangles, boundary edges, nonmanifold regions and
self-intersections. `--no-self-intersections` skips the exact predicate pass,
which is the expensive part. Nothing is modified and nothing is written beside
the report.

## `prepare`

With automatic rotation, `--candidates 5` prints the available ranked finalists
and weighted score contributions to stderr. `--candidate-rank 2` chooses the
second candidate (ranks start at 1). Both require `--rotate auto`; count is 1–32
and defaults to 5. A finite search may find fewer distinct feasible poses;
requesting an unavailable rank is a structured error. Specifying either option
stores the exact selected angles in the `.voxmil` project so reopening preserves
that choice. JSON reports include every candidate under
`placement.search.ranked_candidates`, including its exact pose, bounds,
assessment, term values, weights and counted contributions. Weights are
uncalibrated; actual routed support volume is not measured by this ranking.


    voxelmill prepare inputstl/left_temporal_bone_mars5_oriented.stl \
      --printer profiles/mars5-ultra.ptr --resin profiles/sunlu-abs-like-gray.res \
      --rotate auto --center-offset 0 0 --seal-voids \
      --output output/left-supported.stl --report output/left.json

Places the mesh, builds supports and a raft, unions them, then reopens the
export and reslices it independently before accepting it.

| Option | Effect |
| --- | --- |
| `--rotate RX RY RZ` | Extrinsic X, Y, Z degrees about the original bounding-box center. The word `auto` runs the orientation search. Omitted preserves the incoming orientation. Refused together with a nonidentity `--scale`/`--mirror`; see below. |
| `--scale FACTOR [FACTOR FACTOR]` | One uniform factor or three per-axis factors (`X Y Z`). Must be positive — mirroring is `--mirror`, never a negative scale — and within `[MIN_SCALE, MAX_SCALE]` (0.01 to 100.0; see [geometry.md](geometry.md#scale-and-mirror)). A part is never scaled unless this is given. |
| `--mirror AXIS [AXIS...]` | Mirror the model on `x`, `y` and/or `z`. Reverses triangle winding on the affected axes so the part stays solid; see [geometry.md](geometry.md#scale-and-mirror) for why that matters. |
| `--center-offset X Y` | Final rotated bounding-box center relative to plate center. |
| `--model-lift-mm` | Gap between plate and the lowest model point. Defaults to 5. |
| `--contacts PATH` | JSON array of manual `[x, y, z]` contacts in final plate coordinates. |
| `--paint PATH` | JSON `{blocked, enforced}` arrays of plate-coordinate centroids. Block drops automatic contacts on those faces; enforce always places them. Island births are never blocked. Nothing is painted automatically. A `.voxmil` input carries paint per object in each object's own frame instead, and both forms are accepted; when `--paint` is written into a saved project its marks are recorded against the primary part, which is exact for a single part and is stated in the report when added parts are present. |
| `--add-model PATH` | Another STL on the same plate. Repeatable. Only model-solid intersections are collisions; support envelopes may overlap and the planner treats every part as one field. Pose is identity rotation, no XY offset, 5 mm lift. |
| `--add-model-spec JSON` | JSON object or array of added models with `path`, `rotate [RX,RY,RZ]`, `center_offset [X,Y]`, `lift_mm`, `scale [X,Y,Z]`, `mirror [X,Y,Z]`, and optional `overrides.support` (a support-key overlay for that part only). Repeatable. The object panel's per-part controls cover rotate, offset, lift and the attachment overlay; scale and mirror per added part are set only here or in a `.voxmil` project, not from the editor. |
| `--contact-parameters PATH` | JSON array of `{position_mm, parameters}` records. `parameters` may contain only per-contact geometry keys (tip, pillar, anchor, small-pillar). Global defaults stay unchanged; unmatched positions are reported and not applied. |
| `--tip-shape {cone,cylinder}` | Top contact shape for every support that does not have its own override. |
| `--break-point-diameter-mm` | Optional ball at the top contact for a controlled snap-off. `0` disables it. |
| `--components` | Also write separate model, support and raft STLs. |
| `--project PATH` | Write a `.voxmil` archive of settings, hashes, transforms, edits and validation. |
| `--max-passes N` | Cap on targeted correction passes, 1 to 10; defaults to `support.max_island_passes` (default 5). Each pass adds contacts under any island the reslice found and stops early on success or on no progress; see [algorithms.md](algorithms.md#island-correction-passes). |
| `--allow-unresolved` | Keep the export even though validation failed. The report still records the failure and every diagnostic. |
| `--no-drainage`, `--no-void-analysis` | Skip a check rather than pass it. The check reports `not_run`. |
| `--no-overhang-check` | Skip the unsupported-overhang coverage check; it reports `not_run`. This check is advisory (a warning) and never blocks an export on its own; see [algorithms.md](algorithms.md#unsupported-overhangs). |
| `--drop-attached-unroutable` / `--no-drop-attached-unroutable` | After routing, drop contacts that will not fit if they already have material in a 3×3 printer-pitch neighbourhood one layer below. Island and manual contacts are never dropped. On by default. |
| `--support-void-policy {fail,ignore,fill}` | `fail` (default) keeps support-generated voids as export failures. `ignore` records support-class voids and bottlenecks without failing; model-class still fails. `fill` seals enclosed shells after an exact union; drainage necks are not shells. |
| `--no-memory-limit` | Do not set an address-space ceiling for the run. |

Scale is never changed to make a part fit. A failed search is reported as
`no_feasible_placement`, which means the finite search found nothing, not that
no orientation exists — unless `--clip-to-build-volume` is on, in which case
`prepare` keeps the unrotated pose instead of raising and records
`stages.build_volume.mode: auto_search_exhausted`.

`--scale`/`--mirror` are model decisions, not machine settings: the resolved
factors and flips are carried on `Placement.scale`/`Placement.mirror` and
recorded in `report['placement']` and `stages.transform`, never in a `.ptr`
profile. A `--project` archive embeds that same placement as evidence, and,
like rotation, centering, lift and contacts (see [below](#prepare-an-edited-project-without-the-gui)),
reopening that archive with `voxelmill prepare edited.voxmil` restores it as
the default — give `--scale`/`--mirror` explicitly on that command line to
override the archive's transform instead. The editor's own `.voxmil` save/load
round-trips them the same way; see [gui.md](gui.md).

Nothing is ever scaled or mirrored unless one of these flags is given,
`--rotate auto` included: the orientation search does not consider scale or
mirror at all, so combining `--rotate auto` with a
nonidentity `--scale`/`--mirror` is refused outright with `invalid_placement`
rather than silently searching on the unscaled, unmirrored part. Give
explicit `--rotate RX RY RZ` angles when resizing or mirroring a part. This
is a current limitation of the search, not a design goal.

A run that does scale or mirror the part always records `stages.transform`
(`scale`, `mirror`, a human-readable `note`, `source_size_mm`,
`placed_size_mm`) and appends a `model_transformed` diagnostic at
**warning** severity — never blocking — so an export the user explicitly
asked for still proceeds; `prepare` also prints a one-line `WARNING:` banner
to stderr naming the transform. See [geometry.md](geometry.md#scale-and-mirror)
for the mirror/winding correctness argument and the scale guardrails.

`stages.build_volume` is always present: `clip_to_build_volume`, `fits`,
`overflow_mm` (per-axis millimeters, from `geometry.envelope_overflow_mm`),
`build_mm`, and `edge_clearance_mm`. It reflects the placement chosen, not
whether an export followed.

`stages.resin_usage` is also always present: the output of
`config.resin_usage(settings, raster_volume_mm3)`, where `raster_volume_mm3`
is the cured-volume figure the reslice's raster analysis measured (total
filled pixels times pixel area times layer height), which already includes
supports and any raft, not a mesh's signed volume. A soup assembly (the
raster-path fallback; see `assembly.union`) has no meaningful signed volume
at all, so the raster figure is the only physically grounded source
regardless of which assembly path ran. `resin_usage` reports `volume_mm3`,
`volume_ml`, `mass_g`, `cost`, `currency`, `density_g_cm3`, `cost_per_liter`,
and `source`; `mass_g`/`cost` (and the density/price fields that produced
them) are `null` when the resin profile leaves `density_g_cm3`/
`cost_per_liter` at `0.0`, meaning "not supplied" — see
[configuration.md](configuration.md).

`validation.metrics.support_collisions` audits the routed supports
independently of the router. The support solids are intersected exactly with
each part, and every piece away from a contact tip or model anchor (beyond
`allowance_mm`) counts as an `intrusion`. Graph capsules that overlap without
the graph joining them count as `support_overlaps`. Either one sets
`checks.support_collisions` to `warn` and adds a `support_model_intrusion` or
`support_overlap` diagnostic with the worst location. It is a warning, not a
gate, and on the raster union path, with no exact part solid, the metrics say
`not_run` and no check is added.

## `measure`

    voxelmill measure inputstl/left_temporal_bone_mars5_oriented.stl \
      --printer profiles/mars5-ultra.ptr --scale 1.2 --mirror x \
      --report output/left-measure.json

Sizes a part has, and would have under a proposed pose, scale and mirror.
Writes nothing — no STL, no `.voxmil` — and touches no output path; it is the
question a scale flag actually raises, answered without running a
preparation. `--rotate RX RY RZ`, `--scale`, `--mirror`, `--center-offset`
and `--model-lift-mm` take the same values as the matching `prepare` flags,
defaulting to no rotation, no scale, no mirror, plate center and a 5 mm lift.
`--rotate auto` is refused with `invalid_option`: a search result is not a
measurement of a pose the caller chose, and `measure`'s whole point is to
report the size of a pose that was actually asked for.

The payload reports, for the requested pose:

| Field | Meaning |
| --- | --- |
| `source_size_mm`, `source_center_mm` | Size and center of the untransformed mesh, from its own bounds. |
| `placed_size_mm`, `placed_bounds_mm` | Size and bounds after scale, mirror, rotation, centering and lift. |
| `diagonal_mm` | Norm of `placed_size_mm` — one number for "will this fit through a doorway"-style checks. |
| `scale`, `mirror`, `transform_note` | The resolved per-axis factors and flips, and `scale_note`'s one-line hazard summary (`null` at the identity). |
| `fits`, `overflow_mm` | Whether the placed bounds fit the usable envelope, and by how much they don't, from the same `envelope_overflow_mm` `prepare` uses. |
| `usable_build_mm` | The clearance-reduced X/Y extents and the machine height. |
| `layers_at_current_height` | `placed` top-Z divided by `process.layer_height_mm`, rounded up. |
| `establishes`, `does_not_establish` | States the boundary directly: this establishes the size and plate fit of this pose; it does **not** establish printability — run `prepare` and `validate` for that. |

`--target-mm X Y Z` answers the question in reverse: given a wanted size,
what scale factor reaches it. `0` on an axis leaves that axis unconstrained
rather than demanding zero size, so a part can be sized on one critical
dimension alone; all axes zero is rejected. The response adds
`per_axis_factor` (the factor each constrained axis alone would need) and
`uniform_factor`, which is the **smallest** of the per-axis factors — scaling
up to the largest one would overshoot every other constrained axis. Neither
factor is applied; `--target-mm` only computes what `--scale` would need to
be on a follow-up `measure` or `prepare` call.

A transform recorded here is a warning, never blocking: if `transform_note`
is non-`null`, `measure` prints the same one-line `WARNING:` banner
`prepare` does. See [geometry.md](geometry.md#scale-and-mirror) for what the
guardrails and the mirror/winding correctness point actually are.

## `validate`

    voxelmill validate output/left-supported.stl --report output/left-check.json

Reslices an existing STL and checks it without preparing anything: closed
surface, plate fit, layer connectivity, voids and drainage. Use it to check a
file this tool did not produce, or to recheck one after an external edit.

## `slice`

    voxelmill slice output/left-supported.stl --output output/left.goo \
      --printer profiles/mars5-ultra.ptr --resin profiles/sunlu-abs-like-gray.res

Validates the prepared STL and writes a GOO file, then reopens it and compares
every decoded pixel and per-layer timing against a freshly sliced source before
the final rename. A `.ctb` destination writes unencrypted CTB v3: the GOO is
verified first, then converted without resampling, and the GOO staging file is
deleted. Encrypted CTB and v4/v5 are not written. With `--print-time-s` at the default `0`, the header
`PrintTime` is filled from an uncalibrated schedule estimate (per-layer
exposure, process settle/rest/wait, and motion lift/retract travel) and the
report carries that estimate under `estimated_print_time`. A nonzero
`--print-time-s` still overrides the header and skips that estimate.

With `--clip-to-build-volume` off (the default) a part outside the usable
envelope makes `slice` fail with `goo_envelope`, whose details carry
`overflow_mm` and an `enable_with` hint. On, the export proceeds against the
physical LCD panel and machine height, and `validation.metrics.clipped`
records `overflow_mm`, `clipped_to` (panel/machine-height vs. the smaller
usable envelope the overflow is measured against), `clipped_triangles`,
`clipped_layers`, and the exact pixel counts from one extra raster pass —
`source_pixels`, `retained_pixels`, `clipped_pixels`,
`clipped_pixels_above_machine_z`; see [algorithms.md](algorithms.md). The
`clipped_geometry` diagnostic this adds is error-severity, so the export is
still withheld unless `--allow-unresolved` is also given.

The written report's `verification.layer_topology` records
`equivalent_to_source` instead of re-running the layer analysis over the
decoded frames: every decoded LCD pixel was already proved equal to the source
raster above, and that raster was analyzed in the same report, so repeating the
analysis over full-panel frames would only recompute a provably identical
answer. `voxelmill verify` is the command that runs the analysis when there is
no source raster to lean on.

The payload's `resin_usage` field is the same `config.resin_usage` output
`prepare`'s `stages.resin_usage` carries, computed from
`validation.metrics['raster_volume_mm3']` — the raster volume measured
during this same reslice, so it already includes supports and any raft, and
is not a mesh's signed volume. It is `null`-valued the same way: `mass_g`
and `cost` are `null` whenever the resin profile leaves density or price
unsupplied. The GOO header's `material_grams`, `material_cost` and
`price_currency` fields are filled from the same computation; see
[configuration.md](configuration.md).

The payload's `elephant_foot` block reports what first-layer compensation
actually did to this export: `compensation_mm` and `layers_requested` echo
the resolved settings (`layers_requested` already applies the
`elephant_foot_layers=0` → `bottom_layers` fallback), `layers_compensated`
is how many layers the ramp actually shrank (`0` when compensation is off or
rounds to zero pixels), `pixels_removed` sums the removed pixels across every
compensated layer, and `per_layer` lists one record per compensated layer
with its `radius_px` and exact pixel counts. `resin_usage` and the layer
analysis in `validation` both describe the *uncompensated* raster — see
[algorithms.md](algorithms.md#elephant-foot-first-layer-compensation) for why
that raster figure, not the smaller compensated exposure, is the one
reported. A request that would erase an entire layer raises
`goo_elephant_foot` before any file is written; see
[troubleshooting.md](troubleshooting.md#goo_elephant_foot).

The payload's `dimensional_compensation` block reports slice-time XY
shrinkage and tolerance: the resolved
`shrink_percent_xy` / `tolerance_offset_mm` /
`bottom_tolerance_offset_mm`, a `shrink_percent_z` sub-record that is
always `applied: false` when nonzero (Z would change the layer count),
`uncalibrated` whenever any of those settings is nonzero, and
`pixels_changed` / `per_layer` for layers whose occupancy actually moved.
Set them with `--set process.shrink_percent_xy=1.5` (and siblings). See
[algorithms.md](algorithms.md#xy-shrinkage-and-tolerance-compensation).

## `info` (aliases `goo-info`, `ctb-info`)

    voxelmill info output/left.goo --verify --report output/goo.json
    voxelmill info output/left.ctb --verify

Reads the header and layer count of a GOO or classic unencrypted CTB v3 file;
the format comes from the suffix, and the report names it in `format`.
Encrypted CTB payloads and CTB v4/v5 are rejected at open time. `--verify`
decodes every layer and checks framing and checksums, which costs a full pass
over the file. This is not the topology analysis; use `verify` for islands
and voids. The old `goo-info` and `ctb-info` names still work, for either
format.

## `convert`

    voxelmill convert input.goo output.ctb --report output/convert.json
    voxelmill convert input.ctb output.goo --report output/convert.json

Converts GOO v3 to unencrypted CTB v3, or the reverse, without resampling.
Source pixels and physical panel size must match the selected printer profile.
CTB output is unencrypted v3 only. A successful `.ctb` destination is
deep-checked with `verify_ctb`.

## `verify`

    voxelmill verify output/left.goo --report output/left-verify.json
    voxelmill verify output/left.ctb --report output/left-verify.json

Deep-checks a finished GOO or unencrypted CTB v3 using only that file, with no reference to a source
mesh. Every layer is decoded, unmirrored back to plate coordinates, and fed as
`Layer(index, z_mm, frame)` records into the same `analyze_layers` the STL path
uses, on a grid built from the header's `resolution_x`/`resolution_y` and
`display_width`/`display_height`. `z_mm` comes from each layer's own
`position_z` field. Unmirroring matters because layer topology survives an
axis flip but a diagnostic's `position_mm` does not.

Where a setting can be read from the file — `printer.pixels`,
`printer.build_mm`, `printer.pixel_pitch_mm`, `printer.image_mirror_x/y`,
`process.layer_height_mm`, `process.bottom_layers`, `process.transition_layers`,
`process.normal_exposure_s`, `process.bottom_exposure_s` — it is taken from the
file, not from the supplied profile; those values appear in the payload's
`settings_from_file`. A disagreement between file and profile is listed in
`settings_mismatches`, raises a `goo_settings_differ` warning diagnostic in the
report, and prints a stderr banner. The file's values are the ones used for the
check either way.

GOO does not store the topology thresholds. `settings_from_caller` records the
resolved `support.min_overlap_pixels`, `support.max_span_mm`, and
`repair.min_void_volume_mm3`, including defaults when no override was supplied.
These values can change the verdict for identical file bytes; they are not
file/profile mismatches. `analysis_options.track_voids` records whether void
tracking ran, so a recorded void threshold does not imply that check ran.

`verify` takes `parents=[common]`, so every option in the settings table above
applies, unlike `info`. `--no-void-analysis` skips enclosed-void and
transient-trap tracking; both then report `not_run`.

By default the analysis runs on the window of the panel the file actually
exposes, plus a one-pixel border, rather than the full LCD lattice above:
`GooLayerStream` is built with a `crop` window from `occupied_crop`, which
costs one extra decode pass over the file and removes most of the labeling
cost of `analyze_layers` for any part that doesn't fill the panel. The result
is identical to the uncropped run — same checks, same metrics, same
enclosed-void count, same diagnostic positions — because the crop only moves
the grid's origin; `grid.xy` still returns plate millimeters. `--full-panel`
disables the crop and analyzes the whole LCD instead, at the cost of that
labeling time; a file that exposes nothing anywhere falls back to the full
panel and fails with `empty_raster` either way. `report.metrics.goo` records:

| Field | Meaning |
| --- | --- |
| `shape_px` | The file's own panel size. Not the analyzed window — see `analysis_window_px`. |
| `analysis_window_px` | Width and height of the window actually analyzed. Equal to `shape_px` under `--full-panel`. |
| `analysis_origin_px` | The window's column/row offset into the file's panel. `[0, 0]` under `--full-panel`. |
| `decode_passes` | `2` cropped (one to find the window, one to analyze it), `1` full-panel. |
| `window_note` | Which of the two ran, in words. |

The payload's `establishes` and `does_not_establish` lists state the boundary
of what this check proves: it establishes that the file opens, frames and
decodes, and that the decoded layers satisfy the configured layer topology
rules. It does **not** establish that these pixels came from any particular
source mesh, or that the machine motion or exposure values are calibrated. That
correspondence to a source mesh is exactly what `slice`'s post-write pixel
comparison proves at export time; `verify` is for the case where only the file
is available. Exit codes match `validate` and `slice`: `0` on a passing report,
`2` on a failing one.

## `batch`

    voxelmill batch islands inputstl/*.stl --output-dir output/islands-batch \
      --printer profiles/mars5-ultra.ptr --continue-on-error \
      --extra --max-examples 32

Runs one `OPERATION` over many `INPUT` files, one after another in this
process. `OPERATION` is one of `prepare`, `slice`, `validate`, `islands`,
`measure`, `inspect`, `verify` — the keys of `BATCH_OUTPUTS`. `info`,
`profile`, `resin`, `preset`, `completion`, `manpage`, `gui` and `batch`
itself cannot be batched. Each item is parsed by `build_parser()` — the same
subparser a direct `voxelmill OPERATION ...` call would use — and dispatched
to that operation's own `func`, so an option cannot exist for a single run
and not for a batched one. An argument that operation's parser rejects
raises `SystemExit`, which `batch` catches and records as an `invalid_option`
error on that item rather than letting argparse's own usage message end the
whole run.

Every item's report is written to `DIR/<stem>.<operation>.json`, where
`<stem>` is the input's filename without its suffix. Two inputs with the
same stem write to the same report path, and the later one silently
overwrites the earlier — the manifest still records both runs, but only one
report survives on disk. `prepare` and `slice` also get a named geometry
output; the other five operations write only their report, since none of
them produce geometry:

| Operation | Report | Geometry output |
| --- | --- | --- |
| `prepare` | `DIR/<stem>.prepare.json` | `DIR/<stem>-supported.stl` |
| `slice` | `DIR/<stem>.slice.json` | `DIR/<stem>.goo` |
| `validate`, `islands`, `measure`, `inspect`, `verify` | `DIR/<stem>.<operation>.json` | none |

`DIR/batch-manifest.json` is (re)written after every item and records, per
item, `input`, `report` (the path above), `argv` (the exact argument vector
that item ran with), `exit_code`, and `seconds` elapsed. The manifest as a
whole carries `requested` (inputs given), `ran` (items started), `failed`
(items with a nonzero exit code), `stopped_early` (`ran < requested`), and
an `isolation` field naming the limit below in words, and `manifest_path`,
the file it was written to. The manifest is also printed to stdout on every
run. `--report PATH` names a different destination for the manifest, and the
default `batch-manifest.json` is then not written; per-item reports always go
under `--output-dir` regardless.

`--extra ...` passes everything after it to each item, to be parsed by that
operation's own parser — an `--extra` flag an operation does not accept is
rejected the same way it would be on a direct call. `--report` and
`--output` are named by the batch for every item, so neither may appear in
`--extra`: either literal token anywhere in the pass-through list is
refused with `invalid_option` before any item runs.

`batch` re-emits its own settings for every item, so an item cannot resolve
different settings from the batch that launched it. `--printer`, `--resin`
and `--progress` are forwarded as themselves; every other settings flag is
folded into the same nested override dictionary `_overrides` builds for a
single run and re-emitted as `--set section.key=<json>` pairs. That includes
the boolean toggles (`--no-auto-supports`, `--seal-voids`,
`--clip-to-build-volume`) plus `--support-preset` / `--process-preset`, whose
section values are expanded inline. Deriving the list rather than enumerating
it is deliberate:
an enumerated table silently dropped every flag nobody remembered to add to
it, so a batch accepted a flag on its own command line and then ran every
item without it. Each item's `argv` in the manifest is the exact vector that
item ran with, so what was forwarded is a recorded fact rather than a claim.

Exit code `0` only when `failed` is `0` and `stopped_early` is false; `2`
otherwise, whether the failures were structured or the run stopped short.

Items share one process, not one process per item. A structured failure —
a `VoxelMillError`, or an operation's own nonzero exit — is recorded on that
item's `exit_code`/`error`, and the batch continues to the next input when
`--continue-on-error` is given (default: stop there). A process-level
failure — a crash or a killed worker in the native extension, for
instance — takes the whole batch process down with it; there is no
per-item subprocess boundary to contain it. When that happens the batch
simply stops, and whatever `batch-manifest.json` was written for the items
that finished first is the only evidence of how far the run got before it
did. The manifest's own `isolation` field states this in words, rather than
implying a stronger guarantee than one shared process provides.

## `boolean`

    voxelmill boolean a.stl b.stl --union --output joined.stl
    voxelmill boolean model.stl box.stl --intersect --output cropped.stl
    voxelmill boolean model.stl keepout.stl --subtract --output out.stl

Exact Manifold boolean on two STLs. Exactly one of `--union`, `--intersect`,
or `--subtract` is required; `--output` is required. Each input runs through
the same `assembly.prepare_model` repair path `prepare` uses, so
`--repair` / `--seal-voids` apply. Volumes and triangle counts for A, B and
the result are in the JSON report (`added_volume_mm3` / `removed_volume_mm3`
are relative to A). An input that cannot become a closed solid under the
current repair settings fails with `invalid_solid`.

## `trim`

    voxelmill trim model.stl --point 0 0 10 --normal 0 0 1 \
      --keep positive --output upper.stl

Cuts a mesh with a plane through `--point X Y Z` with `--normal NX NY NZ`,
keeps `--keep positive` (half-space along the normal) or `negative`, and
caps the open face so the result is a closed printable solid. Uses the same
prepare repair path as `boolean`. Report fields include before/after volume
and removed volume. The input must already be a closed solid; open cut
surfaces belong on `cap` instead.

## `cap`

    voxelmill cap open_cut.stl --output closed.stl
    voxelmill cap open_cut.stl --output closed.stl \
      --set repair.max_deviation_mm=0.1 --report output/cap.json

Fills boundary loops that lie within `repair.max_deviation_mm` of a best-fit
plane (SVD; default `0.05` mm). Exact-coordinate welding finds the loops;
each planar loop is triangulated with winding opposite the directed boundary
so the cap matches neighbouring face orientation. A closed input is a no-op
success. Loops that are not planar enough are refused with `cap_incomplete`
and counts of capped versus remaining loops — nothing is written. Does not
run voxel repair or invent a surface for a jagged hole. With the default
explicit repair policy, zero-area/non-finite input triangles are dropped and
counted as `dropped_invalid_triangles`; `--repair none` keeps strict rejection.

## `hollow`

    voxelmill hollow model.stl --output hollowed.stl \
      --set hollow.wall_thickness_mm=2.0 --report output/hollow.json

Voxel-hollows a solid: rasterize, erode inward by `hollow.wall_thickness_mm`,
then either subtract the eroded core from an exact solid or remesh the shell
occupancy. `hollow.mode` is `inner` (a closed cavity) or `bottom_open` (the
cavity is extended through the base of the part so it opens to the exterior
there instead of being sealed). By default a pair of drain/vent holes (`hollow.drain_diameter_mm`,
`hollow.vent_diameter_mm`) is added per enclosed cavity so trapped resin has
somewhere to go; `--no-holes` keeps the cavity sealed and skips them.
`hollow.infill` (`none`, `grid`, `hex`, or `gyroid`) fills the cavity with a
lattice at `hollow.infill_pitch_mm` instead of leaving it open, unioned
before the holes punch through. `hollow.voxel_size_mm` of `0` derives a pitch
from the wall thickness; `hollow.min_wall_thickness_mm` is a floor the
`thickness` command checks against, not itself a repair parameter. This is
geometry only: it does not certify that the resulting wall will survive a
print.

## `thickness`

    voxelmill thickness model.stl --report output/thickness.json

Reports minimum wall thickness from a voxel occupancy distance transform,
without modifying anything. `--threshold-mm` names a thin-region cutoff;
omitted, it defaults to `hollow.min_wall_thickness_mm`. The check reads
`pass` when every measured region clears the threshold, `warn` when a region
does not, and `not_run` when the analysis could not complete. Like every
other voxel measurement in this tool, it is a quantized distance-transform
estimate, not a certified minimum.

## `calibrate`

    voxelmill calibrate exposure --range 2:6 --steps 9 --output output/exposure.goo
    voxelmill calibrate tolerance --range -0.05:0.05 --steps 9 --output output/tolerance.goo

Writes an offline calibration GOO whose layers carry a spatial grid of
patches, each one a different parameter value, plus a JSON report describing
the grid so a printed result can be read back against it. Neither subcommand
contacts a printer or reads a source mesh. `exposure` varies exposure time
(a RERF-style gray-level matrix) across `--range LO:HI` in `--steps` cells;
`tolerance` instead varies a morphological offset, encoded as patch size.
`--layers` (default 2) stacks identical layers so the print has enough
thickness to handle and measure. `--output PATH` is required; `--report`
writes the accompanying JSON elsewhere than stdout. This produces a physical
test print to measure, not a calibrated result by itself; see
[calibration.md](calibration.md).

## `islands`

    voxelmill islands output/left-supported.stl --report output/islands.json

Island-only connectivity scan of an STL: `pipeline.scan_islands` runs the same
`analyze_layers` pass `validate` does, with void tracking switched off, so it
gets `raster_connectivity`, `overlap` and `growth_span` for free and skips the
`enclosed_voids`/`transient_traps` empty-space search — the expensive part of
a full validation. `--max-examples` (default 64) caps the `islands` list of
reported positions; `island_count` is never capped, and `truncated` says
whether positions were dropped from the list.

This is **not** a substitute for `validate`. The payload's `other_checks`
carries every check this pass ran, each stating its own status including
`not_run` — `enclosed_voids` and `transient_traps` read `not_run` because
void tracking was off, while `overlap` and `growth_span` read a real
`pass`/`fail` from the same pass. `not_examined` is a different list: checks
this pass never attempted at all — `drainage_bottlenecks`, `support_routes`,
`plate_fit`, `union_raster_parity` — so it has no status to report, not even
`not_run`. `voxelmill validate` is the command that runs all of them.

Exit codes match `validate` and `slice`: `0` when `check` (raster
connectivity) and `closed_surface` both pass, `2` otherwise. When
`island_count` is nonzero, a warning goes to stderr naming the checks this
scan does not examine, so a `2` here is never mistaken for output from a full
validation. The editor's persistent island badge runs the identical
`island_summary` over the in-memory assembly instead of a file; see
[gui.md](gui.md).

## `profile`

    voxelmill profile --printer profiles/mars5-ultra.ptr \
      --resin profiles/sunlu-abs-like-gray.res

Prints the fully resolved settings, the exposure schedule (including the
interpolated transition layers), and `resin_usage_per_ml` — the same
`resin_usage` computation `prepare`/`slice` write, evaluated at exactly 1 mL
so `mass_g`/`cost` read as per-milliliter figures, `null` when the resin
profile supplies no density or price. No mesh, no output file.

`profile` takes an optional action, `show` (default), `list`, `diff`, or
`save`:

| Action | Flags | Effect |
| --- | --- | --- |
| `show` | `--provenance` | Adds a `provenance` key: which resolution layer (`default`, `printer`, `resin`, `override`) last set each resolved leaf. |
| `list` | `--kind {printer,resin}` | The discoverable profile library: search path and every profile found, with shadowed files and parse errors named rather than hidden. |
| `diff` | `--against PROFILE`, `--kind {printer,resin}` | Leaf-by-leaf differences against another profile identifier/path or the literal word `defaults`. A leaf present on only one side reports as `"<absent>"`. |
| `save` | `--output PATH`, `--name NAME`, `--hardware-only` | Writes the resolved settings as a `.ptr`, stages and round-trips it before replacement, and reports omitted sections. `--hardware-only` writes only printer hardware settings; process, support, resin, repair, assembly, and resources inherit when loaded. |

`--printer`/`--resin` accept a library identifier as well as a path for
every one of these actions. Full detail, including the search-path priority
order, the path-vs-identifier rule, how provenance is computed, and the
editor equivalent, is in [profiles.md](profiles.md).

## `resin`

    voxelmill resin show sunlu-abs-like-gray
    voxelmill resin save --printer mars5-ultra --resin sunlu-abs-like-gray \
      --output profiles/my-resin.res --name "My resin"
    voxelmill resin bind sunlu-abs-like-gray --from mars5-ultra \
      --to my-other-printer --output profiles/sunlu-for-my-other-printer.res

Inspects a resin profile's bound printers, saves the resolved resin for the
current printer, or copies one printer's process block onto another printer id
within the same resin file. Action is the first positional argument: `list`
(default), `show`, `save`, or `bind`; a second
positional argument or `--resin` names the resin identifier or path.

| Action | Flags | Effect |
| --- | --- | --- |
| `list` | (none; `resin` has no `--kind` flag) | The resin subset of the profile library — equivalent to `voxelmill profile list --kind resin`. |
| `show` | | `resin` identity, `bound_printers` (sorted printer ids its processes cover), and the full `processes` table. |
| `save` | `--printer PATH_OR_ID`, `--resin PATH_OR_ID`, `--set ...`, `--output PATH` (required), `--name NAME` | Writes a standalone `.res` containing the resolved resin metadata and the current printer's `process` and `support` blocks. The staged file is resolved again with the current printer before replacement. |
| `bind` | `--from PRINTER_ID`, `--to PRINTER_ID` (required), `--output PATH` (required) | Copies `processes[--from]` verbatim onto `processes[--to]` and writes the result to `--output`. `--from` defaults to the resin's only bound printer; omitting it when more than one is bound is an error naming the printers it does bind. |

`bind` copies the process block exactly as written — exposures, layer
height, support geometry — onto the new printer id. The result states this
directly: the carried-over exposure is a **starting point**, **not a
calibration for the target machine**. `bind` also refuses to write over the
resin profile it just read: `--output` resolving to the same file as the
source is an error, not an in-place update. See [profiles.md](profiles.md).

## `completion` and `manpage`

    voxelmill completion bash --output ~/.local/share/bash-completion/completions/voxelmill
    voxelmill manpage --output voxelmill.1

Both are generated from the live `build_parser()`, in `shellhelp.py` — there
is no checked-in completion script or man page to fall out of date. A
subcommand or flag that exists in the parser appears in the next
`completion`/`manpage` output automatically.

Neither takes the settings parser common to every other command — no
`--printer`, `--resin`, `--set`, and so on: the output depends only on the
parser's structure, not on resolved settings. Both write plain text, not
JSON, unlike every other command, and both write to stdout by default and
to `--output FILE` when given (the same shape as every other command's
`--report`, named `--output` here because the payload is something to
install or pipe onward rather than a report to read).

`completion {bash,zsh,fish}` prints a completion script for the named
shell. To install one:

| Shell | Install |
| --- | --- |
| bash | Source it (`source <(voxelmill completion bash)` in `~/.bashrc`), or drop it in a completion directory: `voxelmill completion bash --output ~/.local/share/bash-completion/completions/voxelmill` |
| zsh | Write it to a file named `_voxelmill` (the `#compdef voxelmill` line at the top is what zsh's completion system keys on) somewhere on `$fpath`, then run `compinit`: `voxelmill completion zsh --output ~/.zfunc/_voxelmill` |
| fish | Write it into fish's own completions directory: `voxelmill completion fish --output ~/.config/fish/completions/voxelmill.fish` |

`manpage` prints a roff man page. `--section N` sets the manual section
number in the `.TH` line (default `1`). Install it under a `man1`
directory and refresh the index —
`voxelmill manpage --output ~/.local/share/man/man1/voxelmill.1 && mandb` —
or render it without installing: `voxelmill manpage | groff -man -Tascii |
less`.

The man page's `NAME` line uses a fixed program summary
(`shellhelp.DEFAULT_SUMMARY`, "prepare, support, slice and verify a single
part for a masked stereolithography printer") rather than the parser's own
`description`. `build_parser()` sets `description` from
`__doc__.splitlines()[0]` — this module's first docstring line, "Command
line entry point." — which names the file, not the program; a man page's
`NAME` line is what `apropos`/`man -k` search, so `manpage` supplies a real
summary instead of that line.

## `support-example`

    voxelmill support-example --height-mm 20 --layout array --output output/support-example.stl

Builds the attachment illustration used by the support editor. `--layout array`
(the default) uses four contacts; `--layout part-to-part` uses a broad lower
and upper model platform to demonstrate model anchors. Both obey the caller's
settings exactly, so a dimension edit is comparable before and after, which
also means `part-to-part` routes nothing when `allow_part_to_part` is turned
off. Its platform reaches past a branch's reach, so every contact lands on it.

`--layout showcase` exists for the opposite reason. It spreads six contacts
over five stations, each shaped to force a different route, so every kind the
router can emit is visible at once: a clear column to the plate (`vertical`),
a low blocker with a free neighbour (`branched`), a tall platform
(`model_anchor`), a post stopping just under the bar (`small_model_pillar`),
and a floating slab with nothing beneath it (raster island). The plate-route
station is a pair rather than a single contact, because two grounded pillars
close together give the brace network somewhere to land even under
`brace_destination=supports`, which accepts only an already grounded node.

To do that it forces six settings, copying the caller's settings rather than
editing them, and reports the ones that actually differed under `overrides`:
`auto_bracing=true`, `allow_part_to_part=true`, `part_to_part_avoidance=0.4`,
`small_pillar_mode="model"`, `small_pillar_diameter_mm=0.6`,
`small_pillar_max_length_mm=2.5`. Avoidance is deliberately not 0: at 0 the
shorter route always wins, a model route around any obstruction is always
shorter than branching past it, and the branch case would never appear.

Brace *geometry* is deliberately not forced, so your own destination, pattern
and angle choices stay visible in the picture. That means the five route kinds
are guaranteed while the brace count is not: a tuning that reaches nothing to
land on shows no braces, which costs no route. The showcase also analyses at
the production column pitch rather than the 0.5 mm the older layouts keep,
since the surface an anchor lands on is read from that raster.

Every layout reports a `categories` count per support kind. `--height-mm`
accepts 3–160 mm, except for `showcase`, which needs at least 8 mm and refuses
anything shorter: below that the bar is shorter than the fixed features
standing in it (a 2 mm tip and a 2 mm bottom connector), the branch station
stops branching, and the layout would quietly show four kinds instead of six.
`--output` optionally writes its illustrative STL. It uses the production router and reports routing and brace
evidence, but performs no print validation, drainage certification, or strength
proof. The output is a visual example only.
The sample accepts support spacing from 1–30 mm; larger or smaller valid project
settings are still usable by `prepare`, but this bounded fixture rejects them.
Use `--base-type plate|none|pad|skate|skeleton|grid|hex|triangle` or the equivalent
`--set support.base_type=...`. The selected base and its measured geometry
evidence are included in the JSON report. Skate, skeleton, grid and hex also
honor their `base_*` settings through `--set`, including
`support.base_edge_slope_deg` for a base that tapers inward from the plate;
values are preserved in portable support presets.

Use `--layout part-to-part --part-to-part-supports --part-to-part-avoidance 0`
to select the lower/upper model-gap example and explicitly enable its primary
model anchor. The bracing controls still apply to the selected layout, and
brace destinations never use model material.

`--model-anchor-shape cone|cylinder` selects the independent bottom connector.
Defaults are `model_anchor_length_mm=2`, `model_anchor_diameter_mm=0.4`, and
`model_anchor_penetration_mm=0.15`. Override with `--set` as needed. Zero length
keeps the direct bottom; zero diameter derives the selected middle diameter.
Top-tip settings stay independent. Enable `--part-to-part-supports` explicitly,
then use `--set support.part_to_part_avoidance=0` in the example to make its
available model anchor compete with the plate route. Braces remain support-only
even when this primary-routing option is enabled.

`--small-pillar-mode middle|model` chooses a thin middle segment or an entire
short model-to-model connector. Both use `support.small_pillar_diameter_mm`
and `support.small_pillar_max_length_mm`; in model mode the latter limits the
whole gap and zero disables selection. `--small-pillar-shape cone|cylinder`
controls model-mode buried ends, with independent
`support.small_pillar_upper_depth_mm` and `support.small_pillar_lower_depth_mm`.
All these options also apply to `prepare`, `preset save`, and other commands
using the common settings stack.

## `gui`

    voxelmill gui inputstl/left_temporal_bone_mars5_oriented.stl \
      --printer profiles/mars5-ultra.ptr

Opens the editor with the same settings stack and the same core services as the
CLI. Needs the `gui` extra. See [gui.md](gui.md).

| Option | Effect |
| --- | --- |
| `--view {front,back,left,right,top,bottom,iso}` | Sets the initial camera view on startup, same views as the `View` menu. Front is the −Y face the green plate edge marks. |
| `--goo PATH` | Opens this GOO file for layer inspection on startup, as if **File → Open GOO or CTB for inspection…** were used immediately after launch. |
| `--screenshot PNG` | Writes a PNG of the editor window, 3D view included, then exits with the capture's status. |
| `--screenshot-delay-ms N` | How long to let the window settle first; default 1500. |

`--screenshot` composites two sources. `QWidget.grab` renders the Qt tree
through Qt's own painter, which needs no permission but cannot see inside the
native VTK child, so the render window is read back separately with
`vtkWindowToImageFilter` and drawn into place (scaled by the pixmap's device
pixel ratio, so a Retina window lands correctly). `QScreen.grabWindow` would
capture both at once but goes through the window server, which on macOS means
Screen Recording permission a process started over SSH cannot be granted.
Because the app captures its own window, this works over SSH on every
platform. Losing the 3D content is reported on stderr and does not fail the
capture; failing to write the PNG exits nonzero.

Empty argv (and a single existing file path) rewrites to `gui` when the binary
is frozen or PySide6 imports. On Windows VirtualBox guests, `cmd_gui` sets
`QT_OPENGL=software` before `QApplication` when the `VBoxGuest` service is
present; a real GPU and an already-set `QT_OPENGL` are left alone.

## `monitor`

Launches the read-only printer monitor without opening a model or requiring an
active print. Supply a known host and SDCP mainboard ID, or use **Discover** in
the window for explicit LAN discovery:

```sh
voxelmill monitor --host 192.168.1.71 --mainboard-id ba95517be2550100
voxelmill monitor --demo
```

The monitor refreshes status/attributes, displays release-film and device
telemetry, opens the printer-supplied RTSP stream (with an FFmpeg/UDP fallback),
loads print history, and downloads available time-lapse videos. It never uploads,
starts, pauses, cancels, moves, or changes printer settings. `--demo` uses the
loopback simulator and performs no network operations.

### Assembly policy

`prepare` defaults to `--set assembly.union=auto`: try the exact solid union,
then report a grouped raster fallback for geometric rejection. `--repair none`
skips solid conversion. `--set assembly.union=exact` refuses fallback with a
structured `exact_union_unavailable` error. Raster runs print a warning on
stderr and keep the ordinary validation exit codes (0 pass, 2 failed checks).
The report and `.voxmil` archive preserve assembly findings and reopened-STL
parity evidence. `--set assembly.max_parity_examples=16` bounds position examples;
`--set assembly.require_raster_parity=false` records a missing check, so a warned
export still needs `--allow-unresolved`.

## Portable support and process presets

```sh
voxelmill preset list
voxelmill preset show heavy
voxelmill preset save --support-preset heavy --support-spacing-mm 2.5 --name my-heavy --output my-heavy.json
voxelmill prepare part.stl --support-preset my-heavy.json --output supported.stl

voxelmill preset list --kind process
voxelmill preset show fine --kind process
voxelmill preset save --kind process --name myfine --process-preset fine --output myfine.json
voxelmill prepare part.stl --process-preset fast --output supported.stl
```

`--support-preset` and `--process-preset` accept a built-in name or JSON path on
every command that resolves settings. Resolution is profiles, support preset,
process preset, convenience flags, then `--set`. `preset` defaults to
`--kind support` so existing support commands stay unchanged; `--kind process`
lists, shows or saves the process table only. Presets are portable version-1
JSON and reject unknown fields. Details: [support-presets.md](support-presets.md).

`prepare --removed-contacts positions.json` suppresses automatic contacts near
the listed plate-coordinate positions, matching the editor's deleted contacts.
Explicit manual contacts take precedence. Both contact file options accept an
empty array. `--contact-parameters` is a third list: each record names a
position in the same plate coordinates and the geometry keys that differ from
the global `support` settings for that contact only. Positions that do not
match a routed contact stay in the report as `contact_parameters_unmatched`
and do not silently attach to a neighbour. Projects written with
`prepare --project` preserve rotation, centering, lift (including zero), both
contact lists, and the per-contact parameter records for GUI reopening.

The editor's **Tasks → Run operation (all options)…** exposes all noninteractive
commands and switches, including full correction passes and component exports.
It executes this same CLI in a separate process; see [gui.md](gui.md).

## Prepare an edited project without the GUI

```sh
voxelmill prepare edited.voxmil --output supported.stl --max-passes 5 --report prepare.json
voxelmill prepare edited.voxmil --rotate 0 0 0 --model-lift-mm 0 --support-preset heavy --output revised.stl
```

A `.voxmil` input supplies its embedded original STL, resolved settings, rotation,
centering, lift and both contact-edit lists. Each explicit pose/contact flag
replaces the corresponding saved value; a file containing `[]` clears that
contact list. Convenience flags and `--set` override saved settings. Explicit
`--printer` or `--resin` profiles resolve settings anew. The embedded STL is
verified and extracted to an application-owned temporary directory, removed
when preparation ends. `project_input` records the archive path and embedded
source hash in the report. No Qt/VTK installation is required for this command.
The input archive cannot also be the STL output or new project destination.
Scale and mirror restore the same way as rotation, centering and lift: the
archive's saved `scale_factors`/`mirror_axes` become the defaults here unless
`--scale`/`--mirror` is given explicitly on this command line, which then
overrides the saved transform — see [above](#prepare).

Added parts restore the same way. They were the one edit this command used to
drop, so a multi-part plate saved in the editor silently prepared as a single
part; `prepare --project` now records `edits.extra_models` and reopening reads
them back. Each added mesh is embedded as `source/models/N.stl`, so the archive
still resolves after the originals move. Passing `--add-model` or
`--add-model-spec` replaces the saved parts outright rather than adding to
them, which is the same rule the other explicit overrides follow.
