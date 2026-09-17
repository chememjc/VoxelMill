"""Plate lifecycle: attachments on demand, staleness, duplicate, arrange, drop."""
from __future__ import annotations

import json
import os
import sys
from copy import deepcopy

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytestmark = pytest.mark.gui

sys.path.insert(0, os.path.dirname(__file__))

import manifold3d as m  # noqa: E402
from PySide6 import QtCore, QtWidgets  # noqa: E402

from voxelmill.geometry import manifold_triangles  # noqa: E402
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


# ---- attachments are an explicit step -----------------------------------

def test_importing_a_model_attaches_nothing(window):
    assert window.attachment_state == 'none'
    assert window.document.settings['support']['automatic'] is False
    assert window.document.derived.plan.metrics['contacts_routed'] == 0
    assert 'supports' not in window.scene.actors


def test_the_editor_default_does_not_change_the_command_line_default():
    """Supports off is an editor decision; prepare must behave as it always did."""
    from voxelmill.config import resolve_settings
    assert resolve_settings()['support']['automatic'] is True


def test_compute_attachments_routes_and_reports_routed(window, application):
    assert window.compute_attachments()
    drain(window, application, stages=('attachments',))
    assert window.attachment_state == 'routed'
    assert window.document.derived.plan.metrics['contacts_routed'] > 0
    assert 'attachments: routed' in window.object_panel.list.item(0).text()


def test_compute_attachments_without_a_model_declines_rather_than_raising(application):
    editor = MainWindow(small_settings(), None, headless=True)
    assert editor.compute_attachments() is False
    assert editor.attachment_state == 'none'
    editor.close()


# ---- the staleness rule -------------------------------------------------

def test_moving_a_part_across_the_plate_keeps_its_attachments(window, application):
    window.compute_attachments()
    drain(window, application, stages=('attachments',))
    window.reload = lambda: None
    window.object_panel.translate_rows['X'].set_value(8.0)
    window.apply_object_pose()
    # Sliding in XY changes neither which faces point down nor how far a
    # support must reach, so what was routed is still the right shape.
    assert window.attachment_state == 'stale'


def test_raising_a_part_drops_its_attachments(window, application):
    window.compute_attachments()
    drain(window, application, stages=('attachments',))
    window.reload = lambda: None
    window.object_panel.translate_rows['Z'].set_value(20.0)
    window.apply_object_pose()
    assert window.attachment_state == 'none'


def test_rotating_a_part_drops_its_attachments(window, application):
    window.compute_attachments()
    drain(window, application, stages=('attachments',))
    window.reload = lambda: None
    window.object_panel.rotate_rows['Y'].set_value(30.0)
    window.apply_object_pose()
    assert window.attachment_state == 'none'


def test_a_dropped_attachment_says_why(window, application):
    window.compute_attachments()
    drain(window, application, stages=('attachments',))
    window.reload = lambda: None
    seen = []
    window.notify = lambda message, **kwargs: seen.append(message)
    window.object_panel.rotate_rows['X'].set_value(15.0)
    window.apply_object_pose()
    assert seen and 'rotating' in seen[0]


def test_nothing_is_dropped_when_there_was_nothing_attached(window):
    window.reload = lambda: None
    window.object_panel.rotate_rows['X'].set_value(15.0)
    window.apply_object_pose()
    assert window.attachment_state == 'none'


# ---- pose editing -------------------------------------------------------

def test_a_slider_drag_is_one_document_edit_not_one_per_tick(window):
    """A drag previews every step live and commits exactly once, on release."""
    window.reload = lambda: None
    before = len(window.document._undo)
    row = window.object_panel.translate_rows['X']
    row.slider.setSliderDown(True)
    for value in (1.0, 2.0, 3.0, 4.0, 5.0):
        row.slider.setValue(int(round(value * 100)))
    assert len(window.document._undo) == before
    row.slider.setSliderDown(False)
    assert len(window.document._undo) == before + 1
    assert window.document.center_offset_mm[0] == 5.0


def test_a_drag_in_the_3d_view_snaps_rotation_to_the_increment(window):
    window.reload = lambda: None
    window.object_panel.set_snap_angle(5.0)
    window._on_object_transformed(0, [0.0, 0.0, 0.0], [0.0, 0.0, 47.0])
    assert window.document.rotation_deg[2] == 45.0
    assert window.object_panel.rotate_rows['Z'].value() == 45.0


def test_a_typed_angle_is_taken_exactly_even_with_snapping_on(window):
    window.reload = lambda: None
    window.object_panel.set_snap_angle(5.0)
    window.object_panel.rotate_rows['Z'].box.setValue(37.5)
    window.apply_object_pose()
    assert window.document.rotation_deg[2] == 37.5


