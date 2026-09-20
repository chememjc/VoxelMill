# chopchop implementation ledger

## Restart here (2026-09-09)

Printing is forbidden; read/write to the printer is allowed. Scale stays 1,1,1
until a printed test. Do not invent CHITUBOX spacing, skate elongation, or
small-pillar max length.

Full suite after B5/N13/N14/G10 plus the previous batch: **651 passed, 10 skipped**.

Still open in plan2 (software, no print required):
1. N13 voxel coarsening, N14 STL recovery, N15 tolerant weld; C8 cap skull cuts
   using trim; E7 TSMC docs/validation; B5 uncalibrated print-time estimate;
   B4 greyscale AA; F1 layer threads; F2/F3/F4 performance; C2/C4/C9/C3
   hollowing; G3/G1/G9/G10/G6/G7/G8/G11 GUI; A8 profile embedding; extra
   models as ZIP members.
2. Needs prints / hardware: D6, E6, H4, I3, C10 weight fit, N12 physical
   mirroring, float-valve passing-print.
3. Deferred until .goo work is done: B1/B2/B7 CTB. B3 needs real printer specs.

User UI test comes at the end.

## Completed — D2 paint, grid default, plate bevel, extra models (2026-09-09)

- [x] Manual paint-on **block** and **enforce** (nothing automatic). Centroids
      in plate mm, island births never blocked, block wins over enforce.
      CLI `--paint`, Setup brush, magenta/green on the model actor.
- [x] New jobs default to `base_type=grid`.
- [x] Plate outer perimeter `raft_slope_deg=30` putty-knife bevel; plate
      contact area unchanged; `base_edge_slope_deg` still refused on plate.
- [x] `--add-model` / Parts → Add model: refuse only model-solid intersections;
      shared support field so parts can anchor on each other. Extra models are
      path records, not yet extra ZIP members.

## Completed this session — drop, voids, faults, skate (2026-09-08)

- [x] Drop unroutable already-attached/near-vertical contacts after routing
      (`support.drop_attached_unroutable`, default on). Island and
      manual/correction contacts are never dropped. True free overhangs still
      fail `support_routes`. GUI `build_supports` now calls `plan_supports`.
- [x] `repair.support_void_policy` fail|ignore|fill. Classification is
      model-only absence. Fill is exact-path enclosed shells; raster reports
      `not_run`. Tip crevices are drainage necks — less taper / wider contact
      closed them on the sphere, not a longer tip. GUI export applies the
      same policy as CLI prepare.
- [x] Faults tab: 2D + shared 3D, same color per class, key, Z clipping.
- [x] CHITUBOX screenshots: skate frustum, raft=None, 10 mm / 0.80 mm / 30°.
      `chitubox-mars5` uses `base_type=skate`. `plate` remains the solid option.
      Prefer grid for less resin/suction. Spacing, elongation, small-pillar max
      length stay in `CHITUBOX_UNSET`.

- [ ] D2 paint-on enforcers/blockers, then D1 branch/tree.
- [ ] Process presets / profile embedding (A8), C6/C7, remaining Tier 1/3.

## Completed — CHITUBOX skate frustum + preset (2026-09-09)

- [x] Skate with `base_edge_slope_deg` is a plate-widest frustum (shared
      printed-layer staircase); slope 0 keeps the vertical capsule.
- [x] `chitubox-mars5` → `base_type=skate`, touch 10 mm, thickness 0.80 mm,
      length 0, slope 30°. `CHITUBOX_UNSET` still records spacing, elongation,
      and small-pillar max length; pad-as-skate-approximation note removed.
- [x] Documented `plate` bevel contract (`raft_thickness_mm` + fixed
      `min(0.25, t/4)` bevel; no raft-slope key) vs per-foot 30° via
      `base_edge_slope_deg`. Global default `base_type` remains `plate`.

## Completed — D3 per-support editing (2026-09-08)

- [x] Per-contact geometry parameters in the shared routing loop, with global
      defaults unchanged and unmatched overrides reported explicitly.
- [x] Individual/multiple selection, batch edit, copy/paste and reset in editor;
      undoable changes and project persistence, including move/delete behavior.
- [x] Independent tip shape/taper and optional break-point ball geometry.
- [x] CLI/project/GUI parity, regression and real-geometry checks, documentation
      and verified lessons. D2 painting follows this implementation.
- [x] Setup tab exposes every personal field; support editor combo for
      `tip_shape`; `--contact-parameters`, `--tip-shape`,
      `--break-point-diameter-mm`. `fill_legacy_settings` supplies missing
      support keys on old schema-1 projects without accepting a half-written
      peel table.
- [x] Full suite after D3 close-out: **579 passed, 10 skipped** in 59.30 s.

## Completed — C5 surface peel-risk advisory (2026-09-08)

- [x] Full-resolution downward triangle scan and exact shared-edge connected
      regions, with projected/surface areas, bounds and physical layer spans.
      Chunked scans, budgeted array connectivity and grouped reductions keep
      resource limits explicit. Detail cap preserves complete aggregates.
- [x] Uncalibrated area/depth/configured-speed score, with bottom/normal stages
      selected relative to physical plate Z=0. Warnings remain advisory and
      independent of drainage. No geometry or printer motion is modified.
- [x] Validated top-level `peel` settings, shared CLI toggle, resolved editor
      settings, project persistence and full-profile round trips. Hardware-only
      profiles omit analysis policy. Disabled/failed checks report `not_run`.
      Older projects inherit an absent peel table; incomplete authored tables
      remain errors, covered in both editor and CLI loading.
- [x] Shared reopened-STL validation covers prepare and editor exports;
      standalone STL validation and STL-to-GOO source validation also report
      peel evidence. Standalone GOO verification explicitly lacks oriented
      surface evidence and makes no surface-check pass claim.
- [x] Synthetic tests cover connectivity, separated/vertex-touching regions,
      angle thresholds, physical layer origin, speed/depth effects, detail
      limits, cancellation/resources, disabled checks, exports and profiles.
- [x] Updated `plan2.md`, `nextsteps.md`, CLI/editor/algorithm/config docs and
      verified lessons in `gotchas.md`.
- [x] Final full regression: **565 passed, 10 skipped** in 57.52 s. Focused C5 checks:
      **21 passed** across the focused files; strengthened drainage and legacy
      project compatibility assertions also pass.
- [x] All six isolated benchmarks passed with no regressions or missing
      baselines; `reports/bench/tier2-c5.json`. Compilation and whitespace
      checks pass. Original fixtures: **9 passed** in 70.74 s. The final full
      suite above includes the legacy-project compatibility fix.

Real-input evidence: `reports/plan2/peel-risk.json`, independent fresh processes.
The 78,028-triangle nut took 0.069 s / 66,176 KiB peak RSS and contains two
regions above the default 100 mm² threshold (maximum 232.965 mm²). The original
5,999,999-triangle right temporal bone took 1.789 s / 561,784 KiB and has no
region above that threshold (maximum 75.285 mm²). No decimation was used.
These results do not establish safe release mechanics or successful printing.
No native rebuild or additional printer traffic was needed. Two smaller workers
handled bounded implementation, tests and review, with at most two active.

