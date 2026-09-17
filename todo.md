# Implementation ledger

## Restart here — 2026-09-11

Baseline this round: 872 passed, 13 skipped. Finished at 990 passed,
13 skipped.

### Naming and language sweep
- [x] Project archive extension `.chop` -> `.voxmil` across code, tests, docs,
      schemas and packaging. No backwards compatibility; `.chop` is simply gone.
      Historical evidence (`reports/**`, `docs/implementation-history-*.md`)
      is left unedited.
- [x] Save dialogs append the extension when the typed name has none.
- [x] British -> US spelling everywhere: centre/center, colour/color,
      normalise/normalize, analyse/analyze, behaviour/behavior, grey/gray.

### Project fidelity
- [x] Reopened projects show original file names, not the SHA-256 stem of the
      extracted mesh.
- [x] Report tab populates for a Check islands run (it only set the badge).

### Print checks
- [x] Check print submenu: islands, enclosed voids, overhangs, suction cups,
      drainage; plus Check all and Check some (checkbox dialog, OK/Cancel).
- [x] Layer view: jump to previous/next layer carrying an issue.
- [x] Layer view: issue pixels colored per issue type.
- [x] View menu toggles per issue type, all enabled by default.

### Attachments
- [x] Attachment placement prevents islands by construction: detect, place an
      attachment under each island, re-check locally, then one full pass.
      `attachments.max_island_passes` preference, default 5.
- [x] Progress line naming the stage during Compute attachments.

### Object manipulation
- [x] FreeCAD-style translate/rotate gizmo (3 arrows, 3 rings), shown only
      while a part is selected in the 3D view and hidden on a click-away.
- [x] Relative / absolute motion mode, absolute measured from the import pose.
- [x] Live 3D preview while a slider drags; rebuild only on release.
- [x] Per-part scale and mirror controls in the object panel.
- [x] Drop to plate (zero the lift against the current rotated low point).
- [x] Zoom to selected.
- [x] Multi-select in the object list; moves apply to every selected part.
- [x] Arrow-key nudge using the snap increment.
- [x] Per-object auto-orient.

### Session state
- [x] Remember window geometry and dock layout between sessions.

### Gizmo and rotation defects (2026-09-11, second pass)
- [x] Gizmo observers set VTK's abort flag, so a drag no longer fights the
      trackball camera underneath it.
- [x] Press-hold-drag-release: a move with the button up ends the drag rather
      than tracking on, and Escape or a right-click cancels it outright.
- [x] Ring drags accumulate each frame's wrapped increment, so a full turn
      reads as 360 rather than 0 and 1.5 turns as 540.
- [x] Rotation wraps into (-180, 180] instead of clamping. In-range angles
      pass through bit-for-bit so an applied orientation candidate stays exact.
- [x] Every long-running operation clears outstanding preview transforms
      first. An abandoned drag used to leave the actor showing a rotation the
      document never accepted, which is what made supports look like they
      ignored the rotation.
- [x] Confirmed by measurement that the router itself always honored rotation.

### Close-out
- [x] Tests for every feature above.
- [x] Documentation rewritten against the code.

## Restart here — 2026-09-10

The program is now **VoxelMill** (`voxelmill` for every command, module, path,
environment variable and icon basename; `VoxelMill` only as the displayed
product name). The old name survives only in historical evidence:
`reports/**` and `docs/implementation-history-2026-09-09.md` record runs that
really executed as `chopchop` and were deliberately left unedited.

AppImage packaging is in this round. Physical printing is still blocked on FEP
replacement; read-only status, history, and camera checks are allowed. Do not
start, upload, move, or print.

- [x] Rename chopchop -> voxelmill across code, tests, scripts, docs and
      packaging. `ChopError` is `VoxelMillError`; `CHOPCHOP_*` is `VOXELMILL_*`;
      the CUDA/TBB compile definitions and CUDA C symbols are `VOXELMILL_*` /
      `voxelmill_cuda_*`. No compatibility aliases: the old console script,
      environment variables and `~/.config/chopchop` are gone. Suite is
      unchanged at 757 passed / 13 skipped, CUDA still compiles and reports one
      device, and the packed editor AppImage runs.
