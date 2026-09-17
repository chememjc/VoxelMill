"""AppDir staging is relocatable and does not consult the host virtualenv."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / 'scripts' / 'build_appimage.py'


@pytest.fixture
def appdir(tmp_path):
    dest = tmp_path / 'VoxelMill.AppDir'
    subprocess.run(
        [sys.executable, str(BUILDER), '--appdir', str(dest), '--cli-only', '--stage-only'],
        check=True, cwd=str(ROOT))
    return dest


def test_appdir_has_desktop_icon_and_apprun(appdir):
    assert (appdir / 'AppRun').is_file()
    desktop = (appdir / 'voxelmill.desktop').read_text()
    # The display name is capitalised; Exec/Icon/StartupWMClass must stay the
    # lowercase command and icon basenames or launching and icon lookup break.
    assert 'Name=VoxelMill' in desktop
    assert 'Exec=voxelmill %F' in desktop
    assert 'Icon=voxelmill' in desktop
    assert 'StartupWMClass=voxelmill' in desktop
    assert (appdir / 'voxelmill.png').stat().st_size > 100
    assert (appdir / 'usr' / 'share' / 'icons' / 'hicolor' / 'scalable' / 'apps' / 'voxelmill.svg').is_file()
    icon_dir = appdir / 'usr' / 'share' / 'icons' / 'hicolor'
    assert (icon_dir / 'scalable' / 'apps' / 'voxelmill-light.svg').is_file()
    assert (icon_dir / 'scalable' / 'apps' / 'voxelmill-symbolic.svg').is_file()
    for name in ('voxelmill.svg', 'voxelmill-light.svg', 'voxelmill-symbolic.svg'):
        assert (icon_dir / 'scalable' / 'apps' / name).read_bytes() == (ROOT / 'icons' / name).read_bytes()
    for size in ('16x16', '22x22', '24x24', '32x32', '48x48', '64x64', '128x128', '256x256', '512x512', '1024x1024'):
        assert (icon_dir / size / 'apps' / 'voxelmill.png').is_file()
        assert (icon_dir / size / 'apps' / 'voxelmill-light.png').is_file()
        assert (icon_dir / size / 'apps' / 'voxelmill-symbolic.png').is_file()
    assert (appdir / 'usr' / 'bin' / 'python3.10').is_file()
    assert (appdir / 'usr' / 'lib' / 'python3.10' / 'site-packages' / 'voxelmill' / 'cli.py').is_file()
    native = list((appdir / 'usr' / 'lib' / 'python3.10' / 'site-packages' / 'voxelmill').glob('_native*.so'))
    assert native, 'native extension missing from AppDir'


def test_appdir_voxelmill_help_does_not_use_the_venv(appdir, monkeypatch):
    monkeypatch.delenv('PYTHONPATH', raising=False)
    monkeypatch.delenv('VIRTUAL_ENV', raising=False)
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('VIRTUAL_ENV', None)
    env['PATH'] = '/usr/bin:/bin'
    result = subprocess.run(
        [str(appdir / 'AppRun'), '--help'],
        capture_output=True, text=True, env=env, cwd='/tmp')
    assert result.returncode == 0, result.stderr
    assert 'prepare' in result.stdout
    assert 'slice' in result.stdout


def test_apprun_opens_gui_only_when_pyside_is_bundled():
    text = (ROOT / 'packaging' / 'appimage' / 'AppRun').read_text()
    assert 'set -- gui' in text
    assert 'PySide6' in text
    desktop = (ROOT / 'packaging' / 'appimage' / 'voxelmill.desktop').read_text()
    assert 'Exec=voxelmill %F' in desktop
    assert 'Terminal=false' in desktop
