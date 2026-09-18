# Platforms

VoxelMill ships as a Linux AppImage plus GitHub Actions portables for macOS
and Windows on `v5*` tags (see
[`.github/workflows/release.yml`](.github/workflows/release.yml) and
[docs/packaging.md](docs/packaging.md)). This page records OS differences and
what must stay as it is.

Related reading: [docs/packaging.md](docs/packaging.md) (AppImage and PyInstaller),
[licenses/THIRD-PARTY.md](licenses/THIRD-PARTY.md) (dependency inventory),
[LICENSE](LICENSE) (SCOPE OF THIS LICENSE).

## Portability conclusion

Keep the CLI, GUI, CPU topology detection, FreeCAD STEP driver, SDCP printer
adapter, and packaging glue in Python. Keep the hot kernels in the
`_native` C++17 pybind11 module. Do **not** write a native CLI for
portability: OS differences already live in Python (`topology.py`,
`resources.py`, `importers.py`), and a second CLI would duplicate argument
parsing, settings resolution, and report contracts without shrinking the
port surface.

The native module is intentionally thin OS-wise: CMake enables optional TBB
and optional CUDA when present, otherwise the same sources build a CPU-only
extension. Topology detection already has Linux, Darwin, and Windows paths
with a uniform fallback; affinity and address-space ceilings do not.

## Status

| Vehicle | Role |
|---|---|
| Linux x86_64 AppImage (`scripts/build_appimage.py`) | Ship vehicle; Actions job on `ubuntu-22.04`. |
| macOS arm64 `.app` / DMG (PyInstaller) | Actions `macos-14`; thin arm64 only (not universal2). Unsigned. |
| macOS x86_64 `.app` / DMG (PyInstaller) | Actions `macos-15-intel`; thin x86_64. Unsigned. |
| Windows onedir zip (PyInstaller `VoxelMill.exe`) | Actions `windows-latest`; unzip and run. Unsigned. |

Do **not** PyInstaller on Linux for release — keep the AppImage. Two thin Mac
DMGs are intentional: VTK/PySide6 wheels are not universal2. Codesign and
notarization remain follow-up work; until then macOS users need
`xattr -dr com.apple.quarantine` on the `.app`.

## Comparison

