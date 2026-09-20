Alpha {version}: portable Linux / macOS / Windows builds. Unsigned.

Bracing now grows downward at 45° from the full-width shoulder below each support tip, while retaining the main pillars. Vertical branch spacing defaults to 15 mm and maximum actual branch length to 30 mm; both are editable independently of support spacing and neighbour distance. The obsolete bottom-up brace-start setting has been removed.

Branches connect to grounded supports or existing branches, or form a new plate landing using the configured base style. Model parts never anchor braces. Full branch thickness, model clearance, build boundaries and complete new feet are checked before a branch is accepted. Support graphs and reports include branch connections and rejection evidence.

Primary part-to-part supports now require explicit enabling. That setting does not relax brace grounding or model clearance.

Generated grid bases are simplified within STL coordinate precision to prevent collapsed triangles on reopening. Connected braces can still narrow drainage channels; existing drainage failures continue to block ordinary export.

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