def test_snapping_off_leaves_a_dragged_angle_alone(window):
    window.reload = lambda: None
    window.object_panel.set_snap_angle(0.0)
    window._on_object_transformed(0, [0.0, 0.0, 0.0], [0.0, 0.0, 47.0])
    assert window.document.rotation_deg[2] == 47.0


# ---- multiple parts -----------------------------------------------------

def test_duplicating_a_part_reuses_its_mesh_and_lays_the_plate_out(window, application):
    window.reload = lambda: None
    assert window.duplicate_object(0, 2)
    assert len(window.document.extra_models) == 2
    assert all(spec['path'] == str(window.document.source) for spec in window.document.extra_models)
    # Copies land on their original, so arranging is part of duplicating.
    offsets = [tuple(window.document.center_offset_mm)] + [
        tuple(spec['center_offset']) for spec in window.document.extra_models]
    assert len(set(offsets)) == 3


def test_arrange_separates_every_part(window, application):
    window.reload = lambda: None
    window.duplicate_object(0, 3)
    assert window.arrange_objects()
    offsets = [tuple(window.document.center_offset_mm)] + [
        tuple(spec['center_offset']) for spec in window.document.extra_models]
    assert len(set(offsets)) == len(offsets)
    build = window.document.settings['printer']['build_mm']
    for x, y in offsets:
        assert abs(x) <= build[0] / 2 and abs(y) <= build[1] / 2


def test_arrange_refuses_rather_than_overlapping_when_the_plate_is_full(window, application):
    window.reload = lambda: None
    tiny = dict(window.document.settings)
    tiny['printer'] = dict(tiny['printer'])
    tiny['printer']['build_mm'] = [12.0, 12.0, 100.0]
    window.document.settings = tiny
    window.duplicate_object(0, 4)
    before = [tuple(spec['center_offset']) for spec in window.document.extra_models]
    assert window.arrange_objects() is False
    assert window.last_error['code'] == 'arrange_no_fit'
    # Nothing moved: a refusal leaves the plate exactly as it was.
    assert [tuple(spec['center_offset']) for spec in window.document.extra_models] == before


def test_removing_a_part_is_refused_for_the_primary(window):
    window.reload = lambda: None
    window.duplicate_object(0, 1)
    window.object_panel.list.setCurrentRow(0)
    assert window.remove_selected_object() is False
    window.object_panel.list.setCurrentRow(1)
    assert window.remove_selected_object()
    assert window.document.extra_models == []


def test_hiding_a_part_only_hides_it(window):
    window.object_panel.list.item(0).setCheckState(QtCore.Qt.Unchecked)
    assert not window.scene.is_visible('model')
    # Hidden is a view state; the part is still on the plate.
    assert window.document.source is not None


# ---- drag and drop ------------------------------------------------------

def test_dropping_stls_on_the_window_adds_them(window, tmp_path):
    window.reload = lambda: None
    extra = tmp_path / 'dropped.stl'
    write_stl(extra, manifold_triangles(m.Manifold.cube((4, 4, 4), True).translate((0, 0, 3))))
    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl.fromLocalFile(str(extra))])
    event = QtGuiDropEvent(mime)
    window.dropEvent(event)
    assert len(window.document.extra_models) == 1
    assert window.document.extra_models[0]['path'] == str(extra)


def QtGuiDropEvent(mime):
    from PySide6 import QtGui
    return QtGui.QDropEvent(QtCore.QPointF(10, 10), QtCore.Qt.CopyAction, mime,
                            QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)


def test_dropping_onto_an_empty_editor_opens_the_first_file(application, cube):
    editor = MainWindow(small_settings(), None, headless=True)
    opened = []
    editor.open_stl = lambda path: opened.append(path)
    editor.add_models = lambda paths: opened.extend(paths)
    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl.fromLocalFile(str(cube))])
    editor.dropEvent(QtGuiDropEvent(mime))
    assert opened == [str(cube)]
    editor.close()


# ---- per-part attachment settings ---------------------------------------

def test_the_first_part_edits_the_plate_values_directly(window):
    """Row 0 has no overlay: its attachment values are the plate's."""
    window.reload = lambda: None
    window.object_panel.list.setCurrentRow(0)
    assert not window.attachment_settings.override.isVisible()
    window.attachment_settings.rows['spacing_mm'].setValue(4.5)
    assert window.document.settings['support']['spacing_mm'] == 4.5
    assert window.document.extra_models == []


def test_an_added_part_inherits_the_plate_until_the_override_is_ticked(window):
    window.reload = lambda: None
    window.duplicate_object(0, 1)
    window.object_panel.list.setCurrentRow(1)
    assert window.document.extra_models[0].get('overrides') == {}
    assert not window.attachment_settings.override.isChecked()
    assert not window.attachment_settings.rows['spacing_mm'].isEnabled()
    assert 'Inheriting' in window.attachment_settings.note.text()


