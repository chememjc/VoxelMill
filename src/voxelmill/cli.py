"""Command line entry point.

Every command resolves settings the same way the GUI will: built-in defaults,
then the printer profile, then the resin's matching process, then explicit
overrides from the flags below. Failures print a structured JSON error on stderr
and return a nonzero status; nothing is written when validation fails unless
``--allow-unresolved`` is given.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import time

import numpy as np

from .contracts import CancellationToken, VoxelMillError, ResourceBudget
from .config import (BASE_TYPES, MODEL_ANCHOR_SHAPES, SMALL_PILLAR_MODES, SMALL_PILLAR_SHAPES,
                     SUPPORT_VOID_POLICIES, TIP_SHAPES, _merge, fill_legacy_settings,
                     layer_exposure, resin_usage, resolve_settings, validate_settings)

SECTIONS = ('printer', 'resin', 'process', 'support', 'peel', 'repair', 'assembly', 'resources', 'hollow')


def _scale(value):
    """One factor for a uniform scale, or three for a per-axis one."""
    if value is None:
        return (1.0, 1.0, 1.0)
    try:
        factors = [float(v) for v in value]
    except (TypeError, ValueError) as exc:
        raise VoxelMillError('invalid_scale', '--scale expects one or three finite factors') from exc
    if len(factors) == 1:
        return tuple(factors * 3)
    if len(factors) != 3:
        raise VoxelMillError('invalid_scale', '--scale takes one uniform factor or three, one per axis')
    return tuple(factors)


def _mirror(value):
    axes = [str(a).lower() for a in (value or ())]
    unknown = sorted(set(axes) - {'x', 'y', 'z'})
    if unknown:
        raise VoxelMillError('invalid_mirror', f'--mirror takes x, y or z, got {unknown}')
    return tuple(axis in axes for axis in 'xyz')


def _rotation(value):
    if value is None:
        return None
    if len(value) == 1 and str(value[0]).lower() == 'auto':
        return 'auto'
    if len(value) != 3:
        raise VoxelMillError('invalid_option', '--rotate takes three angles in degrees or the word auto')
    try:
        return tuple(float(v) for v in value)
    except ValueError as exc:
        raise VoxelMillError('invalid_option', '--rotate expects finite numeric angles or auto') from exc


def _workers(value):
    """Resolve `--workers`, where `auto` means one worker per physical core.

    Resolved here rather than as an argparse `type=` callable: those run inside
    `parse_args`, which `main` does not wrap, so a bad value would surface as a
    traceback instead of the JSON error envelope every other option produces.
    The settings table keeps a plain int; `auto` never reaches it.

    One worker per physical core rather than per logical cpu, because the layer
    analysis this sizes is memory-bandwidth-bound and an SMT sibling contends
    for the same cache instead of adding throughput.
    """
    if value is None:
        return None
    if str(value).strip().lower() == 'auto':
        return 0  # the sentinel ResourceBudget resolves against the real machine
    try:
        return int(value)
    except (TypeError, ValueError):
        raise VoxelMillError('invalid_option',
                             f'--workers expects an integer from 1 to 32 or auto, got {value!r}') from None


def _overrides(args):
    changes = {}
    if getattr(args, 'process_preset', None):
        from .presets import load_process_preset
        changes['process'] = load_process_preset(args.process_preset)['process']

    def put(section, key, value):
        if value is not None:
            changes.setdefault(section, {})[key] = value

    put('support', 'spacing_mm', args.support_spacing_mm)
    put('support', 'automatic', args.auto_supports)
    put('support', 'auto_bracing', args.auto_bracing)
    put('support', 'brace_spacing_mm', args.brace_spacing_mm)
    put('support', 'brace_start_height_mm', args.brace_start_height_mm)
    put('support', 'brace_diameter_mm', args.brace_diameter_mm)
    put('support', 'brace_max_distance_mm', args.brace_max_distance_mm)
    put('support', 'allow_part_to_part', args.part_to_part_supports)
    put('support', 'part_to_part_avoidance', args.part_to_part_avoidance)
    put('support', 'overhang_angle_deg', args.overhang_angle_deg)
    put('support', 'pillar_angle_deg', args.pillar_angle_deg)
    put('support', 'base_type', args.base_type)
    put('support', 'model_anchor_shape', args.model_anchor_shape)
    put('support', 'small_pillar_mode', args.small_pillar_mode)
    put('support', 'small_pillar_shape', args.small_pillar_shape)
    put('support', 'tip_shape', args.tip_shape)
    put('support', 'break_point_diameter_mm', args.break_point_diameter_mm)
    put('support', 'drop_attached_unroutable', args.drop_attached_unroutable)
    put('support', 'tree_supports', args.tree_supports)
    put('support', 'contour_supports', getattr(args, 'contour_supports', None))
    put('support', 'boundary_supports', getattr(args, 'boundary_supports', None))
    put('repair', 'support_void_policy', args.support_void_policy)
    put('peel', 'enabled', args.peel_analysis)
    put('repair', 'min_orifice_area_mm2', args.min_orifice_area_mm2)
    put('repair', 'max_deviation_mm', args.max_deviation_mm)
    put('repair', 'voxel_size_mm', args.repair_voxel_mm)
    put('repair', 'aggressiveness', args.repair)
    put('assembly', 'clip_to_build_volume', args.clip_to_build_volume)
    put('resources', 'worker_policy', getattr(args, 'worker_policy', None))
    put('process', 'layer_height_mm', args.layer_height_mm)
    put('process', 'elephant_foot_compensation_mm', args.elephant_foot_mm)
    put('process', 'elephant_foot_layers', args.elephant_foot_layers)
    put('resources', 'memory_gib', args.memory_gib)
    put('resources', 'workers', _workers(args.workers))
    put('resources', 'scratch_dir', args.scratch_dir)
    put('resources', 'acceleration', getattr(args, 'acceleration', None))
    put('resources', 'cuda_device', getattr(args, 'cuda_device', None))
    if args.seal_voids is not None:
        put('repair', 'seal_voids', args.seal_voids)
    for item in args.set or ():
        if '=' not in item:
            raise VoxelMillError('invalid_option', f'--set expects section.key=value, got {item!r}')
        name, raw = item.split('=', 1)
        if name.count('.') != 1 or name.split('.')[0] not in SECTIONS:
            raise VoxelMillError('invalid_option', f'--set name must be one of {SECTIONS} followed by a key: {name!r}')
        section, key = name.split('.')
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        changes.setdefault(section, {})[key] = value
    return changes or None


def _profile_paths(args):
    """Turn ``--printer``/``--resin`` into paths, accepting library identifiers."""
    from .profiles import resolve_profile_path
    return (resolve_profile_path(args.printer_profile, 'printer'),
            resolve_profile_path(args.resin_profile, 'resin'))


def _settings(args, stored=None):
    """Resolve profiles and CLI overrides, optionally starting from a project."""
    support_preset = getattr(args, 'support_preset', None)
    if (stored is None or args.printer_profile is not None or args.resin_profile is not None):
        printer, resin = _profile_paths(args)
        return resolve_settings(printer, resin, _overrides(args),
                                support_preset=support_preset)
    settings = fill_legacy_settings(deepcopy(stored))
    if support_preset is not None:
        from .config import _support_overlay_from_preset
        _merge(settings['support'],
               _support_overlay_from_preset(support_preset, {}),
               'support')
    changes = _overrides(args)
    if changes:
        _merge(settings, changes, 'settings')
    return validate_settings(settings)


def _budget(settings):
    return ResourceBudget(**settings['resources'])


def _emit(payload, path=None):
    text = json.dumps(payload, indent=2, allow_nan=False, sort_keys=False, default=str)
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text + '\n')
    else:
        print(text)


def _emit_text(text, path=None):
    """Plain output for the generated shell and man page files."""
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text)
    else:
        sys.stdout.write(text)


def cmd_completion(args):
    """Print a shell completion script generated from this exact parser."""
    from .shellhelp import completion
    _emit_text(completion(args.shell, build_parser()), args.output)
    return 0


def cmd_manpage(args):
    """Print a roff man page generated from this exact parser."""
    from . import __version__
    from .shellhelp import manpage
    _emit_text(manpage(build_parser(), version=f'VoxelMill {__version__}',
                       section=args.section), args.output)
    return 0


def _progress(enabled):
    if not enabled:
        from .contracts import no_progress
        return no_progress
    state = {'last': 0.0}

    def report(stage, done, total):
        now = time.monotonic()
        if now - state['last'] < 0.5 and done != total:
            return
        state['last'] = now
        share = f'{done}/{total}' if total else str(done)
        print(f'\r{stage}: {share}          ', end='', file=sys.stderr, flush=True)
    return report


def cmd_inspect(args):
    from .pipeline import inspect_stl
    settings = _settings(args)
    budget = _budget(settings)
    cancel = CancellationToken()
    results = [inspect_stl(path, settings, budget=budget, cancel=cancel,
                           progress=_progress(args.progress),
                           self_intersections=not args.no_self_intersections)
               for path in args.inputs]
    _emit({'schema_version': 1, 'command': 'inspect', 'meshes': results}, args.report)
    return 0


def cmd_prepare(args):
    from .pipeline import prepare
    from .resources import execution_limits
    input_path = Path(args.input)
    project_input = input_path.suffix.lower() == '.voxmil'
    if project_input:
        input_resolved = input_path.resolve()
        for destination in (args.output, args.project):
            if destination is not None and Path(destination).resolve() == input_resolved:
                raise VoxelMillError('source_overwrite', 'A .voxmil input cannot be replaced by an output')

    def run(source, settings, state=None):
        budget = _budget(settings)
        if args.rotate is None:
            rotate = state.get('rotation_deg') if state is not None else None
        else:
            rotate = _rotation(args.rotate)
        if args.center_offset is None:
            center_offset = tuple((state or {}).get('center_offset_mm') or (0.0, 0.0))
        else:
            center_offset = tuple(args.center_offset)
        if args.model_lift_mm is None:
            lift_mm = (state or {}).get('model_lift_mm', 5.0)
        else:
            lift_mm = args.model_lift_mm
        edits = (state or {}).get('edits') or {}
        manual_contacts = _contacts(args.contacts) if args.contacts is not None else edits.get('manual_contacts', [])
        removed_contacts = (_contacts(args.removed_contacts) if args.removed_contacts is not None
                            else edits.get('removed_contacts', []))
        contact_parameters = (_contact_parameters(args.contact_parameters, settings)
                              if args.contact_parameters is not None else edits.get('contact_parameters', []))
        # A project stores one local-frame record per object; --paint supplies a
        # single plate-coordinate table. prepare() accepts both and resolves the
        # per-object form once the placements exist.
        paint = (_paint_file(args.paint, settings) if args.paint is not None
                 else edits.get('paint'))
        # Every other edit falls back to the project; added parts used to be
        # the exception, so preparing a multi-part project from the command
        # line silently produced a single-part plate. load_project has already
        # rewritten each embedded mesh to its extracted path.
        extra_models = (_extra_models(args.add_model, args.add_model_spec)
                        if (args.add_model or args.add_model_spec)
                        else list(edits.get('extra_models') or ()))
        scale = _scale(args.scale) if args.scale is not None else tuple(
            (state or {}).get('scale_factors') or (1.0, 1.0, 1.0))
        mirror = _mirror(args.mirror) if args.mirror is not None else tuple(
            (state or {}).get('mirror_axes') or (False, False, False))
        with execution_limits(budget, hard_memory=not args.no_memory_limit):
            return prepare(source, settings, scale=scale, mirror=mirror,
                           rotate=rotate, center_offset=center_offset, lift_mm=lift_mm,
                           output=args.output, components=args.components, budget=budget,
                           cancel=CancellationToken(), progress=_progress(args.progress),
                           allow_unresolved=args.allow_unresolved, max_passes=args.max_passes,
                           drainage=not args.no_drainage, track_voids=not args.no_void_analysis,
                           overhang_check=not args.no_overhang_check,
                           project=args.project, manual_contacts=manual_contacts,
                           removed_contacts=removed_contacts,
                           candidates=args.candidates, candidate_rank=args.candidate_rank,
                           contact_parameters=contact_parameters,
                           paint=paint, extra_models=extra_models)

    if project_input:
        from .project import load_project
        with tempfile.TemporaryDirectory(prefix='voxelmill-project-input-') as extract_dir:
            state = load_project(input_path, extract_dir)
            source_info = state.get('source', {})
            if not isinstance(source_info, dict):
                raise VoxelMillError('invalid_project', '.voxmil input has invalid source metadata')
            source = source_info.get('extracted_path')
            if not source:
                raise VoxelMillError('invalid_project', '.voxmil input has no embedded source STL')
            stored_settings = state.get('settings')
            if isinstance(stored_settings, dict):
                stored_settings = fill_legacy_settings(deepcopy(stored_settings))
            report = run(source, _settings(args, stored_settings), state)
            report['project_input'] = {'path': str(input_path),
                                       'embedded_source_sha256': source_info.get('sha256'),
                                       'extraction': 'temporary; removed after preparation'}
    else:
        settings = _settings(args)
        report = run(input_path, settings)
    if report.get('stages', {}).get('assembly', {}).get('union') == 'raster':
        print('WARNING: exact union unavailable; raster union used. Read assembly and parity evidence.', file=sys.stderr)
    transform = report.get('stages', {}).get('transform', {})
    if transform.get('note'):
        print(f"WARNING: the model was {transform['note']}.", file=sys.stderr)
    if args.candidates is not None or args.candidate_rank is not None:
        _print_orientation_candidates(report.get('placement', {}).get('search', {}))
    if args.timing:
        from .stage_timing import print_timing_table
        print_timing_table(report.get('timing') or {}, report.get('seconds'))
    _emit(report, args.report)
    return 0 if report.get('validation', {}).get('passed') else 2


def _print_orientation_candidates(search):
    """Human ranking on stderr; stdout/report files remain exact JSON."""
    rows = search.get('ranked_candidates', [])
    print('Orientation candidates (lower scores first; weights uncalibrated):', file=sys.stderr)
    print('rank  score       RX          RY          RZ          assessment', file=sys.stderr)
    for row in rows:
        value = row['total'] if row['total'] is not None else row['cheap_composite']
        angles = ' '.join(f'{angle:11.6f}' for angle in row['rotation_deg'])
        mark = '*' if row['rank'] == search.get('selected_rank') else ' '
        print(f"{mark}{row['rank']:3d}  {value:10.6g} {angles}  {row['ranking_basis']}", file=sys.stderr)
        terms = ', '.join(f"{name}={term['contribution']:.6g}" if term['counted'] else f'{name}=not_counted'
                          for name, term in row['score_terms'].items())
        print('      weighted terms: ' + terms, file=sys.stderr)
    if not rows:
        print('No feasible candidates; the report records the fallback pose.', file=sys.stderr)


def cmd_validate(args):
    from .pipeline import validate_stl
    settings = _settings(args)
    report = validate_stl(args.input, settings, budget=_budget(settings),
                          cancel=CancellationToken(), progress=_progress(args.progress),
                          drainage=not args.no_drainage,
                          track_voids=not args.no_void_analysis)
    payload = {'schema_version': 1, 'command': 'validate', 'input': str(args.input),
               'report': report.to_dict()}
    timing = (report.metrics or {}).get('timing')
    if timing is not None:
        payload['timing'] = timing
    if args.timing:
        from .stage_timing import print_timing_table
        print_timing_table(timing or {})
    _emit(payload, args.report)
    return 0 if report.passed else 2


def _paint_file(path, settings):
    from .paint import normalize_paint
    try:
        if Path(path).stat().st_size > 1024 * 1024:
            raise ValueError('paint file exceeds 1 MiB')
        return normalize_paint(json.loads(Path(path).read_text()))
    except (OSError, ValueError, TypeError) as exc:
        raise VoxelMillError('invalid_paint', str(exc)) from exc


def _extra_models(values, spec_files=()):
    """Resolve simple added paths plus portable JSON pose records."""
    extras = []
    for path in values or ():
        extras.append({'path': str(path), 'rotate': (0.0, 0.0, 0.0),
                       'center_offset': (0.0, 0.0), 'lift_mm': 5.0,
                       'scale': (1.0, 1.0, 1.0), 'mirror': (False, False, False)})
    for filename in spec_files or ():
        try:
            path = Path(filename)
            if path.stat().st_size > 1024 * 1024:
                raise ValueError('added-model spec exceeds 1 MiB')
            records = json.loads(path.read_text())
            if not isinstance(records, list):
                records = [records]
            from .pipeline import normalize_extra_model
            extras.extend(normalize_extra_model(record) for record in records)
        except (OSError, ValueError, TypeError) as exc:
            raise VoxelMillError('invalid_model', f'Cannot read added-model spec: {exc}') from exc
    return extras


def _contact_parameters(path, settings):
    from .contact_parameters import normalize_contact_parameters
    try:
        if Path(path).stat().st_size > 1024 * 1024:
            raise ValueError('contact parameter file exceeds 1 MiB')
        return normalize_contact_parameters(json.loads(Path(path).read_text()), settings)
    except (OSError, ValueError, TypeError) as exc:
        raise VoxelMillError('invalid_contact_parameters', str(exc)) from exc


def _contacts(path):
    if path is None:
        return []
    try:
        if Path(path).stat().st_size > 1024 * 1024:
            raise ValueError('contact file exceeds 1 MiB')
        points = json.loads(Path(path).read_text())
        import numpy as np
        array = np.asarray(points, dtype=float)
        if isinstance(points, list) and not points:
            return []
        if array.ndim != 2 or array.shape[1] != 3 or not np.isfinite(array).all():
            raise ValueError('expected an array of finite [x,y,z] positions')
        return array.tolist()
    except (OSError, ValueError, TypeError) as exc:
        raise VoxelMillError('invalid_contacts', str(exc)) from exc


def cmd_slice(args):
    from .goo import slice_stl
    from .hooks import run_post_slice_hook
    from .resources import execution_limits
    settings = _settings(args)
    with execution_limits(_budget(settings), hard_memory=not args.no_memory_limit):
        report = slice_stl(args.input, args.output, settings,
                           allow_unresolved=args.allow_unresolved,
                           cancel=CancellationToken(), progress=_progress(args.progress),
                           print_time_s=args.print_time_s, report_path=args.report)
    # slice_stl already runs the hook after a successful publish.  When that
    # path did not (older callers / non-written results), still record one here
    # so the CLI exit code and report stay honest about a configured hook.
    if report.get('written') and (report.get('hooks') or {}).get('post_slice') is None:
        hook = run_post_slice_hook(settings, args.output, args.report)
        if hook is not None:
            report.setdefault('hooks', {})['post_slice'] = hook
    if not settings['printer']['image_mirror_verified']:
        print('WARNING: the LCD image orientation for this printer is unverified and the two '
              'reference files disagree. A mirrored threaded or keyed part is scrap. See the '
              'goo_orientation_unverified diagnostic.', file=sys.stderr)
    if args.timing:
        from .stage_timing import print_timing_table
        print_timing_table(report.get('timing') or {}, report.get('seconds'))
    _emit(report, args.report)
    hook = (report.get('hooks') or {}).get('post_slice')
    if hook is not None and not hook.get('ok', True):
        # The GOO stays; the nonzero status is the hook failure signal.
        return 2
    return 0 if report.get('written') and not report.get('warned') else 2


def cmd_report_html(args):
    """Render a saved JSON report as a static HTML page."""
    from .report_html import render_report
    report = json.loads(Path(args.report_json).read_text())
    html = render_report(report)
    _emit_text(html, args.output)
    return 0


def cmd_goo_info(args):
    from .goo import GooReader
    with GooReader(args.input) as reader:
        payload = {'header': reader.header, 'layers': len(reader.layers)}
        if args.verify:
            for index in range(len(reader.layers)):
                reader.decode(index)
            payload['decoded_layers'] = len(reader.layers)
    _emit(payload, args.report)
    return 0


def cmd_ctb_info(args):
    from .ctb import CtbReader
    with CtbReader(args.input) as reader:
        payload = {'format': 'ctb', 'supported_version': 3,
                   'header': reader.header, 'layers': len(reader.layers)}
        if args.verify:
            for index in range(len(reader.layers)):
                reader.decode(index)
            payload['decoded_layers'] = len(reader.layers)
    _emit(payload, args.report)
    return 0


def cmd_convert(args):
    from .ctb import convert_slices
    settings = _settings(args)
    payload = convert_slices(args.input, args.output, settings,
                             cancel=CancellationToken(), progress=_progress(args.progress))
    _emit(payload, args.report)
    verification = payload.get('verification')
    return 0 if verification is None or verification['report']['passed'] else 2

def cmd_verify(args):
    """Deep-check a finished GOO or classic CTB v3 with no source mesh."""
    settings = _settings(args)
    if Path(args.input).suffix.lower() == '.ctb':
        from .ctb import verify_ctb
        payload = verify_ctb(args.input, settings, budget=_budget(settings),
                             cancel=CancellationToken(), progress=_progress(args.progress),
                             track_voids=not args.no_void_analysis)
    else:
        from .goo import verify_goo
        payload = verify_goo(args.input, settings, budget=_budget(settings),
                             cancel=CancellationToken(), progress=_progress(args.progress),
                             track_voids=not args.no_void_analysis,
                             full_panel=args.full_panel)
    _emit(payload, args.report)
    if payload['settings_mismatches']:
        print('WARNING: the GOO describes a different machine or process than the selected '
              'profile; the file was used. See settings_mismatches.', file=sys.stderr)
    return 0 if payload['report']['passed'] else 2


def _exposure_schedule(settings):
    process = settings['process']
    return [layer_exposure(settings, i)
            for i in range(process['bottom_layers'] + process['transition_layers'] + 2)]


#: Per-item outputs a batch names for each operation. ``None`` means the
#: operation writes nothing but its report.
BATCH_OUTPUTS = {
    'prepare': '{stem}-supported.stl',
    'slice': '{stem}.goo',
    'validate': None, 'islands': None, 'measure': None, 'inspect': None, 'verify': None,
}


def cmd_batch(args):
    """Run one operation over many inputs, keeping every item's own report.

    Each item is parsed by the real subcommand parser, so an option cannot
    exist for a single run and not for a batched one. Items run in this
    process, one after another: a structured failure is recorded and the batch
    continues when asked, but a process-level failure ends the batch, and the
    manifest written so far is the evidence of how far it got.
    """
    parser = build_parser()
    directory = Path(args.output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    tail = list(args.extra or ())
    for reserved in ('--report', '--output'):
        if reserved in tail:
            raise VoxelMillError('invalid_option',
                            f'batch names every item\'s {reserved}; remove it from the '
                            'pass-through arguments')
    shared = _shared_batch_arguments(args)
    items, failures = [], 0
    for source in args.inputs:
        stem = Path(source).stem
        report_path = directory / f'{stem}.{args.operation}.json'
        argv = [args.operation, str(source), *shared, *tail, '--report', str(report_path)]
        template = BATCH_OUTPUTS[args.operation]
        if template is not None:
            argv += ['--output', str(directory / template.format(stem=stem))]
        started = time.monotonic()
        record = {'input': str(source), 'report': str(report_path), 'argv': argv}
        try:
            item = parser.parse_args(argv)
            record['exit_code'] = int(item.func(item))
        except VoxelMillError as error:
            record['exit_code'] = 3
            record['error'] = error.to_dict()
        except SystemExit as exit_error:
            record['exit_code'] = int(exit_error.code or 0)
            record['error'] = {'code': 'invalid_option',
                               'message': 'the operation rejected these arguments',
                               'details': {}}
        record['seconds'] = time.monotonic() - started
        if record['exit_code'] != 0:
            failures += 1
        items.append(record)
        print(f"{args.operation} {source}: exit {record['exit_code']} "
              f"in {record['seconds']:.1f}s", file=sys.stderr)
        if record['exit_code'] != 0 and not args.continue_on_error:
            break
    manifest = {'schema_version': 1, 'command': 'batch', 'operation': args.operation,
                'output_dir': str(directory), 'requested': len(args.inputs),
                'ran': len(items), 'failed': failures,
                'stopped_early': len(items) < len(args.inputs),
                'continue_on_error': bool(args.continue_on_error),
                'isolation': 'items share one process; a process-level failure ends the batch',
                'items': items}
    manifest['manifest_path'] = str(args.report or directory / 'batch-manifest.json')
    _emit(manifest, manifest['manifest_path'])
    _emit(manifest)
    return 0 if failures == 0 and not manifest['stopped_early'] else 2


def _shared_batch_arguments(args):
    """Re-emit the batch's own settings for each item, derived not enumerated.

    A hand-listed set of flags silently drops the ones nobody remembered to
    add -- the boolean toggles and half the numeric ones were dropped exactly
    that way. ``_overrides`` already folds **every** settings flag into one
    nested dictionary, so re-emitting that dictionary as ``--set`` pairs cannot
    miss a flag, and a new flag joins the batch the moment it joins the CLI.
    Profiles and progress are not settings and are forwarded as themselves.
    """
    shared = []
    for flag, value in (('--printer', args.printer_profile), ('--resin', args.resin_profile)):
        if value is not None:
            shared += [flag, str(value)]
    if args.progress:
        shared += ['--progress']
    for section, values in (_overrides(args) or {}).items():
        for key, value in values.items():
            shared += ['--set', f'{section}.{key}={json.dumps(value)}']
    return shared


def cmd_measure(args):
    """Sizes before and after a proposed pose, with nothing written.

    This is the question a scale flag actually raises — what will the part
    measure — answered without running a preparation. It is also how a scale
    factor is chosen: measure, compute the ratio, then prepare.
    """
    from .pipeline import measure_stl
    settings = _settings(args)
    payload = measure_stl(args.input, settings, rotate=_rotation(args.rotate),
                          center_offset=tuple(args.center_offset or (0.0, 0.0)),
                          lift_mm=args.model_lift_mm if args.model_lift_mm is not None else 5.0,
                          scale=_scale(args.scale), mirror=_mirror(args.mirror),
                          target_mm=args.target_mm, budget=_budget(settings),
                          cancel=CancellationToken())
    _emit({'schema_version': 1, 'command': 'measure', **payload}, args.report)
    if payload['transform_note']:
        print(f"WARNING: this pose would leave the model {payload['transform_note']}.",
              file=sys.stderr)
    return 0


def cmd_islands(args):
    """Fast island-only connectivity scan; the editor's badge runs the same code."""
    from .pipeline import scan_islands
    settings = _settings(args)
    payload = scan_islands(args.input, settings, budget=_budget(settings),
                           cancel=CancellationToken(), progress=_progress(args.progress),
                           limit=args.max_examples)
    payload = {'schema_version': 1, 'command': 'islands', **payload}
    _emit(payload, args.report)
    if payload['island_count']:
        print(f"WARNING: {payload['island_count']} island(s); this scan does not examine "
              f"{', '.join(payload['not_examined'])}. Run voxelmill validate for those.",
              file=sys.stderr)
    return 0 if payload['check'] == 'pass' and payload['closed_surface'] == 'pass' else 2


