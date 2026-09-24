Alpha {version}: portable Linux / macOS / Windows builds. Unsigned.

## Fixes

- **Editor outlines under generic OpenGL.** The build-volume wireframe and the navigation cube outlines now draw in virtual machines and on systems without 3D acceleration (Windows guests under VirtualBox showed only the green front edge before).
- **Hex infill** (`hollow.infill = "hex"`) no longer crashes when hollowing.
- **Wall-thickness analysis** no longer refines its voxel grid past the memory budget on thin thresholds.
- **Editor responsiveness:** background jobs run in parallel again when `resources.workers` is automatic (the default), instead of queueing one at a time. Superseded placement scratch files are removed instead of accumulating in the temporary directory.
- **Windows core detection** now reads performance and efficiency cores correctly; it previously always fell back to treating every core alike.
- **Editor settings match validation:** the `assembly.union`, `hollow.mode` and `hollow.infill` dropdowns offer exactly the accepted values (`hex` infill was missing; `raster` and `outer` were offered but refused), and numeric controls no longer allow values that validation rejects.
- The part-to-part support example now says why it routes nothing while part-to-part supports are off.

## Performance

- **Slicing** works from the part's footprint instead of the whole LCD panel for every layer. Written layers are byte-identical to 0.5.4. On a small part at 9K: 24.6 s → 1.3 s; with a mirrored printer 37.7 s → 1.6 s; with 4-level antialiasing 151 s → 13 s.
- **Large parts on 16K panels:** a plate-filling part prepares in 36 s instead of 96 s and slices in 34 s instead of 46 s. Drainage bottleneck checks share work across chambers, and support routing indexes placed shafts spatially.
- Native kernels stay multithreaded in builds without oneTBB (the drainage distance transform ran on one core in 0.5.4 portables).
- The island search no longer builds a full boolean union on every pass, contour and boundary support sampling is vectorized, and the viewport computes its out-of-bounds coloring once per update.

## Command-line changes

- `voxelmill info FILE` reads GOO and classic CTB v3 files; `goo-info` and `ctb-info` remain as aliases.
- A command line that cannot be parsed now exits with status **64**; 2 keeps meaning "the command ran and validation failed". `thickness` no longer returns an undocumented 1.
- What will stay stable from 1.0 on is written down in `docs/stability.md`.

This is an alpha. The GUI editor, CLI (`prepare`, `slice`, …), and CPU-only native kernels are bundled. CUDA is not. Qt is used via PySide6 under LGPL v3; see `licenses/THIRD-PARTY.md`. Physical print strength has not been validated.

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
