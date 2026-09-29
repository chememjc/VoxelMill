"""Rotation wraps rather than clamps, and a dropped preview never lingers.

Two bugs, one file. First: the object panel's rotate rows clamped at +/-180,
but rotation is periodic -- a gizmo commit or a nudge that carries an angle
past the boundary must wrap into the same range instead of losing the part
that pushed it past 180. Second: a gizmo or panel drag previews a rotation on
the actor before the document agrees to it; if the drag is abandoned rather
than committed, the actor can be left showing an orientation the document,
and everything that reads the document, never received.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest


pytestmark = pytest.mark.gui

sys.path.insert(0, os.path.dirname(__file__))

import manifold3d as m  # noqa: E402

from voxelmill.geometry import manifold_triangles, rotation_matrix, wrap_rotation_deg  # noqa: E402
from voxelmill.gui.objects import AxisRow  # noqa: E402
from voxelmill.gui.window import MainWindow  # noqa: E402
from test_gui import drain, small_settings, write_stl  # noqa: E402


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


def _actor_matrix(actor):
    """The actor's live user transform as a 4x4, or identity when there is none."""
    transform = actor.GetUserTransform()
    if transform is None:
        return np.eye(4)
    matrix = transform.GetMatrix()
    return np.array([[matrix.GetElement(i, j) for j in range(4)] for i in range(4)])


# ---- the wrap convention itself ------------------------------------------

def test_wrap_convention_at_the_boundary():
    """(-180, 180]: 180 and -180 both name the same orientation and both
    canonicalize to +180; everything else folds back the same way."""
    assert wrap_rotation_deg(180.0) == 180.0
    assert wrap_rotation_deg(181.0) == pytest.approx(-179.0)
    assert wrap_rotation_deg(-180.0) == 180.0
    assert wrap_rotation_deg(200.0) == pytest.approx(-160.0)
    assert wrap_rotation_deg(-181.0) == pytest.approx(179.0)
    assert wrap_rotation_deg(0.0) == 0.0


# ---- BUG 1: a gizmo commit or nudge past 180 must wrap, not clamp --------

def test_gizmo_commit_past_180_wraps_and_matches_the_panel(window):
    window.reload = lambda: None
    window._write_pose(0, {'rotate': [170.0, 0.0, 0.0], 'center_offset': [0.0, 0.0], 'lift_mm': 5.0})
    window._on_object_transformed(0, [0.0, 0.0, 0.0], [30.0, 0.0, 0.0])

    # The document never stores the raw 200: it is wrapped to the same
    # orientation, -160, which the (-180, 180] panel row can show exactly.
    assert window.document.rotation_deg == pytest.approx([-160.0, 0.0, 0.0])
    panel_rotate = [window.object_panel.rotate_rows[axis].value() for axis in ('X', 'Y', 'Z')]
    assert panel_rotate == pytest.approx(window.document.rotation_deg)

    # -160 and 200 are the same rotation; compare matrices, not the numbers.
    assert np.allclose(rotation_matrix(window.document.rotation_deg),
                       rotation_matrix([200.0, 0.0, 0.0]), atol=1e-9)


def test_full_turn_in_twelve_commits_returns_to_start_orientation(window):
    window.reload = lambda: None
    for _ in range(12):
        base = window.document.rotation_deg
        window._on_object_transformed(0, [0.0, 0.0, 0.0], [30.0, 0.0, 0.0])
        assert window.document.rotation_deg != base or base == [0.0, 0.0, 0.0]
    assert np.allclose(rotation_matrix(window.document.rotation_deg), np.eye(3), atol=1e-9)
    panel_rotate = [window.object_panel.rotate_rows[axis].value() for axis in ('X', 'Y', 'Z')]
    assert np.allclose(rotation_matrix(panel_rotate), np.eye(3), atol=1e-9)