def cmd_profile(args):
    from . import profiles as library
    action = getattr(args, 'action', 'show') or 'show'
    if action == 'list':
        payload = library.library_report(args.kind)
        payload['command'] = 'profile list'
    elif action == 'diff':
        if not args.against:
            raise VoxelMillError('invalid_option',
                            'profile diff needs --against PROFILE (or --against defaults)')
        left = _settings(args)
        if args.against == 'defaults':
            right = resolve_settings(None, None, None)
        else:
            right = library.profile_settings(args.against, args.kind or 'printer')
        payload = {'schema_version': 1, 'command': 'profile diff',
                   'left': _profile_sources(args), 'right': args.against,
                   'differences': library.diff_settings(left, right)}
    elif action == 'save':
        if not args.output:
            raise VoxelMillError('invalid_option', 'profile save requires --output')
        payload = library.save_printer_profile(args.output, _settings(args), name=args.name,
                                               hardware_only=args.hardware_only)
    else:
        settings = _settings(args)
        payload = {'schema_version': 1, 'command': 'profile', 'settings': settings,
                   'exposure_schedule_s': _exposure_schedule(settings),
                   'resin_usage_per_ml': resin_usage(settings, 1000.0)}
        if args.provenance:
            printer, resin = _profile_paths(args)
            payload['provenance'] = library.provenance(printer, resin, _overrides(args))
    _emit(payload, args.report)
    return 0


