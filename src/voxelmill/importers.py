"""External CAD importers.

STEP arrives through a FreeCAD headless tessellation subprocess today. The
public ``tessellate_step`` entry point is the seam a native reader (OCP/gmsh)
can replace without touching callers. Optional ``repair.weld_tolerance_mm``
welds near-duplicate tessellation vertices before the STL is finalized.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any

import numpy as np

from .contracts import Canceled, CancellationToken, VoxelMillError

# Prefer the pinned 1.1.3 AppImage; fall back to the machine symlink.
FREECAD_CANDIDATES = (
    Path('/home3/freecad/FreeCAD_1.1.3-Linux-x86_64-py311.AppImage'),
    Path('/home3/freecad/FreeCAD.AppImage'),
)
ANGULAR_DEFLECTION_DEG = 15.0
REPORT_PREFIX = 'VOXELMILL_STEP_REPORT '


def helper_script() -> Path:
    return Path(__file__).resolve().parent / 'data' / 'step_tessellate.py'


def resolve_freecad() -> Path:
    """Return an executable FreeCAD AppImage, or raise ``VoxelMillError('step_import')``."""
    for key in ('VOXELMILL_FREECAD', 'FREECAD'):
        raw = os.environ.get(key)
        if raw:
            path = Path(raw).expanduser()
            if path.is_file() and os.access(path, os.X_OK):
                return path.resolve()
            raise VoxelMillError('step_import',
                            f'{key}={raw!r} is not an executable FreeCAD AppImage')
    for candidate in FREECAD_CANDIDATES:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise VoxelMillError(
        'step_import',
        'FreeCAD AppImage not found; install 1.1.3 at '
        f'{FREECAD_CANDIDATES[0]} or set VOXELMILL_FREECAD',
        {'candidates': [str(p) for p in FREECAD_CANDIDATES]},
    )


def _pack_args(argv: list[str]) -> str:
    return '\x1f'.join(argv) + '\x1f'


def _parse_reports(stdout: str) -> list[dict[str, Any]]:
    reports = []
    for line in stdout.splitlines():
        text = line.strip()
        if not text.startswith(REPORT_PREFIX):
            continue
        payload = text[len(REPORT_PREFIX):]
        try:
            reports.append(json.loads(payload))
        except json.JSONDecodeError as exc:
            raise VoxelMillError('step_import', 'FreeCAD report was not valid JSON',
                            {'line': text, 'error': str(exc)}) from exc
    return reports


def run_freecad_helper(argv: list[str], *, cancel: CancellationToken | None = None,
                       timeout_s: float | None = 600.0) -> dict[str, Any]:
    """Run ``step_tessellate.py`` under FreeCAD ``-c`` with stdin closed."""
    freecad = resolve_freecad()
    script = helper_script()
    if not script.is_file():
        raise VoxelMillError('step_import', f'FreeCAD helper script missing: {script}')
    if cancel is not None:
        cancel.check()

    env = os.environ.copy()
    env['FC_SCRIPT_ARGS'] = _pack_args(argv)
    # AppImages inherit a display and can try to spin a GUI on some hosts.
    env.setdefault('QT_QPA_PLATFORM', 'offscreen')

    try:
        proc = subprocess.Popen(
            [str(freecad), '-c', str(script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
    except OSError as exc:
        raise VoxelMillError('step_import', f'failed to launch FreeCAD: {exc}',
                        {'engine': str(freecad)}) from exc

    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    buckets = {'out': [], 'err': []}

    def _pump(stream, key):
        try:
            buckets[key].append(stream.read())
        finally:
            stream.close()

    assert proc.stdout is not None and proc.stderr is not None
    readers = (
        threading.Thread(target=_pump, args=(proc.stdout, 'out'), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, 'err'), daemon=True),
    )
    for thread in readers:
        thread.start()
    try:
        while proc.poll() is None:
            if cancel is not None:
                try:
                    cancel.check()
                except Canceled:
                    proc.kill()
                    proc.wait()
                    raise
            if deadline is not None and time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                raise VoxelMillError('step_import', 'FreeCAD tessellation timed out',
                                {'engine': str(freecad), 'timeout_s': timeout_s})
            time.sleep(0.05)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        for thread in readers:
            thread.join(timeout=5.0)

    stdout = ''.join(buckets['out'])
    stderr = ''.join(buckets['err'])
    reports = _parse_reports(stdout)
    if proc.returncode != 0 or not reports:
        detail = (stderr or stdout).strip()
        raise VoxelMillError(
            'step_import',
            f'FreeCAD STEP helper failed (exit {proc.returncode})',
            {
                'engine': str(freecad),
                'returncode': proc.returncode,
                'stderr_tail': detail[-4000:],
                'argv': argv,
            },
        )
    # FreeCAD -c can execute the script twice; the last report wins.
    report = dict(reports[-1])
    report['engine'] = str(freecad)
    return report


def write_box_step(output_step: str | Path, size_mm=(10.0, 20.0, 30.0),
                   *, cancel: CancellationToken | None = None) -> dict[str, Any]:
    """Write a rectangular solid STEP via FreeCAD (used by tests and fixtures)."""
    out = Path(output_step).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    sx, sy, sz = (float(v) for v in size_mm)
    return run_freecad_helper(
        ['box', str(out), '0.1', '15', '--box-size', str(sx), str(sy), str(sz)],
        cancel=cancel,
    )


def _repair_weld_tolerance_mm(repair: dict) -> float:
    """Return validated ``repair.weld_tolerance_mm`` (0 = exact / skip rewrite)."""
    try:
        tol = float(repair.get('weld_tolerance_mm', 0.0) or 0.0)
    except (TypeError, ValueError) as exc:
        raise VoxelMillError('step_import',
                        'repair.weld_tolerance_mm must be a finite number') from exc
    if not math.isfinite(tol) or tol < 0:
        raise VoxelMillError('step_import',
                        'repair.weld_tolerance_mm must be a finite non-negative number')
    if tol > 0.05:
        raise VoxelMillError('step_import',
                        'repair.weld_tolerance_mm must be <= 0.05')
    return tol


def apply_step_weld(stl_path: str | Path, weld_tolerance_mm: float,
                    cancel: CancellationToken | None = None) -> dict[str, Any]:
    """Rewrite ``stl_path`` with tolerant welding when ``weld_tolerance_mm`` > 0.

    Tolerance 0 leaves the file unchanged and reports the on-disk triangle count.
    """
    from .mesh import open_stl, weld_mesh, write_stl

    path = Path(stl_path)
    tol = float(weld_tolerance_mm)
    with open_stl(path, cancel=cancel) as mesh:
        triangles = np.asarray(mesh.triangles, dtype=np.float64)
        count = int(mesh.asset.triangle_count)
    if tol == 0:
        return {'triangle_count': count, 'weld_tolerance_mm': 0.0, 'rewrote': False}
    vertices, faces = weld_mesh(triangles, cancel=cancel, weld_tolerance_mm=tol)
    write_stl(path, vertices[faces], cancel=cancel)
    return {
        'triangle_count': int(len(faces)),
        'unique_vertices': int(len(vertices)),
        'weld_tolerance_mm': tol,
        'rewrote': True,
    }


def tessellate_step(path: str | Path, settings: dict, output_stl: str | Path,
                    cancel: CancellationToken | None = None) -> dict[str, Any]:
    """Tessellate ``path`` (STEP) to ``output_stl`` using FreeCAD headlessly.

    Linear deflection comes from ``settings['repair']['step_linear_deflection_mm']``
    (default 0.1 mm). Angular deflection is fixed at 15 degrees. When
    ``repair.weld_tolerance_mm`` is positive, near-duplicate vertices are welded
    before the STL is finalized. Returns a report with engine path, FreeCAD
    version when available, deflection, weld tolerance, triangle count and
    axis-aligned bounds in millimeters.
    """
    source = Path(path)
    if not source.is_file():
        raise VoxelMillError('step_import', f'STEP file not found: {source}')
    suffix = source.suffix.lower()
    if suffix not in ('.step', '.stp'):
        raise VoxelMillError('step_import',
                        f'STEP import expects a .step/.stp file, got {source.suffix!r}')

    repair = settings.get('repair') or {}
    try:
        linear = float(repair['step_linear_deflection_mm'])
    except (KeyError, TypeError, ValueError) as exc:
        raise VoxelMillError('step_import',
                        'settings.repair.step_linear_deflection_mm is required and must be a positive number') from exc
    if not (linear > 0):
        raise VoxelMillError('step_import',
                        'repair.step_linear_deflection_mm must be positive')
    weld_tol = _repair_weld_tolerance_mm(repair)

    destination = Path(output_stl).resolve()
    if destination.resolve() == source.resolve():
        raise VoxelMillError('step_import', 'STEP input and STL output must be different paths')
    destination.parent.mkdir(parents=True, exist_ok=True)

    raw = run_freecad_helper(
        [str(source.resolve()), str(destination), str(linear), str(ANGULAR_DEFLECTION_DEG)],
        cancel=cancel,
    )
    if raw.get('mode') != 'tessellate':
        raise VoxelMillError('step_import', 'FreeCAD helper returned an unexpected report',
                        {'report': raw})
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise VoxelMillError('step_import', f'STL was not written: {destination}')

    triangle_count = int(raw['triangle_count'])
    if weld_tol > 0:
        triangle_count = int(apply_step_weld(destination, weld_tol, cancel=cancel)['triangle_count'])

    return {
        'input': str(source.resolve()),
        'output': str(destination),
        'engine': raw['engine'],
        'freecad_version': raw.get('freecad_version'),
        'linear_deflection_mm': float(raw.get('linear_deflection_mm', linear)),
        'angular_deflection_deg': float(raw.get('angular_deflection_deg', ANGULAR_DEFLECTION_DEG)),
        'weld_tolerance_mm': weld_tol,
        'triangle_count': triangle_count,
        'bounds': raw['bounds'],
        'source_format': 'step',
    }
