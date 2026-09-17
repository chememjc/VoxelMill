# nextsteps.md — closing the gap on Lychee and CHITUBOX, and going past them

Review update (2026-09-10): Relocatable AppImage (CLI-only and full editor;
double-click opens `gui` when PySide6 is bundled). Per-object `overrides.support`
on added models; contour and open-boundary contact sampling. Occupancy treats
any nonzero sample as material so CTB 7-bit greys do not punch holes. Editor
operation dumps skip keys a selected `--support-preset` overlays. Physical
printing remains FEP-blocked. `todo.md` is the current restart ledger.

Review update (2026-09-09, later): Editor STL open no longer crashes on
`extra_models=None`. Multi-object pose controls, portable extra meshes, File >
Quit, CUDA auto/CPU fallback, unencrypted CTB v3 read/write/verify/convert/
GUI scrub and `slice` to `.ctb` are in. N13 coarsens derived voxel pitch within
the deviation ceiling. N15 adds `repair.weld_tolerance_mm` (cap 0.05 mm). C8
`voxelmill cap` fills near-planar open loops. Encrypted CTB and v4/v5 stay
rejected. `todo.md` is the current restart ledger.

Review update (2026-09-09): Grok's baseline reproduces at 651 passed and
10 skipped; all nine original-mesh sample tests pass. D1 tree supports now
respect branch slope and full capsule clearance and export the actual tree
graph. D2/D3, process presets, booleans, closed-solid trimming, XY compensation,
STEP import, HTML reports and STL count recovery are implemented. N13 has
derived-pitch headroom but still no budget-driven coarsening. Print-time
estimates are uncalibrated; autosave recovery is under review. `todo.md` is
the current restart ledger. The current user request promotes CTB alongside
the other offline work and requires automatic CUDA selection when available.

Tier 2.2 update (2026-09-08): C10 ranked candidate reports and CLI/editor
selection are implemented; physical weight fitting remains open. C5 now adds
an independent, uncalibrated surface peel advisory to STL validation and
exports. Read-only printer discovery/status/attributes/listing passed; nothing
was printed. See `todo.md` for current verification and remaining work.

Review update (2026-09-10): the SDCP adapter now exposes read-only telemetry,
ordered print-history retrieval (Cmd 320/321), printer-supplied time-lapse
downloads, and the Cmd 386 RTSP URL. Evidence is recorded in
`reports/plan2/printer-monitor-2026-09-10.json`; this proves connectivity and
protocol responses only. Release-film fields remain telemetry and do not prove
FEP health, and no print, upload, motion, or settings command was issued.

Implementation update (2026-09-08): Tier 2.1 now includes parametric tips,
pillars, eight base strategies — `plate`, `none`, `pad`, `skate`, `skeleton`,
`grid`, `hex`, and `triangle` — optionally tapered inward from the plate, adjustable brace
geometry and collision rejection.
Dedicated printer/resin/support editors and portable saves extend A1/A2/D7;
the live example uses the same router as CLI `support-example`. Part-to-part
supports can be forbidden or scored with adjustable avoidance. `D4`'s porous
bases are implemented for the square lattice, the honeycomb and the
spanning-tree skeleton, with sloped edges. Measured on the
float-valve nut, the porous strategies use 51% to 70% less base resin than the
slab at identical support routing (`docs/examples.md`). This does not complete
D3's per-contact shapes/batch edits. The user has prioritized the Mars 5
Ultra; B3's broader printer database remains deferred.
New scoped items: D8 (part-to-part policy, difficulty 24 / importance 80) and D9
(global support parameter editor with rendered example, 30 / 78), implemented
in Tier 2.1. Scores are planning judgments, not measurements. `todo.md` records
current verification; physical calibration and prior Tier 0 gates remain open.

Tier 2.1 geometry is implemented: ordinary model anchors have independent
cone/cylinder bottoms and whole short model connectors have independent buried
end depths. The existing thin-middle class remains a separate mode. The
CHITUBOX preset's angle convention is corrected (70° from vertical becomes
20° from horizontal); its remaining reference gaps are listed in portable
notes and `docs/support-presets.md`. This completes the planned configurable
geometry, not reconstruction of missing measurements or physical calibration.

Implementation update (2026-09-07): `plan2.md` now controls sequencing. Tier 0.2
has a tested green front (-Y) build-volume edge. Tier 0.1 has shared exact/raster
ingestion, grouped occupancy, streamed soup export and reopened-STL parity;
real-part acceptance remains open because support routing/coverage and drainage
still fail. The proposed interior-face filter did not resolve routing, so it
remains a measured experiment rather than an enabled fix. See `todo.md` for
restart commands and evidence. Other tiers and their scores remain unchanged.

Scope decisions that shaped this document (user, 2026-09-07):

- **Multi-part, manual layout only.** Several parts on a plate with independent
  transforms and per-object overrides. No automatic nesting/packing solver.
- **GOO + CTB.** Elegoo native plus the Chitu-board format that covers most of
  the rest of the consumer MSLA market. Not the full 50-format zoo.
- **Personal now, public-ready later.** Build so a release needs no rewrite —
  stable schema, stable CLI, no hard-coded paths — but skip community
  infrastructure (cloud libraries, profile marketplaces, submission workflows).
- **CUDA is allowed, a CPU fallback is mandatory.** Every accelerated path must
  have a working CPU path. GPU is an optional dependency, never a requirement.
- **Hollowing is wanted *in addition to* void filling**, not instead of it:
  shell offsetting, drain/vent holes and infill lattices, with the existing
  seal-and-validate behaviour kept as a separate, still-default mode.
- **Not miniatures.** Engineering parts and complex meshes from medical
  imaging, printed as functional objects. Scaling is essentially off the table.
  Model cutting with alignment keys is low priority; **planar trimming and a
  boolean AND against an arbitrary bounding-box STL are wanted.**

## How to read the scores

Every item carries two independent 0–100 ranks.

**Difficulty** — implementation cost in this codebase as it stands.
`0` is a one-or-two-line change. `25` is a self-contained module with tests.
`50` is a new subsystem that touches existing contracts. `75` means reworking a
core representation that many modules depend on. `100` is a full rewrite.
The score is calibrated against *this* repository — items are cheaper here when
`voxelmill` already owns the hard part (Manifold booleans, an exact rasterizer, a
validation harness) and more expensive when a shared contract has to change.

**Importance** — how necessary it is for a functional resin slicer, *for this
user's stated workload*. `0` is irrelevant. `50` means a serious user will
notice it missing. `75` means the tool is materially handicapped without it.
`100` means the program does not do its job at all. Engineering-part and
medical-mesh use cases are weighted deliberately: dimensional compensation
scores far higher here than it would for a miniatures tool, and support
aesthetics score lower.

Scores are judgements, not measurements. Where a score is unobvious the detail
section says why.

## Research basis and its limits

Feature inventories were compiled from Mango3D's own documentation and
changelogs, `docs.chitubox.com`, and the vendors' comparison pages, cross-checked
against independent reviews. Complaint research drew on GitHub (notably the
UVtools issue tracker), vendor forums, trade press and comparison writeups.

Three caveats that affect specific claims below:

1. **Reddit was unreachable during research.** No `r/resinprinting`,
   `r/ElegooMars` or `r/AnycubicPhoton` sentiment could be sampled directly.
   Complaint frequencies are directional, not statistical, and one Reddit poll
   result is quoted secondhand through trade press.
2. **Both vendors restructured recently.** CHITUBOX merged Basic and Pro into
   one app with Basic/Advanced/Pro tiers (~Sept 2025); the old `chitubox-pro`
   docs tree is marked deprecated. Lychee renamed Free/Pro/Premium to
   Lite/Plus/Library, and the current line is **7.x** (latest 7.6.5, May 2026) —
   there is no Lychee 8. Tier boundaries below are reconstructed from press
   coverage of the vendors' own announcements, because both live comparison
   pages are JavaScript-rendered and could not be fetched.
3. Some third-party sources are SEO content mills with fabricated-sounding
   precision. Numeric claims from those are excluded; only the qualitative
   complaint underneath is retained, and only where independently corroborated.

---

# 1. What voxelmill already does that neither Lychee nor CHITUBOX does

This section exists because the gap list below is long, and it would be easy to
read it as "voxelmill is behind." On the axes that matter most for functional
parts, it is not. These are advantages to protect, and every one of them is at
risk from a careless implementation of something in section 3.

### 1.1 Analysis is part of slicing, not a second tool

Every slicer researched — CHITUBOX, Lychee, and PrusaSlicer's SLA path — treats
"slice, done" as the finish line. Island detection, resin-trap detection,
suction-cup detection and per-layer repair are pushed into **UVtools**, a
separate application that operates on the already-written printer file and has
no access to the source mesh.

voxelmill does island, enclosed-void, drainage-bottleneck and layer-connectivity
analysis *natively, at printer pitch, as part of preparation*, with the mesh
still in hand (`validation.py: analyze_layers`, `analyze_drainage`,
`VoidForest`, `drainage_clearance`, `gravity_drained`). That is structurally
ahead of both commercial products. UVtools exists because they left the hole.

### 1.2 Independent re-verification before publishing an output

`prepare` writes a staging STL, **reopens it, and independently reslices it**
before the destination file is replaced. `slice` writes a GOO, **decodes every
layer back and compares pixels and per-layer timing against a freshly sliced
source** before the final rename (`goo.py: slice_stl`, `_verify_header_settings`,
`_expected_record`). A pre-existing destination survives a failed run.

No researched competitor does this. The closest analogue in the ecosystem is a
user manually opening the output in UVtools afterwards. Combined with 1.1, this
is the single strongest thing this program has.

### 1.3 A failed answer is a first-class result

Exit code `2` means "the command ran and the answer is no" — validation failed,
or the export was withheld. The report is still written, with every diagnostic.
Only exit `3` means the question went unanswered.

The most-repeated qualitative complaint about CHITUBOX in the entire research
pass is that it does not tell you *why* something failed — "it assumes you
already know." Lychee's advantage over it, per the same sources, is almost
entirely that Lychee complains before slicing. voxelmill's structured
`VoxelMillError` codes and diagnostic lists are further along that axis than either.

### 1.4 Profiles are already plain text

`.ptr` and `.res` are versioned TOML, in the repository, diffable, `git`-able,
editable in `vi`. Meanwhile:

- CHITUBOX splits profile export by product line — `.cfg` (Pro), `.cfgx`
  (Basic), `.cfgd` (Dental) — and moving a profile between them requires
  exporting, importing into a third product, re-exporting, and **manually
  renaming the file extension**. Import fails silently if the destination has no
  matching printer preset.
- Lychee's resin profiles are `.lyr` blobs whose persistence is mediated by an
  **account and cloud sync** (up to 10 backed-up versions), not a local file the
  user controls.
- One vendor-adjacent help article's actual published advice for moving settings
  from CHITUBOX to Lychee is to **screenshot the settings and retype them.**

No source anywhere describes either vendor's profiles as diffable text. This is
already a win; section 3.A is about making it a decisive one.

### 1.5 CLI-first with total settings reach

`--set SECTION.KEY=VALUE` can override *any* resolved setting, and `voxelmill
profile` prints the fully resolved stack including the interpolated exposure
schedule without touching a mesh. Neither competitor has a CLI at all. CHITUBOX
has ChituAction, a GUI-configured macro recorder; Lychee has a Batch tool with a
fixed step list. Neither is scriptable, neither is headless, neither can be run
from CI or a Makefile.

The open PrusaSlicer feature request for resin post-processing scripts (#14541)
shows the demand is unmet even at the FDM-leading vendor.

### 1.6 It refuses to lie to make a run succeed

`no_feasible_placement` is reported when the finite search found nothing — and
the docs say exactly that, rather than implying no orientation exists. Scale is
never reduced to fit. Skipped checks report `not_run` rather than passing.
Uncalibrated numbers are labelled uncalibrated in `docs/calibration.md`, and
`PrintTime` stays `0` rather than inventing an estimate.

Contrast: CHITUBOX's own documentation concedes its resin volume is a
"theoretical value," and its print-time estimate needs a manual user-tuned fudge
factor on high-resolution printers. UVtools issues #983 (resin volume multiplied
by 1000), #151 (print time not recalculated after an exposure edit) and #449
(incorrect material cost) are real, numbered defects of exactly the class this
program's discipline is designed to prevent.

### 1.7 Correctness work already done that the competition demonstrably gets wrong

- **Exact self-intersection predicates** via CGAL in `inspect`, over
  full-resolution originals. CHITUBOX crashes on a 1.5 GB / 32 M-triangle STL
  per forum reports; both vendors' repair is described as failing on
  self-intersecting polygons and flipped normals.
- **The canonical-edge rasterizer fix** (`gotchas.md`): a shared triangulation
  edge whose crossing landed exactly on a sample row centre was dropped by both
  adjacent triangles, punching a full row through solid material and turning a
  sealed cavity into an apparent drained chamber. That is precisely the class of
  bug that produces "the software writes blank layers on non-manifold geometry"
  complaints.
- **`analyze_drainage` reports `not_run` on an unclosed grid** instead of a
  confident false pass — which matters because four of the seven supplied
  originals are open surfaces, i.e. this was the normal path, not an edge case.

