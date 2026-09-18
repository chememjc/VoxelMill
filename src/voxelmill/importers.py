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
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Iterable

import numpy as np

from .contracts import Canceled, CancellationToken, VoxelMillError

ANGULAR_DEFLECTION_DEG = 15.0
REPORT_PREFIX = 'VOXELMILL_STEP_REPORT '
_PATH_NAMES = ('freecad', 'FreeCAD', 'freecadcmd', 'FreeCADCmd')
_APPIMAGE_GLOBS = ('tools/FreeCAD*.AppImage', 'FreeCAD*.AppImage')
_BUNDLE_BINARIES = ('FreeCADCmd', 'FreeCAD', 'freecadcmd', 'freecad')
_WIN_BINARIES = ('FreeCADCmd.exe', 'FreeCAD.exe', 'freecadcmd.exe', 'freecad.exe')
# QFileDialog name filter: macOS treats FreeCAD.app as a file (a bundle).
FREECAD_FILE_FILTER = (
    'FreeCAD (FreeCAD.app FreeCADCmd FreeCAD freecadcmd freecad *.AppImage '
    'FreeCADCmd.exe FreeCAD.exe);;Applications (*.app);;All files (*)'
)


def helper_script() -> Path:
    return Path(__file__).resolve().parent / 'data' / 'step_tessellate.py'


def _repo_root() -> Path:
    # src/voxelmill/importers.py → parents[2] is the repository root.
    return Path(__file__).resolve().parents[2]


def _is_runnable_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.access(path, os.X_OK):
        return True
    return sys.platform == 'win32' and path.suffix.lower() in {'.exe', '.bat', '.cmd', '.com'}


def _binaries_in(folder: Path, extra_names: tuple[str, ...] = ()) -> Iterable[Path]:
    seen: set[str] = set()
    for name in _BUNDLE_BINARIES + _WIN_BINARIES + extra_names:
        if name in seen:
            continue
        seen.add(name)
        yield folder / name


def interpret_freecad_path(path: str | Path | None) -> Path | None:
    """Turn a user path (binary, ``.app`` bundle, or install dir) into an executable.

    ``FreeCAD.app`` is a directory on macOS; the binary lives at
    ``Contents/MacOS/FreeCADCmd`` (preferred, headless) or ``FreeCAD``.
    Windows installers put ``FreeCADCmd.exe`` under ``bin``. A regular
    executable file is returned as-is.
    """
    if path in (None, ''):
        return None
    candidate = Path(path).expanduser()
    if not candidate.exists():
        return None
    if _is_runnable_file(candidate):
        return candidate.resolve()
    if not candidate.is_dir():
        return None
    extra_names: tuple[str, ...] = ()
    if candidate.suffix.lower() == '.app':
        extra_names = (candidate.stem,)
    search = []
    if extra_names:
        search.append(candidate / 'Contents' / 'MacOS')
    search.extend((candidate, candidate / 'bin', candidate / 'Contents' / 'MacOS'))
    seen: set[Path] = set()
    for folder in search:
        if folder in seen or not folder.is_dir():
            continue
        seen.add(folder)
        for binary in _binaries_in(folder, extra_names):
            if _is_runnable_file(binary):
                return binary.resolve()
        if folder.name == 'MacOS':
            for extra in sorted(folder.iterdir()):
                if extra.name.startswith('.') or extra.suffix.lower() == '.dylib':
                    continue
                if _is_runnable_file(extra):
                    return extra.resolve()
    return None


def _appimages_under(root: Path) -> Iterable[Path]:
    for pattern in _APPIMAGE_GLOBS:
        yield from sorted(root.glob(pattern))


def _installed_freecad_candidates() -> Iterable[Path]:
    if sys.platform == 'darwin':
        for root in (Path('/Applications'), Path.home() / 'Applications'):
            if root.is_dir():
                yield from sorted(root.glob('FreeCAD*.app'))
        return
    if sys.platform == 'win32':
        for key in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
            raw = os.environ.get(key)
            if not raw:
                continue
            root = Path(raw)
            if root.is_dir():
                yield from sorted(root.glob('FreeCAD*'))