def _profile_sources(args):
    printer, resin = _profile_paths(args)
    return {'printer': str(printer) if printer else None,
            'resin': str(resin) if resin else None}


def cmd_resin(args):
    """Inspect, save, or bind resin profiles."""
    from . import profiles as library
    if args.action == 'list':
        payload = library.library_report('resin')
        payload['command'] = 'resin list'
    elif args.action == 'show':
        payload = library.resin_document(args.resin or args.resin_profile)
        payload['command'] = 'resin show'
    elif args.action == 'save':
        if not args.output:
            raise VoxelMillError('invalid_option', 'resin save requires --output')
        payload = library.save_resin_profile(args.output, _settings(args), name=args.name)
    else:
        if not args.output:
            raise VoxelMillError('invalid_option', 'resin bind requires --output')
        payload = library.bind_resin_process(
            args.resin or args.resin_profile, args.output,
            source_printer=getattr(args, 'from_printer', None), target_printer=args.to)
    _emit(payload, args.report)
    return 0


def cmd_gui(args):
    from .gui import run
    return run(_settings(args), args)


def cmd_monitor(args):
    from .gui.printer_monitor import run_monitor
    return run_monitor(host=args.host or "", mainboard_id=args.mainboard_id or "", demo=args.demo)


def cmd_support_example(args):
    from .support_example import support_example, save_example
    if args.output and args.report and Path(args.output).resolve() == Path(args.report).resolve():
        raise VoxelMillError('invalid_option', 'Example STL and report must use different paths')
    example = support_example(_settings(args), args.height_mm)
    if args.output:
        save_example(args.output, example)
    payload = {key: value for key, value in example.items() if key not in ('triangles', 'contacts')}
    payload.update(schema_version=1, command='support-example', output=args.output,
                   triangle_counts={key: len(value) for key, value in example['triangles'].items()})
    _emit(payload, args.report)
    return 0


