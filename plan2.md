# plan2.md — tiered implementation plan

Companion to `plan.md` (stage gates) and `nextsteps.md` (scored gap list).
`todo.md` remains the live ledger: items are removed as they are completed and
newly discovered work is appended in place. `gotchas.md` gains a verified lesson
per non-obvious discovery. `nextsteps.md` and the published Artifact are kept in
step.

## Context

`nextsteps.md` scored 77 gap items against Lychee and CHITUBOX but did not
sequence them against real work. Meanwhile four concrete blockers surfaced using
the program on actual parts:

1. Three OpenSCAD-exported STLs (`inputstl/floatvalveR7-{body,cover,nut}.stl`)
   that CHITUBOX slices and prints without complaint are rejected outright.
2. The build plate is an undifferentiated grey box — no way to tell which way
   the printer faces.
3. A model that does not fit does not open **at all**, so there is no way to see
   how much it misses by or to work with it.
4. A `.goo` exported successfully, but its layer images cannot be inspected.

This plan fixes those four first, then works outward in tiers. The governing
priority after the blockers is **slicing quality** — print stability, support
robustness and support configuration — not feature breadth. Export formats other
than `.goo` are deferred to the very end.

## Verified evidence

Measured this session, not assumed.

**The three STLs are byte-perfect.** All three are binary STLs with
`84 + 50n == filesize` exactly, an `OpenSCAD Model` header, no truncation and no
trailing garbage. The reader accepts all three. The defects are geometric:

| | body | cover | nut |
|---|---|---|---|
| triangles | 989,834 | 89,410 | 78,028 |
| zero-area | **4,064** | 0 | 0 |
| nonmanifold edges | **3,719** | 0 | 0 |
| components | **6** | 1 | 1 |
| self-intersections | **94,872** | 0 | **8** |
| boundary edges | 0 | 0 | 0 |

**The blocker is `geometry.mesh_to_manifold` (`geometry.py:392-425`), not the
slicer.** Three absolute gates with no tolerance and no count threshold:
`geometry.py:406-410` (`degenerate_triangles`) kills the body;
`geometry.py:421-424` (`self_intersections`) kills the nut on 8 pairs out of
78,028; `geometry.py:419-420` (`invalid_solid`) is the third. The cover passes.

**The rasterizer is already tolerant enough.** Driving `native/raster.cpp`
directly on the body at 0.1 mm produced `odd_rows = 0` and
`negative_winding_crossings = 0`. The nonzero-winding rule with canonical edge
ordering (`raster.cpp:96-155`) genuinely handles inverted components, overlapping
shells and self-intersections. CHITUBOX succeeds because it only ever needs
closed scanline contours — which voxelmill also already achieves. **The gate in
front of the slicer is stricter than the slicer.**

**A non-fitting model never reaches the viewport.** `services.load_and_place`
calls `geometry.auto_placement` / `placement_for_triangles`, both of which raise
`no_feasible_placement` (`geometry.py:121`, `:376`). The error routes to
`_report_error` (`window.py:393`), `_finish_place` never runs, `self.placed`
stays `None`, and `scene.set_mesh('model', ...)` is never called.

**Four independent reasons the GOO layers are unviewable.** (i) The GUI never
imports `GooReader` — there is no open-`.goo` path at all; the Layers tab only
slices the in-memory union. (ii) `request_layer` returns silently when
`derived.union is None` (`window.py:347`). (iii) The slider does not emit at
value 0, so the tab looks dead until dragged. (iv) `services.slice_layer`
constructs a fresh `_native.Rasterizer` on **every** request (`services.py:139`),
which is minutes per slider move on a large union; `DerivedState.layer_cache`
exists and is never written to.

**`analyze_layers` is already format-agnostic.** It needs only an iterable of
`Layer(index, z_mm, mask)` and a `RasterGrid`. Feeding it `GooReader.decode()`
output is a direct fit — `mask != 0` (`validation.py:195`) means 0/255 frames
work unchanged. This makes the post-generation `.goo` check honest rather than a
parallel implementation.

**Latent bugs found while looking.**
- `repair.aggressiveness = 'none'` is **dead code**, behaviourally identical to
  `'conservative'` — only `pipeline.py:61` branches, and only on `'aggressive'`.
- `mesh_to_manifold` catches only `(ImportError, AttributeError)`
  (`geometry.py:414`), so `mesh.cpp:74`'s `std::invalid_argument` escapes as an
  unwrapped `ValueError`. Today the zero-area gate pre-empts it; once that gate
  becomes a counted finding, this path becomes reachable.
