"""The keyboard shortcut editor: startup overrides, edit/save, conflicts, reset."""
import pytest

pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules')

from PySide6 import QtGui, QtWidgets

from voxelmill.config import resolve_settings
from voxelmill.gui import appprefs
from voxelmill.gui.window import MainWindow


def small_settings():
    return resolve_settings(overrides={
        'process': {'layer_height_mm': 0.2},
        'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [0.1, 0.1],
                    'build_mm': [100., 80., 165.]},
        'resources': {'workers': 2}})


def portable(text):
    return QtGui.QKeySequence(text, QtGui.QKeySequence.PortableText).toString(
        QtGui.QKeySequence.PortableText)


def test_a_stored_override_is_applied_at_startup(application):
    appprefs.save_preferences({'shortcuts': {'compute_attachments': portable('Ctrl+Alt+R')}})
    window = MainWindow(small_settings(), None, headless=True)
    action = window.actions_map['compute_attachments']
    assert action.shortcut().toString(QtGui.QKeySequence.PortableText) == portable('Ctrl+Alt+R')
    # The built-in default is still remembered for Reset.
    assert window._default_shortcut_text('compute_attachments') == portable('Ctrl+R')
    window.close()


def test_editing_and_accepting_the_dialog_persists_the_override(application):
    window = MainWindow(small_settings(), None, headless=True)
    dialog = window.shortcuts_dialog()
    item, edit = dialog.shortcuts_row_widgets['compute_attachments']
    edit.setKeySequence(QtGui.QKeySequence('Ctrl+Alt+R'))
    edit.editingFinished.emit()
    assert dialog.shortcuts_conflict_label.text() == ''
    assert dialog.shortcuts_pending['compute_attachments'] == portable('Ctrl+Alt+R')
    assert item.text() == portable('Ctrl+Alt+R')
    # Nothing is applied to the live action until OK.
    assert window.actions_map['compute_attachments'].shortcut().toString(
        QtGui.QKeySequence.PortableText) == portable('Ctrl+R')

    dialog.shortcuts_buttons.button(QtWidgets.QDialogButtonBox.Ok).click()

    assert window.actions_map['compute_attachments'].shortcut().toString(
        QtGui.QKeySequence.PortableText) == portable('Ctrl+Alt+R')
    stored = appprefs.load_preferences()['shortcuts']
    assert stored['compute_attachments'] == portable('Ctrl+Alt+R')
    window.close()


def test_canceling_the_dialog_discards_the_edit(application):
    window = MainWindow(small_settings(), None, headless=True)
    dialog = window.shortcuts_dialog()
    _item, edit = dialog.shortcuts_row_widgets['compute_attachments']
    edit.setKeySequence(QtGui.QKeySequence('Ctrl+Alt+R'))
    edit.editingFinished.emit()

    dialog.shortcuts_buttons.button(QtWidgets.QDialogButtonBox.Cancel).click()

    assert window.actions_map['compute_attachments'].shortcut().toString(
        QtGui.QKeySequence.PortableText) == portable('Ctrl+R')
    assert appprefs.load_preferences()['shortcuts'] == {}
    window.close()


def test_a_conflicting_edit_against_another_action_is_refused(application):
    window = MainWindow(small_settings(), None, headless=True)
    dialog = window.shortcuts_dialog()
    _item, edit = dialog.shortcuts_row_widgets['compute_attachments']
    # arrange_objects already owns Ctrl+L.
    edit.setKeySequence(QtGui.QKeySequence('Ctrl+L'))
    edit.editingFinished.emit()

    assert dialog.shortcuts_conflict_label.text() != ''
    assert 'Arrange on plate' in dialog.shortcuts_conflict_label.text()
    assert dialog.shortcuts_pending['compute_attachments'] == portable('Ctrl+R')
    assert edit.keySequence().toString(QtGui.QKeySequence.PortableText) == portable('Ctrl+R')
    window.close()


def test_a_conflicting_edit_against_a_fixed_viewport_key_is_refused(application):
    window = MainWindow(small_settings(), None, headless=True)
    dialog = window.shortcuts_dialog()
    _item, edit = dialog.shortcuts_row_widgets['compute_attachments']
    # Ctrl+1 is the fixed "front" view key and must stay unremappable.
    edit.setKeySequence(QtGui.QKeySequence('Ctrl+1'))
    edit.editingFinished.emit()

    assert dialog.shortcuts_conflict_label.text() != ''
    assert dialog.shortcuts_pending['compute_attachments'] == portable('Ctrl+R')
    window.close()


