# Support presets

`voxelmill.presets` provides small, portable support recipes. A preset contains
only support overrides, so applying one keeps the printer, resin, process,
repair, assembly, and resource settings already selected by the caller.

The built-in names are:

| Name | Starting points |
| --- | --- |
| `light` | `spacing_mm = 5.0`, `pillar_diameter_mm = 0.9` |
| `medium` | The default support values from `voxelmill.config` |
| `heavy` | `spacing_mm = 2.0`, `pillar_diameter_mm = 1.6` |
| `chitubox-mars5` | The CHITUBOX table transcribed in `plan2.md` |

Light and heavy are geometric starting points. They are not printer or resin
calibration claims. Values not present in light or heavy inherit the normal
settings when the preset is applied.

`chitubox-mars5` is a partial transcription of the reference configuration,
not a claim of equivalent geometry or print results. `presets.CHITUBOX_UNSET`
lists the missing measurements; portable `notes` explain them and the explicit
derivations. Its base is `skate` with a 10 mm touch diameter, 0.80 mm thickness,
`base_skate_length_mm=0` (circular until elongation is measured), and
`base_edge_slope_deg=30` so each foot is a frustum widest at the plate — the
grayed CHITUBOX raft Slope of 30° from the platform, applied per foot because
Raft Shape is None. Grid remains available for less resin and suction; `plate`
remains the solid-slab option.

