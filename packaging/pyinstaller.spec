# -*- mode: python ; coding: utf-8 -*-
"""VoxelMill onedir (Windows) / .app (macOS) via PyInstaller.

Linux ships as an AppImage (scripts/build_appimage.py); do not use this
spec on Linux for release artifacts. PyInstaller injects Analysis, EXE,
PYZ, COLLECT, BUNDLE, Tree, and SPECPATH when executing this file.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / 'src'
ENTRY = str(SRC / 'voxelmill' / '__main__.py')

datas = []
binaries = []
hiddenimports = [
    'voxelmill',
    'voxelmill.cli',
    'voxelmill.gui',
    'PySide6.QtOpenGLWidgets',
    'vtkmodules.all',
    'vtkmodules.qt.QVTKRenderWindowInteractor',
    'vtkmodules.vtkRenderingOpenGL2',
    'vtkmodules.util.numpy_support',
    'vtkmodules.vtkRenderingOpenGL2',
    'vtkmodules.vtkInteractionStyle',
    'vtkmodules.vtkRenderingFreeType',
    'vtkmodules.vtkRenderingCore',
    'vtkmodules.vtkCommonCore',
    'vtkmodules.vtkCommonDataModel',
    'vtkmodules.vtkCommonExecutionModel',
    'vtkmodules.vtkCommonMath',
    'vtkmodules.vtkCommonTransforms',
    'vtkmodules.vtkFiltersCore',
    'vtkmodules.vtkFiltersGeneral',
    'vtkmodules.vtkFiltersSources',
    'vtkmodules.vtkFiltersGeometry',
    'vtkmodules.vtkFiltersExtraction',
    'vtkmodules.vtkImagingCore',
    'vtkmodules.vtkIOImage',
    'vtkmodules.vtkIOGeometry',
    'vtkmodules.vtkIOLegacy',
    'manifold3d',
    'threadpoolctl',
    'websocket',
]
if sys.version_info < (3, 11):
    hiddenimports.append('tomli')

excludes = [
    'PySide6.QtWebEngine',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebEngineQuick',
    'PySide6.Qt3DAnimation',
    'PySide6.Qt3DCore',
    'PySide6.Qt3DExtras',
    'PySide6.Qt3DInput',
    'PySide6.Qt3DLogic',
    'PySide6.Qt3DRender',
    'PySide6.QtDesigner',
    'PySide6.QtQuick',
    'PySide6.QtQuickWidgets',
    'PySide6.QtQml',
    'tkinter',
    'matplotlib',
]


def _collect(package: str) -> None:
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception:
        return
    datas.extend(pkg_datas)
    binaries.extend(pkg_binaries)
    hiddenimports.extend(pkg_hidden)


for name in (
    'PySide6',
    'shiboken6',
    'vtkmodules',
    'vtk',
    'numpy',
    'scipy',
    'manifold3d',
    'threadpoolctl',
    'websocket',
    'voxelmill',
):
    _collect(name)
if sys.version_info < (3, 11):
    _collect('tomli')

try:
    hiddenimports.extend(collect_submodules('vtkmodules'))
except Exception:
    pass

# Editable / scikit-build installs may leave _native only in site-packages.
_native = importlib.util.find_spec('voxelmill._native')
if _native is not None and _native.origin and Path(_native.origin).is_file():
    binaries.append((_native.origin, 'voxelmill'))

# Package data (profiles, icons, STEP helper) when collect_all missed the tree.
# Analysis.datas must be (src, dest) pairs — a Tree() object unpacks as many
# 3-tuples and raises ValueError: too many values to unpack (expected 2).
_package_data = SRC / 'voxelmill' / 'data'
if _package_data.is_dir():
    datas.append((str(_package_data), 'voxelmill/data'))


def _pairs(entries):
    """Keep only (src, dest) pairs; drop Tree/TOC 3-tuples if they leaked in."""
    out = []
    for item in entries:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            out.append((str(item[0]), str(item[1])))
    return out


datas = _pairs(datas)
binaries = _pairs(binaries)

_VERSION = '0.0.0'
for _line in (SRC / 'voxelmill' / '__init__.py').read_text(encoding='utf-8').splitlines():
    if _line.startswith('__version__'):
        _VERSION = _line.split('"', 2)[1]
        break

a = Analysis(
    [ENTRY],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# Windows needs a console so `VoxelMill.exe prepare` prints. macOS .app with
# console=True sets LSBackgroundOnly, so Finder launch never shows a window.
# CLI from Terminal still writes stdout with console=False. argv_emulation
# turns a dropped file into sys.argv so `_desktop_argv` can open the editor.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VoxelMill',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=sys.platform != 'darwin',
    disable_windowed_traceback=False,
    argv_emulation=sys.platform == 'darwin',
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='VoxelMill',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='VoxelMill.app',
        icon=None,
        bundle_identifier='com.voxelmill.VoxelMill',
        info_plist={
            'CFBundleDisplayName': 'VoxelMill',
            'CFBundleShortVersionString': _VERSION,
            'NSHighResolutionCapable': True,
            'LSBackgroundOnly': False,
            'NSPrincipalClass': 'NSApplication',
        },
    )
