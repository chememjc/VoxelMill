"""The plate object list, its placement controls, and the snap increment."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytestmark = pytest.mark.gui

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from voxelmill.gui.appprefs import (  # noqa: E402
    DEFAULT_SNAP_ANGLE_DEG, DEFAULTS, load_preferences, save_preferences, snap_angle,
)
from voxelmill.gui.objects import AxisRow, ObjectPanel  # noqa: E402


@pytest.fixture(scope='module')
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def panel(application):
    widget = ObjectPanel()
    widget.set_limits((200.0, 120.0, 220.0))
    widget.set_objects([
        {'name': 'primary.stl', 'visible': True, 'supports': 'none'},
        {'name': 'added.stl', 'visible': True, 'supports': 'none'},
    ])
    return widget


# ---- snap ---------------------------------------------------------------

def test_the_default_snap_increment_is_five_degrees():
    assert DEFAULT_SNAP_ANGLE_DEG == 5.0
    assert DEFAULTS['snap_angle_deg'] == 5.0


def test_snap_rounds_to_the_nearest_multiple_and_zero_means_no_snapping():
    assert snap_angle(47.0, 5) == 45.0
    assert snap_angle(48.0, 5) == 50.0
    assert snap_angle(-47.0, 5) == -45.0
    assert snap_angle(47.3, 0) == 47.3
    assert snap_angle(47.3, -1) == 47.3


def test_preferences_round_trip_and_a_corrupt_file_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    assert load_preferences()['snap_angle_deg'] == DEFAULT_SNAP_ANGLE_DEG
    save_preferences({'snap_angle_deg': 15.0})
    assert load_preferences()['snap_angle_deg'] == 15.0
    (tmp_path / 'voxelmill' / 'editor.json').write_text('{ not json')
    assert load_preferences()['snap_angle_deg'] == DEFAULT_SNAP_ANGLE_DEG


def test_an_out_of_range_stored_snap_is_refused_rather_than_used(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    save_preferences({'snap_angle_deg': 5.0})
    (tmp_path / 'voxelmill' / 'editor.json').write_text('{"snap_angle_deg": 900}')
    assert load_preferences()['snap_angle_deg'] == DEFAULT_SNAP_ANGLE_DEG


# ---- the axis row -------------------------------------------------------

def test_the_slider_and_the_text_field_are_two_views_of_one_number(application):
    row = AxisRow('X', minimum=-100, maximum=100)
    edits = []
    row.value_edited.connect(edits.append)
    row.box.setValue(12.5)
    assert row.slider.value() == 1250
    assert edits == [12.5]
    row.slider.setValue(-2500)
    assert row.box.value() == pytest.approx(-25.0)
    assert edits == [12.5, -25.0]


def test_setting_a_value_from_outside_does_not_echo_back_as_an_edit(application):
    """A pose pushed in from the document or a 3D drag must not re-emit."""
    row = AxisRow('X', minimum=-100, maximum=100)
    edits = []
    row.value_edited.connect(edits.append)
    row.set_value(30.0)
    assert row.box.value() == 30.0 and row.slider.value() == 3000
    assert edits == []


def test_a_value_is_clamped_to_the_axis_range(application):
    row = AxisRow('Z', minimum=0, maximum=50)
    row.set_value(999.0)
    assert row.value() == 50.0
    row.set_value(-999.0)
    assert row.value() == 0.0


def test_rotation_rows_offer_forty_five_and_snap_nudges_in_both_directions(panel):
    row = panel.rotate_rows['Z']
    labels = [button.text() for button, _ in row.nudge_buttons]
    assert labels == ['-45', '-5', '+5', '+45']
    row.set_value(0.0)
    row.nudge(-45)
    assert row.value() == -45.0


def test_changing_the_snap_preference_relabels_the_nudge_buttons(panel):
    panel.set_snap_angle(15.0)
    labels = [button.text() for button, _ in panel.rotate_rows['X'].nudge_buttons]
    assert labels == ['-45', '-15', '+15', '+45']
    assert panel.snap(47.0) == 45.0
    panel.set_snap_angle(0.0)
    # A zero increment is not a button; it is hidden rather than offered as a no-op.
    hidden = [button.isVisibleTo(panel) for button, amount in panel.rotate_rows['X'].nudge_buttons
              if amount == 0]
    assert hidden and not any(hidden)
    assert panel.snap(47.3) == 47.3


def test_translation_rows_default_their_nudges_to_the_translate_step(panel):
    labels = [button.text() for button, _ in panel.translate_rows['X'].nudge_buttons]
    assert labels == ['-1', '+1']
    panel.set_translate_step(2.5)
    labels = [button.text() for button, _ in panel.translate_rows['Y'].nudge_buttons]
    assert labels == ['-2.5', '+2.5']


# ---- the object list ----------------------------------------------------

def test_the_list_names_each_part_its_role_and_its_attachment_state(panel):
    assert 'primary' in panel.list.item(0).text()
    assert 'added 1' in panel.list.item(1).text()
    assert 'attachments: none' in panel.list.item(0).text()


def test_selecting_a_row_reports_the_index(panel):
    seen = []
    panel.selection_changed.connect(seen.append)
    panel.list.setCurrentRow(1)
    assert seen == [1]
    assert panel.current_index() == 1


def test_rebuilding_the_list_does_not_fire_a_selection_change(panel):
    panel.list.setCurrentRow(1)
    seen = []
    panel.selection_changed.connect(seen.append)
    panel.set_objects([{'name': 'a', 'visible': True, 'supports': 'none'},
                       {'name': 'b', 'visible': True, 'supports': 'routed'}])
    assert seen == []
    assert panel.current_index() == 1


def test_the_primary_part_cannot_be_removed(panel):
    panel.list.setCurrentRow(0)
    assert not panel.findChild(QtWidgets.QPushButton, 'object_remove').isEnabled()
    panel.list.setCurrentRow(1)
    assert panel.findChild(QtWidgets.QPushButton, 'object_remove').isEnabled()


def test_unchecking_a_row_reports_a_visibility_change(panel):
    changes = []
    panel.visibility_changed.connect(lambda index, visible: changes.append((index, visible)))
    panel.list.item(1).setCheckState(QtCore.Qt.Unchecked)
    assert changes == [(1, False)]


# ---- pose editing -------------------------------------------------------

def test_editing_any_control_reports_the_whole_pose_for_the_selected_object(panel):
    panel.list.setCurrentRow(1)
    seen = []
    panel.pose_edited.connect(lambda index, pose: seen.append((index, pose)))
    panel.translate_rows['X'].box.setValue(12.0)
    assert len(seen) == 1
    index, pose = seen[0]
    assert index == 1
    assert pose['center_offset'][0] == 12.0
    panel.rotate_rows['Y'].nudge(45)
    assert seen[-1][1]['rotate'][1] == 45.0


def test_a_pose_pushed_in_from_the_document_is_not_reported_back_as_an_edit(panel):
    seen = []
    panel.pose_edited.connect(lambda index, pose: seen.append(pose))
    panel.set_pose({'rotate': [1.0, 2.0, 3.0], 'center_offset': [4.0, 5.0], 'lift_mm': 6.0})
    assert seen == []
    assert panel.pose() == {'center_offset': [4.0, 5.0], 'lift_mm': 6.0,
                            'rotate': [1.0, 2.0, 3.0]}


def test_move_sliders_are_clamped_to_the_build_envelope(panel):
    panel.set_limits((100.0, 60.0, 150.0))
    panel.translate_rows['X'].set_value(999.0)
    assert panel.translate_rows['X'].value() == 50.0
    panel.translate_rows['Z'].set_value(-5.0)
    assert panel.translate_rows['Z'].value() == 0.0


def test_reset_rotation_zeroes_all_three_axes_and_reports_once(panel):
    panel.set_pose({'rotate': [10.0, 20.0, 30.0], 'center_offset': [0.0, 0.0], 'lift_mm': 5.0})
    seen = []
    panel.pose_edited.connect(lambda index, pose: seen.append(pose))
    panel.reset_rotation.click()
    assert len(seen) == 1
    assert seen[0]['rotate'] == [0.0, 0.0, 0.0]
    assert seen[0]['lift_mm'] == 5.0


# ---- live drag preview ---------------------------------------------------

def test_dragging_a_slider_previews_every_step_then_commits_once(panel):
    panel.list.setCurrentRow(1)
    previews, commits, edits = [], [], []
    panel.pose_preview.connect(lambda index, pose: previews.append((index, pose)))
    panel.pose_committed.connect(lambda index, pose: commits.append((index, pose)))
    panel.pose_edited.connect(lambda index, pose: edits.append((index, pose)))
    row = panel.translate_rows['X']
    row.slider.setSliderDown(True)
    row.slider.setValue(500)
    row.slider.setValue(1000)
    row.slider.setSliderDown(False)
    assert len(previews) == 2
    assert len(commits) == 1
    # pose_edited still fires for the commit, so existing wiring is untouched.
    assert len(edits) == 1
    assert commits[0][0] == 1
    assert commits[0][1]['center_offset'][0] == row.value()


def test_a_typed_value_or_a_nudge_button_commits_once_without_previewing(panel):
    previews, commits = [], []
    panel.pose_preview.connect(lambda index, pose: previews.append((index, pose)))
    panel.pose_committed.connect(lambda index, pose: commits.append((index, pose)))
    panel.translate_rows['X'].box.setValue(7.0)
    assert previews == []
    assert len(commits) == 1
    panel.rotate_rows['Z'].nudge(5)
    assert previews == []
    assert len(commits) == 2


def test_a_typed_value_does_not_snap_while_a_dragged_slider_does(panel):
    """Existing AxisRow behavior: only a graphical drag snaps."""
    row = panel.rotate_rows['Z']
    row.box.setValue(47.0)
    assert row.value() == 47.0
    row.box.setValue(40.0)
    row.slider.setSliderDown(True)
    row.slider.setValue(int(47.0 * 100))
    row.slider.setSliderDown(False)
    assert row.value() == 45.0


# ---- scale and mirror -----------------------------------------------------

def test_uniform_scale_ties_the_three_rows_and_reports_once(panel):
    assert panel.scale_uniform.isChecked()
    seen = []
    panel.scale_edited.connect(lambda index, scale, mirror: seen.append((index, scale, mirror)))
    panel.scale_rows['X'].box.setValue(2.0)
    assert panel.scale_rows['Y'].value() == 2.0
    assert panel.scale_rows['Z'].value() == 2.0
    assert len(seen) == 1
    assert seen[0][1] == [2.0, 2.0, 2.0]


def test_unchecking_uniform_lets_axes_scale_independently(panel):
    panel.scale_uniform.setChecked(False)
    panel.scale_rows['X'].box.setValue(3.0)
    assert panel.scale_rows['Y'].value() == 1.0
    assert panel.scale_rows['Z'].value() == 1.0


def test_reset_scale_returns_every_axis_to_one(panel):
    panel.scale_rows['X'].box.setValue(4.0)
    panel.reset_scale.click()
    assert panel.scale() == [1.0, 1.0, 1.0]


def test_mirror_checkboxes_reach_the_scale_signal(panel):
    seen = []
    panel.scale_edited.connect(lambda index, scale, mirror: seen.append(mirror))
    panel.mirror_boxes['X'].setChecked(True)
    assert seen == [[True, False, False]]


def test_set_pose_loads_scale_and_mirror_without_emitting(panel):
    seen = []
    panel.scale_edited.connect(lambda index, scale, mirror: seen.append((scale, mirror)))
    panel.set_pose({'rotate': [0.0, 0.0, 0.0], 'center_offset': [0.0, 0.0], 'lift_mm': 0.0,
                    'scale': [2.0, 3.0, 4.0], 'mirror': [True, False, True]})
    assert seen == []
    assert panel.scale() == [2.0, 3.0, 4.0]
    assert panel.mirror() == [True, False, True]


# ---- multi-select ----------------------------------------------------

def test_selected_indices_lists_every_selected_row_ascending(panel):
    panel.set_objects([{'name': f'p{i}', 'visible': True, 'supports': 'none'} for i in range(4)])
    panel.list.clearSelection()
    panel.list.item(3).setSelected(True)
    panel.list.item(1).setSelected(True)
    assert panel.selected_indices() == [1, 3]


def test_multi_select_emits_the_indices_signal_and_updates_status(panel):
    panel.set_objects([{'name': f'p{i}', 'visible': True, 'supports': 'none'} for i in range(3)])
    seen = []
    panel.selection_indices_changed.connect(seen.append)
    panel.list.clearSelection()
    panel.list.item(0).setSelected(True)
    panel.list.item(2).setSelected(True)
    assert seen[-1] == [0, 2]
    assert '2' in panel.status.text()


def test_a_multi_select_pose_edit_carries_every_target_index(panel):
    panel.set_objects([{'name': f'p{i}', 'visible': True, 'supports': 'none'} for i in range(3)])
    selection_model = panel.list.selectionModel()
    model = panel.list.model()
    selection_model.clearSelection()
    selection_model.select(model.index(0, 0), QtCore.QItemSelectionModel.Select)
    selection_model.select(model.index(2, 0), QtCore.QItemSelectionModel.Select)
    selection_model.setCurrentIndex(model.index(0, 0), QtCore.QItemSelectionModel.Current)
    assert panel.selected_indices() == [0, 2]
    seen = []
    panel.pose_committed.connect(lambda index, pose: seen.append((index, pose)))
    panel.translate_rows['X'].box.setValue(9.0)
    assert seen[-1][0] == 0
    assert seen[-1][1]['applies_to'] == [0, 2]


def test_a_single_select_pose_edit_carries_no_applies_to_key(panel):
    panel.list.setCurrentRow(1)
    seen = []
    panel.pose_committed.connect(lambda index, pose: seen.append(pose))
    panel.translate_rows['X'].box.setValue(3.0)
    assert 'applies_to' not in seen[-1]


# ---- arrow-key nudge -------------------------------------------------

def _key(key, modifiers=QtCore.Qt.NoModifier):
    return QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, modifiers)


def test_arrow_keys_nudge_the_selected_part_by_the_translate_step(panel):
    panel.list.setCurrentRow(1)
    before_x = panel.translate_rows['X'].value()
    before_y = panel.translate_rows['Y'].value()
    before_z = panel.translate_rows['Z'].value()
    QtWidgets.QApplication.sendEvent(panel.list, _key(QtCore.Qt.Key_Right))
    assert panel.translate_rows['X'].value() == pytest.approx(before_x + 1.0)
    QtWidgets.QApplication.sendEvent(panel.list, _key(QtCore.Qt.Key_Left))
    assert panel.translate_rows['X'].value() == pytest.approx(before_x)
    QtWidgets.QApplication.sendEvent(panel.list, _key(QtCore.Qt.Key_Up))
    assert panel.translate_rows['Y'].value() == pytest.approx(before_y + 1.0)
    QtWidgets.QApplication.sendEvent(panel.list, _key(QtCore.Qt.Key_PageUp))
    assert panel.translate_rows['Z'].value() == pytest.approx(before_z + 1.0)


def test_shift_arrow_key_nudges_by_ten_times_the_translate_step(panel):
    panel.list.setCurrentRow(1)
    before = panel.translate_rows['X'].value()
    QtWidgets.QApplication.sendEvent(
        panel.list, _key(QtCore.Qt.Key_Right, QtCore.Qt.ShiftModifier))
    assert panel.translate_rows['X'].value() == pytest.approx(before + 10.0)


def test_arrow_key_nudge_emits_exactly_one_commit(panel):
    panel.list.setCurrentRow(1)
    commits = []
    panel.pose_committed.connect(lambda index, pose: commits.append((index, pose)))
    QtWidgets.QApplication.sendEvent(panel.list, _key(QtCore.Qt.Key_Right))
    assert len(commits) == 1
    assert commits[0][0] == 1


def test_arrow_key_does_not_nudge_while_a_spin_box_has_focus(panel):
    panel.show()
    box = panel.rotate_rows['X'].box
    box.setFocus()
    QtWidgets.QApplication.processEvents()
    try:
        if QtWidgets.QApplication.focusWidget() is not box:
            pytest.skip('offscreen platform did not grant the spin box focus')
        before = panel.translate_rows['X'].value()
        QtWidgets.QApplication.sendEvent(panel.list, _key(QtCore.Qt.Key_Right))
        assert panel.translate_rows['X'].value() == before
    finally:
        panel.hide()


# ---- drop to plate, zoom, auto-orient --------------------------------

def test_drop_to_plate_and_zoom_report_the_selected_indices(panel):
    panel.set_objects([{'name': f'p{i}', 'visible': True, 'supports': 'none'} for i in range(3)])
    panel.list.clearSelection()
    panel.list.item(0).setSelected(True)
    panel.list.item(1).setSelected(True)
    drops, zooms = [], []
    panel.drop_to_plate_requested.connect(drops.append)
    panel.zoom_to_selected_requested.connect(zooms.append)
    panel.drop_to_plate.click()
    panel.zoom_selected.click()
    assert drops == [[0, 1]]
    assert zooms == [[0, 1]]


def test_auto_orient_reports_the_current_index_and_disables_with_no_selection(panel):
    panel.list.setCurrentRow(1)
    seen = []
    panel.auto_orient_requested.connect(seen.append)
    assert panel.auto_orient.isEnabled()
    panel.auto_orient.click()
    assert seen == [1]
    panel.list.setCurrentRow(-1)
    assert not panel.auto_orient.isEnabled()


# ---- drag and drop ------------------------------------------------------

def test_only_stl_urls_are_accepted_from_a_drop(panel):
    from PySide6 import QtCore as core
    mime = core.QMimeData()
    mime.setUrls([core.QUrl.fromLocalFile('/tmp/a.stl'),
                  core.QUrl.fromLocalFile('/tmp/b.txt'),
                  core.QUrl('https://example.invalid/c.stl')])
    assert panel._dropped_models(mime) == ['/tmp/a.stl']