### 1.8 No account, no telemetry, no subscription, Linux-native

CHITUBOX Pro **requires online account verification** and binds licences to 2
(personal) or 5 (team) devices; an offline build has been promised but not
shipped. Its Linux build is reported as second-class — the download page has
been observed serving the Windows executable even with Linux selected, and
Gentoo/WineHQ threads exist because people had to fight it. Lychee's free tier
carries ads and a pre-slice wait timer.

Then there is the 2021 precedent: ChiTu encrypted 4K+ board firmware so affected
printers could only be sliced by CHITUBOX. A cited Reddit poll put ~90% of
respondents off buying a ChiTu-based printer under those terms, and a petition
gathered 860+ signatures. UVtools' maintainer was offered CHITUBOX's SDK and
**declined it because it was closed-source**, reverse-engineering `.ctb` instead.

voxelmill is offline by construction, and section 3.H should keep it that way.

### 1.9 Things voxelmill has that are simply absent upstream

| Capability | Lychee | CHITUBOX | voxelmill |
| --- | --- | --- | --- |
| Reslice-and-verify the exported STL before publishing | no | no | **yes** |
| Decode-and-compare the exported printer file before renaming | no | no | **yes** |
| Per-layer void/drainage analysis during preparation | partial (suction-cup detector, Pro-gated) | partial (cavity detection) | **yes, native** |
| Plain-text, diffable, `vi`-editable profiles | no (`.lyr` + cloud) | no (`.cfg`/`.cfgx`/`.cfgd`) | **yes (TOML)** |
| Any CLI at all | no | no | **yes** |
| Structured machine-readable failure reports | no | no | **yes** |
| Exact self-intersection predicates | no | no | **yes (CGAL)** |
| Explicit "this number is uncalibrated" labelling | no | no | **yes** |
| Runs fully offline with no account | yes | **no** | yes |
| Refuses to auto-scale a part to make it fit | n/a | n/a | **yes, by policy** |

---

# 2. Direct answer: what they do that voxelmill does not

The short version, before the ranked list.

**Both have and voxelmill lacks:** multiple parts on a plate; hollowing with
infill; drain/vent hole tools; boolean operations; planar cutting; mesh repair
UI with per-triangle editing; a printer database; more than one output format;
greyscale anti-aliasing; resin volume, weight and cost estimates; a real print
time estimate; support types beyond a straight pillar; paint-on support control;
raft variety; per-Z-band parameter overrides; XY shrinkage and tolerance
compensation; a settings UI that is not a raw JSON box; network transfer to the
printer from the GUI.

**Lychee has and voxelmill lacks:** the Magic one-click pipeline; island
detection at four fidelity levels with a guided resolution workflow; suction-cup
detection; support painting; a support parameter "relative calculator";
projection/grid/inline supports; ten raft types; a community resin profile
library with print-history feedback; theme editor and 77+ remappable shortcuts;
a batch tool; cloud settings backup.

**CHITUBOX has and voxelmill lacks:** eleven named support types; TSMC two-stage
motion control with defined semantics; multi-parameter slicing by model, height
band, or cross-sectional area; mask/LED uniformity compensation; per-pixel layer
image editing; shrinkage, tolerance and print-time compensation; cut-with-keys;
text labels; the Resin Material Alliance library; ChituAction automation; a
post-slice plugin protocol; a companion LAN printer manager with camera.

**Neither has, and voxelmill should not build:** adaptive layer height (not
meaningful for MSLA), multi-material, sculpting/deformation tools. Two features
worth noting as *absent from both*, i.e. green field: neither exposes a numeric
orientation score with ranked candidates, and neither publishes a
compatibility-condition expression language for resin/printer pairing.

---

# 3. Master ranked list

97 items — the original 77 plus twenty (`N1`–`N20`) folded in below from the
plan2 blocker investigation. **Diff** = difficulty out of 100, **Imp** =
importance out of 100, both defined in "How to read the scores" above. Sorted
within each group by importance descending. Every ID links to a detail
subsection below.

## A — Printer and resin profile management

| ID | Item | Diff | Imp |
| --- | --- | ---: | ---: |
| A2 | Resin profile manager; resin process bound to a printer; clone/edit via CLI and GUI | 24 | 84 |
| A1 | Printer profile manager: `list`/`show`/`new`/`clone`/`edit`/`rm`/`validate` | 22 | 82 |
| A3 | Profile search paths: bundled read-only system profiles + XDG user directory | 12 | 70 |
| A7 | GUI profile manager mirroring the CLI exactly, with dirty-state save/discard | 30 | 62 |
| A4 | `inherits` with delta storage, explicit `schema_version`, flatten-on-load | 30 | 55 |
| A5 | Compatibility conditions gating which resins are valid for which printers | 26 | 48 |
| A6 | `profile diff`, and per-key provenance in resolved output | 15 | 45 |
| A8 | Named support/process presets (both portable preset types implemented; profile embedding pending) | 14 | 44 |
| A10 | Per-object and per-Z-band setting overrides | 42 | 50 |
| A9 | Import CHITUBOX `.cfg`/`.cfgx` and Lychee `.lyr` profiles | 34 | 20 |

## B — Output formats and printer coverage

| ID | Item | Diff | Imp |
| --- | --- | ---: | ---: |
| B5 | Print-time estimation (configured schedule implemented; physical calibration pending) | 26 | 76 |
| B6 | Resin volume, weight and cost estimation | 18 | 72 |
| B1 | CTB writer (unencrypted v3 implemented; encrypted variants rejected) | 62 | 70 |
| B3 | Printer database covering the Elegoo line plus a generic-MSLA template | 16 | 62 |
| B4 | Greyscale anti-aliasing in the output, with optional blur | 38 | 58 |
| B2 | CTB reader and decode-verifier (unencrypted v3 implemented; v4/v5 rejected) | 34 | 55 |
| B8 | 3MF / OBJ / PLY import (STEP later) | 30 | 46 |
| B7 | `convert` command between supported printer formats (GOO v3 ↔ unencrypted CTB v3) | 20 | 30 |

## C — Geometry and model preparation

| ID | Item | Diff | Imp |
| --- | --- | ---: | ---: |
| C8 | Close the open cut faces on the four skull surfaces (`voxelmill cap` for near-planar loops; q00 now has a marked real-part refusal test) | 40 | 72 |
| C1 | Multi-part scene with manual layout and per-object transforms | 55 | 66 |
| C2 | Hollowing by shell offset, alongside the existing seal-and-fill mode | 48 | 62 |
| C4 | Drain and vent hole tool, manual placement plus auto-paired placement | 40 | 60 |
| C9 | Wall-thickness and minimum-feature analysis with a heatmap | 34 | 60 |
| C10 | Orientation search: expose ranked scored candidates; calibrate the weights | 34 | 58 |
| C5 | Suction-cup and trapped-volume risk with a peel-force model | 44 | 58 |
| C6 | Boolean operations, including intersect against a bounding-box STL | 18 | 55 |
| C7 | Planar trimming with an arbitrary cut plane | 20 | 50 |
| C3 | Infill lattices for hollowed parts (gyroid / honeycomb / scaffold) | 46 | 42 |
| C12 | Measure, mirror and per-axis scale, with scaling guardrails | 16 | 38 |
| C11 | Text and serial-number embossing/engraving for part marking | 30 | 26 |

## D — Supports

| ID | Item | Diff | Imp |
| --- | --- | ---: | ---: |
| D5 | Re-run island detection automatically after every edit; persistent warning badge | 18 | 70 |
| D3 | Full manual support editor: per-support parameters, batch edit, tip shapes — implemented | 44 | 66 |
| D2 | Paint-on support enforcers and blockers — implemented (manual only) | 40 | 62 |
| D6 | Support mechanics calibration; promote anchor-load from warning to failure | 55 | 58 |
| D1 | Support type library: branch, tree, joint, contour, face, boundary | 52 | 55 |
| D4 | Raft type library — eight strategies implemented; adhesion calibration pending | 24 | 48 |
| D7 | Support presets as portable profile data — implemented (`presets.py`, CLI/GUI) | 12 | 40 |
| D8 | Part-to-part support permission and adjustable avoidance — implemented in Tier 2.1 | 24 | 80 |
| D9 | Global support parameter editor with rendered attachment example — implemented in Tier 2.1 | 30 | 78 |

## E — Slicing and exposure

| ID | Item | Diff | Imp |
| --- | --- | ---: | ---: |
| E4 | XY shrinkage and tolerance compensation (per-layer offset) | 26 | 78 |
| E7 | TSMC semantics: define, validate and document all 18 motion fields | 30 | 66 |
| E5 | First-layer over-cure / elephant-foot compensation | 22 | 56 |
| E8 | Exposure calibration test generator (matrix / RERF-equivalent) | 30 | 52 |
| E2 | Per-Z-band and per-object slice parameter overrides | 34 | 44 |
| E6 | LED uniformity mask compensation | 34 | 34 |
| E3 | Cross-sectional-area-driven exposure adjustment | 30 | 30 |

## F — Performance

| ID | Item | Diff | Imp |
| --- | --- | ---: | ---: |
| F2 | RLE / sparse layer representation end to end, replacing dense bitmaps | 52 | 68 |
| F3 | Z-interval tree over triangles; only test the active set per layer | 28 | 64 |
| F1 | Multithread across layers | 24 | 62 |
| F4 | Incremental re-slice after a local edit | 40 | 54 |
| F6 | Disk-backed, tiled layer records (existing stage-2 debt) | 30 | 46 |
| F10 | Benchmark harness and performance regression gates | 16 | 44 |
| F7 | GPU orientation search — CUDA optional, CPU fallback mandatory | 44 | 34 |
| F5 | SIMD in the rasterizer inner loop | 26 | 32 |
| F8 | GPU rasterization via stencil buffer or compute shader | 58 | 26 |
| F9 | GPU signed-distance-field voxel repair (NanoVDB) | 62 | 22 |

`F10` is implemented as `scripts/benchmark.py`, which runs each scenario in a
fresh process reporting its own `RUSAGE_SELF` peak and gates regressions
against a stored baseline.

## G — GUI

| ID | Item | Diff | Imp |
| --- | --- | ---: | ---: |
| G3 | Generated typed settings pages, retiring the raw JSON override box | 40 | 70 |
| G4 | Per-setting tooltips with the config key, modified markers, revert arrows | 22 | 62 |
| G1 | Simple / Advanced / Expert visibility tiers plus risk colouring | 30 | 58 |
| G10 | Autosave and crash recovery | 22 | 58 |
| G2 | Fuzzy, mode-aware settings search | 20 | 56 |
| G13 | Direct-manipulation gizmos for supports, holes and cut planes | 40 | 54 |
| G5 | Object list / scene tree panel | 28 | 50 |
| G12 | Layer viewer upgrades: overlays, pixel inspection, A/B layer diff | 26 | 48 |
| G7 | Unified notification framework with per-category suppression | 20 | 44 |
| G9 | First-run configuration wizard | 18 | 42 |
| G6 | Named undo/redo history with jump-to-state | 22 | 40 |
| G8 | Theme support and a full keyboard shortcut editor | 22 | 34 |
| G11 | Dockable, resizable panels with persisted layout | 18 | 30 |

## H — Automation, integration, distribution

| ID | Item | Diff | Imp |
| --- | --- | ---: | ---: |
| H4 | SDCP: finish the offline simulator, then hardware acceptance | 30 | 50 |
| H1 | Batch mode: N models × M profiles, headless | 20 | 46 |
| H3 | Versioned report schema, published as JSON Schema | 14 | 40 |
| H5 | Packaging: PyPI wheel plus AppImage or Flatpak | 26 | 36 |
| H2 | Post-slice hook / plugin protocol | 18 | 28 |
| H6 | Shell completion and a man page | 8 | 18 |

## I — Analysis and calibration

| ID | Item | Diff | Imp |
| --- | --- | ---: | ---: |
| I1 | Persist and replay analysis artifacts without a full re-slice | 26 | 52 |
| I2 | Resin density and cost fields in the resin profile | 8 | 40 |
| I3 | Print-time auto-calibration from measured prints | 22 | 36 |
| I4 | Render the JSON report as a readable HTML page | 14 | 26 |

## N — Added during the plan2 blocker investigation

These twenty surfaced from running voxelmill against real parts during the
plan2 blocker investigation, not from the Lychee/CHITUBOX competitor
comparison that grounds groups A–I. They stay at the master-list level only —
plan2.md's own writeup for each item stands in for a detail subsection.

