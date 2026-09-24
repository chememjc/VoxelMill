import pytest


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


def test_hover_delay_is_a_stored_preference_applied_to_the_live_style(tmp_path, monkeypatch):
    """Hover text is the editor's field documentation, so its delay is tunable.

    Qt reads the wake-up delay from the style, never from the widget, so the
    editor installs a proxy style. The dialog has to retune that live style,
    not just write the file: a delay you cannot feel is one you cannot choose.
    """
    from PySide6 import QtWidgets
    from voxelmill.gui import appprefs
    from voxelmill.gui.appprefs import (DEFAULT_TOOLTIP_DELAY_MS, HoverDelayStyle,
                                        MAX_TOOLTIP_DELAY_MS, install_hover_delay,
                                        load_preferences)

    monkeypatch.setattr(appprefs, 'preferences_path', lambda: tmp_path / 'editor.json')
    assert load_preferences()['tooltip_delay_ms'] == DEFAULT_TOOLTIP_DELAY_MS

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    style = install_hover_delay(application, 1000)
    assert isinstance(style, HoverDelayStyle)
    assert style.styleHint(QtWidgets.QStyle.SH_ToolTip_WakeUpDelay) == 1000
    # Installing again must retune, never nest a second proxy over the first.
    again = install_hover_delay(application, 2500)
    assert again is style and again.delay() == 2500
    assert not isinstance(style.baseStyle(), HoverDelayStyle)
    # A typo must not make hover text unreachable, or negative.
    assert install_hover_delay(application, 10 ** 9).delay() == MAX_TOOLTIP_DELAY_MS
    assert install_hover_delay(application, -5).delay() == 0
    install_hover_delay(application, DEFAULT_TOOLTIP_DELAY_MS)


def test_hover_delay_survives_a_corrupt_or_out_of_range_file(tmp_path, monkeypatch):
    import json
    from voxelmill.gui import appprefs
    from voxelmill.gui.appprefs import DEFAULT_TOOLTIP_DELAY_MS, load_preferences

    path = tmp_path / 'editor.json'
    monkeypatch.setattr(appprefs, 'preferences_path', lambda: path)
    for stored in ('nonsense', -1, 10 ** 9, None):
        path.write_text(json.dumps({'tooltip_delay_ms': stored}))
        assert load_preferences()['tooltip_delay_ms'] == DEFAULT_TOOLTIP_DELAY_MS, stored
    path.write_text(json.dumps({'tooltip_delay_ms': 250}))
    assert load_preferences()['tooltip_delay_ms'] == 250
