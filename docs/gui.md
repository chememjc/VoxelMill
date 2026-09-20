# GUI editor

Start the editor with an optional STL. Supply printer and resin profiles on the
command line when their settings should be the starting point:

```sh
.venv/bin/voxelmill gui --printer profiles/mars5-ultra.ptr \
  --resin profiles/sunlu-abs-like-gray.res input.stl
```

**File → Open GOO or CTB for inspection...** loads a finished `.goo` or
unencrypted CTB v3 into the Layers tab so the decoded pixels can be scrubbed
without a source mesh. **File → Export CTB v3...** slices through the same
verified GOO path and converts. Encrypted CTB and v4/v5 are refused.

**File → Open project...**, **Save project** / **Save project...**, and
**Save project as...** read and write `.voxmil` archives; no other project
file extension is recognized. **Save project** overwrites the known path
when one exists (label without ellipsis); without a path it falls through to
**Save project as...**. Dirty **New project**, **Open**, or **Quit** asks
Save / Discard / Cancel; headless discards. The window title shows the
project file name and a modified flag. Save/export dialogs append the right
extension whenever the typed filename has none of its own — a name already
ending in an extension, even a different one chosen on purpose, is left
exactly as typed. For the plain **Export supported STL...** dialog, the
extension added is whichever format filter was selected (STL, GOO or CTB),
not always `.stl`.

## Menus

| Menu | Items |
| --- | --- |
| **File** | New project; Open STL; Import STEP; Open project; Save project (or Save project... with no path); Save project as; Open GOO/CTB inspect; Close slice file; Export supported STL / Elegoo GOO / CTB v3; Quit |
| **Edit** | Undo; Redo; History |
| **Parts** | Add model; Compute attachments; Arrange on plate; Measure STL; Allow part-to-part supports (checkable); Reset all parts to this lift; Support presets submenu |
| **Verification** | Island checks; print checks (islands, enclosed voids, overhangs, suction cups, drainage, Check all, Check some...); Verify GOO/CTB; Inspect STL; Validate STL |
| **Configuration** | Profile library; Printer / Resin / Support editors; Preferences; Theme; Motion; Shortcuts; Allow warned export |
| **Tasks** | Printer monitor; Cancel running job; Run operation |
| **View** | Named camera views, Fit to scene, issue navigation, layout reset (unchanged) |

Primary part-to-part supports are disabled by default. The Parts checkbox
enables model anchors for primary routing only; brace networks always require
a continuous support-only path to the plate or generated base.

Reopening a `.voxmil` project shows each part's original file name in the
object list, not the SHA-256 stem of its extracted mesh. The name travels in
the project manifest; only the file on disk is hash-named, because the
archive keeps working after the original path disappears.

The editor prepares one part at a time. Its left side is a VTK view of the
model, routed supports, raft, contacts, and build volume. A vertical two-handle
Z-clip slider sits on the right of that view (bottom handle is Zmin, top handle
is Zmax, **All** or a double-click shows everything), the same idea as Chitubox
and PrusaSlicer. The front bottom
edge of the build volume (−Y, Z=0) is green; the other eleven edges are red. Its Setup tab holds
the commonly tuned orientation, process, support, and repair values. The **All
resolved settings (JSON)** box exposes every value resolved from the printer
and resin profiles, including printer dimensions, image mirroring, repair
resolution, all support dimensions, resources, and GOO motion values. The JSON
is validated before a rebuild begins; it is the complete manual override
surface when a compact control is not appropriate. A read-only **exposure
schedule s** row below it shows the same schedule `voxelmill profile` prints,
computed by the same `layer_exposure`: bottom layers, the transition ramp,
and the first normal value, then an ellipsis. Transition values are
intermediate and exclude both endpoints.

The automatic controls are deliberate choices, never hidden fallbacks:

| Decision | Automatic mode | Manual mode |
| --- | --- | --- |
| Orientation | Enable **automatic orientation search**. | Clear it and enter RX, RY, RZ, center offset, and lift. |
| Contact selection | **Compute attachments** (Ctrl+R), which enables **automatic support contacts** for the document. | Clear that box to route only the points placed by hand with the Add point tool or a Shift-click. |
| Bracing | `support.auto_bracing=true` in resolved settings. | Set it to `false` in resolved settings. |
| Repair | Choose `none`, `conservative`, or `aggressive`. | Set repair pitch, smoothing, cavity and feature options in resolved settings. |

When automatic orientation search completes, Setup shows its ranked candidates.
Selecting one displays the score terms, assessment status, and whether the
ranking used the full assessment or the cheap fallback. The weights are
uncalibrated guidance and sampled terms are labeled; the full-resolution
bounds remain authoritative for fit. **Apply selected orientation** records
the candidate's exact RX/RY/RZ angles as an undoable manual orientation and
rebuilds the live preview. The ranked list remains available for comparing
other candidates, and is cleared when the source, settings, offset, lift,
scale, or mirror changes.

**Paint mode** (Setup: off / block / enforce, or the Paint blocked and Paint
enforced tools) is a manual brush. Block never places an automatic contact on
the painted faces; enforce always does. Island births are never blocked,
because an unsupported island will not print. Click and drag on the model;
blocked faces are magenta and enforced faces are green on the same model
actor. Nothing is painted automatically.

