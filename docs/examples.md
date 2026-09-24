# Worked examples

Every command here was run against this build. Output paths go under `output/`;
files in `inputstl/` and the reference GOO are immutable.

## Inventory the originals

```sh
.venv/bin/voxelmill inspect inputstl/*.stl --report output/inventory.json
```

Reports hashes, triangle counts, bounds, components, invalid triangles, boundary
edges, nonmanifold regions and self-intersections. Add
`--no-self-intersections` to skip the expensive exact predicate pass.

The two temporal bones are closed. The full skull slab and its four quadrants are
open surfaces with 5,500 to 9,400 boundary edges each, which is what makes them
fail later checks rather than any defect in the analysis.

## Prepare a part

```sh
.venv/bin/voxelmill prepare inputstl/left_temporal_bone_mars5_oriented.stl \
  --printer profiles/mars5-ultra.ptr --resin profiles/sunlu-abs-like-gray.res \
  --rotate auto --center-offset 0 0 --seal-voids \
  --output output/left-supported.stl --report output/left.json --progress
```

Places, supports, unions, then reopens the export and reslices it independently.
Automatic placement over both temporal bones takes about 8 seconds at 6 million
triangles with a peak RSS of 1.15 GiB.

## Read a failing report

A solid sphere with default supports exits `2`:

```json
{
  "raster_connectivity": "pass",  "overlap": "pass",
  "growth_span": "pass",          "enclosed_voids": "pass",
  "transient_traps": "warn",      "closed_surface": "pass",
  "plate_fit": "pass",            "support_routes": "pass",
  "support_slenderness": "pass",  "support_coverage": "pass",
  "support_anchor_load": "pass",  "drainage_bottlenecks": "fail"
}
```

No STL is written. The evidence is, and it names the cause: two bottlenecked
components of 0.0200 and 0.0067 mm³. Those are the crevices between each tapering
support tip and the curved surface it lands on, not a defect in the sphere and
not the raft — selecting `--base-type none`, which builds no raft at all, leaves
the same two components bit-identical (see `gotchas.md`).

Two honest responses. Accept those pockets explicitly:

```sh
.venv/bin/voxelmill prepare part.stl --output output/supported.stl \
  --set repair.min_void_volume_mm3=0.05 --report output/prep.json
```

which exits `0`, writes the STL, and still reports what it set aside:

```json
{"ignored_bottlenecked_components": 2, "ignored_bottlenecked_volume_mm3": 0.0266}
```

Or keep the failure and take the export anyway with `--allow-unresolved`, which
marks the result warned and preserves every diagnostic. What is not available is
a silent pass.

## Slice to GOO

```sh
.venv/bin/voxelmill slice output/supported.stl --output output/part.goo \
  --printer profiles/mars5-ultra.ptr --resin profiles/sunlu-abs-like-gray.res \
  --report output/slice.json
```

The candidate is streamed, then reopened and compared decoded-pixel by
decoded-pixel against a freshly sliced source before the final rename. Confirm
the result independently:

```sh
.venv/bin/voxelmill info output/part.goo --verify --report output/goo.json
```

`--verify` decodes every layer and checks framing and checksums:
`{"layers": 85, "decoded_layers": 85}`.

`--print-time-s` is left at `0`, which records the machine time as unknown.
Supply it only from a measured print.

## The full chain on a real part

`prepare`, `slice` and `verify` run in sequence against two real, defective
CSG-exported parts — `floatvalveR7-nut.stl` (raster assembly path) and
`floatvalveR7-cover.stl` (exact assembly path) — in their original pose, 5 mm
lift, one correction pass, `--allow-unresolved` throughout:

```sh
.venv/bin/voxelmill prepare inputstl/floatvalveR7-nut.stl --max-passes 1 --allow-unresolved \
  --output output/plan2/nut-supported.stl --report output/plan2/nut-prepare.json
.venv/bin/voxelmill slice output/plan2/nut-supported.stl --output output/plan2/nut.goo \
  --allow-unresolved --report output/plan2/nut-slice.json
.venv/bin/voxelmill verify output/plan2/nut.goo --report output/plan2/nut-verify.json
```