def cmd_import_step(args):
    """Tessellate a STEP/.stp file to STL through headless FreeCAD."""
    from .importers import tessellate_step
    if Path(args.input).resolve() == Path(args.output).resolve():
        raise VoxelMillError('step_import', 'STEP input and STL output must be different paths')
    settings = _settings(args)
    report = tessellate_step(args.input, settings, args.output, cancel=CancellationToken())
    _emit({'schema_version': 1, 'command': 'import-step', **report}, args.report)
    return 0


def cmd_preset(args):
    from .presets import (list_presets, list_process_presets, load_preset,
                          load_process_preset, save_preset, save_process_preset)
    kind = getattr(args, 'kind', 'support') or 'support'
    if args.action == 'list':
        names = list_process_presets() if kind == 'process' else list_presets()
        payload = {'schema_version': 1, 'presets': list(names)}
        if kind == 'process':
            payload['kind'] = 'process'
    elif args.action == 'show':
        source = args.preset
        if source is None:
            source = 'default' if kind == 'process' else 'medium'
        payload = (load_process_preset(source) if kind == 'process'
                   else load_preset(source))
    else:
        if not args.output:
            raise VoxelMillError('invalid_option', 'preset save requires --output')
        name = args.name or Path(args.output).stem
        if kind == 'process':
            payload = save_process_preset(args.output, name, _settings(args)['process'])
        else:
            payload = save_preset(args.output, name, _settings(args)['support'])
    _emit(payload, args.report)
    return 0