| ID | Item | Diff | Imp | Status |
| --- | --- | ---: | ---: | --- |
| N1 | Raster-domain union fallback for unrepresentable meshes | 48 | 88 | done (`src/voxelmill/assembly.py`) |
| N12 | Resolve the GOO mirroring question against both references | 16 | 78 | open (measured against both references and inconclusive; the uncertainty now ships as a per-export warning instead) |
| N2 | `voxelmill verify` — standalone post-generation `.goo` check | 26 | 74 | done |
| N3 | Always open a model; clip to build volume; red out-of-bounds | 30 | 70 | done (`assembly.clip_to_build_volume`, off by default) |
| N4 | GOO layer viewer in the GUI | 28 | 66 | done |
| N20 | Union raster parity check in the re-slice | 22 | 66 | done |
| N17 | Porous base/raft strategies; feet-only mode | 34 | 64 | implemented (Tier 2.1); physical calibration pending |
| N19 | Interior-face filter for downward contact sampling | 26 | 62 | superseded (measurement showed the router's own `tip_length_mm` was rejecting roughly half of every float-valve part's contacts, not interior faces; `support.min_tip_length_mm` fixed it and the filter was never needed) |
| N5 | STEP import via FreeCAD 1.1.3, behind an importer interface | 30 | 58 | implemented (`importers.py`) |
| N18 | De-duplicate `gui/services.py` against `pipeline.py` | 16 | 58 | done |
| N11 | Cache decoded layers; stop rebuilding the Rasterizer per request | 20 | 56 | done (byte-bounded layer LRU plus reusable native indices; backward scrubs reset the sweep) |
| N15 | Tolerant vertex welding for tessellated CAD input | 26 | 52 | implemented (`repair.weld_tolerance_mm`, cap 0.05 mm) |
| N16 | Navigation cube and a real camera controller | 24 | 50 | done |
| N13 | `choose_voxel_size` coarsening and deviation headroom | 14 | 48 | done: derived pitch coarsens up to the deviation ceiling |
| N14 | STL reader recovery: wrong count, trailing garbage, truncation | 22 | 46 | implemented and regression-tested |
| N7 | Give `repair.aggressiveness='none'` real meaning | 8 | 44 | done |
| N10 | Fix `mask_to_image` uint8 wrap on 0/255 frames | 4 | 44 | done |
| N8 | Wrap the escaping `ValueError` from `weld_mesh` | 4 | 36 | done |
| N6 | Build plate edge colouring (red, green front) | 8 | 34 | done |
| N9 | Report the true degenerate-triangle count, not per-chunk | 3 | 28 | done |

## Shipped from groups A-I on 2026-09-07

The master tables in section 3 carry no status column, so completions are
recorded here rather than by reformatting them. Each entry names what was
built, not merely that the ID is closed. `todo.md` holds the working detail.

| ID | Shipped | Note |
| --- | --- | --- |
| A3 | profile discovery | Four-layer search path; a higher layer shadows a lower one by identifier and `profile list` names the shadowed files. `--printer`/`--resin` take an identifier or a path. |
| A1 | printer profile manager | `profile save` writes a self-contained `.ptr` from the resolved stack, then re-reads it and requires it to resolve identically. `list`, `show` and `diff` alongside. |
| A2 | resin profile manager | `resin list/show/bind`; `bind` copies a process block onto another printer id verbatim and says in the payload that a carried exposure is a starting point, not a calibration. |
| A6 | `profile diff` and provenance | Provenance re-runs `resolve_settings` with one more layer each time, so it cannot drift from the settings it describes. |
| I2 | resin density and cost | `density_g_cm3`, `cost_per_litre`, `currency`; 0 means not supplied and the derived figures are null rather than guessed. |
| B6 | volume, weight and cost | Derived from the raster volume, which already counts supports and any base. Also fills the GOO header's `material_grams`/`material_cost`/`price_currency`, previously hard zeros. |
| E5 | elephant-foot compensation | A per-axis erosion radius ramping to zero over the bottom layers, applied in the write **and** verification passes so `decoded_pixels` still proves the file matches the intended exposure. |
| D5 | island re-check and badge | `voxelmill islands` and a persistent editor badge on the same `island_summary`; the badge goes stale on any edit and is never an export gate. |
| C12 | measure, mirror, scale | Guardrailed per-axis scale, mirroring with the winding reversal it requires, and a `measure` command that also solves `--target-mm` for the factor that reaches a size. |
| H1 | batch mode | `voxelmill batch` over many inputs with a per-item manifest; forwarding is derived from the overrides dictionary rather than an enumerated flag list. |
| H6 | completion and man page | Generated from the live parser. Generating the man page exposed 27 flags with no help text, all now written. |
| G4 | tooltips, markers, revert | Every compact Setup control names its config key and CLI flag; a dot marks values changed from the resolved profile and a revert button restores one. |
| D7, A8 (support half) | support presets | Shipped earlier the same day; named *process* presets and presets embedded in profiles remain open. |

`B3` (Elegoo printer database) stays open deliberately: it needs real panel
resolutions, pixel pitches and build volumes per machine, and inventing them
would put fabricated specs behind a name a user trusts. The library that would
hold them now exists, so adding verified machines is a data change.

---

# 4. Detail — A: profile management

This group is first because it is the explicitly requested headline, and because
it is where both competitors are weakest. The research supports that judgement:
profile portability pain is a chronic, verified complaint against both, and no
source anywhere describes either vendor's profiles as diffable text.

The current state is a good foundation and an incomplete product. `config.py`
already has a strict schema with `DEFAULTS`, a versioned TOML loader rejecting
unknown keys, and a documented precedence chain
(defaults → printer → matching resin process → explicit CLI). Resin profiles
already bind process blocks to a printer id via
`[processes.<printer-id>.process]`. What does not exist is any way to *manage*
profiles: no create, no clone, no edit, no list, no validate, no diff, and no
discovery outside an explicit `--printer PATH` on the command line.

### A1 — Printer profile manager · Diff 22 · Imp 82

Add a `voxelmill printer` command group:

```
voxelmill printer list                       # every discoverable printer profile
voxelmill printer show mars5-ultra           # resolved contents, with provenance
voxelmill printer new my-saturn --from mars5-ultra
voxelmill printer clone mars5-ultra mars5-ultra-tuned
voxelmill printer set mars5-ultra-tuned printer.pixels='[8520,4320]'
voxelmill printer edit mars5-ultra-tuned     # opens $EDITOR, validates on save
voxelmill printer rm mars5-ultra-tuned
voxelmill printer validate path/to/x.ptr     # schema check, exit 3 on failure
voxelmill printer path mars5-ultra           # print the file path, for scripting
```

`edit` is the item that makes the whole thing feel right: it writes the TOML to
a temp file, runs `$EDITOR` (or `$VISUAL`, falling back to `vi`), validates on
save, and **refuses to install an invalid profile while keeping the user's edit
buffer** so nothing is lost to a typo. That is a small feature with a large
effect on whether people trust the tool with their settings.

Difficulty is low because `config.py` already owns validation; this is a CLI
surface plus a small store module. Importance is high because without it,
"managing profiles" means knowing the TOML schema by heart and copying files by
hand — which is the actual current state.

Files: new `src/voxelmill/profiles.py` (store, discovery, mutation), `cli.py`
(command group), `config.py` (expose a public `validate_profile_document` that
checks a `.ptr`/`.res` *before* merge, not just the merged result).

### A2 — Resin profile manager, bound to printers · Diff 24 · Imp 84

The mirror of A1 for `.res`, plus the part that the user called out as the thing
both competitors get wrong: **each resin's process settings are tied to a
specific printer, and that relationship must be first-class and manipulable.**

```
voxelmill resin list [--printer mars5-ultra]     # filter to resins with a process for it
voxelmill resin show sunlu-abs-like-gray --printer mars5-ultra
voxelmill resin new siraya-blu --from sunlu-abs-like-gray
voxelmill resin clone sunlu-abs-like-gray sunlu-abs-tuned
voxelmill resin bind sunlu-abs-like-gray --printer saturn4-ultra --from mars5-ultra
voxelmill resin unbind sunlu-abs-like-gray --printer saturn4-ultra
voxelmill resin set sunlu-abs-like-gray --printer mars5-ultra process.normal_exposure_s=3.2
voxelmill resin edit sunlu-abs-like-gray
voxelmill resin rm sunlu-abs-tuned
```

`resin bind --from` is the operation that matters and that neither competitor
offers cleanly: copy an existing, working process block for printer X onto
printer Y as a starting point, so retuning a known resin for a second machine
starts from the calibrated numbers rather than from defaults. The schema already
supports arbitrarily many `[processes.<printer-id>]` blocks — nothing but the
command surface is missing.

Two schema additions belong here:

- `[resin] density_g_ml` and `[resin] price_per_l` (see I2), because volume and
  cost estimates are worthless without them and CHITUBOX's own cost estimate is
  only as good as two numbers the user has to remember to type.
- `[processes.<printer>.notes]` — a free-text field. Klipper's `printer.cfg`
  culture is built on the fact that **you can write down why a value is what it
  is, next to the value.** TOML comments already survive a hand edit but are
  lost the moment the program rewrites the file, so a real field is needed for
  anything the tooling round-trips.

### A3 — Profile discovery and the system/user split · Diff 12 · Imp 70

Right now a profile is only found by an explicit path. Add a search order:

1. explicit `--printer` / `--resin` path (wins)
2. `$VOXELMILL_PROFILE_PATH` (colon-separated, for CI and scratch work)
3. `${XDG_CONFIG_HOME:-~/.config}/voxelmill/{printers,resins}/` — user, writable
4. the packaged `src/voxelmill/data/` bundle — system, read-only

Referencing a profile by **id** (`--printer mars5-ultra`) rather than by path is
what makes A1/A2 usable and makes commands portable between machines. A path is
still accepted, unambiguously, because it starts with `.` or `/` or ends in
`.ptr`/`.res`.

System profiles are read-only; `clone` is the only way to derive from one. This
is PrusaSlicer's system/user split and it prevents the single most-reported
OrcaSlicer profile failure — a user edits a shipped profile, an update
overwrites it, work vanishes.

Note `src/voxelmill/data/` already exists and is empty. This is what it is for.

### A4 — Inheritance with delta storage · Diff 30 · Imp 55

Add an optional `inherits = "<id>"` to both profile types, storing only the
delta from the parent. Two hard-won lessons from the research must be designed
in from the start, not retrofitted:

- **Stamp the schema version in every file and check it on load.** OrcaSlicer
  did not, its JSON grew more verbose across versions, and profiles saved by
  older builds silently fail to load correctly after an upgrade. `voxelmill`
  already has `schema_version = 1` and rejects anything else — keep that
  strictness through every future format change, with an explicit migration
  path rather than best-effort parsing.
- **Decide flatten-on-load vs. live inheritance explicitly, and document it.**
  PrusaSlicer flattens on import: inheritance is an authoring convenience, and
  a later fix to a base profile does *not* propagate to children already
  created. OrcaSlicer keeps the link live, and consequently deleting a parent
  makes every descendant **silently vanish from the picker** with the bytes
  still on disk. Recommendation: **live inheritance, but a missing parent is a
  loud `VoxelMillError`, never a silent disappearance**, and `profile show` always
  prints the resolved chain.

Importance is moderate rather than high: with only a handful of printers, the
copy-and-edit workflow of A1/A2 covers most of the need. This matters when the
printer database of B3 lands and a dozen Elegoo profiles differ by four fields.

### A5 — Compatibility conditions · Diff 26 · Imp 48

PrusaSlicer gates presets with `compatible_printers_condition`, a small boolean
expression evaluated against the selected printer's own config keys. **No resin
slicer publishes an equivalent** — this is open ground.

The resin analogue is real and useful: a process block tuned for a 405 nm LED
at a given pixel pitch is not valid on a machine with a different wavelength or
a tilting vat. Something like:

```toml
[processes.mars5-ultra]
compatible_when = "printer.led_wavelength_nm == 405 and printer.pixel_pitch_mm[0] <= 0.020"
```

Keep the expression language *deliberately tiny* — comparisons, `and`/`or`/`not`,
member access, numeric and string literals. Evaluate it with an explicit AST
walker over a whitelist of node types, never `eval`. An unparseable condition is
a `VoxelMillError`, not a silent pass.

This requires adding descriptive fields to the printer schema
(`led_wavelength_nm`, `vat_type`, `has_tilt`) which are useful independently.

### A6 — `profile diff` and value provenance · Diff 15 · Imp 45

Two related things, both cheap, both disproportionately useful for a
settings-heavy tool:

```
voxelmill profile diff --printer a.ptr --resin r.res \
                      --against-printer b.ptr --against-resin r.res
```

...prints only the keys that differ, with both values. This is the operation
people currently perform by screenshotting one program and retyping into
another.

And `voxelmill profile --provenance` annotates every resolved value with where it
came from: `default`, `printer:mars5-ultra`, `resin:sunlu/mars5-ultra`, or
`cli`. PrusaSlicer's orange "modified" markers and per-field revert arrows are
the GUI expression of the same idea (see G4); the CLI should have it first
because `config.py` already computes the merge and simply discards the origin.

Implementation: thread an origin tag through `_merge` in `config.py` into a
parallel provenance dict. It is a small change to a function that already walks
the whole tree.

### A7 — GUI profile manager · Diff 30 · Imp 62

Everything in A1/A2 must be reachable from the GUI, over the same store module,
with no second code path. Concretely: a profile picker for printer and resin, a
clone button, a rename, a delete with confirmation, an "edit in $EDITOR" escape
hatch, and — the important one — **dirty-state tracking with a save/discard
dialog that lists exactly which keys changed.**

That dialog is PrusaSlicer's "Unsaved Changes" flow, and it is the difference
between a settings UI people trust and one that silently eats work. Include the
"remember my choice" preference; prompt fatigue is a documented complaint about
Prusa's own implementation.

