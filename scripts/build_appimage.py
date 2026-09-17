#!/usr/bin/env python3
"""Stage a relocatable AppDir and optionally pack it with appimagetool.

The host virtualenv is not relocatable (python3 is a symlink to /usr/bin).
This copies CPython, the stdlib, selected site-packages, and voxelmill's
native extension into AppDir/usr so AppRun never consults the build tree.
"""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / 'packaging' / 'appimage'
VERSION = (ROOT / 'src' / 'voxelmill' / '__init__.py').read_text().split(
    '__version__ = "')[1].split('"')[0]
SKIP_LIBS = {
    'linux-vdso.so.1', 'ld-linux-x86-64.so.2', 'libc.so.6', 'libm.so.6',
    'libpthread.so.0', 'libdl.so.2', 'librt.so.1', 'libresolv.so.2',
}


def _copy_file(src: Path, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _copy_tree(src: Path, dest: Path, *, ignore=None):
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, symlinks=True, ignore=ignore,
                    ignore_dangling_symlinks=True)


def _ignore_python_stdlib(_directory, names):
    drop = {'test', 'tests', 'idlelib', 'turtledemo', 'ensurepip', '__pycache__'}
    return [name for name in names if name in drop or name.endswith('.pyc')]


def _ignore_site(_directory, names):
    drop = {
        'pip', 'pip-22.0.2.dist-info', '__pycache__', 'tests',
        '_distutils_hack', 'distutils-precedence.pth',
        '_voxelmill_editable.pth', '_voxelmill_editable.py',
        f'voxelmill-{VERSION}.dist-info',
        # Editor runtime does not need WebEngine, QML or designer tools.
        'QtWebEngine', 'QtWebEngineCore', 'QtWebEngineWidgets', 'QtWebEngineQuick',
        'Qt6WebEngine', 'Qt6WebEngineCore', 'Qt6WebEngineWidgets',
        'examples', 'qml', 'Designer', 'Linguist',
    }
    return [name for name in names if name in drop or name.endswith('.pyc')]


def _copy_imported(modname: str, site: Path):
    try:
        module = importlib.import_module(modname)
    except ImportError:
        return
    path = Path(getattr(module, '__file__', '') or '')
    if not path:
        return
    if path.name == '__init__.py':
        src = path.parent
        _copy_tree(src, site / src.name, ignore=_ignore_site)
        parent = src.parent
        stem = src.name
    else:
        _copy_file(path, site / path.name)
        parent = path.parent
        stem = path.name.split('.')[0]
    libs = parent / f'{stem}.libs'
    if libs.is_dir():
        _copy_tree(libs, site / libs.name, ignore=_ignore_site)
    for info in parent.glob('*.dist-info'):
        prefix = info.name.split('-')[0].lower()
        if prefix == stem.lower() or info.name.lower().startswith(stem.lower() + '-'):
            _copy_tree(info, site / info.name, ignore=_ignore_site)


def collect_needed_libs(binaries: list[Path], dest_lib: Path):
    seen = set()
    for binary in binaries:
        if not binary.is_file():
            continue
        try:
            result = subprocess.run(['ldd', str(binary)], capture_output=True, text=True, check=False)
        except FileNotFoundError:
            return
        for line in result.stdout.splitlines():
            if '=>' not in line:
                continue
            name, _, rest = line.strip().partition('=>')
            name = name.strip()
            path = rest.strip().split()[0] if rest.strip() else ''
            if name in SKIP_LIBS or path in ('', 'not'):
                continue
            source = Path(path)
            if not source.is_file() or source.name in seen:
                continue
            seen.add(source.name)
            _copy_file(source, dest_lib / source.name)


def stage_licenses(appdir: Path):
    """Stage the notices a binary distribution has to carry.

    Qt reaches users through PySide6 under the LGPL, and CPython, oneTBB and the
    rest arrive with their own terms. Ship the project's own MIT license, the
    inventory that maps every bundled component to its license, and the license
    texts themselves. Upstream dist-info directories, copied alongside each
    package by _copy_imported, carry each project's own notice as well.
    """
    doc = appdir / 'usr' / 'share' / 'doc' / 'voxelmill'
    _copy_file(ROOT / 'LICENSE', doc / 'LICENSE')
    source = ROOT / 'licenses'
    if not source.is_dir():
        raise SystemExit(f'license inventory not found at {source}')
    _copy_tree(source, doc / 'licenses')


