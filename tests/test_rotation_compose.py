"""Euler angles in and out of ``rotation_matrix``, and world-axis composition.

A gizmo ring turns a part about a fixed world axis. Adding that delta to the
stored X/Y/Z angle is only right while the axes applied before it are zero,
because ``rotation_matrix`` is ``Rz @ Ry @ Rx``; the editor composes matrices
instead and needs an exact way back to angles, including at Y = +-90.
"""
from __future__ import annotations

import numpy as np
import pytest

from voxelmill.geometry import compose_rotation_deg, matrix_to_euler_deg, rotation_matrix


def _random_angles(rng, count):
    angles = rng.uniform(-180.0, 180.0, size=(count, 3))
    # A third of the cases sit on or within a hair of the gimbal-lock poles,
    # at scales from exactly on it out to a few degrees away.
    near = np.arange(count) % 3 == 0
    poles = rng.choice([90.0, -90.0], size=near.sum())
    jitter = rng.normal(0.0, 1.0, size=near.sum()) * 10.0 ** rng.uniform(-14, 0, size=near.sum())
    jitter[::4] = 0.0
    angles[near, 1] = poles + jitter
    return angles


def test_matrix_to_euler_inverts_rotation_matrix_everywhere_including_the_poles():
    rng = np.random.default_rng(20260928)
    for angles in _random_angles(rng, 3000):
        matrix = rotation_matrix(angles)
        recovered = matrix_to_euler_deg(matrix)
        assert np.allclose(rotation_matrix(recovered), matrix, atol=1e-7), angles
        assert -90.0 <= recovered[1] <= 90.0


def test_away_from_the_poles_the_angles_themselves_come_back():
    rng = np.random.default_rng(7)
    for angles in rng.uniform([-179, -89, -179], [179, 89, 179], size=(500, 3)):
        assert matrix_to_euler_deg(rotation_matrix(angles)) == pytest.approx(list(angles), abs=1e-9)


def test_composition_is_the_world_axis_delta_applied_after_the_pose():
    rng = np.random.default_rng(11)
    for base, delta in zip(_random_angles(rng, 500), _random_angles(rng, 500)):
        composed = compose_rotation_deg(delta, base)
        assert np.allclose(rotation_matrix(composed),
                           rotation_matrix(delta) @ rotation_matrix(base), atol=1e-7)
        assert all(-180.0 < angle <= 180.0 for angle in composed)


def test_a_second_axis_is_not_simply_added_to_its_angle():
    """X 90 then Y 90 is not the Euler triple (90, 90, 0) -- that is Y after X."""
    after_x = compose_rotation_deg([90.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    assert after_x == [90.0, 0.0, 0.0]
    then_z = compose_rotation_deg([0.0, 0.0, 90.0], after_x)
    assert then_z == [90.0, 0.0, 90.0]  # Z is applied last, so this one does add
    then_x = compose_rotation_deg([90.0, 0.0, 0.0], [0.0, 0.0, 90.0])
    assert then_x != [90.0, 0.0, 90.0]
    assert np.allclose(rotation_matrix(then_x),
                       rotation_matrix([90.0, 0.0, 0.0]) @ rotation_matrix([0.0, 0.0, 90.0]))


def test_whole_degrees_stay_whole_and_a_zero_delta_changes_nothing():
    assert compose_rotation_deg([0.0, 0.0, 45.0], [0.0, 0.0, 0.0]) == [0.0, 0.0, 45.0]
    assert compose_rotation_deg([30.0, 0.0, 0.0], [170.0, 0.0, 0.0]) == [-160.0, 0.0, 0.0]
    # An equivalent but different triple must not replace the stored one.
    assert compose_rotation_deg([0.0, 0.0, 0.0], [180.0, 0.0, 180.0]) == [180.0, 0.0, 180.0]