def cmd_boolean(args):
    """Union, intersect or subtract two STLs after the shared prepare repair path."""
    from .mesh import open_stl, write_stl
    from .ops import boolean_mesh
    settings = _settings(args)
    budget = _budget(settings)
    cancel = CancellationToken()
    progress = _progress(args.progress)
    with open_stl(args.a, budget, cancel, progress) as left:
        a_triangles = left.triangles
    with open_stl(args.b, budget, cancel, progress) as right:
        b_triangles = right.triangles
    triangles, report = boolean_mesh(a_triangles, b_triangles, args.op, settings,
                                     budget=budget, cancel=cancel, progress=progress)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    write_stl(args.output, triangles, cancel, progress)
    _emit({'schema_version': 1, 'command': 'boolean', 'a': str(args.a), 'b': str(args.b),
           'output': str(args.output), **report}, args.report)
    return 0


def cmd_trim(args):
    """Keep one side of a plane and write a closed, capped solid."""
    from .mesh import open_stl, write_stl
    from .ops import trim_mesh
    settings = _settings(args)
    budget = _budget(settings)
    cancel = CancellationToken()
    progress = _progress(args.progress)
    with open_stl(args.input, budget, cancel, progress) as mesh:
        triangles = mesh.triangles
    result, report = trim_mesh(triangles, args.point, args.normal, settings,
                               keep=args.keep, budget=budget, cancel=cancel,
                               progress=progress)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    write_stl(args.output, result, cancel, progress)
    _emit({'schema_version': 1, 'command': 'trim', 'input': str(args.input),
           'output': str(args.output), **report}, args.report)
    return 0


def cmd_cap(args):
    """Fill near-planar open cut loops; refuse non-planar holes."""
    from .mesh import open_stl, write_stl
    from .ops import cap_open_cuts
    settings = _settings(args)
    budget = _budget(settings)
    cancel = CancellationToken()
    progress = _progress(args.progress)
    with open_stl(args.input, budget, cancel, progress) as mesh:
        triangles = mesh.triangles.copy()
    result, report = cap_open_cuts(triangles, settings, budget=budget, cancel=cancel,
                                   progress=progress)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    write_stl(args.output, result, cancel, progress)
    _emit({'schema_version': 1, 'command': 'cap', 'input': str(args.input),
           'output': str(args.output), **report}, args.report)
    return 0


def cmd_hollow(args):
    """Voxel-hollow a solid, optionally with drain/vent pairs and lattice infill."""
    from .mesh import open_stl, write_stl
    from .hollow import hollow_stl_triangles
    settings = _settings(args)
    settings['hollow']['enabled'] = True
    budget = _budget(settings)
    cancel = CancellationToken()
    progress = _progress(args.progress)
    with open_stl(args.input, budget, cancel, progress) as mesh:
        triangles = np.asarray(mesh.triangles, dtype=np.float32).copy()
    result, report = hollow_stl_triangles(
        triangles, settings, budget=budget, cancel=cancel, progress=progress,
        add_holes=not args.no_holes)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    write_stl(args.output, result, cancel, progress)
    _emit({'schema_version': 1, 'command': 'hollow', 'input': str(args.input),
           'output': str(args.output), **report}, args.report)
    return 0


def cmd_thickness(args):
    """Wall-thickness analysis from an occupancy distance transform."""
    from .mesh import open_stl
    from .hollow import analyze_wall_thickness
    settings = _settings(args)
    budget = _budget(settings)
    cancel = CancellationToken()
    progress = _progress(args.progress)
    with open_stl(args.input, budget, cancel, progress) as mesh:
        triangles = np.asarray(mesh.triangles, dtype=np.float32).copy()
    report = analyze_wall_thickness(
        triangles, settings, threshold_mm=args.threshold_mm, budget=budget, cancel=cancel,
        progress=progress)
    payload = {'schema_version': 1, 'command': 'thickness', 'input': str(args.input),
               'checks': {'wall_thickness': report['status']}, **report}
    _emit(payload, args.report)
    return 0 if report['status'] in ('pass', 'warn', 'not_run') else 1


