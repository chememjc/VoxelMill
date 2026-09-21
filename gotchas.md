# Engineering gotchas

This is a verified lessons log, not a list of hypothetical hazards. Updated 2026-09-20.

- **Bracing must trace support-only grounding.** In 0.5.3, downward branches
  admit only shaft edges reachable from a plate foot without traversing a tip,
  model anchor or bottom connector. A primary part-to-part connection does not
  establish brace grounding. New graph junctions split their destination edges;
  primary elbow edges share one shoulder-based vertical spacing schedule.

- **A candidate cap alone does not bound descending branch origins.** A tiny
  representable spacing on a lone tall support can produce many origins with
  no reachable destination, consuming no candidate attempts. The origin count
  and attempted destinations now each obey the work limit, with cancellation
  checks and separate reported counts.

- **A tilted cylinder ending at Z=0 extends below the build plate.** A new
  brace's diagonal ends above the plate on a short vertical foot stem. Check
  the complete branch envelope and configured foot before accepting it; do not
  clip a bad landing. The length setting measures the diagonal branch axis.

- **Actual-render tests must resolve the unsaved-changes prompt.** The cold
  editor's model, attachment and layer checks completed, but `window.close()`
  blocked in `_confirm_discard_or_save` after routing marked the document dirty.
  Saving before closing removes that test hang without bypassing rendering.

- **Local release builds need an explicit CPU extension.** The development
  environment can contain CUDA native code while releases are CPU-only.
  `build_appimage.py --native-extension` selects a separate CPU build for the
  staged package without replacing the live development extension.

- **Connected downward braces can still create drainage bottlenecks.** On the
  lifted sphere with a 0.8 mm skate base, bracing adds five diagonals and two
  feet. The reopened solid remains one component, its feet stay inside the
  base, and no enclosed cavity appears; sampled drainage still finds a narrow
  channel below the configured 1 mm² threshold. Preserve that failed check and
  withhold ordinary export. Base-only dimensional tests disable bracing
  explicitly; the braced case separately verifies the drainage rejection.

- **Grid-base triangles can collapse only when serialized to float32.** A
  generated grid had four nonzero float64 faces become zero-area STL faces,
  with or without bracing. Simplifying the generated grid within one float32
  coordinate step removes redundant edges while keeping its topology closed.
  Deleting degenerate output faces alone would not establish that. The strict
  reopened-mesh and drainage regressions cover both braced and unbraced grids.

- **`extra_models=None` is not an empty dict.** `load_and_place` used `None`
  to mean "no added parts". `_finish_place` then called `.get` on that value,
  so every single-STL open died with `AttributeError` before the model actor
  was created. Return `{}` and always read with `value.get('extra_models') or
  {}`. The primary stays under the `model` actor key; added parts are
  `model:1` and up. Prefix-clearing `clear('model')` would wipe extras if the
  primary were drawn last.

- **A VTK box widget on load looks like a failed open.** Attaching
  `vtkBoxWidget` in `_finish_model` framed every newly opened STL with a
  manipulator overlay. Attach it only after an explicit object-list or pick
  selection. The widget reports an Euler delta relative to `PlaceWidget`;
  adding that delta to stored RX/RY/RZ is exact for one-axis moves and only
  approximate for large combined rotations.

- **A first-run wizard must not run without a TTY.** `MainWindow(...,
  headless=False)` with no source is how the xvfb render child starts. A
  blocking wizard there hung the suite. Skip the wizard when stdin is not a
  TTY or `VOXELMILL_NO_WIZARD` is set.

- **A clean CMake tree without venv pybind11 never enables CUDA.** System
  pybind11 is 2.9.1; the project requires 3.0.4. Pass
  `-Dpybind11_DIR="$(.venv/bin/python -c 'import pybind11; print(pybind11.get_cmake_dir())')"`.
  With that, `nvcc` 11.5 is found and `cuda_morphology.cu` is on the native
  link line.

- **The development venv is not an AppImage.** `/.venv/bin/python3` is a
  symlink to `/usr/bin/python3`, and numpy/scipy/PySide6 come from the user
  site because the venv was created with `--system-site-packages`. The stager
  copies packages by import path and sets `PYTHONHOME`/`PYTHONNOUSERSITE` so
  the image cannot see the build machine's user site.

- **Copy triangles out of `open_stl` before leaving the `with` block.** The
  memmap is closed on exit; later voxelization of the view segfaults.

- **Elephant-foot and shrink still binarize greyscale AA.** They run `mask != 0`.
  Defaults are off, so AA survives unless those compensations are enabled.

- **CTB 7-bit greys are material.** Occupancy used to threshold coverage AA at
  128, which treated a decoded CTB value of 127 as a hole and failed the
  verifier on a mid-grey patch. Any nonzero sample is now occupancy, so AA
  fringe expands slightly rather than punching islands.

- **Editor operation `--set` dumps overwrite `--support-preset`.** After A8,
  resolve order is resin → named support preset → `--set`. Dumping every
  document default as `--set` stamped `pillar_diameter_mm=1.2` over heavy's
  1.6. Skip keys the selected preset overlays; explicit form fields still win.

- **A GUI AppImage with no args must launch the editor.** Argparse requires a
  subcommand, so a double-click of a CLI-wired AppRun printed help and quit.
  Empty argv (and a single existing file) rewrites to `gui` when the binary is
  frozen or PySide6 imports; CLI-only images do not rewrite.

- **TSMC retract travel must sum to lift travel.** CHITUBOX enforces that the
  two retract substages cover the same distance as the two lift substages.
  `printer.motion.*_height2` with a zero `*_speed2` is refused rather than
  treated as infinite time. Units in the profile are the GOO header numbers
  (observed millimetres and millimetres per second) and stay uncalibrated.

- **A man-page help line that starts with a dot is a groff macro.** `--output`
  help `.goo or .ctb destination` rendered as `macro 'goo' not defined`.
  `shellhelp._roff` now prefixes a leading `.` with `\&`.

- **A worktree-isolated subagent that `pip install -e` steals the shared venv.**
  The editable `.pth` then points at the child's `src/`, so the parent process
  silently imports the child's tree. Restore with `pip install -e .` from the
  repository root after those children finish, and never copy a `.so`.

- **Opened CTB layers still use the Layers-tab source id `goo`.** Returning
  `source='ctb'` made `_finish_layer` drop the payload because the combo data
  is `goo`. Carry `format='ctb'` for the status line; keep `source='goo'` so
  cache keys and the selector match.

- **`slice` must refuse a same-file destination before it checks the suffix.**
  `slice_stl(source, source)` on an `.stl` used to raise `same file`; checking
  `.goo`/`.ctb` first turned that into `slice_format` and hid the overwrite
  guard.

- **Derived voxel repair may coarsen up to the deviation ceiling, never past it.**
  The 0.9 factor is headroom. If the derived grid does not fit the memory
  budget, `choose_voxel_size` searches coarser pitches whose half-diagonal is
  still `<= max_deviation_mm`. An explicit `repair.voxel_size_mm` still raises
  rather than coarsening. If even the ceiling pitch does not fit, `repair_budget`
  is unchanged.

- **Tolerant welding uses a spatial hash, not sort-order neighbours.**
  Adjacent-in-sort misses pairs across a coordinate-order cell. Cell size
  equals the tolerance; the 3×3×3 neighbourhood is searched; first
  representative wins. Cap is 0.05 mm in native, Python and settings
  validation. `inspect_mesh` does not take a tolerance.

- **Open-cut capping only fills near-planar simple boundary loops.** A hole
  whose vertices deviate more than `repair.max_deviation_mm` from a best-fit
  plane is refused; nothing is written. Non-simple or branching boundaries are
  leftovers, not invented surfaces. Cap winding must oppose the directed
  boundary; filling in the same order leaves inconsistent winding and the
  wrong signed volume. `--max-deviation-mm` is the shared CLI flag: on
  `repair` it is voxel headroom, on `cap` it is planarity.

- **A coarsen window needs a large voxel grid, not a tiny memory budget.**
  `ResourceBudget` refuses `memory_gib < 0.25`, so the byte ratio between the
  0.9-headroom pitch and the deviation ceiling is large. Real coarsening is
  asserted on `choose_voxel_size` with synthetic bounds; `voxel_repair`
  report coverage for `coarsened=True` monkeypatches the chooser.

- **CTB 7-bit greyscale cannot store a source value of 1.** The codec stores
  `pixel >> 1` and reconstructs nonzero colours as `(colour << 1) | 1`. A
  source 1 becomes colour 0 and round-trips empty. Tests must use that
  reconstruction, not `((pixel >> 1) << 1) | 1` on the original nonzero mask.
  `ValidationReport.passed` also rejects `not_run`, so `verify_ctb(...,
  track_voids=False)` cannot be asserted as `passed`.

- **Tree centerline clearance misses the emitted solid.** The initial tree mode
  checked only a trunk column and sampled branch centerlines, leaving nearby
  material inside the cylinder radius undetected. Capsule checks now include
  thickness and clearance on vertical and sloped segments. Equal-height branch
  tips also need a lower trunk junction to respect `pillar_angle_deg`; the
  support graph must replace the old independent feet when geometry clusters.
  Regression evidence covers a side-offset obstacle and one connected assembly.

- **Binary STL recovery prefers a clean `84 + 50n` size over the header count.**
  When the file length is exactly one header plus `n` triangle records, the
  reader uses `n` even if the uint32 count is wrong-high or wrong-low
  (`stl_count_mismatch`). Trailing non-aligned bytes never invent extra
  triangles beyond the header count (`stl_trailing_garbage`); only a clean
  longer `84 + 50n` payload widens the mesh. Truncation keeps complete records
  only and still fails when none remain. Verified with synthetic 1–2 triangle
  fixtures in `tests/test_mesh.py`.

- **A painted blocker is a centroid, not a triangle index.** Display may
  decimate the model, so cell IDs on the viewport are not source indices.
  Paint stores plate-coordinate centroids and matches samples within half the
  support spacing. Island births are never blocked: an unsupported island will
  not print. Block wins over enforce on the same mark.

- **Multiple models collide only as solids.** Support envelopes may overlap.
  Intersection uses Manifold on each placed part; if a part is not a solid the
  check is skipped rather than inventing an AABB refusal. Extra STLs are
  concatenated into one column field so part-to-part routes already join them.
  `.chop` records extra-model paths in the manifest; they are not yet separate
  archive members.

- **A plate putty-knife bevel insets the top, not the plate contact.**
  `raft_slope_deg` (default 30, from the plate) keeps the hull at Z=0 and
  shrinks only the top ring. The AABB is unchanged; measure top-slice area or
  volume. `base_edge_slope_deg` remains the per-foot-base taper and is refused
  on plate.

