#!/usr/bin/env python3
"""Behavioral-equivalence gate between VoxelMill v0.1.0 and v0.2.0.

The rewrite at /home3/voxelmill (v0.2.0) is meant to behave identically to
the tree it replaces, /home3/noisecancelingcodex (v0.1.0), for the pipeline
commands that matter: inspect, prepare, and slice. This script drives both
trees with the SAME arguments on the SAME fixture shapes and diffs what came
out -- the JSON reports structurally (after stripping fields that are
expected to vary run to run, like wall-clock seconds) and the binary
`.goo`/`.stl` outputs by content.

    .venv/bin/python scripts/equivalence.py
    .venv/bin/python scripts/equivalence.py --scenario sphere --scenario torus -v
    .venv/bin/python scripts/equivalence.py --update-golden

Each shape in fixtures/shapes/*.stl is one scenario. A scenario runs
inspect -> prepare -> slice as three subprocesses per side (old and new),
each invoked as `<root>/.venv/bin/python -m voxelmill.cli ...` with the
tree's own venv and cwd, so neither side can accidentally import the other's
code. When old-root is unavailable (e.g. it has since been deleted) the
`--update-golden` / golden-comparison path lets the new tree keep guarding
its own regressions: a golden set of normalized reports is checked in once
under old-vs-new agreement, and every later run is compared against it.

Exit codes: 0 all scenarios match, 1 some scenario differs, 2 a harness error
(a fixture is missing, a subprocess could not even start, bad arguments).
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

DEFAULT_OLD_ROOT = Path('/home3/noisecancelingcodex')
DEFAULT_NEW_ROOT = Path('/home3/voxelmill')

#: Keys that are expected to differ between otherwise-identical runs because
#: they measure the run itself (wall time, memory, worker count) rather than
#: what the run computed. Matched by key name at ANY nesting depth -- these
#: were found by diffing two real runs and turned out to recur far deeper
#: than the top level (e.g. buried in `passes[i].supports.seconds` or
#: `validation.metrics.drainage.seconds`), so the normalizer walks the whole
#: tree rather than a fixed list of dotted paths.
VOLATILE_KEYS = {
    'seconds', 'inspection_seconds', 'peak_rss_bytes', 'scratch_bytes', 'analysis_workers',
    'software_version',
}

#: Keys whose *string* values are filesystem paths. Two things vary by
#: construction here: the tree root (the whole point is that the two sides live
#: in different directories) and the randomized per-run scratch directory
#: (/tmp/voxelmill-prepare-<random>/prepared.stl), so only the basename carries
#: meaning. A value under one of these keys that is not a path is left alone --
#: `Path('printer').name` is 'printer' -- so this cannot mask a real
#: difference in a non-path field that happens to share the name.
PATH_KEYS = {'path', 'source', 'output', 'input', 'destination', 'scratch_dir'}

#: Subtree that just echoes the invocation's resource flags back (workers,
#: memory budget, scratch dir) -- never part of what the pipeline computed.
VOLATILE_SUBTREES = {'resources'}

STEPS = ('inspect', 'prepare', 'slice')

#: Columns shown in the summary table. Same as STEPS plus `prepared_stl`,
#: which is not a pipeline step -- it is a derived comparison (a content hash
#: of prepare's output) that is evaluated independently of whether `prepare`
#: itself returned 0, so it gets its own column instead of being folded into
#: the `prepare` one. Only print_summary (and the various "nothing ran"
#: step_status dicts, for cosmetic completeness) use this; everything that
#: actually drives subprocesses or golden-report comparisons still uses
#: STEPS, since prepared_stl has no JSON report of its own.
DISPLAY_STEPS = ('inspect', 'prepare', 'prepared_stl', 'slice')


def normalize(value, key=None):
    """Recursively strip volatile fields and canonicalize path strings.

    Driven by the module-level VOLATILE_KEYS/PATH_KEYS/VOLATILE_SUBTREES sets
    so extending the volatile set later is a one-line change here, not a new
    special case in the diff logic below.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in VOLATILE_KEYS or k in VOLATILE_SUBTREES:
                continue
            out[k] = normalize(v, key=k)
        return out
    if isinstance(value, list):
        return [normalize(v, key=key) for v in value]
    if key in PATH_KEYS and isinstance(value, str):
        return Path(value).name
    return value