def stage_appdir(appdir: Path, *, gui: bool):
    if appdir.exists():
        shutil.rmtree(appdir)
    usr = appdir / 'usr'
    lib = usr / 'lib'
    py = lib / 'python3.10'
    site = py / 'site-packages'
    bindir = usr / 'bin'
    bindir.mkdir(parents=True)
    lib.mkdir(parents=True)

    python = Path(sys.executable).resolve()
    _copy_file(python, bindir / 'python3.10')
    os.chmod(bindir / 'python3.10', os.stat(bindir / 'python3.10').st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    libpython = Path('/usr/lib/x86_64-linux-gnu/libpython3.10.so.1.0')
    if libpython.is_file():
        _copy_file(libpython, lib / libpython.name)

    stdlib = Path('/usr/lib/python3.10')
    if not stdlib.is_dir():
        raise SystemExit(f'CPython stdlib not found at {stdlib}')
    _copy_tree(stdlib, py, ignore=_ignore_python_stdlib)

    # The development venv is --system-site-packages; numpy/scipy/PySide6 live
    # in the user site, VTK in the venv. Copy by import path, not by scanning
    # one directory.
    modules = ['numpy', 'scipy', 'manifold3d', 'threadpoolctl']
    if sys.version_info < (3, 11):
        modules.append('tomli')
    try:
        import websocket  # noqa: F401
        modules.append('websocket')
    except ImportError:
        pass
    if gui:
        modules.extend(['PySide6', 'shiboken6', 'vtkmodules', 'vtk'])
    for name in modules:
        _copy_imported(name, site)

    package = ROOT / 'src' / 'voxelmill'
    _copy_tree(package, site / 'voxelmill',
               ignore=lambda d, names: [n for n in names if n == '__pycache__' or n.endswith('.pyc')])
    import voxelmill as voxelmill_mod
    native_dir = Path(voxelmill_mod.__file__).resolve().parent
    venv_native = Path(sys.prefix) / 'lib' / 'python3.10' / 'site-packages' / 'voxelmill'
    for folder in (native_dir, venv_native):
        if folder.is_dir():
            for so in folder.glob('_native*.so'):
                _copy_file(so, site / 'voxelmill' / so.name)

    stage_licenses(appdir)

    _copy_file(PACKAGING / 'AppRun', appdir / 'AppRun')
    os.chmod(appdir / 'AppRun', 0o755)
    _copy_file(PACKAGING / 'voxelmill.desktop', appdir / 'voxelmill.desktop')
    _copy_file(PACKAGING / 'voxelmill.png', appdir / 'voxelmill.png')
    # AppImage discovers icons from the desktop file's basename.  Keep the
    # complete hicolor set in the AppDir as well so desktop environments can
    # choose an appropriate size without scaling the 256px fallback.
    icon_root = appdir / 'usr' / 'share' / 'icons'
    source_icons = PACKAGING / 'hicolor'
    if source_icons.is_dir():
        _copy_tree(source_icons, icon_root / 'hicolor')

    binaries = [bindir / 'python3.10']
    binaries.extend(site.rglob('*.so'))
    plugins = site / 'PySide6' / 'Qt' / 'plugins'
    if plugins.is_dir():
        binaries.extend(plugins.rglob('*.so'))
    collect_needed_libs(binaries, lib)
    return appdir


def pack_appdir(appdir: Path, output: Path, tool: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault('ARCH', 'x86_64')
    env.setdefault('VERSION', VERSION)
    subprocess.run([str(tool), '--no-appstream', str(appdir), str(output)],
                   check=True, env=env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--appdir', default=str(ROOT / 'output' / 'appimage' / 'VoxelMill.AppDir'))
    parser.add_argument('--output', default=str(ROOT / 'output' / 'appimage' / 'VoxelMill-x86_64.AppImage'))
    parser.add_argument('--tool', default=str(PACKAGING / 'appimagetool'))
    parser.add_argument('--cli-only', action='store_true',
                        help='omit PySide6 and VTK (smaller, no editor)')
    parser.add_argument('--stage-only', action='store_true',
                        help='write the AppDir and stop')
    args = parser.parse_args(argv)
    gui = not args.cli_only
    appdir = stage_appdir(Path(args.appdir), gui=gui)
    print(f'staged {appdir}')
    if args.stage_only:
        return 0
    tool = Path(args.tool)
    if not tool.is_file():
        raise SystemExit(f'appimagetool not found at {tool}; download it or pass --tool')
    pack_appdir(appdir, Path(args.output), tool)
    print(f'wrote {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
