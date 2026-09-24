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
