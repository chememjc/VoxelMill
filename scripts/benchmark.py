#!/usr/bin/env python3
"""Runtime and peak-memory baselines for the pipeline, in fresh processes.

The first performance figures this project had (`docs/performance.md`) were
measured sequentially in one Python process, so their peak RSS values are
cumulative high-water marks and cannot be compared with each other. Every
scenario here runs in its own child, which reports its own `RUSAGE_SELF` peak,
so the numbers are per-scenario and comparable.

    .venv/bin/python scripts/benchmark.py --output reports/bench/baseline.json
    .venv/bin/python scripts/benchmark.py --baseline reports/bench/baseline.json

The second form is the regression gate: exit 2 when a scenario exceeds its
baseline by more than the tolerance. **Wall time on a shared machine is a smoke
alarm, not a measurement** -- the tolerance is deliberately loose and the
reported figure is the minimum over repeats, which is the run least contaminated
by whatever else was running. Peak RSS is far more stable and is gated tighter.

Scenarios needing the 50-300 MB originals are opt-in with `--samples`, matching
the `samples` pytest marker.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'fixtures' / 'shapes'
SCRATCH = Path(os.environ.get('VOXELMILL_BENCH_DIR', '/tmp/voxelmill-bench'))

#: ``name -> (argv, needs_originals)``. Each argv is a full ``voxelmill``
#: command line; the child runs ``cli.main`` on it directly so no console
#: script or shell is in the measurement.
def scenarios(work):
    small = FIXTURES / 'sphere.stl'
    bracket = FIXTURES / 'overhang_bracket.stl'
    return {
        'inspect_small': ([
            'inspect', str(small), '--report', str(work / 'inspect.json')], False),
        'prepare_small': ([
            'prepare', str(small), '--max-passes', '1', '--allow-unresolved',
            '--output', str(work / 'small.stl'), '--report', str(work / 'small.json')], False),
        'prepare_bracket': ([
            'prepare', str(bracket), '--max-passes', '1', '--allow-unresolved',
            '--output', str(work / 'bracket.stl'), '--report', str(work / 'bracket.json')], False),
        'validate_small': ([
            'validate', str(work / 'small.stl'), '--report', str(work / 'validate.json')], False),
        'slice_small': ([
            'slice', str(work / 'small.stl'), '--output', str(work / 'small.goo'),
            '--allow-unresolved', '--report', str(work / 'slice.json')], False),
        'verify_small': ([
            'verify', str(work / 'small.goo'), '--report', str(work / 'verify.json')], False),
        'prepare_nut': ([
            'prepare', str(ROOT / 'inputstl' / 'floatvalveR7-nut.stl'), '--max-passes', '1',
            '--allow-unresolved', '--output', str(work / 'nut.stl'),
            '--report', str(work / 'nut.json')], True),
        'place_temporal_bone': ([
            'prepare', str(ROOT / 'inputstl' / 'right_temporal_bone_mars5_oriented.stl'),
            '--rotate', 'auto', '--max-passes', '1', '--allow-unresolved',
            '--output', str(work / 'bone.stl'), '--report', str(work / 'bone.json')], True),
    }

#: Ordering matters: some scenarios consume what an earlier one wrote.
ORDER = ('inspect_small', 'prepare_small', 'validate_small', 'slice_small', 'verify_small',
         'prepare_bracket', 'prepare_nut', 'place_temporal_bone')


def run_child(argv):
    """Run one voxelmill command in a fresh process and report its own peak RSS."""
    child = [sys.executable, str(Path(__file__).resolve()), '--child', *argv]
    started = time.monotonic()
    finished = subprocess.run(child, capture_output=True, text=True)
    elapsed = time.monotonic() - started
    record = {'exit_code': finished.returncode, 'seconds': elapsed, 'peak_rss_bytes': None}
    for line in reversed(finished.stdout.strip().splitlines()):
        if line.startswith('{"__bench__"'):
            record.update(json.loads(line))
            break
    else:
        record['stderr_tail'] = finished.stderr[-2000:]
    return record


def child_main(argv):
    from voxelmill.cli import main
    code = 0
    try:
        code = main(argv)
    except SystemExit as exit_code:      # argparse and friends
        code = int(exit_code.code or 0)
    finally:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        # ru_maxrss is kibibytes on Linux.
        print(json.dumps({'__bench__': 1, 'exit_code': code,
                          'peak_rss_bytes': int(usage.ru_maxrss) * 1024,
                          'user_seconds': usage.ru_utime,
                          'system_seconds': usage.ru_stime}), flush=True)
    return 0


def compare(results, baseline, time_tolerance, memory_tolerance):
    """Which scenarios regressed, and by how much. Missing ones are reported."""
    regressions, missing = [], []
    for name, current in results.items():
        previous = baseline.get(name)
        if previous is None:
            missing.append(name)
            continue
        for field, tolerance in (('seconds', time_tolerance),
                                 ('peak_rss_bytes', memory_tolerance)):
            was, now = previous.get(field), current.get(field)
            if not was or not now:
                continue
            if now > was * (1 + tolerance):
                regressions.append({'scenario': name, 'metric': field, 'baseline': was,
                                    'measured': now, 'ratio': now / was,
                                    'tolerance': tolerance})
    return regressions, missing


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--child', nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    parser.add_argument('--output', type=Path, help='write the measured results here')
    parser.add_argument('--baseline', type=Path,
                        help='compare against this file and fail on a regression')
    parser.add_argument('--only', action='append', help='run only these scenarios')
    parser.add_argument('--samples', action='store_true',
                        help='include the scenarios needing the 50-300 MB originals')
    parser.add_argument('--repeats', type=int, default=1,
                        help='runs per scenario; the minimum is reported')
    parser.add_argument('--time-tolerance', type=float, default=0.50)
    parser.add_argument('--memory-tolerance', type=float, default=0.15)
    parser.add_argument('--work-dir', type=Path, default=SCRATCH)
    args = parser.parse_args()
    if args.child is not None:
        return child_main(args.child)

    work = args.work_dir
    work.mkdir(parents=True, exist_ok=True)
    available = scenarios(work)
    chosen = [name for name in ORDER
              if name in available
              and (args.samples or not available[name][1])
              and (not args.only or name in args.only)]
    results = {}
    for name in chosen:
        argv, _needs = available[name]
        runs = [run_child(argv) for _ in range(max(1, args.repeats))]
        # The minimum is the run least contaminated by other load. Both are
        # reported so a wide spread is visible rather than hidden.
        best = min(runs, key=lambda r: r['seconds'])
        results[name] = {
            'argv': argv, 'runs': len(runs),
            'seconds': best['seconds'],
            'seconds_max': max(r['seconds'] for r in runs),
            'peak_rss_bytes': max((r['peak_rss_bytes'] or 0) for r in runs) or None,
            'exit_code': best['exit_code'],
        }
        if 'stderr_tail' in best:
            results[name]['stderr_tail'] = best['stderr_tail']
        print(f"{name}: {results[name]['seconds']:.2f}s  "
              f"{(results[name]['peak_rss_bytes'] or 0) / 1024**2:.0f} MiB  "
              f"exit {results[name]['exit_code']}", flush=True)

    payload = {
        'schema_version': 1, 'command': 'benchmark',
        'python': sys.version.split()[0], 'samples_included': bool(args.samples),
        'repeats': max(1, args.repeats), 'results': results,
        'measurement': 'each scenario in a fresh process reporting its own RUSAGE_SELF peak; '
                       'seconds is the minimum over repeats and peak_rss_bytes the maximum',
        'does_not_establish': 'a like-for-like comparison across machines, or that a wall-time '
                              'change is a code change rather than machine load',
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + '\n')

    failing = [name for name, r in results.items() if r['exit_code'] not in (0, 2)]
    if failing:
        print(json.dumps({'scenarios_that_errored': failing}, indent=2), file=sys.stderr)
        return 3
    if args.baseline:
        baseline = json.loads(args.baseline.read_text()).get('results', {})
        regressions, missing = compare(results, baseline, args.time_tolerance,
                                       args.memory_tolerance)
        print(json.dumps({'regressions': regressions, 'not_in_baseline': missing}, indent=2))
        if regressions:
            return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