def cmd_calibrate(args):
    """Write an offline exposure or tolerance calibration GOO plus JSON report."""
    from .calibrate import parse_range, write_calibration
    settings = _settings(args)
    payload = write_calibration(
        args.calibrate_command, settings, args.output,
        range_mm=parse_range(args.range),
        steps=args.steps,
        layers=args.layers,
        progress=_progress(args.progress),
    )
    _emit(payload, args.report)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog='voxelmill', description=__doc__.splitlines()[0])
    parser.add_argument('--version', action='version', version=_version())
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--printer', dest='printer_profile', help='printer .ptr profile')
    common.add_argument('--resin', dest='resin_profile', help='resin .res profile')
    common.add_argument('--support-preset', help='light, medium, heavy, or portable support preset JSON path')
    common.add_argument('--process-preset',
                        help='fine, default, fast, or portable process preset JSON path')
    common.add_argument('--set', action='append', metavar='SECTION.KEY=VALUE',
                        help='override any resolved setting; VALUE is parsed as JSON when possible')
    common.add_argument('--support-spacing-mm', type=float,
                        help='nominal distance between automatic support contacts; also the '
                             'default derivation for max_contact_gap_mm')
    common.add_argument('--auto-supports', action=argparse.BooleanOptionalAction, default=None,
                        help='automatic contacts; disable for manual contacts only')
    common.add_argument('--auto-bracing', action=argparse.BooleanOptionalAction, default=None,
                        help='cross-braces between slender pillars; independent of --auto-supports')
    common.add_argument('--brace-spacing-mm', type=float,
                        help='vertical gap between cross-braces; 0 derives '
                             'max_slenderness * 2 * pillar_radius')
    common.add_argument('--brace-start-height-mm', type=float,
                        help='height of the lowest cross-brace above the plate; 0 derives '
                             'max_slenderness * 2 * pillar_radius')
    common.add_argument('--brace-diameter-mm', type=float,
                        help='cross-brace diameter; 0 derives it from the thinner of the '
                             'two connected pillars')
    common.add_argument('--brace-max-distance-mm', type=float,
                        help='farthest a pillar neighbour may be and still be braced; '
                             '0 derives 1.5 * spacing_mm')
    common.add_argument('--part-to-part-supports', action=argparse.BooleanOptionalAction, default=None,
                        help='allow supports to anchor on the model; disable to require plate routes')
    common.add_argument('--part-to-part-avoidance', type=float,
                        help='0 scores routes equally by length; 1 always prefers an available plate '
                             'route; intermediate values require proportionally shorter model routes')
    common.add_argument('--overhang-angle-deg', type=float,
                        help='face angle from horizontal below which a downward face is treated '
                             'as an overhang needing support')
    common.add_argument('--pillar-angle-deg', type=float,
                        help='angle from horizontal an angled support branch runs at; steeper '
                             'buys stiffness and costs sideways reach')
    common.add_argument('--base-type', choices=BASE_TYPES,
                        help='support base: solid plate, bare feet (none), round pads, '
                             'elongated skate feet, skeleton tree, grid, hex, or triangle connections')
    common.add_argument('--model-anchor-shape', choices=MODEL_ANCHOR_SHAPES,
                        help='bottom connector shape; set support.model_anchor_length_mm to enable')
    common.add_argument('--small-pillar-mode', choices=SMALL_PILLAR_MODES,
                        help='thin middle segment or whole model-to-model connector; '
                             'set diameter and maximum length with --set')
    common.add_argument('--small-pillar-shape', choices=SMALL_PILLAR_SHAPES,
                        help='buried end shape for whole model-to-model small pillars')
    common.add_argument('--tip-shape', choices=TIP_SHAPES,
                        help='top contact shape: cone tapers from tip-base to contact diameter; '
                             'cylinder keeps the contact diameter')
    common.add_argument('--break-point-diameter-mm', type=float,
                        help='optional ball at the top contact for controlled removal; '
                             '0 disables it and it must fit in the tip length plus penetration')
    common.add_argument('--drop-attached-unroutable', action=argparse.BooleanOptionalAction,
                        default=None,
                        help='drop unroutable contacts that already have material one layer below; '
                             'island and manual contacts are never dropped')
    common.add_argument('--tree-supports', action=argparse.BooleanOptionalAction, default=None,
                        help='cluster nearby vertical plate supports onto one trunk with branches')
    common.add_argument('--contour-supports', action=argparse.BooleanOptionalAction, default=None,
                        help='also sample the outer perimeter of downward-face clusters')
    common.add_argument('--boundary-supports', action=argparse.BooleanOptionalAction, default=None,
                        help='also sample open mesh boundary edges (crop cuts); closed solids add none')
    common.add_argument('--support-void-policy', choices=SUPPORT_VOID_POLICIES,
                        help='fail (default) keeps support-generated voids as export failures; '
                             'ignore drops support-class voids only; fill seals enclosed support '
                             'shells on the exact path')
    common.add_argument('--peel-analysis', dest='peel_analysis', action=argparse.BooleanOptionalAction,
                        default=None, help='enable advisory peel analysis (uncalibrated thresholds)')
    common.add_argument('--min-orifice-area-mm2', type=float,
                        help='smallest drain opening a trapped void may have and still pass the '
                             'drainage check; a curved bore measures under its nominal area')
    common.add_argument('--max-deviation-mm', type=float,
                        help='allowed surface deviation for aggressive voxel repair; the voxel '
                             'pitch is derived from it unless --repair-voxel-mm is given')
    common.add_argument('--repair-voxel-mm', type=float,
                        help='explicit voxel pitch for aggressive repair (0 derives it from max deviation)')
    common.add_argument('--repair', choices=('none', 'conservative', 'aggressive'),
                        help='none uses the mesh exactly as authored and goes straight to the '
                             'raster union; conservative tries the exact solid first; aggressive '
                             'adds voxel repair')
    common.add_argument('--clip-to-build-volume', action=argparse.BooleanOptionalAction, default=None,
                        help='place and export a part that does not fit, discarding geometry the '
                             'printer cannot reach and recording exactly how much; off by default, '
                             'where an oversized part is refused instead. Nothing is ever scaled')
    common.add_argument('--layer-height-mm', type=float,
                        help='slice thickness; must lie inside the printer profile range')
    common.add_argument('--elephant-foot-mm', type=float,
                        help='shrink the exported bottom layers by this radius in mm, '
                             'ramping linearly to zero; 0 disables it')
    common.add_argument('--elephant-foot-layers', type=int,
                        help='how many bottom layers the compensation ramps over '
                             '(0 derives it from process.bottom_layers)')
    common.add_argument('--memory-gib', type=float,
                        help='address-space ceiling for this run; exceeding it raises '
                             'memory_budget rather than inviting the OOM killer')
    common.add_argument('--workers',
                        help='CPU workers, 1 to 32, or auto for one per physical core')
    common.add_argument('--worker-policy',
                        choices=('performance', 'efficiency', 'all'),
                        help='which cores to pin workers to; performance keeps foreground '
                             'runs off the efficiency cores, efficiency leaves the fast '
                             'cores free for an interactive session, all declines to pin')
    common.add_argument('--scratch-dir',
                        help='directory for placed-triangle memmaps and staged exports; '
                             'defaults to the system temporary directory')
    common.add_argument('--acceleration', choices=('auto', 'cpu', 'cuda'),
                        help='raster acceleration backend; auto uses CUDA when available')
    common.add_argument('--cuda-device', type=int,
                        help='zero-based CUDA device selected when acceleration uses CUDA')
    common.add_argument('--seal-voids', dest='seal_voids', action='store_true', default=None,
                        help='fill enclosed cavities; needs the exact path, and reports not_run '
                             'on the raster union path rather than pretending to have run')
    common.add_argument('--no-seal-voids', dest='seal_voids', action='store_false',
                        help='leave enclosed cavities alone; the layer analysis still reports them')
    common.add_argument('--report', help='write the JSON report here instead of stdout')
    common.add_argument('--progress', action='store_true', help='print progress to stderr')
    common.add_argument('--timing', action='store_true',
                        help='print a per-stage wall-time table to stderr')
    sub = parser.add_subparsers(dest='command', required=True)

    import_step = sub.add_parser('import-step', parents=[common],
                                 help='tessellate a STEP file to STL via FreeCAD')
    import_step.add_argument('input', help='STEP (.step/.stp) source')
    import_step.add_argument('--output', required=True, help='STL destination')
    import_step.set_defaults(func=cmd_import_step)

    inspect = sub.add_parser('inspect', parents=[common], help='full-resolution mesh inventory')
    inspect.add_argument('inputs', nargs='+')
    inspect.add_argument('--no-self-intersections', action='store_true',
                         help='skip the self-intersection scan, which dominates the run on '
                              'a large mesh')
    inspect.set_defaults(func=cmd_inspect)

    prep = sub.add_parser('prepare', parents=[common], help='place, support, export and re-verify')
    prep.add_argument('input')
    prep.add_argument('--output', required=False,
                      help='supported STL destination; omit to produce evidence only')
    prep.add_argument('--rotate', nargs='+', metavar='DEG',
                      help='RX RY RZ extrinsic degrees, or the word auto; omit to preserve orientation')
    prep.add_argument('--candidates', type=int,
                      help='request 1 to 32 distinct feasible orientation finalists with --rotate auto '
                           '(default 5); prints their ranked scores')
    prep.add_argument('--candidate-rank', type=int,
                      help='choose a 1-based orientation rank with --rotate auto; '
                           'the selected exact pose is preserved in saved projects')
    prep.add_argument('--scale', nargs='+', metavar='FACTOR',
                      help='one uniform factor or three per-axis factors. Nothing is ever scaled '
                           'automatically; a scaled part is recorded as a warning diagnostic')
    prep.add_argument('--mirror', nargs='+', choices=('x', 'y', 'z'), metavar='AXIS',
                      help='mirror the model on these axes. Winding is reversed so the part stays '
                           'solid; a mirrored threaded or keyed part will not assemble')
    prep.add_argument('--center-offset', nargs=2, type=float, default=None, metavar=('X', 'Y'),
                      help='plate-center offset in mm')
    prep.add_argument('--model-lift-mm', type=float, default=None,
                      help='height of the lowest model point above the plate; supports fill the gap')
    prep.add_argument('--contacts', help='JSON array of manual [x,y,z] contacts in final plate coordinates')
    prep.add_argument('--contact-parameters', help='JSON file of per-contact position_mm and parameters records')
    prep.add_argument('--paint', help='JSON object with blocked and enforced arrays of plate-coordinate centroids')
    prep.add_argument('--add-model', action='append', metavar='PATH',
                      help='additional STL on the same plate; refuse only if the model solids intersect, '
                           'not if support envelopes overlap. Repeatable.')
    prep.add_argument('--add-model-spec', action='append', metavar='JSON',
                      help='JSON object or array for added models, with path, rotate [RX,RY,RZ], '
                           'center_offset [X,Y], lift_mm, scale [X,Y,Z], and mirror [X,Y,Z]. Repeatable.')
    prep.add_argument('--removed-contacts', help='JSON array of suppressed automatic contact positions, as in the editor')
    prep.add_argument('--components', action='store_true', help='also write model/support/raft STLs')
    prep.add_argument('--project', help='write a .voxmil project archive')
    prep.add_argument('--allow-unresolved', action='store_true',
                      help='keep the export even when validation fails; diagnostics are preserved')
    prep.add_argument('--max-passes', type=int, default=None,
                      help='correction passes, 1 to 10; defaults to support.max_island_passes. Each '
                           'pass adds contacts where the search found unsupported material and '
                           'stops on success or no progress')
    prep.add_argument('--no-drainage', action='store_true',
                      help='skip the trapped-resin analysis; the check then reports not_run')
    prep.add_argument('--no-void-analysis', action='store_true',
                      help='skip enclosed-void and transient-trap tracking; both report not_run')
    prep.add_argument('--no-overhang-check', action='store_true',
                      help='skip the unsupported-overhang coverage check; it then reports not_run. '
                           'This is advisory (a warning), never an export blocker')
    prep.add_argument('--no-memory-limit', action='store_true',
                      help='do not set an address-space ceiling for this run')
    prep.set_defaults(func=cmd_prepare)

    validate = sub.add_parser('validate', parents=[common], help='reslice and check an existing STL')
    validate.add_argument('input')
    validate.add_argument('--no-drainage', action='store_true',
                          help='skip the trapped-resin analysis; the check reports not_run')
    validate.add_argument('--no-void-analysis', action='store_true',
                          help='skip enclosed-void and transient-trap tracking; both report not_run')
    validate.set_defaults(func=cmd_validate)

    sliced = sub.add_parser('slice', parents=[common],
                            help='validate prepared STL and export verified GOO or unencrypted CTB v3')
    sliced.add_argument('input')
    sliced.add_argument('--output', required=True,
                        help='GOO (.goo) or unencrypted CTB v3 (.ctb) destination')
    sliced.add_argument('--allow-unresolved', action='store_true',
                        help='keep the export even when validation fails; every diagnostic is '
                             'preserved and the file is marked warned')
    sliced.add_argument('--print-time-s', type=int, default=0,
                        help='override PrintTime in seconds; 0 uses the uncalibrated '
                             'schedule estimate (exposure + waits + motion travel)')
    sliced.add_argument('--no-memory-limit', action='store_true',
                        help='do not set an address-space ceiling for this run')
    sliced.set_defaults(func=cmd_slice)

    info = sub.add_parser('goo-info', help='read GOO metadata, optionally decode every layer')
    info.add_argument('input')
    info.add_argument('--verify', action='store_true',
                      help='decode every layer to confirm framing and checksums; for topology '
                           'use the verify command instead')
    info.add_argument('--report', help='write the JSON report here instead of stdout')
    info.set_defaults(func=cmd_goo_info)

    ctb_info = sub.add_parser('ctb-info', help='read classic CTB v3 metadata, optionally decode every layer')
    ctb_info.add_argument('input')
    ctb_info.add_argument('--verify', action='store_true', help='decode every layer and validate RLE framing')
    ctb_info.add_argument('--report', help='write the JSON report here instead of stdout')
    ctb_info.set_defaults(func=cmd_ctb_info)

    convert = sub.add_parser('convert', parents=[common],
                             help='convert GOO v3 and unencrypted CTB v3 without resampling')
    convert.add_argument('input')
    convert.add_argument('output')
    convert.set_defaults(func=cmd_convert)

    verify = sub.add_parser('verify', parents=[common],
                            help='deep-check an existing GOO or unencrypted CTB v3')
    verify.add_argument('input')
    verify.add_argument('--no-void-analysis', action='store_true',
                        help='skip enclosed-void and transient-trap tracking; both then report not_run')
    verify.add_argument('--full-panel', action='store_true',
                        help='analyze the whole LCD instead of cropping to the exposed pixels; '
                             'the result is identical and it is much slower')
    verify.set_defaults(func=cmd_verify)

    batch = sub.add_parser('batch', parents=[common],
                           help='run one operation over many inputs, keeping each item report')
    batch.add_argument('operation', choices=tuple(BATCH_OUTPUTS))
    batch.add_argument('inputs', nargs='+')
    batch.add_argument('--output-dir', required=True,
                       help='per-item reports and outputs are written here under the input stem')
    batch.add_argument('--continue-on-error', action='store_true',
                       help='keep going after a failed item; without it the batch stops there')
    batch.add_argument('--extra', nargs=argparse.REMAINDER, metavar='ARG',
                       help='everything after this is passed to each item and parsed by that '
                            "operation's own parser; --report and --output are named by the batch")
    batch.set_defaults(func=cmd_batch)

    measure = sub.add_parser('measure', parents=[common],
                             help='sizes before and after a proposed pose, scale and mirror; '
                                  'writes nothing')
    measure.add_argument('input')
    measure.add_argument('--rotate', nargs='+', metavar='DEG',
                         help='RX RY RZ extrinsic degrees; auto is not accepted here because a '
                              'search result is not a measurement of a chosen pose')
    measure.add_argument('--scale', nargs='+', metavar='FACTOR',
                         help='one uniform factor or three per-axis factors to measure under')
    measure.add_argument('--mirror', nargs='+', choices=('x', 'y', 'z'), metavar='AXIS',
                         help='axes to mirror before measuring')
    measure.add_argument('--center-offset', nargs=2, type=float, default=None, metavar=('X', 'Y'),
                         help='plate-center offset in mm')
    measure.add_argument('--model-lift-mm', type=float, default=None,
                         help='height of the lowest model point above the plate')
    measure.add_argument('--target-mm', nargs=3, type=float, default=None,
                         metavar=('X', 'Y', 'Z'),
                         help='wanted size on each axis; the uniform and per-axis factors that '
                              'would reach it are reported. Use 0 on an axis to ignore it')
    measure.set_defaults(func=cmd_measure)

    islands = sub.add_parser('islands', parents=[common],
                             help='island-only connectivity scan of an STL; much faster than '
                                  'validate because it skips voids, traps and drainage')
    islands.add_argument('input')
    islands.add_argument('--max-examples', type=int, default=64,
                         help='cap on reported island positions; the count is never capped')
    islands.set_defaults(func=cmd_islands)

    profile = sub.add_parser('profile', parents=[common],
                             help='resolved settings and exposures, or the discoverable profile library')
    profile.add_argument('action', nargs='?', default='show', choices=('show', 'list', 'diff', 'save'),
                         help='show the resolved settings (default), list the library, '
                              'diff against another profile, or save the resolution as a .ptr')
    profile.add_argument('--kind', choices=('printer', 'resin'),
                         help='restrict list, or name the kind that --against refers to')
    profile.add_argument('--against', metavar='PROFILE',
                         help='profile identifier, path, or the word defaults, for diff')
    profile.add_argument('--provenance', action='store_true',
                         help='also report which resolution layer last set each value')
    profile.add_argument('--output', help='destination .ptr for save')
    profile.add_argument('--name', help='printer display name for save')
    profile.add_argument('--hardware-only', action='store_true',
                         help='save only printer hardware settings; other sections inherit')
    profile.set_defaults(func=cmd_profile)

    resin = sub.add_parser('resin', parents=[common],
                           help='list, show, save, or bind resin profiles')
    resin.add_argument('action', nargs='?', default='list', choices=('list', 'show', 'save', 'bind'))
    resin.add_argument('resin', nargs='?', help='resin identifier or path; defaults to --resin')
    resin.add_argument('--from', dest='from_printer', metavar='PRINTER_ID',
                       help='printer id whose process block is copied; defaults to the only one')
    resin.add_argument('--to', metavar='PRINTER_ID',
                       help='printer id to bind the copied process to; defaults to the resolved printer')
    resin.add_argument('--output', help='destination .res for bind')
    resin.add_argument('--name', help='resin display name for save')
    resin.set_defaults(func=cmd_resin)

    example = sub.add_parser('support-example', parents=[common],
                            help='build the support editor attachment example and optionally save its illustrative STL')
    example.add_argument('--height-mm', type=float, default=20.0,
                         help='height of four fixed example contacts, from 3 to 160 mm')
    example.add_argument('--output', help='optional illustrative STL destination; no print validation is performed')
    example.set_defaults(func=cmd_support_example)

    preset = sub.add_parser('preset', parents=[common],
                            help='list, inspect or save portable support or process presets')
    preset.add_argument('action', choices=('list', 'show', 'save'))
    preset.add_argument('preset', nargs='?', default=None,
                        help='built-in name or JSON path for show '
                             '(default: medium for support, default for process)')
    preset.add_argument('--kind', choices=('support', 'process'), default='support',
                        help='preset section to list, show or save (default: support)')
    preset.add_argument('--output', help='preset JSON destination for save')
    preset.add_argument('--name', help='display name for save (default: destination stem)')
    preset.set_defaults(func=cmd_preset)

    boolean = sub.add_parser('boolean', parents=[common],
                             help='union, intersect or subtract two STLs as closed solids')
    boolean.add_argument('a', help='first STL (minuend for --subtract)')
    boolean.add_argument('b', help='second STL (subtrahend for --subtract)')
    op = boolean.add_mutually_exclusive_group(required=True)
    op.add_argument('--union', action='store_const', const='union', dest='op',
                    help='boolean union of A and B')
    op.add_argument('--intersect', action='store_const', const='intersect', dest='op',
                    help='boolean intersection of A and B')
    op.add_argument('--subtract', action='store_const', const='subtract', dest='op',
                    help='boolean difference A minus B')
    boolean.add_argument('--output', required=True, help='result STL destination')
    boolean.set_defaults(func=cmd_boolean)

    trim = sub.add_parser('trim', parents=[common],
                          help='cut a mesh by a plane and cap the open face for printing')
    trim.add_argument('input')
    trim.add_argument('--point', nargs=3, type=float, required=True, metavar=('X', 'Y', 'Z'),
                      help='a point on the cut plane, in millimeters')
    trim.add_argument('--normal', nargs=3, type=float, required=True, metavar=('NX', 'NY', 'NZ'),
                      help='plane normal; --keep positive retains the half-space along this vector')
    trim.add_argument('--keep', choices=('positive', 'negative'), default='positive',
                      help='which side of the plane to keep (default positive)')
    trim.add_argument('--output', required=True, help='capped solid STL destination')
    trim.set_defaults(func=cmd_trim)

    cap = sub.add_parser(
        'cap', parents=[common],
        help='fill near-planar open boundary loops; refuse non-planar holes')
    cap.add_argument('input', help='open or closed STL')
    cap.add_argument('--output', required=True,
                     help='destination STL; written only when every boundary loop caps')
    cap.set_defaults(func=cmd_cap)

    hollow_cmd = sub.add_parser(
        'hollow', parents=[common],
        help='voxel-hollow a solid with optional drain/vent holes and lattice infill')
    hollow_cmd.add_argument('input', help='source STL')
    hollow_cmd.add_argument('--output', required=True, help='hollowed STL destination')
    hollow_cmd.add_argument('--no-holes', action='store_true',
                            help='keep an enclosed cavity; skip automatic drain/vent pairs')
    hollow_cmd.set_defaults(func=cmd_hollow)

    thickness_cmd = sub.add_parser(
        'thickness', parents=[common],
        help='report wall thickness from a voxel distance transform')
    thickness_cmd.add_argument('input', help='source STL')
    thickness_cmd.add_argument('--threshold-mm', type=float,
                               help='thin-region threshold; defaults to hollow.min_wall_thickness_mm')
    thickness_cmd.set_defaults(func=cmd_thickness)

    calibrate = sub.add_parser(
        'calibrate', help='offline exposure or tolerance calibration GOO generator')
    cal_sub = calibrate.add_subparsers(dest='calibrate_command', required=True)
    for kind, help_text in (
            ('exposure', 'spatial exposure matrix (RERF-style gray levels)'),
            ('tolerance', 'spatial tolerance matrix (patch size encodes offset)')):
        action = cal_sub.add_parser(kind, parents=[common], help=help_text)
        action.add_argument('--range', required=True, help='lo:hi parameter range')
        action.add_argument('--steps', type=int, required=True,
                            help='number of matrix cells / parameter samples')
        action.add_argument('--layers', type=int, default=2,
                            help='identical stacked layers for print thickness (default 2)')
        action.add_argument('--output', required=True, help='destination .goo path')
        action.set_defaults(func=cmd_calibrate)

    completion = sub.add_parser('completion',
                                help='print a shell completion script generated from this parser')
    completion.add_argument('shell', choices=('bash', 'zsh', 'fish'))
    completion.add_argument('--output', help='write the script here instead of stdout')
    completion.set_defaults(func=cmd_completion)

    manpage = sub.add_parser('manpage', help='print a roff man page generated from this parser')
    manpage.add_argument('--section', type=int, default=1,
                         help='manual section number for the .TH line')
    manpage.add_argument('--output', help='write the page here instead of stdout')
    manpage.set_defaults(func=cmd_manpage)

    report_html = sub.add_parser(
        'report-html', help='render a JSON report as a readable static HTML page')
    report_html.add_argument('report_json', help='path to a voxelmill JSON report')
    report_html.add_argument('--output', required=True, help='HTML destination')
    report_html.set_defaults(func=cmd_report_html)

    gui = sub.add_parser('gui', parents=[common], help='open the placement and support editor')
    gui.add_argument('input', nargs='?')
    gui.add_argument('--view', choices=('front', 'back', 'left', 'right', 'top', 'bottom', 'iso'),
                     help='initial camera view; front is the -Y face the green plate edge marks')
    gui.add_argument('--goo', help='open this GOO file for layer inspection on startup')
    gui.set_defaults(func=cmd_gui)

    monitor = sub.add_parser('monitor', help='open the read-only printer status, camera and history monitor')
    monitor.add_argument('--host', help='known printer IP address (discovery is also available in the window)')
    monitor.add_argument('--mainboard-id', help='SDCP mainboard ID paired with --host')
    monitor.add_argument('--demo', action='store_true', help='use the offline loopback printer')
    monitor.set_defaults(func=cmd_monitor)
    return parser


def _version():
    from . import __version__
    return f'VoxelMill {__version__}'


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except VoxelMillError as error:
        json.dump({'error': error.to_dict()}, sys.stderr, indent=2, default=str)
        sys.stderr.write('\n')
        return 3
    except KeyboardInterrupt:
        sys.stderr.write('canceled\n')
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