def test_ticking_the_override_writes_only_the_values_that_differ(window):
    window.reload = lambda: None
    window.duplicate_object(0, 1)
    window.object_panel.list.setCurrentRow(1)
    window.attachment_settings.override.setChecked(True)
    window.attachment_settings.rows['pillar_diameter_mm'].setValue(1.8)
    overlay = window.document.extra_models[0]['overrides']['support']
    # Only the changed key is stamped; the rest stay inherited rather than
    # being frozen at today's plate values.
    assert overlay == {'pillar_diameter_mm': 1.8}


def test_unticking_the_override_returns_the_part_to_the_plate_values(window):
    window.reload = lambda: None
    window.duplicate_object(0, 1)
    window.object_panel.list.setCurrentRow(1)
    window.attachment_settings.override.setChecked(True)
    window.attachment_settings.rows['pillar_diameter_mm'].setValue(1.8)
    assert window.document.extra_models[0]['overrides']
    window.attachment_settings.override.setChecked(False)
    assert window.document.extra_models[0]['overrides'] == {}


def test_the_json_field_carries_only_keys_the_controls_do_not(window):
    window.reload = lambda: None
    window.duplicate_object(0, 1)
    window.object_panel.list.setCurrentRow(1)
    window.document.set_extra_model_overrides(
        0, {'support': {'pillar_diameter_mm': 1.8, 'tip_length_mm': 3.0}})
    window._sync_object_pose(1)
    assert window.attachment_settings.rows['pillar_diameter_mm'].value() == 1.8
    assert json.loads(window.object_overrides.text()) == {'tip_length_mm': 3.0}


def test_applying_the_json_field_does_not_wipe_the_controls(window):
    window.reload = lambda: None
    window.duplicate_object(0, 1)
    window.object_panel.list.setCurrentRow(1)
    window.attachment_settings.override.setChecked(True)
    window.attachment_settings.rows['pillar_diameter_mm'].setValue(1.8)
    window.object_overrides.setText('{"tip_length_mm": 3.0}')
    assert window.apply_object_overrides()
    overlay = window.document.extra_models[0]['overrides']['support']
    assert overlay == {'pillar_diameter_mm': 1.8, 'tip_length_mm': 3.0}


def test_changing_attachment_values_marks_routed_attachments_stale(window, application):
    window.compute_attachments()
    drain(window, application, stages=('attachments',))
    window.reload = lambda: None
    window.object_panel.list.setCurrentRow(0)
    window.attachment_settings.rows['spacing_mm'].setValue(5.0)
    assert window.attachment_state == 'stale'


# ---- the tool strip -----------------------------------------------------

def test_the_default_tool_selects_and_does_not_edit_the_model(window):
    from voxelmill.gui.objects import TOOLS
    assert window.tools.tool == 'select'
    assert [name for name, _label, _tip in TOOLS] == [
        'select', 'add', 'remove', 'enforce', 'block']
    before = list(window.document.manual_contacts)
    window.handle_pick([0.0, 0.0, 5.0], 'model')
    assert window.document.manual_contacts == before


def test_the_add_tool_makes_a_plain_click_place_a_point(window):
    window.reload = lambda: None
    window.tools.set_tool('add')
    assert window.handle_pick([1.0, 2.0, 5.0], 'model')
    assert window.document.manual_contacts == [[1.0, 2.0, 5.0]]


def test_the_remove_tool_makes_a_plain_click_suppress_a_point(window):
    window.reload = lambda: None
    window.tools.set_tool('add')
    window.handle_pick([1.0, 2.0, 5.0], 'model')
    window.scene.set_contacts([[1.0, 2.0, 5.0]])
    window.tools.set_tool('remove')
    assert window.handle_pick([1.0, 2.0, 5.0], 'model')
    assert window.document.manual_contacts == []


def test_the_modifier_shortcuts_still_work_whatever_the_tool_is(window):
    """The tool strip exposes the modifiers; it does not replace them."""
    window.reload = lambda: None
    window.tools.set_tool('select')
    assert window.handle_pick([3.0, 4.0, 5.0], 'model', QtCore.Qt.ShiftModifier)
    assert window.document.manual_contacts == [[3.0, 4.0, 5.0]]


def test_a_paint_tool_arms_painting_and_shows_the_brush(window):
    window.tools.set_tool('block')
    assert window.paint_mode.currentText() == 'block'
    assert window.tools.radius.isVisibleTo(window.tools)
    window.tools.set_tool('select')
    assert window.paint_mode.currentText() == 'off'
    assert not window.tools.radius.isVisibleTo(window.tools)


def test_the_setup_paint_combo_and_the_tool_strip_are_one_choice(window):
    window.paint_mode.setCurrentText('enforce')
    assert window.tools.tool == 'enforce'


