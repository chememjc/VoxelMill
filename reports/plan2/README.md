# Tier 0 implementation evidence — 2026-09-07

## Tier 2.1 continuation — 2026-09-08

The support-policy and configuration-editor block finished with **430 passed,
10 skipped** (51.61 s). The opt-in original-fixture suite passed all **9** tests,
including the latch GUI. A new subprocess VTK test verifies that changing a
support parameter changes rendered pixels. Qt/Xvfb tests ran outside the
execution sandbox, as required by the existing display-connectivity gotcha.

All six isolated benchmark scenarios remain within the existing baseline's
time/memory tolerances, with no missing baselines:
[tier2-editors.json](../bench/tier2-editors.json). The benchmark fixture exit
codes retain their existing validation failures; this is performance evidence,
not a passing-print acceptance claim. No device traffic or native rebuild ran
in this continuation. The original Tier 0 evidence below remains unchanged.

## Original Tier 0 checkpoint

All commands were offline. No device discovery, upload or printer operation ran.

The full regression suite passed: **215 passed, 10 optional sample tests skipped**.
It includes the subprocess Xvfb/VTK render test, run outside the execution sandbox.
The final GUI cavity-fill archive regression also passed separately.

## Real-part preparation

[acceptance-summary.json](acceptance-summary.json) records source hashes,
assembly findings, checks, routing counts, runtime, peak RSS and scratch usage.
The full JSON reports are local under `output/plan2/*-prepare.json`, outside
source control. Validation staging STLs were cleaned up after checking; their
hashes remain in the evidence. Every normal export was withheld.

Settings were built-in defaults: native 0.018 mm XY pitch, 0.05 mm layers,
original orientation, 5 mm model lift, drainage and void analysis enabled,
**one correction pass maximum**, no unresolved-export override.

| Part | Assembly | Pixel parity | Seconds | Remaining failed checks |
| --- | --- | --- | ---: | --- |
| Nut | Raster | Pass | 32.5 | Support routes, coverage, drainage |
| Cover | Exact | Not applicable | 166.9 | Growth/span, enclosed voids, support routes, drainage |
| Body | Raster | Pass | 624.5 | Enclosed voids, support routes, coverage, drainage |

These are successful ingestion/reporting runs, not passing print acceptance.
No float-valve GOO was produced. The three calls ran sequentially in one Python
process, so RSS is a process high-water mark, not an isolated per-part benchmark.

Re-run a part in a fresh process (currently expect exit 2 and a saved report):

```sh
.venv/bin/chopchop prepare inputstl/floatvalveR7-body.stl --max-passes 1 --progress --output output/plan2/body-supported.stl --report output/plan2/body-prepare.json
```

## Ingestion prerequisite probe

[body-prerequisites.json](body-prerequisites.json) is the reproducible probe,
including the source hash, complete settings, raw support selection/routing,
experimental two-sided filtering, and every native-pitch layer.

```sh
.venv/bin/python scripts/ingestion_probe.py inputstl/floatvalveR7-body.stl --output reports/plan2/body-prerequisites.json
```

All 1,709 body layers have zero open rows and zero negative winding crossings.
Raw support planning fails 569 of 1,085 routes. The proposed filter removes
11,887 samples but still fails 561 routes. It is not enabled in production.
The initial `body-ingestion-probe.json` and `body-interior-filter-probe.json`
retain the first measurements and filtered failure-position examples.

The exact-path cover also fails 384 of 773 routes, so raw interior faces alone
cannot explain these support failures. See `ISSUES.md` and `gotchas.md` for the
remaining investigation and acceptance gates.

## Latch layer-view regression and preview reuse (2026-09-07)

`Latch_fat_finger.stl` reproduced SIGSEGV when the editor first opened Layers.
GDB stopped in `PyEval_AcquireThread` during `_native.Rasterizer` construction.
The pybind11 3.0.4 rebuild fixes the stale worker thread state; cold-start
headless, real VTK and exact-latch regressions are in
`tests/test_gui_cold_start.py`. Real-render checks require Xvfb outside the
restricted execution sandbox.

`latch-preview-benchmark.json` compares eight forward/backward slice requests
in fresh processes, on the 25,236-triangle supported assembly at default printer
pitch. Fresh indices took 28.75 ms total; reused indices took 7.41 ms (3.88x for
this measured slicing work). All eight mask hashes and open-row counts agree,
including the final layer at index 616. This excludes image conversion and GUI
rendering and is a single local measurement, not a release performance gate.
Reproduce with:

```sh
.venv/bin/python scripts/preview_benchmark.py inputstl/Latch_fat_finger.stl --output reports/plan2/latch-preview-benchmark.json
```
