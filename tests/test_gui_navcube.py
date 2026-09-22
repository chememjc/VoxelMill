"""Navigation cube picking and orbit math -- no render window required.

Face/edge/corner/arrow classification is a function of a point in cube space
(or a normalized marker-viewport coordinate for the orbit arrows and roll
buttons). Label placement uses actor bounds after ``Update``, still without a
display, and so does the check that facet outlines carry no diagonals.
"""
from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest

pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules.all')

from voxelmill.gui.camera import (
    ARROW_TURNS, FACE_VIEWS, NAV_STEP_DEG, ROLL_TURNS, VIEWS, named_view_near,
    orthonormal_up, roll_view_by, rotate_view_90, rotate_view_by,
)
from voxelmill.gui.viewport import (
    ARROW_CENTRES_UV, CUBE_FACE_HALF, CUBE_HALF, CUBE_PAD_HALF, ROLL_CENTRES_UV,
    chamfered_cube_triangles, cube_arrow_at, cube_face_at, cube_hit_at, cube_roll_at,
    navigation_cube_prop, navigation_roll_actors,
)


def _unit(vector):
    vector = np.asarray(vector, dtype=float)
    return vector / np.linalg.norm(vector)


def test_six_faces_still_map_uniquely():
    assert set(FACE_VIEWS) == {'Front', 'Back', 'Left', 'Right', 'Top', 'Bottom'}
    assert len(set(FACE_VIEWS.values())) == 6
    assert set(FACE_VIEWS.values()) <= set(VIEWS)
    mapped = {}
    for position, face in (
            ((0.5, 0.1, 0.1), 'Right'), ((-0.5, 0, 0), 'Left'),
            ((0, 0.5, 0), 'Back'), ((0, -0.5, 0), 'Front'),
            ((0, 0, 0.5), 'Top'), ((0, 0, -0.5), 'Bottom')):
        assert cube_face_at(position) == face
        hit = cube_hit_at(position)
        assert hit == ('face', face)
        mapped[face] = FACE_VIEWS[face]
    assert len(set(mapped.values())) == 6
    assert cube_face_at((0.01, 0.01, 0.01)) is None
    assert cube_hit_at((0.01, 0.01, 0.01)) is None
    assert cube_hit_at((np.nan, 0, 0)) is None


def test_eight_corners_map_to_eight_distinct_views():
    hits = {}
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                position = (0.40 * sx, 0.40 * sy, 0.40 * sz)
                hit = cube_hit_at(position)
                assert hit is not None and hit[0] == 'corner', (position, hit)
                name = hit[1]
                assert name.startswith('iso_') and name in VIEWS
                assert name not in hits
                hits[name] = position
                direction, up = VIEWS[name]
                expected = np.array([sx, sy, sz], dtype=float)
                assert np.allclose(_unit(direction), _unit(expected))
                orthonormal_up(direction, up)
    assert len(hits) == 8
    # Home iso stays the shallower front-right-top pose, not a cube-corner name.
    assert 'iso' in VIEWS and 'iso' not in hits
    assert cube_hit_at((0.4, -0.4, 0.4)) == ('corner', 'iso_+-+')


def test_centre_picks_are_refused_but_bevels_are_not():
    # A bevel used to fall through to None, which is why clicking a 45° side
    # did nothing while its corners and faces both worked.
    assert cube_hit_at((0.40, 0.40, 0.0)) == ('edge', 'edge_++0')
    assert cube_hit_at((0.0, 0.0, 0.0)) is None
    assert cube_hit_at((0.01, 0.01, 0.01)) is None
    assert cube_face_at((0.45, 0.45, 0.45)) is None
    assert cube_face_at((0.40, 0.40, 0.0)) is None


