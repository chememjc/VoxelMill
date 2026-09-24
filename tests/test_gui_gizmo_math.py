"""Pure interaction math behind the transform gizmo -- no render window needed.

``axis_translation`` and ``ring_angle`` turn a screen pick ray into "how far
along this axis" / "what angle around this axis"; they are what a drag
actually computes each frame, kept free of VTK so the degenerate cases (ray
parallel to the axis, ray parallel to the ring's plane, ray landing exactly on
the rotation axis) are cheap to check exhaustively.
"""
from __future__ import annotations



import numpy as np
import pytest

pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules.all')

from voxelmill.gui.gizmo import axis_translation, quantize, ring_angle, wrap_angle


# ---- axis_translation -----------------------------------------------------

def test_axis_translation_of_a_perpendicular_ray_is_the_crossing_point():
    # A ray parallel to Z, offset to x=3, crosses the X axis at x=3.
    s = axis_translation((3.0, 0.0, -50.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
    assert s == pytest.approx(3.0)


def test_axis_translation_reports_signed_distance_along_the_axis():
    s = axis_translation((-4.0, 0.0, -50.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
    assert s == pytest.approx(-4.0)


def test_axis_translation_is_none_when_the_ray_is_parallel_to_the_axis():
    # Same direction as the axis: every point on the axis is equally close.
    assert axis_translation((0.0, 5.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)) is None
    # Anti-parallel counts too.
    assert axis_translation((0.0, 5.0, 0.0), (-2.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)) is None


def test_axis_translation_is_none_for_degenerate_zero_length_inputs():
    assert axis_translation((0, 0, 0), (0, 0, 0), (0, 0, 0), (1, 0, 0)) is None
    assert axis_translation((0, 0, 0), (0, 0, 1), (0, 0, 0), (0, 0, 0)) is None


def test_axis_translation_never_returns_nan():
    result = axis_translation((0, 0, 0), (1, 0, 0), (0, 0, 0), (1, 0, 0))
    assert result is None  # parallel, not NaN


# ---- ring_angle -------------------------------------------------------

def test_ring_angle_reads_zero_along_the_reference_direction():
    angle = ring_angle((2.0, 0.0, -5.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    assert angle == pytest.approx(0.0, abs=1e-9)


def test_ring_angle_turns_a_quarter_turn_between_perpendicular_points():
    zero = ring_angle((2.0, 0.0, -5.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    quarter = ring_angle((0.0, 2.0, -5.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    assert quarter - zero == pytest.approx(np.pi / 2)


def test_ring_angle_is_none_when_the_ray_is_parallel_to_the_ring_plane():
    # Ring normal is Z (lies in the XY plane); a ray running along X never
    # crosses that plane at a single point.
    assert ring_angle((0.0, 0.0, 5.0), (1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)) is None


def test_ring_angle_is_none_when_the_plane_is_behind_the_ray():
    # Ray origin already past the plane, moving further away from it.
    assert ring_angle((2.0, 0.0, 5.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)) is None


def test_ring_angle_is_none_exactly_on_the_rotation_axis():
    # The ray crosses the plane right at its center: no in-plane direction.
    assert ring_angle((0.0, 0.0, -10.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)) is None


def test_ring_angle_is_none_for_a_zero_length_direction_or_normal():
    assert ring_angle((0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 1)) is None
    assert ring_angle((0, 0, 0), (0, 0, 1), (0, 0, 0), (0, 0, 0)) is None


# ---- wrap_angle / quantize ----------------------------------------------

def test_wrap_angle_keeps_a_swept_delta_signed_across_the_branch_cut():
    # Sweeping 270 degrees the "short way" reads as -90 degrees, not +270.
    assert wrap_angle(3 * np.pi / 2) == pytest.approx(-np.pi / 2)
    assert wrap_angle(-3 * np.pi / 2) == pytest.approx(np.pi / 2)
    assert wrap_angle(0.1) == pytest.approx(0.1)


def test_quantize_rounds_to_the_nearest_step():
    assert quantize(7.0, 5.0) == pytest.approx(5.0)
    assert quantize(8.0, 5.0) == pytest.approx(10.0)
    assert quantize(-7.0, 5.0) == pytest.approx(-5.0)


def test_quantize_with_zero_step_means_no_snap():
    assert quantize(7.3, 0.0) == pytest.approx(7.3)
    assert quantize(7.3, None) == pytest.approx(7.3)
