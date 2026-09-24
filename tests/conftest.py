"""Shared test setup.

Qt runs on the offscreen platform unless a test starts its own child process
with another one (see CONTRIBUTING.md for the xvfb/xcb case). pytest imports
this before any test module, so the platform is set before PySide6 loads.
"""
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.fixture(autouse=True)
def _private_user_dirs(tmp_path_factory, monkeypatch):
    """Keep the developer's own editor preferences, autosave and profiles out of tests.

    A real ``~/.config/voxelmill/editor.json`` with ``motion_mode: absolute``
    changed how the pose panels commit and failed four GUI tests, and windows
    closing in tests wrote their geometry back into it.
    """
    root = tmp_path_factory.mktemp('user')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(root / 'config'))
    monkeypatch.setenv('XDG_CACHE_HOME', str(root / 'cache'))
    monkeypatch.setenv('XDG_DATA_HOME', str(root / 'data'))


@pytest.fixture(scope='session')
def application():
    """The one QApplication for the session; skips when PySide6 is absent."""
    widgets = pytest.importorskip('PySide6.QtWidgets')
    return widgets.QApplication.instance() or widgets.QApplication([])