def test_twelve_bevels_map_to_twelve_distinct_views():
    hits = {}
    for axes in ((0, 1), (0, 2), (1, 2)):
        for sa in (-1.0, 1.0):
            for sb in (-1.0, 1.0):
                position = [0.0, 0.0, 0.0]
                position[axes[0]], position[axes[1]] = 0.40 * sa, 0.40 * sb
                hit = cube_hit_at(tuple(position))
                assert hit is not None and hit[0] == 'edge', (position, hit)
                name = hit[1]
                assert name.startswith('edge_') and name in VIEWS
                assert name not in hits
                hits[name] = tuple(position)
                direction, up = VIEWS[name]
                assert np.allclose(_unit(direction), _unit(position))
                orthonormal_up(direction, up)
    assert len(hits) == 12
    assert cube_hit_at((0.0, 0.40, -0.40)) == ('edge', 'edge_0+-')


def test_facet_outlines_carry_no_diagonals():
    triangles, colors, loops = chamfered_cube_triangles()
    assert len(triangles) == len(colors) == 6 * 2 + 12 * 2 + 8
    # One closed perimeter per facet: six squares, twelve bevels, eight corners.
    assert len(loops) == 26
    assert sorted({len(loop) for loop in loops}) == [3, 4]
    assert sum(len(loop) for loop in loops) == 6 * 4 + 12 * 4 + 8 * 3
    for loop in loops:
        if len(loop) != 4:
            continue
        drawn = {round(float(np.linalg.norm(np.asarray(loop[index])
                                           - np.asarray(loop[(index + 1) % 4]))), 6)
                 for index in range(4)}
        for pair in ((0, 2), (1, 3)):
            diagonal = round(float(np.linalg.norm(np.asarray(loop[pair[0]])
                                                  - np.asarray(loop[pair[1]]))), 6)
            assert diagonal not in drawn, (loop, diagonal)


def test_outlines_stay_just_outside_the_pickable_body():
    _, _, loops = chamfered_cube_triangles()
    points = np.asarray([point for loop in loops for point in loop], dtype=float)
    reach = float(np.abs(points).max())
    # Lifted off its facet so it is visible, still well inside the bounds pad
    # the orientation marker frames.
    assert CUBE_HALF < reach < CUBE_PAD_HALF


def test_labels_sit_inside_their_face_squares():
    cube = navigation_cube_prop()
    parts = cube.GetParts()
    parts.InitTraversal()
    labels = {}
    while True:
        part = parts.GetNextProp()
        if part is None:
            break
        name = getattr(part, 'nav_label', None)
        if name:
            labels[name] = part
    assert set(labels) == set(FACE_VIEWS)
    planes = {
        'Right': 0, 'Left': 0, 'Back': 1, 'Front': 1, 'Top': 2, 'Bottom': 2,
    }
    slack = 1e-6
    for name, actor in labels.items():
        actor.GetMapper().Update()
        bounds = actor.GetBounds()
        axis = planes[name]
        corners = (
            (bounds[0], bounds[2], bounds[4]),
            (bounds[0], bounds[2], bounds[5]),
            (bounds[0], bounds[3], bounds[4]),
            (bounds[0], bounds[3], bounds[5]),
            (bounds[1], bounds[2], bounds[4]),
            (bounds[1], bounds[2], bounds[5]),
            (bounds[1], bounds[3], bounds[4]),
            (bounds[1], bounds[3], bounds[5]),
        )
        for corner in corners:
            in_plane = [corner[i] for i in range(3) if i != axis]
            assert abs(in_plane[0]) <= CUBE_FACE_HALF + slack, (name, corner)
            assert abs(in_plane[1]) <= CUBE_FACE_HALF + slack, (name, corner)


def test_rotating_front_90_lands_on_a_side_view():
    sides = {'left', 'right', 'top', 'bottom'}
    landed = {}
    for turn in ARROW_TURNS:
        direction, up = rotate_view_90(*VIEWS['front'], turn)
        assert abs(float(np.dot(_unit(direction), up))) < 1e-9
        orthonormal_up(direction, up)
        match = named_view_near(direction, up)
        assert match in sides, (turn, match, direction, up)
        landed[turn] = match
    assert set(landed.values()) == sides
    assert landed['right'] == 'right'
    assert landed['left'] == 'left'
    assert landed['up'] == 'top'
    assert landed['down'] == 'bottom'