Next implementation: D3/D2 manual/painted support controls and D1 branch/tree
supports. Physical score/force calibration and passing-print gates remain open;
printing remains forbidden by the current user instruction.

## Completed — C10 candidate controls and read-only printer inspection (2026-09-08)

- [x] Expose ranked feasible finalists with exact transforms, term values,
      weights, contributions, full-resolution bounds and assessment limits.
      Missing assessments remain visible with null totals; a single finalist
      receives the same score evidence as a larger list.
- [x] CLI `--rotate auto --candidates N --candidate-rank R` prints ranking
      evidence separately from JSON. Explicit choices persist as exact angles
      in projects. Unavailable ranks fail with the actual candidate count.
- [x] Editor candidate list, score details and apply control; production
      synthetic auto search and rank-2 selection reproduce reported bounds.
      Selection is undoable, and changed source/settings/placement inputs
      invalidate stale results.
- [x] Authorized read-only printer discovery, fresh status, attributes and
      file listing. Idle state confirmed; 53 files listed. No upload, print,
      motion, camera or settings commands were sent. Evidence and unresolved
      attribute discrepancies: `reports/plan2/printer-readonly.json`.
- [x] Fixed strict JSON output for failed coverage with no contacts: undefined
      distance is null, failure remains explicit. CLI regression covers it.
- [x] Updated CLI, GUI, algorithm and SDCP documentation, `plan2.md`,
      `nextsteps.md`, and verified lessons in `gotchas.md`.
- [x] Final full regression after the no-contact report fix: **544 passed,
      10 skipped** in 57.66 s; compilation and whitespace checks pass.

Focused candidate tests: **13 passed**. Original fixtures: **9 passed**.
Real float-valve nut: 78,028 triangles, three assessed candidates in 0.74 s,
peak RSS 80,072 KiB; rank 2 selected without recomputation. Evidence:
`reports/plan2/orientation-candidates.json`. This measures orientation search,
not routed support success or physical printability. No native changes.
Two smaller workers handled bounded implementation, tests and review; finished.

Remaining next items:

- [ ] C10: calibrate weights against printed outcomes; measure actual routed
      support volume per candidate. Current reports explicitly label both
      limitations. Printing is forbidden by current user instruction.
- [x] C5 surface peel advisory completed above.
- [ ] D3/D2 manual and painted support controls and D1 branch/tree work,
      as ordered in `plan2.md` Tier 2.2.
- [ ] Physical mirroring and Tier 0 passing-print acceptance remain open.
- [ ] Sync the externally published roadmap Artifact when its editing tool
      becomes available; only the local roadmap is updated here.

## Completed — Tier 2.1 support architecture (2026-09-08)

- [x] Triangle-connected bases: sorted Delaunay edges, perimeter rim and
      measured footprint; degenerate layouts use a connected foot tree.
- [x] Independent cone/cylinder model-anchor endpoint diameter, length and
      penetration, with full bottom-length reservation and sampled clearance.
      Zero bottom length/depth preserves direct attachment. Invalid candidates
      retain the failed-route gate when no permitted plate route exists.
- [x] Whole model-to-model small-pillar mode, distinct from the retained
      thin-middle class. Cone/cylinder buried ends have independent upper/lower
      depths. Selection uses the whole gap; zero maximum length disables it.
- [x] CLI/editor parity, portable settings and rendered examples; four
      synthetic production prepare/export/reopen cases cover both shapes for
      ordinary model anchors and whole small pillars.
- [x] CHITUBOX table audit against official documentation. Corrected 70° from
      vertical to 20° from horizontal; recorded known small-pillar dimensions
      and bottom depth. Portable preset notes preserve every missing mapping
      and the explicit derivations. See `docs/support-presets.md`.
- [x] Documentation, `plan2.md`, `nextsteps.md`, and three verified lessons in
      `gotchas.md` updated. Prior working-tree changes preserved.

Final full suite: **531 passed, 10 skipped** in 57.84 s. Original fixtures:
**9 passed** (`CHOPCHOP_SAMPLES=1`, `-m samples`), including the original latch
GUI test. All six isolated benchmarks passed with no regressions or missing
baselines (`reports/bench/tier2-complete.json`). Python compilation and
`git diff --check` pass. The real VTK support editor rendered a model-anchor
length change from 1 to 4 mm: **1,981 changed pixels**, 66,307 surface pixels.
Qt/Xvfb required the documented outside-sandbox workaround for that check.
Two smaller subagents handled bounded work; both are finished. No native
rebuild was needed and no printer traffic occurred.

Real-part evidence: `reports/plan2/tier2-architecture.json`. The triangle-only
nut run retains 195 routes / 34 failures, one connected 542.517 mm³ base, and
passes reopened-STL raster parity. A second run with larger explicit bottom
attachments rejects all 85 model anchors (110 routes / 119 failures). Its
void check passes because those attachments are absent, not because the part
became printable. Both exports are warned; original acceptance is still open.

Remaining reference and physical acceptance (not implementation claims):

- [ ] Supply measured CHITUBOX support spacing, skate elongation, maximum
      whole-small-pillar length, and bottom transition geometry. The reference
      preset is deliberately partial until these are available; do not call
      its dimensions a reproduction of the known-good print.
- [ ] Calibrate adhesion, removal and support mechanics on printed parts;
      finish the Tier 0 passing-print and physical mirroring gates below.
- [ ] Update the externally published roadmap Artifact for this checkpoint.
      The local roadmap is current; this session has no callable tool for
      editing that Claude-hosted Artifact, so its update is not claimed.

Next code work is Tier 2.2. Named process presets/profile embedding remain
Tier 1 backlog and are separate from this support-geometry completion.

## Completed — reporting and offline workflow gaps (2026-09-08)

- [x] Standalone GOO verification records caller-supplied overlap, growth-span,
      and minimum-void-volume thresholds in `settings_from_caller`, plus the
      void-analysis toggle in `analysis_options`. A regression proves identical
      file bytes change verdict under a stricter overlap policy while the
      file-derived settings remain identical. Lesson recorded in `gotchas.md`.
- [x] Viewport overflow message labels its triangle total as a display-only
      count; GUI regression and documentation updated.
- [x] `docs/sdcp.md` documents a runnable offline simulator workflow, verified
      through start/pause/resume/cancel with no network or printer traffic.

Verification: **488 passed, 10 skipped** in 55.20 s; focused GOO tests
**20 passed**, printer tests **13 passed**, and viewport-label test **1 passed**.
`git diff --check` is clean. Two smaller subagents handled the independent GUI
label and simulator documentation tasks and both finished. No native changes
or performance-path changes; the benchmark baseline was not rerun.

The subsequent Tier 2.1 completion is recorded above. Real-part passing-print
acceptance and physical mirroring remain open.

