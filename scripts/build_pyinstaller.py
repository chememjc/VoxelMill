#!/usr/bin/env python3
"""Build a portable onedir (and macOS .app) with PyInstaller.

Uses sys.executable so the active venv's PyInstaller runs. Does not invoke
Homebrew. Output lands under output/portable/.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / 'packaging' / 'pyinstaller.spec'
DIST = ROOT / 'output' / 'portable'
WORK = ROOT / 'build' / 'pyinstaller'


def main(argv=None) -> int:
    del argv  # reserved for future flags; keep CLI stable
    if not SPEC.is_file():
        raise SystemExit(f'spec not found: {SPEC}')
    DIST.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        str(SPEC),
        '--noconfirm',
        '--distpath', str(DIST),
        '--workpath', str(WORK),
    ]
    subprocess.run(cmd, check=True, cwd=str(ROOT))
    print(f'wrote under {DIST}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
