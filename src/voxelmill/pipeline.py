"""End-to-end preparation: place, repair, support, union, export, re-verify.

The export is checked by reopening the written STL and reslicing it with the
same rasterizer the printer path will use, so the evidence describes the file
that was actually written rather than the in-memory solid. Finding a contact
set that leaves no island is ``island_guard.route_without_islands``'s job: it
searches the in-memory assembly and writes nothing. Only the contact set it
settles on ever gets written and reread, which is the treatment the exported
evidence and any export are built from.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import os
from pathlib import Path
import sys
import tempfile
import time

try:
    import resource as _resource
except ImportError:  # Windows
    _resource = None  # type: ignore[assignment]

import numpy as np

from .config import resin_usage
from . import geometry
from .contracts import (CancellationToken, VoxelMillError, Diagnostic, ResourceBudget,
                        no_progress)
from .mesh import open_stl, write_stl
from .raster import MeshLayerStream
from .assembly import prepare_model, assemble, RasterParity
from .supports import plan_supports, build_column_field, apply_support_validation
from .validation import (analyze_layers, analyze_drainage, drainage_check,
                         attribute_drainage_by_model, attribute_enclosed_voids_by_model,
                         apply_support_void_policy)
from .peel import apply_peel_check
from .overhangs import apply_overhang_check
from .island_guard import route_without_islands
from .contact_parameters import normalize_contact_parameters
from .stage_timing import StageTimer

CHUNK = 65536


def normalize_extra_model(spec):
    """Canonical, finite pose record shared by CLI, editor and projects."""
    if not isinstance(spec, dict) or not spec.get('path'):
        raise VoxelMillError('invalid_model', 'Added model needs a path')
    result = dict(spec)
    result['path'] = str(result['path'])
    for key, length, default in (('rotate', 3, (0., 0., 0.)),
                                 ('center_offset', 2, (0., 0.)),
                                 ('scale', 3, (1., 1., 1.))):
        values = [float(value) for value in result.get(key, default)]
        if len(values) != length or not np.isfinite(values).all():
            raise VoxelMillError('invalid_model', f'Added model {key} needs {length} finite values')
        result[key] = values
    mirror = result.get('mirror', (False, False, False))
    if len(mirror) != 3 or any(type(value) is not bool for value in mirror):
        raise VoxelMillError('invalid_model', 'Added model mirror needs three booleans')
    result['mirror'] = list(mirror)
    geometry.scale_matrix(result['scale'], result['mirror'])
    result['lift_mm'] = float(result.get('lift_mm', 5.0))
    if not np.isfinite(result['lift_mm']) or result['lift_mm'] < 0:
        raise VoxelMillError('invalid_model', 'Added model lift must be finite and non-negative')
    result['overrides'] = _normalize_extra_overrides(result.get('overrides'))
    # The human name, not the path, is what the object list shows. A project
    # extracts this file to a hash-named temp path on reopen, so the name has
    # to travel separately or the list ends up showing the hash.
    name = spec.get('name')
    result['name'] = name if isinstance(name, str) and name else Path(result['path']).name
    return result


def _normalize_extra_overrides(overrides):
    """Per-object support overlay. Empty means inherit the plate settings."""
    if overrides in (None, {}):
        return {}
    if not isinstance(overrides, dict):
        raise VoxelMillError('invalid_model', 'Added model overrides must be a table')
    unknown = set(overrides) - {'support'}
    if unknown:
        raise VoxelMillError('invalid_model',
                        'Added model overrides may only include a support table, '
                        f'not {sorted(unknown)}')
    support = overrides.get('support') or {}
    if not isinstance(support, dict):
        raise VoxelMillError('invalid_model', 'Added model support overrides must be a table')
    if not support:
        return {}
    from copy import deepcopy
    from .config import DEFAULTS, _merge, validate_settings
    probe = deepcopy(DEFAULTS)
    _merge(probe['support'], support, 'added model overrides.support')
    validate_settings(probe)
    return {'support': dict(support)}


def support_object_groups(settings, extra_models, part_meshes):
    """Per-part sampling groups when an added model carries support overrides.

    The collision field and validation still run on the whole plate. ``None``
    means the caller should sample the repaired union once, as before.
    """
    extras = list(extra_models or ())
    meshes = list(part_meshes or ())
    if not extras or not meshes or len(meshes) != len(extras) + 1:
        return None
    if not any((spec.get('overrides') or {}).get('support') for spec in extras):
        return None
    from copy import deepcopy
    from .config import _merge, validate_settings
    groups = [{'triangles': meshes[0], 'settings': settings, 'override_keys': ()}]
    for spec, mesh in zip(extras, meshes[1:]):
        overlay = (spec.get('overrides') or {}).get('support') or {}
        if overlay:
            merged = deepcopy(settings)
            _merge(merged['support'], overlay, 'added model overrides.support')
            validate_settings(merged)
            groups.append({'triangles': mesh, 'settings': merged,
                           'override_keys': tuple(overlay)})
        else:
            groups.append({'triangles': mesh, 'settings': settings, 'override_keys': ()})
    return groups


def _signed_volume(triangles, cancel):
    """Divergence-theorem volume in chunks; negative means inverted winding."""
    total = 0.0
    for start in range(0, len(triangles), CHUNK):
        cancel.check()
        chunk = np.asarray(triangles[start:start + CHUNK], dtype=np.float64)
        total += float(np.einsum('ij,ij->i', chunk[:, 0],
                                 np.cross(chunk[:, 1], chunk[:, 2])).sum()) / 6.0
    return total


def _materialize(triangles, matrix, directory, cancel, progress):
    """Write placed triangles to a scratch memmap; sources stay untouched."""
    path = Path(directory) / 'placed.f32'
    placed = np.memmap(path, dtype=np.float32, mode='w+', shape=(len(triangles), 3, 3))
    written = 0
    for chunk in geometry.iter_transformed_triangles(triangles, matrix, CHUNK, cancel):
        placed[written:written + len(chunk)] = chunk
        written += len(chunk)
        progress('place', written, len(triangles))
    placed.flush()
    return path, placed


def _append_extra_models(placed, extra_models, settings, directory, budget, cancel, progress):
    """Place more STLs on the same plate. Refuse only model-solid intersections."""
    import manifold3d as m
    solids = []
    try:
        solids.append(geometry.mesh_to_manifold(np.asarray(placed))[0])
    except VoxelMillError as error:
        if error.code not in {'degenerate_triangles', 'weld_rejected', 'invalid_solid',
                              'self_intersections'}:
            raise
        solids.append(None)
    chunks = [np.asarray(placed, dtype=np.float32)]
    parts = []
    for index, spec in enumerate(extra_models):
        spec = normalize_extra_model(spec)
        cancel.check()
        with open_stl(spec['path'], budget, cancel, progress) as mesh:
            placement = geometry.placement_for_triangles(
                mesh.triangles, settings, spec.get('rotate') or (0.0, 0.0, 0.0),
                spec.get('center_offset') or (0.0, 0.0), spec.get('lift_mm', 5.0),
                cancel, spec.get('scale') or (1.0, 1.0, 1.0),
                spec.get('mirror') or (False, False, False))
            triangles = np.concatenate(list(geometry.iter_transformed_triangles(
                mesh.triangles, np.asarray(placement.matrix), CHUNK, cancel)))
        incoming = None
        try:
            incoming = geometry.mesh_to_manifold(triangles)[0]
        except VoxelMillError as error:
            if error.code not in {'degenerate_triangles', 'weld_rejected', 'invalid_solid',
                                  'self_intersections'}:
                raise
            incoming = None
        for existing in solids:
            if existing is None or incoming is None:
                continue
            if not _boxes_overlap(existing.bounding_box(), incoming.bounding_box()):
                continue  # disjoint boxes cannot share volume; skip the exact boolean
            overlap = m.Manifold.batch_boolean([existing, incoming], m.OpType.Intersect)
            if (overlap.status() == m.Error.NoError and not overlap.is_empty()
                    and overlap.volume() > 1e-9):
                raise VoxelMillError(
                    'models_intersect',
                    'Added model collides with another model solid; overlapping support '
                    'envelopes are allowed and the planner treats every part as one field',
                    {'part': index, 'volume_mm3': float(overlap.volume())})
        solids.append(incoming)
        chunks.append(np.asarray(triangles, dtype=np.float32))
        overlay = spec.get('overrides') or {}
        parts.append({'path': str(spec['path']), 'placement': asdict(placement),
                      'triangles': int(len(triangles)),
                      'overrides': overlay})
    combined = np.concatenate(chunks, axis=0)
    path = Path(directory) / 'placed-combined.f32'
    mapped = np.memmap(path, dtype=np.float32, mode='w+', shape=combined.shape)
    mapped[:] = combined
    mapped.flush()
    return mapped, {'parts': parts, 'collision': 'model_solids_only',
                    'supports': 'shared column field and part-to-part routes',
                    'placed_parts': chunks}


def _boxes_overlap(first, second):
    """Whether two ``(min_x, min_y, min_z, max_x, max_y, max_z)`` boxes share volume."""
    return all(first[axis] < second[axis + 3] and second[axis] < first[axis + 3]
               for axis in range(3))


def _reslice(path, settings, budget, cancel, progress, *, track_voids=True, assembly=None):
    """Reopen the written STL and validate it independently at printer pitch."""
    with open_stl(path, budget, cancel) as reopened:
        bounds = np.asarray(reopened.asset.bounds, dtype=float)
        stream = MeshLayerStream(reopened.triangles, bounds, settings, budget=budget,
                                 cancel=cancel, progress=progress)
        parity = (RasterParity(stream, assembly, budget=budget, cancel=cancel)
                  if assembly is not None and assembly.solid is None
                  and settings['assembly']['require_raster_parity'] else None)
        report = analyze_layers(parity if parity is not None else stream, stream.grid, settings, cancel=cancel, budget=budget,
                                progress=progress, track_voids=track_voids)
        report.diagnostics.extend(stream.diagnostics())
        if stream.open_rows:
            report.checks['closed_surface'] = 'fail'
        else:
            report.checks['closed_surface'] = 'pass'
        if parity is not None:
            parity.apply(report)
        if assembly is not None:
            report.diagnostics.extend(assembly.diagnostics())
            if assembly.solid is None and parity is None:
                report.checks['union_raster_parity'] = 'not_run'
        report.checks['plate_fit'] = 'pass' if geometry.envelope_fits(bounds, settings) else 'fail'
        apply_peel_check(report, reopened.triangles, bounds, settings,
                         budget=budget, cancel=cancel, progress=progress)
        report.metrics['reopened'] = {
            'path': str(path), 'sha256': reopened.asset.sha256,
            'triangles': reopened.asset.triangle_count, 'bounds': reopened.asset.bounds,
            'open_rows': stream.open_rows, 'open_layers': stream.open_layers,
            'negative_winding_crossings': stream.negative_winding_crossings,
            'grid': [stream.grid.width, stream.grid.height],
            'crop_origin_px': [stream.grid.column_offset, stream.grid.row_offset],
        }
    return report


def validate_stl(source, settings, *, budget=None, cancel=None, progress=no_progress,
                 drainage=True, track_voids=True):
    """Reslice an existing STL and check it, with no placement and no export.

    The CLI's ``validate`` and the editor's *Validate STL* both call this, so
    the two cannot answer the same question differently. Nothing is written and
    the source is never modified.
    """
    budget = budget or ResourceBudget(**settings['resources'])
    cancel = cancel or CancellationToken()
    timer = StageTimer()
    with timer.stage('load'):
        opened = open_stl(source, budget, cancel, progress)
    with opened as mesh:
        load_diagnostics = list(mesh.diagnostics)
        bounds = np.asarray(mesh.asset.bounds, dtype=float)
        with timer.stage('reslice'):
            stream = MeshLayerStream(mesh.triangles, bounds, settings, budget=budget, cancel=cancel,
                                     progress=progress)
            report = analyze_layers(stream, stream.grid, settings, cancel=cancel, budget=budget,
                                    progress=progress, track_voids=track_voids)
            report.diagnostics.extend(load_diagnostics)
            report.diagnostics.extend(stream.diagnostics())
            report.checks['closed_surface'] = 'fail' if stream.open_rows else 'pass'
            report.checks['plate_fit'] = 'pass' if geometry.envelope_fits(bounds, settings) else 'fail'
            apply_peel_check(report, mesh.triangles, bounds, settings,
                             budget=budget, cancel=cancel, progress=progress)
            report.metrics['open_rows'] = stream.open_rows
            report.metrics['source'] = {'path': str(mesh.asset.path), 'sha256': mesh.asset.sha256,
                                        'triangles': mesh.asset.triangle_count,
                                        'bounds': mesh.asset.bounds}
        if drainage:
            with timer.stage('drainage'):
                drain = analyze_drainage(mesh.triangles, bounds, settings, budget=budget,
                                         cancel=cancel, progress=progress)
                report.metrics['drainage'] = drain
                report.checks['drainage_bottlenecks'] = drainage_check(drain)
                policy = settings['repair'].get('support_void_policy', 'fail')
                if (policy != 'fail' and report.checks['drainage_bottlenecks'] == 'fail'
                        and not drain.get('enclosed_components')):
                    # One STL carries no record of which triangles are support,
                    # so a neck cannot be attributed the way prepare does. Under
                    # a lenient policy say so rather than failing every
                    # supported export on its own tip crevices; sealed
                    # chambers still fail.
                    report.checks['drainage_bottlenecks'] = 'warn'
                    report.diagnostics.append(Diagnostic(
                        'unattributed_drainage_bottleneck',
                        'Drainage necks were found but a single STL cannot say whether the '
                        'supports or the model form them; rerun with '
                        'repair.support_void_policy=fail to treat them as failures',
                        severity='warning',
                        details={'policy': policy,
                                 'bottlenecked_components': drain.get('bottlenecked_components'),
                                 'bottlenecked_volume_mm3': drain.get('bottlenecked_volume_mm3')}))
        else:
            report.checks['drainage_bottlenecks'] = 'not_run'
    report.metrics['timing'] = timer.as_dict()
    return report


def inspect_stl(source, settings, *, budget=None, cancel=None, progress=no_progress,
                self_intersections=True):
    """Full-resolution mesh inventory, shared by the CLI and the editor."""
    from .mesh import inspect_mesh
    budget = budget or ResourceBudget(**settings['resources'])
    cancel = cancel or CancellationToken()
    with open_stl(source, budget, cancel, progress) as mesh:
        return inspect_mesh(mesh, budget, cancel, progress, self_intersections=self_intersections)


@dataclass
class PrepareRun:
    """State shared by the stages of one :func:`prepare` run.

    Stages read what earlier stages produced and add their own results;
    ``report`` is filled as they go, in the order the evidence is gathered.
    """
    source: Path
    settings: dict
    budget: ResourceBudget
    cancel: CancellationToken
    progress: object
    timer: StageTimer
    report: dict
    scratch: object
    started: float
    # Options resolved by _resolve_options
    max_passes: int = 5
    candidate_count: int = 5
    selected_rank: int = 1
    contact_parameters: object = ()
    extra_models: object = ()
    paint: object = None
    object_paint: object = None
    # Produced by the stages
    asset: dict | None = None
    placement: object = None
    placed: object = None
    load_diagnostics: list = field(default_factory=list)
    transform_note: str | None = None
    part_meshes: object = None
    extra_parts: list = field(default_factory=list)
    model: object = None
    model_triangles: object = None
    plan: object = None
    raft: object = None
    union: object = None
    guard: dict | None = None
    search_passes: list = field(default_factory=list)
    target: Path | None = None
    validation: object = None
    union_bounds: object = None
    fits: bool = True


def prepare(source, settings, *, rotate=None, center_offset=(0.0, 0.0), lift_mm=5.0,
            output=None, components=False, budget=None, cancel=None, progress=no_progress,
            allow_unresolved=False, max_passes=None, drainage=True, track_voids=True,
            overhang_check=True, project=None, manual_contacts=(), removed_contacts=(),
            scale=(1.0, 1.0, 1.0), mirror=(False, False, False),
            candidates=None, candidate_rank=None, contact_parameters=(),
            paint=None, extra_models=()):
    """Prepare one part and return a JSON-serializable evidence report.

    The stages run in order: place the primary part, build the model (added
    parts, repair, hollowing), route supports (with the island search when
    automatic), write and reslice the accepted assembly, record the passes,
    run the advisory checks, publish, and optionally save a project. Only the
    reslice of the written file is evidence; everything before it is search.
    """
    source = Path(source)
    if output and source.resolve() == Path(output).resolve():
        raise VoxelMillError('source_overwrite', 'Output must differ from the original source')
    run = PrepareRun(
        source=source, settings=settings,
        budget=budget or ResourceBudget(**settings['resources']),
        cancel=cancel or CancellationToken(), progress=progress, timer=StageTimer(),
        report={}, scratch=None, started=time.monotonic())
    _resolve_options(run, max_passes, rotate, candidates, candidate_rank, contact_parameters,
                     extra_models, paint)
    run.report.update({'schema_version': 1, 'source': str(source), 'settings': settings,
                       'stages': {}, 'diagnostics': [], 'passes': []})
    run.scratch = tempfile.TemporaryDirectory(prefix='voxelmill-prepare-',
                                              dir=settings['resources']['scratch_dir'])
    try:
        _place_primary(run, rotate, center_offset, lift_mm, scale, mirror, candidates, candidate_rank)
        _build_model(run)
        _route_supports(run, manual_contacts, removed_contacts)
        _write_and_reslice(run, track_voids)
        _record_passes(run)
        _advisory_checks(run, overhang_check, drainage, track_voids)
        _publish(run, output, allow_unresolved, components)
        if project:
            _save_project(run, project, rotate, center_offset, lift_mm, candidates, candidate_rank,
                          manual_contacts, removed_contacts, contact_parameters)
        return _finish_report(run)
    finally:
        _cleanup_scratch(run.scratch)


def _resolve_options(run, max_passes, rotate, candidates, candidate_rank, contact_parameters,
                     extra_models, paint):
    """Check the options that do not need the mesh, before any work starts."""
    settings = run.settings
    if max_passes is None:
        # No caller-supplied cap: the CLI and the editor must agree on how many
        # correction passes are allowed, so both fall back to the same
        # configured limit rather than each guessing their own default.
        max_passes = settings['support'].get('max_island_passes', 5)
    if type(max_passes) is not int or not 1 <= max_passes <= 10:
        raise VoxelMillError('invalid_option', 'max_passes must be an integer from 1 to 10')
    if (candidates is not None or candidate_rank is not None) and rotate != 'auto':
        raise VoxelMillError('invalid_candidate', 'Candidate count and rank require automatic rotation')
    candidate_count = 5 if candidates is None else candidates
    selected_rank = 1 if candidate_rank is None else candidate_rank
    if (type(candidate_count) is not int or not 1 <= candidate_count <= 32 or
            type(selected_rank) is not int or not 1 <= selected_rank <= candidate_count):
        raise VoxelMillError('invalid_candidate', 'Candidate count must be 1 to 32 and rank must lie within that count')
    run.max_passes, run.candidate_count, run.selected_rank = max_passes, candidate_count, selected_rank
    run.contact_parameters = normalize_contact_parameters(contact_parameters, settings)
    run.extra_models = [normalize_extra_model(spec) for spec in extra_models] if extra_models else ()
    # Two shapes reach here. ``--paint`` supplies one plate-coordinate table,
    # which is what routing consumes. A project supplies one local-frame record
    # per object, which cannot be resolved until the placements exist, so it is
    # held back and converted in _build_model.
    from .paint import empty_paint, normalize_object_paint, normalize_paint
    if isinstance(paint, (list, tuple)):
        run.object_paint = normalize_object_paint(paint, len(run.extra_models) + 1)
        run.paint = empty_paint()
    else:
        run.paint = normalize_paint(paint)


def _place_primary(run, rotate, center_offset, lift_mm, scale, mirror, candidates, candidate_rank):
    """Load the source, choose its pose, and write the placed triangles to scratch."""
    settings, report, timer, cancel = run.settings, run.report, run.timer, run.cancel
    # open_stl loads in its constructor, so the load stage wraps construction
    # and place runs while the mesh handle is still held open.
    with timer.stage('load'):
        opened = open_stl(run.source, run.budget, cancel, run.progress)
    with opened as mesh:
        asset = asdict(mesh.asset)
        asset['path'] = str(asset['path'])
        report['asset'] = run.asset = asset
        run.load_diagnostics = list(mesh.diagnostics)
        report['diagnostics'].extend(asdict(item) for item in run.load_diagnostics)
        cancel.check()
        clipping = bool(settings['assembly'].get('clip_to_build_volume', False))
        with timer.stage('place'):
            if rotate == 'auto':
                if not np.allclose(scale, 1.0) or any(mirror):
                    raise VoxelMillError(
                        'invalid_placement',
                        'Automatic orientation search does not yet consider scale or mirror; '
                        'give explicit rotation angles when resizing or mirroring a part')
                try:
                    placement = geometry.auto_placement(mesh.triangles, settings, center_offset,
                                                        lift_mm, cancel, finalists=run.candidate_count,
                                                        candidate_rank=run.selected_rank)
                except VoxelMillError as error:
                    # A search that found nothing feasible has not proved that
                    # nothing fits, so with clipping requested the part is still
                    # placed unrotated and the shortfall is measured.
                    if error.code != 'no_feasible_placement' or not clipping:
                        raise
                    placement, _fits, _overflow = geometry.placement_or_overflow(
                        mesh.triangles, settings, (0.0, 0.0, 0.0), center_offset, lift_mm, cancel,
                        scale, mirror)
                    placement.search = {**(placement.search or {}), 'mode': 'auto_search_exhausted',
                                        'reason': 'no orientation fitted; unrotated pose kept for clipping'}
            elif clipping:
                placement, _fits, _overflow = geometry.placement_or_overflow(
                    mesh.triangles, settings, rotate or (0.0, 0.0, 0.0), center_offset, lift_mm,
                    cancel, scale, mirror)
            else:
                placement = geometry.placement_for_triangles(
                    mesh.triangles, settings, rotate or (0.0, 0.0, 0.0), center_offset, lift_mm,
                    cancel, scale, mirror)
            report['placement'] = asdict(placement)
            if rotate == 'auto':
                report['stages']['orientation_selection'] = {
                    'requested_candidates': run.candidate_count,
                    'available_candidates': len(placement.search.get('ranked_candidates', [])),
                    'selected_rank': placement.search.get('selected_rank'),
                    'persist_as_explicit_pose': candidates is not None or candidate_rank is not None,
                }
            # A resized or mirrored part is a different part.  Say so in the
            # report and as a warning diagnostic that survives into the archive,
            # rather than leaving it to be inferred from a matrix.
            note = geometry.scale_note(placement.scale, placement.mirror)
            report['stages']['transform'] = {
                'scale': list(placement.scale), 'mirror': list(placement.mirror),
                'note': note,
                'source_size_mm': (np.asarray(mesh.asset.bounds, dtype=float)[1]
                                   - np.asarray(mesh.asset.bounds, dtype=float)[0]).tolist(),
                'placed_size_mm': (np.asarray(placement.bounds, dtype=float)[1]
                                   - np.asarray(placement.bounds, dtype=float)[0]).tolist(),
            }
            if note is not None:
                run.transform_note = note
            report['stages']['build_volume'] = {
                'clip_to_build_volume': clipping,
                'fits': bool(geometry.envelope_fits(np.asarray(placement.bounds, dtype=float), settings)),
                'overflow_mm': geometry.envelope_overflow_mm(
                    np.asarray(placement.bounds, dtype=float), settings),
                'build_mm': list(settings['printer']['build_mm']),
                'edge_clearance_mm': settings['printer']['edge_clearance_mm'],
            }
            _placed_path, run.placed = _materialize(mesh.triangles, np.asarray(placement.matrix),
                                                    run.scratch.name, cancel, run.progress)
    run.placement = placement


def _build_model(run):
    """Add the other parts, resolve paint to the plate, repair, and hollow."""
    settings, report, cancel = run.settings, run.report, run.cancel
    if run.extra_models:
        run.placed, extra_report = _append_extra_models(
            run.placed, run.extra_models, settings, run.scratch.name, run.budget, cancel, run.progress)
        run.part_meshes = extra_report.pop('placed_parts', None)
        run.extra_parts = extra_report.get('parts') or []
        report['stages']['extra_models'] = extra_report
    if run.object_paint is not None:
        from .paint import to_plate
        run.paint = to_plate(run.object_paint, [np.asarray(run.placement.matrix, dtype=float)] + [
            np.asarray(part['placement']['matrix'], dtype=float) for part in run.extra_parts])
    signed_volume = _signed_volume(run.placed, cancel)
    with run.timer.stage('repair'):
        model = prepare_model(run.placed, settings, budget=run.budget, cancel=cancel,
                              progress=run.progress, source_volume=signed_volume)
        report['stages']['repair'] = model.repair
    if settings['hollow']['enabled']:
        from .hollow import apply_hollow
        with run.timer.stage('hollow'):
            model, hollow_report = apply_hollow(model, settings, budget=run.budget, cancel=cancel,
                                                progress=run.progress)
            report['stages']['hollow'] = hollow_report
    if model.cavity_fill is not None:
        report['stages']['cavity_fill'] = model.cavity_fill
    run.model, run.model_triangles = model, model.triangles
    report['stages']['model'] = {'triangles': int(len(model.triangles)),
                                 'volume_mm3': float(model.solid.volume()) if model.solid is not None else None,
                                 'genus': int(model.solid.genus()) if model.solid is not None else None}


def _route_supports(run, manual_contacts, removed_contacts):
    """Plan supports once (manual) or search for a contact set with no island (automatic)."""
    settings, cancel, budget, progress = run.settings, run.cancel, run.budget, run.progress
    model, model_triangles = run.model, run.model_triangles
    model_bounds = geometry.triangle_bounds(model_triangles, cancel=cancel)
    with run.timer.stage('supports'):
        column_field = build_column_field(model_triangles, model_bounds, settings, budget=budget,
                                          cancel=cancel, progress=progress)

        def replan(extra_contacts):
            return plan_supports(model_triangles, model_bounds, settings, field=column_field,
                                 budget=budget, cancel=cancel, progress=progress,
                                 extra_contacts=extra_contacts, removed_contacts=removed_contacts,
                                 contact_parameters=run.contact_parameters, paint=run.paint,
                                 object_groups=support_object_groups(
                                     settings, run.extra_models, run.part_meshes))
        if not settings['support']['automatic']:
            # Manual routing: plan once here. Automatic mode plans inside
            # island_guard via the same replan callable.
            run.plan, run.raft = replan(list(manual_contacts))
    if settings['support']['automatic']:
        # The search for a contact set that leaves no island runs on the
        # in-memory assembly and never writes anything; only the accepted
        # result gets written, reread and rechecked.
        with run.timer.stage('island_guard'):
            guard = route_without_islands(model, settings, replan=replan, budget=budget,
                                          cancel=cancel, progress=progress, max_passes=run.max_passes,
                                          extra_contacts=manual_contacts)
            run.plan, run.raft, run.union = guard['plan'], guard['raft'], guard['union']
        run.guard = guard
        # A guard that settled on its first, whole-build scan finding
        # nothing to fix ran no real correction. The full reslice reports
        # that identical clean state with actual validation attached, so
        # keeping this scan too would be noise, not evidence.
        if not (len(guard['passes']) == 1 and not guard['passes'][0]['islands']):
            run.search_passes = guard['passes']
    else:
        # automatic=False means the plate is routed by hand; the loop must
        # not go add contacts of its own even if the one pass it is given
        # leaves islands behind.
        with run.timer.stage('assemble'):
            run.union = assemble(model, run.plan.solids, run.raft, budget=budget, cancel=cancel)
    run.report['stages']['assembly'] = run.union.report


def _write_and_reslice(run, track_voids):
    """Write the accepted assembly to scratch and validate the written file."""
    settings, union = run.settings, run.union
    run.union_bounds = np.asarray(union.bounding_box()).reshape(2, 3)
    run.fits = geometry.envelope_fits(run.union_bounds, settings)
    triangles = union.triangle_arrays
    target = Path(run.scratch.name) / 'prepared.stl'
    target.parent.mkdir(parents=True, exist_ok=True)
    with run.timer.stage('write'):
        write_stl(target, triangles, run.cancel, run.progress)
    run.target = target
    # The accepted contact set always gets the full treatment: written,
    # reread and rechecked by the same rasterizer the printer path uses.
    # Nothing before this is evidence that a check actually ran.
    with run.timer.stage('reslice'):
        validation = _reslice(target, settings, run.budget, run.cancel, run.progress,
                              track_voids=track_voids, assembly=union)
    apply_support_validation(validation, run.plan, settings)
    validation.checks['plate_fit'] = 'pass' if run.fits else 'fail'
    if not run.fits:
        validation.diagnostics.append(Diagnostic(
            'no_feasible_placement', 'no feasible placement found for the supported assembly',
            details={'bounds': run.union_bounds.tolist()}))
    guard = run.guard
    if guard is not None and not guard['resolved']:
        # The guard gave up with islands still on the plate; the full reslice
        # will already fail its own connectivity check on the same evidence,
        # but this says why the search stopped rather than leaving that to be
        # inferred from a pass count.
        validation.diagnostics.append(Diagnostic(
            'correction_incomplete',
            f"Island correction stopped with {guard['islands_remaining']} island(s) still "
            f"unsupported after {len(guard['passes'])} pass(es)",
            severity='warning', details={'islands_remaining': guard['islands_remaining'],
                                         'stopped': (guard['passes'][-1].get('stopped')
                                                    if guard['passes'] else None)}))
    run.validation = validation


def _record_passes(run):
    """Search passes, then the one full reslice pass that carries validation."""
    union, validation = run.union, run.validation
    for search in run.search_passes:
        # A search pass only ever answers "does an island remain"; say so
        # explicitly rather than leaving the shape of a full pass record with
        # keys that were never actually measured for it.
        run.report['passes'].append({
            **search, 'kind': 'search',
            'validation': 'not_run: this pass rasterized the assembly directly and did not '
                          'write or reslice an STL, so peel, raster parity, drainage and '
                          'support-routing checks were never evaluated for it'})
    run.report['passes'].append({
        'pass': len(run.search_passes) + 1, 'kind': 'full_reslice',
        'supports': run.plan.metrics, 'raft': run.raft is not None,
        'union_triangles': int(union.num_tri()), 'union_volume_mm3': union.volume(),
        'union_bounds': run.union_bounds.tolist(), 'plate_fit': bool(run.fits),
        'exported_triangles': union.num_tri(),
        'raster_volume_mm3': validation.metrics.get('raster_volume_mm3'),
        'volume_note': None if union.solid is not None else 'soup signed volume is not physical resin volume',
        'validation': validation.to_dict()})
    if union.report.get('support_cavity_fill') is not None:
        run.report['stages']['support_cavity_fill'] = union.report['support_cavity_fill']


def _advisory_checks(run, overhang_check, drainage, track_voids):
    """Overhang coverage, drainage, support-void attribution and the diagnostics
    that travel with the report."""
    settings, union, validation = run.settings, run.union, run.validation
    cancel, budget, progress = run.cancel, run.budget, run.progress
    policy = settings['repair'].get('support_void_policy', 'fail')
    model_group = next((g for g in union.groups if g.name == 'model'), None)
    support_group = next((g for g in union.groups if g.name == 'supports_and_raft'), None)
    if overhang_check:
        # Advice about orientation and support density, not a gate: a gap here
        # means the router itself left a downward sample uncovered, never that
        # the print is proven to fail.
        contacts = np.asarray([node.position_mm for node in run.plan.graph.nodes
                               if node.kind == 'contact'], dtype=float).reshape(-1, 3)
        apply_overhang_check(validation, run.model_triangles, settings, contacts,
                             budget=budget, cancel=cancel, progress=progress)
    else:
        validation.checks['unsupported_overhangs'] = 'not_run'
    if drainage:
        try:
            with run.timer.stage('drainage'):
                drain = analyze_drainage(union.soup_triangles(budget),
                                         np.asarray(union.bounding_box()).reshape(2, 3),
                                         settings, budget=budget, cancel=cancel, progress=progress)
                if policy != 'fail' and support_group is not None and model_group is not None:
                    model_bounds = geometry.triangle_bounds(model_group.triangles, cancel=cancel)
                    model_drain = analyze_drainage(model_group.triangles, model_bounds, settings,
                                                   budget=budget, cancel=cancel, progress=progress)
                    drain = attribute_drainage_by_model(drain, model_drain)
                validation.checks['drainage_bottlenecks'] = drainage_check(drain)
                validation.metrics['drainage'] = drain
                if drain.get('bottlenecked_components'):
                    validation.diagnostics.append(Diagnostic(
                        'drainage_bottleneck',
                        'Void connects to the exterior only through an orifice below the configured area',
                        details=drain))
        except VoxelMillError as error:
            if error.code == 'canceled':
                raise
            validation.checks['drainage_bottlenecks'] = 'not_run'
            validation.diagnostics.append(Diagnostic(
                'drainage_not_run', str(error), severity='warning', details=error.details))
    else:
        validation.checks['drainage_bottlenecks'] = 'not_run'
    if (policy != 'fail' and support_group is not None and model_group is not None
            and track_voids and validation.metrics.get('enclosed_voids') is not None):
        model_bounds = geometry.triangle_bounds(model_group.triangles, cancel=cancel)
        model_stream = MeshLayerStream(model_group.triangles, model_bounds, settings,
                                       budget=budget, cancel=cancel, progress=progress)
        model_layers = analyze_layers(model_stream, model_stream.grid, settings, cancel=cancel,
                                      budget=budget, progress=progress, track_voids=True)
        attribute_enclosed_voids_by_model(validation.metrics['enclosed_voids'],
                                          model_layers.metrics.get('enclosed_voids'))
    if policy == 'fill' and union.solid is None:
        fill = union.report.get('support_cavity_fill') or {
            'status': 'not_run',
            'operation': 'fill_enclosed_shells',
            'reason': 'support_void_policy=fill needs an exact union solid to decompose',
            'consequence': 'enclosed voids remain; drainage necks are never filled by this policy',
        }
        run.report['stages']['support_cavity_fill'] = fill
        validation.metrics['support_cavity_fill'] = fill
    apply_support_void_policy(validation, settings)
    with run.timer.stage('collision_audit'):
        _collision_audit(run)
    if run.load_diagnostics:
        validation.diagnostics.extend(run.load_diagnostics)
    if run.transform_note is not None:
        # A warning, not an error: the user asked for this and it must not
        # block their export.  It does have to be impossible to miss.
        validation.diagnostics.append(Diagnostic(
            'model_transformed', f'The model was {run.transform_note}', severity='warning',
            details=run.report['stages']['transform']))
    run.report['validation'] = validation.to_dict()
    run.report['support_graph'] = asdict(run.plan.graph)


def _collision_audit(run):
    """Exact support-into-part and support-into-support evidence for the plate.

    A warning, not a gate: the router avoids collisions by construction on a
    sampled field, and this is the independent check of that claim.
    """
    from .collisions import support_model_intrusion, support_overlaps
    validation, plan = run.validation, run.plan
    if run.part_meshes:
        parts = []
        for mesh in run.part_meshes:
            try:
                parts.append(geometry.mesh_to_manifold(np.asarray(mesh))[0])
            except VoxelMillError as error:
                if error.code == 'canceled':
                    raise
                parts.append(None)
    else:
        parts = [run.model.solid]
    intrusion = support_model_intrusion(plan.solids, parts, plan.graph, run.settings,
                                        cancel=run.cancel)
    overlaps = support_overlaps(plan.graph)
    audit = {**intrusion, 'support_overlaps': overlaps['overlaps'],
             'support_overlap_examples': overlaps['examples'],
             'basis': 'exact boolean of support solids against each part; graph capsules '
                      'for support pairs; tip and anchor pieces are expected'}
    validation.metrics['support_collisions'] = audit
    if intrusion['unchecked_parts'] == len(parts):
        # No exact part solid (raster union path): the audit is evidence, not
        # a check, so its absence must not turn a passing export into a
        # not-run failure.
        audit['status'] = 'not_run: no exact part solid to intersect'
        return
    found = intrusion['intrusions'] or overlaps['overlaps']
    validation.checks['support_collisions'] = 'warn' if found else 'pass'
    if intrusion['intrusions']:
        validation.diagnostics.append(Diagnostic(
            'support_model_intrusion', 'A support passes through a part away from its tip or anchor',
            severity='warning', position_mm=(intrusion['worst'] or {}).get('center_mm'),
            details={key: intrusion[key] for key in ('intrusions', 'intrusion_volume_mm3', 'worst',
                                                     'allowance_mm')}))
    if overlaps['overlaps']:
        validation.diagnostics.append(Diagnostic(
            'support_overlap', 'Two supports overlap where the support graph does not join them',
            severity='warning', details={'overlaps': overlaps['overlaps'],
                                         'examples': overlaps['examples'][:8]}))


def _publish(run, output, allow_unresolved, components):
    """Copy the staged file to ``output`` only when validation allows it."""
    validation, report = run.validation, run.report
    if output and not validation.passed and not allow_unresolved:
        # The evidence is the product; only the geometry file is withheld.
        report['export'] = {
            'written': False, 'path': str(output),
            'reason': 'validation did not pass; rerun with --allow-unresolved to keep a warned export',
            'failed_checks': [name for name, state in validation.checks.items()
                              if state not in ('pass', 'warn')]}
    elif output:
        atomic_copy(run.target, output, run.cancel)
        validation.metrics['reopened']['path'] = str(output)
        report['validation'] = validation.to_dict()
        written = {'written': True, 'path': str(output), 'warned': not validation.passed,
                   'triangles': int(run.union.num_tri())}
        if components:
            written['components'] = _write_components(Path(output), run.model, run.plan, run.raft,
                                                      run.cancel, run.progress)
        report['export'] = written


def _save_project(run, project, rotate, center_offset, lift_mm, candidates, candidate_rank,
                  manual_contacts, removed_contacts, contact_parameters):
    """Archive the source and every authored decision so the plate reopens as it was."""
    from copy import deepcopy
    from .paint import empty_paint
    from .project import SCHEMA_VERSION as PROJECT_SCHEMA_VERSION, save_project
    report, placement = run.report, run.placement
    report['project'] = str(project)
    # Projects store paint per object, in that object's own frame. A project
    # supplied it that way already. A --paint table is plate coordinates with
    # no part attribution, so it is recorded against the primary: that is exact
    # for a single part, and with added parts present the assumption is stated
    # in the report rather than made silently.
    if run.object_paint is not None:
        saved_paint = run.object_paint
    else:
        from .paint import to_local
        primary = empty_paint()
        for kind in ('blocked', 'enforced'):
            marks = run.paint.get(kind) or []
            if marks:
                primary[kind] = [[float(v) for v in point]
                                 for point in to_local(marks, np.asarray(placement.matrix, dtype=float))]
        saved_paint = [primary] + [empty_paint() for _ in run.extra_parts]
        if run.extra_parts and (primary['blocked'] or primary['enforced']):
            report['stages'].setdefault('paint', {})['attribution'] = (
                'plate-coordinate --paint marks were recorded against the primary part')
    save_project(project, {'schema_version': PROJECT_SCHEMA_VERSION, 'settings': run.settings,
                           'placement': asdict(placement), 'source': {'sha256': run.asset['sha256']},
                           # A chosen finalist is an authored pose. Reopening
                           # must not rerun a default search and pick rank 1.
                           'rotation_deg': (list(placement.rotation_deg)
                                            if candidates is not None or candidate_rank is not None
                                            else rotate or [0.0, 0.0, 0.0]),
                           'center_offset_mm': list(center_offset), 'model_lift_mm': lift_mm,
                           # The same top-level keys Document.manifest writes.
                           # placement.scale is history of the matrix; these are
                           # the authored decision the reload restores, and
                           # omitting them silently reset a scaled project to
                           # 1.0 on reopen.
                           'scale_factors': list(placement.scale),
                           'mirror_axes': [bool(v) for v in placement.mirror],
                           'edits': {'manual_contacts': list(manual_contacts),
                                     'removed_contacts': list(removed_contacts),
                                     'contact_parameters': list(contact_parameters),
                                     'paint': saved_paint,
                                     # save_project embeds each of these as
                                     # source/models/N.stl, so the archive stays
                                     # portable after the originals move.
                                     'extra_models': [deepcopy(spec) for spec in run.extra_models]},
                           'support_graph': asdict(run.plan.graph),
                           'validation': run.validation.to_dict(),
                           'stages': report['stages']}, run.source)


def _finish_report(run):
    """Resin usage, timings and resource figures; returns the finished report."""
    report = run.report
    # Cured resin from the raster measurement, which already counts the
    # supports and any base.  A soup's signed volume is not physical volume,
    # so the raster figure is the only honest source here.
    report['stages']['resin_usage'] = resin_usage(
        run.settings, run.validation.metrics.get('raster_volume_mm3'))
    report['seconds'] = time.monotonic() - run.started
    report['timing'] = run.timer.as_dict()
    if _resource is not None:
        rss = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
        # Linux reports kilobytes; macOS reports bytes.
        report['peak_rss_bytes'] = int(rss) if sys.platform == 'darwin' else int(rss) * 1024
    else:
        report['peak_rss_bytes'] = None
    report['scratch_bytes'] = sum(p.stat().st_size for p in Path(run.scratch.name).rglob('*')
                                  if p.is_file())
    return report


def _cleanup_scratch(scratch):
    """Remove the prepare scratch dir; ignore Windows sharing violations.

    ``placed.f32`` can still be mapped when TemporaryDirectory.cleanup runs
    on Windows, which used to turn a successful ``prepare`` into exit 1.
    """
    try:
        scratch.cleanup()
    except OSError:
        pass


def _write_components(output, model, plan, raft, cancel, progress):
    import manifold3d as m
    written = {}
    stem = output.with_suffix('')
    written['model'] = str(stem) + '-model.stl'
    write_stl(written['model'], model.triangles, cancel, progress)
    if plan.solids:
        supports = m.Manifold.batch_boolean(plan.solids, m.OpType.Add)
        written['supports'] = str(stem) + '-supports.stl'
        write_stl(written['supports'], geometry.manifold_triangles(supports), cancel, progress)
    if raft is not None:
        written['raft'] = str(stem) + '-raft.stl'
        write_stl(written['raft'], geometry.manifold_triangles(raft), cancel, progress)
    return written


def atomic_copy(source, destination, cancel=None):
    """Publish a checked artifact without exposing partial or rejected output."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{destination.name}.', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as dst, open(source, 'rb') as src:
            while block := src.read(1024 * 1024):
                if cancel:
                    cancel.check()
                dst.write(block)
            dst.flush()
            os.fsync(dst.fileno())
        if cancel:
            cancel.check()
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def scan_islands(source, settings, *, budget=None, cancel=None, progress=no_progress,
                 limit=64):
    """Island-only connectivity scan of an existing STL.

    This is the same ``analyze_layers`` pass ``validate`` runs, with void
    tracking and drainage switched off, so it answers one question quickly
    rather than answering every question slowly. It is **not** a substitute for
    ``validate``: enclosed voids, transient traps, growth spans and drainage are
    not examined and are reported as ``not_run`` rather than as passes.
    """
    from .validation import island_summary
    budget = budget or ResourceBudget(**settings['resources'])
    cancel = cancel or CancellationToken()
    with open_stl(source, budget, cancel, progress) as mesh:
        bounds = np.asarray(mesh.asset.bounds, dtype=float)
        stream = MeshLayerStream(mesh.triangles, bounds, settings, budget=budget,
                                 cancel=cancel, progress=progress)
        report = analyze_layers(stream, stream.grid, settings, cancel=cancel, budget=budget,
                                progress=progress, track_voids=False)
        summary = island_summary(report, settings, limit=limit)
        summary['source'] = {'path': str(mesh.asset.path), 'sha256': mesh.asset.sha256,
                             'triangles': mesh.asset.triangle_count}
        summary['closed_surface'] = 'fail' if stream.open_rows else 'pass'
        summary['open_rows'] = stream.open_rows
        # Checks this pass never attempts at all.  The ones it attempts state
        # their own status in other_checks, including not_run.
        summary['not_examined'] = ['peel_risk', 'drainage_bottlenecks', 'support_routes', 'plate_fit',
                                   'union_raster_parity']
    return summary


