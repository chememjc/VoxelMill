# prepare → slice → verify on the three float-valve parts

Measured 2026-09-07, original pose, 5 mm lift, one pass, `--allow-unresolved`
throughout. This is the first time any of these parts has produced a `.goo`.

## What ran

| part | triangles | assembly path | layers | GOO | prepare | slice | standalone `verify` | verify window |
|---|---:|---|---:|---:|---:|---:|---:|---|
| nut | 78,028 | raster | 355 | 7.4 MiB | 33 s | 43.9 s | 30.2 s | 1974 x 1933 |
| cover | 89,410 | **exact** | 609 | 30.7 MiB | 167 s | 198.4 s | 159.3 s | 3338 x 3316 |
| body | 989,834 | raster | 1,709 | 101.3 MiB | 680.5 s | 750.9 s | 418.7 s | 3215 x 3176 |

All three passed every post-write check `slice` makes: framing, header settings,
per-layer timing and motion, and **every decoded LCD pixel** against a freshly
rasterized source — 355, 609 and 1,709 layers compared respectively. None
reported a settings mismatch against the profile that produced it. The body's
`prepare` peaked at 2,359 MiB in its own process.

## The standalone check independently reaches the same verdict

`slice_stl` records `verification.layer_topology = 'equivalent_to_source'`
rather than re-running `analyze_layers` over the decoded frames, on the argument
that proving every decoded pixel equal to the source raster makes the
source-raster analysis in the same report describe those pixels exactly.

All three parts corroborate that argument from the other side. Running
`chopchop verify` on each `.goo` alone — no source mesh, a different grid, a
different code path into the same analysis — reproduces exactly what
`prepare`'s source-raster analysis reported on the five shared checks:

| part | raster_connectivity | overlap | growth_span | enclosed_voids | transient_traps |
|---|---|---|---|---|---|
| nut | pass | pass | pass | fail | warn |
| cover | pass | pass | **fail** | fail | warn |
| body | pass | pass | pass | fail | warn |

Three parts, two assembly paths, 2,673 layers, and no disagreement. That is
independent evidence for the shortcut, not a restatement of it.

## Why they are still warned, not passing

Tier 0.1's gate is a `.goo` that **passes**, and neither does.

| part | still failing |
|---|---|
| nut | `enclosed_voids` — one 1.62e-5 mm3 component, exactly one pixel at one layer, created by the new short anchors. Plus `support_routes` (34) and `drainage_bottlenecks` (6 support crevices, ~0.4 mm3). |
| cover | `growth_span`, `enclosed_voids`, `support_routes` (51 of 773) and `drainage_bottlenecks`. `support_coverage` now **passes**. |
| body | `enclosed_voids`, `support_routes` (91 of 1,085), `support_coverage` (30 uncovered samples) and `drainage_bottlenecks`. `support_slenderness` and `support_anchor_load` warn — both heuristics, and both newly reached because 994 contacts are now routed where 516 were before. |

Support routing improved sharply this session — the cover went from 389 of 773
routed to 722 with 333 shortened tips, and the body from 516 of 1,085 to 994
with 478 — but the remaining failures
are the attachment question recorded in `routing-summary.md`, and the void and
drainage findings are the support structure rather than the model.

## What this establishes

The chain itself works on real, defective, CSG-exported geometry, on both the
exact and the raster assembly paths, and the file it writes is byte-verified
against its own source. It establishes nothing about whether these parts would
print: no part has been printed, the LCD mirroring is unresolved, and support
mechanics are uncalibrated.
