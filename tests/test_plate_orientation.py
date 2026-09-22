"""Headless regression tests for the build-volume orientation cue."""

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest

pytest.importorskip('PySide6')
vtk = pytest.importorskip('vtkmodules.all')

from voxelmill.gui.viewport import Scene

BUILD = {'printer': {'build_mm': [100.0, 80.0, 165.0]}}


def _edges(actor):
    """Every line cell of an actor, as endpoint coordinate pairs."""
    data = actor.GetMapper().GetInput()
    lines = data.GetLines()
    lines.InitTraversal()
    ids = vtk.vtkIdList()
    found = []
    while lines.GetNextCell(ids):
        found.append([data.GetPoint(ids.GetId(i)) for i in range(ids.GetNumberOfIds())])
    return found


def test_build_volume_has_colored_oriented_edges():
    scene = Scene()
    scene.show_build_volume(BUILD)

    assert len(scene._plate) == 2
    by_colour = {tuple(actor.GetProperty().GetColor()): actor for actor in scene._plate}
    assert set(by_colour) == {(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)}
    red, green = by_colour[(1.0, 0.0, 0.0)], by_colour[(0.0, 1.0, 0.0)]

    red_edges, green_edges = _edges(red), _edges(green)
    assert len(red_edges) == 11
    assert len(green_edges) == 1
    # Twelve edges of a box, over its eight corners, however they are split.
    for actor in scene._plate:
        assert actor.GetMapper().GetInput().GetNumberOfPoints() == 8
        assert actor.GetProperty().GetLineWidth() > 1.0

    # The green edge is the front-bottom one: (-X,-Y,0) -> (+X,-Y,0), because
    # the front of the printer is the -Y face.
    np.testing.assert_allclose(green_edges[0], [[-50.0, -40.0, 0.0], [50.0, -40.0, 0.0]])
    assert all(len(edge) == 2 for edge in red_edges)

    corners = {tuple(point) for edge in red_edges + green_edges for point in edge}
    assert len(corners) == 8
    assert all(abs(x) == 50.0 and abs(y) == 40.0 and z in (0.0, 165.0)
               for x, y, z in corners)


def test_build_volume_avoids_per_cell_scalars():
    """Flat colours, because per-cell scalars on lines did not survive.

    Drawn as one actor carrying twelve per-cell colours, the box collapsed to
    its single first edge under the generic software OpenGL a VirtualBox guest
    falls back to. Two flat-coloured actors are the construct the navigation
    cube's outline already proves renders there.
    """
    scene = Scene()
    scene.show_build_volume(BUILD)
    for actor in scene._plate:
        data = actor.GetMapper().GetInput()
        assert data.GetCellData().GetScalars() is None
        assert data.GetPointData().GetScalars() is None


def test_showing_the_build_volume_twice_replaces_it():
    """Both actors have to be removed, or the box doubles on every settings change."""
    scene = Scene()
    scene.show_build_volume(BUILD)
    first = list(scene._plate)
    before = scene.renderer.GetActors().GetNumberOfItems()

    scene.show_build_volume({'printer': {'build_mm': [120.0, 60.0, 200.0]}})
    assert scene.renderer.GetActors().GetNumberOfItems() == before
    assert all(actor not in scene._plate for actor in first)
    green = [a for a in scene._plate if a.GetProperty().GetColor() == (0.0, 1.0, 0.0)][0]
    np.testing.assert_allclose(_edges(green)[0],
                               [[-60.0, -30.0, 0.0], [60.0, -30.0, 0.0]])