def _type_tag(value):
    if isinstance(value, bool):
        return 'bool'
    if isinstance(value, (int, float)):
        return 'number'
    if value is None:
        return 'null'
    return type(value).__name__


def diff_values(old, new, path, out):
    """Depth-first structural diff of two already-normalized JSON values.

    Appends human-readable ``.dotted.path[index]: old vs new`` lines to
    ``out``. Floats compare with a relative tolerance since the two trees may
    do arithmetic in a different order; everything else compares exactly.
    """
    if isinstance(old, dict) and isinstance(new, dict):
        keys = sorted(set(old) | set(new))
        for k in keys:
            child = f'{path}.{k}'
            if k not in old:
                out.append(f'{child}: <missing on old> vs {_short(new[k])}')
            elif k not in new:
                out.append(f'{child}: {_short(old[k])} vs <missing on new>')
            else:
                diff_values(old[k], new[k], child, out)
        return
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            out.append(f'{path}: list length {len(old)} vs {len(new)}')
            return
        for i, (a, b) in enumerate(zip(old, new)):
            diff_values(a, b, f'{path}[{i}]', out)
        return
    if _type_tag(old) != _type_tag(new):
        out.append(f'{path}: type {_type_tag(old)} vs {_type_tag(new)} ({_short(old)} vs {_short(new)})')
        return
    if isinstance(old, float) or isinstance(new, float):
        if not math.isclose(float(old), float(new), rel_tol=1e-9):
            out.append(f'{path}: {_short(old)} vs {_short(new)}')
        return
    if old != new:
        out.append(f'{path}: {_short(old)} vs {_short(new)}')


def _short(value, limit=120):
    text = json.dumps(value) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[:limit] + '...'


def diff_reports(old_json, new_json, label, cap=40):
    """Normalize both sides, diff, and cap the output. Returns (match, lines)."""
    differences = []
    diff_values(normalize(old_json), normalize(new_json), '', differences)
    # The top-level call passes path='' so every real line begins with '.';
    # strip that leading dot for readability (".foo" -> "foo", ".x[0]" as is).
    differences = [d[1:] if d.startswith('.') else d for d in differences]
    if len(differences) > cap:
        shown = differences[:cap]
        shown.append(f'... and {len(differences) - cap} more')
    else:
        shown = differences
    lines = [f'{label}: {d}' for d in shown]
    return not differences, lines


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def compare_stl(old_path, new_path):
    if sha256_file(old_path) == sha256_file(new_path):
        return True, []
    return False, [f'{old_path.name}: sha256 differs']


def compare_goo(old_path, new_path, new_root):
    """Compare two .goo files ignoring the fields export always randomizes.

    A raw byte/hash compare always fails for .goo: the header embeds
    `file_create_time` (a strftime call at export time) and
    `software_version`. So this opens both files with the NEW tree's
    `voxelmill.goo.GooReader` -- imported from new_root's own venv/site
    tree, never guessed at -- and compares every other header field plus
    the decoded per-layer pixel payloads. If that reader can't be imported
    (e.g. the compiled decoder extension is missing), fall back to a
    byte compare that excludes the fixed 195477-byte header, and say so
    plainly rather than silently downgrading precision.
    """
    reader = _load_goo_reader(new_root)
    if reader is None:
        return _compare_goo_fallback(old_path, new_path)

    ignored_header_fields = {'file_create_time', 'software_version'}
    problems = []
    with reader(old_path) as old_goo, reader(new_path) as new_goo:
        for key in sorted(set(old_goo.header) | set(new_goo.header)):
            if key in ignored_header_fields:
                continue
            old_value, new_value = old_goo.header.get(key), new_goo.header.get(key)
            if isinstance(old_value, float) or isinstance(new_value, float):
                if not math.isclose(float(old_value), float(new_value), rel_tol=1e-9):
                    problems.append(f'header.{key}: {old_value} vs {new_value}')
            elif old_value != new_value:
                problems.append(f'header.{key}: {old_value} vs {new_value}')

        old_count, new_count = len(old_goo.layers), len(new_goo.layers)
        if old_count != new_count:
            problems.append(f'layer_count: {old_count} vs {new_count}')
        else:
            first_bad, bad_count = None, 0
            for index in range(old_count):
                old_pixels = old_goo.decode(index)
                new_pixels = new_goo.decode(index)
                if old_pixels.shape != new_pixels.shape or (old_pixels != new_pixels).any():
                    bad_count += 1
                    if first_bad is None:
                        first_bad = index
            if bad_count:
                problems.append(f'{bad_count}/{old_count} layers differ, first at index {first_bad}')
    return not problems, [f'{old_path.name}: {p}' for p in problems]


