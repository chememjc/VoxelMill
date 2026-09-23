# Third-party components and license obligations

VoxelMill itself is MIT licensed; see [`../LICENSE`](../LICENSE). Nothing in
this repository is third-party code — no library is vendored into `src/`,
`native/`, `scripts/` or `fixtures/`. This file inventories everything
VoxelMill *depends on*, links against, or bundles into a distributable
AppImage, and states what each one asks of a redistributor.

Audited 2026-09-12 against the versions pinned in `pyproject.toml` and the
versions actually installed on the build machine.

Audited again 2026-09-17 for v0.2.0: added original `native/runs.cpp` row-RLE
/ run-CCL kernels; no new runtime, GUI, or native third-party dependency;
pybind11 remains 3.0.4 (BSD-3-Clause). `native/runs.cpp` is original VoxelMill
code. It reproduces dense NumPy / `scipy.ndimage.label` results bit-for-bit
by design; no SciPy, OpenCV, or other third-party source was copied.
Behavioral compatibility with `scipy.ndimage.label` component numbering is
not a license dependency on SciPy beyond the existing BSD runtime dependency
already listed below. Optional CUDA morphology in `native/cuda_morphology.cu`
remains original host/device code; only a binary built when `nvcc` is present
statically incorporates the NVIDIA CUDA runtime under the CUDA Toolkit EULA.

