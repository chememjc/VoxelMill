"""Geometry evidence for the open triangulated support base."""
import numpy as np
import pytest
import manifold3d as m

from voxelmill.bases import build_base, triangulation_edges
from voxelmill.config import resolve_settings
from voxelmill.contracts import CancellationToken, Canceled


def triangle_settings(**changes):
    return resolve_settings(overrides={'support': {'base_type': 'triangle', **changes}})


def test_delaunay_triangle_base_has_open_bays_and_one_connected_solid():
    feet = np.array([[-10, -10], [-10, 10], [10, -10], [10, 10]], dtype=float)
    result = build_base(feet, triangle_settings(base_touch_diameter_mm=2.4,
                                                base_thickness_mm=.8,
                                                base_strut_width_mm=.8), .6)
    solid, record = result['solid'], result['record']
    assert record['triangle_cells'] == 2
    assert record['triangle_edges'] == record['skeleton_edges'] == 5
    assert record['connected_components'] == len(solid.decompose()) == 1
    assert record['open_area_fraction'] > .2
    assert len(solid.slice(.4).to_polygons()) > 1
    for x, y in feet:
        contact = m.Manifold.cylinder(.8, .6, .6, 24).translate((x, y, 0))
        assert (contact - solid).volume() == pytest.approx(0, abs=1e-8)


@pytest.mark.parametrize('feet', [
    [[0, 0]], [[0, 0], [0, 0]], [[0, 0], [5, 0]],
    [[0, 0], [5, 0], [10, 0]],
])
def test_degenerate_triangle_foot_sets_fall_back_to_a_connected_tree(feet):
    edges, cells = triangulation_edges(np.asarray(feet, dtype=float))
    assert cells == 0
    result = build_base(feet, triangle_settings(base_touch_diameter_mm=2.4,
                                                base_thickness_mm=.8,
                                                base_strut_width_mm=.8), .6)
    assert result['record']['triangle_cells'] == 0
    assert result['record']['triangle_edges'] == len(np.unique(feet, axis=0)) - 1
    assert result['record']['connected_components'] == 1


def test_triangle_edges_are_invariant_to_input_order():
    points = np.array([[-3, -2], [4, -1], [5, 5], [-2, 6], [0, 1]], dtype=float)
    first = triangulation_edges(points)
    second = triangulation_edges(points[::-1])
    assert first == second


def test_triangle_base_honours_cancellation():
    token = CancellationToken()
    token.cancel()
    with pytest.raises(Canceled):
        build_base([[0, 0], [5, 0], [2, 4]], triangle_settings(), .6, cancel=token)