def _load_goo_reader(new_root):
    """Import voxelmill.goo.GooReader from the NEW tree's own site-packages."""
    src_dir = new_root / 'src'
    inserted = str(src_dir) not in sys.path
    if inserted:
        sys.path.insert(0, str(src_dir))
    try:
        from voxelmill.goo import GooReader  # type: ignore
        return GooReader
    except Exception:
        return None
    finally:
        if inserted:
            sys.path.remove(str(src_dir))


def _compare_goo_fallback(old_path, new_path):
    print('WARNING: voxelmill.goo.GooReader was not importable; degrading to a raw byte '
          'compare that excludes the first 512 header bytes. This will not catch a '
          'layer-payload difference that happens to land inside an unread region.',
          file=sys.stderr)
    header_skip = 512
    old_bytes = old_path.read_bytes()[header_skip:]
    new_bytes = new_path.read_bytes()[header_skip:]
    if old_bytes == new_bytes:
        return True, []
    return False, [f'{old_path.name}: bytes differ past the first {header_skip} header bytes (degraded compare)']


def find_shapes(root):
    """All shape fixtures: the 13 valid top-level scenarios plus the 5 error
    fixtures under fixtures/shapes/invalid/ (degenerate, flipped_winding,
    nonmanifold_edge, open_box, self_intersecting) -- those exercise the
    failure paths and diagnostic payloads, which is exactly the code a
    rewrite is most likely to get subtly wrong.

    Both sets are addressed by bare basename (no 'invalid/' prefix needed on
    --scenario), via resolve_shape below, so a name colliding between the
    two directories would silently make one shadow the other. There is no
    collision today, but this checks rather than assumes, so a future
    fixture addition fails loudly instead of quietly shadowing.
    """
    base = root / 'fixtures' / 'shapes'
    top = sorted(base.glob('*.stl'))
    invalid = sorted((base / 'invalid').glob('*.stl'))
    seen = {}
    for path in top + invalid:
        seen.setdefault(path.stem, []).append(path)
    collisions = sorted(name for name, paths in seen.items() if len(paths) > 1)
    if collisions:
        raise ValueError(
            f'fixture name collision between top-level and invalid/ shapes: {", ".join(collisions)}')
    return top + invalid


def resolve_shape(root, name):
    """Locate a scenario's .stl by bare name, top-level first then invalid/.

    Mirrors find_shapes's namespace: a scenario name is looked up the same
    way regardless of whether it came from the default listing or from an
    explicit --scenario. Returns a Path that may not exist -- callers already
    check .exists() (that is how a scenario is treated as "shape missing").
    """
    base = root / 'fixtures' / 'shapes'
    candidate = base / f'{name}.stl'
    if candidate.exists():
        return candidate
    return base / 'invalid' / f'{name}.stl'


