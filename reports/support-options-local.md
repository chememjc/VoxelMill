# Configurable support options — local verification

Verified on Linux, 2026-09-20. This is an unreleased update after `0e977f2`.
The application version remains 0.5.3. No push, tag, or release was performed.
The published [0.5.3 verification record](releases/v0.5.3.md) is unchanged.

## Changes

The Support editor opens on a dedicated Bracing tab. Vertical spacing, neighbor
reach, complete branch length, diameter, destination mode, node connection
limit, angle, pattern, minimum origin height, and fan rotation are editable.
Common controls are also visible in Simple Setup. The CLI, portable presets,
projects, and routing reports retain the same settings.

Destination modes are Supports only, Base only, and Supports or base. Density
allows 1–8 distinct neighbor connections per spacing interval, including
incoming connections. Single and alternating diagonals and paired X braces
are available. Each X pair consumes one neighbor slot and shares a graph
junction at its crossing; it requires reciprocal vertical shaft spans. Model
parts never ground braces. Clearance, complete base footprints, build volume,
cancellation, and candidate limits remain enforced.

The model-gap example and Show part-to-part supports action demonstrate four
primary model anchors. The action explicitly enables that policy and sets
avoidance to zero in the editor draft. Disabling the policy removes model
anchors. Lower hemisphere blends close the notch under inclined/tree tip
attachments, with buried collars providing overlap without widening the tip.

## Verification

`VOXELMILL_SAMPLES=1 .venv/bin/python -m pytest -q -ra --tb=short`:
**1,218 passed, 8 skipped**, in 244.88 seconds. This includes available original
model tests, actual Qt/VTK rendering, settings round trips, and geometry tests.
The skips are one absent reference GOO, five FreeCAD-dependent tests, and the
unavailable zsh/fish completion tests. After the final acceptance-harness
screenshot change, all 15 focused AppImage/acceptance tests also passed.

Prepared meshes and decoded slices were checked for the default braces, X
braces at 60°, alternating braces at 30°, a four-branch base fan, and tree
supports with reduced cone-base diameter. Exact reopened assemblies had one
component; connectivity and overlap checks passed. Probe-volume tests at 20°,
45°, and 70° confirm that the tip shoulder notch is filled for conical and
cylindrical tips while preserving their outer surfaces.

## Local AppImage

Artifact: `output/support-options-local/VoxelMill.AppImage`.

SHA-256:
`8d7ec1c8c1648146ac7d03f3124aa47fcf76ce2710572989c4b28536c1b99870`.

Built with `scripts/build_appimage.py` and the independent CPU native extension.
All 62 application Python files match the extracted AppImage byte for byte.
Acceptance runs use the bundled Python, Qt, VTK, native extension, and package,
outside the development environment:

```sh
.venv/bin/python scripts/appimage_acceptance.py \
  output/support-options-local/VoxelMill.AppImage \
  --output-dir output/support-options-local/acceptance \
  --expected-version 0.5.3 --support-options
```

Startup, model loading, settings, regeneration, preview, project save/reopen,
preparation, slicing, and decoded output verification passed. The prepared
mesh has 29,334 triangles, one exact component, minimum Z=0, and zero raster
islands. The standard fixture generated 28 braces. Decoded connectivity,
overlap, growth-span, and enclosed-void checks passed; the transient-trap
advisory remains a warning. As in the release harness, preparation disables
its drainage/void analyses and records that export warning.

The packaged editor additionally passed these cases:

| Case | Braces | New brace feet | Model anchors |
| --- | ---: | ---: | ---: |
| Supports only | 5 | 0 | 0 |
| Base only | 24 | 24 | 0 |
| Supports or base | 15 | 10 | 0 |
| Alternating diagonals | 5 | 0 | 0 |
| X bracing | 6 | 0 | 0 |
| Part-to-part enabled | 0 | 0 | 4 |
| Part-to-part disabled | 0 | 0 | 0 |

The tree preview produced one shared trunk. Rendered tree joints, X braces,
and model anchors were visually inspected. Support preset and project round
trips passed. JSON evidence and rendered PNGs are retained locally beside
`output/support-options-local/acceptance/acceptance.json`.

Coverage limits: no physical prints or new macOS/Windows builds were run for
this local-only update. This verification does not establish mechanical strength.
