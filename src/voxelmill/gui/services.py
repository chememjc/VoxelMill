"""Editor jobs. These call exactly the same core the CLI does.

Nothing here knows about Qt, so each function can be run and tested without a
window, and the CLI and the editor cannot drift apart in what they compute.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import tempfile

import numpy as np

from .. import geometry
from ..contracts import VoxelMillError, Diagnostic, ResourceBudget
from ..mesh import open_stl, write_stl
from ..raster import MeshLayerStream, RasterGrid
from ..assembly import prepare_model, assemble as assemble_parts
from ..pipeline import _append_extra_models, _reslice, support_object_groups
from ..supports import apply_support_validation, build_column_field, plan_supports
from ..validation import (analyze_drainage, analyze_layers, drainage_check,
                          apply_support_void_policy, attribute_drainage_by_model,
                          attribute_enclosed_voids_by_model)


def budget_for(document):
    return ResourceBudget(**document.settings['resources'])


def load_and_place(document, token, progress):
    """Placement plus a scratch copy of the placed triangles.

    The editor always opens the model, fit or not.  A part that does not fit is
    exactly the one somebody needs to see and rotate, and refusing to draw it
    leaves them with an error message and an empty viewport.  ``fits`` and
    ``overflow_mm`` travel with the result so the window can say by how much,
    and the viewport can color the unreachable triangles red.  Export still
    refuses unless clipping was asked for; only loading is permissive.
    """
    budget = budget_for(document)
    with open_stl(document.source, budget, token, progress) as mesh:
        asset = mesh.asset
        search_note = None
        if document.rotation_deg == 'auto':
            try:
                placement = geometry.auto_placement(mesh.triangles, document.settings,
                                                    document.center_offset_mm,
                                                    document.model_lift_mm, token)
            except VoxelMillError as error:
                if error.code != 'no_feasible_placement':
                    raise
                placement, _fits, _overflow = geometry.placement_or_overflow(
                    mesh.triangles, document.settings, (0.0, 0.0, 0.0),
                    document.center_offset_mm, document.model_lift_mm, token,
                    document.scale_factors, document.mirror_axes)
                search_note = ('no orientation fitted the build volume; the unrotated pose is '
                               'shown so it can be inspected and moved')
        else:
            placement, _fits, _overflow = geometry.placement_or_overflow(
                mesh.triangles, document.settings, document.rotation_deg,
                document.center_offset_mm, document.model_lift_mm, token,
                document.scale_factors, document.mirror_axes)
        bounds = np.asarray(placement.bounds, dtype=float)
        fits = geometry.envelope_fits(bounds, document.settings)
        overflow = geometry.envelope_overflow_mm(bounds, document.settings)
        directory = Path(tempfile.mkdtemp(prefix='voxelmill-gui-',
                                          dir=document.settings['resources']['scratch_dir']))
        path = directory / 'placed.f32'
        placed = np.memmap(path, dtype=np.float32, mode='w+', shape=(asset.triangle_count, 3, 3))
        written = 0
        for chunk in geometry.iter_transformed_triangles(mesh.triangles, np.asarray(placement.matrix),
                                                         65536, token):
            placed[written:written + len(chunk)] = chunk
            written += len(chunk)
            progress('place', written, asset.triangle_count)
        placed.flush()
    extra = getattr(document, 'extra_models', None) or ()
    extra_report = None
    if extra:
        placed, extra_report = _append_extra_models(
            placed, extra, document.settings, directory, budget, token, progress)
        combined_bounds = geometry.triangle_bounds(np.asarray(placed), cancel=token)
        fits = geometry.envelope_fits(combined_bounds, document.settings)
        overflow = geometry.envelope_overflow_mm(combined_bounds, document.settings)
    return {'placement': placement, 'asset': asset, 'placed': placed, 'scratch': directory,
            'fits': bool(fits), 'overflow_mm': overflow, 'search_note': search_note,
            # Always a dict. ``None`` used to mean "no extras", but the editor
            # then called ``.get`` on it and every single-STL open died in
            # ``_finish_place`` before the model could be drawn.
            'extra_models': extra_report or {}}


def build_model(document, placed, placement, token, progress):
    """Repair policy and cavity sealing, exactly as ``prepare`` applies them."""
    model = prepare_model(placed, document.settings, budget=budget_for(document),
                          cancel=token, progress=progress)
    report = {**model.repair}
    if model.cavity_fill is not None:
        report['cavity_fill'] = model.cavity_fill
    return {'solid': model, 'repair': report, 'triangles': model.triangles,
            'bounds': model.bounds}


def _plate_paint(document):
    """Per-object local paint collapsed into the plate frame the router uses.

    Routing has always worked in plate coordinates. Paint is now stored per
    object in that object's own frame so it travels with the part, so this is
    the one place the two representations meet.
    """
    from ..paint import to_plate
    records = document.normalized_paint() if hasattr(document, 'normalized_paint') else None
    if not records:
        return None
    matrices = list(getattr(document.derived, 'part_matrices', ()) or ())
    if not matrices:
        return None
    return to_plate(records, matrices)


def _support_replan(document, triangles, bounds, field, budget, token):
    """A ``replan(extra_contacts, progress) -> (plan, raft)`` closure.

    ``build_supports`` and ``route_attachments`` route the same plate with the
    same paint, per-object overrides and support groups; only how many times
    they call it, with what extra contacts, and what progress label they want
    differs. Building the closure once here is what keeps the ``plan_supports``
    argument list in one place instead of two.
    """
    settings = document.settings
    extra_report = getattr(document.derived, 'extra_report', None) or {}
    part_meshes = extra_report.get('placed_parts') or getattr(document.derived, 'part_meshes', None)
    groups = support_object_groups(settings, getattr(document, 'extra_models', None), part_meshes)
    paint = _plate_paint(document)

    def replan(extra_contacts, progress):
        return plan_supports(triangles, bounds, settings, field=field, budget=budget, cancel=token,
                             progress=progress, extra_contacts=extra_contacts,
                             removed_contacts=document.removed_contacts,
                             contact_parameters=getattr(document, 'contact_parameters', ()),
                             paint=paint, object_groups=groups)
    return replan


def _plan_contacts(plan):
    return np.asarray([node.position_mm for node in plan.graph.nodes if node.kind == 'contact'],
                      dtype=float).reshape(-1, 3)


def build_supports(document, triangles, bounds, field, token, progress):
    settings = document.settings
    if field is None:
        field = build_column_field(triangles, bounds, settings, budget=budget_for(document),
                                   cancel=token, progress=progress)
    replan = _support_replan(document, triangles, bounds, field, budget_for(document), token)
    plan, raft = replan(document.manual_contacts, progress)
    return {'field': field, 'plan': plan, 'raft': raft, 'contacts': _plan_contacts(plan)}


def route_attachments(document, triangles, bounds, field, token, progress):
    """Route supports, then keep adding attachments under any island until none remain.

    Wraps ``island_guard.route_without_islands``: its own ``progress`` only
    ever narrates a scan (its ``replan`` argument is this module's own
    closure, and ``assemble`` reports nothing at all), so the wrapping below
    is what lets the caller's status bar say which pass is running and
    whether it is routing, assembling or scanning.
    """
    from ..island_guard import route_without_islands
    settings = document.settings
    budget = budget_for(document)
    if field is None:
        field = build_column_field(triangles, bounds, settings, budget=budget,
                                   cancel=token, progress=progress)
    max_passes = int(settings['support'].get('max_island_passes', 5))
    replan_supports = _support_replan(document, triangles, bounds, field, budget, token)
    # Mutable so the progress wrappers below -- built once, called every pass
    # -- always report the pass that is actually running.
    stage = {'pass': 0}

    def replan(extra_contacts):
        stage['pass'] += 1
        plan, raft = replan_supports(extra_contacts, lambda label, done, total: progress(
            f"pass {stage['pass']}/{max_passes}: routing ({label})", done, total))
        # route_without_islands assembles immediately after this returns and
        # never reports progress while doing it; this is the only chance to
        # name that stage before the next scan's progress calls arrive.
        progress(f"pass {stage['pass']}/{max_passes}: assembling", 1, 1)
        return plan, raft

    def scan_progress(label, done, total):
        progress(f"pass {stage['pass']}/{max_passes}: scanning ({label})", done, total)

    guard = route_without_islands(
        document.derived.solid, settings, replan=replan, budget=budget, cancel=token,
        progress=scan_progress, max_passes=max_passes, extra_contacts=document.manual_contacts)
    return {'field': field, 'plan': guard['plan'], 'raft': guard['raft'], 'union': guard['union'],
            'contacts': _plan_contacts(guard['plan']), 'guard': guard}


def assemble(model, plan, raft, *, cancel=None):
    return assemble_parts(model, plan.solids, raft, cancel=cancel)


def auto_orient_object(triangles, current_rotate_deg, settings, center_offset, lift_mm, token, progress):
    """Orientation search for one already-placed part.

    ``triangles`` are the part's *placed* geometry (current rotation, scale,
    mirror and position already applied -- see
    ``MainWindow._object_placed_triangles``), so ``geometry.auto_placement``
    searches for a rotation on top of that pose, not from the raw import
    frame. Composing its result with ``current_rotate_deg`` is what turns
    that delta back into the absolute ``rotate`` the pose path expects;
    center offset and lift are passed straight through since only the
    orientation is being searched.
    """
    triangles = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
    placement = geometry.auto_placement(triangles, settings, center_offset=center_offset,
                                        lift_mm=lift_mm, cancel=token, progress=progress)
    delta = geometry.rotation_matrix(placement.rotation_deg)
    current = geometry.rotation_matrix(current_rotate_deg)
    absolute = geometry.matrix_to_euler_deg(delta @ current)
    return {'rotate': absolute, 'placement': placement}


def export_and_validate(document, union, path, token, progress, *, drainage=True, track_voids=True):
    """Write the STL, then reopen and reslice it independently."""
    budget = budget_for(document)
    settings = document.settings
    triangles = union.triangle_arrays
    write_stl(path, triangles, token, progress)
    report = _reslice(path, settings, budget, token, progress,
                      track_voids=track_voids, assembly=union)
    fits = geometry.envelope_fits(np.asarray(union.bounding_box()).reshape(2, 3), settings)
    report.checks['plate_fit'] = 'pass' if fits else 'fail'
    if not fits:
        report.diagnostics.append(Diagnostic('no_feasible_placement',
                                             'no feasible placement found for the supported assembly'))
    policy = settings['repair'].get('support_void_policy', 'fail')
    model_group = next((g for g in getattr(union, 'groups', ()) if g.name == 'model'), None)
    support_group = next((g for g in getattr(union, 'groups', ()) if g.name == 'supports_and_raft'), None)
    if drainage:
        drain = analyze_drainage(union.soup_triangles(budget), np.asarray(union.bounding_box()).reshape(2, 3), settings,
                                 budget=budget, cancel=token, progress=progress)
        if policy != 'fail' and support_group is not None and model_group is not None:
            model_bounds = geometry.triangle_bounds(model_group.triangles, cancel=token)
            model_drain = analyze_drainage(model_group.triangles, model_bounds, settings,
                                           budget=budget, cancel=token, progress=progress)
            drain = attribute_drainage_by_model(drain, model_drain)
        report.metrics['drainage'] = drain
        report.checks['drainage_bottlenecks'] = drainage_check(drain)
    else:
        report.checks['drainage_bottlenecks'] = 'not_run'
    if (policy != 'fail' and support_group is not None and model_group is not None
            and track_voids and report.metrics.get('enclosed_voids') is not None):
        model_bounds = geometry.triangle_bounds(model_group.triangles, cancel=token)
        model_stream = MeshLayerStream(model_group.triangles, model_bounds, settings,
                                       budget=budget, cancel=token, progress=progress)
        model_layers = analyze_layers(model_stream, model_stream.grid, settings, cancel=token,
                                      budget=budget, progress=progress, track_voids=True)
        attribute_enclosed_voids_by_model(report.metrics['enclosed_voids'],
                                          model_layers.metrics.get('enclosed_voids'))
    apply_support_void_policy(report, settings)
    # Support routing carries its own evidence (unroutable contacts, support
    # coverage and anchors).  It must affect the GUI export decision just as it
    # does the CLI prepare path.
    if document.derived.plan is not None:
        apply_support_validation(report, document.derived.plan, settings)
    return report


def export_goo(document, source, path, token, progress, *, allow_unresolved=False):
    """Write and independently verify a GOO from an already validated staging STL.

    The GOO module owns byte-level encoding and decoded-pixel verification. The
    GUI only supplies the application-owned staging STL, so it never exposes a
    user's destination to a failed preparation write.
    """
    from ..goo import slice_stl
    return slice_stl(source, path, document.settings, allow_unresolved=allow_unresolved,
                     cancel=token, progress=progress,
                     scratch_dir=document.settings['resources']['scratch_dir'])


class LayerSlicer:
    """Reuse sorted native indices across scrubs of one immutable assembly.

    Native sweeps are mutable: a lock serializes requests, and moving backward
    resets the cursor without re-extracting or sorting the assembly triangles.
    Cancellation invalidates the cursor because it can interrupt a partial sweep.
    """

    def __init__(self, union, settings, budget):
        import threading
        self.union, self.settings, self.budget = union, settings, budget
        self._lock = threading.Lock()
        self._rasters = None
        self._grid = None
        self._last_index = -1

    def slice(self, index, token):
        from .. import _native
        while not self._lock.acquire(timeout=0.05):
            token.check()
        try:
            token.check()
            bounds = self.union.bounds
            height = self.settings['process']['layer_height_mm']
            if index < 0 or index >= max(0, int(np.ceil(bounds[1, 2] / height))):
                raise VoxelMillError('invalid_layer', 'Layer index outside assembly')
            if self._rasters is None:
                grid = RasterGrid.for_bounds(bounds, self.settings, crop=True)
                self.budget.require(grid.width * grid.height * 3 + self.union.num_tri() * 160
                                    + 192 * 1024**2, 'layer preview')
                # Commit only a completely built set, so cancellation during
                # construction cannot leave half of a grouped union cached.
                rasters = [_native.Rasterizer(np.ascontiguousarray(t, dtype=np.float32), token.check)
                           for t in self.union.triangle_arrays]
                self._rasters, self._grid = rasters, grid
            if index < self._last_index:
                for raster in self._rasters:
                    raster.reset()
            grid = self._grid
            mask = np.zeros((grid.height, grid.width), dtype=np.uint8)
            open_rows = 0
            try:
                for raster in self._rasters:
                    token.check()
                    result = raster.slice((index + 0.5) * height, grid.width, grid.height,
                                          grid.x0, grid.y0, grid.dx, grid.dy, token.check, 'nonzero')
                    np.bitwise_or(mask, result['mask'], out=mask)
                    open_rows += result['odd_rows']
            except BaseException:
                for raster in self._rasters:
                    raster.reset()
                self._last_index = -1
                raise
            self._last_index = index
            return {'grid': grid, 'index': index, 'z_mm': (index + 0.5) * height,
                    'mask': mask, 'open_rows': open_rows,
                    'filled_pixels': int(np.count_nonzero(mask))}
        finally:
            self._lock.release()


def slice_layer(union, settings, index, budget, token):
    """One standalone preview; interactive callers retain a LayerSlicer."""
    return LayerSlicer(union, settings, budget).slice(index, token)


# ---- GOO inspection -------------------------------------------------------
#
# The editor can open a finished file and scrub it exactly as it scrubs the
# in-memory union.  Every function here reopens the file, so nothing holds a
# seekable handle across a thread boundary; re-indexing a layer chain costs one
# small read per layer and is invisible next to decoding one full-panel frame.


def open_goo(path):
    """Header, layer table and previews for a finished printer file (GOO or CTB v3)."""
    from ..formats import for_path
    return for_path(path).summary(Path(path))


def goo_layer(path, index, *, cancel=None):
    """One decoded, unmirrored layer of a printer file, in the payload ``LayerView`` expects."""
    from ..formats import for_path
    return for_path(path).display_layer(Path(path), index, cancel=cancel)


def verify_goo_file(path, settings, *, cancel=None, progress=None, track_voids=True):
    """The exact check ``voxelmill verify`` runs, for the editor's menu item."""
    from ..contracts import no_progress
    from ..formats import for_path
    return for_path(path).verify(Path(path), settings, cancel=cancel,
                                 progress=progress or no_progress, track_voids=track_voids)


