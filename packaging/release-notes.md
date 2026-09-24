Beta {version}: portable Linux / macOS / Windows builds. Unsigned. The first beta.

## Supports

- **Defaults that print.** With default settings, 14 of the 18 test shapes used to fail validation, which blocks the export. Every valid test shape now passes. Contacts sit on a hexagonal lattice, and a repair pass adds a contact wherever a downward face is out of reach. Model anchors can land on slopes up to 60°, and part-to-part anchoring is on by default. Support crevices at the tip/model interface are warnings rather than failures (`repair.support_void_policy = "ignore"`); cavities in the model still fail.
- **A CHITUBOX Light look.** Contacts are 0.35 mm with 0.2 mm penetration, pillars are 0.9 mm, and 0.6 mm braces zigzag between neighbouring pillars every 5 mm from 3 mm above the plate. The `light` and `heavy` presets and the shipped resin profile are rescaled around these values. Separate pillars keep the support clearance between them.
- **Braces on pillars standing on the model.** A new checkbox (`support.brace_model_pillars`, `--brace-model-pillars`, off by default) lets braces join pillars that stand on the model. On the bracket test part it braces 16 of its 17 model-standing pillars, and their longest unbraced run drops from 22.6 mm to 9.9 mm.
- **No starved pillars in dense rows.** A pillar left with an unbraced run longer than two brace intervals gets another attempt at bracing. On the bracket the worst plate pillar went from 25.9 mm unbraced to 10 mm.
- **Collision audit.** Every `prepare` intersects the supports with each part and checks support shafts against each other (`validation.metrics.support_collisions`). This found and fixed brace feet inside neighbouring pillars, pillars standing partly inside a part's wall, branches overlapping near elbows, and overlapping pillars in tree mode.
- **`slice`, `verify` and `validate` accept what `prepare` passed.** A single STL or slice file cannot say which voids the supports made, so these commands used to fail, and withhold, an export `prepare` had just passed, on one-voxel crevices under the support tips. Under the default void policy they now warn when no finding can be a model cavity: every void is smaller than a support contact, and any sealed drainage chamber is a single grid cell. A hollow part without a drain still fails.
- Every report states the longest unbraced pillar run and its slenderness, for pillars on the plate and on the model (`supports.unbraced`).

## Editor

- **Settings search** ranks every setting by its path, label, CLI flag and help text. It tolerates typos, filters the settings pages and highlights the best match. When the current settings mode hides a match, a link offers to switch modes.
- **Typed settings pages** for hollowing, peel analysis, assembly and resources. The raw JSON box is now only an Expert fallback.
- **Keyboard shortcuts** can be changed under Configuration → Shortcuts. The editor refuses conflicting keys, can reset one shortcut or all of them, and saves your changes.
- **Layer viewer pixel readout:** hover over a layer to see the pixel's coordinates, its position in mm, its value and any issue marked there.
- **Profile library** asks before replacing unsaved editor changes, and before saving over an existing profile.
- **Islands** are no longer re-checked after every edit by default, but still after supports are generated. *Verification → Re-check islands after every edit* restores the old behaviour.
- The Setup form has checkboxes for bracing and for bracing pillars that stand on the model.

## Command line

- `--brace-model-pillars / --no-brace-model-pillars`.
- The brace options' help text now states the current defaults.

This is a beta. The GUI editor, CLI (`prepare`, `slice`, …), and CPU-only native kernels are bundled. CUDA is not. Qt is used via PySide6 under LGPL v3; see `licenses/THIRD-PARTY.md`. Physical print strength has not been validated.

## Install

### Linux x86_64

```sh
chmod +x VoxelMill-{version}-linux-x86_64.AppImage
./VoxelMill-{version}-linux-x86_64.AppImage --help
./VoxelMill-{version}-linux-x86_64.AppImage gui
```

Host OpenGL and X11 or Wayland are required for the editor. They are not bundled.

### macOS (Intel or Apple Silicon)

Open the matching DMG (`macos-x86_64` for Intel, `macos-arm64` for Apple Silicon) and copy `VoxelMill.app` to Applications. The build is unsigned, so clear Gatekeeper quarantine before the first launch:

```sh
xattr -dr com.apple.quarantine /Applications/VoxelMill.app
open /Applications/VoxelMill.app
/Applications/VoxelMill.app/Contents/MacOS/VoxelMill --help
```

If macOS still refuses: **System Settings → Privacy & Security → Open Anyway**.

### Windows x64

Unzip `VoxelMill-{version}-windows-x64.zip` and run `VoxelMill\VoxelMill.exe`. The exe is a console program, so `VoxelMill.exe prepare …` and other CLI subcommands work. SmartScreen may warn on an unsigned zip; choose **Open anyway**.