def test_rotate_row_spans_the_full_range_with_wrap(application):
    row = AxisRow('X', minimum=-180, maximum=180, suffix=' deg',
                  nudges=(-45, -5, 5, 45), nudge_unit='degrees', wrap=True)
    row.set_value(180.0)
    assert row.value() == pytest.approx(180.0)
    row.set_value(-180.0)
    assert row.value() == pytest.approx(180.0)
    row.nudge(45.0)  # 180 + 45 = 225, wraps to -135
    assert row.value() == pytest.approx(-135.0)
    row.set_value(170.0)
    row.nudge(45.0)  # the nudge-button repro: 215 wraps to -145, not clamped to 180
    assert row.value() == pytest.approx(-145.0)


def test_translation_still_clamps_to_its_range(application):
    row = AxisRow('X', minimum=-50, maximum=50, suffix=' mm', nudges=(-1.0, 1.0), nudge_unit='mm')
    row.set_value(999.0)
    assert row.value() == 50.0
    row.set_value(-999.0)
    assert row.value() == -50.0
    row.nudge(1000.0)
    assert row.value() == 50.0


# ---- BUG 2: an abandoned preview must not survive a background job -------

def test_preview_transform_is_cleared_before_compute_attachments_runs(window, application):
    actor = window.scene.actors['model']
    assert actor.GetUserTransform() is None

    # A drag that previews and never commits -- the sticky-drag bug made this
    # common. The document never sees it.
    window._on_object_preview_transformed(0, [0.0, 0.0, 0.0], [45.0, 0.0, 0.0])
    assert not np.allclose(_actor_matrix(actor), np.eye(4))
    assert window.document.rotation_deg == [0.0, 0.0, 0.0]

    window.compute_attachments()
    drain(window, application, stages=('attachments',))

    # No long-running operation may start while a preview transform is
    # outstanding: compute_attachments clears it before it submits the job,
    # so the actor the user is looking at agrees with the document again.
    assert np.allclose(_actor_matrix(actor), np.eye(4))
    assert window.document.rotation_deg == [0.0, 0.0, 0.0]


# ---- VM-099: a second ring must not snap the part back --------------------

def _drag_x_then_y_then_z(window, index):
    for delta in ([30.0, 0.0, 0.0], [0.0, 45.0, 0.0], [0.0, 0.0, 60.0]):
        window._on_object_transformed(index, [0.0, 0.0, 0.0], delta)


def _expected_after_xyz(start):
    return (rotation_matrix([0.0, 0.0, 60.0]) @ rotation_matrix([0.0, 45.0, 0.0])
            @ rotation_matrix([30.0, 0.0, 0.0]) @ rotation_matrix(start))


@pytest.mark.parametrize('mode', ['relative', 'absolute'])
def test_ring_drags_on_three_axes_compose_in_either_motion_mode(window, mode):
    window.reload = lambda: None
    window._set_motion_mode(mode)
    window.object_panel.set_snap_angle(15.0)
    start = [10.0, 20.0, 0.0]
    window.document.set_orientation(start, [4.0, -3.0], 7.5)
    _drag_x_then_y_then_z(window, 0)
    assert np.allclose(rotation_matrix(window.document.rotation_deg),
                       _expected_after_xyz(start), atol=1e-9)
    # Neither the offset nor the lift is reset by a rotation.
    assert window.document.center_offset_mm == pytest.approx([4.0, -3.0])
    assert window.document.model_lift_mm == pytest.approx(7.5)


@pytest.mark.parametrize('mode', ['relative', 'absolute'])
def test_an_added_part_composes_its_ring_drags_too(window, mode):
    window.reload = lambda: None
    window._set_motion_mode(mode)
    window.duplicate_object(0, 1)
    window.document.set_extra_model_pose(0, rotate=[0.0, 0.0, 25.0], center_offset=[12.0, 5.0],
                                         lift_mm=6.0)
    _drag_x_then_y_then_z(window, 1)
    spec = window.document.extra_models[0]
    assert np.allclose(rotation_matrix(spec['rotate']), _expected_after_xyz([0.0, 0.0, 25.0]),
                       atol=1e-9)
    assert list(spec['center_offset']) == pytest.approx([12.0, 5.0])
    assert spec['lift_mm'] == pytest.approx(6.0)


