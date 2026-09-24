"""Shared test setup.

Qt runs on the offscreen platform unless a test starts its own child process
with another one (see CONTRIBUTING.md for the xvfb/xcb case). pytest imports
this before any test module, so the platform is set before PySide6 loads.
"""
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.fixture(scope='session')
def application():
    """The one QApplication for the session; skips when PySide6 is absent."""
    widgets = pytest.importorskip('PySide6.QtWidgets')
    return widgets.QApplication.instance() or widgets.QApplication([])
