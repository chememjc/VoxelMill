"""AppDir staging is relocatable and does not consult the host virtualenv."""
import os
from pathlib import Path
import importlib.util
import subprocess
import sys
import sysconfig

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / 'scripts' / 'build_appimage.py'


def load_builder():
    spec = importlib.util.spec_from_file_location('build_appimage', BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def appdir(tmp_path):
    if sys.version_info[:2] != (3, 10):
        pytest.skip('the AppImage bundles CPython 3.10 and is built from it')
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


def test_appdir_does_not_redirect_to_the_source_tree(appdir):
    site = appdir / 'usr' / 'lib' / 'python3.10' / 'site-packages'
    assert not (site / '_voxelmill_editable.pth').exists()
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('VIRTUAL_ENV', None)
    env['PYTHONHOME'] = str(appdir / 'usr')
    env['PYTHONNOUSERSITE'] = '1'
    env['PYTHONPATH'] = str(site)
    env['LD_LIBRARY_PATH'] = str(appdir / 'usr' / 'lib')
    env['PATH'] = '/usr/bin:/bin'
    result = subprocess.run(
        [str(appdir / 'usr' / 'bin' / 'python3.10'), '-c',
         'import voxelmill; print(voxelmill.__file__)'],
        capture_output=True, text=True, env=env, cwd='/tmp')
    assert result.returncode == 0, result.stderr
    origin = result.stdout.strip()
    assert str(site / 'voxelmill') in origin, origin


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


def test_pyinstaller_mac_bundle_is_not_background_only():
    text = (ROOT / 'packaging' / 'pyinstaller.spec').read_text()
    # console=True makes PyInstaller set LSBackgroundOnly; Finder then starts
    # a process and never shows a window.
    assert "console=sys.platform != 'darwin'" in text
    assert "'LSBackgroundOnly': False" in text
    assert 'NSPrincipalClass' in text
    # Finder/Dock use the bundle icns, not Qt's window icon. icon=None was
    # the Python rocket.
    assert 'voxelmill.icns' in text
    assert "'CFBundleIconFile': 'voxelmill.icns'" in text
    assert (ROOT / 'packaging' / 'voxelmill.icns').is_file()
    assert (ROOT / 'packaging' / 'voxelmill.icns').read_bytes()[:4] == b'icns'
    assert (ROOT / 'packaging' / 'voxelmill.ico').is_file()


def test_site_copy_keeps_numpy_core_tests():
    mod = load_builder()
    assert 'tests' not in mod._ignore_site('/opt/numpy/_core', ['tests', '__init__.py', '__pycache__'])
    assert '__pycache__' in mod._ignore_site('/opt/numpy/_core', ['tests', '__pycache__'])
    assert 'tests' in mod._ignore_site('/opt/numpy', ['tests', 'linalg'])
    assert 'tests' in mod._ignore_site('/opt/scipy/ndimage', ['tests', '__init__.py'])


def test_explicit_native_extension_replaces_staged_environment_copy(tmp_path):
    mod = load_builder()
    package = tmp_path / 'site-packages' / 'voxelmill'
    package.mkdir(parents=True)
    (package / '_native.old.so').write_bytes(b'cuda environment build')
    suffix = sysconfig.get_config_var('EXT_SUFFIX') or '.so'
    cpu = tmp_path / f'_native{suffix}'
    cpu.write_bytes(b'cpu release build')

    staged = mod._stage_native_override(cpu, package)

    assert staged.read_bytes() == b'cpu release build'
    assert list(package.glob('_native*.so')) == [staged]
    with pytest.raises(SystemExit, match='native extension not found'):
        mod._stage_native_override(tmp_path / f'missing{suffix}', package)
    wrong = tmp_path / f'other{suffix}'
    wrong.write_bytes(b'wrong module')
    with pytest.raises(SystemExit, match='must be named _native'):
        mod._stage_native_override(wrong, package)


def test_relative_appdir_and_native_override_are_resolved_before_staging(tmp_path, monkeypatch):
    mod = load_builder()
    captured = {}
    native = tmp_path / '_native.so'
    native.write_bytes(b'cpu')

    def stage(appdir, *, gui, native_extension):
        captured.update(appdir=appdir, gui=gui, native=native_extension)
        return appdir

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mod, 'stage_appdir', stage)
    assert mod.main(['--appdir', 'relative/AppDir', '--native-extension', '_native.so',
                     '--cli-only', '--stage-only']) == 0
    assert captured == {
        'appdir': (tmp_path / 'relative/AppDir').resolve(),
        'gui': False,
        'native': native.resolve(),
    }


def test_cli_rewrites_empty_argv_to_gui_when_pyside_is_present(tmp_path, monkeypatch):
    from voxelmill.cli import _desktop_argv
    monkeypatch.setattr('voxelmill.cli._gui_is_importable', lambda: True)
    assert _desktop_argv([]) == ['gui']
    assert _desktop_argv(['-psn_0_12345']) == ['gui']
    stl = tmp_path / 'part.stl'
    stl.write_bytes(b'solid x\nendsolid x\n')
    assert _desktop_argv([str(stl)]) == ['gui', str(stl)]
    assert _desktop_argv(['prepare', str(stl)]) == ['prepare', str(stl)]
    assert _desktop_argv(['--version']) == ['--version']
    assert _desktop_argv(['gui']) == ['gui']
    monkeypatch.setattr('voxelmill.cli._gui_is_importable', lambda: False)
    assert _desktop_argv([]) == []
    assert _desktop_argv([str(stl)]) == [str(stl)]
    # Frozen Windows EXE: Explorer empty-argv must open GUI even if the
    # PySide6 probe fails after freeze (sys._MEIPASS / delayed import).
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert _desktop_argv([]) == ['gui']
    assert _desktop_argv([str(stl)]) == ['gui', str(stl)]
    assert _desktop_argv(['prepare', str(stl)]) == ['prepare', str(stl)]
    assert _desktop_argv(['--version']) == ['--version']
    assert _desktop_argv(['gui']) == ['gui']
    monkeypatch.setattr(sys, 'argv', [r'C:\VoxelMill\VoxelMill.exe'])
    assert _desktop_argv(None) == ['gui']


def test_windows_vm_uses_software_opengl_only_in_virtualbox(monkeypatch):
    from voxelmill import cli as cli_mod

    monkeypatch.delenv('QT_OPENGL', raising=False)
    monkeypatch.setattr(cli_mod.sys, 'platform', 'linux')
    assert cli_mod._windows_vm_software_gl() is False

    monkeypatch.setattr(cli_mod.sys, 'platform', 'win32')

    class _Key:
        pass

    def _open(_hive, path):
        if path.endswith('VBoxGuest'):
            return _Key()
        raise OSError('missing')

    fake_winreg = type('winreg', (), {
        'HKEY_LOCAL_MACHINE': object(),
        'OpenKey': staticmethod(_open),
    })
    monkeypatch.setitem(sys.modules, 'winreg', fake_winreg)
    assert cli_mod._windows_vm_software_gl() is True
    monkeypatch.setenv('QT_OPENGL', 'desktop')
    assert cli_mod._windows_vm_software_gl() is False
    monkeypatch.delenv('QT_OPENGL')

    def _missing(_hive, _path):
        raise OSError('missing')

    monkeypatch.setitem(sys.modules, 'winreg',
                        type('winreg', (), {
                            'HKEY_LOCAL_MACHINE': object(),
                            'OpenKey': staticmethod(_missing),
                        }))
    assert cli_mod._windows_vm_software_gl() is False