def measure_stl(source, settings, *, rotate=None, center_offset=(0.0, 0.0), lift_mm=5.0,
                scale=(1.0, 1.0, 1.0), mirror=(False, False, False), target_mm=None,
                budget=None, cancel=None, progress=no_progress):
    """Sizes a part has, and would have under a proposed pose. Writes nothing.

    ``target_mm`` answers the question a scale flag raises in reverse: given a
    wanted size, what factor reaches it. A zero on an axis means "do not
    constrain this axis", so a part can be sized on its critical dimension
    alone. The uniform factor is the smallest of the per-axis ones, because
    scaling up to the largest would overshoot every other axis.
    """
    budget = budget or ResourceBudget(**settings['resources'])
    cancel = cancel or CancellationToken()
    if rotate == 'auto':
        raise VoxelMillError('invalid_option',
                        'measure takes explicit angles; an automatic search result is not a '
                        'measurement of a pose the caller chose')
    with open_stl(source, budget, cancel, progress) as mesh:
        source_bounds = np.asarray(mesh.asset.bounds, dtype=float)
        placement, fits, overflow = geometry.placement_or_overflow(
            mesh.triangles, settings, rotate or (0.0, 0.0, 0.0), center_offset, lift_mm,
            cancel, scale, mirror)
        asset = asdict(mesh.asset)
        asset['path'] = str(asset['path'])
    placed_bounds = np.asarray(placement.bounds, dtype=float)
    source_size = source_bounds[1] - source_bounds[0]
    placed_size = placed_bounds[1] - placed_bounds[0]
    payload = {
        'source': asset,
        'source_size_mm': source_size.tolist(),
        'source_center_mm': source_bounds.mean(axis=0).tolist(),
        'placed_size_mm': placed_size.tolist(),
        'placed_bounds_mm': placed_bounds.tolist(),
        'diagonal_mm': float(np.linalg.norm(placed_size)),
        'scale': list(placement.scale), 'mirror': list(placement.mirror),
        'transform_note': geometry.scale_note(placement.scale, placement.mirror),
        'fits': bool(fits), 'overflow_mm': list(overflow),
        'usable_build_mm': [float(settings['printer']['build_mm'][0]
                                  - 2 * settings['printer']['edge_clearance_mm']),
                            float(settings['printer']['build_mm'][1]
                                  - 2 * settings['printer']['edge_clearance_mm']),
                            float(settings['printer']['build_mm'][2])],
        'layers_at_current_height': int(math.ceil(placed_bounds[1][2]
                                                  / settings['process']['layer_height_mm'])),
        'establishes': 'the size and plate fit of this pose',
        'does_not_establish': 'printability; run prepare and validate for that',
    }
    if target_mm is not None:
        wanted = np.asarray(target_mm, dtype=float)
        if wanted.shape != (3,) or not np.isfinite(wanted).all() or (wanted < 0).any():
            raise VoxelMillError('invalid_option', '--target-mm takes three finite nonnegative sizes')
        constrained = (wanted > 0) & (placed_size > 0)
        if not constrained.any():
            raise VoxelMillError('invalid_option',
                            'every requested target axis is zero or has no extent to scale')
        per_axis = np.ones(3)
        per_axis[constrained] = wanted[constrained] / placed_size[constrained]
        payload['target_mm'] = wanted.tolist()
        payload['per_axis_factor'] = per_axis.tolist()
        payload['uniform_factor'] = float(per_axis[constrained].min())
        payload['target_note'] = ('the uniform factor is the smallest per-axis factor, so no '
                                  'constrained axis overshoots its target')
    return payload
