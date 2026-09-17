#!/usr/bin/env python3
"""Release/performance gate: tests, then isolated-process baseline comparison.

Run from the repository with .venv/bin/python scripts/check.py. Use --samples
for original-fixture acceptance and sample benchmarks, or --skip-benchmark for
a tests-only development check. Baseline changes must be reviewed separately.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, default=ROOT / 'reports/bench/baseline.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/benchmark-check.json')
    parser.add_argument('--samples', action='store_true')
    parser.add_argument('--skip-benchmark', action='store_true')
    args = parser.parse_args()
    env = os.environ.copy()
    if args.samples:
        env['VOXELMILL_SAMPLES'] = '1'
    tests = subprocess.run([sys.executable, '-m', 'pytest', '-q'], cwd=ROOT, env=env)
    if tests.returncode or args.skip_benchmark:
        return tests.returncode
    command = [sys.executable, str(ROOT / 'scripts/benchmark.py'),
               '--baseline', str(args.baseline.resolve()), '--output', str(args.output.resolve())]
    if args.samples:
        command.append('--samples')
    return subprocess.run(command, cwd=ROOT, env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
