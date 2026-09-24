# Profiles and projects

STL coordinates are millimeters unless explicitly converted by the caller. Physical printer dimensions belong exclusively to the printer profile. Profile version 1 is UTF-8 TOML; unknown fields, nonfinite values, incorrect types, and incompatible pixel pitch/build dimensions are errors. Python 3.10 uses `tomli`; Python 3.11+ uses `tomllib`.

The editable examples are `profiles/mars5-ultra.ptr` and `profiles/sunlu-abs-like-gray.res`. Copies under `voxelmill/data` ship in the package. Defaults are also built into `voxelmill.config`, so installed commands do not depend on the working directory. Resolution is built-in defaults → supplied printer profile → supplied resin's matching printer process → explicit nested overrides. Each call returns independent mutable settings. Resin profiles may contain only resin identity and printer-specific process/support sections; they cannot redefine physical printer dimensions, including through an unmatched printer entry. An explicitly supplied resin without a matching printer ID is rejected.

```python
from voxelmill.config import resolve_settings, layer_exposure
settings = resolve_settings(
    'profiles/mars5-ultra.ptr',
    'profiles/sunlu-abs-like-gray.res',
    {'process': {'normal_exposure_s': 3.8},
     'resources': {'memory_gib': 16, 'workers': 2}},
)
print(layer_exposure(settings, 4))  # first intermediate layer; indices start at zero
```

Resolved settings contain `schema_version`, `printer`, `resin`, `process`, `support`, `repair`, `assembly`, and `resources`. `validate_settings` checks a complete resolved dictionary. The profile files show the complete printer and resin process syntax. A printer profile can additionally provide `[process]`, `[support]`, `[repair]`, `[assembly]`, and `[resources]` defaults. CLI overrides use the same section and key names.

