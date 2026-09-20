# Packaging

## AppImage

`scripts/build_appimage.py` stages a relocatable AppDir and, with
`appimagetool`, packs `output/appimage/VoxelMill-x86_64.AppImage`.
`scripts/make_appimage.sh` is a thin wrapper that picks the project venv
Python when present and forwards every argument to `build_appimage.py`.

The project virtualenv is not relocatable: `/.venv/bin/python3` is a symlink
to `/usr/bin/python3`. The stager therefore copies CPython, the stdlib,
VoxelMill, and the runtime site-packages into `AppDir/usr`. `AppRun` sets
`PYTHONHOME` and `PYTHONNOUSERSITE` so a host venv cannot leak in.

Linux x86_64 AppImage, macOS DMGs, and the Windows zip are built on GitHub
Actions from tag `v0.*` (workflow
[`.github/workflows/release.yml`](../.github/workflows/release.yml); see
also [`../platforms.md`](../platforms.md)). Rehearse with `workflow_dispatch`
before tagging.

## macOS and Windows portables (PyInstaller)

Linux stays on the AppImage path above. macOS and Windows use
`packaging/pyinstaller.spec` via `scripts/build_pyinstaller.py` (no
Homebrew). Output is under `output/portable/`.

- **Two thin Mac DMGs**, not a universal2 binary: VTK and PySide6 wheels are
  architecture-specific, so Actions builds arm64 on `macos-14` and x86_64 on
  `macos-15-intel`, each producing its own `.app` then `hdiutil` DMG.
- **Unsigned.** After download, clear Gatekeeper quarantine before first open:

  ```sh
  xattr -dr com.apple.quarantine /path/to/VoxelMill.app
  # or on the DMG mount / copied app
  ```

- **Windows:** unzip `VoxelMill-windows-x64.zip` and run `VoxelMill.exe`
  (`console=True` so `VoxelMill.exe prepare …` and other CLI subcommands work).

### Icons

The SVG artwork in `icons/` is canonical. Run
`.venv/bin/python scripts/generate_icons.py` after changing it; the script uses
QtSvg to render dark full-color, light, and symbolic variants at the distributed
hicolor sizes (16 through 512 px plus 1024 px) and copies the dark 256 px image
to the AppImage desktop fallback and package data, and writes
`packaging/voxelmill.icns` (macOS `.app` / Finder / Dock) plus
`packaging/voxelmill.ico` (Windows exe). The dark full-color variant
is the application default; symbolic artwork is intended for small or visually
busy contexts. `scripts/generate_icons.py --check` verifies that generated files
match the canonical SVGs without rewriting them.

`build_appimage.py` stages the dark icon as the AppImage root icon and copies
the complete hicolor tree, including scalable SVGs, under
`usr/share/icons/hicolor`. This lets desktop environments select a native
resolution while retaining the 256 px fallback.

```sh
# Fast CLI-only image for tests and headless machines
.venv/bin/python scripts/build_appimage.py --cli-only --stage-only \
  --appdir output/appimage/VoxelMill.AppDir
# Same via the wrapper:
# scripts/make_appimage.sh --cli-only --stage-only --appdir output/appimage/VoxelMill.AppDir

# Full editor (includes PySide6 and VTK; ~406 MiB packed)
curl -L -o packaging/appimage/appimagetool \
  https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x packaging/appimage/appimagetool
.venv/bin/python scripts/build_appimage.py
./output/appimage/VoxelMill-x86_64.AppImage --help
./output/appimage/VoxelMill-x86_64.AppImage gui part.stl
```

All path arguments are resolved before the stager runs its verification child
from `/tmp`. Relative `--appdir`, `--output`, `--tool`, and
`--native-extension` paths are interpreted relative to the directory where
`build_appimage.py` was invoked, rather than a later subprocess directory.

### CPU-only release build

The active development extension may have CUDA enabled. Do not replace it just
to build a portable release: a running Python process can still have that file
mapped. Build a separate CPU extension and select it explicitly when staging:

```sh
cmake -S . -B build/appimage-cpu -G Ninja \
  -Dpybind11_DIR="$(.venv/bin/python -c 'import pybind11; print(pybind11.get_cmake_dir())')" \
  -DCMAKE_CUDA_COMPILER=
cmake --build build/appimage-cpu -j2

APPIMAGE_EXTRACT_AND_RUN=1 .venv/bin/python scripts/build_appimage.py \
  --appdir output/appimage-053/VoxelMill.AppDir \
  --output output/appimage-053/VoxelMill-0.5.3-linux-x86_64.AppImage \
  --tool packaging/appimage/appimagetool \
  --native-extension build/appimage-cpu/_native.cpython-310-x86_64-linux-gnu.so
```

`--native-extension` validates the extension filename, removes any native
module copied from the active environment, and stages the named file before
the isolated import check. Use the extension suffix produced by the build's
Python when it differs from the example. Acceptance also requires the bundled
runtime to report `CUDA compiled=false`.

### Linux artifact acceptance

Run the reusable acceptance test against the packed artifact, outside a
development environment. Actual rendering uses Xvfb and may need to run
outside a restricted execution sandbox:

```sh
.venv/bin/python scripts/appimage_acceptance.py \
  output/appimage-053/VoxelMill-0.5.3-linux-x86_64.AppImage \
  --output-dir output/appimage-053/acceptance-final \
  --expected-version 0.5.3
```

For an artifact downloaded from the tagged release, use a new evidence
directory and record the workflow run that produced it:

```sh
mkdir -p /tmp/voxelmill-v0.5.3
gh release download v0.5.3 \
  --pattern 'VoxelMill-0.5.3-linux-x86_64.AppImage' \
  --dir /tmp/voxelmill-v0.5.3
.venv/bin/python scripts/appimage_acceptance.py \
  /tmp/voxelmill-v0.5.3/VoxelMill-0.5.3-linux-x86_64.AppImage \
  --output-dir output/appimage-053/published-acceptance \
  --expected-version 0.5.3 \
  --workflow-url https://github.com/OWNER/REPOSITORY/actions/runs/RUN_ID
```

The runner clears only files it owns in the selected output directory, so
stale outputs cannot satisfy a rerun. It records the artifact SHA-256 and
verifies:

- version, startup, model inspection, configuration defaults, preparation,
  support regeneration, project save/reopen, slicing, and standalone decoded
  validation;
- that imports and the CPU native extension come from the extracted AppImage,
  never the checkout or active virtualenv;
- downward braces and grounded support routes, zero raster islands, layer
  connectivity and overlap, and decoded-pixel parity;
- exact reopening of the prepared STL as one manifold component wholly at or
  above the plate; and
- a real Qt/VTK framebuffer, a nonempty layer preview, and settings preserved
  after the GUI project is reopened.

Preparation deliberately skips its expensive drainage and void analyses and
uses an explicitly warned export so the harness can inspect the resulting
artifact. That warning is retained in `prepare.json`; it does not relax the
route, island, exact-mesh, render, or pixel checks. Slicing and standalone
verification run their own checks, and warning diagnostics remain in their
reports. `--skip-gui` exists only to record unavailable render coverage; it is
not sufficient for release acceptance.

The concrete v0.5.3 commands, checksum, results, warnings, and unavailable
platform or physical-print coverage are recorded in
[`reports/releases/v0.5.3.md`](../reports/releases/v0.5.3.md).

`--cli-only` omits VTK and PySide6. The editor then fails at import with a
missing-module error rather than a missing system package. A CLI-only image
was smoke-tested at v0.3.0 (`--version` reports the package version; the staged
`voxelmill._native` exposes `extract_runs` and `distance_transform_edt`).

A full editor image (the default, without `--cli-only`) opens the GUI when
double-clicked or when given a single existing file: empty argv (and a single
existing file path) rewrites to `gui` when the binary is frozen or PySide6
imports. `--help`, `prepare`, `slice` and the other subcommands stay CLI. A
CLI-only image has no PySide6 tree, so AppRun does not rewrite the argument
list.

On Windows VirtualBox guests, launching the editor sets `QT_OPENGL=software`
(and `LIBGL_ALWAYS_SOFTWARE`) before `QApplication` when the `VBoxGuest`
service is present. Real GPUs and an already-set `QT_OPENGL` are unchanged.

The desktop file uses `Exec=voxelmill %F` and `Terminal=false`. Host OpenGL /
X11 or Wayland libraries are still required for the editor; they are not
bundled. Qt WebEngine and QML are omitted from the image.

CUDA is not bundled. The image uses the CPU morphology fallback unless the
host has a compatible driver and the native extension was built with CUDA.
Release configures should leave `nvcc` out of the link line so the artifact
does not carry the CUDA Toolkit EULA.

A clean CMake reconfigure needs the venv pybind11:

```sh
cmake -S . -B build/check -Dpybind11_DIR="$(.venv/bin/python -c 'import pybind11; print(pybind11.get_cmake_dir())')"
```

System pybind11 2.9 is rejected; without this path CUDA detection never runs.

## Licensing obligations of a distributed image

VoxelMill is MIT licensed ([`../LICENSE`](../LICENSE)). The AppImage, unlike the
source tree, also carries third-party binaries, and two of them are LGPL. The
full inventory and the reasoning behind each obligation is
[`../licenses/THIRD-PARTY.md`](../licenses/THIRD-PARTY.md); this section is the
operational checklist.

`stage_appdir()` calls `stage_licenses()`, which copies `LICENSE` and the whole
`licenses/` tree to `usr/share/doc/voxelmill/` in the AppDir. Each bundled
package's own `*.dist-info` directory, carrying its upstream notice, is copied
next to the package by `_copy_imported()`. Between them, every permissive
component's "reproduce the notice" requirement is met with no manual step.

Qt, reached through PySide6, is LGPL v3 and needs three things the build cannot
do on its own:

- **Say so.** The About dialog and the release notes must state that the
  application uses Qt via PySide6 under the LGPL v3, naming the bundled Qt
  version, and note that Qt is a trademark of The Qt Company.
- **Offer the source.** Point recipients at <https://download.qt.io/archive/qt/>
  with the bundled version number. Mirroring it yourself is not required.
- **Preserve relinking.** LGPL §4(d) requires that a user be able to substitute
  their own build of the library. This works today because PySide6 and Qt are
  loaded dynamically by the Python import system, and it must stay that way:
  do not statically link Qt, and do not strip the bundled Qt libraries beyond
  what upstream ships. The supported procedure is

  ```sh
  ./VoxelMill-x86_64.AppImage --appimage-extract
  # replace the Qt libraries under squashfs-root/usr/lib/python3.10/site-packages/PySide6/
  ./squashfs-root/AppRun
  ```

One build-time caution. When CMake finds `nvcc`, the CUDA runtime is linked
**statically** into `_native.so`, which puts NVIDIA object code inside the
image under the CUDA Toolkit EULA rather than under the MIT license. For a
release artifact that carries no NVIDIA terms at all, build on a machine
without CUDA or configure with `-DCMAKE_CUDA_COMPILER=`; the CPU morphology
fallback covers the loss.