def run_cli(root, args, cwd):
    """Run one voxelmill.cli subcommand under `root`'s own interpreter."""
    python = root / '.venv' / 'bin' / 'python'
    command = [str(python), '-m', 'voxelmill.cli', *args]
    completed = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True)
    return {'returncode': completed.returncode, 'stdout': completed.stdout, 'stderr': completed.stderr}


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def run_scenario_steps(root, shape, work_dir, workers=None):
    """Run inspect -> prepare -> slice for one shape under one tree.

    Stops early (recording no further steps) once inspect's returncode is
    nonzero, since prepare needs inspect's *files* only implicitly (both
    just take the shape path). prepare is different: it exits nonzero on
    plenty of fixtures even with --allow-unresolved (that flag only waives
    unresolved islands -- e.g. torus separately fails the enclosed_voids and
    drainage_bottlenecks validation checks), but it still WRITES a complete,
    valid prepared.stl in that case. So slice is attempted whenever
    prepared.stl exists on disk, regardless of prepare's returncode -- only
    a prepare that produced nothing at all blocks slice. Whether a nonzero
    prepare is actually *comparable* across the two trees (same returncode,
    both wrote the file) is decided later in evaluate_scenario; this
    function just tries whatever it has enough input to try.
    Returns {step: {'process': {...}, 'report': dict|None}}.
    """
    worker_flags = ['--workers', str(workers)] if workers is not None else []
    steps = {}
    inspect_report = work_dir / 'inspect.json'
    result = run_cli(root, ['inspect', str(shape), '--report', str(inspect_report), *worker_flags], work_dir)
    steps['inspect'] = {'process': result, 'report': _read_json(inspect_report)}
    if result['returncode'] != 0:
        return steps

    prepared = work_dir / 'prepared.stl'
    prepare_report = work_dir / 'prepare.json'
    result = run_cli(root, ['prepare', str(shape), '--max-passes', '1', '--allow-unresolved',
                            '--output', str(prepared), '--report', str(prepare_report),
                            *worker_flags], work_dir)
    steps['prepare'] = {'process': result, 'report': _read_json(prepare_report),
                        'output': prepared if prepared.exists() else None}
    if result['returncode'] != 0 and not prepared.exists():
        return steps

    out_goo = work_dir / 'out.goo'
    slice_report = work_dir / 'slice.json'
    result = run_cli(root, ['slice', str(prepared), '--allow-unresolved', '--output', str(out_goo),
                            '--report', str(slice_report), *worker_flags], work_dir)
    steps['slice'] = {'process': result, 'report': _read_json(slice_report),
                      'output': out_goo if out_goo.exists() else None}
    return steps


def run_scenario(name, old_root, new_root, base_scratch, workers, verbose):
    """Run one shape's scenario under both trees, in isolated scratch dirs."""
    old_shape = resolve_shape(old_root, name)
    new_shape = resolve_shape(new_root, name)
    old_work = base_scratch / 'old' / name
    new_work = base_scratch / 'new' / name
    old_work.mkdir(parents=True, exist_ok=True)
    new_work.mkdir(parents=True, exist_ok=True)

    old_steps = run_scenario_steps(old_root, old_shape, old_work, workers) if old_shape.exists() else None
    new_steps = run_scenario_steps(new_root, new_shape, new_work, workers) if new_shape.exists() else None
    return {
        'name': name, 'old_steps': old_steps, 'new_steps': new_steps,
        'old_work': str(old_work), 'new_work': str(new_work),
    }


def _evaluate_golden_only(name, new_steps, golden_dir, verbose):
    """Compare the new side's reports against recorded goldens when the old
    tree is unavailable entirely (module docstring: "the golden-comparison
    path lets the new tree keep guarding its own regressions"). Mirrors the
    old-vs-new loop in evaluate_scenario, with the golden JSON standing in
    for old_info['report'].

    A step whose golden file does not exist is reported as an explicit
    error, not a quiet skip: a golden set that is silently missing a step
    is exactly the situation that would let a regression through
    unnoticed, so it must show up in the summary and fail the run rather
    than pass by omission.

    prepared_stl and the slice `.goo` binary payload are inherently
    old-vs-new comparisons -- there is no golden binary to compare
    against. With no old side they have not happened, so they are marked
    'skip' rather than silently reported as a match.
    """
    report_lines = []
    step_status = {}
    ok = True
    for step in STEPS:
        new_info = new_steps.get(step)
        golden_path = golden_dir / name / f'{step}.json'
        golden_exists = golden_path.exists()

        if new_info is None:
            if golden_exists:
                step_status[step] = 'differ'
                report_lines.append(
                    f'{name}.{step}: golden has a recorded report but the new run did not reach this step')
                ok = False
            else:
                step_status[step] = 'skip'
            continue

        if not golden_exists:
            step_status[step] = 'error'
            report_lines.append(
                f'{name}.{step}: NO GOLDEN ENTRY recorded for this step -- cannot verify it '
                '(re-record the golden set or investigate why it is missing)')
            ok = False
            continue

        golden_json = _read_json(golden_path)
        new_report = new_info['report']
        if golden_json is None or new_report is None:
            if golden_json != new_report:
                step_status[step] = 'differ'
                report_lines.append(f'{name}.{step}: report.json present on only one side (golden)')
                ok = False
            else:
                step_status[step] = 'match'
            continue

        matched, lines = diff_reports(golden_json, new_report, f'{name}.{step}.json (golden)')
        step_status[step] = 'match' if matched else 'differ'
        report_lines.extend(lines)
        ok = ok and matched

    # Neither binary comparison has an old side to run against.
    step_status['prepared_stl'] = 'skip'
    slice_info = new_steps.get('slice')
    if slice_info is not None and slice_info.get('output') is not None:
        report_lines.append(
            f'{name}.slice: binary .goo payload not compared (golden-only mode has no reference binary)')

    return {'name': name, 'step_status': step_status, 'lines': report_lines, 'ok': ok}