The cover runs the same three commands against `floatvalveR7-cover.stl`. This
was the first time either part had produced a `.goo`. All three commands exit
`2` for both parts — see the exit-code table in [cli.md](cli.md): `2` is a
result, not a crash, and the report is written whether or not the geometry
file was. `--allow-unresolved` is what keeps a warned export instead of
withholding it; without it `slice` writes no file and the report says why.

The nut rasterizes to 355 layers and a 7.4 MiB GOO; `slice` took 43.9 s,
standalone `verify` 30.2 s over a 1974 x 1933 analysis window. The cover, on
the exact assembly path, rasterizes to 609 layers and a 30.7 MiB GOO; `slice`
took 198.4 s, `verify` 159.3 s over a 3338 x 3316 window. (These numbers come
from the local reports under `output/plan2/`, which are gitignored, not
committed.)

Both files passed every post-write check `slice` makes: framing, header
settings, per-layer timing and motion, and every decoded LCD pixel against a
freshly rasterized source (`verification.decoded_pixels: pass`,
`layers_compared` equal to the layer count above, for both parts). Neither
reported a settings mismatch against the profile that produced it. Because
that pixel comparison already holds, `slice` records
`verification.layer_topology: equivalent_to_source` instead of re-running the
layer analysis over the decoded frames: the source raster is already analyzed
in the same report, so repeating the analysis over the decoded pixels would
only recompute a provably identical answer.

Standalone `verify` is the check for when there is no source mesh: it works
from the `.goo` file alone, on a grid built from the file's own header,
through a different code path into the same `analyze_layers`. Run on
`nut.goo` and `cover.goo`, it reaches the same verdict `prepare`'s
source-raster analysis reported on the same five checks, for both parts. The
nut, identically at `prepare` and at `verify`:

```json
{
  "raster_connectivity": "pass",  "overlap": "pass",
  "growth_span": "pass",          "enclosed_voids": "fail",
  "transient_traps": "warn"
}
```

The cover matches the same way, differing only in `growth_span`, which is
`fail` in both its `prepare` and its `verify` report. That agreement, from two
independent code paths, is what backs `slice`'s shortcut above.

Neither export actually passes — Tier 0.1's gate is a `.goo` that passes.
Read the nut's `enclosed_voids: fail` as a worked example of how to read a
failing report. The evidence is one component:

```json
{"count": 1, "volume_mm3": 1.62e-05, "examples": [{"component": 635, "volume_mm3": 1.62e-05, "first_layer": 269}]}
```

`1.62e-5 mm3` is exactly one pixel at one layer: `0.018 x 0.018 x 0.05 mm =
1.62e-5 mm3`, this printer's pixel pitch and layer height. It is created by
the new support anchors, not present in the model.
`repair.min_void_volume_mm3` defaults to `0` deliberately — every enclosed
void is counted, however small. Raising it to make this specific check pass
would be choosing a threshold to clear a gate, not fixing anything; if a
threshold is set anyway, the report says what it excluded rather than hiding
it (`negligible_count` and `negligible_volume_mm3` here,
`ignored_bottlenecked_components` and `ignored_bottlenecked_volume_mm3` for
the equivalent drainage case — see
[drainage_bottlenecks: fail](troubleshooting.md)).

The nut's other failures are the same kind of thing: `support_routes` (34 of
229 contacts unroutable) and `drainage_bottlenecks` (6 support crevices,
~0.4 mm3) are the support structure, not the model. The cover's remaining
failures — `growth_span`, `enclosed_voids`, `support_routes` (51 of 773
contacts unroutable) and `drainage_bottlenecks` — are the same category on a
larger, more heavily supported part; `support_coverage` now passes for the
cover.

This run establishes that the chain works end to end on real, defective
geometry, on both the raster and exact assembly paths, and that the file it
writes is byte-verified against its own source. It does not establish that
either part would print: nothing has been printed,
`goo_orientation_unverified` rides on every export from this printer profile
— the LCD mirroring is unresolved, so a mirrored threaded or keyed part is
scrap and looks correct until it is assembled — and support mechanics are
uncalibrated.

## Compare base strategies on that same part

