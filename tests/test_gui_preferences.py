import os
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6')

from voxelmill.config import resolve_settings
from voxelmill.gui.document import Document
from voxelmill.gui.preferences import PreferencesDialog
from PySide6 import QtWidgets


@pytest.fixture(scope='session')
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_preferences_apply_to_the_document_and_are_undoable(app):
    document = Document(resolve_settings())
    dialog = PreferencesDialog(document, headless=True)
    dialog.acceleration.setCurrentText('cpu')
    dialog.workers.setValue(3)
    assert dialog.apply()['resources']['acceleration'] == 'cpu'
    assert document.settings['resources']['workers'] == 3
    document.undo()
    assert document.settings['resources']['workers'] == 2


def test_applying_preferences_keeps_the_layout_and_motion_mode(app, tmp_path, monkeypatch):
    """Apply rebuilt the file from two fields, which erased everything else.

    The same file holds the motion mode and the saved window layout. Writing
    only the snap angle and the nudge step reverted both to their defaults
    every time someone touched Apply.
    """
    from voxelmill.gui import appprefs
    monkeypatch.setattr(appprefs, 'preferences_path', lambda: tmp_path / 'editor.json')
    appprefs.save_preferences({'snap_angle_deg': 15.0, 'motion_mode': 'absolute',
                               'translate_step_mm': 2.5, 'window_geometry': 'AAAA',
                               'window_state': 'BBBB'})
    document = Document(resolve_settings())
    dialog = PreferencesDialog(document, headless=True)
    assert dialog.apply() is not None
    stored = appprefs.load_preferences()
    assert stored['motion_mode'] == 'absolute'
    assert stored['window_geometry'] == 'AAAA'
    assert stored['window_state'] == 'BBBB'
    assert stored['translate_step_mm'] == 2.5
    assert stored['snap_angle_deg'] == 15.0


def test_the_island_pass_cap_reaches_the_settings_table(app):
    document = Document(resolve_settings())
    dialog = PreferencesDialog(document, headless=True)
    dialog.island_passes.setValue(3)
    assert dialog.settings()['support']['max_island_passes'] == 3
