# Competitor research: Lychee and CHITUBOX

Competitive analysis compiled 2026-09-07. It explains *why* VoxelMill works the
way it does and where it stands against the two dominant resin slicers. This is
reference material: the open feature backlog that came from this research lives
in [ISSUES.md](../ISSUES.md) (section "Feature backlog", keeping its original
IDs A1–I4 and Diff/Imp scores). The full scored list and the per-item design notes
are in git history: `git show 92ea91a:nextsteps.md`.

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

## 1. What voxelmill already does that neither Lychee nor CHITUBOX does

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

## 2. Direct answer: what they do that voxelmill does not

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


## 3. Where this ends up

If the feature backlog in [ISSUES.md](../ISSUES.md) is completed, the comparison looks like this.

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

## 4. Sources and confidence

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
  are directional, which is why [performance.md](performance.md#research-where-the-speed-comes-from) recommends measuring before optimizing.

Full research notes, with inline citations, were kept in a session scratchpad and are not in this repository:
`lychee.md`, `chitubox.md`, `complaints.md`, `perf-ux.md`.
