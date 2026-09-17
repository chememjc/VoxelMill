"""Headless regression tests for the build-volume orientation cue."""

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest

pytest.importorskip('PySide6')
vtk = pytest.importorskip('vtkmodules.all')

from voxelmill.gui.viewport import Scene


def test_build_volume_has_colored_oriented_edges():
    scene = Scene()
    scene.show_build_volume({'printer': {'build_mm': [100.0, 80.0, 165.0]}})

    data = scene._plate.GetMapper().GetInput()
    assert data.GetNumberOfPoints() == 8
    assert data.GetNumberOfLines() == 12

    colors = data.GetCellData().GetScalars()
    assert colors.GetNumberOfTuples() == 12
    rgb = np.asarray([colors.GetTuple3(i) for i in range(12)])
    assert np.sum(np.all(rgb == [0, 255, 0], axis=1)) == 1
    assert np.sum(np.all(rgb == [255, 0, 0], axis=1)) == 11

    # Cell zero is the green front-bottom edge: (-X,-Y,0) -> (+X,-Y,0).
    vtk_ids = vtk.vtkIdList()
    data.GetLines().InitTraversal()
    data.GetLines().GetNextCell(vtk_ids)
    endpoints = np.asarray([data.GetPoint(vtk_ids.GetId(i)) for i in range(vtk_ids.GetNumberOfIds())])
    np.testing.assert_allclose(endpoints, [[-50.0, -40.0, 0.0], [50.0, -40.0, 0.0]])
    assert scene._plate.GetProperty().GetLineWidth() > 1.0