def test_the_brush_size_is_one_number_in_both_places(window):
    window.tools.radius.setValue(2.5)
    assert window.paint_radius.value() == 2.5


def test_a_dragged_rotation_slider_snaps_but_a_typed_angle_does_not(window):
    window.reload = lambda: None
    window.object_panel.set_snap_angle(5.0)
    row = window.object_panel.rotate_rows['Z']
    row.slider.setValue(4730)
    assert row.value() == 45.0
    row.box.setValue(37.5)
    assert row.value() == 37.5


def test_adding_models_arranges_them_instead_of_asking_for_offsets(window, tmp_path, monkeypatch):
    """Guessing a center offset before the part is loaded is what made this clunky."""
    window.reload = lambda: None
    extra = tmp_path / 'added.stl'
    write_stl(extra, manifold_triangles(m.Manifold.cube((4, 4, 4), True).translate((0, 0, 3))))
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getOpenFileNames',
                        staticmethod(lambda *a, **k: ([str(extra)], '')))
    prompts = []
    monkeypatch.setattr(QtWidgets.QInputDialog, 'getDouble',
                        staticmethod(lambda *a, **k: prompts.append(a) or (0.0, True)))
    assert window.add_extra_model_dialog()
    assert prompts == []
    assert len(window.document.extra_models) == 1
    offsets = [tuple(window.document.center_offset_mm),
               tuple(window.document.extra_models[0]['center_offset'])]
    assert offsets[0] != offsets[1]


def test_adding_a_model_to_a_routed_plate_marks_it_stale(window, application, tmp_path):
    window.compute_attachments()
    drain(window, application, stages=('attachments',))
    window.reload = lambda: None
    extra = tmp_path / 'more.stl'
    write_stl(extra, manifold_triangles(m.Manifold.cube((4, 4, 4), True).translate((0, 0, 3))))
    window.add_models([str(extra)])
    assert window.attachment_state == 'stale'


# ---- paint travels with its part ----------------------------------------

def test_paint_follows_the_part_when_it_moves(window, application):
    """The whole reason paint is stored in the object's own frame.

    Plate-coordinate marks stayed where the part had been, so moving a painted
    part silently detached its paint from the faces it was drawn on. Only the
    place stage is drained here: it is what recomputes the placement matrix
    that converts stored marks back to the plate.
    """
    window.tools.set_tool('block')
    position = window.placed.reshape(-1, 3, 3)[0].mean(axis=0)
    window._on_painted(position, 0)
    window.rebuild = lambda: None
    window._on_paint_finished()
    before = sorted(window.plate_paint()['blocked'])
    assert before, 'the brush marked nothing to begin with'
    stored = deepcopy(window.document.paint[0]['blocked'])

    window.object_panel.translate_rows['X'].set_value(12.0)
    window.apply_object_pose()
    drain(window, application, stages=('place',))

    # The stored marks never move; they are in the part's own frame.
    assert window.document.paint[0]['blocked'] == stored
    after = sorted(window.plate_paint()['blocked'])
    assert len(after) == len(before)
    for a, b in zip(before, after):
        assert b[0] - a[0] == pytest.approx(12.0)
        assert b[1] == pytest.approx(a[1])
        assert b[2] == pytest.approx(a[2])


def test_a_brush_stroke_is_attributed_to_the_part_it_landed_on(window, application):
    window.reload = lambda: None
    window.duplicate_object(0, 1)
    del window.reload
    window.reload()
    drain(window, application, stages=('place',))
    window.tools.set_tool('block')
    second = window._object_placed_triangles(1)
    assert second is not None and len(second)
    window.rebuild = lambda: None
    window._on_painted(second.reshape(-1, 3, 3)[0].mean(axis=0), 1)
    window._on_paint_finished()
    assert window.document.paint[0]['blocked'] == []
    assert window.document.paint[1]['blocked']


def test_arrange_reserves_room_for_a_part_the_preview_has_not_drawn_yet(window):
    """A duplicate exists as a pose before it exists as geometry.

    Reserving a nominal 1 mm square for it packed the plate as though the copy
    were tiny, and the parts then overlapped for real.
    """
    window.reload = lambda: None
    footprints, poses = window._object_footprints()
    known = footprints[0]
    window.duplicate_object(0, 1)
    footprints, poses = window._object_footprints()
    assert len(footprints) == len(poses) == 2
    assert footprints[1] == known
    offsets = [tuple(window.document.center_offset_mm),
               tuple(window.document.extra_models[0]['center_offset'])]
    gap = max(abs(offsets[0][0] - offsets[1][0]), abs(offsets[0][1] - offsets[1][1]))
    assert gap >= known[0] or gap >= known[1]
