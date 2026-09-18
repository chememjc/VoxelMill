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

Linux x86_64 AppImage is the v0.3.0 ship vehicle. macOS and Windows
drag-and-drop packages are built on GitHub Actions (see
[`../platforms.md`](../platforms.md)).

### Icons

The SVG artwork in `icons/` is canonical. Run
`.venv/bin/python scripts/generate_icons.py` after changing it; the script uses
QtSvg to render dark full-color, light, and symbolic variants at the distributed
hicolor sizes (16 through 512 px plus 1024 px) and copies the dark 256 px image
to the AppImage desktop fallback and package data. The dark full-color variant
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

`--cli-only` omits VTK and PySide6. The editor then fails at import with a
missing-module error rather than a missing system package. A CLI-only image
was smoke-tested at v0.2.0 (`--version` reports `0.2.0`; the staged
`voxelmill._native` exposes `extract_runs`).

A full editor image (the default, without `--cli-only`) opens the GUI when
double-clicked or when given a single existing file. `--help`, `prepare`,
`slice` and the other subcommands stay CLI. A CLI-only image has no PySide6
tree, so AppRun does not rewrite the argument list.

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