```sh
for kind in skate skeleton grid hex; do
  .venv/bin/voxelmill prepare inputstl/floatvalveR7-nut.stl --max-passes 1 \
    --allow-unresolved --base-type $kind \
    --set support.base_touch_diameter_mm=2.4 --set support.base_thickness_mm=0.8 \
    --set support.base_skate_length_mm=5 \
    --output output/bases/nut-$kind.stl --report output/bases/nut-$kind.json
done
.venv/bin/voxelmill prepare inputstl/floatvalveR7-nut.stl --max-passes 1 \
  --allow-unresolved --base-type plate \
  --output output/bases/nut-plate.stl --report output/bases/nut-plate.json
```

`plate` takes no `base_touch_diameter_mm` or `base_thickness_mm` — it is sized
by `raft_expansion_mm` and `raft_thickness_mm` — and passing them exits `3`
with `invalid_profile` rather than ignoring them.

All five route the same 195 contacts, fail the same 34, and fail the same four
checks. `metrics.supports.base` is the only thing that moves:

| Base | Volume mm³ | Contact area mm² | Open fraction | Components |
| --- | --- | --- | --- | --- |
| `plate` | 965.80 | 969.26 | 0.000 | 1 |
| `skate` | 472.94 | 591.17 | 0.345 | 1 |
| `hex` | 458.92 | 573.65 | 0.300 | 1 |
| `grid` | 452.37 | 565.46 | 0.310 | 1 |
| `skeleton` | 293.90 | 367.38 | 0.552 | 1 |

101 unique feet, 110 routed feet, one connected base in every case. The porous
strategies use 51% to 70% less resin than the slab. `hex` and `grid` land 1.4%
apart because at equal pitch and wall width the two lattices open the same
fraction by construction; pick between them on wall shape, not on resin.

Adding `--set support.base_edge_slope_deg=70` tapers the wall inward from the
plate in whole printed layers. The contact area does not move — 565.46 mm² for
the grid with and without it — while base volume falls to 391.07 mm³ for the
grid and 396.35 mm³ for the honeycomb, another 14%.

Trapped resin moves too: `plate` reports 6 bottlenecked components totalling
0.399 mm³ against `skeleton`, `grid` and `hex` at 4 totalling 0.0266 mm³ and
`skate` at 3 totalling 0.126 mm³. Every one still fails
`drainage_bottlenecks`, so the base changes the size of that problem and not
the verdict.

These are measurements of emitted geometry — footprint, openings, volume,
connectivity. None of them says how hard the base is to remove or whether it
holds the part down; no part has been printed on any of these bases.

## Check a file this tool did not produce

```sh
.venv/bin/voxelmill validate some-other.stl --report output/check.json
```

Reslices and checks without preparing anything.

## See the resolved settings

```sh
.venv/bin/voxelmill profile --printer profiles/mars5-ultra.ptr \
  --resin profiles/sunlu-abs-like-gray.res
```

The quickest way to see what a profile combination and a set of flags actually
resolve to, including the interpolated exposure ramp, without touching a mesh.

## A part that does not fit

```sh
.venv/bin/voxelmill prepare inputstl/skull_slab_q00_mars5_oriented.stl \
  --rotate auto --report output/q00.json
```

Exits `3` with `no_feasible_placement` and `smallest_overflow_mm: 13.72` against
an available 144.2 × 68.6 × 165 mm. The full slab overflows by 51.35 mm. Nothing
is scaled or cut; see [troubleshooting.md](troubleshooting.md) for when a finite
search result should and should not be believed.

## Placement evidence over every original

```sh
.venv/bin/python scripts/sample_placements.py
```

Writes `output/sample-placements.json`: hash, triangle count, status, timing,
peak RSS and the full search record per mesh. Both temporal bones place; the slab
and all four quadrants correctly report `no_feasible_placement`.

## Generate shell completion and a man page

```sh
.venv/bin/voxelmill completion bash | head -3
.venv/bin/voxelmill manpage | head -3
```

Both read the live `build_parser()`, so a command or flag that exists in the
parser appears here without a second script to keep in sync:

```
# voxelmill bash completion, generated by `voxelmill completion bash`.
_voxelmill_complete() {
  local current previous command
```

```
.TH VOXELMILL 1 "2026-09-07" "voxelmill 0.1.0" "voxelmill manual"
.SH NAME
voxelmill \- prepare, support, slice and verify a single part for a masked stereolithography printer
```

The `NAME` line is a fixed program summary, not the parser's own
`description` (that description is the module docstring's first line and
names the file, not the program). `--output FILE` writes either one to a
file instead of stdout, for installing into a shell's completion directory
or a `man1` path; see [cli.md](cli.md#completion-and-manpage) for the exact
commands.

## Independent model attachments and triangle bases

Inspect a bottom connector with dimensions independent of the top tip:

```sh
.venv/bin/voxelmill support-example --model-anchor-shape cone \
  --part-to-part-supports \
  --part-to-part-avoidance 0 --set support.model_anchor_length_mm=1 \
  --set support.model_anchor_diameter_mm=0.4 \
  --set support.model_anchor_penetration_mm=0.2 \
  --output output/anchor-example.stl
```

For whole small model-to-model connectors, select `--small-pillar-mode model`,
set `support.small_pillar_diameter_mm=0.4` and an explicit maximum gap with
`support.small_pillar_max_length_mm`. Upper/lower depths are independent.
The example is illustrative; `prepare` performs the production checks.

The triangle-only nut comparison used:

```sh
.venv/bin/voxelmill prepare inputstl/floatvalveR7-nut.stl \
  --max-passes 1 --allow-unresolved --base-type triangle \
  --set support.base_touch_diameter_mm=3 \
  --set support.base_thickness_mm=0.8 --set support.base_strut_width_mm=1 \
  --output output/plan2/triangle-nut.stl --report output/plan2/triangle-nut.json
```

It emits one connected 542.517 mm³ base with 20.3% measured open area and
retains 195 routed contacts / 34 failures. Reopened-STL raster parity passes;
print validation does not. Adding 1 mm bottom connectors and 0.25 mm small
connector penetration rejects 85 model attachments on this part, so that
configuration is not a route-quality improvement. Both runs and their exact
settings are recorded in `reports/plan2/tier2-architecture.json`.

## Run the tests

```sh
.venv/bin/python -m pytest -q                 # fast suite
VOXELMILL_SAMPLES=1 .venv/bin/python -m pytest -q -m samples   # full-resolution originals
```

The full-resolution suite over the immutable originals is opt-in because it is
slow. The GUI's real-render test needs `xvfb-run` and skips cleanly without it.


## Configurable support illustrations (0.5.4)

Compare dense X bracing with a clearly visible model-to-model gap:

```sh
voxelmill support-example --brace-destination supports --brace-pattern x \
  --brace-branches-per-node 3 --brace-spacing-mm 8 --brace-max-distance-mm 12 \
  --brace-angle-deg 60 --output output/x-bracing.stl
voxelmill support-example --layout part-to-part --part-to-part-supports \
  --part-to-part-avoidance 0 --output output/model-gap.stl
```

See every support kind in one picture, with no settings to find first:

```sh
voxelmill support-example --layout showcase --output output/showcase.stl \
  --report output/showcase.json
```

Six contacts over five stations, one per route: a clear column to the plate,
a low blocker with a free neighbour to branch around, a tall platform to
anchor on, a post stopping just under the bar for a thin model pillar, and a
floating slab with nothing beneath it. The brace network appears too, and the
`categories` block in the report counts what was actually produced. It needs
at least 8 mm of height and refuses anything shorter rather than showing fewer
kinds than it promises. This is
the one layout that does not obey the caller's settings exactly: it forces
the six it needs and lists them under `overrides`. It also analyses at the
production column pitch, because the surface an anchor lands on is read from
that raster.

These examples use the production router but are illustrations, not validated
print jobs. The Support editor exposes the same options in **Bracing**,
**Part-to-part anchors** and **Thin pillars**, with a **Show part-to-part
supports** action for the model gap and the same three layouts in its example
selector.
X pairs require reciprocal vertical shaft spans; obstructed or unreachable
branches are omitted. Model parts never anchor braces.