Paint belongs to the part it was drawn on, not to the plate. A stroke is
attributed to whichever part the brush landed on, and every mark is stored in
that part's own mesh frame, so moving or rotating a painted part carries its
paint with it instead of leaving it behind on the faces it no longer covers.
**Clear blocked on this part** and **Clear enforced on this part** do exactly
that: the selected part only. This is what the `.voxmil` schema-2 bump records;
a schema-1 project is refused rather than converted, because its
plate-coordinate marks cannot be attributed to a part after the fact. **Parts → Add model...** (also the object panel's
**Add...** button) places one or more further STLs on the plate; overlapping
support envelopes are allowed, intersecting model solids are not, and
supports are planned on the combined field so they can anchor between parts.
The **attachments for the selected part** controls under the object list set
the common `support.*` values per part: the first part has no overlay, so
those controls are the plate values themselves, while an added part inherits
every plate value until **override the plate attachment settings** is ticked.
Only the values that actually differ are stamped into the overlay, so the
rest stay inherited rather than frozen at today's plate values, and unticking
the box clears the overlay rather than copying the plate into it. The
**support overlay JSON** field beside them reaches every other `support.*`
key, which is the same split the Setup tab makes between compact controls and
the complete resolved-settings box; the collision field stays shared.
Clicking a part in the 3D view selects it, the same selection the object
panel's list shows. **File → Quit** closes the editor. **Configuration → Preferences...**
chooses `resources.acceleration` (`auto`/`cpu`/`cuda`), the CUDA device,
workers, the memory ceiling, and the rotation snap increment described under
[The object panel](#the-object-panel-move-rotate-scale-and-mirror) below. Auto
uses CUDA only after a successful runtime and device probe; an explicit
`cuda` choice is refused when no device is available.

## The 3D-view gizmo

A part shows a translate/rotate manipulator only while it is selected — opening
an STL never drops a manipulator on the model. It is built from plain VTK
actors rather than `vtkBoxWidget`, in the shape FreeCAD and Fusion use: three
colored arrows along X/Y/Z for translation, and three colored rings for
rotation about each axis, sized to the selected part and centered on it.
Hovering a handle brightens it and dims the rest, so the axis under the
cursor is unambiguous.

Dragging a handle moves or rotates the part live — the same preview path a
Move/Rotate slider drag uses — and commits the delta once, on release, as one
undoable edit. A translated part's gizmo stays anchored to its new position
so the next drag starts from there. Every dragged rotation is rounded to the
editor's rotation snap increment; a typed value in the object panel is taken
exactly. Clicking empty space (neither the gizmo nor a model actor) dismisses
the gizmo and clears the selection, the same as clicking away from a
selection in FreeCAD. Clicking the navigation cube is always a camera
command and is checked first, so it can never be mistaken for a gizmo drag.

## Model-editing tools

A tool strip sits above the 3D view. **Select** is the default and leaves a
click to camera and part selection. **Add point** places an attachment point
where you click the model, **Remove point** suppresses the displayed point you
click, and **Paint enforced** and **Paint blocked** are brushes with a radius
in millimeters. These were previously reachable only as Shift-, Ctrl- and
Alt-clicks, which meant nothing on screen said they existed; the modifiers
still work exactly as before whatever tool is selected, and the strip and the
Setup tab's paint mode are one choice shown in two places.

## The object panel: Move, Rotate, Scale and Mirror

The object panel lists every part on the plate: its name, whether it is the
**primary** or **added N**, its attachment state (`none`, `stale`, or
`routed`), and a check box that hides a part in the 3D view only — a hidden
part is still placed, still checked, and still exported. Selecting a row
selects the same part a click in the 3D view would. The list allows selecting
several rows at once (Shift/Ctrl-click); a Move or Rotate edit made with more
than one row selected applies to every selected part, each keeping its
position relative to the others, exactly as a multi-part drag in the 3D view
would. A Scale or Mirror edit with several rows selected sets every selected
part to the same absolute scale and mirror rather than the same delta —
there is no meaningful "delta" for a multiplier.

Move X/Y/Z and Rotate X/Y/Z each have a slider and an absolute field that are
two views of the same number, clamped to the build envelope; typing a value
sets it exactly. The rotate rows also have -45/-snap/+snap/+45 nudge buttons,
and the Move rows have nudge buttons sized to the arrow-key step described
below. Dragging the part in the 3D view (the gizmo) or a slider writes the
same numbers, live: every step of a slider drag moves the part on screen
immediately, and the edit commits once, as a single undoable command, when
the drag ends — there is no debounce delay between letting go and the commit
landing. Every dragged rotation is rounded to the nearest multiple of the
rotation snap increment, whether it was dragged on the axis slider or on the
object itself; a typed angle is taken exactly, because someone who types 37.5
means 37.5. The rotation snap increment is set in Configuration → Preferences (default
5 degrees, "off" disables it) and lives in `~/.config/voxelmill/editor.json`,
not in the settings table: it changes nothing about the output, so it must
not travel inside a printer profile or a `.voxmil` project.

Arrow keys nudge the selected part(s) in the plate's XY: Left/Right move X,
Up/Down move Y, PageUp/PageDown move Z, by the object panel's translate step
(1 mm; Shift nudges by ten times that). Unlike the rotation snap increment,
this step is not currently exposed in Configuration → Preferences. The nudge is
ignored while a spin box has focus, so typing a number is never interrupted
by that number's own arrow keys.

`Configuration → Motion` chooses how a gizmo drag or an arrow-key nudge is interpreted:
**Relative to current pose** (the default) lands the edit on top of wherever
the part already is, and **Absolute from import pose** measures it from the
part's pose at import instead (no rotation, centered, 5 mm lift) — so the
same drag always ends up in the same place regardless of where the part
started. In absolute mode the object panel's fields read as an offset from
the import pose rather than as the pose itself: a field reading 0 means
"still at the import pose." This is a per-person editor preference, stored
next to the rotation snap increment, not a project or profile setting.

Scale X/Y/Z and the Mirror X/Y/Z boxes here are per-part: they apply to
whichever row is selected, including the primary part, and are the same
`voxelmill prepare --scale`/`--mirror` factors and the same guardrails — the
spin boxes are range-limited to `[MIN_SCALE, MAX_SCALE]` (0.01 to 100.0) and
a nonpositive or out-of-range factor is refused with `invalid_scale`. **Reset
scale** sets X/Y/Z back to 1. **Uniform** keeps the three scale axes equal;
editing one sets the other two to match. See
[geometry.md](geometry.md#scale-and-mirror) for why mirroring reverses
triangle winding. The Setup tab (below) also carries scale and mirror rows,
but only for the primary part, applied through **Apply and rebuild**; the two
controls edit the same document state, so either one shows the other's
result.

**Drop to plate** zeros the selected part's lift, resting it on the bed. The
lift is already defined as the height of the part's lowest point in its
current rotation, so dropping to plate is exactly setting that number to
zero — there is no separate geometry computation. The Setup tab's **model
lift** is the plate floor: the minimum of every part's `lift_mm`. Changing
it shifts every part by the same delta and commits immediately on the
spinbox. **Parts → Reset all parts to this lift** writes that value onto
every part, collapsing relative gaps. **Zoom to selected** frames
the selected part(s) in the 3D view, the same way `Fit to scene` frames
everything. **Auto-orient** runs the orientation search on the current part
alone, searching on top of its *placed* geometry (current rotation, scale,
mirror and position) rather than its raw import pose, and composes the
result back into an absolute rotation. This is a different search from the
Setup tab's plate-wide **automatic orientation search**: it is scoped to one
already-placed part among several, using the only geometry available for a
part that is not alone on the plate.

**Duplicate** copies the selected part, reusing the mesh already loaded, N
copies at a time; because copies land exactly on their original, duplicating
always arranges the plate afterwards. **Remove** drops an added part; the
primary cannot be removed this way. **Arrange** (also Parts → Arrange on
plate, Ctrl+L) is a deterministic bottom-left packer that lays every part out
without overlap inside the build envelope less the edge clearance, using the
support spacing as the gap between parts; it refuses with `arrange_no_fit`
and changes nothing rather than returning an overlapping layout. The packed
group is then centered on the plate, because a corner fill is the right pack
and the wrong presentation. STL files can be dropped on the window or the
object panel; a drop onto an empty editor opens the first file, and every
dropped part lands at the plate center and the plate is then arranged.
**Add...** takes the same route and accepts several files at once. It does
not ask where to put them: guessing a center offset for a part that has not
been loaded yet is what made adding a second part a game of trial and error,
so the packer places them and they can then be moved like anything else.

Nothing is attached on import: the editor forces `support.automatic` off for
its own document at construction, regardless of the profile's resolved
value, so a part can be positioned before anything is routed to it. This is
an editor-only default — the command line is unchanged, `voxelmill prepare`
still routes supports automatically, and `support.automatic` still defaults
to true in resolved settings. **Parts → Compute attachments** (Ctrl+R), or the
**Compute attachments** button in the object panel, routes supports for the
plate as it stands. It runs the same island-correction loop `voxelmill
prepare` runs (see
[Islands and the correction loop](#islands-and-the-correction-loop) below):
route, assemble, scan, and if the scan finds islands, add contacts under them
and repeat, up to `support.max_island_passes` (default 5, range 1-10) passes.
Every row then reads `attachments: routed`.

Moving a part sideways (X/Y) marks attachments `stale` rather than dropping
them, because sliding it across the plate changes neither which of its faces
point down nor how far a support must reach. Raising or lowering it (Z), or
rotating it, drops them back to `none` instead, because changing its height
changes every support length and rotating it changes which faces need
supporting at all — neither can be salvaged by translating what was already
routed. Scaling or mirroring a part also drops its attachments to `none`: a
resized or mirrored part is a different shape from whatever was routed for
it. Arranging a `routed` plate also marks it `stale`, since arranging only
moves parts in X/Y.

A `.voxmil` project embeds extra-model meshes as `source/models/N.stl` next to
the primary `source/original.stl`, so the archive is portable after the
original paths disappear. Schema-1 projects that only recorded external extra
paths still open through those paths.

Shift-click a model surface to add a support contact. Ctrl-click a displayed
contact to suppress it across future automatic passes. Alt-click a displayed
contact to move it. The Setup tab's **per-contact support parameters** list
selects one or many contacts; **Apply selected** writes only the fields that
were touched, so a batch diameter change does not stamp every other default
onto the selection. **Copy first** / **Paste selected** copies the effective
geometry (global defaults plus any override) from the first selected contact;
**Reset** drops the override so that contact uses the global settings again.
Moving or deleting a contact takes its override with it. Every edit has Undo
and Redo. Edits, settings, source hash, support graph, per-contact parameters,
and historical validation can be stored in a `.voxmil` project. A loaded
historical validation is shown as history; the editor always rebuilds and
revalidates before it exports.

Configuration → Printer editor..., Resin editor..., and Support editor... open
dedicated validated editors for those sections. Printer saves are hardware-only
`.ptr` profiles; resin saves include the current printer's process and support
blocks; support saves are portable JSON presets. Load restores a draft, Save as
writes the corresponding file, and Apply to project changes the document through
its undo stack. Closing an editor discards unapplied edits. The resin loader
resolves its process against the actual current printer, including its id and
layer-height limits, so a resin bound to a custom printer is checked in the
right context.
Apply immediately invalidates earlier project jobs and starts a rebuild, even
while the parameter window remains open. Printer settings show the build volume
with its green front edge; all fields name their exact CLI configuration key in
a tooltip.

The Support editor shows a four-contact floating-beam illustration from the
production router. Its height control is 3–160 mm, and changing a setting
invalidates the previous preview before an asynchronous replacement job is
submitted. The status reports routed contacts, model anchors, generated
downward braces, and braces rejected by model collision checks. Braces begin
at the full-width shoulder below each tip taper, travel at 45°, and never
anchor on model parts. Export example STL
writes this illustrative geometry only; it has no print validation or strength
claim. Cancellation and stale-job results are discarded.
The shared **base type** choices are `plate`, `none`, `pad`, `skate`,
`skeleton`, `grid`, `hex`, and `triangle`. Skate length includes its rounded
ends and zero derives a circular footprint from the touch diameter. Skeleton
uses a deterministic minimum-spanning tree; triangle instead connects the
feet with a Delaunay triangulation and a perimeter rim, leaving open
triangular bays; grid adds rotated orthogonal strips, a perimeter rim, and
foot tethers; hex adds a honeycomb on the same pitch.
**Base edge slope** tapers the base inward from the plate so it is widest where
it touches, in whole printed layers, and `0` keeps the vertical wall. The
editor keeps mode-specific values when changing modes, while validation still
rejects touch diameter, thickness or edge slope on `plate` and `none`. New fields show their exact `support.*` keys and derivation rules in
tooltips; geometry measurements do not claim print strength.
The fixture accepts support spacing from 1–30 mm to bound preview work; other
valid project spacing remains editable and saveable, with the unavailable
example explained in the status line. Four contacts are fixed for comparison,
so automatic contact selection and coverage parameters are exercised on the
project rather than by this fixture.

Model-anchor controls are independent from the top contact: `model_anchor_shape`
selects a cone or cylinder for the bottom connector,
`model_anchor_length_mm` sets its length (default 2 mm; zero preserves the
direct anchor route), `model_anchor_diameter_mm` sets its diameter (default
0.4 mm; zero derives the middle pillar diameter), and
`model_anchor_penetration_mm` sets its penetration below the sampled surface
(default 0.15 mm). Part-to-part routes are point-to-point (balls at both ends);
short gaps do not swell to `pillar_diameter_mm`. Each field is available in the
support editor with the same `support.*` key and derivation rule shown in its
tooltip.

The small-pillar controls similarly expose the CHITUBOX-style model connector
choice: `small_pillar_mode` selects middle or model segments, `small_pillar_shape`
selects the buried end shape, and the upper/lower depth fields set penetration
when model mode is selected. The model mode threshold remains explicit through
`small_pillar_max_length_mm`; zero disables the small class rather than
silently applying it to every connector.

## Islands and the correction loop

**Compute attachments** and `voxelmill prepare` share one island-correction
loop (`island_guard.route_without_islands`), so the editor and the command
line can never settle for a different plate given the same geometry and
settings. It routes, assembles, and scans for islands; if the scan finds
any, their positions are fed back in as extra contacts and the loop routes
again. This repeats until a scan finds nothing, a pass makes no further
progress, or `support.max_island_passes` passes have run.

The first and last scans always cover the whole build. A pass in between
scans only a padded crop around the pillars the previous pass just added,
because that is far cheaper than relabeling the whole panel on every pass —
but a cropped scan cannot tell a component that was cut off by the crop edge
from a real island, so any component touching the crop edge is discarded
rather than counted. The verdict always comes from a full scan: if a cropped
pass happens to look clean, one more whole-build scan confirms it before the
loop reports success.

When passes run out with islands still present, that is reported honestly as
`correction_incomplete` with the remaining count and their positions, not
presented as a routing that worked. `support.max_island_passes` (default 5,
range 1-10) caps how many passes may run; each pass is a full route plus an
assembly, so the cap is also a time limit.

## Verification

The **Verification** menu runs island and print checks over the current
in-memory assembly, without writing anything: **Check islands now**,
**Re-check islands after every edit**, **Islands**, **Enclosed voids**,
**Overhangs**, **Suction cups**, **Drainage**, **Check all**, and
**Check some...** (a checkbox dialog listing the print checks, all checked by
default), then **Verify GOO or CTB (deep check)...**, **Inspect STL...**, and
**Validate STL...**. Results replace the Report dock's diagnostics and jump the
layer view's diagnostic filter to the codes just found. None of these is an
export decision — **Export supported STL** and **Export Elegoo GOO** always run
the full validation regardless of what Verification last found, the same way a
green island badge is never an export gate.

Each check reuses the exact analysis its dedicated path already runs, so a
result here can never disagree with the equivalent CLI command or the export
validation:

| Menu item | What it runs | What it does and does not establish |
| --- | --- | --- |
| Islands | The same `analyze_layers` pass and `island_summary` the island badge uses. | Raster layer connectivity only — see [Island badge](#island-badge) below. |
| Enclosed voids | The same layer pass, with void tracking turned on. | Enclosed empty space in the raster, not whether it can drain. |
| Overhangs | `overhangs.apply_overhang_check` — see below. | A configured-distance coverage check, not a strength or sag proof. |
| Suction cups | `peel.apply_peel_check`, the same advisory as `validate`'s `peel_risk`. | An uncalibrated geometric screen for broad flat undersides; see [algorithms.md](algorithms.md#surface-peel-risk-advisory). |
| Drainage | `validation.analyze_drainage`/`drainage_check`, exactly as export validation computes it. | Circular-equivalent path clearance on the analysis grid, not a physical drain test. |

**Overhangs** is the new `unsupported_overhangs` check
(`src/voxelmill/overhangs.py`): every downward-facing sample the router itself
would look at is checked against the routed contacts, and one that has no
contact within `support.spacing_mm` is flagged. It is a **warning**, never an
export gate — `unsupported_overhangs` can read `warn` and the export still
proceeds. The match radius is the configured support spacing, so a sparse
support field weakens this check rather than failing it: fewer contacts mean
a larger gap has to open up before a sample counts as unsupported, not that
the check gets stricter as coverage thins out. It is also the CLI's
`--no-overhang-check` flag on `prepare`; see [cli.md](cli.md).

## Layer view: issues

Diagnostic markers in the Layers tab are colored per diagnostic code, from a
fixed, colorblind-safe palette, so an island birth and a drainage bottleneck
read as different things at a glance rather than as one undifferentiated red
dot; a legend under the view lists the codes actually drawn on the current
layer with their swatches. A thin **issue strip** beside the layer slider
marks every layer that carries an issue, drawn the same colors, so scrubbing
past a problem is visible before the slider gets there. Buttons under the
view, **Prev issue** and **Next issue**, jump to the nearest layer below or
above the current one that still carries a *visible* issue; `View → Next
issue layer` and `View → Previous issue layer` (Ctrl+Shift+] and
Ctrl+Shift+[) do the same from anywhere in the window.

`View → Show issues` is a submenu with one checkable action per diagnostic
type (Islands, Enclosed voids, Transient traps, Growth span, Unsupported
overhangs, Suction cups, Drainage bottlenecks), all on by default. Clearing
one hides its markers in the Layers tab, removes it from the issue strip, and
excludes it from what Prev/Next issue jump between — it does not remove the
finding from the Report dock. A diagnostic code the menu does not yet name
(a check added to the pipeline later) still gets a usable label and starts
visible, rather than being silently unrepresented.

## Scale, mirror and the measurement row

The Setup tab's **scale X Y Z** and **mirror axes** rows are the same
per-axis transform `voxelmill prepare --scale`/`--mirror` take, and the same
guardrails apply: the scale spin boxes are range-limited to `[MIN_SCALE,
MAX_SCALE]` and a request outside that or a nonpositive factor is refused
with `invalid_scale`, leaving the document unchanged rather than clamping
silently. They apply to the primary part only — see
[The object panel](#the-object-panel-move-rotate-scale-and-mirror) above for
the per-part controls that also cover added parts and multi-select. See
[geometry.md](geometry.md#scale-and-mirror) for why mirroring reverses
triangle winding and why the scale range exists.

The **measured size** row is read from the document's real placement
matrix, so it already reflects rotation, scale and mirror together, not
just the raw scale factors — it is the same number `voxelmill measure` would
report for this pose. It reads **no placement yet** before anything has
been placed, rather than showing a stale or zero size. Once a transform is
applied it also shows the one-line hazard note (`scale_note`) that would
appear as a report warning, e.g. `1.60 x 1.60 x 0.80 mm (mirrored on X)`.

Applying the Setup tab (`apply_settings`) issues three separate undoable
commands in sequence — **settings**, then **transform**, then
**orientation** — because each edits a different part of the document and
each has to be independently revertible. A consequence worth knowing:
reverting only a scale or mirror change made through the Setup tab takes
**two** undos, not one, because the orientation command pushed after it
sits on top of the stack.

**Parts → Measure STL...** runs `pipeline.measure_stl`, the same function
`voxelmill measure` calls, on a chosen file (or the currently open model),
using the document's own rotation (substituting an unrotated pose when the
document's orientation is set to automatic, since `measure` refuses
`auto`), center offset, lift, scale and mirror. It writes nothing and does
not touch the open document. The result goes to the Report tab as JSON, and
the status bar shows the placed size, whether it fits, and the transform
note when there is one.

The Layers tab renders one printer-pitch layer at a time. Its layer slider is
vertical (layer 0 at the plate) and the zoom slider is horizontal; Ctrl+wheel
zooms, a plain wheel steps one layer. Its diagnostic filter selects marker
categories. The Faults tab reuses the same left-hand scene (no second VTK
window) and the same layer job pipeline: it shows a 2D layer image with
per-class colored markers, the same vertical layer slider and zoom slider as
Layers, a visible color key with class toggles, and Z clipping that is shared
with the 3D view. **Show all** next to the Z clipping header clears the clip.
Fault glyphs in the 3D view use the same colors and are non-pickable so they
cannot steal support placement. Leaving the tab clears the fault actor but
keeps the Z clip the 3D slider already had. The Report tab has the full JSON
report and a selectable diagnostics list; double-clicking a diagnostic opens
the Layers tab at that layer. An automatic island scan after a move or rotate
fills the report without switching to that tab; **Check islands now** still
does.

Spin boxes and combo boxes ignore the mouse wheel until they have been clicked
(they have keyboard focus). The filter walks parents so a wheel that lands on
the inner `QLineEdit` (Cocoa) is still ignored. Hover-scrolling a tall Setup
page must not change values.

Every expensive stage runs in a background job. A changed document cancels the
obsolete generation and discards results that raced with the new decision. Esc
cancels running work. The displayed mesh can be decimated solely for VTK
responsiveness; the view states this explicitly and validation uses every
triangle.

The opt-in original-fixture GUI coverage launches a fresh `xvfb-run` process
with isolated `XDG_CACHE_HOME`, opens the complete right temporal-bone and skull
q00 triangle streams with repair and automatic supports disabled, renders the
viewport, and scrubs layer 0, a middle layer, the final layer, and back to 0.
It verifies that the displayed union retains the complete source triangle count.
This is GUI/layer access evidence; it does not claim corrected support routing
or physical print acceptance for those originals.

The CLI and editor share exact/raster ingestion and assembly. Conservative
repair now falls back to grouped raster occupancy when the solid gates reject a
mesh; `none` bypasses those gates. Validation and saved project diagnostics state
which path was used. On the raster path the Layers view ORs the model and
generated support masks independently, and export checks that the reopened STL
matches those pixels. Cavity filling remains unavailable without an exact solid.

## Setup tab: config keys, CLI flags, and the modified baseline

`MainWindow.SETTING_KEYS` names twelve compact Setup controls — layer height,
bottom and normal exposure, bottom and transition layer counts, support
spacing, overhang angle, automatic support contacts, repair mode, seal
cavities, minimum orifice area, and clip-to-build-volume — and for each one
its resolved-settings section and key and, for most of them, a dedicated CLI
flag. Each control's tooltip states its `section.key` and how to set it from
the command line: with the dedicated flag when one exists (e.g.
`--support-spacing-mm`), or with `--set section.key=VALUE` always. Bottom
exposure, normal exposure, bottom layers, and transition layers have no
dedicated flag and are reachable only through `--set`. A test resolves every
named key against `voxelmill.config.DEFAULTS` and every named flag against the
argument parsers `voxelmill.gui.operations.operation_parsers` builds, so a
tooltip cannot name a config key that does not exist or a flag the CLI does
not accept. The **scale X Y Z**, **mirror axes**, rotation, center offset,
and lift controls are not part of this set — they are placement decisions
for this part, not profile settings, and carry their own tooltips instead.

Beside each of the twelve controls sits a dot and a circular-arrow revert
button. The dot appears only when the control's current value differs from
`Document.baseline_settings` — the settings the printer/resin profile stack
resolved to before any editor edit — and its tooltip names both the old and
the new value, e.g. `support.spacing_mm was changed from the profile value
3.0 to 5.0.`. A control that matches the baseline shows no dot and its revert
button is disabled.

The baseline is not the same as "whatever the editor opened with" once a
profile has been applied: **Configuration → Profile library… → Apply to editor**
calls `Document.adopt_baseline`, so after that a dot means changed from the
profile just chosen, not from the settings the editor started with.
Applying the Setup tab itself (`apply_settings`) never moves the baseline,
so a dot placed by an edit stays until reverted or until a profile is
applied again. Opening a new STL or `.voxmil` project also re-baselines,
because each creates a fresh `Document` and a `Document`'s baseline is
always the settings it was constructed with — for a newly opened STL that is
the previous document's current (possibly already-modified) settings, and
for a `.voxmil` project it is the settings recorded in that archive, not a
fresh profile resolution. Either way, "modified" always means "differs from
this document's baseline settings," which is not always "differs from a
named profile."

Reverting one control (the `↺` button, or `revert_setting`) restores
just that setting to its baseline value as one more undoable `settings`
command, then reruns the pipeline the same way **Apply and rebuild** does
when a model is open. A key that the baseline does not have at all — for
example one removed from a hand-edited baseline — raises `invalid_setting`
rather than reverting to a guessed value; this is deliberately a different
outcome from the dot being absent, which means the key is present in the
baseline and currently equal to it.

## Exports

**Export supported STL** writes a private staging STL, reopens it, and runs the
same independent raster, drainage, fit, and support checks as the command-line
preparation path. The requested destination is replaced only after a passing
report. A pre-existing destination is preserved if validation fails or a job
is canceled. **Allow warned export** (Configuration menu) is an explicit per-window override
for a failed report and is labeled as such in the status bar and report.

**Export Elegoo GOO** follows that STL validation, then slices the staged
prepared union and independently checks the decoded GOO pixels and settings
before publishing the `.goo` file.

It needs every one of the 18 machine motion settings under `printer.motion`. The
supplied Mars 5 Ultra profile does provide them, taken from the immutable
reference GOO, so an export succeeds — but they are observed values, not a
hardware-calibrated motion recipe, and their units and tilt-release behavior
remain unverified. Review or override them before a physical print. A profile
missing any of them raises `goo_motion` and the GUI shows that error rather than
inventing a value. `PrintTime` stays zero unless a caller supplies a measured
estimate.

**Tasks → Printer monitor…** opens independently of the current model
and does not require a print task. It can discover or connect to a known SDCP
printer, refresh status and attributes, show release-film/device telemetry,
open the printer-supplied RTSP camera URL (embedded playback or an explicit
FFmpeg/UDP fallback), load print history, and download available time-lapse
videos. The monitor is read-only: it never uploads, starts, pauses, cancels,
moves, or changes printer settings. The same monitor is available from
`voxelmill monitor --host HOST --mainboard-id ID`; `--demo` uses the offline
loopback simulator.

## A model that does not fit

The editor always opens and places a model, whether or not it fits the build
volume and regardless of the `clip_to_build_volume` setting — a part too big
to print is exactly the one somebody needs to see and rotate, not an error
message and an empty viewport. Triangles with any vertex outside the usable
envelope (X/Y against the edge-clearance-reduced panel, Z against 0 and
machine height) are drawn in red (`OUT_OF_BOUNDS_COLOR`, RGB 230, 60, 50) on
the **same actor** as the rest of the model, not a separate one: `handle_pick`
only accepts a support contact on the actor whose `role` is `'model'`, and a
second actor for flagged triangles would silently stop supports from being
placed on them. `Scene.out_of_bounds[role]` counts flagged triangles on the
decimated display geometry, so it can undercount against the full mesh the
same way the preview note already warns about. The status bar labels this as a
display-only count and reports the per-axis overflow in millimeters when
placement does not fit.

This is purely a viewing affordance. Export is unaffected by it: an oversized
export still needs `assembly.clip_to_build_volume` on to proceed at all, and
still needs **Allow warned export** once it does, because clipping records an
error-severity diagnostic.

The Setup tab's **clip geometry to the build volume on export** checkbox binds
`assembly.clip_to_build_volume`. Its tooltip states the same contract as the
CLI flag: off refuses an oversized export outright; on discards geometry the
printer cannot reach and records exactly how much; nothing is ever scaled.

## Navigation cube and camera views

A chamfered orientation cube sits in the top-right corner of the 3D view
(viewport 0.80, 0.76 to 1.0, 1.0), inside a `vtkOrientationMarkerWidget`. It is
built from static polydata — faces, truncated corners, and edges — with
face-centered `vtkVectorText` captions (**Front, Back, Left, Right, Top,
Bottom**). It does not use `vtkAnnotatedCubeActor` or `vtkFeatureEdges`.
**Front is −Y**, the same face the green build-volume edge marks. The widget
itself is display-only (`InteractiveOff`); a click is picked separately and
turned into a camera move. `_on_click` checks the cube first, then the gizmo,
so a click on the cube is always a camera command and never a support edit or
a gizmo drag.

Picking a face means "show me that side": picking the +X face moves the
camera to +X, it does not look toward +X. A corner click selects one of the
eight `iso_*` views. Four FreeCAD-style orbit arrows sit in the marker
viewport (screen space) around the cube. A pick within 0.15 of the cube's
center (by dominant axis component) is refused rather than guessed, since
snapping the camera somewhere the user did not click is worse than doing
nothing. Home iso remains a shallower front-right-top than a true cube-corner
isometric.

The `View` menu and matching shortcuts:

| View | Shortcut | Direction |
| --- | --- | --- |
| Front | Ctrl+1 | −Y |
| Back | Ctrl+2 | +Y |
| Left | Ctrl+3 | −X |
| Right | Ctrl+4 | +X |
| Top | Ctrl+5 | +Z |
| Bottom | Ctrl+6 | −Z |
| Home (isometric) | Ctrl+0 | shallower front-right-top |
| Fit to scene | Ctrl+F | (no direction change) |

Side views use +Z view-up; top and bottom use ±Y, because a view-up parallel
to the view direction leaves roll undefined. Selecting a view orients the
camera, then lets the renderer fit the scene along that direction
(`ResetCamera` moves along the current view vector, so only the framing
changes) — framing always suits whatever is loaded, not a distance guessed in
advance. `Fit to scene` reframes without changing the view direction.

Snapping to a view interpolates over 12 frames at 16 ms: the view direction
is interpolated on the sphere and renormalized each step, so the camera
swings around the model instead of cutting through it. Exactly-opposite views
have no defined interpolation plane and still arrive. The transition always
lands exactly on the named pose and reframes at the end, so it can never stop
almost-but-not-quite square on. `MainWindow.set_view` raises
`VoxelMillError('invalid_view', …)` for an unknown name.

The `voxelmill gui --view {front,back,left,right,top,bottom,iso}` option sets
the initial view on startup; see [cli.md](cli.md).

## Window layout

The window's geometry (position and size) and its dock/toolbar layout persist
across sessions in `~/.config/voxelmill/editor.json`, the same file the
rotation snap increment and the motion mode (relative/absolute) live in —
this is editor preference, not project or profile state. The very first run, or after
**View → Reset layout**, uses the editor's built-in default instead: docks
tabbed together on the right at a width sized to the Setup page's controls,
capped at a share of the window so the 3D view never disappears.
**View → Reset layout** discards the remembered layout, retabs the docks in
their default order and position, and resizes the window to 1400×900 — the
way out for a dock that has been dragged somewhere unusable, without hand-
editing `editor.json`.

## GOO inspection

**File → Open GOO or CTB for inspection…** and **Close opened slice file** load
or dismiss a finished slice file. **Verification → Verify GOO or CTB (deep
check)…** runs exactly `verify_goo`, the same function `voxelmill verify` calls,
so a result reached in the editor is the same result the CLI would report on
that file.

The Layers tab's layer slider is vertical and runs bottom-to-top, so its
travel matches the print: layer 0 is the plate. The horizontal slider beneath
the view is zoom, screen pixels per printer pixel. The mouse wheel over the
view moves one layer at a time; Ctrl+wheel zooms about the cursor; dragging
pans; a **Fit** button shows the whole frame and resumes fitting on resize.
Zoom moves along a fixed ladder rather than scaling continuously: every step
at or above 1:1 is an integer and every step below it is 1/n, so a printer
pixel is always a whole block of screen pixels, or a whole block of printer
pixels is one screen pixel — fractional scaling would blur the one thing this
view exists to show. Only the visible crop is rendered, so the cost of a
paint is bounded by the viewport rather than by the zoom factor. At 5x and
above (`PIXEL_GRID_MIN_SCALE`) each printer pixel is drawn as its own square
with a one-pixel black gutter, so a solid region reads as a grid of separate
exposed pixels rather than one white field; the zoom readout appends `grid`
once this is active.

The Layers tab gains a **Source** selector next to the slider: **Current
assembly** slices the in-memory union at the configured layer height, and an
opened GOO shows the decoded pixels the printer will actually expose. Opening a
file selects it as the source, sets the slider range to its layer count, shows
the file's own embedded small preview as a thumbnail beside the selector, and
switches to the Layers tab. Layer 0 is requested explicitly both when the
Layers tab is first shown and whenever the source changes, because the slider
does not emit a signal at value 0 and the tab would otherwise look dead until
dragged.

When a layer cannot be produced — no GOO open when the GOO source is selected,
no assembly built yet when the union source is selected — the status bar says
why instead of the tab returning silently. A full-panel decoded frame (8520 ×
4320 on the Mars 5 Ultra) is decimated for display the same way a union slice
is, by block maximum rather than striding so a narrow support tip survives the
preview; the info line under the image reports the factor, e.g. `preview 1:4`.

Diagnostic markers from a union validation report are colored per diagnostic
code (see [Layer view: issues](#layer-view-issues) above) and are **not**
drawn over a decoded GOO layer. That report indexes the assembly's layers, not
the opened file's, and drawing its markers on the file's pixels would invent a
correspondence between the two that does not exist.

Decoded and sliced layer payloads are cached in a byte-bounded LRU (default 256
MiB) keyed by source. Rebuilding the assembly drops only the cached
`union` entries; an opened GOO's decoded layers are unaffected and survive it.
A cached layer is redrawn with no background job.

## STL inspection and validation

**Verification → Inspect STL (mesh inventory)…** and **Validate STL (reslice
and check)…** run `pipeline.inspect_stl` and `pipeline.validate_stl`, the same
functions `voxelmill inspect` and `voxelmill validate` call, so a result reached
in the editor is the same result the CLI would report on that file. Neither
writes anything, and neither touches the currently loaded document — the picked
file is independent of whatever is open in the editor.

Results go to the Report tab as JSON, and the validate one also populates the
selectable diagnostic list. The inspect status line reports triangles,
connected components, degenerate triangles, nonmanifold edges, and
self-intersections.

**Parts → Measure STL...** is the same shape of command — writes nothing,
independent of the open document — but answers a size/fit question rather
than a topology or validation one; see
[Scale, mirror and the measurement row](#scale-mirror-and-the-measurement-row)
above.

## Island badge

A permanent label sits in the status bar, always showing one of three
states: **not checked** (nothing has scanned this assembly yet), a count
(**islands: no islands** or **islands: N island(s)**, colored green or red),
or the same count grayed out with **(stale)** appended. It answers one
question — is there unsupported material — and says how old the answer is;
its tooltip names the checks it did not examine.

`services.scan_islands` is `pipeline.scan_islands`'s counterpart for the
editor: it rasterizes `document.derived.union`, the in-memory assembly,
directly, rather than reading a file. It is the identical `analyze_layers`
pass with void tracking off, run through the same `validation.island_summary`
— same analysis, different source of layers, so the badge and a `voxelmill
islands` run on an equivalent exported STL cannot disagree about islands.
Nothing is written and no export decision is taken from the scan; a green
badge is **never** an export gate. **Export supported STL** and **Export
Elegoo GOO** always run the full validation regardless of what the badge
says.

Any edit invalidates the last scan: `mark_islands_stale()` runs at the start
of `reload()` and `rebuild()` — before the pipeline restarts — and grays the
badge out and appends `(stale)` rather than continuing to show a result that
now describes geometry the user has since changed. The grayed color applies
whether the badge is showing `not checked` or a stale count. Once the new
assembly finishes building, `_finish_union` re-earns the badge automatically
when **Re-check islands after every edit** (Verification menu, on by default) is
checked and no export is pending; the toggle can be turned off to scan only
on demand. **Check islands now** (Verification menu, Ctrl+I) runs the scan
immediately, in the background like every other expensive stage, and shows
a status-bar message naming the checks it did not examine when it finishes.
This badge and the **Verification → Islands** menu item run the same
underlying scan; the badge always covers the whole build (it has no
correction loop of its own), while Verification reads the assembly exactly as
it stands at the moment it is invoked.

## Profile library

**Configuration → Profile library…** opens `ProfileLibraryDialog`, the editor's
view onto the same discoverable printer/resin library and provenance/diff/save
machinery `voxelmill profile` and `voxelmill resin` use from the command line —
both sides call the same `voxelmill.profiles` functions, so a profile picked
here resolves identically to the same reference passed to `--printer`/
`--resin`. The dialog lists every discovered profile with its layer and any
shadowed file, and offers **Apply to editor**, **Diff against editor**,
**Provenance**, **Save printer profile…**, **Bind resin to printer…**, and
**Refresh**. Full detail on every button and on the search path, provenance,
and resin-bind semantics behind them is in [profiles.md](profiles.md).

## Testing the editor

The editor splits into a Qt-free `Scene` and a widget, so almost everything —
documents, undo/redo, job staleness, contact hit testing, layer images, export
gating — is exercised headlessly under `QT_QPA_PLATFORM=offscreen`.

Tests that drive real render windows run in a **subprocess under
`xvfb-run`**, and skip cleanly when `xvfb-run` is absent. VTK opens a genuine X
window; under the offscreen platform the server rejects the window id and Xlib
answers `BadWindow` by calling `exit()`, which kills the whole pytest session
rather than failing one test. These tests assert on `vtkWindowToImageFilter`
output rather than on `QWidget.grab()`, because Qt cannot capture VTK's native
child window: the grabbed screenshot shows a black viewport whether or not
anything rendered.

Follow both rules for any new test that touches a render window.

## Complete operation options

**Tasks → Run operation (all options)…** offers every noninteractive CLI
operation and its complete argument form. This includes preparation correction
passes (`--max-passes`, 1–10), component STLs, manual and suppressed contact JSON
files, `.voxmil` project output, analysis switches (including
`--no-overhang-check`), per-operation printer/resin profiles, full-panel GOO
verification, print-time metadata and report paths.
`batch`, `completion` and `manpage` are offered here too, generated from the
same parser as every other operation (`operation_parsers` excludes only `gui`
itself).
Hover an option for its help text. Required fields have an asterisk. Paths with
spaces need quotes only in fields accepting multiple values; `--set` accepts a
JSON array of assignments, for example `["support.spacing_mm=2"]`.

By default a run uses the editor's **applied** settings; preparation also starts
from its pose and manual/suppressed contacts. Explicit form values take priority.
Picking a printer or resin profile resolves settings from those profiles instead
of overlaying the editor settings. Uncheck **Use current editor settings and
support edits** for ordinary CLI defaults. Unapplied Setup edits are not used.

Each run executes the same CLI in a separate process with its resource limits.
The dialog stays responsive, shows progress/errors and the resulting JSON, and
can save the displayed report. Use `--report` to write the complete report
when it exceeds the 16 MiB display limit. Cancel sends an interrupt and waits
for cleanup. Exit 2 means validation failed, with the report retained. The
operation's output does not replace the interactive preview; open its STL or GOO
explicitly to inspect the completed result.

**Parts → Support presets** offers light, medium and heavy starting points,
loading a portable JSON preset and saving the current applied support settings.
Applying a preset is undoable, preserves printer/resin settings and rebuilds
supports. These names describe geometry, not calibrated print strength. See
[support-presets.md](support-presets.md).

Fresh-start tests now cover loading, support construction, switching to Layers
and scrubbing backward in both headless and real VTK windows. The latch sample
regression runs with `VOXELMILL_SAMPLES=1`. Native builds require pybind11 3.0.4;
see [troubleshooting.md](troubleshooting.md) if an older installation crashes.

The assembly preview now also keeps its sorted native triangle indices between
cache misses. Forward scrubs reuse the sweep; backward scrubs reset its cursor
without sorting again. Access is serialized, cancellation resets an interrupted
sweep, and rebuilding drops the indices. Only the latest requested source and
index may paint the preview, including when a cached hit supersedes a running
job. The final partially filled layer remains reachable by the slider.

To automate an edited document, save it as `.voxmil` and run
`voxelmill prepare edited.voxmil --output supported.stl --report prepare.json`.
The CLI restores its settings, pose, contact edits and added parts without
importing Qt; each added mesh travels inside the archive, so a multi-part
plate prepares as the plate it was saved as. Supplying `--add-model` or
`--add-model-spec` replaces the saved parts rather than adding to them; see
[cli.md](cli.md#prepare-an-edited-project-without-the-gui).
The operation dialog accepts a `.voxmil` input too; uncheck its editor-defaults
checkbox to use the chosen archive's own defaults.


Export validation includes a separate `peel_risk` advisory for large, nearly
horizontal downward surfaces. Its report lists region bounds, areas, layer
spans and an uncalibrated score. Edit the top-level `peel` table in resolved
settings to adjust its threshold or disable it. A warning suggests reviewing
orientation and the configured release process; it does not establish a
physical suction force or replace trapped-void and drainage checks.