# ---- whole-file checks the CLI also offers --------------------------------
#
# Both call straight into ``pipeline``, so the editor and the command line
# cannot answer the same question about the same file differently.


def inspect_file(path, settings, *, cancel=None, progress=None, self_intersections=True):
    """``voxelmill inspect`` on one file, for the editor's menu."""
    from ..contracts import no_progress
    from ..pipeline import inspect_stl
    return {'schema_version': 1, 'command': 'inspect', 'input': str(path),
            'meshes': [inspect_stl(path, settings, cancel=cancel,
                                   progress=progress or no_progress,
                                   self_intersections=self_intersections)]}


def validate_file(path, settings, *, cancel=None, progress=None, drainage=True, track_voids=True):
    """``voxelmill validate`` on one file, for the editor's menu."""
    from ..contracts import no_progress
    from ..pipeline import validate_stl
    report = validate_stl(path, settings, cancel=cancel, progress=progress or no_progress,
                          drainage=drainage, track_voids=track_voids)
    return {'schema_version': 1, 'command': 'validate', 'input': str(path),
            'report': report.to_dict()}


def _assembly_layer_report(document, union, token, progress, *, track_voids):
    """The ``analyze_layers`` pass shared by the island scan and the print checks.

    The editor cannot write an STL for every edit, so this rasterizes the
    in-memory assembly directly rather than reopening a file. Keeping the
    choice of stream (an exact manifold vs. grouped raster groups) in one
    place means the island badge and the checks menu can never rasterize the
    same assembly two different ways.
    """
    from ..assembly import UnionLayerStream
    from ..raster import MeshLayerStream
    from ..validation import analyze_layers
    budget = budget_for(document)
    settings = document.settings
    bounds = np.asarray(union.bounding_box()).reshape(2, 3)
    if union.solid is not None:
        stream = MeshLayerStream(union.triangle_arrays[0], bounds, settings,
                                 budget=budget, cancel=token, progress=progress)
    else:
        stream = UnionLayerStream(union.groups, bounds, settings,
                                  budget=budget, cancel=token, progress=progress)
    report = analyze_layers(stream, stream.grid, settings, cancel=token, budget=budget,
                            progress=progress, track_voids=track_voids)
    return report, stream