**Nothing below conflicts with the MIT license.** No copyleft component is
statically linked or derived from. The two LGPL components (Qt via PySide6, and
the system C/C++ runtimes) are dynamically linked, which places notice and
relinking duties on a *binary* distribution and no condition at all on this
source code. Those duties are listed under
[AppImage distribution obligations](#appimage-distribution-obligations).

## Runtime dependencies

Installed by pip; not redistributed by this repository, but bundled into the
AppImage.

| Component | Version | License | Text | Obligation |
|---|---|---|---|---|
| NumPy | 1.26.4 (`>=1.26,<3`) | BSD-3-Clause | [`third-party/numpy-LICENSE.txt`](third-party/numpy-LICENSE.txt) | Reproduce copyright notice |
| SciPy | 1.12.0 (`>=1.10,<2`) | BSD-3-Clause | [`third-party/scipy-LICENSE.txt`](third-party/scipy-LICENSE.txt) | Reproduce copyright notice |
| manifold3d | 3.3.2 (pinned) | Apache-2.0 | [`third-party/manifold3d-LICENSE.txt`](third-party/manifold3d-LICENSE.txt) | Reproduce notice; state changes if modified (none made) |
| threadpoolctl | 3.6.0 (`>=3.1,<4`) | BSD-3-Clause | [`third-party/threadpoolctl-LICENSE.txt`](third-party/threadpoolctl-LICENSE.txt) | Reproduce copyright notice |
| websocket-client | 1.8.0 (`>=1.7,<2`) | Apache-2.0 | [`third-party/websocket-client-LICENSE.txt`](third-party/websocket-client-LICENSE.txt) | Reproduce notice |
| tomli | 2.2.1 (pinned, Python < 3.11) | MIT | [`third-party/tomli-LICENSE.txt`](third-party/tomli-LICENSE.txt) | Reproduce copyright notice |

## Optional GUI dependencies (`[gui]` extra)

| Component | Version | License | Text | Obligation |
|---|---|---|---|---|
| PySide6 / Qt 6 | 6.11.1 (`>=6.6,<7`) | LGPL-3.0-only (upstream offers GPL-2.0/GPL-3.0 as alternatives) | [`third-party/LGPL-3.0.txt`](third-party/LGPL-3.0.txt), [`third-party/GPL-3.0.txt`](third-party/GPL-3.0.txt) | **See below** — notice, license text, relinking, source offer |
| shiboken6 | 6.11.1 | LGPL-3.0-only | same as PySide6 | Same as PySide6 |
| VTK | 9.7.0 (`>=9.3,<10`) | BSD-3-Clause | [`third-party/vtk-LICENSE.txt`](third-party/vtk-LICENSE.txt) | Reproduce copyright notice |

PySide6 is imported dynamically through Python's normal import machinery. It is
never statically linked and VoxelMill contains no Qt derivative work, so the
LGPL reaches the AppImage bundle only, not this source tree.

## Test dependencies (`[test]` extra)

Not redistributed and not bundled.

| Component | Version | License |
|---|---|---|
| pytest | 8.4.2 (`>=7,<9`) | MIT |
| psutil | 5.9.5 (`>=5,<8`) | BSD-3-Clause — [`third-party/psutil-LICENSE.txt`](third-party/psutil-LICENSE.txt) |
| jsonschema | 4.26.0 (`>=4,<5`) | MIT |

## Build-time dependencies

| Component | Version | License | Note |
|---|---|---|---|
| pybind11 | 3.0.4 (pinned) | BSD-3-Clause — [`third-party/pybind11-LICENSE.txt`](third-party/pybind11-LICENSE.txt) | Header-only, compiled into `_native.so`. BSD permits this; reproduce the notice in binary distributions. |
| scikit-build-core | 0.11.6 (pinned) | Apache-2.0 | Build backend only; no code reaches the artifact. |
| CMake | `>=3.18` | BSD-3-Clause | Build tool; not distributed. |

## Native libraries linked into `_native.so`

| Component | Linkage | License | Text | Obligation |
|---|---|---|---|---|
| oneTBB (`libtbb.so.12`) | Dynamic, optional (`VOXELMILL_TBB`) | Apache-2.0 | [`third-party/onetbb-COPYRIGHT.txt`](third-party/onetbb-COPYRIGHT.txt), [`third-party/Apache-2.0.txt`](third-party/Apache-2.0.txt) | Reproduce notice |
| libstdc++ / libgcc_s | Dynamic | GPL-3.0 **with GCC Runtime Library Exception** | [`third-party/GPL-3.0.txt`](third-party/GPL-3.0.txt) | The Runtime Library Exception explicitly permits linking from non-GPL software. No obligation on VoxelMill's license. |
| glibc (`libc`, `libm`) | Dynamic | LGPL-2.1-or-later | — | Dynamic linking only; system library. Not bundled into the AppImage. |
| NVIDIA CUDA runtime | **Static**, only when `nvcc` is present at build time | NVIDIA CUDA Toolkit EULA | — | **See below** |

### CUDA is statically linked when built on a CUDA machine

`CMakeLists.txt` enables `native/cuda_bind.cpp` and `native/cuda_morphology.cu`
whenever CMake finds a CUDA compiler. CMake's default
`CUDA_RUNTIME_LIBRARY=Static` then links the CUDA runtime *into* `_native.so`:
verified on this machine by the absence of `libcudart` from `ldd` alongside
`cudart` strings in the binary.

That object code is NVIDIA's, not yours. The CUDA EULA permits redistributing
the runtime as part of an application, but:

- Your MIT grant covers your code only. It cannot and does not grant recipients
  rights to NVIDIA's statically linked runtime.
- A binary built this way must carry the NVIDIA attribution the EULA requires.

If you would rather not carry that at all, build release artifacts on a machine
without `nvcc`, or configure with `-DCMAKE_CUDA_COMPILER=`. The CUDA path is
entirely optional — `voxelmill.acceleration` falls back to the CPU morphology
path and every test passes without it.

## Bundled into the AppImage

Everything above that is a runtime or GUI dependency, plus:

| Component | Version | License | Text | Obligation |
|---|---|---|---|---|
| CPython | 3.10 | PSF License Agreement 2.0 | [`third-party/cpython-LICENSE.txt`](third-party/cpython-LICENSE.txt) | Reproduce the PSF license and copyright in the distribution |

## External tools invoked as subprocesses

These are run as separate processes with no linking and no shared address
space. Under both the GPL/LGPL's own terms and settled practice, invoking a
program at arm's length creates no derivative work and imposes no license
obligation. Neither is bundled or redistributed.

| Tool | License | How it is used |
|---|---|---|
| FreeCAD | LGPL-2.1-or-later | STEP import: `voxelmill.importers` runs the FreeCAD AppImage with `-c` and a script (`src/voxelmill/data/step_tessellate.py`). The user supplies FreeCAD. |
| appimagetool (AppImageKit) | MIT | `scripts/build_appimage.py --tool`. The user supplies it; it is not committed (`.gitignore`). |

## AppImage distribution obligations

`scripts/build_appimage.py` calls `stage_licenses()`, which copies
[`../LICENSE`](../LICENSE) and this whole `licenses/` directory into
`usr/share/doc/voxelmill/` inside the AppDir. Upstream `*.dist-info`
directories — each carrying its project's own notice — are copied alongside
every bundled package by `_copy_imported()`. That satisfies the "reproduce the
notice" duty for every permissive component above.

The LGPL components need four things, all satisfied:

1. **Notice.** State in the application and its documentation that VoxelMill
   uses Qt via PySide6 under the LGPL v3, naming the bundled version. Qt is a
   trademark of The Qt Company. See `docs/packaging.md`.
2. **License text.** `LGPL-3.0.txt` and `GPL-3.0.txt` (LGPLv3 incorporates
   GPLv3 by reference) ship in the AppDir via `stage_licenses()`.
3. **The right to relink.** LGPL §4(d) requires the user be able to replace the
   library and re-run the application. VoxelMill satisfies this by
   construction: PySide6 and Qt load dynamically through Python's import
   system, so extracting the AppImage, swapping the libraries and re-running
   `AppRun` works. Do not defeat this — no static Qt, and no stripping of the
   bundled Qt libraries beyond what upstream ships. The procedure is documented
   in `docs/packaging.md`.
4. **Source availability.** LGPL §4 wants recipients able to obtain the
   library's source. A written offer naming the bundled version and pointing at
   <https://download.qt.io/archive/qt/> satisfies this; you do not have to
   mirror it.

## Removed to reach a clean MIT license

| Component | License | Resolution |
|---|---|---|
| CGAL (`AABB_tree`, `AABB_traits`, `AABB_triangle_primitive`, `box_intersection_d`) | **GPL-3.0-or-later** or commercial | **Removed 2026-09-12.** These are header-only templates that compiled directly into `_native.so`, making it a derivative work and forcing GPL-3.0-or-later on the whole program. Replaced by `native/geom.hpp`: an original BVH, exact orientation predicates in double-double arithmetic, and segment/triangle intersection tests. |
| CGAL kernel and `intersections.h` | LGPL-3.0-or-later or commercial | Removed with the above. Header-only LGPL compiled into a binary also raises a relinking question that no longer arises. |
| GMP (`libgmp.so.10`) | LGPL-3.0-or-later / GPL-2.0-or-later | Was pulled in transitively by CGAL. Gone; `ldd _native.so` no longer lists it. |

## Reference material consulted

No code was taken from any of these. Listed for transparency.

| Source | License | How it was used |
|---|---|---|
| ELEGOO GOO format specification | Published by ELEGOO at <https://github.com/elegooofficial/GOO> | **Normative source** for `native/goo.cpp`. Every offset, type and endianness claim was verified byte-for-byte against a reference file; see `docs/goo-format.md`. |
| UVtools `GooFile.cs` | AGPL-3.0 | Consulted **only as interoperability evidence**, to resolve places where the official specification contradicts itself. No code was copied. `docs/goo-format.md` pins the revision consulted and records each disagreement. Producing the same bytes as another encoder is format compatibility, not derivation — file formats are not copyrightable (*SAS Institute v. World Programming*, C-406/10), and elements dictated by an external format specification are filtered out of infringement analysis (*Computer Associates v. Altai*). |
| CHITUBOX support documentation | Vendor documentation | Numeric parameter values transcribed into the `chitubox-mars5` preset in `src/voxelmill/presets.py`, which records the source URL and marks every value the table does not determine. Facts such as measurement values are not copyrightable (*Feist v. Rural*). CHITUBOX is a trademark of its owner; the preset name identifies the settings it transcribes and implies no affiliation. |
