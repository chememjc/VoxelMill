Beta {version}: portable Linux / macOS / Windows builds. Unsigned. A bug-fix and feature update to the first beta.

## Editor

- **STEP import adds to the plate.** Import STEP used to open its result as a new document and discard the parts already on the plate. It now adds the part(s) and re-arranges the plate, like Add model. With nothing loaded, the first file opens the plate and the rest are added once it is placed.
- **Open several files at once.** Open STL, Import STEP and Add model accept multi-selection. Open STL still replaces the plate; Import STEP, Add model and drops add to it. STEP files can be dropped on the window when FreeCAD is available.
- **Rotation gizmo keeps earlier rotations.** In *Absolute from import pose* mode, grabbing a second rotation ring reset the part to its import pose plus that one axis, and also reset its position and lift. Every drag now starts from the current pose in both modes; the mode only changes what the panel fields show. Ring turns are now composed exactly (a turn about X on a part already turned about Z used to land somewhere else), only the dragged angle is snapped, an auto-oriented part turns from its found orientation, and the camera no longer re-frames after every drag.
- **Smooth pane resizing on macOS.** The Mac build's 3D view was stuck in a continuous repaint loop, which made dragging the pane separators slow on Intel Macs. Renders are now coalesced to one per 40 ms during a drag, and the Layers view reuses its image while its size changes.
- **Support explanations in the Report tab.** After Compute attachments, any contact that could not be routed is listed with the reason and, in its tooltip, the settings worth changing. Activating it jumps to its layer.

## Supports

- **Knife-edge islands are supported.** Where an overhanging wall meets a slope diagonally to the pixel grid, single pixels can hang from the layer below by a corner, directly above the part and too close to it for a model anchor. Supports could not reach them, so export was withheld. They now get a short stub from the part below. The same island is no longer reported and routed twice.
- **Supports no longer pass through each other's tips.** Routed tips are now reserved, so a later pillar, anchor, tree or brace avoids them. This fixes the tip-versus-pillar `support_overlap` warnings.
- **Thicker tree trunks.** New setting `support.trunk_diameter_mm` (`--trunk-diameter-mm`, default 1.2 mm) makes a tree's shared trunk thicker than its 0.9 mm branches. A value below the branch diameter is raised to it; projects saved before this setting existed keep branch-thick trunks. The `heavy` preset uses 1.8 mm. A trunk that would hit the model or another support falls back to separate pillars, and the report counts why.
- **Failures say why.** `support_unroutable` now names what blocked the contact (the part below it, a branch that would start inside the part, material too close for an anchor, a neighbouring tip, the part-to-part policy) with the obstruction height and the settings to change. `incomplete_support_routes` counts failures by reason and lists positions. `support_overlap` names the two supports and where they meet. `scripts/routing_probe.py` accepts a project file.

## Reports

- **Resin usage by model and supports.** `prepare`, the editor's validation and `report-html` report cured resin as total, model and supports (including any raft or base), in mL, and in grams when the resin profile sets a density. Without a density, only mL is reported. The bundled Sunlu ABS-like gray profile now sets 1.10 g/cm³. `slice` reads a merged STL and reports the total only.

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