def scan_islands(document, union, token, progress, *, limit=64):
    """Island-only scan of the in-memory assembly, on the printer lattice.

    It is the same ``analyze_layers`` pass and the same ``island_summary`` the
    ``islands`` command uses; only the source of the layers differs. Nothing is
    written and no export decision is taken from it — export still requires the
    full validation.
    """
    from ..validation import island_summary
    settings = document.settings
    report, stream = _assembly_layer_report(document, union, token, progress, track_voids=False)
    summary = island_summary(report, settings, limit=limit)
    summary['closed_surface'] = 'fail' if stream.open_rows else 'pass'
    summary['open_rows'] = stream.open_rows
    summary['assembly'] = 'exact' if union.solid is not None else 'raster'
    summary['not_examined'] = ['drainage_bottlenecks', 'support_routes', 'plate_fit',
                               'union_raster_parity']
    # Diagnostic is a dataclass, not JSON-serializable on its own; the window
    # feeds this straight into json.dumps for the Report dock.
    summary['diagnostics'] = [asdict(d) for d in report.diagnostics if d.code == 'raster_island']
    return summary


#: Legal values for ``run_print_checks``' ``checks`` argument.
PRINT_CHECK_NAMES = ('islands', 'enclosed_voids', 'overhangs', 'suction_cups', 'drainage')


