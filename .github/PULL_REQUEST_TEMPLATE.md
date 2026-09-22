## What this changes

<!-- One subject per pull request. A fix and a refactor are two. -->

## Why

<!-- The problem, not the patch. If it fixes an issue, link it. -->

## What you verified, and on what

<!--
Be specific. "1,272 passed on Linux; macOS and Windows untested" is useful.
"Tests pass" is not.
-->

- [ ] `.venv/bin/python -m pytest -q`
- [ ] `VOXELMILL_SAMPLES=1 .venv/bin/python -m pytest -q -ra`
- [ ] Editor tests (`-m gui`)
- [ ] Ran the actual application, not only the tests

Platforms actually run on:

- [ ] Linux
- [ ] macOS (say Intel or Apple Silicon)
- [ ] Windows

<!-- Platform coverage here is asymmetric and that is fine. Say plainly
     which ones you ran rather than leaving it to be guessed. -->

## Checklist

- [ ] The failing case is a test in this same change.
- [ ] Behaviour changes are reflected in `docs/`.
- [ ] A new or changed setting has its entry in `src/voxelmill/gui/helptext.py`,
      so its hover text is not just the field name read back.
- [ ] Anything not fitted to measured prints is still labelled **uncalibrated**,
      and no new wording implies a strength, drainage or printability guarantee.
- [ ] Anything non-obvious that cost time is written down in `gotchas.md`.

## Performance

<!--
Only if this touches the pipeline, the native kernels, or a per-voxel or
per-triangle path. Numbers, not adjectives: `scripts/check.py` compares
against the recorded baseline. A port from Python to C++ needs a benchmark
showing the win; see CONTRIBUTING.md.
-->

N/A