def test_only_the_delta_is_snapped_not_the_resulting_angles(window):
    window.reload = lambda: None
    window.object_panel.set_snap_angle(15.0)
    window.document.set_orientation([7.0, 0.0, 0.0], [0.0, 0.0], 5.0)
    window._on_object_transformed(0, [0.0, 0.0, 0.0], [0.0, 0.0, 31.0])
    # 31 snaps to 30; the 7 degrees already there are not rounded to 0 or 15.
    assert window.document.rotation_deg == pytest.approx([7.0, 0.0, 30.0])


def test_a_drag_on_an_auto_oriented_part_starts_from_the_found_orientation(window):
    window.reload = lambda: None
    window.object_panel.set_snap_angle(0.0)
    window.document.rotation_deg = 'auto'
    window.document.placement.rotation_deg = [90.0, 0.0, 0.0]
    window._on_object_transformed(0, [0.0, 0.0, 0.0], [0.0, 30.0, 0.0])
    assert window.document.rotation_deg != 'auto'
    assert np.allclose(rotation_matrix(window.document.rotation_deg),
                       rotation_matrix([0.0, 30.0, 0.0]) @ rotation_matrix([90.0, 0.0, 0.0]),
                       atol=1e-9)


def test_a_multi_select_edit_turns_every_part_by_the_same_world_rotation(window):
    window.reload = lambda: None
    window.duplicate_object(0, 1)
    window.document.set_extra_model_pose(0, rotate=[90.0, 0.0, 0.0])
    window._write_pose(0, {'rotate': [0.0, 0.0, 30.0], 'center_offset': list(
        window.document.center_offset_mm), 'lift_mm': window.document.model_lift_mm,
        'applies_to': [0, 1]})
    assert window.document.rotation_deg == pytest.approx([0.0, 0.0, 30.0])
    assert np.allclose(rotation_matrix(window.document.extra_models[0]['rotate']),
                       rotation_matrix([0.0, 0.0, 30.0]) @ rotation_matrix([90.0, 0.0, 0.0]),
                       atol=1e-9)


def test_panel_nudges_still_step_the_displayed_angle(window):
    window.reload = lambda: None
    window.document.set_orientation([30.0, 0.0, 0.0], [0.0, 0.0], 5.0)
    window._sync_object_pose(0)
    window.object_panel.rotate_rows['Y'].nudge(45.0)
    window.apply_object_pose()
    assert window.document.rotation_deg == pytest.approx([30.0, 45.0, 0.0])


def test_a_pose_commit_does_not_reframe_the_camera(window):
    """Only an open, a new plate or an added part frames the view."""
    window.reload = lambda: None
    assert window._frame_next_model is False
    window._on_object_transformed(0, [0.0, 0.0, 0.0], [0.0, 0.0, 30.0])
    assert window._frame_next_model is False
    window.new_project()
    assert window._frame_next_model is True


def test_a_composed_pose_survives_save_and_reopen(window, application, tmp_path):
    window.reload = lambda: None
    window.object_panel.set_snap_angle(0.0)
    window.duplicate_object(0, 1)
    window.document.set_orientation([0.0, 0.0, 0.0], [-15.0, 0.0], 6.0)
    window.document.set_extra_model_pose(0, center_offset=[15.0, 0.0])
    _drag_x_then_y_then_z(window, 0)
    _drag_x_then_y_then_z(window, 1)
    primary = list(window.document.rotation_deg)
    extra = list(window.document.extra_models[0]['rotate'])
    project = tmp_path / 'posed.voxmil'
    window.document.save(project)

    reopened = MainWindow(small_settings(), None, headless=True)
    try:
        reopened.open_project(project, extract_dir=tmp_path / 'extracted')
        assert reopened.document.rotation_deg == pytest.approx(primary)
        assert reopened.document.center_offset_mm == pytest.approx([-15.0, 0.0])
        assert reopened.document.model_lift_mm == pytest.approx(6.0)
        assert list(reopened.document.extra_models[0]['rotate']) == pytest.approx(extra)
        assert list(reopened.document.extra_models[0]['center_offset']) == pytest.approx([15.0, 0.0])
    finally:
        reopened.jobs.cancel_all()
        reopened.jobs.wait(5000)
        reopened.document.dirty = False
        reopened.close()