- [x] Editor placement rework. Object panel replaces the added-objects combo:
      every part with its role, attachment state and visibility, plus move and
      rotate rows whose slider and absolute field are two views of one number.
      Rotation snaps when dragged or nudged and is exact when typed; the
      increment is an editor preference (default 5 deg) in
      `~/.config/voxelmill/editor.json`, not in the settings table.
- [x] Attachments are an explicit editor step: nothing on import, File >
      Compute attachments (Ctrl+R), and each row reads none/stale/routed.
      Moving a part in XY marks them stale; Z or rotation drops them and says
      why. The CLI default is untouched.
- [x] Multiple parts: Duplicate (N copies, reusing the loaded mesh), Arrange
      (Ctrl+L) over a new deterministic packer in `arrange.py` that refuses
      rather than overlapping and centres the packed group, drag-and-drop STL
      onto the window, and per-part attachment settings controls beside the
      overlay JSON.
- [x] Tool strip over the 3D view (Select / Add point / Remove point / Paint
      enforced / Paint blocked). The Shift/Ctrl/Alt modifiers still work.
- [x] Layer view: vertical bottom-to-top layer slider, zoom slider, wheel for
      layers, Ctrl+wheel zoom about the cursor, drag to pan, and a per-pixel
      black gutter at 5x and above. Only the visible crop is rendered, so cost
      is bounded by the viewport rather than the zoom factor.
- [x] Paint is one record per plate object in that object's own frame, so it
      travels with the part. `.chop` schema is 2; schema 1 is refused rather
      than converted because its marks carry no part attribution.
- [x] `voxelmill prepare project.chop` restores added parts. They were the one
      edit the command dropped, so a multi-part plate silently prepared as its
      primary part; `--project` now records `edits.extra_models` and the
      archive embeds each mesh.
- [x] Documentation swept against the code for this round: gui, architecture,
      cli, configuration, and the gotchas log.

## Restart here — 2026-09-09

Do not print: the FEP film needs replacement. Read-only printer status,
attributes, history, and camera access are authorized and have been exercised;
no upload, motion, settings, or print command is allowed.
Keep physical scale/calibration unchanged until measured print results exist.

Prior implementation evidence is preserved in
[the historical ledger](docs/implementation-history-2026-09-09.md). That file
contains superseded checklists; this file is the current work list.

## Review and implementation in progress

- [x] Reproduce Grok baseline: 651 passed, 10 skipped (68.27 s).
- [x] Fix tree support thickness clearance, branch slope and stale support graph.
      Independent geometry/connectivity and obstruction regressions added.
- [x] Multi-object graphical placement, shared supports, portable project meshes,
      model-solid collision checks, CLI `--add-model-spec`, File menu Quit.
      Opening a single STL no longer crashes in `_finish_place`.
- [x] CTB GUI layer access and `slice`/export to CTB. Reader/writer/verify/
      convert for unencrypted v3 are implemented; encrypted and v4/v5 stay
      rejected. Layers-tab source id remains `goo` with `format=ctb`.
- [x] Default CUDA selection with CPU fallback, preferences and CLI
      `--acceleration`/`--cuda-device`. Morphology uses the selected backend;
      native CUDA compiled and one device is available on this machine.
- [x] Original-fixture baseline: 9 sample tests pass (67.20 s).
- [x] Full suite this round: 689 passed, 10 skipped (70.05 s).
- [x] `VOXELMILL_SAMPLES=1`: 9 sample tests passed (69.59 s). xvfb VTK render
      passed. CLI AppImage staged and packed (59 MiB). Full editor AppImage
      packed (406 MiB) with PySide6/VTK; `gui --help` and editor imports
      succeed from the image.
- [x] Clean CMake reconfigure finds nvcc 11.5 when venv pybind11 3.0.4 is on
      `pybind11_DIR`; `cuda_morphology.cu` is on the native link line.