Files: `gui/window.py`, `gui/document.py` (the document already tracks a
settings dict and has undo/redo; profile dirtiness is a sibling concept, and per
PrusaSlicer's design principle should **not** go on the geometry undo stack —
per-field revert is the right affordance for settings).

### A8 — Named presets inside profiles · Diff 14 · Imp 44

Lychee ships Light/Medium/Heavy support presets; CHITUBOX has no named presets
at all, only raw parameters, which reviewers note as a usability gap. voxelmill
currently has one implicit support configuration.

Add named blocks and a selector:

```toml
[processes.mars5-ultra.support_presets.light]
spacing_mm = 5.0
pillar_diameter_mm = 0.9

[processes.mars5-ultra.support_presets.heavy]
spacing_mm = 2.0
pillar_diameter_mm = 1.6
```

with `--support-preset heavy` resolving between the resin process block and CLI
overrides in the existing precedence chain. Cheap, because the precedence
machinery exists; useful, because it turns "retype six numbers" into one flag.

### A9 — Import competitor profiles · Diff 34 · Imp 20

`voxelmill resin import --from-chitubox x.cfgx` and `--from-lychee x.lyr`.

Ranked low deliberately. The formats are undocumented and would need
reverse-engineering; the value is a one-time migration; and the number of
settings that transfer *meaningfully* (exposure, layer height, lift, rest times)
is small enough to retype once. Worth doing only if a large existing profile
library needs to move. Listed for completeness because it is a real feature
delta, not because it should be built soon.

### A10 — Per-object and per-Z-band overrides · Diff 42 · Imp 50

CHITUBOX's Multi-Parameter Slice has Model, Height and Cross-sectional-Area
modes; PrusaSlicer has per-object overrides and height-range modifiers.

The important insight from the research is that **per-object overrides and
profile inheritance are the same mechanism at different scopes** — a scoped
overlay storing only deltas from its parent — and should be implemented once,
not twice. Build A4's delta resolution generically enough that the scope chain
becomes `defaults → printer → resin process → project → object → Z-band → CLI`.

Depends on C1 (a scene with more than one object) and E2 (the slicer honouring
band-scoped parameters). Scored here as the settings-model half of that work.

---

# 5. Detail — B: formats and printer coverage

### B5 — Real print-time estimation · Diff 26 · Imp 76

`PrintTime` is currently written as `0` unless a caller supplies
`--print-time-s`. That is honest — and section 1.6 argues the honesty is a
feature — but a slicer that cannot tell you how long a print takes is missing a
basic function, and the number is *computable* from data already in the profile.

Per layer: exposure + settle-before-exposure + rest-after-exposure +
wait-after-lift + (lift distance ÷ lift speed) + (retract distance ÷ retract
speed), with bottom, transition and normal layers each using their own values,
and the two-stage motion fields (E7) folded in when non-zero.

The competitor bar here is low and the complaints are specific: CHITUBOX's own
docs concede high-resolution printers have data-loading delays that make the
estimate wrong, and expose a **manual** compensation field the user must
discover and tune. UVtools #151 is a print time that does not recalculate after
an exposure edit. So: compute it from the schedule, recompute it on every
parameter change, never cache it, and expose a per-printer
`printer.time_overhead_per_layer_s` constant that I3 can calibrate from real
prints rather than leaving as a fudge factor.

Keep `--print-time-s` as an override, and keep reporting `0` as "unknown" if the
motion fields are themselves unverified — but say so in the report rather than
silently emitting a confident wrong number.

Files: `goo.py` (`header_from_settings`, `_layer_values`), `config.py`.

### B6 — Resin volume, weight and cost · Diff 18 · Imp 72

Sum the exposed pixel area per layer × pixel pitch² × layer height. The
rasterizer already produces exactly the masks this needs, so the marginal cost
is an accumulator in the existing per-layer loop.

Report model volume, support volume and raft volume **separately** — that split
is genuinely useful when deciding whether a support strategy is worth it, and
neither competitor breaks it out. Multiply by `resin.density_g_ml` for weight
and `resin.price_per_l` for cost (I2).

Engineer against the known defect class here. UVtools #983 is a resin volume
multiplied by 1000, #449 is an incorrect material cost after a repair. Both are
unit-conversion bugs. Pick one internal unit (mm³), convert only at the
reporting boundary, and put a unit-round-trip test in `test_goo.py`.

Note the honest framing CHITUBOX's own docs use and match it: this is a
theoretical cured-volume figure that will diverge from bottle consumption
because of drainage, adhesion and volatility. Say so in the report.

### B1 — CTB writer · Diff 62 · Imp 70

The single largest format item, and the reason the difficulty is 62 rather than
35: `.ctb` has multiple versions, and version 4+ from ChiTu-based boards is
**encrypted**. That is the format at the centre of the 2021 controversy, where
ChiTu locked several popular printers to CHITUBOX, a petition gathered 860+
signatures, and UVtools' maintainer refused the offered closed-source SDK and
reverse-engineered read support instead.

Practical consequences for planning:

- UVtools has open-source, actively maintained `.ctb` encode/decode. It is the
  reference implementation, the same way it was used to cross-check the GOO work
  in `docs/goo-format.md`. Pin a revision and compare against it exactly as
  stage 4 did for GOO.
- Scope it as: unencrypted CTB v3 first (widely accepted, much simpler), then
  v4+ encrypted as a separate, later increment.
- The existing GOO architecture generalises well. `goo.py` has a clean
  `GooWriter`/`GooReader` pair with an independent decode-and-compare verifier;
  the right move is to lift a `LayerFormat` interface out of it so CTB is a
  second implementation rather than a parallel universe. Do that refactor
  *before* writing CTB, not after.
- `printer.output_formats` already exists in the schema as a list. It is
  currently `["goo"]` and was clearly designed for this.

Importance 70 rather than 85 because the user's own printer is a Mars 5 Ultra
and GOO already works — this is coverage for other machines, not for today.

### B3 — Printer database · Diff 16 · Imp 62

Ship `.ptr` profiles for the Elegoo Mars and Saturn lines plus a documented
generic-MSLA template, in the packaged `src/voxelmill/data/` (A3). Each needs
build volume, pixel count, pixel pitch, mirroring, layer-height range, output
format, and — flagged clearly as uncalibrated until verified — motion fields.

Low difficulty (it is data entry against a schema that already exists) and high
value: it is the difference between "a program for one printer" and "a program."
The honest-labelling discipline of section 1.6 must extend here — a shipped
profile whose motion values were transcribed from a reference file and never
verified on hardware must say so in a `notes` field, not imply calibration.

Lychee claims 750+ printers, CHITUBOX 30+ brands. Matching that is not the goal;
covering the Elegoo line properly and letting anyone write a profile in `vi` is
a better answer for this project than a database nobody can audit.

### B4 — Greyscale anti-aliasing · Diff 38 · Imp 58

The GOO writer currently emits binary masks. Both competitors do greyscale edge
anti-aliasing: CHITUBOX with a grey-level range plus an optional 2–8 px image
blur, Lychee with an AA mode, radius, grey offset and an HD toggle plus a
separate switch for AA on supports.

CHITUBOX's own documentation concedes the trade-off: more blur means softer
edges but a less sharp image with lost detail. That is because a global blur
radius is the wrong tool. The better implementation — and a genuine
differentiator — is **coverage-based greyscale**: compute each boundary pixel's
actual area coverage during rasterization and emit that as the grey level,
rather than rendering binary and blurring afterwards. The rasterizer in
`native/raster.cpp` already computes exact edge crossings (that is what the
canonical-edge fix was about), so the sub-pixel information is *already there*
and currently thrown away.

Two things must not break: the decode-and-compare verifier has to learn
greyscale tolerance rather than exact pixel equality, and the `analyze_layers`
island/void analysis must keep operating on a **binary** threshold of the mask,
because a greyscale edge is not a connectivity change. Keep them separate.

Also expose an AA-off switch for supports independently, as Lychee does —
softening a support tip contact is usually wrong.

### B2 — CTB reader and verifier · Diff 34 · Imp 55

Non-negotiable companion to B1. The decode-and-compare-before-rename discipline
(section 1.2) is this project's best property and must not be dropped for the
second format. Mirrors `GooReader` and `_verify_header_settings`.

### B8 — 3MF / OBJ / PLY import · Diff 30 · Imp 46

Currently STL only. 3MF matters most — it carries units, transforms and multiple
objects, which is exactly what C1's multi-part scene wants, and it is the format
both competitors accept. OBJ and PLY are cheap additions. STEP is a much larger
undertaking (a real CAD kernel) and is deliberately out of scope; for
engineering parts, exporting STL from the CAD tool is the normal path.

### B7 — `convert` command · Diff 20 · Imp 30

`voxelmill convert in.goo out.ctb --printer target.ptr`. Retarget an existing
sliced file to another machine without re-slicing. This is a large part of why
UVtools exists. Cheap once B1/B2 exist, and low importance because the primary
workflow is slicing from a mesh.

---

# 6. Detail — C: geometry and model preparation

### C8 — Close the open cut faces · Diff 40 · Imp 72

This is existing recorded debt, not a competitive gap, and it outranks most of
the feature list because it blocks the actual workload. Four of the seven
supplied originals are open surfaces. `analyze_drainage` correctly reports
`not_run` on them rather than passing them falsely — which is right, and also
means **those four meshes cannot currently reach a printable state.**

Medical-imaging meshes are exactly this: segmented surfaces with cut boundaries
where the volume was cropped. If the target workload is complex meshes from
imaging, closing an open boundary into a solid is a core operation, not an edge
case.

What is needed: detect boundary loops (`mesh.cpp` already counts boundary
edges), and close them either by planar capping when the loop is near-planar —
the common case for a crop plane — or by a minimal-area triangulation otherwise.
Then verify closure by the existing rasterizer `odd_rows` check, which is
already the ground truth for "is this grid closable."

Interacts with C7: a crop-plane cap and a planar trim are the same geometry
operation viewed from two directions, and should share an implementation.

### C1 — Multi-part scene with manual layout · Diff 55 · Imp 66

Per the scope decision: several parts on a plate, independent transforms,
per-object settings, **no auto-nesting solver.**

This is the largest structural change in the document because single-part is
baked into the contracts. `MeshAsset`, `Placement`, `SupportGraph` and the
`.chop` project format all assume one part, and the pipeline in `pipeline.py`
threads one triangle array through placement, supports, union, export and
validation.

The work: introduce a `Scene` holding an ordered list of placed objects; make
`Placement` per-object; make the support planner run per-object but the
collision field and the validation pass run over the **whole plate** — that
distinction is where naive multi-part implementations get it wrong, because a
support routed for object A must not pass through object B, and island analysis
is a property of the layer, not of an object.

Difficulty 55 rather than 70 because the analysis layer (`validation.py`)
already works on a rasterized plate rather than on objects, so it largely does
not care. The cost is concentrated in `pipeline.py`, `project.py`, the GUI
document, and the `.chop` schema version bump.

Skipping the nesting solver removes a genuinely hard optimization problem and
most of the difficulty. Add a simple grid arrange (`voxelmill arrange --grid`)
as an obvious convenience, not as packing.

### C2 — Hollowing by shell offset · Diff 48 · Imp 62

Per the scope decision this is **additive**: keep `seal_voids` and the
fill-and-validate behaviour as the default, and add hollowing as a separate,
explicitly requested operation with its own settings block.

Implementation is an inward offset of the surface producing an inner shell, then
a boolean difference. Manifold is already a dependency and does the boolean;
the offset is the work. Two viable routes:

- **Voxel route.** Reuse the existing `repair.py` voxel infrastructure —
  occupancy voxelization, erode by wall thickness in voxels, re-mesh the eroded
  boundary, subtract. Robust on the ugly non-manifold input that medical meshes
  produce, which is the deciding factor. Resolution-limited, so the voxel pitch
  must be chosen against the wall thickness and reported.
- **Mesh offset route.** Sharper, faster, and fragile on self-intersecting
  input — which is precisely what four of the seven originals are.

Recommendation: voxel route first, because it composes with the repair path this
codebase already has and because the input meshes are hostile.

Settings: `hollow.enabled`, `hollow.wall_thickness_mm`,
`hollow.voxel_size_mm`, `hollow.mode` (`inner` / `bottom_open`), and crucially
`hollow.min_wall_thickness_mm` as a **validated floor** — for functional parts,
a shell thinner than requested is a structural defect, and the existing
validation discipline should fail rather than warn.

Note that hollowing makes the drainage analysis *more* important, not less: a
hollowed part is by construction an enclosed void until it is drained. The
existing `VoidForest`/`drainage_check` machinery is the right consumer, and this
is where voxelmill's analysis depth pays off against Lychee's Pro-gated
suction-cup detector.

### C4 — Drain and vent holes · Diff 40 · Imp 60

The most-cited failure mode in hollowing complaints. CHITUBOX's automatic
placement is reported to use a naive "lowest point" heuristic that ignores
suction entirely; the resulting single bottom hole with no top vent forms a
vacuum on lift that can tear the FEP or pull the part off the plate. Lychee's
hollow is reported to leave pinholes that need manual holes anyway.