def evaluate_scenario(raw, old_root, new_root, golden_dir, update_golden, verbose):
    """Turn one scenario's raw run(s) into per-step match/differ + diff lines."""
    name = raw['name']
    old_steps, new_steps = raw['old_steps'], raw['new_steps']
    report_lines = []
    step_status = {}

    if update_golden:
        if new_steps is None:
            step_status = {step: 'skip' for step in DISPLAY_STEPS}
            report_lines.append(f'{name}: shape missing under new root, nothing to write')
            return {'name': name, 'step_status': step_status, 'lines': report_lines, 'ok': True}
        for step in STEPS:
            info = new_steps.get(step)
            if info is None:
                step_status[step] = 'skip'
                continue
            golden_path = golden_dir / name / f'{step}.json'
            if info['report'] is not None:
                golden_path.parent.mkdir(parents=True, exist_ok=True)
                golden_path.write_text(json.dumps(normalize(info['report']), indent=2, sort_keys=True) + '\n')
            step_status[step] = 'wrote'
        # prepared_stl has no JSON report to write to the golden set (it is
        # a direct old-vs-new hash compare, and golden mode does not involve
        # old-root at all) -- always 'skip' here, just for a tidy column.
        step_status['prepared_stl'] = 'skip'
        return {'name': name, 'step_status': step_status, 'lines': report_lines, 'ok': True}

    ok = True
    if old_steps is None and new_steps is None:
        return {'name': name, 'step_status': {s: 'skip' for s in DISPLAY_STEPS},
                'lines': [f'{name}: shape missing under both roots'], 'ok': True}
    if new_steps is None:
        return {'name': name, 'step_status': {s: 'error' for s in DISPLAY_STEPS},
                'lines': [f'{name}: shape missing under new root'], 'ok': False}
    if old_steps is None:
        if old_root.exists():
            # The old root is there but this particular shape is not -- a
            # genuine fixture problem, not the "old tree is gone" case the
            # golden path exists for.
            return {'name': name, 'step_status': {s: 'error' for s in DISPLAY_STEPS},
                    'lines': [f'{name}: shape missing under old root'], 'ok': False}
        # The whole old tree is unavailable (e.g. it has since been
        # deleted). Per the module docstring, fall back to comparing the
        # new side against the recorded goldens instead of failing outright
        # -- that is the entire point of --golden.
        if not golden_dir_has_content(golden_dir / name):
            return {'name': name, 'step_status': {s: 'error' for s in DISPLAY_STEPS},
                    'lines': [f'{name}: old root {old_root} does not exist and no golden entries are '
                              'recorded for this scenario -- nothing to compare against'], 'ok': False}
        return _evaluate_golden_only(name, new_steps, golden_dir, verbose)

    for step in STEPS:
        old_info, new_info = old_steps.get(step), new_steps.get(step)
        if old_info is None and new_info is None:
            step_status[step] = 'skip'
            continue
        if old_info is None or new_info is None:
            step_status[step] = 'differ'
            report_lines.append(f'{name}.{step}: ran on only one side')
            ok = False
            continue

        old_rc = old_info['process']['returncode']
        new_rc = new_info['process']['returncode']
        if old_rc != new_rc:
            step_status[step] = 'differ'
            report_lines.append(f'{name}.{step}: returncode {old_rc} vs {new_rc}')
            ok = False
            # Both sides failing differently, or one failing where the other
            # didn't, means there is nothing comparable downstream -- e.g.
            # slice cannot run without prepare's output.
            if old_rc != 0 or new_rc != 0:
                break
            continue
        if old_rc != 0:
            # Both sides failed the SAME way. For most steps that is the end
            # of the story (expected for fixtures/shapes/invalid/: no report,
            # no output, nothing left to compare). `prepare` is the
            # exception -- it deliberately keeps writing a usable
            # prepared.stl (and a complete prepare.json) alongside a failing
            # validation verdict, so when BOTH sides produced that file,
            # fall through to the same report comparison the success path
            # uses below, and keep going to `slice` instead of stopping
            # here. (prepared.stl itself is compared separately, after this
            # loop, regardless of returncode -- see the prepared_stl block.)
            step_status[step] = 'match'
            if verbose:
                report_lines.append(f'{name}.{step}: both sides failed with returncode {old_rc} (expected)')
            has_comparable_output = (step in ('prepare', 'slice')
                                     and old_info.get('output') and new_info.get('output'))
            if not has_comparable_output:
                break

        step_ok = True
        if old_info['report'] is None or new_info['report'] is None:
            if old_info['report'] != new_info['report']:
                report_lines.append(f'{name}.{step}: report.json present on only one side')
                step_ok = False
        else:
            matched, lines = diff_reports(old_info['report'], new_info['report'], f'{name}.{step}.json')
            step_ok = matched
            report_lines.extend(lines)

        if step == 'slice' and old_info.get('output') and new_info.get('output'):
            matched_bin, lines = compare_goo(old_info['output'], new_info['output'], new_root)
            step_ok = step_ok and matched_bin
            report_lines.extend(f'{name}.{step}: {line}' for line in lines)
        elif step == 'slice' and bool(old_info.get('output')) != bool(new_info.get('output')):
            step_ok = False
            report_lines.append(f'{name}.{step}: output file present on only one side')

        step_status[step] = 'match' if step_ok else 'differ'
        ok = ok and step_ok

    # prepared.stl is compared independently of the `prepare` step's
    # returncode -- it is written whenever prepare produced output at all,
    # even alongside a failing validation verdict (see run_scenario_steps),
    # and the STL is known to be deterministic and byte-identical across
    # runs, so a plain content hash is the correct and most direct check of
    # whether the two trees computed the same geometry. Reported as its own
    # column rather than folded into `prepare` because the two measure
    # different things: `prepare` is "did the CLI invocation behave the same
    # way" (returncode + JSON report), this is "is the mesh it wrote
    # byte-identical".
    old_prepare, new_prepare = old_steps.get('prepare'), new_steps.get('prepare')
    old_stl = old_prepare.get('output') if old_prepare else None
    new_stl = new_prepare.get('output') if new_prepare else None
    if old_stl and new_stl:
        matched_bin, lines = compare_stl(old_stl, new_stl)
        step_status['prepared_stl'] = 'match' if matched_bin else 'differ'
        report_lines.extend(f'{name}.prepared_stl: {line}' for line in lines)
        ok = ok and matched_bin
    elif bool(old_stl) != bool(new_stl):
        step_status['prepared_stl'] = 'differ'
        report_lines.append(f'{name}.prepared_stl: output file present on only one side')
        ok = False
    else:
        step_status['prepared_stl'] = 'skip'

    if golden_dir_has_content(golden_dir):
        for step in STEPS:
            new_info = new_steps.get(step)
            if new_info is None or new_info['report'] is None:
                continue
            golden_path = golden_dir / name / f'{step}.json'
            if not golden_path.exists():
                continue
            golden_json = _read_json(golden_path)
            matched, lines = diff_reports(golden_json, new_info['report'], f'{name}.{step}.json (golden)')
            if not matched:
                ok = False
                report_lines.extend(lines)
                if step_status.get(step) == 'match':
                    step_status[step] = 'differ'

    return {'name': name, 'step_status': step_status, 'lines': report_lines, 'ok': ok}


