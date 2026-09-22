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

    # Twelve edges of a box, one actor each; see the one-cell test below.
    assert len(scene._plate) == 12
    red = [a for a in scene._plate if a.GetProperty().GetColor() == (1.0, 0.0, 0.0)]
    green = [a for a in scene._plate if a.GetProperty().GetColor() == (0.0, 1.0, 0.0)]
    assert len(red) == 11
    assert len(green) == 1
    for actor in scene._plate:
        assert actor.GetProperty().GetLineWidth() > 1.0

    # The green edge is the front-bottom one: (-X,-Y,0) -> (+X,-Y,0), because
    # the front of the printer is the -Y face.
    np.testing.assert_allclose(_edges(green[0])[0],
                               [[-50.0, -40.0, 0.0], [50.0, -40.0, 0.0]])

    red_edges = [edge for actor in red for edge in _edges(actor)]
    assert len(red_edges) == 11
    assert all(len(edge) == 2 for edge in red_edges)
    corners = {tuple(point) for edge in red_edges + _edges(green[0]) for point in edge}
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
    assert len(scene._plate) == 12
    green = [a for a in scene._plate if a.GetProperty().GetColor() == (0.0, 1.0, 0.0)][0]
    np.testing.assert_allclose(_edges(green)[0],
                               [[-60.0, -30.0, 0.0], [60.0, -30.0, 0.0]])


def test_every_line_actor_holds_exactly_one_cell():
    """One line cell per actor, everywhere lines are drawn.

    A polydata carrying several line cells renders only its first under the
    generic OpenGL a VirtualBox guest falls back to: the build volume showed
    one edge and the navigation cube's outlines vanished, while triangles kept
    drawing. The counts here are small enough that one actor per cell costs
    nothing, and it keeps the pixel-constant width a tube would trade away.
    """
    from voxelmill.gui.viewport import navigation_cube_prop

    scene = Scene()
    scene.show_build_volume(BUILD)
    assert len(scene._plate) == 12
    for actor in scene._plate:
        assert actor.GetMapper().GetInput().GetNumberOfLines() == 1

    cube = navigation_cube_prop()
    parts = cube.GetParts()
    parts.InitTraversal()
    outlines = []
    while True:
        part = parts.GetNextProp()
        if part is None:
            break
        if getattr(part, 'nav_role', None) == 'outline':
            outlines.append(part)
    # Six faces, twelve bevels, eight corners.
    assert len(outlines) == 26
    for actor in outlines:
        assert actor.GetMapper().GetInput().GetNumberOfLines() == 1