The fix is stated plainly in the research and is worth building properly:
**place holes in pairs — a drain at the gravity-lowest point of each enclosed
void, and a vent at its highest point** — and size them from resin viscosity
rather than a constant. Put viscosity in the resin profile so the recommendation
comes from calibrated data.

voxelmill is unusually well placed to do this well, because `VoidForest` already
identifies enclosed voids as connected components with volumes, and
`gravity_drained` already does a gravity sweep. The hole placer consumes those
results rather than reinventing them.

Manual placement must exist too — click a point, get a hole with diameter,
depth, taper and a through/blind mode — because on an anatomical part the
algorithm will sometimes want to drill through something that matters.

Then re-run drainage validation and **verify the holes actually connect the void
to outside air**, which is exactly what `drainage_clearance` already computes.
CHITUBOX ships a separate "3D drain holes verification" feature; here it is the
existing check.

### C9 — Wall thickness and minimum feature analysis · Diff 34 · Imp 60

Scored higher than it would be for a miniatures tool. For functional engineering
parts, "is this rib thinner than the printer can resolve" is a load-bearing
question, and it is the kind of thing that is cheap to check and expensive to
discover after a four-hour print.

CHITUBOX has no wall-thickness heatmap at all (only a hollow-shell parameter).
Lychee's is referred to in third-party coverage but could not be confirmed in
primary documentation. So this is close to open ground.

Two related outputs:

- **Thickness field**, from the existing `native/distance.cpp` distance work or
  a voxel distance transform, rendered as a heatmap in the viewport and
  summarised in the report as a minimum, a histogram, and a list of regions
  below threshold.
- **Minimum printable feature check** against the printer's actual pixel pitch
  and layer height — a feature narrower than ~2 pixels will not survive, and the
  program knows both numbers.

Report it as a validation check with a configurable threshold, defaulting to
warn rather than fail, since a thin region may be intentional.

### C10 — Orientation search: expose the scoring · Diff 34 · Imp 58

**Neither competitor exposes an orientation score.** CHITUBOX documents its
auto-orient as using the longest surface-point distance as the vertical axis.
Lychee's is a single deterministic pass targeting ~45° faces, documented by
Lychee itself as producing unpredictable results on dense organic models — which
is exactly this user's input class.

voxelmill already ranks finalists on trapped resin, sealed cavities, support
accessibility and a centre-of-mass lever arm (`geometry.py: _rank_finalists`,
`validation.py: orientation_assessment`). The engine is there; three things are
tracked below.

- **Show the ranking — implemented 2026-09-08.** Return the top N candidates with their per-term scores
  and let the user pick, in both CLI (`--rotate auto --candidates 5` printing a
  table) and GUI (a list with a live preview). This turns a black box into a
  tool, and it is a feature no competitor has.
- **Calibrate the weights.** `TRAPPED_WEIGHT`, `CAVITY_WEIGHT`, `ACCESS_WEIGHT`
  and `STABILITY_WEIGHT` are recorded in `todo.md` as uncalibrated guesses. They
  should be fitted against real outcomes, and until then the report should
  continue to say they are guesses.
- **Real support volume per candidate.** Currently a proxy. A genuine routing
  pass per finalist is the accurate answer and is expensive — which makes this
  the strongest GPU candidate in the whole document (F7), because evaluating
  many candidate orientations is embarrassingly parallel across candidates.

The existing 20°-separation finding is worth revisiting too: `todo.md` notes
only two finalists exist for a temporal bone, which suggests the initial spread
is too narrow rather than the refinement being too coarse.

### C5 — Suction cup and trapped-volume risk · Diff 44 · Imp 58

Lychee's Suction Cup Detector is Pro-gated, highlights risk zones, and
explicitly **does not fix them** — the user must add a blocker or a hole
manually. CHITUBOX has cavity detection that serves a similar advisory role.

**Implemented 2026-09-08:** `peel_risk` now reports connected near-horizontal
STL surface regions, areas, physical layer spans and a configured-speed score
in STL validation, reopened exports and STL-to-GOO source validation. Thresholds
and the score are explicitly uncalibrated. Standalone GOO masks cannot provide
this oriented-surface evidence and say so. Physical force, tilt-release and
failure-probability calibration remain open.

voxelmill's `analyze_drainage` and `VoidForest` find enclosed and
poorly-drained volumes. The complementary surface check addresses the *other* suction mode: a large
flat downward-facing area that is not an enclosed void at all, but which forms a
vacuum against the FEP during peel. That is a surface-area and peel-mechanics
question, not a topology question, and it needs a separate check: find
downward-facing regions above an area threshold, weight by depth in the layer
stack and by lift speed, and report a risk score.

Pair it with the existing `drainage_clearance` to distinguish the two failure
modes clearly in the report, because the responses differ: inspect vent paths
for trapped voids, and review orientation and release settings for surface peel
findings. Whether those changes solve a physical failure requires verification.

### C6 — Boolean operations, including intersect-with-STL · Diff 18 · Imp 55

Explicitly requested: **a logical AND against an arbitrary bounding-box STL.**

Manifold is already a dependency, `mesh_to_manifold` and `manifold_triangles`
already exist in `geometry.py`, and `fill_enclosed_cavities` already runs
Manifold operations. Union, difference and intersection are therefore close to
free — this is a CLI surface and a GUI action over machinery that is present.

```
voxelmill boolean model.stl --intersect crop-box.stl --output cropped.stl
voxelmill boolean model.stl --subtract keepout.stl --output out.stl
voxelmill boolean a.stl b.stl --union --output joined.stl
```

Difficulty 18 is the honest number given the engine exists. Importance 55
because it directly serves the stated workflow: cropping a segmented anatomical
mesh to a region of interest with an arbitrary solid is a normal medical-imaging
operation, and it is the non-scaling way to make an oversized part fit.

One caution worth designing for: intersecting a hostile input mesh will produce
garbage unless it is repaired first. Run the same repair path `prepare` uses,
and report added and removed volume as the existing edit-recording contract
requires.

### C7 — Planar trimming · Diff 20 · Imp 50

Also explicitly requested. An arbitrary plane, defined by a point and a normal
or by three picked points, with the option to keep either side or both as
separate objects.

Shares an implementation with C8's planar capping: trimming produces an open
boundary that must be capped to stay printable, which is the same operation
running in the same direction. Build them together.

Deliberately **not** including alignment keys and dowel pins — that was ranked
low priority, and it is a miniatures-and-props feature. Cut, cap, print, glue or
bolt.

### C3 — Infill lattices · Diff 46 · Imp 42

Gyroid, honeycomb and a scaffold/grid pattern inside a hollowed shell, matching
CHITUBOX's Scaffold/Hive/Grid and Lychee's Lattice Infill with its 0.8–3 mm
strut diameter range.

Gyroid is attractive here because it is a trivially evaluated implicit
surface — `sin x cos y + sin y cos z + sin z cos x = c` — which means it can be
sampled directly on the voxel grid C2 already builds, intersected with the
hollow cavity, and unioned back. That makes it cheaper than it looks, and
cheaper than a mesh-based lattice.

Importance 42: for functional parts, an infilled shell is a stiffness and
resin-cost trade-off that matters, but a solid or simply hollow part is usually
the right answer and always available. Do it after hollowing and holes work.

### C12 — Measure, mirror, per-axis scale · Diff 16 · Imp 38

Table stakes present in both competitors and absent here. Measure is the one
that matters most for engineering work — point-to-point and
perpendicular-to-face distance in the viewport.

Scale needs a deliberate guardrail given the stated position that scaling is
essentially off the table: allow it, but make a non-unity scale a **prominent,
persistent annotation in the report and the GUI**, not a silent transform. The
existing policy of never auto-scaling to fit must stay.

### C11 — Text and serial embossing · Diff 30 · Imp 26

CHITUBOX gates Text Labels behind Pro. Low importance for miniatures, mildly
useful here — part numbers, revision marks and orientation witness marks on
functional parts are a real workshop need. Requires font rasterization to a
mesh, then a boolean (which C6 provides). Late.

---

# 7. Detail — D: supports

The research is unambiguous that support quality is the most-cited differentiator
between the two competitors and the main reason people switch. It is also where
voxelmill is furthest behind on breadth — one pillar type, one raft, automatic or
manual contacts — while being *ahead* on the analysis that tells you whether the
supports worked.

### D5 — Re-run island detection after every edit · Diff 18 · Imp 70

Highest importance in this group and among the cheapest items in the document.

The complaint is specific and appears against both products: supports go stale.
Rotating a model after generating supports does not always re-run island
detection; islands the algorithm missed become "a floating piece of cured resin
in the vat the next morning." Lychee's own changelog confirms island-detection
fixes shipped in 7.6.2/7.6.3, so this is acknowledged, not hypothetical.

voxelmill's GUI already cancels obsolete generations and discards raced results
(`gui/jobs.py`), so the hard part — stale-result rejection — is done. What is
needed is the policy: **any change to orientation, placement, supports or
process settings invalidates the validation result**, and the UI shows a
persistent "not validated since last edit" badge until it is re-run. Never
display a stale pass as if it were current.

This is the single best importance-to-difficulty ratio in the document.

### D3 — Full manual support editor · Diff 44 · Imp 66

Lychee's manual support is built from four editable sections — tip, mid, base,
base-tip — each with its own shape and parameters, plus copy/paste of parameters
between supports, right-click actions (make vertical, make straight, make
pillar, recalculate), and a Pro-gated "relative calculator" that rescales ~15
parameters at once. CHITUBOX has add/edit/delete with batch frame-selection,
symmetrical supports and merge.

Implemented: per-contact geometry overlays on the shared routing loop
(`contact_parameters.py`), Setup-tab multi-select with apply / copy / paste /
reset, cone or cylinder tips with an optional break-point ball, CLI
`--contact-parameters` plus `--tip-shape` / `--break-point-diameter-mm`, and
project persistence including move/delete. Global defaults stay unchanged;
unmatched records are reported. Paint-on enforcers and blockers remain D2.

### D2 — Paint-on enforcers and blockers · Diff 40 · Imp 62

The consensus best "escape hatch when the algorithm gets it wrong" pattern in
modern slicers. Lychee gates support painting behind Pro; CHITUBOX has a
paint-on mode and a free-draw area; OrcaSlicer and PrusaSlicer both do it for
FDM supports and seams.

Implemented: Setup paint mode off/block/enforce and the Paint enforced / Paint
blocked tools, brush radius, click-drag on the model. Marks are triangle
centroids, not indices (display may be decimated, so cell IDs are not source
indices), and each is stored in the frame of the part it was painted on rather
than in plate coordinates, so paint travels with the part when it is moved or
rotated. Block drops automatic contacts and their coverage requirement; enforce
always places a contact; island births are never blocked; block wins over
enforce on the same mark. Nothing is automatic. CLI `--paint` still takes one
plate-coordinate table. Magenta/green on the model actor. D1 tree supports are implemented;
contour/face/boundary sampling is implemented (`support.contour_supports`,
`support.boundary_supports`); face sampling remains the default downward lattice.

### D6 — Support mechanics calibration · Diff 55 · Imp 58

`support_anchor_load` currently warns rather than fails, correctly, because a
share of area is not a strength result, and every support dimension in the
profile is documented as uncalibrated. `todo.md` records this honestly and
`docs/calibration.md` says so publicly.

The complaint this addresses is that auto-support "consistently underestimates
the load on sharp overhangs and the bases of heavy parts" — supports that look
adequate in preview and fail in the vat.

The work is physical, not just software: derive a peel-force model from lift
speed, cross-sectional area, layer height and FEP condition; calibrate pillar
strength against test prints; then promote the anchor-load check from warning to
failure with a real threshold. Difficulty 55 reflects that this needs printed
test artifacts and measurement, not only code.

Until then the current warning-with-explicit-uncertainty is the right behaviour
and should not be quietly upgraded to a confident pass.

### D1 — Support type library · Diff 52 · Imp 55

CHITUBOX names eleven types (adaptive, vertical, upright, branch, joint, small
pillar, forked, tree, contour, face, boundary). Lychee has projection, grid and
inline supports plus cross-bracing. voxelmill has a vertical pillar with a
tapered tip, plus bracing.

Highest value first, given the workload: **branch/tree supports** (one trunk
serving several contacts — less resin, fewer plate marks, far better on the
irregular downward surfaces anatomical meshes produce), then **model-to-model
contacts** landing on already-printed geometry (`plan.md` already lists this as
policy but it is only partially realised), then **contour and boundary
supports** along edges, which matter for warp control on flat engineering parts.

Difficulty 52 because routing a branching structure that is collision-free
against the model *and* against other supports is genuinely harder than routing
independent pillars — though `route_contacts` and `_segment_clear` in
`supports.py` already provide the collision primitives and the branching
attempts scaffold.

### D4 — Raft type library · Diff 24 · Imp 48

One bevelled raft today. Lychee has ten types and gates the good ones behind
Pro; CHITUBOX has four base shapes (plate, cross grid, hexagonal grid, linear
connection) with parameters.

Worth adding: a **grid/skeleton raft** (much less resin, much easier removal), a
**line/triangle connection** raft, and **no raft at all** for parts supported
directly on the plate. Each needs its own contact-area and adhesion reporting,
because raft choice is an adhesion-versus-removal trade-off and the program
should quantify it rather than leaving it to feel.

