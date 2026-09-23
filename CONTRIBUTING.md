# Contributing to VoxelMill

VoxelMill prepares a single part for resin printing and then proves what it
wrote: every exported file is re-opened and validated at printer pitch rather
than trusted from the in-memory solid. That habit is the project's character,
and it is the thing most worth preserving in a change.

Read [`docs/architecture.md`](docs/architecture.md) for the file layout and
[`gotchas.md`](gotchas.md) before you debug anything. `gotchas.md` is a list of
traps that already cost someone a day: a Cocoa expose that hangs on
`vtkFeatureEdges`, a hover-wheel that silently edits a spin box, a startup
modal that blocks every unattended launch. Most of them are not guessable.

## Choosing a language: C++, Python, or neither

The split is by measurement, not by taste.

**Python orchestrates. C++ does the per-voxel and per-triangle work.**
The hot kernels already live in `native/` (`raster.cpp`, `mesh.cpp`,
`voxel.cpp`, `goo.cpp`, `ctb.cpp`, `distance.cpp`, `intersections.cpp`),
bound through pybind11 in `native/module.cpp` and imported as
`voxelmill._native`. Everything else, the CLI, settings resolution, routing
policy, reports and the GUI, is Python, and is fast enough.

Three rules follow from that, and each was learned the expensive way:

1. **Measure before you port.** Python interpreter startup is not the
   bottleneck: `import voxelmill.cli` is 0.10 s and `numpy + scipy +
   manifold3d` another 0.20 s. A standalone binary would save about 0.3 s per
   run. A standalone C CLI was designed, measured and cancelled, because it
   would have duplicated argument parsing, settings resolution and the report
   contracts without shrinking the Python surface that actually costs time.
   If your rewrite does not have a benchmark showing the win, it is not a win.

2. **Prefer array work to a new kernel.** A loop over triangles in Python is
   a bug; the same loop expressed once in NumPy is usually within a small
   factor of C++ and stays readable and testable from Python. Reach for
   `native/` when the operation is genuinely per-voxel over a full raster, or
   when it needs a data layout NumPy cannot express.

3. **C++, not C.** The kernels are already C++ and rewriting them in C gains
   nothing; the remaining wins are architectural, not syntactic. Of the ten
   files in `native/`, only `edt.cpp` calls `tbb::parallel_for`;
   `tbb::global_control` is bound as a worker *ceiling* rather than as
   anything that creates work. So if you are chasing throughput, the
   remaining kernels are largely serial, and that is a far better target
   than a language change.

New native code needs a Python fallback or an honest skip when the module is
missing, because the package has to import on a machine that never built it.
CUDA is optional and is **not** bundled in releases: a release build is
CPU-only, and `scripts/appimage_acceptance.py` asserts exactly that.

## Style

There is no linter and no formatter in this repository, which means the
existing code is the style guide. Match the file you are editing.

- **Comments say why, not what.** The code already says what it does. A
  comment earns its place by recording the reason, the measurement, or the
  failure that made this the shape it is. Look at the constants in
  `src/voxelmill/gui/viewport.py` for the intended density.
- **Docstrings lead with one line, then the reasoning.** Say what the
  function returns and which assumption it depends on.
- Four spaces, no tabs. Lines run to roughly 100 characters.
- Type hints where they clarify a contract; this is not a fully annotated
  codebase and partial annotation is fine.
- Errors are `VoxelMillError(code, message)` from `contracts.py`, with a
  machine-readable code, because the CLI turns them into JSON.
- No em dashes in comments or documentation; use a plain double hyphen.
- Settings live in `config.DEFAULTS`. Both GUI surfaces are generated from it,
  so a new setting appears in the Setup table and the dedicated editors
  automatically, and a test asserts that coverage. Give it an entry in
  `src/voxelmill/gui/helptext.py` in the same commit, or the hover text will
  be the field's own name read back at the user.

## Claims and evidence

Be careful about what the software asserts. This project is deliberately
conservative about the difference between *measured*, *computed* and
*guessed*:

- Anything not fitted to measured prints is labelled **uncalibrated**, in the
  settings metadata and in the hover text. Keep it that way.
- A report says what was checked and what was not. "Preparation intentionally
  skips drainage analysis in this harness" is the kind of sentence that has to
  survive.
- Nothing here certifies physical strength, drainage or printability. Do not
  add wording that implies otherwise.

## Tests

```sh
.venv/bin/python -m pytest -q                 # the usual gate
.venv/bin/python -m pytest -q -m gui          # editor tests, offscreen Qt
VOXELMILL_SAMPLES=1 .venv/bin/python -m pytest -q -ra    # full, with sample fixtures
.venv/bin/python scripts/check.py             # tests plus the benchmark comparison
.venv/bin/python scripts/equivalence.py --jobs 4        # reports vs reports/golden/
.venv/bin/ruff check src tests scripts         # lint (pip install '.[lint]')
.venv/bin/mypy src/voxelmill                  # types; see [tool.mypy] overrides
```

`.github/workflows/ci.yml` runs the suite on every push and pull request, and
runs the golden comparison as an advisory job. When a change alters reports on
purpose, re-record with `scripts/equivalence.py --update-golden` and review the
JSON diff in the same commit.

There is no `conftest.py`. GUI test files set `QT_QPA_PLATFORM=offscreen`
themselves at the top of the file, before importing PySide6; copy that pattern
in a new one. A test that needs a real window runs in a child process under
`xvfb-run` with `QT_QPA_PLATFORM=xcb`, because VTK opens a genuine X window
and the offscreen platform hands it a window id the server rejects, which
kills the whole session rather than one test. `tests/test_gui.py` has the
pattern.

What a good test looks like here:

- **Assert the defect, not the implementation.** The navigation cube test
  asserts that a click on a 45 degree facet selects a view, not that a
  particular branch was taken.
- **Prove the property, not the picture.** "This support reaches the model"
  is checked by unioning the router's own solids and requiring a single
  connected component, because sampling the column under a contact gives a
  false positive for any branched strut that leaves that column.
- **A test for a display-dependent bug asserts on the source** when the
  failure cannot be reproduced on the test host. A device-pixel-ratio bug is
  invisible at ratio 1.
- **Never let a test hang where the bug hung.** If the defect was a modal
  nobody could dismiss, assert the modal is never constructed.

Add a line to `gotchas.md` when you lose time to something non-obvious. That
file is the most valuable documentation in the repository.

## Pull requests

- One subject per pull request. A fix and a refactor are two.
- Say what you verified and on what. "1,272 passed on Linux; macOS untested"
  is a useful sentence; "tests pass" is not.
- Include the failing case in the same commit as the fix.
- Update the docs in `docs/` in the same commit as the behaviour. A setting
  whose meaning changed and whose hover text did not is an unfinished change.
- Platform coverage is asymmetric: Linux is developed and tested locally,
  macOS and Windows are built by GitHub Actions and tested by hand when
  hardware is available. Say plainly which ones you actually ran.

## Releases

Maintainer-only, and documented in [`docs/packaging.md`](docs/packaging.md).
The short version: build a CPU-only native extension, stage the AppImage with
`--native-extension`, pass `scripts/appimage_acceptance.py`, run the release
workflow manually until it is green, and only then push the tag. The
verification record for each release lives in `reports/releases/`.
