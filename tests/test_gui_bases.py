"""GUI parity and headless previews for the selectable support bases."""
import time


import pytest
pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules')
from PySide6 import QtWidgets

from voxelmill.config import BASE_TYPES, resolve_settings
from voxelmill.gui.document import Document
from voxelmill.gui.editors import ConfigurationEditor, ENUMS
from voxelmill.gui.window import MainWindow


@pytest.fixture(scope='session')
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait_preview(dialog, app):
    dialog.refresh_preview()
    deadline = time.monotonic() + 20
    while dialog.example is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert dialog.example is not None, dialog.status.text()
    return dialog.example


def test_base_choices_are_shared_by_editor_and_setup(app):
    editor = ConfigurationEditor(Document(), 'support', headless=True)
    assert ENUMS[('support', 'base_type')] == BASE_TYPES
    assert tuple(editor.fields['support', 'base_type'].itemText(i)
                 for i in range(editor.fields['support', 'base_type'].count())) == BASE_TYPES
    window = MainWindow(headless=True)
    assert tuple(window.base_type.itemText(i) for i in range(window.base_type.count())) == BASE_TYPES
    for key in ('base_skate_length_mm', 'base_rotation_deg', 'base_strut_width_mm', 'base_cell_size_mm'):
        assert f'support.{key}' in editor.fields['support', key].toolTip()
        assert editor.fields['support', key].toolTip().split('\n', 1)[1]
    editor.reject()
    window.close()


@pytest.mark.parametrize('base_type', BASE_TYPES)
def test_each_base_has_real_headless_preview(base_type, app):
    settings = resolve_settings()
    settings['support']['base_type'] = base_type
    dialog = ConfigurationEditor(Document(settings), 'support', headless=True)
    dialog.fields['support', 'base_type'].setCurrentText(base_type)
    example = wait_preview(dialog, app)
    assert example['triangles']['supports'].size > 0
    assert 'raft' in example['triangles']
    assert example['triangles']['raft'].size > 0 or base_type == 'none'
    dialog.reject()


def test_new_base_fields_round_trip_through_portable_support_save(app, tmp_path):
    settings = resolve_settings()
    settings['support'].update({
        'base_type': 'skate', 'base_skate_length_mm': 12.0,
        'base_rotation_deg': 23.0, 'base_strut_width_mm': .9,
        'base_cell_size_mm': 6.5,
    })
    dialog = ConfigurationEditor(Document(settings), 'support', headless=True)
    path = tmp_path / 'skate.json'
    assert dialog.save_file(str(path)) is not None
    other = ConfigurationEditor(Document(), 'support', headless=True)
    other.load_file(str(path))
    assert other.settings()['support'] == settings['support']
    dialog.reject()
    other.reject()