`raft_from_feet` in `geometry.py` is the extension point.

Implemented 2026-09-08 in `bases.py`: `none`, `pad`, `skate`, `skeleton` (the
spanning-tree/line connection) and `grid` (the square lattice), alongside the
unchanged `plate`, then `hex` (the hexagonal lattice) and
`base_edge_slope_deg` (the sloped edge). Each reports measured contact area,
convex envelope, open area and fraction, connected components and resin
volume. `triangle` now adds triangulated foot connections and a perimeter rim,
with a tree fallback for degenerate layouts. The adhesion half of the
trade-off is still unquantified — the reports are explicit that they establish
geometry and not adhesion, removal force or stability.

### D7 — Support presets as portable data · Diff 12 · Imp 40

The complement to A8: once presets exist in the profile schema, make them
exportable and importable on their own so a tuned support recipe can be shared
or version-controlled independently of the whole resin profile. Lychee's preset
import/export is a well-liked feature and this is a cheap superset of it, since
the format is already text.

---

# 8. Detail — E: slicing and exposure

### E4 — XY shrinkage and tolerance compensation · Diff 26 · Imp 78

**The highest-importance item in this group, and the score is driven entirely by
the stated workload.** For miniatures this is cosmetic. For functional
engineering parts it is the difference between a bore that accepts a bearing and
one that does not.

CHITUBOX has both, as separate concepts, and the distinction is worth copying:

- **Shrinkage compensation** — a global percentage scale applied at slice time,
  without resizing the model. It corrects the resin's cure shrinkage. Applying
  it at slice time rather than to the mesh is the right design, because it keeps
  the model dimensionally truthful and keeps the compensation attached to the
  resin, where it belongs (it is a material property, so it goes in the `.res`
  process block).
- **Tolerance compensation** — a per-layer inner/outer offset in the raster
  domain, with a separate value for bottom layers. This corrects the systematic
  oversize from light bleed and over-cure, which is what makes printed holes
  undersized and printed pins oversized. It is a morphological erode/dilate on
  the layer mask, applied with opposite sign to inner and outer boundaries.

Implementation lands in the rasterizer and the mask post-processing path, not in
the geometry — `native/raster.cpp` and the mask handoff into `goo.py`. The inner
versus outer distinction requires knowing which boundaries are holes, which the
existing connected-component labelling in `validation.py` already determines.

Settings belong in the resin process block, per printer, because they are a
property of the resin/printer/exposure combination: `process.shrink_percent_xy`,
`process.shrink_percent_z`, `process.tolerance_offset_mm`,
`process.bottom_tolerance_offset_mm`.

Pair it with a calibration workflow (E8) so the numbers are measured rather than
guessed, and hold to the existing discipline: an uncalibrated compensation
should default to zero and say it is uncalibrated, not ship a plausible-looking
constant.

### E7 — TSMC semantics and motion field validation · Diff 30 · Imp 66

The profile already carries all 18 `printer.motion` fields, including the
second-stage `*_height2` / `*_speed2` pairs that constitute two-stage motion
control. The GOO exporter requires all of them. What is missing is **meaning**:
`docs/gui.md` and the profile comments both state plainly that the values were
observed in the reference GOO, that their units and tilt-release behaviour are
unverified, and that they must be reviewed before a physical print.

CHITUBOX documents TSMC as splitting a two-substage single-speed lift/retract
cycle into a four-substage two-speed cycle — retract from the FEP slowly, then
move fast, then approach slowly — which shortens cycle time without losing
prints. That is the semantic model to encode.

Three deliverables: document the units and the sequence for each field; add
validation (retract distances must sum to the corresponding lift distance, which
CHITUBOX enforces and which is a common misconfiguration); and feed the resulting
motion timeline into B5's print-time computation, which cannot be right without
it.

This unblocks B5 and is a prerequisite for trusting any output on hardware. The
honest labelling stays until a real printer verifies it.

### E5 — First-layer / elephant-foot compensation · Diff 22 · Imp 56

The research found **no named elephant-foot compensation feature in either resin
slicer**, and no complaint threads either — it is handled implicitly through
bottom exposure time and burn-in layer count. In FDM slicers it is an explicit,
well-understood geometric offset.

For functional parts printed directly on a raft or plate, first-layer flare is a
real dimensional error, and E4's tolerance machinery makes this nearly free once
built: apply a separate, larger negative offset to the bottom layers, ramped out
over the transition layers. The transition-layer interpolation already exists in
`config.py`'s exposure schedule; this reuses the same ramp for a geometric
quantity.

Small effort, genuinely underserved area, directly relevant to the workload.

### E8 — Exposure calibration test generator · Diff 30 · Imp 52

Neither competitor has a real one built in. CHITUBOX ships TMA/TMB/TMC test
*models* with a documented manual procedure. Lychee hosts calibration
*documentation* and can slice externally authored test STLs. Anycubic's RERF is
a firmware feature, not a slicer feature.

So a built-in generator is a genuine differentiator, and it is a natural fit for
this codebase: emit a printer file whose layers carry **different exposure
values across a spatial matrix or across Z bands**, plus a report mapping each
cell to its parameters so the printed result can be read back unambiguously.
The per-layer parameter records already exist in the GOO writer
(`_layer_values`, `_expected_record`) — varying them deliberately is a small
extension of machinery built for a different purpose.

```
voxelmill calibrate exposure --printer mars5-ultra --resin sunlu-abs-like-gray \
  --range 2.0:5.0 --steps 8 --output output/expo-test.goo
voxelmill calibrate tolerance --range -0.05:0.05 --steps 5 --output output/tol.goo
```

The tolerance variant feeds E4 directly, closing the loop between "the program
has a compensation setting" and "the user knows what to put in it." That closed
loop is something neither competitor offers.

### E2 — Per-Z-band and per-object slice overrides · Diff 34 · Imp 44

CHITUBOX's Multi-Parameter Slice, Model and Height modes. The settings-model
half is A10; this is the slicer honouring it — resolving the active parameter
set per layer rather than once per job, and writing per-layer records that
reflect it.

The GOO writer already emits per-layer parameter records, so the format supports
it. The change is in the pipeline: stop treating the process block as a constant.

Note CHITUBOX's own limitation, which is worth beating: multi-parameter slicing
and its per-pixel editing mode are mutually exclusive. There is no reason for
that here.

### E6 — LED uniformity mask compensation · Diff 34 · Imp 34

CHITUBOX has Mask Settings with hub-and-spoke and matrix patterns to correct
uneven light distribution across the panel. It matters on large panels with
noticeable centre-to-edge falloff.

Implementation: a per-printer greyscale correction map, multiplied into the mask
at write time. Depends on B4 (greyscale output) — meaningless on binary masks.
Depends on measurement to be worth anything, which is why importance is 34: a
guessed correction map is worse than none.

### E3 — Cross-sectional-area-driven exposure · Diff 30 · Imp 30

CHITUBOX's third Multi-Parameter mode: vary parameters by the cross-sectional
area at each layer, on the theory that a large area needs different peel handling
than a small one. Plausible, thinly documented, and unverified. The area per
layer is already computed for B6's volume sum, so the input is free.

Low importance: this is a refinement on top of E2 with no strong evidence behind
it. Build the mechanism, leave the policy off by default.

---

# 9. Detail — F: performance

### The headline finding, and it is not what was expected

**GPU acceleration is not the big win here. CPU architecture is.**

The evidence, in order of weight:

1. **No production slicer uses the GPU for slicing math.** CHITUBOX lets you
   pick Direct3D/OpenGL/Metal — for the *viewport*. PrusaSlicer's SLA
   rasterizer is the AGG software scanline rasterizer, CPU-only. Lychee is
   closed but community reports are consistently CPU/RAM-bound.
