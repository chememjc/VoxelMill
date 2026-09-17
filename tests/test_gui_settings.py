"""Headless coverage for settings descriptors, tiers, history, theme and docks."""
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules')

from PySide6 import QtWidgets

from voxelmill.config import DEFAULTS
from voxelmill.gui.settings_table import SETTINGS_DESCRIPTORS, build_descriptors
from voxelmill.gui.window import MainWindow
from voxelmill.config import resolve_settings


@pytest.fixture(scope='session')
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def small_settings():
    return resolve_settings(overrides={
        'process': {'layer_height_mm': 0.2},
        'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [0.1, 0.1],
                    'build_mm': [100., 80., 165.]},
        'resources': {'workers': 2}})


def _leaf_paths(obj, prefix=''):
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f'{prefix}.{key}' if prefix else key
            yield from _leaf_paths(value, path)
    else:
        yield prefix


def test_descriptor_table_covers_every_defaults_key():
    paths = {descriptor.path for descriptor in SETTINGS_DESCRIPTORS}
    expected = set(_leaf_paths(DEFAULTS))
    assert paths == expected
    rebuilt = {descriptor.path for descriptor in build_descriptors()}
    assert rebuilt == expected
    motion = [d for d in SETTINGS_DESCRIPTORS if d.path.startswith('printer.motion.')]
    assert motion
    assert all(d.tier == 'expert' and d.risk == 'uncalibrated' for d in motion)
    assert all('--set ' in d.tooltip and d.path in d.tooltip for d in SETTINGS_DESCRIPTORS)


def test_expert_mode_reveals_motion_and_simple_hides_it(application):
    window = MainWindow(small_settings(), None, headless=True)
    assert window.visibility_tier == 'simple'
    assert window.motion_table.isHidden()
    motion_field = window.motion_table._widgets.get('printer.motion.lift_height')
    assert motion_field is not None
    assert window.settings_json.isHidden()
    assert window.orientation_weights.isHidden()

    window._apply_visibility_tier('advanced')
    assert not window.settings_table.isHidden()
    assert window.motion_table.isHidden()
    assert window.settings_json.isHidden()

    window._apply_visibility_tier('expert')
    assert not window.motion_table.isHidden()
    assert not motion_field.isHidden()
    assert not window.settings_json.isHidden()
    assert not window.orientation_weights.isHidden()
    tip = motion_field.toolTip()
    assert 'printer.motion.lift_height' in tip
    assert 'Uncalibrated' in tip
    window.close()


def test_history_menu_lists_undo_label_after_add_contact(application):
    window = MainWindow(small_settings(), None, headless=True)
    window.document.add_contact([1.0, 2.0, 3.0])
    window._refresh_undo()
    window._rebuild_history_menu()
    labels = [action.text() for action in window.history_menu.actions()]
    assert 'add support' in labels
    window.jump_history(1)
    assert window.document.manual_contacts == []
    window.close()


def test_theme_and_shortcuts_dialog(application):
    window = MainWindow(small_settings(), None, headless=True)
    window.set_theme('dark')
    assert window.theme_name == 'dark'
    dialog = window.shortcuts_dialog()
    table = dialog.findChild(QtWidgets.QTableWidget, 'shortcuts_table')
    assert table is not None and table.rowCount() > 0
    texts = [table.item(row, 1).text() for row in range(table.rowCount())]
    assert any(text for text in texts)
    window.set_theme('system')
    window.close()


def test_notifications_support_category_suppression(application, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    from voxelmill.gui.notifications import NotificationCenter, load_suppressed
    center = NotificationCenter()
    seen = []
    center.notified.connect(seen.append)
    center.notify('first', category='islands', level='warning')
    assert len(seen) == 1
    center.suppress('islands')
    center.notify('second', category='islands', level='warning')
    assert len(seen) == 1
    assert 'islands' in load_suppressed()


def test_docks_exist_and_can_be_hidden_shown(application):
    window = MainWindow(small_settings(), None, headless=True)
    assert set(window.panel_docks) == {'setup', 'layers', 'faults', 'report'}
    assert window.tabs.tabText(window.faults_tab_index) == 'Faults'
    window.layers_dock.hide()
    assert window.layers_dock.isHidden()
    window.layers_dock.show()
    assert not window.layers_dock.isHidden()
    window.tabs.setCurrentIndex(window.layers_tab_index)
    assert window.tabs.currentIndex() == window.layers_tab_index
    window.close()