## Completed — Tier 2.1 honeycomb and sloped base edges (2026-09-08)

Continues the block below. Full suite **487 passed, 10 skipped** in 52.03 s;
release gate green with zero benchmark regressions and no missing baselines
(`reports/bench/tier2-hex-slope.json`).

- [x] `hex`: hexagonal openings on the triangular lattice whose six neighbours
      are all `base_cell_size_mm` apart, so pitch means the same
      centre-to-centre distance for both lattices, plus the same perimeter rim
      and MST foot tethers the grid uses. `MAX_HEX_CELLS` refuses an impossible
      density before emitting anything.
- [x] `base_edge_slope_deg`: any added base tapers inward from the plate,
      widest where it touches, quantised to whole printed layers at the
      resolved `process.layer_height_mm`. `0` keeps the vertical wall; the
      value is refused on `plate` (which has its own bevel) and `none`.
- [x] Eleven new tests in `tests/test_bases.py`: hexagonal cell area against
      the closed form, the equal-open-fraction result against `grid`, feet
      between cells, the complexity refusal, plate contact preserved under
      slope for all five added bases, the exact top-band inset, and the
      exhaustion failure with the height it reached.

Two findings, both in `gotchas.md`. A honeycomb is **not** a cheaper grid: at
equal pitch and wall width both open `((p-w)/p)²` by construction, and on the
nut `hex` 458.92 mm³ against `grid` 452.37 mm³ is the honeycomb coming out
1.4% *heavier*. And a union of solids that only touch face-to-face is
not reliably one body: a tapered base with the exactly right volume decomposed
into two, and the count follows band height rather than band count. Every band
is extruded from the plate to its own top instead, which overlaps in volume and
yields the identical staircase.

Real-part evidence, same settings as the table below, `metrics.supports.base`:

| Base | Volume mm³ | Contact area mm² | Open fraction |
| --- | --- | --- | --- |
| `hex` | 458.92 | 573.65 | 0.300 |
| `grid` | 452.37 | 565.46 | 0.310 |
| `hex` at 70° | 396.35 | 573.65 | 0.300 |
| `grid` at 70° | 391.07 | 565.46 | 0.310 |

The taper takes another 14% of the base volume and leaves the plate contact
area bit-identical, which is the whole argument for it. Routing, coverage and
every failing check are unchanged from the four runs below.

Independent model anchors and triangle connections were completed in the
checkpoint above. Whether a tapered edge is easier to get a blade under is
untested; nothing here has been printed.

## Completed — Tier 2.1 base strategies (2026-09-08)

Baseline entering this block: **430 passed, 10 skipped**; original samples
**9 passed**. Final: **475 passed, 10 skipped** in 52.88 s, and the opt-in
original-fixture suite (`CHOPCHOP_SAMPLES=1`, `test_samples.py` plus
`test_gui_cold_start.py`) **10 passed** in 40.71 s.

- [x] Implement configurable elongated skate feet, skeleton/MST base, and
      porous orthogonal grid base; preserve the existing plate default.
- [x] Measure actual base footprint and volume, count connected components,
      and keep removal descriptions explicitly geometric rather than calibrated.
- [x] Expose every setting through CLI, presets and the support editor's live
      example. One smaller worker owns GUI wiring and GUI parity tests.
- [x] Verify dimensions, connectivity, footprint savings, degenerate foot sets,
      settings/file round trips, and the production preparation path.
      `tests/test_bases.py` is **37 passed**: skate length/width/thickness and
      rotation independence against measured bounding boxes and exact polygon
      areas, one connected component for skeleton and grid with every lower
      pillar fully inside the base, real through holes in a rotated grid, the
      MST checked against an independent dense-graph solution, single,
      duplicate, two-point and collinear foot sets, input-order invariance to
      the triangle, and bit-identical legacy plate geometry.
      `tests/test_gui_bases.py` is **8 passed**: shared choices, a real
      headless preview per base type, and a portable support-file round trip
      of every new field.
- [x] Update docs, gotchas, plan status and run regression/performance checks.

Production preparation path, `inputstl/floatvalveR7-nut.stl`, one correction
pass, `--allow-unresolved`, 101 unique feet under 110 routed feet, one
connected base in every case. Every support metric other than elapsed seconds
is identical across the four runs — 195 contacts routed, 34 failed, the same
four failing checks — so the differences below belong to the base alone:

| Base | Volume mm³ | Contact area mm² | Open fraction | Bottlenecks |
| --- | --- | --- | --- | --- |
| `plate` | 965.80 | 969.26 | 0.000 | 6 / 0.399 mm³ |
| `skate` | 472.94 | 591.17 | 0.345 | 3 / 0.126 mm³ |
| `grid` | 452.37 | 565.46 | 0.310 | 4 / 0.0266 mm³ |
| `skeleton` | 293.90 | 367.38 | 0.552 | 4 / 0.0266 mm³ |

51% to 70% less base resin than the slab. Each run reopened its export and
matched the recorded sha256. `drainage_bottlenecks` still fails for all four:
the base changes the size of that problem, not the verdict. Worked commands
and this table are in `docs/examples.md`; the two new findings — that the base
*does* move trapped resin on a real part, unlike on the synthetic sphere, and
that a hull-clipped grid is not automatically one connected solid — are in
`gotchas.md`.

Release/performance gate passed: full suite green, all six isolated benchmarks
within the checked-in tolerances, zero regressions, no missing baselines.
Evidence: `reports/bench/tier2-bases.json`.

Subsequent checkpoints above complete hex, sloped edges and independent
model-anchor geometry. Process preset embedding, Tier 2.2, and physical Tier 0
acceptance remain open.
No printer traffic. The verification and documentation continuation that closed
this block used no subagents.

Implementation findings: the old bare-foot area summed ideal nominal circles,
ignoring thin pillars, faceting and overlaps. It now measures the actual union
of plate-contact polygons; the ideal sum remains as `nominal_disc_area_mm2`.
Plate geometry is pinned bit-identical. Grid connectivity includes a perimeter
rim and MST foot tethers, and complexity limits fail before emitting partial
bases.

Two documentation corrections found while verifying, both stale rather than
new: `docs/examples.md` still blamed the pillar/raft junction for the sphere's
drainage bottleneck, which `gotchas.md` had already corrected to the tip/model
crevice; and `docs/support-presets.md` listed three built-in presets when
`list_presets()` returns four. The `chitubox-mars5` preset deliberately stays
on `base_type = 'pad'` now that `skate` exists — the CHITUBOX table names the
shape but gives no elongation, and `skate` with an underived length is the
same round foot under a name implying a measurement nobody has. The reason is
recorded in `presets.py` and `docs/support-presets.md`.

The subsequent checkpoints above complete the remaining base and anchor
geometry. No base has been printed on; adhesion, removal force and peel
stability remain uncalibrated.

## Completed — Tier 2.1 and configuration editors (2026-09-08)

- [x] Recovered interrupted work: independent tip diameter, branch angle,
      short-pillar class, brace spacing/start height, plate/none/circular-pad
      bases, CHITUBOX partial preset and dimensional tests exist in source.
      The drainage attribution correction is already in gotchas.md.
