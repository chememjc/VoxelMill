"""Wiring for the object panel's new controls, the island-guard attachments
job, and remembered window geometry/layout.

Follows the offscreen-Qt conventions of the other ``test_gui*`` modules: a
headless :class:`MainWindow` never opens a real render window, so these run
under ``QT_QPA_PLATFORM=offscreen`` with no display at all.
"""
from __future__ import annotations

import base64
import json
import os
import sys

import numpy as np
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytestmark = pytest.mark.gui

sys.path.insert(0, os.path.dirname(__file__))

import manifold3d as m  # noqa: E402
from PySide6 import QtCore, QtWidgets  # noqa: E402

from voxelmill.config import resolve_settings  # noqa: E402
from voxelmill.geometry import manifold_triangles  # noqa: E402
from voxelmill.gui.appprefs import load_preferences, preferences_path, save_preferences  # noqa: E402
from voxelmill.gui.window import MainWindow  # noqa: E402
from test_gui import drain, small_settings, write_stl  # noqa: E402


@pytest.fixture(scope='module')
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def cube(tmp_path):
    path = tmp_path / 'cube.stl'
    write_stl(path, manifold_triangles(m.Manifold.cube((10, 10, 10), True).translate((0, 0, 6))))
    return path


@pytest.fixture
def window(application, cube):
    editor = MainWindow(small_settings(), cube, headless=True)
    drain(editor, application)
    yield editor
    editor.close()


def _select(panel, indices, current=None):
    """Select exactly ``indices`` in the object list, ``current`` (or the
    first) as the current row -- the same low-level selection-model dance
    ``test_gui_objects.py`` uses, since ``QListWidget.setCurrentRow`` alone
    cannot produce a multi-row selection."""
    model = panel.list.model()
    selection_model = panel.list.selectionModel()
    selection_model.clearSelection()
    for index in indices:
        selection_model.select(model.index(index, 0), QtCore.QItemSelectionModel.Select)
    selection_model.setCurrentIndex(model.index(current if current is not None else indices[0], 0),
                                    QtCore.QItemSelectionModel.Current)


# ---- scale and mirror reach the document ---------------------------------

def test_scale_edit_reaches_the_document_for_the_primary_object(window):
    window.reload = lambda: None
    window.object_panel.list.setCurrentRow(0)
    window.object_panel.scale_rows['X'].box.setValue(2.0)
    # Uniform scale is on by default, so X ties Y and Z.
    assert window.document.scale_factors == pytest.approx([2.0, 2.0, 2.0])


def test_mirror_edit_reaches_the_document_for_the_primary_object(window):
    window.reload = lambda: None
    window.object_panel.list.setCurrentRow(0)
    window.object_panel.mirror_boxes['Y'].setChecked(True)
    assert window.document.mirror_axes == [False, True, False]


def test_scale_and_mirror_edit_reaches_the_document_for_an_added_part(window):
    window.reload = lambda: None
    assert window.duplicate_object(0, 1)
    window.object_panel.list.setCurrentRow(1)
    window.object_panel.scale_uniform.setChecked(False)
    window.object_panel.scale_rows['X'].box.setValue(1.5)
    window.object_panel.mirror_boxes['Z'].setChecked(True)
    spec = window.document.extra_models[0]
    assert spec['scale'] == pytest.approx([1.5, 1.0, 1.0])
    assert spec['mirror'] == [False, False, True]


def test_object_panel_shows_scale_and_mirror_when_switching_rows(window):
    """``_sync_object_pose`` must feed the panel's scale/mirror rows too."""
    window.reload = lambda: None
    window.document.set_transform([2.0, 3.0, 4.0], [True, False, True])
    window._sync_object_pose(0)
    assert window.object_panel.scale() == pytest.approx([2.0, 3.0, 4.0])
    assert window.object_panel.mirror() == [True, False, True]


# ---- drop to plate --------------------------------------------------------

def test_drop_to_plate_zeroes_the_lift(window):
    window.reload = lambda: None
    window.object_panel.list.setCurrentRow(0)
    window.object_panel.translate_rows['Z'].set_value(37.0)
    window.apply_object_pose()
    assert window.document.model_lift_mm == pytest.approx(37.0)
    window.object_panel.drop_to_plate.click()
    assert window.document.model_lift_mm == 0.0


def test_drop_to_plate_zeroes_every_selected_part(window):
    window.reload = lambda: None
    assert window.duplicate_object(0, 1)
    window.document.set_extra_model_pose(0, lift_mm=22.0)
    window.document.set_orientation(window.document.rotation_deg, window.document.center_offset_mm, 9.0)
    _select(window.object_panel, [0, 1])
    window.object_panel.drop_to_plate.click()
    assert window.document.model_lift_mm == 0.0
    assert window.document.extra_models[0]['lift_mm'] == 0.0