def find_freecad(preferred: str | Path | None = None) -> Path | None:
    """Return an executable FreeCAD binary, or ``None`` if none is available.

    Never raises. Search order matches :func:`resolve_freecad`, except a set but
    invalid ``VOXELMILL_FREECAD`` / ``FREECAD`` yields ``None`` instead of an
    error (callers that need the CI contract should use ``resolve_freecad``).
    ``preferred`` is typically the editor ``freecad_path`` preference and may
    be a macOS ``.app`` bundle or a Windows install directory.
    """
    for key in ('VOXELMILL_FREECAD', 'FREECAD'):
        raw = os.environ.get(key)
        if raw:
            return interpret_freecad_path(raw)

    found = interpret_freecad_path(preferred)
    if found is not None:
        return found

    for name in _PATH_NAMES:
        found_on_path = shutil.which(name)
        if found_on_path:
            resolved = interpret_freecad_path(found_on_path)
            if resolved is not None:
                return resolved

    for candidate in _installed_freecad_candidates():
        resolved = interpret_freecad_path(candidate)
        if resolved is not None:
            return resolved

    for root in (_repo_root(), Path.cwd()):
        for candidate in _appimages_under(root):
            resolved = interpret_freecad_path(candidate)
            if resolved is not None:
                return resolved

    for candidate in sorted(Path.home().glob('FreeCAD*.AppImage')):
        resolved = interpret_freecad_path(candidate)
        if resolved is not None:
            return resolved
    return None


def resolve_freecad(preferred: str | Path | None = None) -> Path:
    """Return an executable FreeCAD binary, or raise ``VoxelMillError('step_import')``.

    Search order: ``VOXELMILL_FREECAD`` / ``FREECAD`` (if set, an invalid path
    still errors — CI contract), then ``preferred`` (editor prefs path, which
    may be a ``.app`` bundle), then PATH (``freecad`` / ``FreeCAD`` /
    ``freecadcmd`` / ``FreeCADCmd``), then platform install locations
    (``/Applications/FreeCAD*.app``, Windows ``FreeCAD*`` under Program Files),
    then ``tools/FreeCAD*.AppImage`` and ``FreeCAD*.AppImage`` under the repo
    root and cwd, then ``~/FreeCAD*.AppImage``.
    """
    for key in ('VOXELMILL_FREECAD', 'FREECAD'):
        raw = os.environ.get(key)
        if raw:
            found = interpret_freecad_path(raw)
            if found is not None:
                return found
            raise VoxelMillError(
                'step_import',
                f'{key}={raw!r} is not an executable FreeCAD binary',
            )

    found = find_freecad(preferred=preferred)
    if found is not None:
        return found

    searched: list[str] = []
    searched.extend(str(candidate) for candidate in _installed_freecad_candidates())
    for root in (_repo_root(), Path.cwd()):
        searched.extend(str(candidate) for candidate in _appimages_under(root))
    searched.extend(str(candidate) for candidate in sorted(Path.home().glob('FreeCAD*.AppImage')))
    raise VoxelMillError(
        'step_import',
        'FreeCAD not found; set VOXELMILL_FREECAD to an executable, a macOS '
        'FreeCAD.app bundle, or put freecad, FreeCAD, or freecadcmd on PATH',
        {'searched': searched},
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
                       timeout_s: float | None = 600.0,
                       preferred: str | Path | None = None) -> dict[str, Any]:
    """Run ``step_tessellate.py`` under FreeCAD ``-c`` with stdin closed."""
    freecad = resolve_freecad(preferred=preferred)
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
                    cancel: CancellationToken | None = None,
                    preferred: str | Path | None = None) -> dict[str, Any]:
    """Tessellate ``path`` (STEP) to ``output_stl`` using FreeCAD headlessly.

    Linear deflection comes from ``settings['repair']['step_linear_deflection_mm']``
    (default 0.1 mm). Angular deflection is fixed at 15 degrees. When
    ``repair.weld_tolerance_mm`` is positive, near-duplicate vertices are welded
    before the STL is finalized. Returns a report with engine path, FreeCAD
    version when available, deflection, weld tolerance, triangle count and
    axis-aligned bounds in millimeters. ``preferred`` is forwarded to
    :func:`resolve_freecad` (editor ``freecad_path``).
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
        preferred=preferred,
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
