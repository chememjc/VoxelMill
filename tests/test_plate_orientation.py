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

    red = [a for a in scene._plate if a.GetProperty().GetColor() == (1.0, 0.0, 0.0)]
    green = [a for a in scene._plate if a.GetProperty().GetColor() == (0.0, 1.0, 0.0)]
    assert len(green) == 1
    assert len(red) == 11  # every other edge of the box
    for actor in scene._plate:
        assert actor.GetProperty().GetLineWidth() > 1.0

    # The green edge is the front-bottom one: (-X,-Y,0) -> (+X,-Y,0), because
    # the front of the printer is the -Y face.
    np.testing.assert_allclose(_edges(green[0])[0],
                               [[-50.0, -40.0, 0.0], [50.0, -40.0, 0.0]])

    # Twelve distinct edges over eight corners.
    segments = set()
    for actor in scene._plate:
        for path in _edges(actor):
            for first, second in zip(path, path[1:]):
                segments.add(frozenset((tuple(first), tuple(second))))
    assert len(segments) == 12
    corners = {point for segment in segments for point in segment}
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
        data = actor.GetMapper().GetInput()
        assert data.GetNumberOfLines() == 1
        assert data.GetNumberOfPoints() == 2

    # Two-point cells only. A polyline stops drawing once it carries more than
    # three segments under a guest's generic OpenGL, closed or not, and a
    # polydata with several cells draws only its first. This is the one shape
    # observed to survive.
    for path in (path for actor in scene._plate for path in _edges(actor)):
        assert len(path) == 2, path

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
    # Six faces and twelve bevels are quads, eight corners are triangles, and
    # every perimeter segment is its own two-point actor.
    assert len(outlines) == (6 + 12) * 4 + 8 * 3
    for actor in outlines:
        data = actor.GetMapper().GetInput()
        assert data.GetNumberOfLines() == 1
        assert data.GetNumberOfPoints() == 2