def golden_dir_has_content(golden_dir):
    return golden_dir.exists() and any(golden_dir.iterdir())


def print_summary(evaluations, update_golden):
    """Readable terminal table: one row per scenario, plus a total line."""
    header = f'{"scenario":<28} ' + ' '.join(f'{s:<13}' for s in DISPLAY_STEPS) + '  result'
    print(header)
    print('-' * len(header))
    total_ok = 0
    for ev in evaluations:
        cells = ' '.join(f'{ev["step_status"].get(s, "-"):<13}' for s in DISPLAY_STEPS)
        result = 'WROTE' if update_golden else ('OK' if ev['ok'] else 'DIFFERS')
        print(f'{ev["name"]:<28} {cells}  {result}')
        total_ok += int(ev['ok'])
    print('-' * len(header))
    verb = 'written' if update_golden else 'matching'
    print(f'total: {total_ok}/{len(evaluations)} scenarios {verb}')
    for ev in evaluations:
        for line in ev['lines']:
            print(f'  {line}')


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--old-root', type=Path, default=DEFAULT_OLD_ROOT,
                        help='v0.1.0 tree (default: %(default)s)')
    parser.add_argument('--new-root', type=Path, default=DEFAULT_NEW_ROOT,
                        help='v0.2.0 tree (default: %(default)s)')
    parser.add_argument('--scenario', action='append', dest='scenarios',
                        help='shape name (no extension) to run; repeatable. Default: every '
                             'fixtures/shapes/*.stl under new-root')
    parser.add_argument('--workers', type=int, default=None,
                        help='forwarded as --workers to the pipeline commands (unset: pipeline default)')
    parser.add_argument('--jobs', type=int, default=1,
                        help='scenarios to run concurrently (default: 1; these runs are memory-heavy)')
    parser.add_argument('--update-golden', action='store_true',
                        help="write the NEW side's normalized reports into --golden instead of comparing")
    parser.add_argument('--golden', type=Path, default=None,
                        help='golden directory (default: <new-root>/reports/golden)')
    parser.add_argument('--verbose', '-v', action='store_true')
    return parser.parse_args(argv)