| Concern | Linux x86_64 | macOS x86_64 | macOS arm64 | Windows |
|---|---|---|---|---|
| **CPython** | Bundled into AppDir (`PYTHONHOME`); host 3.10+ for editable builds | Must bundle or pin a framework/build Python; SIP and notarization constrain relocation | Same; prefer arm64-native CPython, not Rosetta-only | Embeddable CPython or venv layout; path length and `python.exe` vs launcher |
| **pybind11 C++17 `_native`** | gcc/clang via scikit-build-core; works today | Xcode clang + CMake; same sources | Same; build arm64 (or universal2) `_native*.so` | MSVC (or clang-cl) + CMake; extension is `.pyd`, not `.so` |
| **Optional TBB** | Dynamic `libtbb.so` when found (`VOXELMILL_TBB`) | Homebrew/oneAPI TBB possible; dyld path must be set in the app bundle | Same | Dynamic `tbb.dll` if found; PATH / delay-load care in the installer |
| **Optional CUDA** | Enabled only if `nvcc` at configure; static cudart by default — release builds should use `-DCMAKE_CUDA_COMPILER=` or a machine without nvcc | NVIDIA CUDA on Intel Macs only historically; Apple Silicon has no CUDA. Treat as unavailable | No CUDA | CUDA Toolkit + MSVC possible; same "omit from release" rule if NVIDIA EULA is unwanted |
| **Affinity** | `os.sched_setaffinity` over `/proc/self/task` in `resources.execution_limits` | No public thread-affinity API; `topology` detects cores but cannot pin | Same | `SetThreadAffinityMask` / group APIs exist but are unused; processor groups >64 threads already bail in `topology._detect_windows` |
| **Memory ceiling** | `resource.setrlimit(RLIMIT_AS, …)` soft address-space cap | No `RLIMIT_AS`; jetsam / memory limits are process-external. Soft ceiling must degrade to advisory or Job-object equivalent does not exist | Same | No `RLIMIT_AS`. Job objects can cap commit charge; not wired. `MemoryError` path still needs a substitute or soft fail |
| **Topology** | sysfs hybrid PMU / cpufreq / SMT asymmetry (`topology._detect_linux`) | `sysctl` perflevels (`_detect_macos`); no affinity mask to honor | Same; P/E via perflevel0/1 | `GetLogicalProcessorInformationEx` EfficiencyClass (`_detect_windows`); untested on real hardware |
| **Wheels (numpy / scipy / manifold3d / PySide6 / VTK)** | Manylinux wheels; AppImage copies import trees | macOS x86_64 wheels generally available for pinned ranges | arm64 wheels required; manifold3d and VTK need confirmed arm64 artifacts for the pinned versions | Win_amd64 wheels; VTK and PySide6 pull large DLL trees that the packager must stage |
| **GUI / OpenGL** | Host X11/Wayland + GL; AppImage does not bundle them. Tests use `xvfb-run` | Cocoa + Metal/OpenGL layer; Qt platform `cocoa`. VTK + QVTK must be validated on a real display; no Xvfb | Same; Apple Silicon GL/Metal quirks | ANGLE / desktop OpenGL / Qt `windows` plugin. VTK DLLs and Qt platform plugins must sit next to the exe or on PATH |
| **Distributor format** | AppImage (`output/appimage/VoxelMill-x86_64.AppImage`) | Signed `.app` inside a notarized DMG (or zip). Hardened runtime, entitlements, staple | Same | Portable zip with embedded Python, or Inno/MSI. Code signing recommended for SmartScreen |
| **STEP / FreeCAD** | Headless FreeCAD via PATH / AppImage discovery in `importers.resolve_freecad`; override `VOXELMILL_FREECAD` / `FREECAD` | `interpret_freecad_path` expands `FreeCAD.app` to `Contents/MacOS/FreeCADCmd` (else `FreeCAD`); also searches `/Applications` and `~/Applications` | Same; arm64 FreeCAD build | `FreeCAD*/bin/FreeCADCmd.exe` under Program Files, or an `.exe` path; argv and `QT_QPA_PLATFORM=offscreen` still apply for the helper |
| **SDCP printer** | UDP discovery + WebSocket (`websocket-client`); LAN broadcast | Same sockets stack; local-network permission prompts on recent macOS may apply | Same | Same; Windows Firewall may prompt on first broadcast/listen |
| **Tests** | Full suite on Linux; GUI real-render gated on `xvfb-run` | Affinity tests skip (`sched_setaffinity` absent). Need a Cocoa/offscreen strategy instead of Xvfb | Same | Affinity and `RLIMIT_AS` tests skip or need Windows doubles. No `xvfb-run`; use Qt offscreen / OSMesa / skipped real-render |

## Linux AppImage (verify only)

`docs/packaging.md` and `scripts/build_appimage.py` already define the ship
path: stage a relocatable AppDir with CPython, site-packages, and `_native`,
then pack with `appimagetool`. `--cli-only` drops PySide6 and VTK.

Verification checklist (no redesign):

```sh
.venv/bin/python scripts/build_appimage.py --cli-only --stage-only \
  --appdir output/appimage/VoxelMill.AppDir
# With appimagetool present:
.venv/bin/python scripts/build_appimage.py
./output/appimage/VoxelMill-x86_64.AppImage --help
```

CUDA is not bundled. Release configures should leave `nvcc` out of the link
line so the image does not carry the CUDA Toolkit EULA.

## macOS notes

- **Topology is already sketched.** `_detect_macos` reads `hw.perflevel*` and
  treats every logical cpu as its own core when there is no SMT. Until someone
  runs it on hardware, treat the fallback path as the safe default.
- **Affinity and `RLIMIT_AS` cannot be ported as-is.** `resources.execution_limits`
  must no-op or substitute soft limits on Darwin; worker counts from
  `topology.default_workers` still apply.
- **Codesign and notarization dominate calendar time.** A PySide6+VTK `.app`
  that loads unsigned native code will fail Gatekeeper; Apple's notarization
  pipeline (ticket staple, hardened runtime) is the long pole, not CMake.
- **FreeCAD** is an external subprocess today. `find_freecad` accepts a
  `FreeCAD.app` bundle (resolved to `Contents/MacOS/FreeCADCmd`) as well as
  `VOXELMILL_FREECAD`; do not assume the Linux AppImage candidates exist.