- [x] Verified recovered support/preset block: **32 passed**. Broader support,
      configuration, and batch regression: **68 passed** before new editor tests.
- [x] Add part-to-part policy: forbid model anchors, prefer plate routes with
      adjustable avoidance, or score both kinds equally; preserve route failures.
- [x] Expose brace diameter and neighbour reach as adjustable settings alongside
      existing enable, spacing, and start controls; check brace/model collisions.
- [x] Dedicated printer, resin, and support editor windows, portable saves/loads,
      shared validation and undoable application; live rendered support example.
- [x] CLI parity for editor saves and the support example; resin save delegated
      to one smaller worker (maximum two agents including the primary).
- [x] Update module docs, plan status, and six verified gotchas. Printer saving
      compares staged content before replacement, excludes omitted resin metadata,
      and supports hardware-only files. Resin saving resolves against the actual
      printer; quoted TOML identifiers and motion keys round-trip.
- [x] Isolated benchmark gate: all six scenarios within baseline tolerances,
      no missing baselines. Evidence: `reports/bench/tier2-editors.json`.
- [x] Final full regression suite after last fixes: **430 passed, 10 skipped**
      in 51.61 s. Python compilation and `git diff --check` are clean.
- [x] Verify editor integration and real VTK rendering; parameter edits change
      rendered pixels, not just Qt widgets. Apply invalidates parent jobs while
      the editor remains open. Additional focused checks: **55 passed**.

Final verification: full suite **430 passed, 10 skipped**; the dedicated
real VTK editor render test passes and asserts that changing pillar diameter
changes rendered pixels. Qt/Xvfb could not connect inside the sandbox (the
existing gotcha); rendering passes outside it. Original-fixture suite:
**9 passed** outside the sandbox, including the latch GUI.
Benchmark measurements: inspect 0.19 s /
53 MiB, prepare 11.80 s / 160 MiB, validate 11.52 s / 147 MiB, slice 32.84 s /
166 MiB, verify 16.14 s / 130 MiB, bracket prepare 61.00 s / 521 MiB. These are
performance checks against existing expected failing-validation fixtures, not
evidence of passing physical prints.

Subsequent checkpoints above complete base and model-anchor geometry. Process
preset embedding and Tier 2.2 remain open. Tier 0 float-valve passing-print
acceptance and physical GOO mirroring remain open; no printer traffic.

This continuation used one smaller worker sequentially alongside the primary,
with at most two agents active. Prior working-tree edits were preserved; no
native source was changed in this continuation and no rebuild was required.
All background commands and delegated work have finished. Restart an existing
GUI to load the Python changes; open Configuration → Printer/Resin/Support editor.

## Prior work — Tier 1: profiles, resin economics, compensation, islands, transforms (2026-09-07)

Baseline entering this session: **281 passed, 8 skipped**.

- [x] `A3` profile discovery and the system/user split. New `profiles.py` owns a
      four-layer search path — `CHOPCHOP_PROFILE_PATH` entries, then
      `$XDG_CONFIG_HOME/chopchop/profiles`, then `/etc/chopchop/profiles`, then
      the profiles packaged with chopchop. A higher layer shadows a lower one by
      identifier and `profile list` names the shadowed files rather than hiding
      them. `--printer`/`--resin` now accept a bare identifier as well as a
      path, and the two are distinguished by shape so neither is ever guessed
      at as the other.
- [x] `A6` `profile diff` and provenance. `provenance()` re-runs
      `resolve_settings` with one more layer each time and attributes each leaf
      to the last layer that changed it, so the answer cannot drift from the
      settings it describes. `profile diff --against PROFILE|defaults` reports
      leaf-by-leaf differences with an explicit `<absent>` for one-sided keys.
- [x] `A1` printer profile manager. `profile save --output X.ptr` writes a
      self-contained profile from the fully resolved stack, then re-reads it and
      **requires it to resolve identically**, failing if not. A TOML writer
      lives in `profiles.py` because `tomllib` never writes.
- [x] `A2` resin profile manager. `chopchop resin list|show|bind`, where `bind
      --from ID --to ID --output X.res` copies one printer's process block onto
      another printer id. The copy is verbatim and the payload says so: an
      exposure carried across machines is a starting point, not a calibration.
      Binding refuses to overwrite its own source and refuses an ambiguous
      `--from` or a `--to` equal to it.
- [x] `I2` resin density and cost fields. `resin.density_g_cm3`,
      `resin.cost_per_litre` and `resin.currency`. Both numbers default to 0.0
      meaning "not supplied", and the derived weight and cost are then `null`
      rather than a guessed number. `currency` is capped at 8 ASCII bytes
      because the GOO header field is 8 ASCII bytes, and it is checked when set
      rather than at export.
- [x] `B6` resin volume, weight and cost. `config.resin_usage` derives them from
      the raster volume measurement, which already counts supports and any base
      and is the only honest source because a soup's signed volume is not
      physical volume. `prepare` reports `stages.resin_usage`; `slice` reports
      `resin_usage`; the GOO header's `material_grams`, `material_cost` and
      `price_currency` were hard-coded 0/0/`$` and are now derived, staying
      0/0/`$` when the profile supplies nothing.
- [x] `E5` first-layer / elephant-foot compensation.
      `process.elephant_foot_compensation_mm` (0 disables) and
      `process.elephant_foot_layers` (0 derives from `bottom_layers`) shrink the
      exported bottom frames by a radius that ramps linearly to zero. Erosion
      runs on the cropped mask, which is the same answer as eroding the
      36.8 Mpx panel and far cheaper. **Both the write pass and the
      verification pass apply it**, so `decoded_pixels` still proves the file
      matches the intended exposure. A compensation that would erase a whole
      layer is refused rather than exported, and the report names the pixels
      removed per layer.
- [x] Editor parity: File > Profile library... reaches everything `profile` and
      `resin` do — browse with shadowing and parse errors shown, apply a
      printer/resin pair as one undoable edit, diff against the editor's
      settings, show provenance, save a `.ptr`, bind a resin. The compensation
      settings reach the editor through the Setup tab's settings box and the
      Run operation form, which is generated from the CLI parser.
      `test_every_cli_subcommand_has_a_place_in_the_editor` now leaves `gui` as
      the only deliberate exception; `profile` is no longer one.

- [x] `D5` re-run island detection after every edit, with a persistent badge.
      `validation.island_summary` extracts the evidence from a completed layer
      analysis without recomputing anything, so the badge and the authoritative
      check cannot disagree. `pipeline.scan_islands` runs it on a file for the
      new `chopchop islands` command; `gui/services.scan_islands` runs it on the
      in-memory assembly for the editor. Same analysis, different source of
      layers, asserted equal by a test. The badge shows not-checked / a count /
      **(stale)**, greys out after any edit, and re-earns itself after every
      rebuild (Edit menu toggle, on by default; Ctrl+I to run it now). It is
      never an export gate: `other_checks` states each attempted check's own
      status including `not_run`, and `not_examined` names drainage, support
      routes, plate fit and raster parity, which this pass never attempts.