- **A copied angle can reverse the intended constraint.** CHITUBOX's middle
  angle is relative to its vertical top segment; `pillar_angle_deg` is measured
  from horizontal. The reference's 70° therefore maps to 20°, not 70°. The
  preset now performs that conversion. Verified against the
  [official parameter definitions](https://docs.chitubox.com/en-US/chitubox-basic/v1.9.5/setting-up/configure-support-parameters);
  the reference table retains its original value and the preset test checks
  the converted value.

- **A CHITUBOX small pillar is a whole model-to-model connector.** It is not
  the shorter middle segment that voxelmill originally called a small pillar.
  Its upper and lower depths belong to its two model contacts. The new
  `small_pillar_mode=model` is separate from the retained `middle` mode;
  selection uses the entire surface gap. A 5 mm gap uses the new connector
  at a 5 mm limit but not at 4.99 mm, even though the middle alone is shorter.
  The reference omits that maximum-length setting, so the preset records its
  known dimensions with selection explicitly disabled until a limit is supplied.

- **Fewer voids can mean missing supports rather than better supports.** On
  the float-valve nut, the triangle-only run routes 195 contacts and fails 34.
  Enabling 1 mm bottom connectors and 0.25 mm small-pillar penetration rejects
  all 85 previous model anchors: only 110 routes remain and 119 fail. Its
  enclosed-void check passes, but that is not an improvement in print readiness.
  The unchanged 542.517 mm³ triangle base confirms the changed geometry belongs
  to model attachments. Both runs keep their failures in the report.

- **A standalone GOO verdict still depends on policy outside the file.**
  The header carries no minimum overlap, maximum growth span, or minimum void
  volume. The same four-layer block passes overlap at the default threshold
  and fails at 129 pixels because adjacent layers share only 128 pixels.
  `verify` now records all three resolved thresholds in `settings_from_caller`
  and the void-analysis toggle in `analysis_options`; file-derived geometry
  alone is insufficient to explain or reproduce the verdict.

- **A forbidden model anchor must still fail support-route validation.**
  Disallowing part-to-part supports leaves some manually requested contacts with
  no permitted route. The wide-pedestal fixture routes its contact when allowed
  and fails it when forbidden. Count `contacts_blocked_by_policy` and retain the
  ordinary failed-route gate; never drop the contact to satisfy the preference.

- **Brace centreline clearance misses collisions with the brace's thickness.**
  The old brace generator never queried model occupancy. A fixture offset
  0.25 mm from the strut centreline is hit by a 0.8 mm brace even though the
  line misses. Check intersecting grid cells across radius plus clearance, and
  report rejected braces. This remains analysis-grid evidence, not an exact
  surface clearance or a stiffness calculation.

- **Cap attempted braces, including rejected ones.** Once collision rejection
  exists, a cap counting only emitted solids does not bound the work when all
  candidates hit the model. The brace candidate budget now counts every attempt;
  an interval too small to advance the floating-point height is refused.

- **Printer files omit resin metadata, so round-trip comparisons must too.**
  A printer save with custom resin density used to fail after writing the file,
  because the writer omits `[resin]` and the comparison expected it back. Compare
  precisely the serialized sections, report `omitted_sections`, and compare the
  staged file before replacing the destination. A syntactically valid but
  changed staged file must preserve the old destination just as a parse failure
  does; `tests/test_profiles.py` covers both.

- **Resin matching happens before explicit printer overrides.** Resolving a
  staged resin using `resolve_settings(None, resin, {'printer': current})` still
  matches its process against the default printer id and validates against the
  default layer range first. Resin saves and editor loads now stage the actual
  printer profile and resolve against it. Custom ids, layer ranges, and dotted
  or quoted TOML identifiers have round-trip regressions.

- **Applying a modal editor can race a worker even before the editor closes.**
  The modal event loop still delivers parent-window jobs. Invalidate the parent
  generation at Apply and refresh/rebuild immediately; doing it only after
  `exec()` returns permits a pre-edit worker result to populate edited state.
  Support-preview edits likewise invalidate their own generation immediately,
  before the debounce timer submits a replacement.

- **The proposed interior-face filter does not solve body support routing.** On
  the unrotated float-valve body lifted 5 mm, at 0.05 mm layer height, the raw
  planner requests 1,085 contacts and fails 569 routes. Dropping samples whose
  column is occupied at both adjacent layers removes 11,887 of 183,449 samples,
  but still fails 561 routes and leaves 24 samples uncovered. Do not ship that
  filter as a complete fix or relax support validation to claim acceptance.
  Evidence: `reports/plan2/body-ingestion-probe.json` and
  `reports/plan2/body-interior-filter-probe.json`.
- **Unroutable float-valve contacts are not confined to raw soup.** A one-pass
  prepare of the cover takes the exact solid path, yet 384 of 773 requested
  contacts fail routing in its original pose at 5 mm lift. The nut fails 119 of
  229 while its exported-carrier raster parity passes. Diagnose overhang
  selection and available tip/clearance space as well as interior faces;
  removing raw internal triangles alone cannot explain the exact cover result.
- **Zero negative winding crossings does not prove an exported soup matches
  its parts.** A reversed model and a positive penetrating support can cancel
  to zero inside their overlap. Independently OR group masks, then compare the
  reopened STL on the reopened grid; report differing pixels and positions.
  `tests/test_assembly.py` includes this penetrating-contact failure.
- **An Xvfb child can fail only inside the execution sandbox.** In this session
  the Qt/Xvfb child aborted in the sandbox, while the same render test passed
  outside it. Its Qt failure text was on stdout and stderr was empty, so the
  test now includes both streams when reporting a child failure. Do not disable
  the render assertion to make the sandbox run green.

- **Printer permissions are session-specific.** The earlier printing-session
  restriction was superseded on 2026-09-08: the user authorizes communication
  with the now-idle printer but explicitly forbids starting any print. Read-only
  status/attributes/file-list queries are permitted. Physical print acceptance
  remains deferred; a prior offline-only note must not be mistaken for current
  authorization.
- **Editable installs do not rebuild C++.** The Python source can be current while the imported extension is stale. Rebuild with `scripts/rebuild.sh -j2` after native changes and check the imported module path. The original baseline passed 110 tests, but passing tests did not establish the stage gates below.
- **Never copy a rebuilt `.so` over the installed extension.** A live Python process can have the old inode memory-mapped; truncating it during `cp` can SIGBUS the process. `scripts/rebuild.sh` installs through a temporary file and atomic rename. Use that script, not an ad-hoc copy, for every native rebuild.
- **Voxel pitch does not bound repair deviation.** Half the voxel diagonal describes a quantisation scale; filling open contours, repairing voxel connectivity, smoothing, or losing thin features can exceed it. Verify the actual repaired surface against the original in both directions. Never increase the allowed deviation automatically to make a model work.
- **An invalid mesh's signed volume is not physical volume.** A negative value or open surface makes added/removed volume inferred from that number unreliable. Keep net signed-volume comparisons labelled. Preserve originals.
- **A connected void can still have a narrow neck.** The old drainage routine assigned outside air's pass to every chamber connected to it. Identify disconnected *eroded cores* and measure their connection to outside. Circular-equivalent clearance area is not the area of an arbitrary slot. XY cell-centre sampling can miss sub-grid walls; it is not a conservative occupancy proof.
- **Validate a temporary export before publishing.** Writing the chosen destination and then unlinking it on failed validation destroys any earlier output. Publish a checked temporary artifact atomically, and keep existing files on cancellation or failure. Reject a destination that aliases the source.
- **GOO occupancy is not GOO intensity.** Native raster masks use `uint8` values 0/1, while a binary GOO LCD layer must contain 0/255. Convert occupancy only at the GOO frame boundary; preserve values above 1 for a separately calibrated greyscale path. Decoding/re-encoding a 1-valued layer is structurally valid but physically underexposes it.
- **GOO layers need three independent passes.** Validate the source raster and drainage first, stream the candidate, then reopen it and compare every decoded full-LCD pixel and per-layer timing against a fresh source raster before the final rename. Do not retain a full build volume just to make this comparison.
- **GOO source and destination must be stable.** Reject source/output aliases and symlink outputs. Hash the STL on validation, write, and verification opens, because a changed source otherwise mixes validation from one mesh with pixels from another.
- **GOO framing is length-based.** RLE payloads can contain CR/LF; use the declared length and require the delimiter at every exact layer boundary. The V3 checksum covers only bytes after `0x55` through the final payload byte. The decoder also needs image/blob limits and contiguous bytes to remain bounded under malformed input.
- **Reference GOO motion is evidence, not calibration.** The Mars 5 Ultra values in the default profile were observed in the immutable GOO and make export serializable, but their units, tilt-release behaviour, physical orientation, and total print duration remain unverified. Keep `PrintTime` at zero unless a caller supplies a calibrated/manual estimate; do not infer it from those fields.
- **Failed support routes must reach the export report.** Exact raster connectivity alone does not prove that planned contacts were placed or are removable. Keep capped diagnostic counts in metrics and propagate them to validation.
- **A sample hull is not the full mesh hull.** Automatic placement is a finite, sampled search followed by a full-triangle bounds check; failure is “no feasible placement found,” never a proof that no orientation fits. The supported assembly needs a second envelope check.
- **Preview decimation is for display only.** Layer checks and final geometry must retain all triangles and native printer pitch. Block-max decimation preserves thin supports better than striding but can make growth-distance checks lenient; report its resolution.

- **Full-panel growth checks need tiled EDT, not a second full-panel array.** A
  8520×4320 printer panel makes a full distance transform and overlap-key arrays
  consume hundreds of MiB each. The exact growth fallback now uses 256×256
  tiles with anisotropic threshold-sized halos, including equality pixels and
  explicitly handling empty halos. Worker concurrency is capped using the
  previous-mask, labels, EDT, component-count and prefetch-copy budget before
  analysis starts.
- **One-pixel voids are real raster evidence.** Default `min_void_volume_mm3` is zero. A user-selected nonzero threshold reports the ignored population explicitly; do not silently enable it to get a passing export.
- **Cancellation is not an analysis warning.** Do not catch `Cancelled` as a generic recoverable `VoxelMillError` and continue into export.
- **Profile flags must work or fail explicitly.** Unsupported tiny-feature removal and automatic drilling are rejected. Automatic contact selection and bracing have independent off switches; manual editing does not imply automatic regeneration is desired.
- **SDCP upload completion is not verification success.** The protocol has no positive MD5-complete response. Keep upload, file-list confirmation, status/error observation, and Cmd 128 start as separate steps.
- **SDCP print sub-status is sticky.** `Complete` and `Stopped` can persist after a job, and an accepted Cmd 128 can initially show stale state. Confirm a new task with a live machine status and a changed task ID or filename; use `wait=False` only when the caller owns that reconciliation.
- **SDCP command names and fields have wire-level typos.** Match responses by RequestID, tolerate a wrong echoed Cmd, emit `Filename` for Cmd 128 and `FileName` for Cmd 255, and preserve lowercase file-list keys.
- **Discovery is a LAN control action.** Keep it explicit and opt-in. Adapter construction/connect must not broadcast or discover implicitly; physical acceptance remains deferred while the printer is in use.
- **WebSocket idle is not a disconnect.** `websocket-client` raises `WebSocketTimeoutException`, which is not a built-in `TimeoutError`; normalize it as an idle read. Any other reader exception must transition the adapter to disconnected and wake command waiters.
- **A cached SDCP status is not a refresh.** Require a new status generation after Cmd 0. This matters before upload/start busy checks and prevents actions based on stale state.
- **An accepted start can be unknowable.** A command timeout or a missing live status after Ack 0 leaves an uncertain start. Record it and reconcile with a fresh status before another Cmd 128; require the new filename/task identity when firmware supplies one.

- **Replacing a loaded native library must be atomic.** The old rebuild script used `cp` onto the imported `.so`, truncating executable pages mapped by running jobs. A full fixture run stopped with SIGBUS during such rebuilds. The installer now writes a temporary file and renames it. Existing processes retain their original inode; new processes load the rebuilt version. This is distinct from actual OOM or the editable-install stale-library problem.

- **A fatal X error kills the whole test session, not one test.** VTK opens a real X window; under the offscreen Qt platform it receives a window id the server rejects, and Xlib answers `BadWindow` by calling `exit()`. The suite aborted after 62 of 148 tests with no failure summary, which reads like a hang rather than a defect. Run any real-render check in a subprocess under `xvfb-run`, so a process death stays a single test failure.
- **Qt's `grab()` does not capture VTK's native render window.** The screenshot shows the surrounding widgets with a black viewport whether or not anything rendered, so asserting on its size or file bytes proves only that Qt drew its own chrome. Assert on `vtkWindowToImageFilter` output from the render window itself, counting pixels that differ from the known background colour.
- **A shared triangulation edge can erase a whole raster row.** Two triangles sharing an edge traverse it in opposite directions, and interpolating each way rounds to different doubles. When that crossing lands exactly on a sample row centre, the half-open row rule drops the row from both segments and punches a hole clean through solid material. On a 9 mm hollow cube this joined a sealed cavity to outside air, so it reported as a drained chamber rather than an enclosed one. Interpolate along a canonically ordered edge so both triangles yield bit-identical crossings. This assumes welded vertices; unwelded near-duplicates still diverge.
- **`odd_rows` is evidence that the mask is wrong.** The rasterizer already reports rows whose winding never closes, but `analyze_drainage` discarded them and kept the mask. Every unfilled cell inside solid material is a leak that joins a chamber to outside air, and the analysis then finds no bottleneck to report. An unclosed occupancy grid can never certify drainage: report `not_run`, never `pass`. Four of the seven originals are open surfaces, so this is the normal path, not the exotic one.
- **A sealed cavity used to pass the drainage check.** The check tested only `bottlenecked_components`, and a fully enclosed chamber has none. It was caught elsewhere by the layer `enclosed_voids` check, which made the two reports contradict each other. `drainage_check` now fails on enclosed chambers too.
- **A curved orifice under-measures on the analysis grid.** Equality passes, but sampling a round bore at cell centres reports less clearance than it has: a bore at exactly the 1 mm² threshold measures 0.886 mm² at the default three voxels per radius, converging to 0.969 mm² at ten. A bore needs roughly 15% over the nominal diameter to clear the default grid. The error always understates the opening, so marginal parts fail rather than pass; do not widen the tolerance to compensate.
- **Refinement makes finalists near-duplicates.** The coarse-to-fine search refines around its best eight seeds, so consecutive feasible candidates differ by a fraction of a degree and any re-ranking has nothing to choose between: five finalists on a temporal bone scored 5.0728 to 5.0797. Require a minimum geodesic separation between accepted finalists, and check it *before* the full-resolution bounds pass, which is where the loop's time goes. Doing so took a temporal bone from 38.3 s back to 7.1 s. Even then only two finalists 20 degrees apart exist for that part; report the count rather than implying the search compared five real alternatives.
- **Normalising a lever arm by the footprint divides by nearly nothing.** The model hangs from supports rather than standing on the plate, so its lowest layer is often a single cell. Dividing the centre-of-mass offset by that footprint's radius gave 27.5 on a temporal bone, which then clamped to the cap for every candidate and silently became a constant. Normalise by the part's own XY half-extent and take the anchor from the area that can actually carry pillars.
- **An unclosed grid understates trapped resin.** Holes let the flood escape, so the orientation whose occupancy grid failed reports *less* trapped volume and wins on the strength of its own defect. A finalist without a closed grid keeps its accessibility and stability terms and forfeits the void terms. The right temporal bone reaches this path at the default coarse pitch, so it is not hypothetical.
- **Gravity runs toward +Z here, so a cup that drains opens toward +Z.** It is easy to label these backwards: `cube - cube.translate([0,0,3])` opens toward +Z and sheds resin, and only the 180-degree rotation of it is a bowl. Check a fixture's trapped volume against the cavity volume you can compute by hand before trusting a drainage result.
- **The supported assembly fails drainage on its own supports.** A plain solid
  sphere with supports reports two bottlenecked components of a few hundredths
  of a cubic millimetre with roughly 0.67 mm² of clearance. This is the support
  structure, not the model, and it predates the enclosed-cavity rule.
  `repair.min_void_volume_mm3` now applies to drainage bottlenecks as well as
  layer voids, still defaulting to zero and still counting the ignored
  population explicitly. **Corrected 2026-09-07: this entry used to say the
  crevice was where a pillar meets the raft. It is not** — see the entry below.
- **Sample the downward faces even when automatic contacts are off.** Coverage is measured against those samples, and a manual-only run is exactly when an overhang gets missed, so skipping the sampling makes the check unanswerable in the case that needs it most.
- **Contact coverage and anchor load are geometric, not mechanical.** Coverage says a contact sits within reach of an overhang; load says how much downward area leans on it by nearest assignment. Neither says the pillar holds. Coverage failure blocks an export; load exceedance is a warning until pillar mechanics are calibrated against real prints.
- **`docs/gui.md` claimed GOO export could not run.** It said the Mars 5 Ultra profile "intentionally leaves those values empty", but the profile carries all 18 `printer.motion` fields from the reference GOO and export works. A doc that describes a blocked path readers can actually walk is worse than no doc; `goo_motion` is raised only when a profile is genuinely missing a field. Re-check profile claims against `voxelmill profile` rather than against memory of an earlier state.

- **A 255-valued layer wraps to a plausible grey, not to an obvious error.**
  `mask_to_image` did `mask * 220` on uint8. A geometry raster holds 0/1 and
  renders 220; a decoded GOO frame holds 0/255 and `255 * 220 mod 256` is 228 —
  eight units brighter than correct, which no one would ever notice by eye.
  Normalise with `mask != 0` before scaling. The same class of bug hides
  anywhere occupancy and intensity share a code path.

- **Re-running the topology analysis on a verified GOO recomputes a known
  answer.** `slice_stl` already proves every decoded LCD pixel equal to the
  source raster, and that raster was analysed earlier in the same report, so
  feeding the decoded full-panel frames back through `analyze_layers` costs a
  full labelling pass over 36.8 Mpx per layer to produce an identical result.
  Record the equivalence (`verification.layer_topology`) and ship the standalone
  `voxelmill verify` for the case that genuinely needs it — holding only the
  file, with no source raster to lean on.

- **A GOO describes its own machine; a profile on the command line may not.**
  `verify` takes panel resolution, physical display size, pixel pitch, mirroring,
  layer height, bottom/transition counts and both exposures from the header, not
  from `--printer`. Analysing a file on a profile's grid silently measures the
  wrong lattice. Report the disagreement (`goo_settings_differ`) rather than
  reconciling it, and say which values were used.

- **Unmirror a decoded layer before analysing it.** Islands, growth spans and
  enclosed voids are invariant under an axis flip, so a mirrored frame passes or
  fails identically — but every `position_mm` in the resulting diagnostics lands
  on the wrong side of the plate, which is worse than no diagnostic at all.

- **A stream's layer count is derived from bounds, so clamping one stream
  clamps nothing else.** `slice_stl` builds three separate `MeshLayerStream`s
  from the same source. Clamping only the validation stream to a clipped layer
  count let the writer emit more layers than the header declared, which the
  writer caught as `goo_layers`. Every pass must be clamped, or validation, the
  written bytes and the verification compare different sets of layers.

- **Colour out-of-bounds triangles on the model actor, not a second actor.**
  `handle_pick` accepts a support contact only when the picked actor's `role` is
  `'model'`. Splitting unreachable triangles into their own actor makes them
  visibly red and silently unsupportable, which is a worse failure than the one
  being fixed. Per-cell scalars on the same actor cost nothing extra: the
  geometry is already one three-point cell per triangle with unshared vertices.

- **Clipping to the panel and measuring overflow are two different volumes.**
  The printer can expose any LCD pixel, so an export clips at the physical panel
  and the machine height. `envelope_fits` refuses against the *usable* envelope,
  which additionally reserves `printer.edge_clearance_mm`. Reporting one number
  for both makes the clip record disagree with itself; the report names which
  volume each figure belongs to.

- **A view up parallel to the view direction is silently degenerate.** VTK does
  not complain; the view matrix simply collapses. Top and bottom views need a
  Y-based up while every side view uses +Z, and the up vector is
  re-orthogonalised against the direction rather than trusted as authored.

- **A camera transition that lerps positions cuts through the model.** Two
  opposite views also have no interpolation plane at all: the midpoint of the
  direction vectors is the zero vector. Interpolate the *direction* and
  renormalise each step, fall back to the destination when the sum degenerates,
  and land on the exact named pose at the end rather than wherever the last
  frame arrived.

- **The router's own tip length, not interior faces, was rejecting half the
  contacts.** `route_contacts` would anchor on already-printed model material
  only when the gap above it exceeded a full `tip_length_mm` (2.0 mm). On all
  three float-valve parts the blocked contacts sit 1.39-1.48 mm above the
  material below them: far enough to need a support, too close to fit one the
  router would emit. Letting the tip cone span a shorter gap took the nut from
  110 of 229 routed to 195, the cover from 389 of 773 to 722, and the body from
  516 of 1,085 to 994. Evidence: `reports/plan2/routing-summary.md` and the
  three `*-routing-probe.json` reports.

- **Widening the branch search buys nothing here.** The obvious reading of
  "8 nearest candidates tried, all blocked, thousands available" is that the
  search is too narrow. Measured at 64 candidates it recovered **zero** contacts
  on all three parts: the columns near a blocked one are blocked by the same
  feature, and the ones that are free are outside the 45-degree reach. Measure a
  search-breadth hypothesis before widening a search.

- **Reproduce a router's decision sequence before changing it.** The aggregate
  `contacts_failed` count named no constraint, and three plausible causes
  (interior faces, search breadth, grid resolution) each explained the symptom.
  A probe that re-runs the same decisions and records the deciding quantity per
  contact separated them in one pass, and its routed/failed counts matching the
  router exactly is what makes the attribution trustworthy.

- **New supports create new single-voxel voids.** Adding 85 short anchors to the
  nut introduced one enclosed component of 1.62e-5 mm3 — exactly one pixel at
  one layer (0.018 x 0.018 x 0.05). It is real raster evidence and it fails the
  export by default, as it should. Expect a routing improvement to surface void
  and drainage findings that more sparsely supported geometry did not reach.

- **Every contact the router still cannot route is already attached.** Asked on
  the printer lattice instead of the 0.15 mm analysis grid, all 34 of the nut's,
  91 of the body's and 50 of the cover's 51 remaining unroutable contacts have
  occupied material within their own 3x3 pixel neighbourhood one layer below --
  a 0.054 mm window. They are downward samples on near-vertical walls that fall
  on either side of the surface boundary, which is why the own-pixel count
  varies (9, 54, 37) while the 3x3 count does not. That is the same attachment
  `raster_connectivity` certifies, so they are not islands. **Updated 2026-09-08:**
  the user decision is that a near-vertical already-attached face supports
  itself, so `support.drop_attached_unroutable` (default on) drops those
  contacts after routing rather than failing `support_routes`. Island births
  and manual/correction contacts are never dropped. A true free overhang that
  will not fit still fails. The drop does not answer sag; paint-on D2 is the
  escape hatch if a dropped wall actually needed a pillar.

- **The STL-vs-GOO orientation method cannot recover a slicer's own pose.** Both
  reference files were produced from `right_temporal_bone_mars5_oriented.stl`,
  so the source is shared, but each slicer placed the model itself. Aligning on
  a shared top scored a best mean IoU of 0.21 on the CHITUBOX file and 0.13 on
  the SatelLite one, and the four sampled layers picked three different flips in
  each. Widening the Z window from 1 mm to 3 mm changed the CHITUBOX winner from
  `flip_xy` to `identity`: the answer moves with the search parameters, which is
  the clearest sign it is noise. Our slices held 1.0-2.1 M pixels against
  references of 1.1-5.3 M, so we were slicing real material, not empty space --
  the shapes genuinely do not align, which is a pose mismatch and not a small Z
  error. Centroid alignment removes translation and nothing removes an unknown
  rotation about Z. The mirroring question stays open.

- **An unresolved orientation belongs in every export, not in a profile
  comment.** A mirrored threaded or keyed part is scrap and looks correct until
  it is assembled. `printer.image_mirror_verified` defaults to false and every
  GOO export carries a `goo_orientation_unverified` warning with both reference
  files' flags until someone sets it true against a printed part. It is a
  warning, not an error, so it does not block an export it cannot judge.

- **Analysing a GOO on the full panel costs minutes of labelling empty space.**
  `verify` fed `analyze_layers` the uncropped 8520x4320 lattice, so every layer
  paid a 36.8 Mpx `ndi.label` regardless of how small the part was. A 500-layer
  supported sphere took minutes to verify against 32 s to slice the same
  geometry. Cropping to the exposed pixels plus a one-pixel border costs one
  extra decode pass -- decoding is far cheaper than labelling -- and changes
  nothing: the border preserves exterior connectivity exactly as
  `RasterGrid.for_bounds` does on the mesh path, and moving the window origin
  keeps every `grid.xy` in plate millimetres. `--full-panel` keeps the old
  behaviour and a test asserts the two agree on checks, metrics and every
  diagnostic position.

- **A benchmark harness earns its keep on the first run.** The full-panel
  verification cost was invisible in the test suite, which only ever verifies
  tiny synthetic panels, and invisible in the acceptance runs, which never got
  as far as verifying a `.goo`. One scenario on a 500-layer sphere surfaced it
  immediately. Measure the ordinary path, not only the fixtures.

- **Every float in a GOO header is float32, so a profile that wrote it does not
  match it exactly.** `verify` compared the profile's `printer.build_mm` against
  the header at `atol=1e-6` and reported the Mars 5 Ultra's own 77.76 mm depth
  as a mismatch, because it comes back as 77.76000213623047. A field that cries
  wolf on every single export is a field people learn to skip. Compare at the
  format's own quantisation, the same `2e-5` `_finite_equal` already uses to
  verify a written header. Found by running the real chain, not by a test.

- **A warm test process hid the Layers-tab segfault.** The editor first imports
  `_native` from a Qt worker. pybind11 3.0.1 retained that worker's temporary
  Python thread state; a subsequent rasterizer callback crashed inside
  `PyEval_AcquireThread`. The latch reproduced it even with VTK disabled, while
  pre-importing `_native` on the main thread avoided it. Build with pinned
  pybind11 3.0.4, which includes upstream
  https://github.com/pybind/pybind11/pull/5870, and rebuild the extension with
  `scripts/rebuild.sh -j2`; upgrading the Python package alone does not change
  already-compiled GIL handling. Fresh-process tests must avoid warming native
  imports. The exact latch now passes Preview → Layers and backward scrubbing
  with real VTK under Xvfb.

- **A generation identifies a document, not a slider request.** Two layer
  requests in the same generation can finish out of order. Each submission now
  has its own identity, so an old completion cannot delete the new token or
  paint obsolete pixels. Cached hits create no worker submission, so the view
  must additionally check the requested index and selected source before drawing.

- **CLI-prepared projects were losing the editor's decisions.** Saving only a
  placement matrix omitted the rotation, offset, lift and contact edits that
  `Document.load` uses to rebuild. Save those authored fields too. A zero lift
  is valid: `value or 5.0` changed it to 5 mm on reopen; use an explicit default.

- **Caching masks alone still sorts the mesh on every unseen layer.** A cache
  miss rebuilt every native rasterizer. Keep the sorted indices for the current
  assembly, serialize access to its mutable sweep, and reset the sweep when
  moving backward. Cancellation can interrupt the active-triangle update, so
  reset on failure too; merely remembering the last completed Z is insufficient.

- **An immutable-fixture test must enumerate its manifest, not an input folder.**
  The opt-in original-mesh suite globbed every STL, then required a matching
  entry in a seven-file manifest. Adding the latch and float-valve inputs caused
  four `StopIteration` failures before any geometry was checked. Parameterize
  that suite from the manifest; keep newer parts in their own acceptance tests
  until their reference evidence is recorded.

- **An unreferenced duplicate of a profile went stale and nobody noticed.**
  `src/voxelmill/data/mars5-ultra.ptr` still carried the empty
  `[printer.motion]` table from before the reference values were recorded,
  while `profiles/mars5-ultra.ptr` had them. Nothing imported the packaged
  copy, so it drifted silently for a day; the moment profile discovery made
  that directory the built-in library layer, resolving the identifier
  `mars5-ultra` would have refused every GOO export with `goo_motion`. The two
  files are now byte-identical and `test_packaged_profiles_match_the_repository_copies`
  keeps them that way. A copy no code path reads is not dormant, it is a
  landmine waiting for the first code path that reads it.

- **`os.altsep` is `None` on POSIX, and `'' in string` is always true.** The
  path-versus-identifier test in `profiles._looks_like_path` wrote
  `(os.altsep or '') in reference`, so every bare identifier was classified as
  a filesystem path and `--printer mars5-ultra` failed with "No printer
  profile at mars5-ultra". Defaulting an absent separator to the empty string
  turns a substring test into a tautology; skip the separator instead.

- **A default resin density would fabricate a weight in every report.** The
  obvious default for `resin.density_g_cm3` is ~1.1, which is right for most
  photopolymers and wrong for the one in the vat. `0.0` means "not supplied"
  and the derived `mass_g` and `cost` are then reported as `null`, which is
  the difference between "we do not know" and a number nobody entered. The
  GOO header's `material_grams` and `material_cost` stay `0.0` in that case,
  which is exactly what every export wrote before those fields were derived.

- **Elephant-foot and dimensional compensation have to be applied in the
  verification pass too, or the file stops matching the thing that was
  checked.** `slice_stl`'s third pass recomputes the expected frame from the
  source mesh and compares every decoded LCD pixel. Shrinking or eroding
  layers only in the write pass would fail `decoded_pixels` as a pixel
  mismatch. Both passes call `export_mask` (dimensional compensation then
  `compensate_mask`), so the check still proves the file matches the intended
  exposure rather than the raw geometry, and the report names the pixels
  changed per layer.

- **A compensation ramp quantises to whole pixels, so adjacent layers share a
  radius.** 0.2 mm over three layers on a 0.1 mm pitch is 2, 1 and 1 pixels,
  not three distinct rings. A test asserting a strictly decreasing removal per
  layer is asserting something the pixel lattice cannot deliver. Assert the
  radii, which are the quantity the code actually computes.

- **Eroding the cropped mask is the same answer as eroding the full frame.**
  Everything outside `RasterGrid.for_bounds`'s crop is empty, so
  `binary_erosion(..., border_value=0)` on the crop equals the erosion of the
  36.8 Mpx panel restricted to that window — at a few thousand pixels instead
  of tens of millions. `test_erosion_of_the_crop_equals_erosion_of_the_whole_frame`
  pins the equivalence rather than leaving it as a comment.

- **A mirror without a winding reversal produces an empty part, not a mirrored
  one.** A negative-determinant transform turns every outward normal inward.
  The nonzero-winding rasterizer then reads the whole model as outside and
  fills nothing, and the exact union treats it as a hole rather than a solid —
  a failure that looks like the file being wrong rather than the transform.
  `iter_transformed_triangles` now reverses each triangle's vertex order
  whenever `det < 0`, which is the single place every transform in the program
  flows through, so no caller can forget it. A proper rotation has `det = +1`
  and is untouched, and mirroring *two* axes composes back to a proper motion
  and is also untouched. Verified by signed volume: the mirrored cube keeps
  +0.256 mm3 instead of flipping to -0.256.

- **The Setup tab is three undo commands, not one.** Applying it runs
  `set_settings`, then `set_transform`, then `set_orientation`, so reverting a
  scale takes two undos. A test asserting a single undo restores the scale is
  asserting something the editor has never done; assert the labels
  (`orientation`, then `transform`) instead.

- **A guessed scale bound is better than none here.** `scale_matrix` refuses a
  factor outside [0.01, 100]. The bound is arbitrary, but the failure it
  catches is not: a metre-to-millimetre slip is a factor of 1000, and it
  produces either a part no build volume can hold or one that quantises to
  nothing on the printer lattice. A caller who genuinely wants more scales
  twice and says so in their own record.

- **A CLI-saved `.chop` lost its scale and mirror while the editor's kept
  them.** `pipeline.prepare` wrote `placement.scale` and `placement.mirror`
  inside the placement record, but `cmd_prepare` restores an authored decision
  from the *top-level* `scale_factors` / `mirror_axes` keys that only
  `Document.manifest` wrote. Reopening a CLI-authored project therefore
  prepared the part at 1.0 while the archive still showed 2.0 as history —
  the same failure mode as the earlier "CLI-prepared projects were losing the
  editor's decisions" entry, in a new field. A matrix inside a placement record
  is history of what was done; the authored decision needs its own key, and the
  two writers of a manifest must write the same keys.

- **A generated man page is a documentation audit.** Rendering one immediately
  produced twenty-seven "No description." entries — real flags with no `help=`
  text, which had been equally missing from `--help` and nobody had noticed
  because `--help` shows the flag name and moves on. A `.SS` heading with
  nothing under it is impossible to ignore. `test_shellhelp.py` now asserts
  `'No description.' not in page`, so the gap cannot come back.

- **`add_parser(help=...)` does not set the subparser's `description`.**
  argparse records that text on the parent's `_SubParsersAction`, so reading
  `subparser.description` returns `None` and every generated completion and man
  page labelled each command with its `prog` or with nothing. Read
  `action._choices_actions` for the one-line summaries instead.

- **roff eats a leading hyphen.** Every `--flag` in a generated man page has to
  be written `\-\-flag`, or the renderer silently substitutes a different
  character and the page documents options nobody can type. `groff -ww`
  exiting with empty stderr is the check that the escaping worked; the test
  renders the page and asserts both.

- **A hand-listed forwarding table drops whatever nobody remembered to add.**
  `batch` re-emitted its settings flags for each item from an enumerated list,
  which silently omitted every boolean toggle (`--no-auto-supports`,
  `--seal-voids`, `--clip-to-build-volume`) and half the numeric ones, so a
  batch accepted a flag on its own command line and then ran every item
  without it. `_overrides` already folds every settings flag into one nested
  dictionary; re-emitting *that* as `--set` pairs cannot miss a flag, and a new
  flag joins the batch the moment it joins the CLI. The test asserts the item
  and the batch resolve identical settings, not that particular flags appear.

- **An inherited flag that nothing reads is worse than an absent one.**
  `batch` took `--report` from the shared parser and never looked at it, so a
  user naming a manifest destination got their manifest somewhere else and no
  warning. Either honour an inherited flag or reject it; `batch --report` now
  names where the manifest goes.

- **Undo changed the document and left every control showing the old value.**
  `MainWindow.undo`/`redo` refreshed the undo labels and restarted the
  pipeline but never called `_sync_widgets_from_document`, so an undone
  settings edit reverted `document.settings` while the Setup fields — and the
  new modified dots — still showed the value that had just been undone. The
  viewport was right and the panel was wrong, which is the worst combination
  because the panel is what the next edit is built from. Found while
  documenting the markers, not by a test: nothing asserted that a control
  follows the document rather than only the pipeline.

- **The support drainage bottleneck is the tip/model crevice, not the
  pillar/raft one.** The entry above blamed the pillar-to-raft junction for
  years' worth of reading. Measured on the same synthetic sphere now that the
  base is a strategy: `plate`, `pad` and `none` give a **bit-identical**
  result — two components, 0.013303 mm³, areas 0.667 and 0.886 mm² — and
  `none` builds no raft at all, so the raft cannot be what forms them. The
  seeds sit at z = 4.61 mm, which is 0.4 mm *below* the sphere's underside at
  z = 5.0 and within ~1 mm of a support contact; a raft top would be at
  z = 1.0. Varying the tip cone moves it and varying the base does not:
  contact diameter 0.2 mm leaves one component, 0.9 mm leaves none, a 0.4 mm
  tip base leaves none, and zero penetration leaves one of 0.033 mm³. The
  model alone has none. So the trapped pocket is the narrowing gap between a
  tapering tip and the curved surface it lands on, and no base strategy can
  remove it — only the tip geometry can. `tests/test_support_segments.py`
  pins this so the attribution cannot drift back.

- **An identical number across three configurations is evidence, not a
  coincidence to move past.** The three base strategies returned
  `0.013302749787049376` to the last digit. Two of them build different solids
  and one builds nothing at all, so identical-to-the-ULP output meant the
  measured quantity did not depend on what changed — which is what located the
  real cause. A result that refuses to move when the input does is pointing at
  the wrong input.

- **A base's contact area is not the sum of its nominal feet.** The bare-foot
  (`none`) report used to add up ideal circles of the nominal pillar radius.
  That triple-counts: it ignores the short-pillar class and cones that start at
  the plate, which touch at their own smaller radii; it ignores that a polygonal
  24-sided ring is 1.7% smaller than the circle it approximates; and it counts
  two coincident feet twice. Two feet at the same point with radii 0.2 and 0.3
  are one 0.3 mm polygon, not 0.13 mm² of disc. Every base now measures the
  union of the polygons it actually emits (`footprint_basis` says so), and the
  ideal sum survives only as `nominal_disc_area_mm2` for comparison.

- **A grid clipped to a hull is not automatically one connected solid.** Struts
  cut by the hull boundary leave stubs that touch nothing, and a foot that falls
  between two grid lines is an island. The grid therefore carries an explicit
  perimeter rim every clipped strip reaches, plus the minimum spanning tree over
  the feet, and `build_base` refuses any `skeleton` or `grid` result whose
  measured `connected_components` is not 1 rather than shipping a base that
  looks connected in a preview. That check earns its keep: a strut width near
  the boolean's precision produces a footprint that renders as one piece and
  decomposes into several.

- **On a real part the base does change trapped resin — the synthetic sphere
  said it did not.** Measured on `floatvalveR7-nut.stl` (101 unique feet,
  identical settings otherwise): `plate` reports 6 bottlenecked components
  totalling 0.399 mm³, `skate` 3 totalling 0.126 mm³, and `skeleton` and `grid`
  agree to the last digit at 4 totalling 0.0266 mm³. The earlier entry above is
  still right about its own measurement — on a sphere with a handful of
  well-separated feet the base contributes nothing and the tip crevice is
  everything — but it must not be read as "the base never contributes". With
  101 feet at 3 mm spacing the slab closes gaps that a porous base leaves open.
  None of the four clears `drainage_bottlenecks`, so this changes the size of
  the problem, not the verdict, and the mechanism behind the differing component
  *counts* has not been isolated.

- **Porous bases save what they claim to, and the number is worth stating in
  resin.** Same part, same supports, base volume only: `plate` 965.80 mm³,
  `skate` 472.94, `grid` 452.37, `skeleton` 293.90 — 51% to 70% less than the
  slab, at 0.31 to 0.55 measured open-area fraction. Every support metric other
  than elapsed seconds — 195 contacts routed, 34 failed, the same coverage
  figures and the same four failing checks — is identical across all four,
  which is what makes this a comparison of bases and nothing else. It is still
  geometry: no printed part has yet been pulled off any of these bases.

- **A honeycomb is not a cheaper grid.** At equal pitch and wall width the two
  lattices open exactly the same fraction of their frame — `((p-w)/p)²` for
  both — because the honeycomb's 15.5% higher cell density cancels its 13.4%
  smaller cells. Measured on the float-valve nut at the same settings: `grid`
  452.37 mm³ against `hex` 458.92 mm³, 1.4% apart and the honeycomb slightly
  heavier once the rim and foot tree are counted. `hex` earns its place on wall
  orientation and cell shape, which are removal and stiffness arguments nobody
  here has tested. It does not earn it on resin, and a report that implied
  otherwise would be inventing a benefit.

- **A union of solids that only touch face-to-face is not reliably one body.**
  The tapered base was built as bands from `i*h` to `(i+1)*h`, each the previous
  section offset inward. The union looked right and had exactly the right
  volume, but `decompose()` returned two components, so the base's own
  connectivity gate rejected it. The count does not follow the band count: the
  same footprint gives one body at 2 and 4 bands and two from 8 bands up, so it
  is band *height* — how thin the coincident-face contact gets — that decides,
  which makes it a precision artifact rather than a rule. Extruding every band
  from the plate to its own top instead makes successive bands overlap in
  volume, which unions to one body and produces the identical staircase to the
  last digit, because each section is contained in the one below it. Overlap,
  not contact, is what makes a union connected.

- **A taper that a resin printer can make is a staircase, so emit a
  staircase.** `base_edge_slope_deg` steps at `process.layer_height_mm`. A
  smooth cone in the STL would be resampled to those same steps at slice time,
  and reporting a smooth angle would then describe something the machine never
  built. The report carries `edge_steps`, `edge_step_mm` and `edge_setback_mm`,
  and a slope that eats the footprint before reaching the configured thickness
  fails with the height it reached rather than quietly emitting a short base —
  0.5 mm struts at 20° run out at 0.45 mm of a 0.80 mm base.

- **Tapering the base costs nothing at the plate.** Because the taper starts
  from the untouched plate section and only removes material above it, the
  measured contact area is bit-identical with and without slope — 565.46 mm²
  for the nut's grid either way — while base volume falls another 14%
  (452.37 to 391.07 mm³ at 70°). That is the whole argument for the setting:
  the adhesion-side geometry is unchanged and the removal-side geometry is not.
  Whether a tapered edge is actually easier to get a blade under is still
  untested.

- **CHITUBOX Raft Shape None plus Platform Touch Shape Skate is a tapered
  per-foot frustum, not a pad approximation and not a slab.** The reference
  screenshots set Raft Shape = None (greyed raft Thickness 1.00 mm, Slope 30°)
  and Bottom Platform Touch Shape = Skate at 10.00 mm / 0.80 mm. `base_type=
  skate` with `base_edge_slope_deg=30` (angle from the plate, same convention
  as CHITUBOX raft slope) emits the upside-down trapezoid putty-knife foot;
  `base_skate_length_mm=0` keeps it circular because elongation was never
  measured. Do not invent spacing, elongation, or small-pillar max length —
  those stay in `CHITUBOX_UNSET`. Prefer `grid` when resin/suction matter;
  keep `plate` when a solid option is required. `plate`'s own bevel is the
  fixed `min(0.25, raft_thickness_mm/4)` top edge from `raft_from_feet`, not
  `base_edge_slope_deg`.

- **An idle SDCP printer can retain the previous job's filename and completed
  layer counts.** The 2026-09-08 Mars 5 Ultra query returned machine/print state
  0 and an empty task ID alongside layer 2159/2159. Determine activity from fresh
  status fields, not the presence of a filename. Its optional text ping timed
  out while Cmd 0, 1 and 258 succeeded; ping alone is not a connectivity verdict.

- **Device attributes are not automatically a printable-envelope profile.**
  This Mars 5 Ultra reported `XYZsize=218.88x128.88x220`, conflicting with the
  configured and official 153.36x77.76x165 mm envelope, while pixel resolution
  matched. The cause is unverified; do not overwrite calibration from that
  field. Evidence is in `reports/plan2/printer-readonly.json`.

- **An orientation choice must persist as exact angles.** Saving only `auto`
  reruns the default search on reopen and loses a non-default rank or candidate
  count. Explicit CLI candidate options now save the selected pose, while the
  report retains full ranking evidence. Display-rounded angles are not poses.

- **Failed coverage still needs a JSON report.** With downward samples and no
  contacts, coverage previously emitted an infinite distance; strict CLI JSON
  serialization then crashed instead of returning its validation failure.

- **Less tip taper closes the sphere crevice; a longer tip was not proven.** On
  the synthetic supported sphere, `contact_diameter_mm=0.9` or
  `tip_base_diameter_mm=0.4` (equal to contact, no taper) removed the drainage
  bottleneck entirely. Extending tip length was not shown to help.
  `repair.support_void_policy=ignore` can record those support-class necks
  without failing export; `fill` seals enclosed shells after an exact union and
  does not fix drainage necks. Do not silently raise `min_void_volume_mm3` to
  pass.
  Undefined distance is now null with an explicit no-contact reason, while
  coverage remains failed and all samples remain uncovered. The CLI candidate
  regression exercises this path with automatic supports disabled.

- **Bottom exposure belongs to the plate's layer stack, not a mesh's minimum
  Z.** A model lifted to Z=5 mm is already above the bottom layers. Surface peel
  screening uses physical Z=0 and the configured bottom-layer height; regression
  checks translate a face without changing the process or machine envelope.
  A face on the bottom/normal boundary uses normal lift settings.

- **A full profile snapshot must include newly added analysis policy.**
  Hardware-only `.ptr` files intentionally omit analysis settings, but the
  default full snapshot promises to retain them. Adding `peel` only to resolved
  defaults would silently reset a custom threshold on save/reopen. Its full
  profile round trip now includes the top-level table, separately from hardware.

- **Surface component analysis must not allocate Python objects per edge or
  scan every face for each component.** The original temporal bone has 87,098
  near-horizontal downward components. Exact NumPy edge sorting and grouped
  reductions screened all 5,999,999 triangles in 1.79 s at 561,784 KiB peak RSS.
  Count every component before limiting report details; the detail cap is not
  permission to drop measurement evidence. See `reports/plan2/peel-risk.json`.

- **New settings must not make existing schema-1 projects unreadable.** Both
  editor loading and CLI project preparation supply the absent `peel` table
  before validating stored settings. A legacy archive without it retains its
  authored spacing. A present but incomplete section still fails: broad default
  merging would accidentally accept malformed archived settings.

- **Keys added to an existing section are not the same as a new section.**
  `peel` can be absent as a table, so filling the whole table is safe and a
  present-but-incomplete table can still fail. `tip_shape` and every later
  support key land inside a table every older archive already has, so they
  cannot be distinguished from a typo that omitted `spacing_mm`.
  `fill_legacy_settings` therefore setdefaults missing support keys from
  current defaults and still rejects unknown keys. Do not treat that as
  permission to default-fill a half-written `peel` table.

- **A painted blocker is a centroid, not a triangle index.** Display may
  decimate the model, so cell IDs on the viewport are not source indices.
  Paint stores plate-coordinate centroids and matches samples within half the
  support spacing. Island births are never blocked: an unsupported island will
  not print. Block wins over enforce on the same mark.

- **Multiple models collide only as solids.** Support envelopes may overlap.
  Intersection uses Manifold on each placed part; if a part is not a solid the
  check is skipped rather than inventing an AABB refusal. Extra STLs are
  concatenated into one column field so part-to-part routes already join them.
  `.chop` records extra-model paths in the manifest; they are not yet separate
  archive members.

- **A plate putty-knife bevel insets the top, not the plate contact.**
  `raft_slope_deg` (default 30, from the plate) keeps the hull at Z=0 and
  shrinks only the top ring. The AABB is unchanged; measure top-slice area or
  volume. `base_edge_slope_deg` remains the per-foot-base taper and is refused
  on plate.

- **Per-contact overrides look up by rounded millimetres, never by nearest
  neighbour.** `contact_key` rounds to 1 µm so a GUI float and an STL float
  still match, and a duplicate key is rejected rather than merged. An authored
  record whose position is not a routed contact is reported
  `contact_parameters_unmatched` and is not applied to a nearby contact; a
  moved contact takes its override with it.

- **Drainage bottleneck seeds are voxel indices without an authored origin.**
  `analyze_drainage` stores `analysis_pitch_mm` and `seed_zyx` but not the
  padded grid origin (`bounds[0] - 2 * pitch`). The Faults overlay recovers
  millimetre positions only when the caller supplies the assembly AABB; without
  it those examples cannot be placed in 2D or 3D.

- **STEP import shells out to FreeCAD, not the system Python.** Resolve via
  `VOXELMILL_FREECAD` / `FREECAD`, then PATH (`freecad`, `FreeCAD`,
  `freecadcmd`), then `tools/FreeCAD*.AppImage` / `FreeCAD*.AppImage` under
  the repo root or cwd, then `~/FreeCAD*.AppImage`. Prefer FreeCAD 1.1.3 when
  choosing an AppImage. Tessellation API matches 1.1.1 on a 10×20×30 mm box
  (12 triangles, identical bounds), so 1.1.3 is the N5 pin. Headless traps
  still apply: close stdin, pass args via `FC_SCRIPT_ARGS` (0x1F-joined), no
  `if __name__ == "__main__"` in the `-c` helper, `os._exit` instead of
  `sys.exit`, and log through both `FreeCAD.Console` and `print`. The helper
  can run twice per launch — treat writes as idempotent and take the last
  `VOXELMILL_STEP_REPORT` line.

- **A package rename is not done until the editable install is rebuilt.**
  Renaming `src/chopchop` to `src/voxelmill` leaves a stale
  `_chopchop_editable.pth`, `_chopchop_editable.py`, and a site-packages
  `chopchop/` directory holding the compiled `_native*.so`. Until those are
  deleted and `pip install -e .` is rerun, `import voxelmill` fails while the
  old name keeps resolving, so a green suite would prove nothing. This venv's
  pip is 22.0.2: it has neither `-D` nor `--config-settings`, so the pybind11
  pin goes through the environment instead —
  `SKBUILD_CMAKE_DEFINE="pybind11_DIR=$(.venv/bin/python -c 'import pybind11;
  print(pybind11.get_cmake_dir())')" .venv/bin/pip install -e . --no-build-isolation`.
  Confirm CUDA survived by checking `_native.cuda_status` still exists, not by
  reading the CMake log.

- **In a desktop entry only `Name=` takes the capitalised product name.**
  `Exec=`, `Icon=`, `StartupWMClass=` and `TryExec=` are the console-script and
  icon *basenames*: capitalising them silently breaks launching and icon
  lookup, because the AppImage resolves its icon from the desktop file's
  `Icon=` value against `usr/share/icons/hicolor`. A blanket case-sensitive
  rename will happily rewrite all five. `tests/test_appimage.py` now asserts
  the split rather than the display name alone.

- **Adding a CLI subcommand breaks the CLI/GUI parity test by design.**
  `test_every_cli_subcommand_has_a_place_in_the_editor` keeps an explicit
  subcommand-to-editor-method map, so a new parser entry (`monitor`) fails
  until the editor handler is named there. That is the intended reminder that a
  CLI verb needs a GUI route, not a stale assertion to relax.

- **The symbolic icon variant is white-on-transparent.** It carries no colour
  of its own — 14120 of 65536 pixels at 256 px, all pure white with an alpha
  ramp. It is legible only on a dark ground or where the toolkit recolours it,
  so it is not a drop-in for the dark full-colour icon in an arbitrary small
  context. The application icon stays the dark full-colour variant at every
  size; symbolic ships alongside it for contexts that recolour.

- **Cmd 258 is two different responses behind one command number.** `Url="/"`
  returns storage roots with `storageType`/`totalSize`/`usedSize`; any other
  URL returns directory entries with only `name` and `type`. Reading capacity
  off a directory listing yields `None` forever, and the specification's single
  field table hides the split. `storage()` uses the root listing; `list_files()`
  does not pretend to carry sizes. Unknown sizes stay `None` — a printer that
  omits `totalSize` has not reported zero free bytes.

- **An empty `/usb/` listing is not empty.** With `UsbDiskStatus` 0 the Mars 5
  Ultra answered `/usb/` with 16 records whose `name` was `""` and `type` 0,
  not with an empty list or an error. An entry count is not evidence that media
  is attached; filter on a nonempty `name`.

- **The printer does not sanitize `..` in a Cmd 258 `Url`.** `/local/..` was
  accepted and listed the sibling mount points `mmcblk0p1`/`p2`/`p3`. The
  firmware follows whatever path it is handed, so the `Url` the application
  sends is the only guard, and names coming back are untrusted input when a
  local destination path is built from them. Observed read-only during the
  2026-09-10 sweep; nothing outside `/local/` was opened or downloaded.

- **Cmd 258 names carry a doubled separator.** Every entry came back as
  `/local//name.goo`, not `/local/name.goo`. Normalize before comparing a
  listing against a filename the application chose, or an upload-verification
  match will never fire.

- **A dock's widget is the scroll area, not the page inside it.** Sizing the
  Setup dock from `dock.widget().minimumSizeHint()` reads the `QScrollArea`,
  whose own minimum is about 70 px, so the dock opened far narrower than its
  contents and rows sat behind a horizontal scroll bar. Read
  `scroll.widget().minimumSizeHint()` and add `PM_ScrollBarExtent`. Docks also
  cannot be resized meaningfully before the window has a size, so
  `resizeDocks` belongs in `showEvent`, not in the constructor.

- **A `QFormLayout` row puts its widget in the narrow field column.** The
  object panel is six axis rows plus four nudge buttons each; in a form row it
  was squeezed into the column beside a 220 px label and clipped. `addRow` with
  a single argument spans both columns, which is what a wide composite control
  needs.

- **`QWidget.grab()` does not capture a VTK viewport.** The interactor is a
  native OpenGL surface, so a grabbed screenshot shows black with scattered
  rasterizer noise where the 3D view is while the real render is fine. Judge
  the viewport from the xvfb render test, which reads the render window's own
  buffer, not from a widget grab.

- **A stale autosave blocks an unattended editor launch.** `offer_recovery`
  is modal, so a leftover `~/.cache/voxelmill/autosave.chop` hangs any
  scripted run before the first paint. Give such a run its own
  `XDG_CACHE_HOME` and `XDG_CONFIG_HOME`, and set `VOXELMILL_NO_WIZARD=1`;
  the wizard's TTY check is not enough on its own because `xvfb-run` may still
  present one.

- **Snapping belongs to dragging, not to typing.** The rotation axis slider
  and a drag of the object in the 3D view both round to the snap increment;
  the spin box beside the slider does not, because someone who types 37.5
  means 37.5. Applying the snap in `set_value` would have caught the typed
  path too, so it is applied in the slider handler alone.

- **Paint is one local-frame record per object, and schema 1 is refused.**
  Marks used to be plate-coordinate centroids, so moving a part left its paint
  behind on the faces it no longer covered. They are now stored in each
  object's own mesh frame and converted to plate coordinates at display and
  route time, which is the only place the two representations meet. A
  schema-1 project is refused rather than reinterpreted: its marks carry no
  part attribution, so calling them the primary's would be a guess presented
  as a fact. `normalize_object_paint` raises on the old single
  blocked/enforced table for the same reason.

- **`prepare` takes paint in two shapes and must not confuse them.** `--paint`
  is one plate-coordinate table; a project supplies one local-frame record per
  object. The per-object form cannot be resolved until the placements exist,
  so it is held back and converted after `_append_extra_models`, whose `parts`
  records already carry each placement matrix. Going the other way, a
  `--paint` table saved into a project is recorded against the primary, and
  with added parts present that assumption is written into the report rather
  than made silently.

- **`Document.manifest` carried its own `schema_version` literal.** Bumping
  the constant in `project.py` left the editor writing the old number, and the
  failure only appeared when a saved project was reopened. Both writers now
  import the one constant.

- **An arranged plate must reserve room for a part that has no geometry yet.**
  A duplicated or just-added part exists as a pose before the preview has
  drawn it, and `_object_footprints` filled the gap with a nominal 1 mm
  square. The packer then placed the copy as if it were tiny and the solids
  genuinely overlapped, so the next rebuild died on `models_intersect`. Use
  the largest footprint already known: too much room only spreads the plate,
  too little produces a layout the collision check rejects.

- **Every project edit fell back to the archive except added parts.**
  `cmd_prepare` read rotation, centring, lift, contacts, contact parameters,
  scale, mirror and paint from a `.chop` input, but built `extra_models` from
  the command line alone. A multi-part plate saved in the editor therefore
  prepared as its primary part and nothing said so: the output was a valid
  single-part print of a plate the user had built with several. `prepare
  --project` now also writes `edits.extra_models`, and `save_project` already
  embeds each added mesh as `source/models/N.stl`, so the archive resolves
  after the originals move. An explicit `--add-model` replaces the saved parts
  rather than adding to them, matching every other override.

- **The editor never ran the CLI's island-correction loop.** `pipeline.prepare`
  iterates: plan supports, assemble, reslice, collect `raster_island`
  diagnostics, feed their positions back as extra contacts, repeat up to
  `max_passes`. The editor's `compute_attachments` only flipped
  `support.automatic` on and kicked a single `build_supports` job, so a plate
  prepared in the GUI could keep islands the same geometry would never keep
  through `voxelmill prepare`. Any correction loop has to live where both
  entry points reach it.

- **`load_project` rewrites every mesh path to a hash.** Extracted meshes land
  at `<sha256>.stl` so the archive cannot smuggle a path, which is correct --
  but the object list read its labels from that path and so showed hex names
  after a reopen. A display name has to travel in the manifest beside the
  hash, and must never be used as a filesystem path.

- **A cropped island scan cannot tell a truncated component from an island.**
  The correction loop's middle passes rasterize only a padded box around the
  pillars they just added, which is the only way five passes fit in an
  interactive editor. A real component that extends past that box is cut at
  the edge and reads as a fresh island. `analyze_layers` now records
  `touches_crop_edge` per `raster_island` diagnostic so a cropped caller can
  discard those, and the verdict always comes from a final full scan. A fast
  path that can change a verdict is not a fast path.

- **The no-progress guard must compare like with like.** A local pass counts
  islands in part of the build and a full pass counts them in all of it.
  Comparing a local count against the previous full count reads a smaller
  window as progress and keeps the loop running on nothing, so the guard
  compares each pass only against the last pass of the same scan kind.

- **`passes[-1]['validation']` is not the final validation.** `prepare`
  appends the pass record before the post-plan checks run, so overhangs and
  drainage land in `report['validation']` but not in that snapshot. This
  predates the island-guard rewrite; read the top-level `validation` when you
  want the authoritative checks.

- **The overhang check's match radius is `support.spacing_mm`.** A sample
  counts as supported when a routed contact lies within the configured
  spacing, which is the router's own coverage criterion. Widening the spacing
  therefore weakens the check rather than failing it: at 14 mm spacing seven
  contacts covered 400 downward samples. It is advice about orientation and
  support density, never a proof that the print succeeds.

- **Renaming a settings key breaks every saved project.**
  `resin.cost_per_litre` -> `resin.cost_per_liter` is stored inside the
  project manifest's settings table, and `validate_settings` refuses unknown
  fields, so a project saved before the rename fails to open with
  `Unknown resin fields: ['cost_per_litre']`. This round accepted that
  deliberately; a later one with a stable release cannot.

- **"Supports ignore the rotation" was the viewport lying, not the router.**
  Measured on the real nut rotated (-90, 15, 0): 148 of 168 routed contacts
  land exactly on `downward_contacts` samples of the *placed* mesh, and none
  coincide with samples of the unrotated mesh. The remainder are island
  corrections and model anchors, which are not downward samples by
  definition. What actually happened is that the gizmo's live preview sets a
  `vtkTransform` on the actor and only the commit clears it. Every gizmo
  observer ran at priority 1.0 without ever setting VTK's abort flag, so the
  trackball camera consumed the same press and the drag fought the camera;
  a drag that never delivered its release left the preview transform standing.
  The actor then showed a rotation the document had never accepted, and
  `compute_attachments` -- which no longer does a full reload -- routed the
  pose the document actually held. Display and router disagreed, and the
  supports looked wrong. **A preview transform is a claim the document has
  not agreed to; no long-running operation may start while one is
  outstanding.**

- **A VTK observer that does not abort is not a handler, it is a bystander.**
  `AddObserver(..., 1.0)` only sets priority. Without
  `interactor.GetCommand(observer_id).SetAbortFlag(1)`, the event continues to
  the interactor style as well, so a gizmo drag and a camera orbit run at the
  same time. Keep the ids `AddObserver` returns; you cannot abort without them.

- **Rotation is periodic, so clamping it destroys data.** The object panel's
  rotate rows were built with `minimum=-180, maximum=180` and `AxisRow`
  clamps, while the document does not. A +30 degree commit on top of 170 left
  the document at 200 and the panel at 180, and the next panel edit wrote 180
  back, losing 20 degrees. Wrap into (-180, 180] instead. For the same reason
  a ring drag must accumulate `wrap_angle` of each frame's *increment*: taking
  `wrap_angle` of the total against the drag's start caps a drag at half a
  turn and flips its sign past that.

## v0.2.0 — native core and threading

- **`prepare` spends 97% of its time in `validation.py`, not in the
  rasterizer.** A cProfile of `prepare fixtures/shapes/overhang_bracket.stl`
  (85.5 s under the profiler) puts 83.1 s inside `analyze_layers`: 74.5 s in
  the `_consume` per-layer body, 31.6 s in `_growth_pixels`, 29.7 s in
  `VoidTracker.add`, and 27.4 s of *tottime* in `numpy.ufunc.reduce` — dense
  `.any()` reductions, 32 617 of them reached through `block_any`.
  `raster.slice_coverage`, the actual scan conversion, is 1.3 s. Optimize the
  per-layer analysis; do not start with the rasterizer, and do not put it on a
  GPU on the assumption that scan conversion is the cost.

- **`execution_limits` pinned every thread to one physical core.** It took
  `sorted(os.sched_getaffinity(0))[:budget.workers]`, and cpu numbering puts
  SMT siblings adjacently, so the default `workers=2` selected cpu0 and cpu1 —
  the two hyperthreads of a single P-core, with 23 cores idle. Selecting two
  *distinct* cores instead took the bracket `prepare` from 73.7 s to 61.3 s
  with no other change. Choose cpus through `topology.select_cpus`, never by
  taking the lowest N.

- **An average SMT factor describes no real core on a hybrid CPU.**
  32 logical / 24 physical rounds to 1, which is wrong for the P-cores (2) and
  right for the E-cores by accident. Report the widest core instead.

- **`.goo` files are never byte-identical between two runs.** The header
  carries `file_create_time` from `time.strftime` at write time, plus
  `software_version`. Any equivalence check between versions has to normalize
  those two fields and compare the layer payloads, not the whole file.

- **`header_from_settings` hardcoded the version string.** It wrote
  `software_version='0.1.0'` into every GOO header as a default argument, so a
  release bump silently left stale provenance in the output. It now defaults to
  `voxelmill.__version__`; `scripts/build_appimage.py` had the same literal in
  two places and now reads the package version too.

- **Python startup is not a bottleneck worth rewriting for.**
  `import voxelmill.cli` is 0.10 s and `numpy + scipy + manifold3d` is 0.20 s,
  against runs measured in tens of seconds. Heavy imports are already lazy. A
  standalone native binary is worth building for deployment and for owning the
  scheduling, not for process startup.

- **Nothing in `native/` is actually parallel.** `native/module.cpp` binds
  `tbb::global_control` as `WorkerLimit`, which is a concurrency *ceiling*; no
  kernel in `mesh.cpp`, `raster.cpp`, `voxel.cpp`, `intersections.cpp` or
  `distance.cpp` calls `tbb::parallel_for`. Linking TBB is not using it.

- **Only the `manifold3d` Python wheel exists on this machine — no C++
  headers.** Moving boolean operations into a native core means vendoring
  upstream Manifold through CMake `FetchContent` at a version matching the
  pinned wheel, which is build-system work, not a code port. Roughly 38 call
  sites across `geometry.py`, `assembly.py`, `bases.py`, `ops.py`, `hollow.py`,
  `repair.py`, `support_segments.py` and `pipeline.py` depend on it behaving
  identically.

- **Support "bridging" is called bracing in this codebase.** The feature is
  `supports._brace`, gated by `support.auto_bracing` and sized by
  `brace_spacing_mm`, `brace_max_length_mm`, `brace_diameter_mm`,
  `brace_max_distance_mm`, and the unreleased destination, pattern, angle,
  density, minimum-height, and azimuth controls. Version 0.5.3 removes the old
  bottom-up `brace_start_height_mm`. Grep for `bridge` and you will find only a
  GUI/CLI docstring. Generated fields alone did not make these controls easy
  to find: brace spacing and reach were buried in Advanced settings and the
  long support form. The Bracing tab now groups them explicitly, and the
  main Setup form exposes the common controls at the Simple tier.

- **An inclined cylinder cap leaves a notch beneath a horizontal tip base.**
  A connected boolean alone does not catch this visible defect. Probe volumes
  below the tip base at 20°, 45°, and 70° expose the missing sector. A lower
  hemisphere fills it within the checked shaft capsule; a small buried collar
  provides volumetric overlap without widening the tip taper.

- **Alternating brace levels need shared bands.** Counting every incoming
  diagonal endpoint against the next origin with a symmetric distance test
  suppresses every second alternating level. Alternating and X patterns count
  connections in shoulder-derived bands; the original single pattern retains
  its minimum shared-height interval.

- **Raising `--workers` does nothing, because only the prefetch is parallel.**
  The bracket `prepare` takes 61.3, 61.2, 61.6, 61.0 and 60.5 s at 2, 4, 8, 16
  and 24 workers, while peak RSS climbs from 656 MB to over 1 GB. The pool in
  `analyze_layers` submits only `_label_occupancy`; `_consume` runs on the main
  thread and carries `_growth_pixels`, `forest.add` and the dense reductions.
  Worker count is not a tuning knob until that loop is restructured, so leave
  the default at 2 rather than paying the memory for nothing.

- **`scipy.ndimage` releases the GIL; NumPy reductions do not.** Measured with
  8 Python threads on layer-sized masks: `distance_transform_edt` 4.3x,
  `binary_erosion` 5.0x, `ndi.label` 3.3x, and `ndarray.any()` 0.28x — slower
  than serial. Per-layer scipy work can therefore be threaded in Python before
  any native port; the dense `.any()` reductions cannot, and have to be
  removed by making layers sparse instead of by threading them.

- **Report JSON is deterministic across worker counts, but not textually
  stable.** Runs at 4, 8 and 16 workers produced byte-identical STL output and
  no substantive report difference. What does differ, and must be normalized by
  any equivalence check: `seconds` at every nesting depth, `peak_rss_bytes`,
  `scratch_bytes`, `analysis_workers`, the output path, the per-run scratch
  directory `/tmp/voxelmill-prepare-<random>/`, and `settings.resources.*`.

- **`analyze_layers` parallelizes because the dependency is pairwise, not a
  prefix.** `_consume` looked sequential, but the only cross-layer input is the
  immediately preceding layer's occupancy mask. Given mask[i-1] and mask[i],
  both labelings, the overlap bincount and the growth distance transform are
  independent of every other layer, so they all move to the pool and only the
  accumulators, the bounded diagnostics and the VoidForest union-find stay
  ordered. That took the bracket `prepare` from 61.3 s to 27.8 s. The thing
  that makes it legal is holding the previous mask alongside each in-flight
  layer, which `_layer_worker_cap` has to price into the memory budget.

- **`np.unique` on concatenated border rows was pure overhead.** Both
  `VoidForest.add` and the island check built a border-component set with
  `np.unique(np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1])))`.
  Scattering `True` through those indices into a flags array gives an identical
  result without the sort, and that sort was the single largest NumPy cost in
  the profile (8.2 s, of which 6.9 s was `ndarray.sort`).

- **Worker scaling plateaus at eight, and past it memory grows linearly for
  nothing.** With the parallel stage widened, the bracket `prepare` runs 52.1,
  30.7, 28.8, 27.8, 28.5 and 28.4 s at 2, 4, 6, 8, 12 and 24 workers, while
  peak RSS goes 609 MB, 600, 725, 782, 1010, 1952. The ordered merge stage is
  the Amdahl limit. `topology.PARALLEL_PLATEAU` records the number, and
  `resources.workers = 0` derives `min(plateau, physical cores)`.

- **Layer streams allocate a fresh mask per layer, which is what makes holding
  them safe.** `MeshLayerStream` yields `Layer(index, z, result['mask'])` from a
  frozen dataclass, and `UnionLayerStream` allocates `mask = np.zeros(...)`
  inside its loop before OR-ing each group in. Neither reuses a buffer, so a
  sliding window can keep references to previous layers. Check this again
  before adding a stream that recycles output arrays; it would silently corrupt
  every windowed consumer.

- **A settings default of 0 meaning "derive" has to be allowed by the
  validator.** `tests/test_config.py` listed `{'resources': {'workers': 0}}`
  among the overrides that must be rejected. Turning 0 into the derive sentinel
  makes that assertion wrong, not the code.

- **A spin box range has to admit a derive sentinel, or the GUI silently
  overwrites it.** `PreferencesDialog`'s worker spin box was `setRange(1, 32)`.
  Once the default became 0, `setValue(0)` clamped to 1, the dialog showed 1,
  and Apply wrote `workers = 1` into the document — single-worker mode, the
  slowest possible setting, for anyone who merely opened Preferences. The range
  now starts at 0 with `setSpecialValueText('auto (N)')` so the sentinel is
  both representable and legible. Check every widget bound to a setting whose
  domain gains a sentinel value.

- **Do not edit `src/` while `scripts/equivalence.py` is running.** Both sides
  are subprocesses that import the live working tree, so a mid-run edit gets
  picked up by some scenarios and not others and the comparison silently
  becomes meaningless. The run takes tens of minutes because the old side is
  the slow one; wait it out, or copy the tree first.

- **An equivalence harness that evaluates only at the end runs out of memory.**
  The first full run was killed partway through: it collected every scenario's
  parsed reports into a dict and diffed them after the last one finished, so
  footprint grew monotonically across the fixture set, and because nothing was
  printed until the end the kill destroyed every partial result too. Evaluate
  each scenario as it finishes, keep only the verdict, and print a flushed
  progress line to stderr so a long run is observable and survivable.

- **Path-valued report fields are not only under keys named `path`.** Reports
  also carry `source` (the input STL) and `output` (the written GOO), and both
  embed the tree root, which differs by construction between the two sides.
  Normalizing only `path` made every `prepare` and `slice` compare as DIFFERS
  for a reason that had nothing to do with the code under test.

- **A green equivalence tally is not coverage — read the step columns.** The
  harness stopped a scenario at the first nonzero returncode, and `prepare`
  exits 2 on many fixtures, so six of the thirteen never reached `slice` and
  the GOO encoder was compared on two shapes while the summary line said
  "13/13 scenarios matching". `--allow-unresolved` waives *unresolved islands
  only*; `torus` still fails `enclosed_voids` and `drainage_bottlenecks` and
  exits 2 while writing a perfectly good `prepared.stl`. Any harness that
  treats "both sides failed identically" as a match has to keep comparing
  after that point, or the failure path becomes a coverage hole that reports
  itself as a pass.

- **`prepare` writes its output alongside a failing verdict.** A nonzero exit
  means validation found something, not that nothing was produced. The written
  mesh is complete and worth comparing; skipping it because the command
  "failed" discards the most direct evidence that two trees compute the same
  geometry.

- **Golden-only comparison needs the old root to be absent, not empty.**
  `evaluate_scenario` used to treat `old_steps is None` as an error, so the
  golden diff block was dead code and every check still paid for the v0.1.0
  tree. That is fixed: a missing old root now runs `_evaluate_golden_only`
  against `reports/golden/v010`. A shape that exists under an *existing* old
  root but has no files there is still an error, so a mis-pointed `--old-root`
  cannot silently skip into goldens. The five `fixtures/shapes/invalid/`
  cases are covered old-vs-new only; they have no goldens yet.

- **A check that was skipped must not report `pass`.** `checks` entries are
  initialized optimistically and only downgraded on failure, so gating a
  computation off leaves its verdict reading as verified. `track_voids=False`
  already handled this by forcing `'not_run'`; `check_growth=False` had to do
  the same, and drop `growth_violation_pixels` rather than publish a 0 that
  looks like a measurement. Any new "skip this work" flag inherits this
  obligation.

- **`pipeline._reslice` and `island_guard.scan_assembly_islands` cannot share
  one `analyze_layers`.** They look redundant and are not. The island guard
  rasterizes each assembly group separately and ORs them (assembly.py:270);
  the reslice re-reads the *written* STL as one flat soup under the nonzero
  winding rule, where overlapping shells can cancel (raster.py:125-127). That
  divergence is exactly what `RasterParity` exists to detect, `write_stl`
  downcasts every vertex to float32 on the way out (mesh.py:293), and
  pipeline.py:3-9 states the reslice exists to describe the file actually
  written rather than the in-memory solid. Make each call cheaper; do not
  merge them.

- **A cProfile line number is not a diagnosis.** The profile put `{ndarray.sort}`
  inside `VoidForest.merge` at the top of the list, and that was the right line.
  Three different sort-free rewrites of it then measured 4.40 s, 7.94 s and
  20.80 s on the same dumped label arrays. The one that looked obviously right
  on paper — compacting the label space before scattering — was 33 % *slower*
  end to end than the sort it replaced, because it needs four fancy-index passes
  over the chunk before it can scatter, and NumPy fancy indexing runs at 2-4
  ns/element, dearer per element than the ufunc passes and the sort together.
  Benchmark the candidate against real data; do not reason it out and ship it.

- **Union-find accumulation order is part of the reported number.**
  `VoidForest.union` does `volume[a] += volume[b]` and `trapped += ...` in
  floating point, which is not associative. The sequence of union calls — the
  pairs, their order, and the duplicates that arise when one label pair straddles
  several row chunks — therefore has to be preserved exactly by any rewrite of
  `merge`. Hoisting the per-chunk dedup to once per layer looks like an
  optimization and silently drops those duplicates. The evidence this is real:
  the bracket fixture reports
  `peak_present_trapped_volume_mm3 = 2.0886070650760757e-15`, a pure
  accumulation residue that moves if anything reorders.
  `tests/test_validation.py::test_merge_union_sequence_matches_reference` pins
  the sequence against a kept copy of the original implementation; the reference
  class is deliberate, not dead code.

- **The worker pool being idle is not the same as it being the bottleneck.**
  After the layer analysis was parallelized, 110.9 s of the pool's 190.2
  thread-seconds was `SimpleQueue.get` — idle wait — while one serial stage held
  61 % of wall clock. Eight workers bought 2.90x, not 8x. Look at the serial
  remainder before adding more parallelism.

- **cProfile sees only the calling thread.** Once per-layer analysis moved onto a
  `ThreadPoolExecutor`, a plain `cProfile` run of the default configuration
  undercounted the pool badly and made the remaining serial work look larger
  than it was in CPU terms while hiding where wall clock actually went. Profile
  with `--workers 1` for honest CPU attribution, and separately instrument the
  pool threads (give each its own `cProfile.Profile` via a `Thread.run` patch and
  merge with `pstats.Stats(*files)`) to get wall-clock truth.

- **Ask whether the work can be deleted before working out how to defer it.** A
  full-panel `np.bincount` ran on every layer to feed the `pixels` field of at
  most 128 diagnostics, and the obvious fix was a lazy proxy. The better fix was
  to notice that the consumer already held the label array and was already
  paying a full-panel `labels == component` pass to locate the island: the count
  falls out of that same mask for free. The proxy would have shipped a
  duck-typed object where an ndarray used to be, with a cache and a lifetime
  question across a thread-pool boundary, to buy about 50 ms of amortization
  over a whole build. Deleting the work needed no new type at all.

- **`np.argwhere(mask)[0]` materializes every hit to read the first one.**
  `np.argmax(mask)` short-circuits and allocates nothing; `divmod` by the row
  width recovers the same C-order coordinates. Measured 4.81 ms -> 0.45 ms on a
  3.54 Mpx panel with the component in the final row, which is the worst case
  for the scan.

- **A test can assert on a field and still be no coverage.**
  `tests/test_raster.py:45` asserted `d.details.get('pixels') == 1` on a
  single-pixel island — a value that survives almost any wrong implementation of
  the count, including one that returns a constant. The eager-bincount removal
  was checked against it, passed, and was still unverified until three tests
  with hand-countable distinct areas (1, 4, 7, 12, 15, 20 px) were added and
  mutation-checked by perturbing the count. Prefer fixtures whose expected
  values are distinct and wrong-by-construction if the code is wrong.

- **The benchmark fixture does not exercise the diagnostic paths.**
  `overhang_bracket` reports `island_components: 0` and zero `raster_island`
  diagnostics, so anything touching island reporting is unmeasured and untested
  by the headline benchmark. Check what a fixture actually reports before
  trusting it to cover a change.

- **A partial RLE conversion that scatters dense labels at the boundary is
  not a speedup.** Computing CCL/counts/overlap in run space and then writing
  a full-panel int32 label image cost 15.6 s over 900 layers against 0.11 s
  for the run kernels, and measured 1.06x end to end. The unit of value is
  keeping the whole per-layer path — both CCLs, overlap, border, extent, and
  `VoidForest.merge` pair extraction — in run space. `run_scatter` is for
  tests and the rare mixed-representation merge, never the hot path.
  `VOXELMILL_NATIVE_RUNS=0` is the A/B kill-switch; it must not change
  results, only representation.

- **`VoidForest.add` is not the `analyze_layers` path.** `supports.py`
  indexes `empty_ids[empty_labels.ravel()[closed]]` and needs a dense 2-D
  label array. Leaving `add` on dense components while `analyze_layers`
  stays in run space is deliberate. Scattering at `add`'s return would also
  work, but changing the return shape would not.

- **STEP is the only FreeCAD consumer.** Missing FreeCAD must not block STL,
  Prepare, or slice. The path lives in editor.json (`freecad_path`), with
  `VOXELMILL_FREECAD` winning for CI. The first-run Locate prompt is skipped
  when headless, `VOXELMILL_NO_WIZARD`, or stdin is not a TTY.

- **macOS FreeCAD is a `.app` bundle, not a binary.** `Path.is_file()` is
  false for `/Applications/FreeCAD.app`; the executable is
  `Contents/MacOS/FreeCADCmd` (prefer, headless `-c`) or `FreeCAD`. A Locate
  dialog that required `is_file() and X_OK` rejected the bundle the user
  actually picks, so STEP import stayed disabled. `interpret_freecad_path`
  expands a bundle, a Windows `FreeCAD*/bin` install dir, or a plain
  executable. Store the path the user chose; resolve at use.

- **Do not run modal dialogs or VTK `Initialize()` before `window.show()`.**
  On Darwin a FreeCAD/wizard `QMessageBox` during `MainWindow.__init__`, then
  `QVTKRenderWindowInteractor.Initialize()` on the still-hidden widget, left
  the main window never appearing after the prompt. `run()` shows the window,
  starts VTK, then `complete_startup()`. Tests that construct a window
  directly skip the prompts on purpose.

- **Empty argv on a GUI build must open the editor.** AppRun already rewrote
  this; the PyInstaller Mac/Windows entry did not, so double-clicking
  `VoxelMill.app` / `VoxelMill.exe` printed argparse's required-subcommand
  help and exited. `_desktop_argv` in `cli.main` now matches AppRun when
  PySide6 is importable.

- **A Mac `.app` with PyInstaller `console=True` is `LSBackgroundOnly`.**
  Finder `open` then starts a process (`ApplicationType=BackgroundOnly`) and
  never shows a window, even after argv is rewritten to `gui`. Terminal
  `Contents/MacOS/VoxelMill gui` can still pop Qt dialogs. Build the Mac EXE
  with `console=False` and set `LSBackgroundOnly=False` in the bundle plist.
  Windows stays `console=True` so the zip CLI prints.

- **Finder and Dock ignore Qt's window icon.** PyInstaller `BUNDLE(icon=None)`
  left the bootloader's Python rocket on `VoxelMill.app`. The spec now points
  at `packaging/voxelmill.icns` (`CFBundleIconFile`) and the Windows EXE at
  `packaging/voxelmill.ico`, both written by `scripts/generate_icons.py`.

- **Do not strip `numpy/_core/tests` out of the AppImage.** A blanket
  `tests` ignore on `copytree` dropped `numpy._core.tests`. NumPy 2.2's
  `numpy.testing` imports `numpy._core.tests._natype`, and scipy's
  array-api compat does `from numpy import *`, so `prepare` died with
  `ModuleNotFoundError: numpy._core.tests` while `--version`/`--help`
  stayed green. Stage verification must import `scipy.ndimage` /
  `voxelmill.pipeline`, not only `math` and `_native`.

- **Mac packages from Actions are thin, not universal2.** VTK and PySide6
  wheels are one arch each. Unsigned `.app` will Gatekeeper-warn; users run
  `xattr -dr com.apple.quarantine` on the app. Do not compile on the iMac;
  download the Intel DMG artifact if you want to smoke-test.

- **QVTK paintEvent must not Render inside a Cocoa CATransaction.** 0.5.0
  Intel hung in `vtkCocoaRenderWindow::Render` → `vtkFeatureEdges` during
  `-[_NSOpenGLViewBackingLayer display]` / `handleExposeEvent
  SynchronousDelivery`. Replacing the annotated cube was not enough: 0.5.1
  still hung in `vtkCocoaRenderWindow::Start` on the same expose path
  (sampled 100% of main thread for minutes; Apple Events never ran).
  `vtkGenericOpenGLRenderWindow` crashed on that iMac (`vtkOpenGLState::Pop`
  null, OpenGL 3.2 reported as 0.0) from Finder `open`. Keep the native
  render window, build the nav cube from chamfered polydata + `vtkVectorText`
  (no `vtkAnnotatedCubeActor` / `vtkFeatureEdges`), and on Darwin defer
  `paintEvent` → `Render()` with `QTimer.singleShot(0)` so the transaction
  can finish.

- **A hover-wheel over a spin box is an accidental edit.** Qt's default
  `WheelFocus` changes `QDoubleSpinBox`/`QComboBox` values while the user is
  scrolling the Setup page. Install `FocusedWheelFilter` on the
  `QApplication` so a wheel is ignored until the field has been clicked, walk
  parents so a Cocoa wheel that lands on the inner `QLineEdit` is still
  ignored, and forward it to the enclosing scroll area so the page still moves.

- **Windows VirtualBox guests hang QVTK before `window.show()`.** Detect the
  `VBoxGuest` service and set `QT_OPENGL=software` before `QApplication`; leave
  a real GPU and an already-set `QT_OPENGL` alone.

- **Capture native VTK widgets through the actual window.** `QWidget.grab()`
  repaints the native OpenGL child into a software buffer and produced corrupt
  preview screenshots under Xvfb, although `vtkWindowToImageFilter` frames were
  correct. `dialog.screen().grabWindow(int(dialog.winId()))` captures the rendered
  X11 window correctly. The AppImage acceptance harness uses that path for full
  editor images and VTK readback for viewport-only images.