- **Wheels.** Confirm `manifold3d==3.3.2` and `vtk>=9.3,<10` publish macOS
  arm64 wheels for the target CPython before promising an arm64 editor image.

## Windows notes

- **Topology is already sketched** via EfficiencyClass; multi-group machines
  raise and fall back. Untested.
- **No `resource.RLIMIT_AS`.** Address-space soft ceilings need a Windows
  mechanism (Job object commit limit) or must become advisory. Leaving the
  current `setrlimit` call in place will throw at import/use on Windows.
- **MSVC + scikit-build-core** is the expected extension build. Sources are
  standard C++17; the risk is packaging (DLL search path for TBB, runtime,
  VTK, Qt plugins), not language dialect.
- **VTK/Qt DLL sprawl** is the packaging analogue of the Linux AppImage
  stager: everything the editor imports must be adjacent to the launcher or
  discoverable without a developer shell.
- **FreeCAD** install layouts differ; resolution must accept an `.exe` path
  through the existing env override.

## License

### Source tree

The MIT grant in [LICENSE](LICENSE) covers every file in this repository
(Python under `src/voxelmill/`, C++ under `native/`, scripts, docs, schemas,
icons, fixtures). No third-party source is vendored. That remains true for a
macOS or Windows *source* build: porting does not change the license of the
tree.

`native/runs.cpp` is original work. It reproduces `scipy.ndimage.label`
behavior (component numbering and related contracts) for bit-exact tests; no
SciPy source was copied.

v0.2.0 work on RLE kernels, topology detection, and pybind11 3.0.4 did not
add copyleft to the source tree.

### Binary distributions

A shipped image (AppImage today; `.app` / `.exe` later) is not MIT-only. It
bundles third-party binaries. Obligations are inventoried in
[licenses/THIRD-PARTY.md](licenses/THIRD-PARTY.md); the operational checklist
for the Linux image is in [docs/packaging.md](docs/packaging.md).

| Component class | License signal on a binary image |
|---|---|
| NumPy, SciPy, pybind11, threadpoolctl | BSD — reproduce notices (already staged via `*.dist-info` + `licenses/`) |
| manifold3d; oneTBB when dynamically linked | Apache-2.0 — reproduce notice; oneTBB stays dynamic (`libtbb` / `tbb.dll`) |
| PySide6 / Qt (GUI image only) | LGPL-3.0 — notice, license texts, source offer, preserve dynamic relinking |
| NVIDIA cudart when `nvcc` linked statically | CUDA Toolkit EULA — avoid for release artifacts: `-DCMAKE_CUDA_COMPILER=` or build without nvcc |
| CPython | PSF — reproduce license text |

FreeCAD remains an arm's-length subprocess supplied by the user; it is not
bundled and imposes no license condition on VoxelMill's source or on a
bundle that merely invokes it.

## What would bite a first Mac/Win build

1. **`resources.py`** — `RLIMIT_AS` and `sched_setaffinity` / `/proc/self/task`
   are Linux-only. Import or first `execution_limits` use fails unless gated.
2. **`importers.py` FreeCAD discovery** — Linux looks for PATH binaries and
   `FreeCAD*.AppImage` under the repo/cwd/home. macOS resolves `FreeCAD.app`
   bundles (including `/Applications`); Windows looks under Program Files
   `FreeCAD*/bin`. Env override still wins.
3. **VTK / Qt display backend** — Linux tests assume X11 + `xvfb-run`. macOS
   needs Cocoa; Windows needs platform plugins and DLL placement. Offscreen CI
   is a separate problem on each OS.
4. **Codesign / notarization (macOS) and SmartScreen (Windows)** — unsigned
   native extensions and Qt/VTK bundles are blocked or warned on current
   desktop OS defaults; plan calendar time for certificates and staple/CI
   integration.
5. **Wheel availability** — especially `manifold3d` and VTK on macOS arm64
   for the pinned Python; missing wheels block an editor bundle even when
   `_native` compiles.
6. **Affinity tests** — `tests/test_topology.py` already skips without
   `sched_setaffinity`; Windows/macOS need either skips or alternate
   assertions so CI stays green without pretending to pin threads.