- [x] `C12` measure, mirror and per-axis scale with scaling guardrails.
      `geometry.scale_matrix` owns the bounds and the sign rule; scale must be
      positive within [0.01, 100] and mirroring is a separate per-axis flag,
      never a negative factor. **`iter_transformed_triangles` now reverses
      triangle winding on any negative determinant** — without it a mirrored
      part rasterizes as empty. `prepare --scale/--mirror` records
      `stages.transform` and a `model_transformed` **warning** diagnostic plus a
      stderr banner; nothing is scaled automatically and the warning does not
      block an export the user asked for. Combining a transform with
      `--rotate auto` is refused, because the search does not consider either.
      New `chopchop measure` reports source and placed sizes, fit, overflow and
      layer count, and solves `--target-mm` for the per-axis and uniform factors
      that reach a wanted size (a zero leaves that axis unconstrained; the
      uniform factor is the smallest so nothing overshoots). The editor gets
      Setup-tab scale/mirror rows, a live measured-size row and File > Measure
      STL, all on the same functions.
- [x] `H1` batch mode. `chopchop batch OPERATION INPUT... --output-dir DIR`
      runs one operation over many inputs, keeping each item's own report plus a
      `batch-manifest.json` holding every item's exact argv, exit code and
      elapsed time. `--extra` passes arguments through to be parsed by *that
      operation's own parser*, so an option cannot exist for a single run and
      not a batched one; `--report` and `--output` are named by the batch and
      refused in the pass-through. The batch re-emits its own settings flags per
      item, so an item cannot resolve different settings from the batch that
      launched it. `--continue-on-error` keeps going; without it the run stops
      at the first failure and the manifest records `stopped_early`. Items share
      one process and the manifest says so rather than implying an isolation the
      code does not provide.
- [x] `H6` shell completion and man page. `chopchop completion {bash,zsh,fish}`
      and `chopchop manpage`, both generated from the live `build_parser()`, so
      a new command or flag appears the moment it exists and there is no
      checked-in script to forget. The bash script is syntax-checked with
      `bash -n` and the man page rendered with `groff -ww` in the test suite;
      zsh and fish are checked when those shells are installed and skipped
      otherwise. **Generating the man page immediately exposed 27 flags with no
      `help=` text** — equally missing from `--help` — and all 27 now have it;
      a test asserts `'No description.'` never appears again.
- [x] Bug found while documenting: a CLI-saved `.chop` lost its scale and
      mirror on reopen, because `prepare` wrote them only inside
      `placement`, while `cmd_prepare` restores from the top-level
      `scale_factors` / `mirror_axes` keys the editor writes. Fixed and covered
      by `test_a_cli_saved_project_reopens_at_the_scale_it_was_saved_with`.
- [x] `G4` tooltips with config keys, modified markers and revert arrows.
      Every compact Setup control names its exact `section.key` and the CLI flag
      that sets the same value, so neither interface is a dead end; a test
      asserts every named key exists in the defaults and every named flag exists
      in the parser, so a tooltip cannot document something unreachable.
      `Document.baseline_settings` records what the profile stack resolved to
      before any editor edit, a dot marks each control that differs (its tooltip
      naming the old and the new value), and a revert button restores that one
      setting as an undoable edit. Applying a profile from the library adopts it
      as the new baseline, so "modified" means changed from the profile the user
      chose. A key absent from the baseline raises `invalid_setting` rather than
      guessing a value.
- [x] Third bug found while documenting: `undo`/`redo` refreshed the undo
      labels and restarted the pipeline but never re-synced the Setup controls,
      so an undone settings edit left every field — and every new modified dot —
      showing the value that had just been reverted. The viewport was right and
      the panel was wrong, which is the worse half to get wrong because the next
      edit is built from the panel.
- [x] Two more bugs found while documenting `batch`. `--report` was inherited
      from the shared parser and never read, so a named manifest destination was
      silently ignored; it now names where the manifest goes. And
      `_shared_batch_arguments` enumerated the flags it forwarded, silently
      dropping every boolean toggle and half the numeric ones — a batch accepted
      a flag and then ran every item without it. Forwarding is now **derived**
      from the same `_overrides` dictionary a single run builds, so a new flag
      joins the batch the moment it joins the CLI. The test asserts an item and
      its batch resolve identical settings rather than that particular flags
      appear.

Session hygiene: no printer traffic, no device connection, no native code
changed and no rebuild needed. Documentation moved with the code — four lesser
subagents wrote `docs/profiles.md` and the `cli`, `configuration`, `gui`,
`geometry`, `algorithms`, `architecture`, `examples` and `troubleshooting`
updates, never more than one running beside the primary agent. Three of the
four found real defects while reading the code they were documenting, all
listed above and all fixed with tests.

Not done, and why:
- [ ] `B3` Elegoo printer database. Deferred deliberately: it needs real panel
      resolutions, pixel pitches and build volumes per machine, and inventing
      them would put fabricated specs behind a name a user trusts. The library
      that would hold them now exists, so adding verified machines later is a
      data change rather than a code change.
- [ ] `A8` named *process* presets and presets embedded in profiles. The support
      half shipped earlier; the process half is still open.

Now: **374 passed, 10 skipped**, from the 281 this session started at.
Opt-in samples suite: **9 passed** over the original meshes, no regression.
`scripts/check.py` passed on the final build: full suite green and all six
isolated small-fixture benchmarks inside their checked-in time and memory
tolerances — zero regressions and no missing baselines. Measured this run:
inspect_small 0.20 s / 53 MiB, prepare_small 12.82 s / 167 MiB, validate_small
12.59 s / 147 MiB, slice_small 34.76 s / 166 MiB, verify_small 15.60 s /
130 MiB, prepare_bracket 59.69 s / 508 MiB. `git diff --check` and Python
compilation both clean. No native code changed, so no rebuild was needed.
The two extra skips are the zsh and fish completion checks, which skip when
those shells are not installed; bash and groff are, and both run.
New tests: `tests/test_profiles.py` (23), `tests/test_gui_profiles.py` (7),
`tests/test_elephant_foot.py` (11), `tests/test_islands.py` (5),
`tests/test_transform.py` (15), `tests/test_batch.py` (10),
`tests/test_shellhelp.py` (9, 2 skipped), plus GUI badge, transform and
settings-marker tests in `tests/test_gui.py`. Sixteen gotchas recorded, including a stale packaged profile
that discovery would have turned into a `goo_motion` refusal and a mirror that
would have rasterized as empty.

## Prior work — CLI/GUI completion and layer crash (2026-09-07)

