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
    assert document.settings['resources']['workers'] == 0


def test_worker_spin_box_admits_the_derive_sentinel(app):
    """Range (1, 32) clamped the 0 default up to 1, so Apply silently wrote
    single-worker mode into the document -- the slowest possible setting."""
    from voxelmill.topology import default_workers
    document = Document(resolve_settings())
    dialog = PreferencesDialog(document, headless=True)
    assert dialog.workers.minimum() == 0
    assert dialog.workers.value() == 0
    assert str(default_workers()) in dialog.workers.specialValueText()
    assert dialog.apply()['resources']['workers'] == 0


def test_applying_preferences_keeps_the_layout_and_motion_mode(app, tmp_path, monkeypatch):
    """Apply rebuilt the file from two fields, which erased everything else.

    The same file holds the motion mode, FreeCAD path, and the saved window
    layout. Writing only the snap angle and the nudge step reverted the rest
    to their defaults every time someone touched Apply.
    """
    from voxelmill.gui import appprefs
    monkeypatch.setattr(appprefs, 'preferences_path', lambda: tmp_path / 'editor.json')
    appprefs.save_preferences({'snap_angle_deg': 15.0, 'motion_mode': 'absolute',
                               'translate_step_mm': 2.5, 'window_geometry': 'AAAA',
                               'window_state': 'BBBB',
                               'freecad_path': '/opt/FreeCAD.AppImage'})
    document = Document(resolve_settings())
    dialog = PreferencesDialog(document, headless=True)
    assert dialog.freecad_path.text() == '/opt/FreeCAD.AppImage'
    assert dialog.apply() is not None
    stored = appprefs.load_preferences()
    assert stored['motion_mode'] == 'absolute'
    assert stored['window_geometry'] == 'AAAA'
    assert stored['window_state'] == 'BBBB'
    assert stored['translate_step_mm'] == 2.5
    assert stored['snap_angle_deg'] == 15.0
    assert stored['freecad_path'] == '/opt/FreeCAD.AppImage'


def test_freecad_path_save_and_load(tmp_path, monkeypatch):
    from voxelmill.gui import appprefs
    monkeypatch.setattr(appprefs, 'preferences_path', lambda: tmp_path / 'editor.json')
    assert appprefs.load_preferences()['freecad_path'] == ''
    appprefs.save_preferences({'freecad_path': '/usr/bin/freecad'})
    assert appprefs.load_preferences()['freecad_path'] == '/usr/bin/freecad'
    appprefs.save_preferences({'freecad_path': ''})
    assert appprefs.load_preferences()['freecad_path'] == ''
    path = appprefs.preferences_path()
    path.write_text('{"freecad_path": 12}\n')
    assert appprefs.load_preferences()['freecad_path'] == ''


def test_the_island_pass_cap_reaches_the_settings_table(app):
    document = Document(resolve_settings())
    dialog = PreferencesDialog(document, headless=True)
    dialog.island_passes.setValue(3)
    assert dialog.settings()['support']['max_island_passes'] == 3