def test_reset_restores_the_built_in_default(application):
    window = MainWindow(small_settings(), None, headless=True)
    dialog = window.shortcuts_dialog()
    item, edit = dialog.shortcuts_row_widgets['compute_attachments']
    edit.setKeySequence(QtGui.QKeySequence('Ctrl+Alt+R'))
    edit.editingFinished.emit()
    assert dialog.shortcuts_pending['compute_attachments'] == portable('Ctrl+Alt+R')

    row = next(index for index in range(dialog.shortcuts_table.rowCount())
              if dialog.shortcuts_table.item(index, 0).text() == 'Compute attachments')
    dialog.shortcuts_table.cellWidget(row, 3).click()

    assert dialog.shortcuts_pending['compute_attachments'] == portable('Ctrl+R')
    assert item.text() == portable('Ctrl+R')
    assert edit.keySequence().toString(QtGui.QKeySequence.PortableText) == portable('Ctrl+R')
    window.close()


def test_reset_all_restores_every_edited_row(application):
    window = MainWindow(small_settings(), None, headless=True)
    dialog = window.shortcuts_dialog()
    for name, text in (('compute_attachments', 'Ctrl+Alt+R'), ('arrange_objects', 'Ctrl+Alt+L')):
        _item, edit = dialog.shortcuts_row_widgets[name]
        edit.setKeySequence(QtGui.QKeySequence(text))
        edit.editingFinished.emit()
    assert dialog.shortcuts_pending['compute_attachments'] == portable('Ctrl+Alt+R')
    assert dialog.shortcuts_pending['arrange_objects'] == portable('Ctrl+Alt+L')

    dialog.shortcuts_reset_all.click()

    assert dialog.shortcuts_pending['compute_attachments'] == portable('Ctrl+R')
    assert dialog.shortcuts_pending['arrange_objects'] == portable('Ctrl+L')
    window.close()


def test_view_actions_have_no_edit_widget_and_are_marked_fixed(application):
    window = MainWindow(small_settings(), None, headless=True)
    dialog = window.shortcuts_dialog()
    assert 'view_front' not in dialog.shortcuts_row_widgets
    table = dialog.shortcuts_table
    fixed_rows = [row for row in range(table.rowCount())
                 if table.item(row, 0).text() in ('Front', 'View front')]
    assert fixed_rows
    for row in fixed_rows:
        widget = table.cellWidget(row, 2)
        assert widget is None or not hasattr(widget, 'keySequence')
    window.close()


def test_an_unknown_action_name_in_prefs_is_dropped_rather_than_applied(application, tmp_path):
    path = appprefs.preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    import json
    path.write_text(json.dumps({'shortcuts': {
        'does_not_exist': 'Ctrl+Alt+Z',
        'compute_attachments': 'not a real shortcut ///',
        'view_front': 'Ctrl+9',  # a stale entry naming a fixed action
        'arrange_objects': portable('Ctrl+Alt+L'),
    }}))
    loaded = appprefs.load_preferences()
    # "not a real shortcut ///" fails to round-trip through PortableText and
    # is dropped at load time; the other two are structurally valid strings
    # and are only dropped once applied against actions_map.
    assert 'compute_attachments' not in loaded['shortcuts']
    assert loaded['shortcuts']['arrange_objects'] == portable('Ctrl+Alt+L')

    window = MainWindow(small_settings(), None, headless=True)
    assert window.actions_map['arrange_objects'].shortcut().toString(
        QtGui.QKeySequence.PortableText) == portable('Ctrl+Alt+L')
    # Unknown action and the fixed view_front override never got applied and
    # leave the in-memory preferences, but opening a window writes nothing.
    stored = window.editor_preferences['shortcuts']
    assert 'does_not_exist' not in stored
    assert 'view_front' not in stored
    assert 'does_not_exist' in appprefs.load_preferences()['shortcuts']
    assert window.actions_map['view_front'].shortcut().toString(
        QtGui.QKeySequence.PortableText) == portable('Ctrl+1')
    window.close()