| Section | Keys and defaults |
| --- | --- |
| `printer` | `id="mars5-ultra"`, `name="Elegoo Mars 5 Ultra"`, `build_mm=[153.36,77.76,165]`, `pixels=[8520,4320]`, `pixel_pitch_mm=[0.018,0.018]`, `edge_clearance_mm=2`, `image_mirror_x=false`, `image_mirror_y=false`, `image_mirror_verified=false`, `layer_height_range_mm=[0.01,0.2]`, `output_formats=["goo"]`, and all 18 reference-observed `motion` fields shown in `mars5-ultra.ptr` |
| `resin` | `id="sunlu-abs-like-gray"`, `name="Sunlu ABS-like gray"`, `density_g_cm3=0.0`, `cost_per_liter=0.0`, `currency="$"` |
| `process` | `layer_height_mm=0.05`, `bottom_exposure_s=35`, `normal_exposure_s=3.5`, `bottom_layers=4`, `transition_layers=5` |
| `process` waits | Independent `bottom_` and `normal_` keys: `rest_after_exposure_s=1`, `settle_before_exposure_s=0.5`, `wait_after_lift_s=0` |
| `process` elephant foot | `elephant_foot_compensation_mm=0.0` (disabled), `elephant_foot_layers=0` (derives the ramp length from `bottom_layers`) |
| `process` dimensional | `shrink_percent_xy=0.0`, `shrink_percent_z=0.0`, `tolerance_offset_mm=0.0`, `bottom_tolerance_offset_mm=0.0` (all disabled and uncalibrated) |
| `process` antialiasing | `antialias_levels=1` (`1`, `2`, or `4`; 1 is binary occupancy, 2 or 4 supersample to coverage grayscale), `antialias_supports=false` (support tips stay binary unless this is set) |
| `support` | `automatic=true`, `auto_bracing=true`, `allow_part_to_part=false`, `drop_attached_unroutable=true`, `tree_supports=false`, `tree_cluster_mm=0` (derives `2 * spacing_mm`), `contour_supports=false`, `boundary_supports=false`, `part_to_part_avoidance=1`, `spacing_mm=3`, `contact_diameter_mm=0.4`, `penetration_mm=0.15`, `tip_shape="cone"`, `break_point_diameter_mm=0.8`, `pillar_diameter_mm=1.2`, `tip_length_mm=2`, `tip_base_diameter_mm=0` (derives `pillar_diameter_mm`), `model_anchor_shape="cone"`, `model_anchor_length_mm=2`, `model_anchor_diameter_mm=0.4`, `model_anchor_penetration_mm=0.15`, `pillar_angle_deg=45`, `small_pillar_diameter_mm=0`, `small_pillar_max_length_mm=0` (both zero disables the thin-pillar class), `brace_spacing_mm=15`, `brace_diameter_mm=0` (derives from the thinner connected pillar), `brace_max_distance_mm=0` (derives `1.5 * spacing_mm`), `brace_max_length_mm=30` (maximum complete diagonal length), `brace_destination="both"`, `brace_pattern="single"`, `brace_branches_per_node=1` (integer 1–8), `brace_angle_deg=45` (strictly between 0 and 90), `brace_min_height_mm=0`, `brace_azimuth_deg=0`, `base_type="grid"`, `raft_slope_deg=30` (plate outer putty-knife bevel; 0 is a near-vertical rim), `base_edge_slope_deg=0` (inward taper of any added base except `plate`/`none`; 0 is vertical), `base_touch_diameter_mm=0`, `base_thickness_mm=0`, `base_skate_length_mm=0`, `base_rotation_deg=0`, `base_strut_width_mm=0`, `base_cell_size_mm=6`, `min_tip_length_mm=0.3`, `raft_thickness_mm=1`, `raft_expansion_mm=2`, `max_slenderness=40`, `max_span_mm=3`, `min_overlap_pixels=1`, `overhang_angle_deg=45`, `support_clearance_mm=0.3`, `max_island_passes=5` (integer 1–10; caps `prepare`'s and the editor's island-correction loop, see [algorithms.md](algorithms.md#island-correction-passes)), `max_contact_gap_mm=0` (derives `spacing_mm`), `max_contact_load_mm2=0` (derives `4 * spacing_mm^2`) |
| `repair` | `seal_voids=true`, `min_orifice_area_mm2=1`, `aggressiveness="conservative"`, `max_deviation_mm=0.05`, `remove_tiny_features=false`, `auto_drain_holes=false`, `voxel_size_mm=0` (derived), `smooth_iterations=0`, `min_void_volume_mm3=0`, `support_void_policy="fail"` (`fail` / `ignore` / `fill`) |
| `assembly` | `union="auto"` (`auto` or `exact`), `require_raster_parity=true`, `max_parity_examples=16` (integer 0–256), `clip_to_build_volume=false` |
| `resources` | `memory_gib=32`, `workers=0` (0 derives one per physical core, capped at 8 where the measured speedup plateaus), `worker_policy="performance"` (`performance` / `efficiency` / `all`), `scratch_dir=null` (omit the key in TOML to use its default); `acceleration="auto"` (`auto` / `cpu` / `cuda`), `cuda_device=0`, `post_slice_hook=null`; layer analysis caps worker concurrency against full-panel mask/label/EDT scratch estimates |
| `hollow` | `enabled=false`, `wall_thickness_mm=2.0`, `voxel_size_mm=0` (derives from wall thickness), `mode="inner"` (`inner` or `bottom_open`), `min_wall_thickness_mm=1.0`, `drain_diameter_mm=2.0`, `vent_diameter_mm=2.0`, `infill="none"` (`none` / `grid` / `hex` / `gyroid`), `infill_pitch_mm=4.0` |

`resin.density_g_cm3` and `resin.cost_per_liter` both default to `0.0`, meaning "not supplied": `config.resin_usage` treats `0.0` (or any value `<= 0`) as absent and reports the derived `mass_g`/`cost` (and the `density_g_cm3`/`cost_per_liter` it echoes) as `null` rather than as a fabricated number computed from an unset field. A negative value is rejected by `validate_settings` the same as any other resin setting; only `0.0` reads as "not supplied". `resin.currency` defaults to `"$"` and must be a nonempty ASCII string of at most 8 bytes, because it is written into the GOO header's `price_currency` field, a fixed 8-byte ASCII slot — a value that would not fit or would not encode is rejected at profile-resolution time rather than at export time. `resolve_settings` runs `config.resin_usage(settings, volume_mm3)` for `voxelmill profile`'s `resin_usage_per_ml`, and `pipeline.prepare`/`goo.slice_stl` run it against the raster-measured cured volume for `stages.resin_usage`/the sliced payload's `resin_usage`; see [cli.md](cli.md). The same three fields also feed the GOO header directly: `header_from_settings` fills `material_grams`, `material_cost`, and `price_currency` from this computation, where every export previously wrote `0.0`, `0.0`, and `"$"` unconditionally regardless of the resin in use. See [profiles.md](profiles.md) for `voxelmill profile`/`voxelmill resin` and the profile library that resolves `--printer`/`--resin`.

The key was renamed from `resin.cost_per_litre` to `resin.cost_per_liter` (US spelling, matching the rest of this codebase). There is no migration: a `.res` profile or a `.voxmil` project written with the old key name will not load — `validate_settings` rejects it as an unknown field — until that one field is renamed by hand.

Five transition layers are intermediate values, excluding both endpoints. With the requested defaults, zero-based layers 0–3 receive 35 s; layers 4–8 receive 29.75, 24.5, 19.25, 14, and 8.75 s; layer 9 onward receives 3.5 s. These are the user's settings, independent of the reference GOO file.

`process.elephant_foot_compensation_mm` shrinks the exported bottom layers by
a radius, in millimeters, that ramps linearly to zero over
`process.elephant_foot_layers` layers, to correct the wider-than-model cure
those layers get from bottom-layer overexposure. `0.0` (the default)
disables it. `validate_settings` rejects any value above `1.0` mm, because a
radius that large would erase a bottom layer's geometry rather than
compensate it — see [algorithms.md](algorithms.md) for how the ramp, the
per-axis pixel rounding, and the erasure refusal actually work.
`process.elephant_foot_layers` is the integer layer count the ramp runs
over; `0` (the default) derives it from `process.bottom_layers` instead of
naming a second number that has to be kept in sync. Both are ordinary
`process` keys, reachable through `--set section.key=value`, the dedicated
`--elephant-foot-mm`/`--elephant-foot-layers` CLI shortcuts (see
[cli.md](cli.md)), or the GUI's resolved-settings JSON, the same as any
other process setting.

`process.shrink_percent_xy` scales each exported layer about the plate
center (or the crop center when no grid origin is available) to compensate
resin cure shrinkage without resizing the source mesh. Positive values
enlarge the exposure. `process.shrink_percent_z` is accepted and reported,
but is **not applied** at slice time: a honest Z scale would change the
layer count, so the export leaves layers unchanged and records
`not_applied` with that reason. `process.tolerance_offset_mm` is a
morphological radius in millimeters — positive erodes (compensate
over-cure / light bleed), negative dilates — converted to pixels per axis
the same way elephant-foot is. `process.bottom_tolerance_offset_mm`, when
nonzero, replaces `tolerance_offset_mm` on the bottom layers only. All four
default to `0.0` meaning off and **uncalibrated**; a nonzero request is
flagged `uncalibrated` in the sliced payload rather than treated as a
measured resin property. Reach them with
`--set process.shrink_percent_xy=...` (and the sibling keys); see
[algorithms.md](algorithms.md#xy-shrinkage-and-tolerance-compensation) and
[cli.md](cli.md).

Support dimensions, span and slenderness limits, and maximum repair deviation are calibration starting points. `min_tip_length_mm` (default 0.3) is the shortest tip cone `route_contacts` may emit when anchoring a contact on already-printed model material; it is the contact depth from the known-good CHITUBOX configuration recorded in [support-presets.md](support-presets.md#the-reference-chitubox-configuration), a value observed on a working print, not a calibrated constant. Validation requires `penetration_mm < min_tip_length_mm <= tip_length_mm`. The layer-height range is a configurable software range pending firmware/profile verification. Image mirroring describes the canonical raster; machine-specific GOO orientation and tilt-release fields remain unverified. `image_mirror_verified` gates that specific uncertainty: it is boolean, validated the same way as `image_mirror_x`/`image_mirror_y` (`printer.image_mirror_verified must be boolean`), and defaults to `false` because the two reference GOO files for this machine disagree on the mirror flags (CHITUBOX Basic v2.3.1: `mirror_x=1, mirror_y=0`; ELEGOO SatelLite: `mirror_x=0, mirror_y=1`). While it is `false`, every GOO export from `slice_stl` appends a `goo_orientation_unverified` diagnostic at severity `warning` and sets `validation.checks['image_orientation'] = 'warn'`; `voxelmill slice` also prints a one-line stderr banner. It is a warning, not an error, so it does not block the export — the report cannot judge which orientation is physically correct, so it states that instead of refusing. Setting it `true` after checking a printed part suppresses the diagnostic and the check reads `pass`. See [troubleshooting.md](troubleshooting.md#goo_orientation_unverified) for the offline check that was run against both references and came back inconclusive. `output_formats=["goo"]` records the printer format, not hardware verification status. The default `motion` table is copied from the immutable reference GOO so exports are serializable; it is not a calibrated motion recipe. Review or override every motion field manually before a physical print. Filling defaults do not imply that a geometry implementation has successfully completed the requested analysis: validation must report any missing or failed check.

## Support geometry: three segments, each with its own setting

`route_contacts` (see [algorithms.md](algorithms.md#supports)) builds a
routed contact as three segments, each owned by different `support` keys:
a top tip (`contact_diameter_mm`, `tip_base_diameter_mm`,
`tip_length_mm`, `penetration_mm`, `tip_shape`, `break_point_diameter_mm`), a middle pillar
(`pillar_diameter_mm`, `pillar_angle_deg`, and optionally
`small_pillar_diameter_mm`/`small_pillar_max_length_mm`), and a bottom
segment — the plate, a pad, or model material — governed by `base_type`
and, for `pad`, `base_touch_diameter_mm`/`base_thickness_mm`.

`tip_shape` (`cone` default, or `cylinder`) is the top contact. A cone
tapers from `tip_base_diameter_mm` to `contact_diameter_mm`; a cylinder
keeps the contact diameter and ignores the tip-base diameter as a cone-only
control. `break_point_diameter_mm` defaults to `0.8` and unions a sphere onto
the top contact for a controlled snap-off; `0` emits no ball. Validation
requires a nonzero value to be at least `contact_diameter_mm` and to fit
inside `tip_length_mm + penetration_mm`. These two keys, and the other
individual geometry keys, can also be set per contact through
`--contact-parameters` / the Setup tab: global defaults stay unchanged,
lookup is exact at 1 µm rounding, and an unmatched position is a warning
rather than a silent neighbour merge.

`tip_base_diameter_mm` is the tip cone's lower diameter where it meets the
pillar. It used to come from `pillar_diameter_mm` directly. CHITUBOX calls
this dimension Tip Down Diameter and keeps it independent of the middle
segment's own diameter; the two are equal in the known-good configuration
recorded in [support-presets.md](support-presets.md#the-reference-chitubox-configuration), which is exactly why conflating them went
unnoticed. `0` (the default) still derives it from `pillar_diameter_mm`,
preserving the old behavior. Validation refuses a `contact_diameter_mm`
wider than a nonzero `tip_base_diameter_mm`.

`contour_supports` (default `false`) also samples the outer perimeter of
downward-face clusters. `boundary_supports` (default `false`) also samples
open mesh boundary edges above the plate; a closed solid adds none. Face
centroids and interior lattices remain the default sampling. Added models may
carry `overrides.support` with a subset of these keys; the plate still shares
one collision field.

`pillar_angle_deg` (default 45) replaces a hard-coded 45 degrees that used
to decide how steeply an angled branch may run. Reaching sideways by
`lateral` costs `lateral * tan(pillar_angle_deg)` of vertical drop, so a
steeper angle buys stiffness and costs reach — and fewer contacts have
enough drop available to branch at all. The default reproduces the
previous behavior exactly.

With `small_pillar_mode="middle"` (default),
`small_pillar_diameter_mm`/`small_pillar_max_length_mm` add a second,
thinner pillar class for short runs: a run no longer than
`small_pillar_max_length_mm` is built at `small_pillar_diameter_mm`
instead of `pillar_diameter_mm`. Both default to `0`, disabling the class.
The validator requires both set or both zero, because a thin pillar with
no length limit would replace every pillar. The choice is made once, from
the run's total length, before any geometry for that run is emitted, and
the cross-brace strut connecting it scales to the thinner diameter too.

`small_pillar_mode="model"` instead sizes a complete connector between two
model surfaces. Its maximum length is the entire surface-to-surface gap, and
it never replaces a plate pillar. A zero maximum disables selection, including
when a diameter is recorded. `small_pillar_shape="cone"` (default) gives the
shaft conical buried ends; `"cylinder"` continues the same diameter into each
surface. `small_pillar_upper_depth_mm` and `small_pillar_lower_depth_mm`
(both default `0`) independently size the two penetrations. Nonzero depths
are refused in middle mode. Model mode checks the whole shaft footprint on
the column grid and refuses penetration beyond the central column's material
run. It retains ordinary routing failures and the part-to-part policy.

The ordinary model-anchor bottom is independent of the top tip. Bottom to
top, a part-to-part support is a bottom connector buried in the lower body,
then the middle pillar, then the tip at the contact; the four
`model_anchor_*` settings describe only that bottom connector:

```
                 ___/\___      tip, contact_diameter_mm at the contact
                    ||          middle pillar, pillar_diameter_mm
    junction   ____/\____      model_anchor_length_mm above the surface
  ~~~~~~~~~~~~~~~~~~~~~~~~~~~   lower model surface
                   \  /         model_anchor_penetration_mm below it,
                    \/          model_anchor_diameter_mm across
```

The lower surface height is read from the analysis column raster, not from
the mesh, so it is exact only to that pitch; `route_contacts` reports this as
`clearance_basis`. The editor's **Showcase** example analyses at the
production pitch for exactly this reason (see [examples.md](examples.md)).

| Setting | Default | Meaning |
| --- | --- | --- |
| `model_anchor_shape` | `"cone"` | Cone to the middle radius, or `"cylinder"` at the bottom diameter. |
| `model_anchor_length_mm` | `2` | Height above the lower model surface; zero keeps the direct attachment. |
| `model_anchor_diameter_mm` | `0.4` | Lower endpoint diameter; zero derives the selected middle diameter. A nonzero diameter requires positive length. |
| `model_anchor_penetration_mm` | `0.15` | Depth below the lower model surface; works with a direct attachment too. |

Part-to-part routes are point-to-point (balls at both ends). Short gaps stay
thin via `_fit_anchor_tips` and do not swell to `pillar_diameter_mm`. A
positive bottom length must fit in full along with `min_tip_length_mm`; only
the top tip can shorten. A failed bottom candidate may still use a plate
route; when no permitted route remains it fails `support_routes`. The new
bottom envelope is checked against model columns with XY clearance, and depth
must fit inside the central column's lower material run. This is sampled
clearance evidence, not exact surface intersection. The report records actual
endpoint and junction coordinates. On a cone the configured diameter is at
the buried endpoint, not at the model's surface plane. Whole small model
pillars use their own depth/shape settings instead of these bottom settings.

Older schema-1 archives that omit the break-point / model-anchor keys still
fill historical zeros through `fill_legacy_settings` / `_LEGACY_SUPPORT_OFF`
rather than sprouting the new nonzero defaults. Explicit zeros already stored
in a project are kept as zeros.

**Brace controls (0.5.4):** Downward braces begin at the full-width shoulder
below each tip taper and proceed at `brace_angle_deg` (45° by default) toward a
grounded support network or a valid plate landing.
`brace_spacing_mm` is their vertical origin spacing and defaults to 15 mm;
it is independent of primary `spacing_mm`. `brace_max_length_mm` limits the
complete diagonal branch to 30 mm by default. `brace_max_distance_mm` remains
the separate neighbor search limit and `0` derives `1.5 * spacing_mm`.
Candidates are processed from highest to lowest, use the shortest valid
support-only connection, and are omitted when no destination fits the length,
clearance, or build-volume rules. A model part is never a brace anchor, even
when `allow_part_to_part=true`; that flag applies only to primary support
routing. `brace_destination` selects grounded supports, new base feet, or both;
the default `both` prefers supports. `brace_pattern` selects single diagonals,
alternating directions by level, or paired X diagonals between reciprocal
vertical shaft spans. X rejects a pair when either diagonal is unavailable or
collides; base landings fan in every pattern. `brace_branches_per_node` defaults
to 1 and allows 1–8 connections per vertical spacing interval;
shared incoming connections count, and an X pair consumes one neighbor slot.
`brace_min_height_mm` and `brace_azimuth_deg` default to 0. These values are
ordinary `support` keys, reachable through `--set section.key=value`, the
dedicated brace CLI shortcuts, or the GUI's Bracing tab.

`support.tree_cluster_mm` (default `0`, which derives `2 * spacing_mm`) is the
radius `tree_supports` clusters nearby vertical plate supports within before
building a shared trunk; see [algorithms.md](algorithms.md#tree-supports).

`allow_part_to_part` controls whether a primary contact may anchor on already
printed model material and defaults to `false`. When enabled,
`part_to_part_avoidance` compares the model route
with the available plate route by centerline length: `0` lets both compete
equally, `1` preserves the historical preference for the plate route, and an
intermediate value accepts the model only when it is proportionally shorter
than the plate route: `model_length < plate_length * (1 - avoidance)`. A model
anchor remains a fallback when no plate route exists unless the boolean is off.
Disabling the boolean blocks model anchors and counts
those contacts in `contacts_blocked_by_policy`; unroutable contacts still
reach the existing failure gates.

`brace_diameter_mm` sets the cross-brace diameter when nonzero; `0` derives it
from the thinner of the two connected pillars. `brace_max_distance_mm` limits
which pillar neighbors can be connected and defaults to `1.5 * spacing_mm`.
`brace_spacing_mm` and `brace_max_length_mm` are strictly positive; their
defaults are 15 mm and 30 mm. These brace values are ordinary `support` keys, reachable
through `--set section.key=value` or the dedicated brace CLI shortcuts (see
[cli.md](cli.md)), or the GUI's Bracing tab. Before a brace is
emitted, its capsule is checked against occupied model columns on the support
analysis grid. A collision rejects that candidate and increments
`braces_collision_rejected`; the grid test is a clearance heuristic, not a
mechanical strength proof.

`base_type` chooses what routed supports land on: `grid` (default) is the
porous lattice; `plate` is the legacy convex hull raft, `none` emits actual
24-sided bare-foot sections,
and `pad` gives each unique foot a circular disc. `skate` gives each foot a
capsule; its total length includes the rounded ends, and zero derives the
touch diameter, so no elongation is inferred. With `base_edge_slope_deg` the
skate is a frustum widest at the plate (putty-knife edge); slope `0` keeps the
vertical capsule. `skeleton` connects rounded
pads with a deterministic Euclidean minimum-spanning tree. `grid` adds that
tree, clipped orthogonal strips, and an explicit perimeter rim; the grid is
rotated about its local origin and centered on the rotated foot hull.
`hex` is the same construction with a honeycomb in place of the square
lattice; at equal pitch and wall width the two open the same fraction, so the
choice is wall shape rather than resin. Prefer `grid` for less resin/suction;
keep `plate` when a solid slab is required. `base_edge_slope_deg` tapers any
added base inward from the plate (CHITUBOX raft-slope convention), quantised
to whole printed layers, and `0` keeps the vertical wall every base had
before. It is invalid for `none`, and for `plate`, whose outer rim has its own
key: `raft_slope_deg` (default 30°, a putty-knife edge), plus a small top
chamfer of `min(0.25, raft_thickness_mm/4)`. For the same ~1 mm / 30° edge on
an added base (`skate`, `pad`, `grid`, …), use `base_thickness_mm` ≈ 1 with
`base_edge_slope_deg=30`.
`triangle` joins pads with Delaunay edges and a perimeter rim; degenerate foot
sets use the spanning tree. Its struts and pads share the same dimensions as
`skeleton`, and it supports the same edge taper.
`base_cell_size_mm` is lattice pitch center-to-center, while
`base_strut_width_mm=0` derives the nominal pillar diameter. All added bases
use `base_touch_diameter_mm`/`base_thickness_mm` (zero derives from raft
settings). `base_rotation_deg` applies to skate and grid geometry. Each
strategy records emitted footprint area, convex-envelope area, open area and
component count in `metrics.base`; these are geometry measurements, not
adhesion or strength claims. `base_touch_diameter_mm`/`base_thickness_mm`
remain invalid for `plate` and `none`, even though mode-specific fields are
retained when switching modes. At most 8192 unique feet and 2048 candidate
grid lines are accepted; excess work raises structured `base_complexity`.

None of this is a strength result. Slenderness, bracing, and anchor load
remain configured heuristics (see [algorithms.md](algorithms.md#supports)),
and `build_base`'s record states plainly that a base establishes its own
shape and size, not plate adhesion — that needs a printed part.

A `.voxmil` project is a version 2 ZIP containing `manifest.json` and, optionally, `source/original.stl` plus one `source/models/N.stl` per added part, so the archive stays portable after the original paths on disk disappear. Schema 2 stores `edits.paint` as one `{blocked, enforced}` record per plate object, primary first, with every mark in that object's own mesh frame; schema 1 stored a single plate-coordinate table and is refused rather than converted, because its marks cannot be attributed to a part after the fact. `save_project(path, manifest, source_path=None)` preserves supplied JSON state such as resolved settings, placement, edits, support graph, input hashes, and validation. Dataclasses and `Path` values are accepted; NumPy arrays must be converted with `.tolist()`. It embeds source bytes without modifying them, calculates SHA-256 in 1 MiB chunks, checks an optional supplied `source.sha256`, and saves atomically. Source metadata contains the original name/path, hash, byte count, and archive member name. The returned manifest is normalized JSON data.

`load_project(path, extract_dir=None)` checks the archive and embedded hash on every load. Without extraction it returns state without following external source paths. With extraction it writes a hash-named STL atomically and adds `source.extracted_path` to the returned state. Symlink extraction directories and destinations are rejected. Callers should use an application-owned scratch directory. Geometry validation status is retained verbatim; reopening a project does not establish that its geometry passes newly implemented checks. Revalidate before export.

Readers allow only the manifest, the primary source, and up to 1024 `source/models/N.stl` extra-model sources, reject duplicate members and JSON keys, traversal paths, encryption, unsupported compression, nonfinite JSON, truncation, and hash/size mismatches. Limits: manifest 16 MiB, each source 16 GiB, entire archive 32 GiB, central directory 1 MiB across at most 1026 entries, maximum compression ratio 256. Writers store embedded STL without compression so large immutable sources remain streamable and compression-ratio rejection does not affect archives they generate. Archive checks are corruption/bounds checks, not cryptographic authentication. Projects with a source bigger than 16 GiB require a later archive-version/resource-policy extension.

Verification:

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_config.py tests/test_project.py
```

All resolved settings are available through `voxelmill profile` and CLI `--set section.key=value`; JSON values preserve numbers, booleans and arrays. The GUI exposes the same settings. `--no-auto-supports` keeps only supplied/manual contacts, `--no-auto-bracing` disables generated braces, and a numeric `--rotate RX RY RZ` disables orientation search. Manual contacts use final plate coordinates. Turning off automatic supports also disables corrective contact additions. `remove_tiny_features` and `auto_drain_holes` are reserved and rejected when true, so an unsupported option never silently does nothing.

The editor's Setup tab names each compact control's resolved-settings key and
CLI flag (or `--set section.key=VALUE` where no dedicated flag exists) in its
tooltip, and marks a control whose value differs from the profile stack's
resolved baseline. See
[gui.md](gui.md#setup-tab-config-keys-cli-flags-and-the-modified-baseline) for
the full set of controls covered and how the baseline is established.

The default cavity threshold is zero: every enclosed raster void is counted. A manually chosen nonzero `min_void_volume_mm3` reports both excluded count and volume. It applies to drainage bottlenecks as well as layer voids, as `ignored_bottlenecked_components` and `ignored_bottlenecked_volume_mm3`. A supported assembly needs that: support/model and base crevices can add trapped pockets, so the supported assembly can fail a check the model passes. The synthetic sphere isolates the tip/model crevice; the real nut also shows a base-dependent contribution (see `gotchas.md`). Do not raise `min_void_volume_mm3` silently to pass an export.

`repair.support_void_policy` (`fail` / `ignore` / `fill`, default `fail`) is the
supported-assembly answer to that class of finding. `fail` keeps today's
behavior. `ignore` drops support-class voids and drainage bottlenecks only
(recorded under `ignored_support_voids` / `ignored_support_bottlenecks`); a
hollow model cavity still fails. `fill` seals enclosed shells after an exact
union and re-validates; on the raster path the fill stage is `not_run` and
enclosed voids are not claimed as a pass. Fill does not close tip/model
drainage necks — those need tip geometry (wider contact or less taper), not a
volume floor.

Drainage uses circular-equivalent path clearance; its sampled grid and bisection interval are in the report. Equality passes on that grid, which cannot certify sub-grid walls or arbitrary slot area — and sampling a round bore at cell centers reports less clearance than it has, so a bore needs roughly 15% over the nominal diameter to clear the default grid. The error always understates the opening, so marginal parts fail rather than pass.

`max_contact_gap_mm` is the furthest a sampled downward face may sit from a contact; exceeding it fails `support_coverage` and blocks an export. `max_contact_load_mm2` is the downward area a single contact may carry under nearest-point assignment; exceeding it warns rather than fails, because a share of area is not a strength result. Both derive from `spacing_mm` when left at zero, and both are geometric reach measures awaiting calibration against real prints.

`assembly.union="auto"` tries the exact solid path, then uses grouped raster
occupancy on a geometric rejection. `exact` prohibits fallback. Repair `none`
uses authored triangles directly and therefore conflicts with exact-only
assembly. Parity compares the reopened STL against fresh group masks; disabling
it records `not_run`, which does not pass validation. Existing schema-1 printer
profiles inherit assembly defaults, and the GUI supplies them when reopening
older schema-1 projects that have no assembly section.

`clip_to_build_volume` is off by default. Off, a part outside the usable
envelope is refused (`goo_envelope`) rather than exported cut. On, `prepare`
and `slice_stl` proceed against the geometry the printer can physically reach
— the LCD panel in X/Y, the machine height in Z — and record exactly how much
was discarded; the export is still withheld unless `--allow-unresolved` is
also given, because the resulting `clipped_geometry` diagnostic is
error-severity. Nothing is ever scaled, on or off.

## Acceleration and resources

`resources.acceleration` (`auto` default, `cpu`, or `cuda`) chooses the raster
morphology backend. `auto` uses CUDA only after a successful runtime and
device probe at the configured `resources.cuda_device` (default `0`), and
falls back to the deterministic CPU path otherwise; an explicit `cuda` is
refused with a structured error rather than silently falling back when no
usable device is found. Both are also `--acceleration`/`--cuda-device` on the
CLI and `resources.acceleration`/`cuda_device` in the editor's Preferences
dialog. `resources.post_slice_hook` (default `null`, meaning none) is an
opt-in shell command run after a successful `slice`/GOO export, given the
output and report paths as `VOXELMILL_OUTPUT`/`VOXELMILL_REPORT` environment
variables with a 120 s timeout; it is never inherited from a printer or
resin profile — a hook is arbitrary code, and a profile is not where arbitrary
code belongs — and its failure never deletes an already-published GOO.

`repair.step_linear_deflection_mm` (default `0.1` mm) and
`repair.weld_tolerance_mm` (default `0`, capped at `0.05` mm) govern STEP
import tessellation and near-duplicate vertex welding respectively; see
[geometry.md](geometry.md) and [cli.md](cli.md#import-step).

## Support presets

Portable support-only presets apply after printer/resin profiles and before
explicit CLI flags and `--set`. They preserve every unrelated settings section.
Use `--support-preset light|medium|heavy|PATH` or the GUI **Parts → Support
presets** submenu. `preset save` captures the complete current support section.
Named process presets and embedding presets inside printer/resin profiles remain
future work. See [support-presets.md](support-presets.md) for the versioned JSON
format.


## Peel screening

The top-level `peel` table contains `enabled=true`, `max_angle_deg=10.0`,
`area_threshold_mm2=100.0`, and `reference_lift_speed=0.05`. Angle is in `[0,90)`;
area and reference speed must be finite and positive. These are uncalibrated
advisory settings. They do not alter the printer motion table. Reference speed
uses the same native units as the motion fields, whose physical calibration
remains unverified. Settings are retained in `.voxmil` projects and full profile
snapshots; hardware-only profiles omit this analysis policy.

Use `--set peel.area_threshold_mm2=150` or edit the resolved settings JSON in
the editor. `--no-peel-analysis` disables the check and reports `not_run`, which
cannot satisfy the all-checks-ran validation gate. See the surface peel-risk
section in [algorithms.md](algorithms.md) for the score and its limits.
