Alpha {version}: portable Linux / macOS / Windows builds. Unsigned.

## Configurable supports

- A dedicated **Bracing** tab exposes vertical spacing, support-to-support reach, maximum branch length, and diameter. Common controls are also visible in Simple Setup.
- Choose **Supports only**, **Base only**, or **Supports or base** destinations. Adjust density from 1–8 connections per node, branch angle, minimum origin height, and fan rotation.
- Select single diagonals, alternating diagonals, or X bracing. X pairs require reciprocal vertical shaft spans and share a junction at their crossing.
- Use **Show part-to-part supports** to demonstrate model anchors in a clear lower/upper model-gap example. The action explicitly enables model anchors and sets avoidance to zero in the editor draft.
- Angled and tree shafts now join conical tip bases continuously, closing the visible notch beneath the tip.
- A new **Showcase** example puts every support kind in one picture: a plate route, a branch around a blocker, a model anchor, a thin model pillar, a supported island, and the brace network. It forces the six settings those routes need and lists them under the picture, so nothing has to be found first. Part-to-part anchor fields and thin-pillar fields are now separate tabs, since thin pillars in middle mode apply to every pillar, not only to part-to-part routes.

## Editor navigation and clarity

- The navigation cube's twelve 45° sides are now clickable, so all 26 facets it draws select a view. Only facet perimeters are outlined; the lines that used to cross every face are gone.
- The orbit arrows step **45°**, matching the sides the cube shows, and sit close to the cube instead of far out from it. Two new chevrons on the top row roll the view 45° left or right in its own plane.
- Every option carries hover text explaining what it does, from one shared table both the Setup rows and the dedicated editors read. Previously the part-to-part and thin-pillar fields had no explanation in either place. The **hover text delay** is adjustable in Configuration → Preferences (default 1000 ms) and applies immediately.
- The Report tab lists every report field as Parameter / Value rows with collapsible groups, instead of a raw JSON dump. Right-click still gives **Copy report as JSON**; the payload is unchanged.
- `gui --screenshot PNG` writes an image of the window, 3D view included. It captures the app's own window, so it needs no screen-recording permission and works over SSH on every platform.
- Fixed: a leftover autosave made every unattended launch stop on a recovery prompt nobody could answer. That prompt now skips when the wizard does, and the autosave is left in place for the next interactive start.

The CLI, presets, projects, and routing reports preserve these options. Main pillars remain; default brace spacing is 15 mm, maximum complete length is 30 mm, and angle is 45°. Model parts never anchor braces. Branch thickness, clearance, build boundaries, complete base footprints, cancellation, and candidate limits remain checked.

Linux verification includes the full test suite, prepared-mesh and decoded-layer connectivity checks, and actual-render AppImage acceptance. A few tests require unavailable reference files, FreeCAD, or shells. Support struts are checked for reaching what they were routed to by unioning the router's own solids and requiring a single connected component. Physical print strength has not been validated.

This is an alpha. The GUI editor, CLI (`prepare`, `slice`, …), and CPU-only native kernels are bundled. CUDA is not. Qt is used via PySide6 under LGPL v3; see `licenses/THIRD-PARTY.md`.

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
