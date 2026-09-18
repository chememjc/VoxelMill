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
    'vtkmodules.all',
    'vtkmodules.qt.QVTKRenderWindowInteractor',
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
_package_data = SRC / 'voxelmill' / 'data'
if _package_data.is_dir():
    datas.append(Tree(str(_package_data), prefix='voxelmill/data'))

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
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
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
            'CFBundleShortVersionString': '0.3.0',
            'NSHighResolutionCapable': True,
        },
    )