- [x] Reproduced Preview → Layers SIGSEGV with `Latch_fat_finger.stl`.
      GDB identified stale pybind11 thread state at `PyEval_AcquireThread`.
      Pinned/rebuilt with pybind11 3.0.4 (upstream #5870). Fresh-process
      headless and real VTK regressions plus the exact latch all pass (3 tests).
- [x] Background requests now carry request IDs: obsolete same-name progress
      and results cannot replace a newer layer or remove its cancellation token.
      Two new overlapping/cancellation tests pass (one delegated agent).
- [x] Tasks → Run operation exposes every CLI command/option in a responsive
      subprocess form, including correction passes, component STLs, contact
      files, project output, analysis flags and per-operation profiles. Editor
      settings/pose/contacts supply defaults; explicit overrides win.
- [x] Reusable native preview indices now avoid sorting on every cache miss;
      backward/cancelled sweeps reset, overlapping requests serialize, final
      partial layer is reachable. Exact/grouped mask parity tests added. Latch
      eight-slice benchmark: 28.75 → 7.41 ms (3.88x), all masks identical;
      evidence `reports/plan2/latch-preview-benchmark.json`.
- [x] CLI `prepare edited.chop` restores stored settings, pose and contacts;
      explicit flags override each field. Verified temporary extraction and
      input/output alias protection; Qt is not imported by the CLI. Six tests
      cover the project path, overrides, profiles and original STL defaults.
- [x] Portable support presets (D7/support portion of A8): light/medium/heavy,
      validated JSON load/save, `--support-preset` and `preset list/show/save`,
      undoable GUI menu and all-options form. Process presets/profile embedding
      remain open. Core and cross-interface tests pass.
- [x] Final fast suite: **281 passed, 8 skipped** (46.11 s). Opt-in original
      hash/placement/raster and cold-start real VTK/latch run: **10 passed**
      (30.36 s). Python compilation and whitespace checks pass.
- [x] `scripts/check.py` release gate passed: full tests green and all six
      isolated small-fixture benchmarks within the checked-in time/memory
      tolerances; zero regressions and no missing baselines. Evidence:
      `reports/bench/cli-gui-completion.json`.

Next restart work:
- [ ] Tier 0 float-valve passing-print acceptance and physical mirroring remain
      unresolved; validation gates were not relaxed. Continue the routing/
      mechanical and support-pocket investigations below.
- [ ] Tier 1 profile discovery/management and named process presets remain open.
- [ ] Update the external Claude roadmap Artifact separately; local plan2,
      nextsteps and documentation are current. No external Artifact was edited.

No background jobs remain from this implementation session. Restart an existing
GUI process to load the rebuilt native extension. One lesser subagent was used
sequentially alongside the primary agent (maximum two active agents).

Live state of remaining work. Items are removed when complete; newly discovered
work is appended in place. Stage numbering follows `plan.md`.

Standing decisions (2026-09-06 user):
- Invalid-solid fixtures are closed with a **native C++ voxel repair** (occupancy
  voxelization + well-composedness + manifold boundary), not OpenVDB. Slicing
  stays soup-tolerant via a nonzero-winding fill rule.
- Effort is **depth-first through stage 2** before GUI/GOO/printer.
- New venv packages and network reference fetches are permitted.

Historical 2026-09-07 restriction: the printer was in use and connections were
forbidden. Superseded 2026-09-08 by permission for communication, but no printing.

## Prior checkpoint — plan2.md Tier 0 (2026-09-07)

The current request prioritizes `plan2.md`; its Tier 0 supersedes the older
stage-2-before-GUI sequencing above. That checkpoint used no device traffic;
the current permission is recorded at the top of this ledger.

Implemented and tested this session:

- Tier 0.2: twelve explicit build-volume edges, eleven red and the front (-Y)
  bottom edge green. Headless topology/color test and real VTK render passed.
- Tier 0.1 core: `assembly.py` owns shared CLI/GUI ingestion and exact/raster
  assembly. `repair=none` skips solid conversion; exact-only assembly refuses
  fallback. Open contours, cancellation and resource errors remain failures.
- STL export streams triangle groups; independent reopened-STL raster parity
  uses a fresh grouped OR on the reopened grid. Mismatches carry pixel counts
  and position examples. Reports, CLI stderr and saved validation show fallback.
- Degenerate counts cover all chunks; native weld ValueError is structured;
  skipped intersection scans have reasons. Soup solid volume/genus are null;
  raster volume and unavailable cavity fill are reported honestly.
- Synthetic supported sphere exact/grouped equality, positive overlapping shells,
  penetrating reversed-shell cancellation, degenerate faces, open contours,
  CLI/GUI export, old project settings and streamed STL regressions pass.
- Geometry, algorithms, troubleshooting, CLI/configuration/GUI docs, `plan.md`
  and `nextsteps.md` now describe the implemented contract.

## Completed 2026-09-07 (prior session)

Kept rather than deleted because each carries the measurement or the
evidence path a later session needs; the open list below is only what is
left.

- Investigate raw-face support selection/routing. Done, and the cause was
      neither interior faces nor search breadth: the router refused to anchor on
      model material unless the gap exceeded a full `tip_length_mm`, and these
      parts' blocked contacts sit 1.39-1.48 mm below their overhangs.
      `support.min_tip_length_mm` (0.30 mm, the CHITUBOX contact depth) lets the
      tip cone span a shorter gap. Routed: nut 110 -> 195 of 229, cover 389 ->
      722 of 773, body 516 -> 994 of 1,085. A 64-candidate branch search
      recovered zero on all three. Evidence: `reports/plan2/routing-summary.md`,
      `scripts/routing_probe.py`, `reports/plan2/{nut,cover,body}-routing-probe.json`.
- 0.3 Navigation cube and camera helpers. `gui/camera.py` owns named views,
      `steps_to` transitions and `fit`; the annotated cube sits top-right with
      Front on -Y, is picked separately from the model, and every view has a
      menu item and a shortcut. `chopchop gui --view NAME` sets the opening view.
- 0.4 Load oversized models, report clipping, and highlight overflow.
      `assembly.clip_to_build_volume` (default false) turns the refusal into a
      recorded clip; `envelope_overflow_mm` and `placement_or_overflow` describe
      a part that does not fit; the editor always opens it and paints the
      unreachable triangles red on the model actor. The clip record measures
      source, retained and clipped pixels exactly on a panel-aligned extended
      grid, plus clipped triangles and layers. `clipped_geometry` is an error
      diagnostic, so an export is still withheld without `--allow-unresolved`.
- 0.5 Open GOO and fix/cache layer previews. Layers-tab source selector,
      File > Open/Close GOO plus Verification → Verify GOO, `mask_to_image`
      normalises before scaling and decimates by block maximum, and
      `layer_cache` is a byte-bounded LRU keyed by source. `voxelmill gui --goo
      PATH` opens one at startup.
- 0.6 Standalone decoded-GOO topology verification and export integration.
      `chopchop verify <file.goo>` and the editor's Verify item run the same
      `verify_goo`. `slice_stl` records `verification.layer_topology =
      equivalent_to_source` rather than recomputing a provably identical answer.
- Tier 1 `F10` benchmark harness. `scripts/benchmark.py` runs each scenario
      in a **fresh process** that reports its own `RUSAGE_SELF` peak, which is
      what the existing figures lacked: the nut/cover/body numbers in this file
      were measured sequentially in one process, so their peak RSS values are
      cumulative and not comparable. `--baseline` is the regression gate, with a
      loose time tolerance and a tight memory one, because wall time on a shared
      machine is a smoke alarm rather than a measurement. `--samples` opts into
      the scenarios needing the 50-300 MB originals.
- `inspect` and `validate` were CLI-only, and `cmd_validate` held its
      analysis inline in `cli.py`. Both now live in `pipeline.inspect_stl` /
      `pipeline.validate_stl`; the CLI and the editor's Verification menu call
      the same function, and a test compares the two reports for the same file.
- The Setup tab shows the exposure schedule `chopchop profile` prints.
- `chopchop gui --view NAME` and `--goo PATH` bridge the two GUI-only
      affordances (camera view, opened GOO) to the command line.
- `verify` no longer analyses the whole panel. `goo.occupied_crop` finds
      the window every exposed pixel falls inside, plus a one-pixel border, for
      one extra decode pass. Measured on a 500-layer supported sphere:
      **450 s / 1,280 MiB -> 15.8 s / 130 MiB**, 28.5x faster on 9.9x less
      memory, with a test asserting the cropped and full-panel runs agree on
      checks, metrics, enclosed-void count and every diagnostic position.
      `chopchop verify --full-panel` keeps the old behaviour. The remaining
      debt is unchanged: nothing is tiled, so a part that really does fill the
      panel still holds a full-window label image.

Remaining, in restart order:

- [ ] 0.1 Finish real-part acceptance. All three original-pose, 5 mm lift,
      one-pass prepare runs completed. Evidence: `reports/plan2/acceptance-summary.json`;
      full reports: `output/plan2/{body,cover,nut}-prepare.json` (local, ignored).
      Body and nut use raster and pass pixel parity; cover uses exact. Every
      run still fails support routing and drainage. Body additionally fails
      enclosed voids/coverage; nut coverage; cover growth/enclosed voids.
      Normal exports were withheld; no float-valve GOO was produced or verified.
- [ ] The remaining unroutable contacts (nut 34, cover 51, body 91) are all
      already attached. On the printer lattice, every one of the nut's and body's
      and 50 of the cover's 51 have material within their own 3x3 pixel
      neighbourhood one layer below — a 0.054 mm window — which is the same
      attachment `raster_connectivity` certifies and which passes on all three
      parts. They are downward samples on near-vertical walls falling either side
      of the surface boundary. The open question is now **mechanical**: does a
      face attached along its length sag? `downward_contacts` selects on face
      angle alone and cannot distinguish it from a free overhang. No filter was
      enabled. Deciding this needs either an attachment-aware selection rule with
      its own justification, or the sag model that Tier 2's `D6` defers to the
      hardware campaign. Evidence: `reports/plan2/routing-summary.md`.
- [ ] The nut now fails `enclosed_voids` on one 1.62e-5 mm3 component (exactly
      one pixel at one layer) introduced by the new short anchors, plus six
      drainage bottlenecks totalling ~0.4 mm3 in support crevices. Both are the
      support structure rather than the model. Decide whether support-generated
      pockets warrant their own reporting category before touching
      `min_void_volume_mm3`.
      **Located 2026-09-07, and it was not where the notes said.** The crevice
      is where a support *tip* meets the model, not where a pillar meets the
      raft. On the synthetic sphere all three base strategies give a
      bit-identical result and `none` builds no raft at all, while varying the
      tip cone moves it: a 0.9 mm contact or a 0.4 mm tip base leaves zero
      bottlenecks. So no base strategy can remove these, and the lever is the
      tip geometry. `gotchas.md` is corrected and
      `tests/test_support_segments.py` pins the attribution.
- [ ] Re-run full correction passes and useful orientations. The chain itself
      is proven: **all three float-valve parts ran prepare → slice → verify end
      to end for the first time**, across both the raster and the exact assembly
      paths. All three `.goo` files passed every post-write check including
      every decoded LCD pixel against a fresh source raster, and neither
      reported a settings mismatch. Running `chopchop verify` on each file alone
      — no source mesh, different grid, different path into the same analysis —
      reached exactly the same verdict as the source-raster analysis on all five
      shared checks, for every part — three parts, two assembly paths, 2,673
      layers, no disagreement. That corroborates the `layer_topology =
      equivalent_to_source` claim `slice_stl` records instead of recomputing it.
      Evidence and the numbers: `reports/plan2/chain-acceptance.md`.
      Tier 0.1 is still **not** met: its gate is a `.goo` that *passes*, and all
      three are warned — the nut on one single-voxel enclosed void plus routing
      and drainage; the cover on growth span, enclosed voids, 51 routes and
      drainage; the body on enclosed voids, 91 routes, coverage and drainage.
      The cover's `support_coverage` now passes. The body's slenderness and
      anchor-load heuristics now warn, reached only because 994 contacts route
      where 516 did before.
- [ ] Resolve GOO physical mirroring. The offline STL-vs-GOO method was run
      against both references (both from `right_temporal_bone_mars5_oriented.stl`)
      and is **inconclusive**: best mean IoU 0.21 and 0.13, the four sampled
      layers pick three different flips in each file, and widening the Z window
      from 1 mm to 3 mm changed the CHITUBOX winner. Each slicer posed the model
      itself and centroid alignment removes translation but not an unknown
      rotation about Z. Evidence:
      `reports/plan2/mirror-{chitubox,satellite}.json`.
      The plan's fallback is implemented: `printer.image_mirror_verified`
      defaults to false and every export carries a `goo_orientation_unverified`
      warning naming both references' flags.
      Next method to try, which needs no pose at all: the two stored images are
      related by a **proper** rigid motion if and only if the two files' flags
      are mutually consistent, so a chirality test between the two references
      answers it without recovering either pose. A rotation-invariant but
      reflection-sensitive descriptor (complex-moment phase, or matching under
      the covariance eigenframe) is the shape of that test. No printer traffic
      is needed for it.
- [x] The published Artifact is back in step. "chopchop Slicer Roadmap",
      https://claude.ai/code/artifact/fce7f6f9-c6ad-49db-b9fd-d0a4cafe4ae6 — the
      twenty N rows added with details, 77 items becomes 97, the totals now
      derive from `ITEMS.length` so they cannot drift again, and F10 plus the
      thirteen finished N items are checked. N19 is deliberately left unchecked
      with its detail saying measurement superseded it rather than that it
      shipped, and N12 stays open. The footer now says a checked item is
      implemented and tested, never printed and confirmed.
- [x] `scripts/check.py` runs tests then `scripts/benchmark.py --baseline`
      against the checked-in baseline for releases/performance changes.
- [ ] Take a full `--samples` isolated-process performance baseline on originals.
      Only the small-fixture release baseline exists so far; latch preview
      timing is separately recorded in `reports/plan2/latch-preview-benchmark.json`.
- [ ] Tier 1's remaining items stay in `plan2.md`. `F10` is now available to
      measure the rest of the performance group (`F1` multithread across layers)
      against, which was the reason for doing it first.

Reproduce the ingestion prerequisite measurements (no repair or export):

```sh
.venv/bin/python scripts/ingestion_probe.py inputstl/floatvalveR7-body.stl --output reports/plan2/body-prerequisites.json
```

That report includes source SHA-256, pose, full settings, raw and experimental
filtered routing, plus all 1,709 native-pitch layers (zero open rows, zero negative
winding crossings). The two initial probe JSON files retain the first measurements.

Verification: **246 passed, 10 optional sample tests skipped** after Tier
0.3-0.6, the routing fix, the CLI/GUI parity work and the verification crop,
up from the 215 that ended the previous session, including the Xvfb/VTK render
test. `git diff --check` and Python compilation pass. No native code changed or
was rebuilt this session.

Re-run one real-part measurement (exit 2 is the expected current failed-validation
result; preserve the report):

```sh
.venv/bin/chopchop prepare inputstl/floatvalveR7-body.stl --max-passes 1 --progress --output output/plan2/body-supported.stl --report output/plan2/body-prepare.json
```

Measured elapsed times: nut 32.5 s, cover 166.9 s, body 624.5 s. The reports'
peak RSS values are cumulative process high-water marks because these three
measurements ran sequentially in one Python process, so they are not comparable
with each other. `scripts/benchmark.py` exists precisely to stop that
happening again: it runs each scenario in a fresh process reporting its own
peak. Re-measure these three through it rather than trusting the numbers above.
No background job remains running from this session.

## CLI/GUI parity (2026-09-07)

Every CLI subcommand now has an editor equivalent, and a test asserts it so a
new command cannot land on one side only. `gui` (the editor itself) and
`profile` (the resolved-settings JSON box plus the new exposure-schedule row)
are the two deliberate exceptions.

- [x] All preparation flags and per-invocation verify profiles are exposed by
      Tasks → Run operation. Direct interactive export remains a one-pass export
      of the current assembly; choose Run operation for correction passes.

## Discovered while implementing Tier 0.3-0.6 (2026-09-07)

- [ ] The clip measurement pass (`goo._measure_clip`) rasterizes every source
      layer a second time on an extended grid. It is exact and runs only when
      clipping was requested, but it roughly doubles the source-raster cost of
      a clipped export. Measure it on a real oversized part before deciding
      whether it needs an approximate mode.
- [ ] Navigation cube face picking is now exercised in the `xvfb-run` render
      child (front view, centre of the marker viewport, expects `Front`), which
      is what caught `vtkCellPicker` failing to resolve the annotated cube's
      assembly parts. Only that one face and one camera pose are covered; the
      other five faces are covered headlessly through `cube_face_at` only.

## Stage 1 — geometry, placement, cavity analysis
- [ ] Orientation weights (`TRAPPED_WEIGHT`, `CAVITY_WEIGHT`, `ACCESS_WEIGHT`,
      `STABILITY_WEIGHT`) are uncalibrated guesses. Only `actual_support_volume`
      is still listed as unassessed, and it needs a real routing pass per
      candidate rather than a proxy.
- [ ] Feasible orientations cluster: only two finalists 20 degrees apart exist
      for a temporal bone. Worth checking whether a wider initial spread, rather
      than more refinement, would surface genuinely different options.
- [ ] The finalist grid samples one slice per cell and can miss features thinner
      than its pitch. It only orders candidates that already passed the
      full-resolution envelope check, but that limit should be measured.
- [ ] `_attach_assessment` materialises the whole transformed mesh (~432 MB at
      6M triangles) because the native `Rasterizer` takes one array. Measured
      peak RSS stayed at 1.15 GiB against a 32 GiB default budget, but the
      allocation is not budget-checked and `auto_placement` takes no budget.

## Stage 2 — supports, STL export, independent post-check
- [ ] No tiled / disk-backed layer records; full-resolution validation holds one
      layer plus label images in RAM (~500 MB at 8520x4320).
- [ ] Correction passes are not yet exercised against a case that needs them;
      only the auto-disabled stopping path is covered.
- [ ] `support_anchor_load` warns rather than fails because a share of area is
      not a strength result. It stays a warning until pillar mechanics are
      calibrated.
- [ ] Bracing remains a stiffness heuristic keyed to the slenderness limit, with
      no independent check that a brace helps.

## Stage 3 — GUI
- [ ] Implemented. The real-render test now runs in a subprocess under
      `xvfb-run`; without it the fatal X error killed the whole session.
- [x] Full-resolution latch load/support/Preview → Layers is covered under
      real VTK in a fresh process.
- [ ] Full-resolution skull/temporal-bone originals still need editor coverage.

## Stage 4 — GOO
- [ ] Implemented and verified against the immutable reference by decode.
      Antialiasing beyond binary masks is not implemented.
- [ ] Machine `motion` fields, tilt semantics, physical orientation and print
      duration remain unverified. `PrintTime` stays zero.

## Stage 5 — printer
- [x] Authorized read-only discovery, fresh status, attributes and file listing
      on 2026-09-08; `reports/plan2/printer-readonly.json` records idle state.
      No upload, print, motion, camera or settings commands were sent.
- [ ] Resolve optional text-ping timeout and undocumented `XYZsize` discrepancy
      before relying on those fields. Keep the current printer profile.
- [ ] Physical printing, upload/control and calibration acceptance remain open;
      the current authorization explicitly forbids printing.

## Verification debt
- [ ] Full-resolution single-pass prepare is now measured on the float-valve
      inputs; establish isolated-process baselines for the seven original skull/
      temporal-bone inputs and all correction passes. Existing auto-placement
      baseline: 7.9 s, 1.15 GiB per temporal bone.
- [ ] The four open skull surfaces have no repair that closes their cut faces,
      so they cannot yet reach a printable state.

## Findings recorded 2026-09-07
- The rasterizer punched a full-row hole through solid material wherever a
  shared triangulation edge crossing landed on a sample row centre. A sealed
  cavity therefore read as drained. Fixed by canonical edge interpolation;
  regression covered by mesh-level cavity fixtures.
- `analyze_drainage` discarded `odd_rows`, so an open mesh silently produced a
  holed occupancy grid and a false drainage pass. It now reports `not_run`.
  This is the normal path for four of the seven originals.
- A fully enclosed cavity used to pass the drainage check, contradicting the
  layer `enclosed_voids` result. `drainage_check` now fails on it.
- A supported assembly fails drainage on its own supports: the pillar/raft
  crevice is a genuine ~0.02 mm3 pocket. `repair.min_void_volume_mm3` now
  applies to bottlenecks too, still defaulting to zero.
- Only the two temporal bones fit the Mars 5 Ultra. The slab overflows by
  51.35 mm and each quadrant by ~13.6 mm; these are correct
  `no_feasible_placement` results, not search failures.
