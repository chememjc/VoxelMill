"""Navigation cube picking and orbit math -- no render window required.

Face/corner/arrow classification is a function of a point in cube space (or a
normalized marker-viewport coordinate for the orbit arrows). Label placement
uses actor bounds after ``Update``, still without a display.
"""
from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest

pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules.all')

from voxelmill.gui.camera import (
    ARROW_TURNS, FACE_VIEWS, VIEWS, named_view_near, orthonormal_up, rotate_view_90,
)
from voxelmill.gui.viewport import (
    CUBE_FACE_HALF, cube_arrow_at, cube_face_at, cube_hit_at, navigation_cube_prop,
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


def test_edge_and_centre_picks_are_refused():
    assert cube_hit_at((0.40, 0.40, 0.0)) is None
    assert cube_hit_at((0.0, 0.0, 0.0)) is None
    assert cube_face_at((0.45, 0.45, 0.45)) is None


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
    assert cube_hit_at((0.92, 0.50)) == ('arrow', 'right')
    assert cube_hit_at((0.08, 0.50)) == ('arrow', 'left')
    assert cube_hit_at((0.50, 0.92)) == ('arrow', 'up')
    assert cube_hit_at((0.50, 0.08)) == ('arrow', 'down')
    assert cube_arrow_at(0.50, 0.50) is None
    assert cube_arrow_at(0.96, 0.96) is None
    assert cube_hit_at((0.70, 0.0, 0.0)) == ('arrow', 'right')
    assert cube_hit_at((0.0, 0.0, -0.70)) == ('arrow', 'down')