- `degenerate_triangles` reports only `count_in_chunk` (987 of the body's 4,064).
- `choose_voxel_size` never coarsens despite its docstring, and at default
  `max_deviation_mm = 0.05` the derived 0.0577 mm voxel has a half-diagonal of
  exactly 0.05 — **zero headroom** before well-composedness additions count.
- `mask_to_image` (`layerview.py:8-32`) does `np.flipud(mask) * 220` on uint8,
  which **wraps** for a 255-valued frame (255×220 mod 256 = 228). It looks
  plausible by accident and will misrender any decoded GOO layer.
- `gui/services.py:54-67` and `:84-90` duplicate `pipeline._solid_from` and
  `pipeline._assemble` verbatim. Any ingestion fix applied only to the pipeline
  leaves the GUI rejecting exactly the parts the CLI now accepts.
- The two reference GOO files disagree on mirroring: CHITUBOX writes
  `mirror_x=1, mirror_y=0`; ELEGOO SatelLite writes `mirror_x=0, mirror_y=1`;
  voxelmill writes `0,0`. A direct layer comparison between the two references
  was **inconclusive** because each slicer posed the model differently. This
  needs the existing STL-vs-GOO method (`scripts/goo_orientation_check.py`), not
  a reference-to-reference diff. Until resolved, exports may print mirrored —
  which for a threaded functional part is a silently ruined print.

**Baseline: 165 tests passing.** Every tier must leave that number higher and
green.

## Contract changes made deliberately

Two documented policies change. Both are explicit decisions and both are
recorded loudly rather than quietly.

1. **The build volume becomes a clipping window.** Today
   `goo._check_export_bounds` (`goo.py:486-493`) raises `goo_envelope` —
   "refusing to clip pixels" — and `allow_unresolved` deliberately does not
   bypass it; `docs/troubleshooting.md` states "Nothing is ever scaled or cut to
   make a part fit." That becomes: geometry outside the build volume cannot be
   printed because the printer cannot reach it, so it is clipped, shown in red
   in the viewport, and the report records exactly how much was clipped.
   **Scaling is still never automatic.**

2. **A mesh that is not representable as a solid no longer aborts.** The exact
   Manifold path is tried first; on failure the run falls back to a
   raster-domain union and says so prominently. The correctness bar does not
   move — the independent re-slice still has to pass, and it gains a stronger
   check than it has today.

`docs/troubleshooting.md`, `docs/geometry.md`, `docs/algorithms.md` and
`plan.md` are updated in the same commits as the code.

---

# Tier 0 — the four reported blockers

## 0.1 Robust ingestion: the raster-domain union fallback

The largest single piece, and the core of this tier.

### The decisive observation

**STL is a triangle-soup format with no manifold requirement.** The union
*solid* exists only because `pipeline._assemble` (`pipeline.py:68-78`) uses
`Manifold.batch_boolean` as the union operator. Move the union into the raster
domain and the exported file can be written as concatenated soup, with the whole
downstream chain unchanged. `write_stl` (`mesh.py:197`) validates shape and
finiteness only — it has never required manifoldness.

### Design: group, rasterize separately, OR

A new module `src/voxelmill/assembly.py` holding `PartGroup`, `Assembly`,
`UnionLayerStream` and `assemble()`. Not `repair.py` — that module's contract is
the explicitly-requested `aggressiveness == 'aggressive'` path, and the fallback
must fire for a user on `'conservative'`. It also repairs nothing and moves
nothing; filing it under repair would invite the report to claim a repair
happened.

`UnionLayerStream` is a deliberate structural clone of `MeshLayerStream`
(`raster.py:41-97`) — same attribute names, same `Layer` yield, same
`diagnostics()` — so `analyze_layers(stream, stream.grid, settings, ...)` works
with zero changes.

**Group by orientation trustworthiness, not by part identity:**

- `supports` and `raft` come from `geometry.cylinder_between` and
  `geometry.raft_from_feet` — every one closed and positively oriented.
  Concatenating them is provably safe: consistently outward shells accumulate
  only positive winding, and a sum of positives is never zero. Overlapping
  pillars read `+2`, braces `+3`; all nonzero, all filled.
- `model` is the untrusted soup and gets its own group.

**Why not one concatenated soup in a single pass.** `raster.cpp:124` assigns
`dir = ascending ? -1 : +1`, so a correct shell reads `+1` inside and a reversed
one reads `-1`. Where they overlap the sum is `0`, and `raster.cpp:147` treats
`winding == 0` as outside — the pixel goes empty. This is not hypothetical:
`support.penetration_mm` defaults to 0.15, so **every contact tip is
deliberately buried inside the model**. If the shell it enters is reversed, the
tip punches a hole instead of joining. ORing independent occupancy masks has no
sign and cannot cancel.

**Correction to an earlier assumption.** `negative_winding_crossings == 0` does
**not** prove cancellation is absent. A reversed shell entered while already
inside positive material takes the winding from 1 to 0 and never goes negative.
The measured zero over 40 slices of an 80 mm part is useful evidence, not a
proof. The grouping and the parity check below carry that load instead; the
metric is reported as a warning precondition, not relied on as a guard.

### The parity check — stronger evidence than today

Today `_reslice` (`pipeline.py:80-102`) reopens the written STL and *analyzes*
it. It never compares it to anything, so it supports "the file passes the layer
checks", not "the file is the thing we validated."

The fallback supports the stronger claim cheaply, because the union is defined
by a re-runnable computation:

1. The union mask for layer *k* is `OR(model_k, supports_k, raft_k)`, computed
   in memory from the placed part groups.
2. The exported STL is `concat(model, supports, raft)` — plain soup.
3. `_reslice` reopens those bytes and rasters them as **one** soup.
4. Compare (3) against a **freshly recomputed** (1), layer by layer.

Not circular: (3) reads bytes off disk through `open_stl` and knows nothing
about the grouping; (1) is recomputed from the in-memory groups. Only the
sampling lattice is shared, which is what a comparison requires.

**And this comparison is the cancellation detector.** Single-soup rasterization
is exactly where model↔support cancellation would occur. If it occurs, (3) has a
hole (1) does not, and parity fails on that layer with a pixel count and a
position.

Lattice alignment matters: a float32 STL round trip can nudge the reopened
bounds by an ULP, shifting `RasterGrid.for_bounds`'s crop by a pixel and turning
a correct comparison into a total mismatch. Build the reference stream with the
**reopened** grid, so `UnionLayerStream.__init__` takes `grid=` and
`layer_count=` overrides.

Add `validation.checks['union_raster_parity']` **only on the raster path** —
`ValidationReport.passed` (`contracts.py:108`) rejects `'not_run'`, so inserting
it on the exact path would break every currently-passing export.

`closed_surface` stays fatal. The fallback tolerates zero-area triangles,
self-intersections, nonmanifold edges and multiple components. It must **not**
tolerate `odd_rows > 0` — an unclosed contour means the mask has holes and is
not evidence. The body measures `odd_rows = 0`; a genuinely open mesh correctly
still fails.

### Why not a voxel union — arithmetic, not taste

Union bounds are roughly 57 × 57 × 86 mm.

| pitch | grid | dense bytes | verdict |
|---|---|---|---|
| printer-matched (0.018 / 0.05) | 3167 × 3167 × 1720 | 17.2 GB | over the 12.0 GB budget fraction and over `voxel.cpp:65`'s 24 GiB cap |
| `repair` default 0.0577 | 988 × 988 × 1491 | 1.46 GB | affordable, but see below |

The only lossless pitch is the one that cannot be afforded. Worse,
`extract_surface` (`voxel.cpp:122-158`) emits one quad per exposed voxel face
through an `unordered_map` — at 0.0577 mm that is ~12M triangles, a ~600 MB STL,
a CGAL deviation tree over 990k against 12M triangles, and a full-resolution
re-slice of 12M triangles. And it destroys the geometry that matters most:
`contact_diameter_mm` is 0.4, so a contact disc becomes 7 voxels across and the
0.15 mm penetration under 3. The supports are exact today; voxelizing trades
certainty for nothing.

The raster path's deviation is exactly zero — its only quantisation is the
printer's own lattice, which the output gets regardless.

**Escalation, if ever needed:** voxel-repair the *model only*, leaving supports
exact, then union through the normal Manifold path. Confines all approximation
to geometry that was already broken. Do not implement it yet — do not add dead
config keys; `config.py:159-160` already sets the precedent of hard-rejecting
unimplemented options.

### `seal_voids` genuinely breaks on this path — report it honestly

`fill_enclosed_cavities` (`geometry.py:473-493`) needs `solid.decompose()`.
There is no solid. A raster equivalent is possible but inherently two-pass and,
critically, **not expressible in the soup STL** — so the exported file would no
longer rasterize to the validated union and parity would fail by design.

Do not fake it:

```python
report['stages']['cavity_fill'] = {
    'status': 'not_run',
    'reason': 'the raster union path has no solid to decompose; seal_voids '
              'requires the exact path or repair.aggressiveness = "aggressive"',
    'consequence': 'enclosed voids are reported by the layer analysis rather than filled',
}
```

The existing `enclosed_voids` check (`validation.py:266-268`) then fails
validation for a part with sealed cavities — the honest outcome. Documented in
`docs/troubleshooting.md` as a real functional difference between the paths.

### Gate changes in `geometry.py`

`mesh_to_manifold` keeps its strictness and its error codes; what changes is
that it accumulates **all** findings before raising, so a user learns all three
answers rather than only the first that fired, and `pipeline._solid_from`
(`pipeline.py:59-65`) stops treating those raises as fatal.

- `geometry.py:406-410` — count zero-area triangles across all chunks; report
  the true total, not `count_in_chunk`.
- `geometry.py:411-416` — catch the escaping `ValueError` explicitly as
  `VoxelMillError('weld_rejected', ...)`. Falling into the `np.unique` branch instead
  would be worse: it would build a solid out of degenerate faces.
- `geometry.py:421-424` — **short-circuit the CGAL scan** when an earlier
  finding already disqualified the exact path, recording
  `self_intersections: 'not_run'` with a reason. On the body that scan is 95k
  pairs over 990k triangles; skipping it is the difference between a fast
  fallback and a slow one.

`VoxelMillError('exact_union_unavailable', ...)` is raised only when
`assembly.union == 'exact'`.

### Give `repair.aggressiveness = 'none'` a real meaning

**`'none'` means the model is used exactly as authored**: `mesh_to_manifold` is
not attempted at all and the raster union is used directly. That removes the
dead branch, gives `--repair none` and the GUI combo (`window.py:144`) real
behaviour, and lets a user skip an expensive CGAL scan on a part they already
know is soup. `assembly.union` then stays a pure strictness switch.

### Config

```python
'assembly': {
    'union': 'auto',                 # 'auto' | 'exact'
    'require_raster_parity': True,
    'max_parity_examples': 16,
},
```

A new top-level section is not free: `config.py:106-112` enforces exact key
sets, `config.py:109` hardcodes the section tuple, `config.py:187` allowlists
what a `.ptr` may carry, and `cli.py:20` `SECTIONS` gates `--set`. All four need
`'assembly'`. `schema_version` stays 1 — the change is purely additive and every
existing profile still resolves. `tests/test_config.py:14` asserts
`resolve_settings(mars5, sunlu) == DEFAULTS` exactly and must be updated
deliberately.

### Report surface — three places, because one JSON field is easy to miss

`report['stages']['assembly']` carrying `union` (`exact` / `raster`),
`exact_union_attempted`, `exact_union_blocked_by` with every finding and its
count, per-group triangle counts and orientation status, `max_displacement_mm:
0.0`, and explicit `establishes` / `does_not_establish` strings including
"the exported STL is not a valid closed solid; it is not".

Plus a `severity='warning'` `Diagnostic('exact_union_unavailable', ...)` so it
survives into `validation.to_dict()` and the `.chop` archive — **warning, not
error**, because `contracts.py:109` fails the report on any error diagnostic and
the point is a usable export.

Plus a one-line stderr banner from `cmd_prepare`. The JSON is the product, but a
user watching a terminal should not have to grep for this. Exit code stays 0.

### Known risk to measure before writing code

**Interior downward faces will likely break support planning on the fallback
path.** On the exact path `model_triangles = manifold_triangles(solid)` is the
boolean-resolved boundary, so internal faces are gone. On the fallback path the
model group is raw soup, so `downward_contacts` (`supports.py:216`) samples the
downward faces of all six of the body's components, including internal ones.
Those flow into `select_contacts` and `contact_coverage`. Two consequences: a
contact on an internal face fails `field.reachable` (`supports.py:427`) and
increments `contacts_failed`, which `apply_support_validation` turns into
`support_routes: 'fail'` — a hard export blocker; and an unreachable internal
face stays permanently uncovered, so `contact_coverage` returns
`coverage: 'fail'` — also a hard blocker.

**Measure this first**: run `build_column_field` + `downward_contacts` +
`select_contacts` on the placed body soup and count `contacts_failed`,
`contacts_in_sealed_cavities` and `uncovered_fraction`. If they are large, the
interior-face filter is a prerequisite, not an afterthought, and belongs in the
same change. Proposed filter: drop any downward sample whose column has material
both one layer below and one layer above, reporting
`interior_face_samples_dropped`. It is a no-op on the exact path.

Also re-run `negative_winding_crossings` over **every** layer of the placed body
at the real layer height, rather than relying on a 40-slice sample.

### Also in this change

- **`gui/services.py:54-67` and `:84-90` must become thin wrappers** over the
  same `assembly.assemble`, not a third copy, or the GUI keeps rejecting the
  parts the CLI now accepts.
- **`write_stl` accepts a sequence of arrays** (~5 lines) so the soup carrier
  does not materialize a single concatenated array — 37 MB for the body, but
  ~216 MB for a 6M-triangle model.
- **`pipeline.prepare` stops calling `union.volume()`** on the raster path. A
  soup has no signed volume that is physical resin volume — already a
  `gotchas.md` entry. Report `validation.metrics['raster_volume_mm3']`, which
  `analyze_layers` already computes, and set `union_volume_mm3: null` with a
  note.

### Tests

Synthetic fixtures, one per failure class, no 50 MB files — reusing `placed()` /
`assemble()` from `tests/test_supports.py:11-19`: `with_zero_area()` (a
collapsed triangle), `self_intersecting()` (two crossing positive boxes),
`touching_components()` (face-sharing cubes: nonmanifold edges, no boundary
edges, n components), `inverted_overlap()` (a correct box overlapped by a
reversed one).

The most important test: **the fallback reproduces the exact path exactly** —
take the clean sphere fixture, build the exact union, and assert
`UnionLayerStream` masks are bit-identical to `MeshLayerStream` over
`manifold_triangles(exact_union)` at a coarse pitch. If that passes, the
fallback is not a different answer, it is the same answer computed differently.

Then: single-soup cancels where grouped-OR does not; a penetrating support into
a reversed shell is caught by parity with a position example; degenerate
triangles do not change the union raster (backing the claim that the gate
protected Manifold, not the rasterizer); `prepare` falls back on a
self-intersecting model and still writes a passing export; a clean model still
takes the exact path and `union_raster_parity` is **absent** from `checks`;
`union='exact'` refuses to fall back; `repair='none'` goes straight to raster
with no CGAL scan; `seal_voids` reports `not_run` with its reason.

**Done when:** all three float valve STLs run `prepare` → `slice` → `verify` to
a `.goo` that passes, and the report says which assembly path each used and why.

## 0.2 Build plate orientation

`Scene.show_build_volume` (`viewport.py:58-72`) is one grey `vtkOutlineFilter`
actor. Replace with an explicit `vtkPolyData` — 8 points, 12 line cells — with a
`vtkUnsignedCharArray` on `GetCellData()` and
`mapper.SetScalarModeToUseCellData()`. Eleven edges red, the front bottom edge
green, with a heavier line width so it reads at a glance.

**Front is the −Y face.** There is no camera setup anywhere in the codebase
(`grep` for `GetActiveCamera` returns nothing), so VTK defaults apply: camera on
+Z looking down −Z with +Y up — an initial top-down plan view with −Y at the
bottom of the screen. The green edge is `(-w/2, -d/2, 0) → (+w/2, -d/2, 0)`.

**Test:** headless — `Scene` needs no display. Assert the cell-scalar array has
12 entries, exactly one green, and that the green cell's endpoints are the −Y
bottom pair. No existing test asserts plate colour.

## 0.3 Navigation cube

A FreeCAD-style navigation cube in the **top-right** of the 3D view: a small
interactive cube showing the current orientation, whose faces, edges and corners
are clickable to snap the camera to that view. UVtools has the same affordance.

VTK provides most of this — `vtkAnnotatedCubeActor` inside a
`vtkOrientationMarkerWidget` gives a labelled, correctly-oriented cube in a
corner viewport. The default widget is display-only, so face picking needs a
`vtkPropPicker` on the marker's own renderer mapped to a camera azimuth /
elevation, plus a smooth interpolated transition rather than a jump.

Label the faces in printer terms, not axis letters — **Front / Back / Left /
Right / Top / Bottom** — with Front on −Y so it agrees with the green plate edge
from 0.2. That consistency is the point: two independent cues telling the user
the same thing.

This also supplies the camera control the codebase currently lacks entirely, so
build a small camera helper (`set_view(name)`, `home()`, `fit()`) alongside it
and give it keyboard shortcuts. `reset_camera()` (`viewport.py:174`) becomes one
caller of that helper.

**Test:** headless for the state machine — assert `set_view('front')` produces
the expected camera position, focal point and view-up, and that every cube face
maps to a distinct view. The render-window part follows the `xvfb-run` +
`vtkWindowToImageFilter` rule below.

## 0.4 Always open the model; clip to the build volume; highlight in red

- **Load regardless of fit.** Add a non-raising placement variant so
  `services.load_and_place` gets a `Placement` plus `fits: bool` and an overflow
  measurement instead of an exception. `geometry.envelope_fits`
  (`geometry.py:72-81`) already computes the predicate. The CLI `prepare` path
  keeps raising unless clipping is requested, so batch behaviour does not change
  by accident.
- **Highlight out-of-bounds in red.** `polydata_from_triangles`
  (`viewport.py:23-37`) sets no scalar arrays at all today. Add per-cell
  scalars: a triangle is out of bounds if any vertex lies outside the usable
  envelope. Geometry is one 3-point cell per triangle with unshared vertices, so
  cell scalars need no vertex reindexing. **Keep `actor.role == 'model'`** —
  `handle_pick` (`window.py:526`) only accepts support contacts on that role, so
  a second actor would silently break support placement.
- **Clip at slice time — smaller than it looks.** `RasterGrid.for_bounds`
  (`raster.py:33-39`) **already clamps** the crop to the LCD with
  `max(0, min(w-1, ...))`, so `_full_frame`'s `goo_frame` guard is already
  satisfied. And the rasterizer already clips correctly, verified directly:
  `clamped_row` (`raster.cpp:87`) clamps an edge's row *range* to
  `[0, height]`, so an edge entirely outside emits no crossings and one spanning
  the grid emits exactly the rows it truly crosses; `clamped_col`
  (`raster.cpp:136`) clamps a crossing's x to `[0, width]`, so a span entering
  from off-panel starts filling at column 0 and one entirely off-panel collapses
  to zero width. The real work is therefore only:
  1. **Remove the gate** — `goo._check_export_bounds` (`goo.py:486-493`) raises
     `goo_envelope` before any of that runs. It becomes clip-and-record.
  2. **Clamp Z** — `layer_count` derives from `bounds[1,2]` (`goo.py:495`) and
     must be clamped to `build_mm[2]`.
  3. **A regression test** anyway: a mesh straddling the boundary must produce
     in-frame pixels identical to the same mesh translated fully inside.

  The report gains clipped-triangle and clipped-pixel counts plus the overflow
  in mm per axis, and a `clipped_geometry` diagnostic at severity `error` so it
  can never pass unnoticed.

**Tests:** a mesh deliberately larger than a small synthetic plate — assert it
loads, the out-of-bounds cell-scalar count is exactly right, export succeeds,
and the report records the clip. Reuse `tests/test_goo.py:23-31`'s
`small_settings()` so it runs in milliseconds.

## 0.5 View layer images, including from an exported `.goo`

- **`services.open_goo(path)` / `goo_layer(path, index)`** returning exactly the
  payload `LayerView.show_layer` expects (`mask`, `grid`, `index`, `z_mm`,
  `filled_pixels`, `open_rows`), with the grid built from the GOO header:
  `RasterGrid(resolution_x, resolution_y, -display_width/2, -display_height/2,
  display_width/resolution_x, display_height/resolution_y)`.
- **Fix the `mask_to_image` uint8 wrap** — normalise with `(mask != 0)` before
  scaling.
- **Downsample before building the QImage.** A full frame is 8520×4320 =
  36.8 Mpx and `mask_to_image` allocates H×W×3 plus a `.copy()` — roughly 110 MB
  per layer. Decimate to the label size first, or use `Format_Grayscale8`.
- **An "Open GOO…" action** and a Layers-tab source selector: current union, or
  an opened file. Decode on a background job following the documented contract
  (`services` function, `jobs.submit`, `_finish_*`).
- **Fix the dead ends**: `request_layer` returning silently when the union is
  `None` should say why in the status bar; request layer 0 when the tab is first
  shown so it is not blank until dragged.
- **Use `DerivedState.layer_cache`** (declared, cleared, never written) as a
  decoded-image LRU. Note `raster.cpp:157` enforces nondecreasing Z *within one
  `Rasterizer`*, so a rasterizer cannot be cached and scrubbed backwards — the
  cache holds decoded images, not rasterizers.
- The GOO previews are free: `GooReader` already decodes `small_preview`
  (116×116) and `big_preview` (290×290) into RGB arrays. Show one as a thumbnail.

**Tests:** headless. Write a tiny GOO with `small_settings()`, open it through
the service, assert payload shape and layer count, and assert `mask_to_image`
produces a non-degenerate image for a 255-valued mask — a direct regression test
for the wrap bug.

## 0.6 Post-generation `.goo` verification

A standalone final check runnable on any generated file — islands, resin traps,
enclosed voids — not just framing and checksums.

`goo-info --verify` today decodes every layer and **discards the result**
(`cli.py:203-205`). `analyze_layers` is format-agnostic and takes exactly the
frames that loop already holds.

- New `voxelmill verify <file.goo>` subcommand. **It needs `parents=[common]`** —
  `goo-info` is the only subcommand without it, so it has no
  `--printer`/`--resin`/`--set` and no settings, while `analyze_layers` needs
  `min_overlap_pixels`, `max_span_mm`, `layer_height_mm` and
  `min_void_volume_mm3`. Where a setting can be derived from the GOO header,
  prefer the header and record that it was derived.
- Streams `Layer(index, z_mm, decoded)` into `analyze_layers` with a full-LCD
  uncropped grid. Unmirror before analysis so diagnostic `position_mm` values
  are meaningful — topology is mirror-invariant, coordinates are not.
- Exit `0` on pass, `2` on fail, matching `cmd_validate` and `cmd_slice`.
- Attach the same analysis to `slice_stl`'s existing pass three, which already
  decodes every layer (`goo.py:673-693`) — free, since the frame is in hand.
  Results go under the existing `verification` sub-dict.

**Tests:** build a GOO containing a deliberate floating island and one enclosed
void using `small_settings()`; assert `verify` fails with the right diagnostic
codes and exit code, and that a clean file passes.

---

# Tier 1 — low-effort items (difficulty < 25)

Everything in `nextsteps.md` scored below 25, minus non-`.goo` export work.
Each is small enough to land with its test in one sitting.

**Profiles and config** — `A3` profile discovery and the system/user split (12),
`A1` printer profile manager (22), `A2` resin profile manager with
`resin bind --from` (24), `A8` named support presets (14), `A6` `profile diff`
and provenance (15), `I2` resin density and cost fields (8), `B3` Elegoo printer
database (16).

`A8` is promoted in practice because Tier 2's support work needs somewhere to
put a known-good preset.

Implemented 2026-09-07: portable support-only light/medium/heavy presets and
strict JSON load/save (`D7`, support portion of `A8`), shared by CLI and GUI.
Named process presets and embedding presets inside profiles remain open. See
`docs/support-presets.md`. `scripts/check.py` now runs the full test suite and
`F10` baseline comparison as the release/performance entry point.

**Slicing quality, cheap** — `B6` resin volume, weight and cost (18); `E5`
first-layer / elephant-foot compensation (22); `D5` re-run island detection
after every edit with a persistent badge (18), the best
importance-to-difficulty ratio in the whole list; `D7` support presets as
portable data (12).

**Geometry, cheap** — `C6` booleans including intersect-against-a-bounding-box
STL (18); `C7` planar trimming (20); `C12` measure, mirror, per-axis scale with
scaling guardrails (16). `C6` and `C7` share the planar-capping implementation.

**Performance** — `F10` benchmark harness and regression gates (16), done
**first** in this group so the rest is measurable; `F1` multithread across
layers (24).

**GUI polish** — `G4` tooltips with config keys, modified markers, revert arrows
(22); `G2` fuzzy mode-aware settings search (20); `G9` first-run wizard (18);
`G10` autosave and crash recovery (22); `G7` notification framework with
per-category suppression built in from the start (20); `G6` named undo/redo
history (22); `G8` theme and shortcut editor (22); `G11` dockable panels (18).

**Automation** — `H3` versioned report schema as JSON Schema (14); `H1` batch
mode (20); `H2` post-slice hooks (18); `H6` shell completion and man page (8);
`I4` report as HTML (14).

Deferred by the "`.goo` only until the end" instruction: `B7` format conversion.

---

# Tier 2 — slicing quality: stability, support robustness, configuration

The stated priority. This is where the program gets better at *printing*.

## 2.1 Support and base architecture — fully parametric

Implementation checkpoint (2026-09-08): independent tip-base diameter, branch
angle, short-pillar class, the original brace spacing/start/diameter/reach
experiment, plate/none/pad strategies, and the partial `chitubox-mars5` preset
were implemented and tested. The start-height experiment is historical;
0.5.3 replaces it with downward shoulder-origin branches and a complete
diagonal length limit.
`allow_part_to_part` can forbid model anchors; `part_to_part_avoidance` spans
equal length comparison (0) through mandatory preference for available plate
routes (1). Brace/model clearance is checked on the existing analysis grid.
Dedicated printer, resin/process and support editors save portable files; the
support editor and `support-example` CLI share a rendered/exportable attachment
fixture built by the router. This is geometric feedback, not print acceptance.

Base strategies added 2026-09-08: `skate` emits an explicit capsule whose
total length includes both rounded ends, `skeleton` connects feet along a
deterministic Euclidean minimum spanning tree, and `grid` adds a rotated square
lattice clipped to the foot hull with a connected perimeter rim. All three are
extruded planar footprints, so every opening is a vertical through hole rather
than a roofed cavity. Each reports measured contact area, convex-envelope area,
open-area fraction, connected components and resin volume, taken from the
polygons actually emitted rather than from nominal dimensions. `plate`
geometry is pinned bit-identical to the pre-existing hull raft.

Added 2026-09-08, second pass: `hex` is the honeycomb on the same
centre-to-centre pitch as `grid`, and `base_edge_slope_deg` tapers any added
base inward from the plate in whole printed layers, leaving the plate contact
area untouched. That completes CHITUBOX's four base shapes and the greyed-out
raft slope. At equal pitch and wall width the honeycomb and the square grid
open the same fraction by construction, so `hex` is a wall-shape choice, not a
resin saving.

Implementation completed 2026-09-08: `triangle` connects foot pads through a
Delaunay triangulation and perimeter rim, with a tree fallback for degenerate
layouts. Ordinary model anchors now have independent cone/cylinder shape,
endpoint diameter, length and penetration. Whole model-to-model small pillars
have a separate mode, independent depths, and conical or cylindrical buried
ends. All controls reach CLI, editor, portable presets and the production
router. New connector dimensions are checked against available gap and sampled
material; a refusal retains the failed-route gate.

The CHITUBOX table is audited in `docs/support-presets.md`. Its 70° middle
angle is from the vertical top, so the preset now uses 20° from horizontal.
Small-pillar dimensions are recorded in whole-model mode but selection remains
disabled until its missing maximum length is supplied. Exact reference
equivalence still needs spacing, skate elongation and bottom transition
geometry; these are explicit preset notes, not guessed measurements. Physical
adhesion/removal acceptance remains deferred. Current verification is in `todo.md`.

### The known-good CHITUBOX configuration

| Segment | Parameter | Value |
|---|---|---|
| Top | Touch Shape | None |
| Top | Contact Depth | 0.30 mm |
| Top | Connection Shape | Cone |
| Top | Tip Upper Diameter | 0.30 mm |
| Top | Tip Down Diameter | 0.80 mm |
| Top | Connection Length | 2.00 mm |
| Middle | Shape | Cylinder |
| Middle | Diameter | 0.80 mm |
| Middle | Angle | 70.00° |
| Middle | Small Pillar Shape | Cone |
| Middle | Small Pillar Diameter | 0.40 mm |
| Middle | Small Pillar Upper Depth | 0.25 mm |
| Middle | Small Pillar Lower Depth | 0.25 mm |
| Middle | Max Cross Structure Spacing | 30.00 mm |
| Middle | Cross Start Height | 3.00 mm |
| Bottom | Platform Touch Shape | Skate |
| Bottom | Touch Diameter | 10.00 mm |
| Bottom | Thickness | 0.80 mm |
| Bottom | Model Contact Shape | None |
| Bottom | Contact Diameter | 0.40 mm |
| Bottom | Contact Depth | 0.20 mm |
| Bottom | Contact Point | 1 |
| **Raft** | **Raft Shape** | **None** |

**The Raft tab reads `Shape: None`, and every other raft field is greyed out.**
The known-good print therefore uses **no raft at all** — supports land directly
on the plate with a 10 mm "skate" foot, 0.80 mm thick. The greyed values are
CHITUBOX's defaults for when a raft *is* enabled and are recorded here only as
a starting point, explicitly uncalibrated: Area Ratio 110 %, Thickness 1.00 mm,
Height 1.80 mm, Slope 30°, Grid Side Length 2.00 mm, Grid Width 2.00 mm.

This originally exposed the single convex-hull raft restriction. The
implementation now supports individual feet without a slab; the preset uses
circular pads until the reference skate's elongation is measured.

### Design direction: porous by default, everything configurable

A solid convex slab is the worst option on both axes that matter — it wastes
resin and it is hard to remove. Prefer **porous / skeletal** bases. The base
becomes a strategy with its own parameters, matching CHITUBOX's degree of
configurability and going past it where the geometry allows:

- **`none`** — feet only, each with its own plate-touch shape. *This is the
  known-good default and must exist first.*
- **`skate`** — the elongated foot pad in the reference configuration
  (diameter and thickness independent).
- **`grid` / `hex`** — a porous lattice spanning the foot hull: far less resin,
  far easier removal, better drainage, and it does not trap a suction film the
  way a solid slab does. Parameters: cell side, strut width, thickness, height,
  edge slope.
- **`line` / `skeleton`** — struts connecting feet along a minimum spanning tree
  rather than filling a hull.
- **`plate`** — the current solid hull, retained for adhesion-critical parts,
  with area ratio, thickness, height and slope exposed.

Every dimension is a setting; nothing is derived from another value unless the
user asks for it (`0` meaning "derive"), which is the existing convention for
`max_contact_gap_mm`. Each base type reports its own contact area, resin volume
and estimated removal difficulty so the trade-off is quantified rather than felt.

### Structural work in the support model

Historical note (2026-09-20): the early design below describes the former
bottom-up cross-brace implementation. The shipped contract now uses downward
45° branches from the shoulder below each tip taper. `brace_spacing_mm` is
vertical shoulder spacing (15 mm default), `brace_max_length_mm` is the
complete diagonal limit (30 mm default), and `brace_max_distance_mm` remains
the separate neighbor reach. `brace_start_height_mm` no longer exists. The
0.5.4 update adds selectable support/base destinations, node density,
angle, single/alternating/X patterns, minimum origin height, and fan rotation;
see [local verification](reports/support-options-local.md).

At the start of this plan, `route_contacts` built exactly two segments — one
uniform cylinder and one cone whose taper length *is* `tip_length_mm` — with one
global `pillar_diameter_mm`, a hard-coded 45° branch limit (`supports.py:458`),
and bracing whose spacing and start height were the same derived number
(`max_slenderness * 2 * pillar_r`). Those restrictions are now removed.

In order:

1. **Three independent segments** — top (contact/tip), middle (pillar), bottom
   (plate touch or model anchor) — each with its own shape and dimensions.
2. **Separate contact depth from tip length**, so the CHITUBOX pair maps
   directly rather than loosely onto `contact_diameter_mm` + `penetration_mm`.
3. **Pillar angle as a setting** (the reference's 70° from vertical maps to
   20° from horizontal here), replacing the hard-coded 45°.
4. **Small-pillar class** — a second, thinner pillar for short or light contacts.
5. **Independent cross-brace spacing and start height** (30 mm / 3 mm) instead
   of one derived number serving both. **Superseded:** this historical design
   was replaced by downward shoulder-origin branches in the 0.5.3 alpha; see
   the note above and `docs/algorithms.md`.
6. **Plate touch shapes with their own diameter and thickness**, including the
   feet-only mode above.

Config additions need care: `config.py`'s validator is whitelist-strict in both
directions and the catch-all loop (`config.py:144-150`) treats every support
value as a positive finite number, so **shape enums need an explicit branch**
like `repair.aggressiveness` at `:164-165`, and any value allowed to be zero
needs adding to the exemption tuple at `:149-150`.

**Tests implemented:** dimensional regressions cover tip and bottom endpoint
radii, penetration depth, brace interval, plate footprints, connected base
geometry, short-model selection and independent reopened exports. The
CHITUBOX preset is explicitly partial: table values and coordinate conversions
are asserted, and missing reference dimensions travel with the portable file.
Here no raft plus per-foot pads is `base_type=pad`; `none` means bare pillars.

## 2.2 Print stability and support robustness

- `C10` orientation search: expose ranked scored candidates with per-term scores
  (34) — no competitor does this — and calibrate the four weights recorded in
  `todo.md` as guesses. Ranked reports, CLI selection and editor controls are
  implemented (2026-09-08); physical weight fitting and real routed support
  volume per candidate remain open.
- `C5` suction-cup and peel-vacuum detection (44), complementing the existing
  trapped-void analysis with the large-flat-downward-face failure mode.
  Surface-region advisory implemented 2026-09-08 with full-resolution area,
  physical layer spans and configured lift-speed scoring. This is an
  uncalibrated heuristic, not a measured vacuum or force prediction.
- `D3` full manual support editor (44) — per-contact parameters, batch
  edit, tip shape and break-point ball implemented 2026-09-08; `D2` paint-on
  block/enforce implemented 2026-09-09 (manual only). `D1` branch/tree remains.

- `D1` branch/tree supports (52) — cluster nearby vertical plate supports
  onto one trunk (`support.tree_supports`, default off; `--tree-supports`).
  Implemented 2026-09-09; reviewed to enforce branch slope, capsule thickness
  clearance and a graph matching the emitted tree. Contour/face/boundary types
  remain open.
- `D6` support mechanics calibration (55) is **deferred to the hardware
  campaign**; it needs printed test artifacts, not code.

## 2.3 Resolve the mirroring question

Run `scripts/goo_orientation_check.py` against **both** reference files
(CHITUBOX `mirror_x=1`, ELEGOO SatelLite `mirror_y=1`) using their own source
STL, which fixes the pose and makes the comparison valid. Record the conclusion
in `gotchas.md` and set `image_mirror_x/y` in `profiles/mars5-ultra.ptr`
accordingly — or, if still ambiguous, keep the current values and make the
uncertainty a loud report entry rather than a profile comment nobody reads. A
mirrored threaded part is scrap.

---

# Tier 3 — remaining high-impact work

In `nextsteps.md` priority order, still excluding non-`.goo` export.

`C8` close open cut faces (72) — the four skull surfaces; shares planar-capping
with `C7`. `E7` TSMC semantics and motion field validation (66), which `B5`
depends on. `B5` real print-time estimation (76). `E4` XY shrinkage and
tolerance compensation (78) — the highest-importance item in its group and
directly relevant to threaded functional parts. `G3` generated typed settings
pages (70) and `G1` visibility tiers (58). `F3` / `F2` / `F4` the performance
sequence. `C2` / `C4` / `C9` / `C3` hollowing, drain holes, wall-thickness
analysis, lattice infill. `C1` / `A10` / `G5` / `E2` the multi-part scene and
scoped overrides. `E8` exposure calibration generator (52). `I3` print-time
auto-calibration (22, needs measured prints).

## STEP import (`N5`)

**FreeCAD now, in-process later.** Shell out to the installed FreeCAD to
tessellate STEP into a mesh, behind an importer interface so a native reader
(gmsh or OCP) can replace it without touching callers.

**Use FreeCAD 1.1.3** — point `VOXELMILL_FREECAD` at a 1.1.3 AppImage, or put
`freecad` / `FreeCAD` / `freecadcmd` on PATH — rather than 1.1.1, unless the
tessellation API differs materially, in which case pin whichever works and
record why in `gotchas.md`. A compatibility check is cheap: tessellate one
STEP with each candidate and compare triangle counts and bounds.

The `freecad-headless` skill on this machine documents the CLI's silent-failure
traps and **must be read before writing that code**. Expose tessellation
deviation as a setting and record it in the report — a STEP part's mesh fidelity
is a slicing input, not an implementation detail. Pairs with `N15`.

## New items added during planning

Folded into the `nextsteps.md` master list and the Artifact.

| ID | Item | Diff | Imp |
|---|---|---:|---:|
| N1 | Raster-domain union fallback for unrepresentable meshes | 48 | 88 |
| N2 | `voxelmill verify` — standalone post-generation `.goo` check | 26 | 74 |
| N3 | Always open a model; clip to build volume; red out-of-bounds | 30 | 70 |
| N4 | GOO layer viewer in the GUI | 28 | 66 |
| N5 | STEP import via FreeCAD 1.1.3, behind an importer interface | 30 | 58 |
| N6 | Build plate edge colouring (red, green front) | 8 | 34 |
| N7 | Give `repair.aggressiveness='none'` real meaning | 8 | 44 |
| N8 | Wrap the escaping `ValueError` from `weld_mesh` | 4 | 36 |
| N9 | Report the true degenerate-triangle count, not per-chunk | 3 | 28 |
| N10 | Fix `mask_to_image` uint8 wrap on 0/255 frames | 4 | 44 |
| N11 | Cache decoded layers; stop rebuilding the Rasterizer per request | 20 | 56 |
| N12 | Resolve the GOO mirroring question against both references | 16 | 78 |
| N13 | `choose_voxel_size` coarsening and deviation headroom | 14 | 48 |
| N14 | STL reader recovery: wrong count, trailing garbage, truncation | 22 | 46 |
| N15 | Tolerant vertex welding for tessellated CAD input | 26 | 52 |
| N16 | Navigation cube and a real camera controller | 24 | 50 |
| N17 | Porous base/raft strategies; feet-only mode | 34 | 64 |
| N18 | De-duplicate `gui/services.py` against `pipeline.py` | 16 | 58 |
| N19 | Interior-face filter for downward contact sampling | 26 | 62 |
| N20 | Union raster parity check in the re-slice | 22 | 66 |

`N15` matters specifically because of `N5`: welding is **exact-coordinate only**
(`mesh.cpp:49-56`), and tessellated CAD output routinely has near-duplicate
vertices that would become phantom boundary edges. STEP import without it will
produce meshes that look broken for no good reason.

---

# Format support and hardware boundaries

The 2026-09-09 request supersedes the earlier GOO-only deferral: implement
`B1` CTB writer, `B2` CTB reader/verifier and `B7` format conversion alongside
the remaining offline work, with explicit supported versions and independent
decode evidence.
`B4` greyscale anti-aliasing stays in Tier 3 because it improves `.goo` output
quality rather than adding a format.

Also deferred: `D6` support mechanics calibration, `E6` LED uniformity, `H4`
SDCP hardware acceptance — physical tests remain deferred until the FEP film
is replaced. Read-only printer communication and camera access are authorized.

---

# Working discipline

- **`todo.md` is the live ledger.** Items are removed as completed; newly
  discovered work is appended in place. It answers "what is left" at a glance.
  `plan2.md` holds the tiering and does not duplicate the ledger.
- **`gotchas.md` gains an entry per verified, non-obvious lesson**, in the
  existing flat-bullet format with a bolded lead sentence. Only things actually
  observed. Several from this planning session go in immediately: the gate being
  stricter than the slicer, the `mask_to_image` wrap, the dead `'none'` mode,
  the zero deviation headroom, that `negative_winding_crossings == 0` does not
  prove absence of cancellation, and that `for_bounds` and the rasterizer
  already clip correctly.
- **Documentation moves with the code, in the same commit.** `docs/cli.md` for
  every new command and flag, `docs/gui.md` for the viewport, navigation cube
  and layer changes, `docs/configuration.md` for every new settings key,
  `docs/algorithms.md` for the union fallback and `.goo` verification,
  `docs/troubleshooting.md` and `docs/geometry.md` for the clipping contract and
  the `seal_voids` difference between paths.
- **`nextsteps.md` and the Artifact stay in step.** New items get IDs and scores
  in the master table, a detail section, and a phase; completed items are marked
  done in the Artifact so its progress tracking stays truthful.
- **Every new feature ships with tests.** Prefer synthetic fixtures
  (`small_settings()`, `RasterGrid` + `Layer` lists, manifold3d primitives) so
  the fast suite stays fast; put anything needing the 50–300 MB originals behind
  the existing `samples` marker.
- **Rebuild native code with `scripts/rebuild.sh`**, never by copying a `.so` —
  a live process can have the old inode mapped and `cp` can SIGBUS it.
- **Render-window tests run in a subprocess under `xvfb-run`** and assert on
  `vtkWindowToImageFilter` output, never `QWidget.grab()`, which silently
  captures a black viewport. This already killed a whole pytest session once.

# Verification

Per tier, before moving on:

1. `.venv/bin/python -m pytest -q` — green, with the collected count grown by
   the tests the tier added. Baseline is **165**.
2. Tier 0: all three float valve STLs run end to end —
   `voxelmill prepare … && voxelmill slice … && voxelmill verify …` — each
   producing a `.goo` that passes the deep check, with the report naming the
   assembly path used.
3. `VOXELMILL_SAMPLES=1 .venv/bin/python -m pytest -q -m samples` still passes
   over the seven original meshes; no regression in placement results.
4. GUI: launch on a part that does not fit and confirm it opens, the plate front
   edge is green, out-of-bounds geometry is red, the navigation cube snaps to
   each named view, and an exported `.goo` can be reopened and scrubbed layer by
   layer.
5. `F10`'s benchmark harness records runtime and peak RSS for a full `prepare`
   on an original — a figure that does not currently exist — and later tiers are
   measured against it rather than asserted to be faster.