The parameter meanings were checked against the
[official CHITUBOX manual](https://docs.chitubox.com/en-US/chitubox-basic/v1.9.5/setting-up/configure-support-parameters).
Its middle angle is relative to the vertical top segment, so the recorded
70° maps to VoxelMill's `pillar_angle_deg=20` measured from horizontal.
Earlier versions of this preset incorrectly used 70 here.

The remaining reference rows are accounted for as follows:

| Reference | Implementation or explicit limit |
| --- | --- |
| Top: no touch shape, cone connection | Existing tip cone, with no added bead; diameter/depth/length retain the recorded values. |
| Middle: cylinder | Existing nominal cylinder. |
| Small pillar: cone, 0.40 mm, upper/lower depths 0.25 mm | `small_pillar_mode="model"`, conical buried ends and independent depths. Its maximum whole-gap length is absent from the reference, so `0` disables selection until supplied. |
| Bottom: model depth 0.20 mm | `model_anchor_penetration_mm=0.20`, independent of the top depth. |
| Bottom: no contact shape, diameter 0.40 mm | No added bead. The diameter is still unmapped: the new bottom connector needs a transition length the reference does not supply. Its default remains a direct attachment with derived diameter. |
| Bottom: one contact point | One lower model contact per routed support; no multi-point bottom is inferred. |
| Raft: None | No spanning slab. Grayed raft Thickness/Slope feed the per-foot skate (`0.80` mm already from Bottom; Slope 30° → `base_edge_slope_deg`). |
| Missing spacing, skate elongation, small-pillar max length | Default spacing, circular skate (`base_skate_length_mm=0`), and disabled small-pillar selection are stated, not measured equivalents. |

The `min_tip_length_mm=0.60` guardrail is derived as twice the recorded top
penetration; it is not a CHITUBOX measurement. The preset therefore remains a
starting point. The independent anchor and whole small-pillar geometry can be
configured completely without claiming to reconstruct those missing values.

The support section also includes `allow_part_to_part` and
`part_to_part_avoidance`. The boolean defaults to false and permits model
anchors for primary supports when enabled; avoidance `0`
lets model and plate routes compete by length, while `1` keeps the historical
plate preference and intermediate values require a proportionally shorter
model route. `brace_spacing_mm` (15 mm by default) sets vertical origin
spacing below each shoulder, `brace_max_length_mm` (30 mm by default) limits
the complete branch, and `brace_diameter_mm` and `brace_max_distance_mm`
independently control brace thickness and neighbor reach. **Unreleased**
`brace_destination` (`supports`, `base`, or `both`), `brace_pattern`
(`single`, `alternating`, or `x`), `brace_branches_per_node` (1–8),
`brace_angle_deg` (strictly between 0° and 90°), `brace_min_height_mm`, and
`brace_azimuth_deg` add destination, pattern, density, angle, height, and
orientation controls. Brace candidates
always require a support-only grounded path; model parts are never brace
anchors, even when primary part-to-part supports are enabled. Candidates that
intersect occupied model columns on the analysis grid are rejected and
counted.

`base_type` accepts `plate`, `none`, `pad`, `skate`, `skeleton`, `grid`, and
`hex`, plus `triangle` for triangulated foot connections.
The resolve default is `grid`; `plate` remains the solid-slab option.
`base_touch_diameter_mm` and
`base_thickness_mm` apply to the added bases and zero derives them from raft
settings; they remain invalid for `plate` and `none`. `base_skate_length_mm=0`
derives a round skate, `base_rotation_deg` rotates skate/grid geometry,
`base_strut_width_mm=0` derives nominal pillar width,
`base_cell_size_mm` is lattice pitch center-to-center for both `grid` and
`hex`, and `base_edge_slope_deg` tapers the base wall inward from the plate
(`0` keeps it vertical). Presets retain all these
keys even when the current base type does not use them, so switching modes does
not silently erase a user's values. The one consequence to know: switching back
to `plate` or `none` while `base_touch_diameter_mm`, `base_thickness_mm` or
`base_edge_slope_deg` is still set is refused with `invalid_profile`, because
those bases cannot express them. Zero those fields rather than expecting them
to be ignored. The generated base report measures actual
footprint area, openings, volume and connectivity; it does not establish
adhesion or strength.

```python
from voxelmill.config import resolve_settings
from voxelmill.presets import apply_preset, list_presets, load_preset, save_preset

print(list_presets())    # ('light', 'medium', 'heavy', 'chitubox-mars5')
settings = apply_preset(resolve_settings(), 'heavy')
custom = load_preset('path/to/support-preset.json')
settings = apply_preset(settings, custom)
save_preset('path/to/shared.json', 'functional', {
    'spacing_mm': 2.5,
    'pillar_diameter_mm': 1.4,
})
```

`load_preset` accepts a built-in name or an explicitly supplied JSON path. It
does not search user directories or discover profiles. Names are
case-insensitive for the three built-ins; every other string is treated as a
path.

Portable files have schema version 1 and exactly three top-level fields:

```json
{
  "schema_version": 1,
  "name": "functional",
  "support": {
    "spacing_mm": 2.5,
    "pillar_diameter_mm": 1.4
  }
}
```

The `support` object may contain only keys known to the resolved support
settings. It may be partial. The implementation merges it over a copy of the
full defaults and runs `validate_settings`, which rejects unknown fields,
invalid relationships, nonfinite numbers, and unsupported values. Applying a
preset validates the resulting complete settings and returns a new dictionary;
the input dictionary is unchanged.

`save_preset` writes UTF-8 JSON through a same-directory temporary file and an
atomic replacement. It accepts a preset dictionary, built-in name, or JSON
path, and also supports the convenient `save_preset(path, name, support)` form.
An optional `notes` array of strings is informational and survives preset
load/save; it does not alter settings. The reference preset uses it for source
attribution, derivations, and unresolved measurements.

The CLI offers `preset list`, `preset show NAME_OR_PATH`, and
`preset save --output FILE --name NAME` (saving the resolved support values).
`--support-preset NAME_OR_PATH` selects one for preparation or other configured
commands. The GUI **Parts → Support presets** submenu applies built-ins, loads
JSON or saves the current support section, with undo for application. All
options are also available through **Tasks → Run operation**.

## Process presets

Process presets are the same portable format with a `process` table instead of
`support`. They never carry printer hardware. Built-ins are uncalibrated
starting points:

| Name | Starting points |
| --- | --- |
| `fine` | `layer_height_mm = 0.03` when the printer range allows it, otherwise `0.05` |
| `default` | The default process values from `voxelmill.config` |
| `fast` | `layer_height_mm = 0.1`, `bottom_layers` one below the default when valid |

```python
from voxelmill.config import resolve_settings
from voxelmill.presets import (
    apply_process_preset, list_process_presets, load_process_preset,
    save_process_preset,
)

print(list_process_presets())  # ('fine', 'default', 'fast')
settings = apply_process_preset(resolve_settings(), 'fast')
save_process_preset('path/to/process.json', 'draft', {
    'layer_height_mm': 0.1,
    'bottom_layers': 3,
})
```

```json
{
  "schema_version": 1,
  "name": "draft",
  "process": {
    "layer_height_mm": 0.1,
    "bottom_layers": 3
  }
}
```

CLI: `preset list --kind process`, `preset show fine --kind process`, and
`preset save --kind process --name NAME --output FILE`. `--process-preset`
applies after `--support-preset` and before `--set` on every settings-resolving
command.