def test_orbit_arrows_are_view_relative():
    for direction, centre in ARROW_CENTRES_UV.items():
        assert cube_hit_at(centre) == ('arrow', direction)
        assert cube_arrow_at(*centre) == direction
    assert cube_arrow_at(0.50, 0.50) is None
    assert cube_arrow_at(0.96, 0.96) is None
    assert cube_hit_at((0.70, 0.0, 0.0)) == ('arrow', 'right')
    assert cube_hit_at((0.0, 0.0, -0.70)) == ('arrow', 'down')


def test_arrow_zones_clear_the_cube_silhouette():
    """The glyphs sit close to the cube without stealing its own picks.

    ``cube_hit_under`` consults the arrows before it picks the body, so the
    touch zones have to stop outside the silhouette. ``ResetCamera`` frames the
    bounding sphere of the pad, which is what sets that silhouette.
    """
    edge = 0.5 + 0.5 * CUBE_HALF / (CUBE_PAD_HALF * np.sqrt(3.0))
    assert 0.70 < edge < 0.80
    for direction, (cu, cv) in ARROW_CENTRES_UV.items():
        axis = cu if direction in ('left', 'right') else cv
        assert abs(axis - 0.5) > edge - 0.5
    # Nothing on the silhouette itself is claimed by an arrow or a roll button.
    for offset in (0.0, 0.01, 0.02):
        probe = edge - offset
        assert cube_arrow_at(probe, 0.50) is None, probe
        assert cube_roll_at(probe, 0.50) is None, probe


def test_roll_buttons_flank_the_up_arrow():
    for turn, centre in ROLL_CENTRES_UV.items():
        assert cube_roll_at(*centre) == turn
        assert cube_hit_at(centre) == ('roll', turn)
        # Roll wins over the arrows, so it must not reach the up arrow itself.
        assert cube_arrow_at(*centre) is None
    assert ROLL_CENTRES_UV['left'][1] == ARROW_CENTRES_UV['up'][1]
    assert cube_roll_at(*ARROW_CENTRES_UV['up']) is None
    assert cube_hit_at(ARROW_CENTRES_UV['up']) == ('arrow', 'up')
    assert cube_roll_at(0.5, 0.5) is None
    assert len(navigation_roll_actors()) == len(ROLL_TURNS)


def test_arrow_and_roll_steps_are_45_degrees():
    for turn in ARROW_TURNS:
        direction, up = rotate_view_by(*VIEWS['front'], turn)
        assert abs(float(np.dot(_unit(direction), up))) < 1e-9
        angle = np.degrees(np.arccos(
            np.clip(float(np.dot(_unit(direction), _unit(VIEWS['front'][0]))), -1, 1)))
        assert abs(angle - NAV_STEP_DEG) < 1e-6, (turn, angle)
        # A 45° step lands on a named bevel view, so it snaps like a face pick.
        assert named_view_near(direction, up).startswith('edge_')
    for turn in ROLL_TURNS:
        direction, up = roll_view_by(*VIEWS['front'], turn)
        assert np.allclose(_unit(direction), _unit(VIEWS['front'][0]))
        angle = np.degrees(np.arccos(np.clip(
            float(np.dot(up, orthonormal_up(*VIEWS['front']))), -1, 1)))
        assert abs(angle - NAV_STEP_DEG) < 1e-6, (turn, angle)
    # Rolling both ways returns the pose it started from.
    direction, up = roll_view_by(*VIEWS['front'], 'left')
    assert np.allclose(orthonormal_up(*roll_view_by(direction, up, 'right')),
                       orthonormal_up(*VIEWS['front']))