def _run_scenario_worker(args):
    """Top-level so ProcessPoolExecutor can pickle it."""
    name, old_root, new_root, scratch, workers, verbose = args
    return run_scenario(name, old_root, new_root, scratch, workers, verbose)


def main(argv=None):
    args = parse_args(argv)
    old_root, new_root = args.old_root.resolve(), args.new_root.resolve()
    golden_dir = (args.golden or (new_root / 'reports' / 'golden')).resolve()

    if not (new_root / 'fixtures' / 'shapes').is_dir():
        print(f'error: {new_root} has no fixtures/shapes directory', file=sys.stderr)
        return 2
    if not args.update_golden and not old_root.exists():
        if not golden_dir_has_content(golden_dir):
            print(f'error: old root {old_root} does not exist and golden dir {golden_dir} is empty; '
                  'nothing to compare against', file=sys.stderr)
            return 2

    shapes = args.scenarios or [p.stem for p in find_shapes(new_root)]
    if not shapes:
        print('error: no scenarios found', file=sys.stderr)
        return 2

    scratch_root = Path(tempfile.mkdtemp(prefix='voxelmill-equivalence-'))
    try:
        jobs = max(1, args.jobs)
        if args.update_golden:
            golden_dir.mkdir(parents=True, exist_ok=True)
        # Evaluate each scenario as soon as it finishes and keep only the
        # verdict. Holding every scenario's parsed reports until the end grew
        # monotonically across the fixture set and got a full run killed for
        # memory; the evaluation is a few status strings and diff lines.
        # `pool.map` yields in submission order, so ordering stays deterministic
        # either way.
        def _evaluate(raw, position):
            evaluation = evaluate_scenario(raw, old_root, new_root, golden_dir, args.update_golden, args.verbose)
            verdict = 'WROTE' if args.update_golden else ('OK' if evaluation['ok'] else 'DIFFERS')
            # stderr, flushed: a long run shows progress, and a run that is
            # killed still leaves a record of how far it got. The ordered table
            # on stdout is unaffected.
            print(f'[{position}/{len(shapes)}] {evaluation["name"]}: {verdict}',
                  file=sys.stderr, flush=True)
            return evaluation

        evaluations = []
        if jobs == 1:
            for position, name in enumerate(shapes, 1):
                evaluations.append(_evaluate(
                    run_scenario(name, old_root, new_root, scratch_root, args.workers, args.verbose),
                    position))
        else:
            work_items = [(name, old_root, new_root, scratch_root, args.workers, args.verbose)
                         for name in shapes]
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                for position, result in enumerate(pool.map(_run_scenario_worker, work_items), 1):
                    evaluations.append(_evaluate(result, position))
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)

    print_summary(evaluations, args.update_golden)
    if args.update_golden:
        return 0
    return 0 if all(ev['ok'] for ev in evaluations) else 1


if __name__ == '__main__':
    raise SystemExit(main())
