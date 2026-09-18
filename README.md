# VoxelMill

A staged Linux application for single-part resin-print preparation. Implementation status, evidence and remaining stage gates are tracked in [plan.md](plan.md).

v0.2.0 keeps a Python CLI and optional GUI, with hot geometry and raster work in
the C++ `_native` kernels. The Linux AppImage is the ship vehicle; macOS and
Windows port analysis lives in [platforms.md](platforms.md).

The editor's File menu includes dedicated **Printer**, **Resin**, and **Support**
editors with portable configuration saves. The support editor renders an example
attachment array as dimensions change. Part-to-part supports can be forbidden or
given an adjustable preference; bracing has independent size, spacing, start,
and reach controls. Supports land on a selectable base — the original convex
`plate`, bare `none` feet, per-foot `pad` or `skate` shapes, a spanning-tree
`skeleton`, or a porous `grid` or `hex` lattice, optionally tapered inward from
the plate — and each reports its own measured contact area, open area and
resin volume. All settings and file operations are also
available from the CLI; `voxelmill support-example --output example.stl` exports
the same illustrative array. See [GUI workflows](docs/gui.md) and [CLI options](docs/cli.md).

Development setup (Python 3.10+, CMake, C++17 compiler):

```sh
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install pybind11==3.0.4 scikit-build-core==0.11.6 manifold3d==3.3.2 tomli==2.2.1
.venv/bin/python -m pip install --no-build-isolation -e '.[test]'
.venv/bin/python -m pytest
.venv/bin/voxelmill --help
```

Original files in `inputstl/` and the reference GOO are immutable. Generated files belong under `output/` and are excluded from source control. Small generic test shapes live in `fixtures/shapes/` and are committed; `scripts/make_test_shapes.py` regenerates them reproducibly and `fixtures/shapes/README.md` says what each one is for. The default printer is Mars 5 Ultra; the resin defaults are the user's Sunlu ABS-like gray process, not the reference GOO's settings.

The optional editor needs the GUI extras, for example `pip install -e
'.[gui,test]'`. Its real-render test additionally needs `xvfb-run`, and skips
cleanly without it.

## License

VoxelMill is [MIT licensed](LICENSE). Every file in this repository is original
work; no third-party source is vendored in.

The libraries VoxelMill depends on, links against and bundles into an AppImage
keep their own licenses. All of them are permissive or dynamically linked, and
none conflicts with MIT. [`licenses/THIRD-PARTY.md`](licenses/THIRD-PARTY.md)
inventories every one with its license text and the obligations it places on a
redistributor; [`docs/packaging.md`](docs/packaging.md) has the checklist for
shipping a binary image.

## Documentation

| Page | What it covers |
| --- | --- |
| [cli.md](docs/cli.md) | Every command, option and exit code. |
| [examples.md](docs/examples.md) | Worked runs, including how to read a failing report. |
| [configuration.md](docs/configuration.md) | Profiles, resolved settings and `.voxmil` projects. |
| [profiles.md](docs/profiles.md) | Profile discovery, provenance, comparison and resin binding. |
| [support-presets.md](docs/support-presets.md) | Portable named support presets. |
| [geometry.md](docs/geometry.md) | Coordinates, placement, orientation ranking, repair and support primitives. |
| [algorithms.md](docs/algorithms.md) | What each analysis computes, assumes and cannot conclude. |
| [calibration.md](docs/calibration.md) | Which numbers are measured and which await a real print. |
| [troubleshooting.md](docs/troubleshooting.md) | Named failures and what to do about them. |
| [architecture.md](docs/architecture.md) | File layout and per-function reference. |
| [gui.md](docs/gui.md) | Editor controls, manual workflows and export behavior. |
| [packaging.md](docs/packaging.md) | AppImage staging and a relocatable CPython bundle. |
| [goo-format.md](docs/goo-format.md), [sdcp.md](docs/sdcp.md) | Format and protocol references. |

`gotchas.md` is the verified lessons log: read it before changing the
rasterizer, the drainage analysis, the GUI tests or the native build.

## Measurement scripts

These answer questions about the program rather than doing work for a print.
None of them exports anything, changes a setting or contacts a printer.

| Script | Question it answers |
| --- | --- |
| `scripts/benchmark.py` | Runtime and peak RSS per scenario, each in a fresh process; `--baseline` is the regression gate. |
| `scripts/routing_probe.py` | Why `route_contacts` rejected each contact, and what a candidate fix would recover. |
| `scripts/ingestion_probe.py` | Tier 0 ingestion prerequisites on a real part, with no repair or export. |
| `scripts/goo_orientation_check.py` | Which axis flip of our own raster matches a reference GOO's stored pixels. |
| `scripts/inventory.py`, `scripts/sample_placements.py` | Mesh inventory and placement results over the immutable originals. |

Their reports live under `reports/`, which is committed, unlike `output/`.

Run the tests with `.venv/bin/python -m pytest -q`. The full-resolution suite
over the immutable originals is opt-in:
`VOXELMILL_SAMPLES=1 .venv/bin/python -m pytest -q -m samples`.

For a release or performance change, run the tests and the isolated-process
benchmark gate together:

```sh
.venv/bin/python scripts/check.py
```

A relocatable AppImage (CLI or editor) is built by
`scripts/build_appimage.py` (or the thin wrapper `scripts/make_appimage.sh`);
see [packaging.md](docs/packaging.md).

This compares against `reports/bench/baseline.json` and writes the measurement to
`output/benchmark-check.json`. `--samples` opts into full original fixtures and
sample benchmarks; `--skip-benchmark` performs only the tests. See
[portable support presets](docs/support-presets.md) and
[complete CLI/GUI operation options](docs/gui.md#complete-operation-options)
for the new workflows. A saved GUI project can be prepared headlessly with
`voxelmill prepare edited.voxmil --output supported.stl`.