# ---- multi-select pose commits move every selected part -------------------

def test_multi_select_pose_commit_moves_every_selected_part(window):
    window.reload = lambda: None
    assert window.duplicate_object(0, 2)
    before = [list(window._object_pose(i)['center_offset']) for i in range(3)]
    _select(window.object_panel, [0, 2], current=0)
    assert window.object_panel.selected_indices() == [0, 2]
    window.object_panel.translate_rows['X'].box.setValue(before[0][0] + 9.0)
    after = [list(window._object_pose(i)['center_offset']) for i in range(3)]
    assert after[0][0] == pytest.approx(before[0][0] + 9.0)
    assert after[2][0] == pytest.approx(before[2][0] + 9.0)
    # Part 1 was never selected, so the same edit must not have reached it.
    assert after[1][0] == pytest.approx(before[1][0])


def test_multi_select_drag_preview_moves_every_selected_actor(window, application):
    """``pose_preview`` must reuse the gizmo preview path for every target,
    not just the current row -- and must not touch the document."""
    assert window.duplicate_object(0, 1)
    drain(window, application)
    _select(window.object_panel, [0, 1], current=0)
    before = [list(window._object_pose(i)['center_offset']) for i in range(2)]
    pose = window.object_panel.pose()
    pose['center_offset'] = [pose['center_offset'][0] + 5.0, pose['center_offset'][1]]
    pose['applies_to'] = [0, 1]
    window._on_pose_preview(0, pose)
    after = [list(window._object_pose(i)['center_offset']) for i in range(2)]
    # A preview never writes to the document.
    assert after == before
    for index in (0, 1):
        key = 'model' if index == 0 else f'model:{index}'
        actor = window.scene.actors.get(key)
        assert actor is not None
        # A live preview transform was applied (not the identity transform).
        assert actor.GetUserTransform() is not None


# ---- zoom to selected -------------------------------------------------------

def test_zoom_to_selected_frames_the_selected_actor(window):
    assert window._on_zoom_to_selected_requested([0])


def test_zoom_to_selected_with_no_actor_says_so_instead_of_crashing(window):
    assert window._on_zoom_to_selected_requested([5]) is False
    assert 'nothing to zoom' in window.statusBar().currentMessage()


# ---- auto-orient one object -------------------------------------------------

def test_auto_orient_runs_and_writes_a_rotation_through_the_pose_path(window, application):
    assert window.auto_orient_selected(0)
    drain(window, application, stages=('orient_object',))
    assert window.last_error is None
    assert isinstance(window.document.rotation_deg, list)
    assert len(window.document.rotation_deg) == 3
    assert all(np.isfinite(window.document.rotation_deg))


# ---- island guard reporting ------------------------------------------------

def test_unresolved_island_guard_reports_honestly_and_marks_the_badge(window):
    document = window.document
    guard = {
        'plan': document.derived.plan,
        'raft': document.derived.raft,
        'union': document.derived.union,
        'contacts': [],
        'passes': [
            {'pass': 1, 'scan': 'full', 'islands': 3, 'contacts_added': 2, 'layers_scanned': 40},
            {'pass': 2, 'scan': 'full', 'islands': 2, 'contacts_added': 1, 'layers_scanned': 40,
             'stopped': 'max_passes'},
        ],
        'islands_remaining': 2,
        'island_positions': [(0.0, 0.0, 5.0), (1.0, 1.0, 5.0)],
        'max_passes': 2,
        'resolved': False,
    }
    value = {'field': document.derived.column_field, 'plan': guard['plan'], 'raft': guard['raft'],
            'union': guard['union'], 'contacts': np.zeros((0, 3)), 'guard': guard}
    seen = []
    window.notify = lambda message, **kwargs: seen.append((message, kwargs.get('level')))
    window._finish_attachments(value)
    # Attachments genuinely were routed -- that much is true regardless of
    # whether every island got cleared.
    assert window.attachment_state == 'routed'
    assert seen, 'an unresolved guard must say something'
    message, level = seen[-1]
    assert level == 'warning'
    assert '2 island' in message and 'remain' in message
    assert '3 contact' in message or 'contact(s) added' in message
    # Never claim success it did not earn.
    assert 'no islands remain' not in message
    assert window.island_summary['island_count'] == 2
    # Red, not green: '#1b7f3b' is the all-clear color, '#b00020' is not.
    assert '#b00020' in window.island_badge.styleSheet()
    assert '#1b7f3b' not in window.island_badge.styleSheet()
    report = json.loads(window.diagnostics.toPlainText())
    assert report['validation']['passed'] is False