## Remaining software work from plan2

- [x] N13 budget-driven voxel coarsening within the existing deviation limit
      (derived pitch may coarsen up to the no-headroom ceiling; explicit
      `voxel_size_mm` still raises).
- [x] N15 bounded tolerant welding (`repair.weld_tolerance_mm`, default 0,
      cap 0.05 mm, spatial hash). `inspect_mesh` remains exact.
- [x] C8 `voxelmill cap` fills near-planar open boundary loops; non-planar
      holes are refused. Marked q00 skull sample reaches honest
      `cap_incomplete` topology refusal; no fabricated cap is emitted.
- [x] E7 TSMC retract=lift sums and stage-2 speed checks. B4 coverage
      greyscale AA (`process.antialias_levels` 1/2/4). B5 remains uncalibrated.
- [x] F1 threaded label prefetch in `analyze_layers` (memory-capped). F2/F3/F4
      still wait on a prepare-on-original profile.
- [x] C2 `voxelmill hollow`, C4 drain/vent pairs, C9 `voxelmill thickness`,
      C3 grid/hex/gyroid infill.
- [x] G3 typed settings table, G1 Simple/Advanced/Expert, G9 wizard (TTY only),
      G6 History menu, G7 notifications, G8 theme/shortcuts, G11 docks, G10
      autosave recovery (`offer_recovery`).
- [x] A8 resin-embedded `support_presets` consumed by `--support-preset`.
- [x] E8 `voxelmill calibrate exposure|tolerance` (offline GOO + JSON map).
- [ ] Real float-valve correction/orientation acceptance; retain all failing
      validations and source parity evidence without relaxing thresholds.
- [x] Full-resolution skull/temporal-bone GUI coverage (right temporal bone
      and skull q00 render/scrub under isolated XDG cache); bounded/tiled
      full-panel analysis. Original GUI evidence does not claim print
      acceptance or corrected support routing.
- [ ] Synchronize externally published roadmap when its editing tool is available.
- [x] Relocatable AppImage (`scripts/build_appimage.py`). CLI-only (~59 MiB)
      and full editor (~406 MiB packed with PySide6/VTK; double-click opens
      `gui`). Host OpenGL is still required. CUDA is not bundled.
- [x] Per-object `overrides.support` on `--add-model-spec` / added objects;
      contour and open-boundary contact sampling (`--contour-supports`,
      `--boundary-supports`). Face sampling remains the default downward
      lattice. Tree clustering is unchanged.
- [x] Read-only printer monitor (`voxelmill monitor` and File > Printer
      monitor): status/attributes telemetry, RTSP camera with FFmpeg/UDP
      fallback, batched print history, and time-lapse downloads. No upload,
      motion, settings, or print controls are exposed.
- [x] Read-only Cmd 258 storage sweep against the idle Mars 5 Ultra, merged
      into `reports/plan2/printer-monitor-2026-09-10.json`. `storage()` reads
      capacity from the root listing (`/local`, 6.74 GB, 63.8% used) and keeps
      unknown sizes `None`. Recorded firmware behaviour: `/usb/` returns
      unnamed placeholder records when no disk is attached, names carry a
      doubled separator, and `..` in `Url` is not sanitized. Nothing outside
      `/local/` was opened or downloaded.
- [x] AppImage icon package uses the dark full-color design by default, with
      scalable SVG and hicolor PNG sizes; light and symbolic variants remain
      available for appropriate small/icon contexts.

## Needs physical evidence or unavailable specifications

- [ ] Replace FEP before any printing; physical GOO mirroring, support mechanics
      (D6), LED uniformity (E6), printer acceptance (H4), time calibration (I3),
      orientation weight fitting (C10), and a passing float-valve print.
- [ ] B3 additional printer profiles require verified specifications.
- [ ] CHITUBOX spacing, skate elongation, whole-small-pillar maximum length
      and missing transition geometry remain unmeasured; do not invent them.