2. **Every GPU slicer found is a research prototype**, all using the same
   3-pass OpenGL stencil-buffer trick (Matt Keeter's, Formlabs' WebGL one,
   brettlajzer's, a PySLM prototype). The best documented number — 5 M
   triangles into ~4,000 layers in ~3 minutes on a GTX 1080 — is measured
   against unspecified "previous software," not a tuned CPU baseline.
3. **The fastest actively maintained open-source MSLA slicer uses no GPU at
   all.** `mslicer` (Rust, 2026) claims 20–120× over competing slicers from
   three CPU-side choices: multithreading, spatial acceleration structures, and
   **slicing directly into RLE-compressed output** instead of materializing
   dense bitmaps.
4. `OpenSLAice` (2026) is CPU-only and gets its wins from adaptive slicing —
   doing less work, not doing the same work faster.

So: **parallelize, then restructure the representation, then prune, and only
then consider offloading.** The one workload where the GPU is clearly the right
answer is orientation search (F7), because it is embarrassingly parallel across
candidates and multiplies a per-candidate cost by tens of trials.

CUDA is permitted here with a mandatory CPU fallback, which is the right policy —
but note that on this machine (i9-13900HX, 24 cores / 32 threads, AVX2 + VNNI,
62 GB RAM) the CPU items below will likely deliver more than the GPU items, for
less risk, and will benefit every user rather than only NVIDIA ones.

### F2 — RLE / sparse layer representation · Diff 52 · Imp 68

The most consequential change available, and the one `mslicer` credits most.

Today the full-resolution validation path holds a dense layer plus label images
in RAM — roughly 500 MB at 8520×4320 per `todo.md`. Most of any layer is empty;
most analysis touches boundaries and interiors, not the whole pixel grid.

Slice **directly into run-length form** and keep it that way through island
labelling, void analysis and file output. Connected-component labelling over
runs rather than pixels is a well-understood transformation and is dramatically
cheaper when occupancy is sparse. Expected 5–20× on memory traffic and
downstream analysis, plus a large cut in peak RSS.

Difficulty 52 because it changes a representation that `validation.py`,
`supports.py`, `raster.py`, `native/raster.cpp` and `goo.py` all touch. It is
the one item here worth doing early despite the cost, because every later
performance item is cheaper on top of it and more expensive underneath it.

Do it before F1 and F6 if sequencing allows — parallelizing a dense
representation then converting it to sparse means doing the threading work twice.

### F3 — Z-interval tree over triangles · Diff 28 · Imp 64

Currently every layer is tested against the triangle set. Sort triangles once
into a Z-interval tree, then per layer test only the triangles whose Z extent
overlaps that layer — typically a tiny fraction of 6 M.

This reduces the *fundamental* amount of work rather than running the same work
faster, which is why it outranks SIMD and the GPU rasterizer. Published
conservative-slicing work reports 3–25× from exactly this pruning. Standard
practice in FDM slicers already.

Best difficulty-to-value ratio in this group. `native/raster.cpp`.

### F1 — Multithread across layers · Diff 24 · Imp 62

Layer rasterization, per-layer labelling and per-layer drainage analysis are
independent once triangles are binned by Z. Near-linear scaling with core count;
on 32 threads that is a large, low-risk win.

`resources.workers` already exists in the schema and defaults to 2. The budget
contract (`memory_gib`, bounded workers) is already designed for this — the
constraint is that per-worker memory must be bounded, which is much easier after
F2 than before it.

Caveat worth respecting: the existing design deliberately refuses to exceed the
memory budget rather than silently coarsening. Threading must honour that, which
means per-worker allocation accounting, not just a thread pool.

### F4 — Incremental re-slice · Diff 40 · Imp 54

After a local edit — a moved support, a nudged orientation, a changed exposure —
re-rasterize only the layers whose active-triangle set actually changed, using
F3's interval tree to determine which those are. Potentially 10–100× on the
interactive tweak loop specifically; no effect on a cold full slice.

This is what makes the GUI feel like a tool rather than a batch job, and it
directly serves D5 (re-validate after every edit) by making re-validation cheap
enough to be automatic rather than a button.

### F6 — Disk-backed tiled layer records · Diff 30 · Imp 46

Recorded stage-2 debt. PrusaSlicer's SLA path already streams: only a few
support slices are in memory at once, specifically to bound RSS. The `.chop`
scratch-dir and budget contracts exist for this.

F2 reduces the need substantially — sparse layers may simply fit. Re-measure
after F2 before building this.

### F10 — Benchmark harness · Diff 16 · Imp 44

There is currently a runtime and peak-RSS record for `auto_placement` (7.9 s,
1.15 GiB per temporal bone) and, per `todo.md`, **none for a full `prepare` on
an original.** Every item in this group is unmeasurable without that.

Build it first: a small suite over the immutable originals recording wall time,
peak RSS, scratch usage and output hashes, with thresholds that fail CI on
regression. It costs almost nothing and it is the only way to know whether F2
actually delivered 5× or 1.2×.

Also the guard against a real, documented complaint: CHITUBOX users have been
advised to **downgrade** because updates caused slowdowns. Do not be that.

### F7 — GPU orientation search · Diff 44 · Imp 34

The one workload where the GPU is clearly right. Each candidate orientation
needs an independent occupancy-grid evaluation; that is parallel across
candidates on top of being parallel within each. Academic work on
orientation/support-volume optimization specifically identifies GPU evaluation
as the enabler, and pairing it with a smarter optimizer (Bayesian rather than
grid search, reported at up to 17× fewer iterations) compounds the win.

This is also what makes C10's "real support volume per candidate" affordable
instead of prohibitive.

Policy per the stated decision: CUDA is acceptable, a CPU fallback is mandatory,
the dependency is optional, and the program must run identically without it.
Importance 34 not because the speedup is small but because orientation search
is already ~7 s and is not the bottleneck — this becomes important only once
C10 makes each candidate much more expensive.

If portability later matters more than peak speed, the vendor-neutral options
are OpenGL compute (reusing the GL context VTK already owns — lowest friction),
`wgpu-py`, or Vulkan compute. CUDA/CuPy/Numba-CUDA are NVIDIA-only and should
stay strictly an optional fast path.

### F5 — SIMD in the rasterizer · Diff 26 · Imp 32

Realistic expectations, not folklore: hand-written AVX2 gives roughly 1.3× over
an SSE2 baseline for image-processing kernels, AVX-512 about 1.4×, with
multiplicative gains alongside threading on cache-resident working sets and much
less at sizes where memory bandwidth dominates. A full-resolution layer is
firmly in the bandwidth-dominated regime, which is precisely why F2 (less
memory traffic) outranks this.

Worth doing after F1/F2/F3, not before.

### F8 — GPU rasterization · Diff 58 · Imp 26

The stencil-buffer technique is real and proven, but for this codebase the
caveats are heavy: it **requires watertight input** (four of seven originals are
not, and C8 is the prerequisite); it yields binary solid/void with **no
connectivity information**, so it does not replace the analysis pass that is
this program's main value; and at 8520×4320 read-back per layer for thousands of
layers is PCIe-bound unless results stay resident for the next stage.

Realistically 5–20× against a *well-optimized* CPU rasterizer, not the ~240×
claimed against unspecified prior software — and F1+F2+F3 capture much of that
range at lower risk with no dependency. Recommendation: do not build this until
the CPU items are done and measured.

### F9 — GPU SDF voxel repair · Diff 62 · Imp 22

NanoVDB is designed exactly for GPU-accelerated sparse volumes and is production
proven in Houdini, Blender and Arnold. If repair were moving to an SDF
representation anyway, this would be a large win.

It is a mesh→voxel→mesh architectural change with a heavy dependency, for a
stage that is not currently the bottleneck. Lowest importance in the document.
Listed for completeness.

---

# 10. Detail — G: GUI

The stated model is PrusaSlicer: easy to use while exposing everything. The
mechanism that makes PrusaSlicer work is worth stating precisely, because it is
often mis-copied as "add a beginner mode."

**Every setting carries declarative metadata** — a visibility tier
(Simple/Advanced/Expert), a risk colour (green = safe for a beginner, yellow =
advanced, red = only touch this when building a profile for a new printer), a
label, a tooltip, a unit, and a range. **One settings renderer consumes that
metadata** to draw every page. The maintenance cost of the entire
progressive-disclosure system is therefore about one enum per setting, not a
forked UI. That is why it succeeds where "beginner/expert mode" projects
usually collapse.

voxelmill's current GUI is honest but not this. The Setup tab holds commonly
tuned values, and everything else lives in a validated raw-JSON box described in
`docs/gui.md` as "the complete manual override surface when a compact control is
not appropriate." That is a reasonable stopgap and a bad destination: JSON has
no tooltips, no units, no ranges, no search, no revert, and no indication of
which values differ from the profile.

### G3 — Generated typed settings pages · Diff 40 · Imp 70

The keystone. Build a settings **descriptor table** — one entry per key in
`config.py`'s `DEFAULTS`, carrying type, unit, range, tier, risk, label, tooltip
and the config key path — and generate the settings UI from it.

Two properties make this worth the effort beyond cosmetics:

- **The CLI and GUI stop drifting.** The same table drives `--set` validation,
  `--help` text, GUI widgets, shell completion (H6) and the JSON Schema (H3).
  The stated requirement that the UI be intuitive while the CLI exposes every
  setting is satisfied structurally rather than by discipline.
- **`--set` and the GUI become provably equivalent**, which is currently a claim
  the docs make and nothing enforces.

Keep the raw JSON box as an expert escape hatch — it is genuinely useful and
some future setting will always arrive before its widget does. Demote it from
primary surface to last resort.

### G4 — Tooltips, modified markers, revert arrows · Diff 22 · Imp 62

Free once G3's descriptor table exists, and disproportionately effective:

- Every field's tooltip shows the human description **and the config key path**,
  so a GUI user can find the `--set` incantation without reading docs. This one
  detail does more for CLI/GUI parity than any amount of documentation.
- A value differing from the resolved profile is marked (PrusaSlicer uses
  orange) with a per-field revert arrow.
- Per A6, the marker distinguishes *which* layer supplied the value — default,
  printer, resin, or this session.

Per PrusaSlicer's explicit design principle: settings changes get per-field
revert, **not** a place on the geometry undo stack. Two undo mechanisms for one
concept is a bug.

### G1 — Simple / Advanced / Expert tiers · Diff 30 · Imp 58

Tag each descriptor with a tier and a risk colour, add a mode selector, and
render accordingly. The risk colouring is worth keeping separate from the tier
because they answer different questions: "should I see this?" versus "will this
hurt me?"

For this codebase the red tier is obvious and important — the 18 uncalibrated
`printer.motion` fields, the aggressive voxel repair controls, and the
uncalibrated orientation weights all belong there, with tooltips that say they
are uncalibrated.

### G10 — Autosave and crash recovery · Diff 22 · Imp 58

Neither competitor was confirmed to have it, and "lost work" is a recurring
complaint theme. `.chop` already serializes the full document — settings,
hashes, transforms, edits, support graph, validation. Autosave is therefore a
timer plus an atomic write to a recovery slot, and recovery is a prompt on next
launch.

Cheap, and it protects exactly the expensive thing: a manually curated support
layout on a six-million-triangle mesh.

### G2 — Fuzzy, mode-aware settings search · Diff 20 · Imp 56

`Ctrl+F` over the descriptor table, fuzzy/typo-tolerant, searching labels,
tooltips and config key paths. Critically, **search respects the current mode** —
otherwise progressive disclosure leaks and the tiering is pointless.

Searching the key path matters here: someone who read `docs/cli.md` and knows
`repair.min_void_volume_mm3` should be able to type it and land on the widget.

### G13 — Direct-manipulation gizmos · Diff 40 · Imp 54

Rotation and translation gizmos, a cut-plane handle for C7, hole placement by
clicking a surface for C4, and support drag-placement for D3. The viewport
already does contact hit-testing, so picking exists; the work is the interaction
model and the VTK widget plumbing.

Follow the two hard-won GUI-testing rules in `gotchas.md` and `docs/gui.md` for
anything touching a render window: a real-render test runs in a subprocess under
`xvfb-run`, and assertions go against `vtkWindowToImageFilter` output rather than
`QWidget.grab()`, which silently captures a black viewport. That trap already
killed a whole pytest session once.

### G5 — Object list panel · Diff 28 · Imp 50

A scene tree — objects, instances, per-object overrides, per-Z-band modifiers.
Depends on C1 and A10; it is their UI. PrusaSlicer's right-panel tree is the
model to copy, including the gear icon that opens per-scope overrides.

### G12 — Layer viewer upgrades · Diff 26 · Imp 48

The Layers tab renders one printer-pitch layer with a diagnostic filter. Worth
adding: overlay modes (islands, enclosed voids, drainage bottlenecks, supports
versus model) toggled independently; pixel inspection reporting exact coordinates
and grey value; and an **A/B diff between adjacent layers**, which is the fastest
way to see an island being born and is something neither competitor offers.

CHITUBOX has per-pixel layer editing. That is deliberately excluded here — it
edits the output behind the model's back, which contradicts this program's
verify-what-you-shipped contract. Fix the geometry, re-slice, re-verify.

### G7 — Unified notification framework · Diff 20 · Imp 44

One toast/overlay component for progress, warnings and errors, rather than ad
hoc dialogs per feature. Copy the pattern, and copy the lesson from PrusaSlicer's
open issue about warning fatigue: **build per-category suppression on day one**
rather than being asked for it later.

### G9 — First-run configuration wizard · Diff 18 · Imp 42

Pick a printer, pick or create a resin, confirm the process block, done. Uses
A5's compatibility conditions to avoid offering a resin profile that has no
process block for the chosen printer. The single highest-leverage thing for
someone opening the program for the first time — including this user's future
self on a new machine.

### G6 — Named undo/redo history · Diff 22 · Imp 40

Undo/redo exists. Add named steps ("add contact," "suppress contact," "rotate,"
"set wall thickness") and a right-click dropdown to jump several states at once.
Settings changes stay off this stack per G4.

### G8 — Theme and shortcut editor · Diff 22 · Imp 34

Lychee has a theme editor and 77+ remappable shortcuts, and both are genuinely
praised. Treat as a floor to match, not a differentiator. Dark mode matters more
than it sounds for a tool used to stare at greyscale layer images.

### G11 — Dockable panels with persisted layout · Diff 18 · Imp 30

Qt provides most of this. Persist the layout so it survives a restart.

---

# 11. Detail — H: automation, integration, distribution

### H4 — SDCP completion · Diff 30 · Imp 50

The offline simulator and its documented workflow are complete. The user
authorized communication with the idle printer, but explicitly forbade
printing. Discovery, fresh status, attributes, history, and file listing passed;
evidence is in `reports/plan2/printer-readonly.json` and
`reports/plan2/printer-monitor-2026-09-10.json`. No upload, print, motion, or
settings command was sent. The printer-supplied RTSP stream decoded as H.264 at
1280×720, and a downloaded history time-lapse was verified with ffprobe. The
optional text heartbeat timed out despite successful protocol queries. Reported
dimensions conflict with the configured and official envelope; their meaning is
unverified and the profile is unchanged.

Remaining: upload and print-control acceptance and sustained status watch.
Physical print and calibration gates remain deferred.

Worth noting the competitive position: Lychee added SDCP 3.0 with camera
monitoring in 7.1/7.2; CHITUBOX pushes most network functionality into a separate
ChituManager application. Both are LAN-only. VoxelMill's monitor is deliberately
read-only and independent of the slicing editor; it does not make editing or
exporting contact a printer.

### H1 — Batch mode · Diff 20 · Imp 46

N models × M profiles, headless, one report per combination plus a summary.
Lychee's Batch tool is a fixed step list; CHITUBOX's ChituAction is a GUI macro
recorder. Neither is scriptable.

voxelmill needs almost nothing for this, because the CLI already is the
automation surface — this is a driver loop, parallelism across jobs bounded by
the existing resource budget, and a summary report. The real differentiator is
already present: **the exit codes mean something**, so a batch run in CI can
distinguish "validation failed on model 3" from "the tool crashed."

### H3 — Versioned report schema · Diff 14 · Imp 40

Every command writes the same JSON report shape. Give it an explicit
`report_version`, publish a JSON Schema, and validate against it in tests.

This makes the report a stable contract other tools can consume, which is the
thing that lets this program be a component rather than only an application. The
OrcaSlicer lesson applies here as much as to profiles: an unversioned format
that grows silently breaks consumers written against the older shape.

Generated from G3's descriptor table where the shapes overlap.

### H5 — Packaging · Diff 26 · Imp 36

Per the "personal now, public-ready later" decision, this is preparation rather
than a release: keep the build reproducible, keep dependencies pinned, keep
paths XDG-correct (A3), and avoid anything that assumes the source tree is the
install location.

When a release does happen, the Linux story is a genuine competitive opening.
CHITUBOX's Linux build is reported as second-class — the download page has been
observed serving a Windows executable with Linux selected, and Gentoo and WineHQ
threads exist because people had to fight it. Lychee is natively cross-platform
and is explicitly preferred by Linux users for that reason. A first-class native
Linux resin slicer with a CLI is an underserved niche both incumbents leave
partly open.

The C++ extension and CGAL/GMP make an AppImage or Flatpak more work than a pure
wheel; a wheel plus documented system dependencies is the pragmatic first step.

### H2 — Post-slice hooks · Diff 18 · Imp 28

CHITUBOX has `.CHplugin` — a zip with a manifest and an executable, invoked
after slicing with the output path as an argument. It is community-documented
rather than officially specified.

The equivalent here is smaller and better: a configured command run after a
successful export, receiving the output path and the report path. That covers
uploading, archiving, notifying and post-processing without inventing a plugin
API.

Security note: a hook is arbitrary code execution configured in a settings file.
It must be opt-in, never inherited silently from an imported profile, and
clearly visible in `voxelmill profile` output.

### H6 — Shell completion and man page · Diff 8 · Imp 18

Generated from G3's descriptor table and the argparse tree. Cheap, and it makes
the CLI feel finished. Completing profile ids from the A3 search path is the
part that actually gets used.

---

# 12. Detail — I: analysis and calibration

### I1 — Persist and replay analysis artifacts · Diff 26 · Imp 52

UVtools is fast at iterating on issue detection because it works against
already-rendered layer images with no re-slice cost per iteration. voxelmill's
analysis is better placed — it has the mesh — but pays a full re-slice to re-run
anything.

Persist the per-layer island maps, void forests and drainage graphs into the
scratch area or the `.chop` project, keyed by a hash of the geometry and the
settings that affect them. Re-running an analysis with a changed *threshold* —
`min_orifice_area_mm2`, `min_void_volume_mm3` — should then be instant, because
the expensive part is unchanged.

Composes with F4: F4 makes re-slicing cheap after a geometry edit, I1 makes
re-analysis free after a threshold edit. Together they turn the report from a
batch artifact into something you can interrogate.

### I2 — Resin density and cost fields · Diff 8 · Imp 40

`resin.density_g_ml` and `resin.price_per_l` in the `.res` schema. Prerequisite
for B6. Trivial, and it is the input CHITUBOX makes the user remember to type
every time.

### I3 — Print-time auto-calibration · Diff 22 · Imp 36

CHITUBOX exposes a manual print-time compensation field because high-resolution
printers have data-loading delays it cannot predict. That is a fudge factor the
user must discover exists.

Better: `voxelmill calibrate time --actual 4h20m --report output/left.json`
computes the per-layer overhead constant that reconciles the estimate with the
measured print, and writes it into the printer profile as a calibrated,
labelled value. The reference GOO in this repository is itself named with a
measured duration (`04h20m`), which is exactly the kind of datum this consumes.

### I4 — Report as HTML · Diff 14 · Imp 26

Render the JSON report as a readable page — diagnostics grouped by severity,
per-layer charts, thumbnails of flagged layers. The JSON stays the contract; the
HTML is a view. Useful for looking at a failed run without reading raw JSON,
and for sharing a result.

---

# 13. Suggested build order

Sequenced by dependency and value per unit of effort, not by raw score. The
principle throughout: **build the things other things need, and measure before
optimizing.** Each phase ends at a point where the program is coherent and
shippable to yourself.

## Phase 0 — Instrument and stop guessing

`F10`, `H3`, `I2`

Nothing else in this document can be evaluated without a benchmark. There is
currently no runtime or peak-RSS figure for a full `prepare` on an original —
only `auto_placement` is measured. Build the harness over the immutable
originals, version the report schema, and add the two resin fields that
everything downstream needs.

Small, fast, and it converts the rest of the plan from opinion into measurement.

## Phase 1 — The profile system

`A3`, `A1`, `A2`, `A8`, `A6`, `A7`, `B3`

The headline request, and the right thing to build first regardless: it is
cheap, it is where the competition is weakest, and every later phase adds
settings that want a home.

Order matters. Discovery and the system/user split (A3) first, because the
manager commands need somewhere to write. Then the printer and resin managers
with clone, edit and the printer-bound `resin bind --from` operation. Then
presets, diff and provenance. Then the GUI manager over the same store — no
second code path. Then populate the Elegoo profiles, because by this point
adding a printer is data entry rather than surgery.

Ends with: profile management that is straightforwardly better than either
competitor's, from both the CLI and the GUI, in text files you can `vi` and
`git`.

## Phase 2 — Correctness for functional parts

`C8`, `E7`, `B5`, `B6`, `E4`, `E5`

The workload-specific phase. C8 first, because four of seven originals cannot
currently reach a printable state — that is a blocker, not a feature. Then pin
down the motion semantics (E7), which unblocks a real print-time estimate (B5),
which with volume and cost (B6) removes the last "we don't know" from a routine
run.

Then the dimensional work: shrinkage and tolerance compensation (E4) and
first-layer compensation (E5). E4 is the highest-importance item in its group
precisely because these are functional parts — a bore that does not accept a
bearing is a failed print regardless of how it looks.

Ends with: parts that come off the plate the size they were designed to be, and
a run that reports how long it took and what it cost.

## Phase 3 — Make it fast, in the order the evidence supports

`F3`, `F2`, `F1`, `F4`, `F6` (re-measure first), `F5`

Interval-tree pruning first (F3): best ratio in the document, reduces the
fundamental work rather than the constant factor. Then the sparse/RLE
representation (F2) — the largest single win available and the reason to do it
before threading, since parallelizing a dense representation and then converting
it means doing the threading work twice. Then threading (F1), which is
near-linear on 32 threads once per-worker memory is bounded.

Then incremental re-slice (F4), which is what makes Phase 5's GUI feel
responsive and makes automatic re-validation (D5) affordable.

Re-measure before building F6 — sparse layers may simply fit, making
disk-backing unnecessary. SIMD (F5) last, and only if the benchmark says the
inner loop is still hot; at full-resolution layer sizes the workload is
bandwidth-bound, which is exactly the regime where SIMD underdelivers.

Explicitly **not** in this phase: every GPU item. Do the CPU work, measure, then
decide.

## Phase 4 — Support quality

`D5`, `D3`, `D4`, `D2`, `D1`, `C10`, `C5`

D5 first — automatic re-validation after every edit is the best
importance-to-difficulty ratio in the document, and F4 has just made it cheap.
Then the manual editor (D3) and raft variety (D4), then paint-on enforcers and
blockers (D2), then branching supports (D1).

Then the orientation work: expose the ranked scored candidates (C10), which no
competitor does, and add peel-vacuum detection (C5) to complement the trapped-
void analysis that already exists.

D6 (support mechanics calibration) is deliberately excluded — it needs printed
test artifacts and measurement, and belongs to the same real-hardware campaign as
E8 and I3.

Ends with: support generation competitive with Lychee's, on top of analysis that
is already better than either.

## Phase 5 — The GUI, done properly

`G3`, `G4`, `G1`, `G2`, `G10`, `G7`, `G9`, `G12`, `G13`, `G6`, `G8`, `G11`

Strictly in this order, because G3's descriptor table is the foundation and
almost everything else is nearly free on top of it. Tooltips with key paths,
modified markers and revert arrows (G4); visibility tiers and risk colouring
(G1); mode-aware fuzzy search (G2) — that quartet is the PrusaSlicer pattern,
and it is what satisfies "intuitive UI, every setting reachable from the CLI"
structurally rather than by discipline.

Then autosave (G10), notifications with per-category suppression built in from
the start (G7), the first-run wizard (G9), the layer viewer upgrades including
A/B layer diff (G12), and direct-manipulation gizmos (G13) — observing the
`xvfb-run` and `vtkWindowToImageFilter` testing rules for anything touching a
render window.

Ends with: a GUI that is a real front-end to the CLI rather than a raw JSON box
with a 3D view attached.

## Phase 6 — Hollowing, booleans, trimming

`C6`, `C7`, `C2`, `C4`, `C9`, `C3`, `C12`

The explicitly requested geometry operations. Booleans first (C6) — Manifold is
already in the build, so intersect-against-a-bounding-box-STL is close to free
and directly serves the medical-mesh cropping workflow. Planar trimming (C7)
shares its capping implementation with C8, already built in Phase 2.

Then hollowing (C2) via the voxel route, because the input meshes are hostile
and the voxel path composes with the repair infrastructure that exists. Then
drain and vent holes (C4), consuming the void forest and gravity sweep that are
already computed — placed in pairs and sized by viscosity, which is the fix for
the most-cited hollowing failure in the research. Then wall-thickness analysis
(C9), lattice infill (C3), and the transform utilities (C12).

Ends with: hollowing that is better reasoned than either competitor's, because
the drainage analysis it feeds into already exists and is already better.

## Phase 7 — Scene, second format, automation

`C1`, `A10`, `G5`, `E2`, `B1`, `B2`, `B4`, `H1`, `B8`, `D7`, `B7`

Multi-part with manual layout (C1) is the largest structural change, which is
why it is late — everything before it is cheaper in a single-part world, and it
needs a settled settings model. It brings A10's scoped overrides, G5's object
tree and E2's per-band slicing with it; build them together.

Then the format work: refactor a `LayerFormat` interface out of `goo.py`
**before** writing CTB, then the writer (B1, unencrypted v3 first, encrypted v4+
as a separate increment), then the reader and decode-verifier (B2) — the
verify-before-publish contract must not be dropped for the second format.
Greyscale anti-aliasing (B4) via coverage computed during rasterization rather
than blur applied afterwards, which is the sharper answer and uses sub-pixel
information the rasterizer already discards.

Then batch mode (H1), additional mesh formats (B8), portable support presets
(D7) now that a second format exists to share them across, and the `convert`
command (B7) that falls out of having two formats and a verifier for each.

## Phase 8 — Hardware, calibration, and optional acceleration

`E8`, `I3`, `D6`, `E6`, `H4`, `F7`, `I1`, `H5`, `H2`, `H6`, `I4`, `E3`, `C11`,
`A4`, `A5`, `A9`, `F8`, `F9`

The campaign that requires a free printer and physical measurement: the exposure
and tolerance calibration generators (E8), print-time calibration from measured
prints (I3), support mechanics (D6), LED uniformity (E6), and SDCP acceptance
(H4) once the hardware is authorised.

Then optional acceleration — F7's GPU orientation search, which only becomes
worthwhile once C10 has made each candidate expensive — and the remaining
polish and long-tail items.

F8 and F9 sit at the very end deliberately. On present evidence they should
probably never be built.

---

# 14. Where this ends up

If the phases above are completed, the comparison looks like this.

**Matched:** file format coverage for the printers that matter, hollowing with
infill and properly reasoned drain/vent holes, booleans and planar trimming,
multi-part plates, support type variety and manual editing, paint-on control,
greyscale anti-aliasing, per-band parameter overrides, print time and resin cost,
a settings UI that is genuinely usable, a printer database.

**Exceeded, and these are the reasons to build it at all:**

- **Verification.** Reslice the exported STL and decode the exported printer file
  before publishing either. Neither competitor does this, and no amount of
  feature parity substitutes for it.
- **Analysis in the slicer.** Island, void, drainage and suction analysis at
  printer pitch, as part of preparation, with the mesh in hand. Everyone else
  makes you open UVtools afterwards.
- **Profiles as text you own.** Versioned TOML with clone, diff, provenance,
  inheritance, `$EDITOR` integration and full CLI management. Against `.cfg` /
  `.cfgx` / `.cfgd` extension-renaming and cloud-mediated `.lyr` blobs, this is
  not a close contest.
- **A real CLI.** Headless, scriptable, batchable, with exit codes that mean
  something. Neither competitor has one at all.
- **Orientation you can argue with.** Ranked candidates with per-term scores.
  Neither exposes a score.
- **A calibration loop that closes.** Generate the test file, print it, feed the
  measurement back into the profile. Both competitors ship a compensation field
  and leave you to guess the number.
- **Honesty as a feature.** `not_run` instead of a false pass, `no_feasible_
  placement` instead of silent rescaling, uncalibrated values labelled as such,
  and a failed answer written as evidence rather than swallowed.
- **Offline, no account, no telemetry, no subscription, Linux-native.** One
  competitor requires online account verification and device-bound licences; the
  other mediates your settings through its cloud. Both have a documented history
  the open-source community remembers.

**Deliberately not pursued:** automatic nesting solvers, alignment keys and
dowel pins, sculpting and deformation, adaptive layer height, multi-material,
cloud profile libraries, per-pixel editing of sliced output, and AI model
generation. Each is either irrelevant to functional engineering parts and
medical meshes, or actively contrary to the verify-what-you-shipped contract.

---

# 15. Sources and confidence

Compiled 2026-09-07 from vendor documentation and changelogs
(`doc.mango3d.io`, `lychee.co`, `docs.chitubox.com`), trade press (All3DP,
3D Printing Industry, 3Dnatives), GitHub (UVtools, PrusaSlicer, OrcaSlicer,
mslicer), academic sources on GPU slicing and orientation optimization, and
independent reviews.

Claims to treat with care, all flagged where they appear above:

- **Reddit was unreachable.** No direct community sentiment could be sampled;
  complaint frequencies are directional. One poll figure is quoted secondhand
  through trade press.
- **Both vendors' live comparison pages are JavaScript-rendered** and could not
  be fetched. Tier boundaries come from press coverage of the vendors' own
  announcements, not from the primary tables.
- **Version and naming corrections:** there is no Lychee 8 — the line is 7.x
  (7.6.5, May 2026) and the tiers are now Lite/Plus/Library, not Free/Pro/
  Premium. CHITUBOX merged Basic and Pro into one app with Basic/Advanced/Pro
  tiers around September 2025; the `chitubox-pro` documentation tree is marked
  deprecated.
- **Unconfirmed absences** — features neither documentation nor review could
  confirm exist, which may simply be undocumented: a Lychee wall-thickness
  heatmap ("Mesh Doctor" is third-party terminology), a distinct Lychee support
  blocker separate from its hollowing blockers, CHITUBOX profile file formats and
  their human-editability, and any orientation scoring UI in either product.
- **Pricing figures are approximate.** CHITUBOX Advanced $9.99/mo or $99/yr and
  Pro $15.99/mo or $169/yr come from announcement coverage; Lychee Plus is
  around €9.99/mo with conflicting third-party figures for Library.
- **Performance numbers cited for GPU slicing** (5 M triangles into ~4,000 layers
  in ~3 minutes on a GTX 1080) are measured against unspecified prior software,
  not a tuned CPU baseline. `mslicer`'s 20–120× claim is the vendor's own. Both
  are directional, which is why section 9 recommends measuring before optimizing.

Full research notes, with inline citations, are in the session scratchpad:
`lychee.md`, `chitubox.md`, `complaints.md`, `perf-ux.md`.