def test_resolved_island_guard_reports_success_and_a_clean_badge(window):
    document = window.document
    guard = {
        'plan': document.derived.plan,
        'raft': document.derived.raft,
        'union': document.derived.union,
        'contacts': [],
        'passes': [{'pass': 1, 'scan': 'full', 'islands': 0, 'contacts_added': 0, 'layers_scanned': 40,
                    'stopped': 'no_islands_found'}],
        'islands_remaining': 0,
        'island_positions': [],
        'max_passes': 5,
        'resolved': True,
    }
    value = {'field': document.derived.column_field, 'plan': guard['plan'], 'raft': guard['raft'],
            'union': guard['union'], 'contacts': np.zeros((0, 3)), 'guard': guard}
    seen = []
    window.notify = lambda message, **kwargs: seen.append((message, kwargs.get('level')))
    window._finish_attachments(value)
    assert window.attachment_state == 'routed'
    message, level = seen[-1]
    assert level == 'info'
    assert 'no islands remain' in message
    assert '#1b7f3b' in window.island_badge.styleSheet()


# ---- window geometry and dock layout preferences ---------------------------

def test_window_geometry_round_trips_through_preferences(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    prefs = load_preferences()
    assert prefs['window_geometry'] is None
    assert prefs['window_state'] is None
    prefs['window_geometry'] = base64.b64encode(b'fake-geometry-bytes').decode('ascii')
    prefs['window_state'] = base64.b64encode(b'fake-state-bytes').decode('ascii')
    save_preferences(prefs)
    reloaded = load_preferences()
    assert reloaded['window_geometry'] == prefs['window_geometry']
    assert reloaded['window_state'] == prefs['window_state']


def test_a_corrupt_stored_geometry_is_ignored_not_crashed(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    path = preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'window_geometry': 'not valid base64 !!!', 'window_state': 12345}))
    prefs = load_preferences()
    assert prefs['window_geometry'] is None
    assert prefs['window_state'] is None


def test_a_truncated_stored_geometry_is_ignored_not_crashed(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    path = preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Half a base64 group: still not decodable strictly.
    path.write_text(json.dumps({'window_geometry': 'QUJDR'}))
    prefs = load_preferences()
    assert prefs['window_geometry'] is None


def test_geometry_and_state_round_trip_through_save_and_restore(application, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    first = MainWindow(resolve_settings(), None, headless=True)
    first.resize(1111, 555)
    first._save_window_layout()

    second = MainWindow(resolve_settings(), None, headless=True)
    assert second.editor_preferences['window_geometry'] == first.editor_preferences['window_geometry']
    assert second._restore_window_layout() is True
    assert second.height() == 555
    first.close()
    second.close()


def test_headless_close_never_writes_preferences(application, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    window = MainWindow(resolve_settings(), None, headless=True)
    window.close()
    assert not preferences_path().exists()


def test_a_garbled_but_base64_valid_geometry_does_not_crash_restore(application, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    window = MainWindow(resolve_settings(), None, headless=True)
    window.editor_preferences['window_geometry'] = base64.b64encode(b'not a real geometry blob').decode('ascii')
    window.editor_preferences['window_state'] = base64.b64encode(b'not a real state blob').decode('ascii')
    # Qt's own restore fails closed on bytes that decode but are not a real
    # geometry/state blob; this must not raise either way.
    assert window._restore_window_layout() is False
    window.close()


def test_reset_layout_action_clears_the_stored_layout(application, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    window = MainWindow(resolve_settings(), None, headless=True)
    window.editor_preferences['window_geometry'] = base64.b64encode(b'anything').decode('ascii')
    window.editor_preferences['window_state'] = base64.b64encode(b'anything').decode('ascii')
    # Wedge the layout: float a dock away from where the default puts it.
    window.setup_dock.setFloating(True)
    window.actions_map['reset_layout'].trigger()
    assert window.editor_preferences['window_geometry'] is None
    assert window.editor_preferences['window_state'] is None
    assert not window.setup_dock.isFloating()
    assert window.dockWidgetArea(window.setup_dock) == QtCore.Qt.RightDockWidgetArea
    window.close()


def test_a_first_run_with_no_stored_layout_gets_the_built_in_default(application, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    window = MainWindow(resolve_settings(), None, headless=True)
    assert window.editor_preferences['window_geometry'] is None
    window.show()
    QtWidgets.QApplication.processEvents()
    # showEvent's own default-sizing path ran; nothing crashed trying to
    # restore a layout that was never saved.
    assert getattr(window, '_docks_sized', False) is True
    window.close()