def run_print_checks(document, union, token, progress, *, checks):
    """Run the named print checks over the current assembly and return one report dict.

    ``checks`` is a set or sequence drawn from :data:`PRINT_CHECK_NAMES`. Every
    check reuses the same analysis its dedicated path already runs --
    ``analyze_layers`` for islands and enclosed voids (the same pass
    ``scan_islands`` uses), ``peel.apply_peel_check`` for suction cups,
    ``validation.analyze_drainage``/``drainage_check`` for drainage exactly as
    ``export_and_validate`` computes it, and ``overhangs.apply_overhang_check``
    for unsupported overhangs -- so a result here can never disagree with the
    dedicated scan or the export validation it stands in for. The returned
    dict is shaped like the reports ``MainWindow._set_report`` already accepts,
    and every diagnostic in it is a plain JSON-able dict.
    """
    from dataclasses import asdict as _asdict
    from ..contracts import ValidationReport
    from ..validation import analyze_drainage, drainage_check, island_summary
    checks = set(checks)
    settings = document.settings
    budget = budget_for(document)
    result_checks, metrics, diagnostics = {}, {}, []

    def _extend(sub_report):
        result_checks.update(sub_report.checks)
        metrics.update(sub_report.metrics)
        diagnostics.extend(_asdict(d) for d in sub_report.diagnostics)

    if 'islands' in checks or 'enclosed_voids' in checks:
        # One rasterization pass serves both: void tracking is only turned on
        # when enclosed voids were actually asked for, so an islands-only run
        # stays as cheap as ``scan_islands``.
        track_voids = 'enclosed_voids' in checks
        report, _stream = _assembly_layer_report(document, union, token, progress,
                                                  track_voids=track_voids)
        if 'islands' in checks:
            summary = island_summary(report, settings)
            result_checks['raster_connectivity'] = summary['check']
            metrics['islands'] = summary
            diagnostics.extend(_asdict(d) for d in report.diagnostics if d.code == 'raster_island')
        if 'enclosed_voids' in checks:
            result_checks['enclosed_voids'] = report.checks.get('enclosed_voids', 'not_run')
            result_checks['transient_traps'] = report.checks.get('transient_traps', 'not_run')
            metrics['enclosed_voids'] = report.metrics.get('enclosed_voids')
            metrics['transient_traps'] = report.metrics.get('transient_traps')
            diagnostics.extend(_asdict(d) for d in report.diagnostics
                               if d.code in ('enclosed_voids', 'transient_trap'))

    if 'suction_cups' in checks:
        from ..peel import apply_peel_check
        bounds = np.asarray(union.bounding_box()).reshape(2, 3)
        sub = ValidationReport()
        apply_peel_check(sub, union.soup_triangles(budget), bounds, settings,
                         budget=budget, cancel=token, progress=progress)
        _extend(sub)

    if 'drainage' in checks:
        bounds = np.asarray(union.bounding_box()).reshape(2, 3)
        drain = analyze_drainage(union.soup_triangles(budget), bounds, settings,
                                 budget=budget, cancel=token, progress=progress)
        metrics['drainage'] = drain
        result_checks['drainage_bottlenecks'] = drainage_check(drain)

    if 'overhangs' in checks:
        try:
            from ..overhangs import apply_overhang_check
        except ImportError as error:
            # The other module may not have landed yet. This must not read as
            # a silent pass: the reason travels in metrics so it still shows
            # in the Report dock rather than just disappearing.
            result_checks['unsupported_overhangs'] = 'not_run'
            metrics['unsupported_overhangs'] = {
                'status': 'not_run', 'reason': f'overhangs module unavailable: {error}'}
        else:
            triangles = document.derived.model_triangles
            plan = document.derived.plan
            contacts = (np.asarray([node.position_mm for node in plan.graph.nodes
                                    if node.kind == 'contact'], dtype=float).reshape(-1, 3)
                       if plan is not None else np.zeros((0, 3), dtype=float))
            sub = ValidationReport()
            apply_overhang_check(sub, triangles, settings, contacts,
                                 budget=budget, cancel=token, progress=progress)
            _extend(sub)

    passed = (bool(result_checks) and all(v in ('pass', 'warn') for v in result_checks.values())
              and not any(d.get('severity') == 'error' for d in diagnostics))
    return {'passed': passed, 'checks': result_checks, 'diagnostics': diagnostics, 'metrics': metrics}


def measure_file(path, settings, *, rotate=(0.0, 0.0, 0.0), center_offset=(0.0, 0.0),
                 lift_mm=5.0, scale=(1.0, 1.0, 1.0), mirror=(False, False, False),
                 target_mm=None, cancel=None, progress=None):
    """The sizes ``voxelmill measure`` prints, on the same function."""
    from ..pipeline import measure_stl
    from ..contracts import no_progress
    return measure_stl(path, settings, rotate=list(rotate), center_offset=tuple(center_offset),
                       lift_mm=float(lift_mm), scale=tuple(scale), mirror=tuple(mirror),
                       target_mm=target_mm, cancel=cancel, progress=progress or no_progress)
