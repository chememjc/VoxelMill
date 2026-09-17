"""Per-contact support parameter routing and connector geometry."""

import numpy as np
import manifold3d as m
import pytest

from voxelmill.config import resolve_settings
from voxelmill.contact_parameters import normalize_contact_parameters, parameters_for_contact
from voxelmill.contracts import VoxelMillError
from voxelmill.geometry import manifold_triangles
from voxelmill.support_segments import tip_segment
from voxelmill.supports import build_column_field, route_contacts


def scene():
    settings = resolve_settings(overrides={'support': {'base_type': 'none'}})
    solid = m.Manifold.cube((30, 30, 4), True).translate((-15, -15, 2))
    triangles = manifold_triangles(solid).astype(np.float32)
    bounds = np.asarray(solid.bounding_box()).reshape(2, 3)
    field = build_column_field(triangles, bounds, settings, pitch_mm=.5)
    return settings, field


def test_normalization_is_deep_and_exact_coordinate_lookup():
    settings, _ = scene()
    records = normalize_contact_parameters([
        {'position_mm': [0, 0, 4], 'parameters': {'pillar_diameter_mm': .8}},
    ], settings)
    assert parameters_for_contact(records, [0.0000004, 0, 4]) == {'pillar_diameter_mm': .8}
    assert settings['support']['pillar_diameter_mm'] != .8
    with pytest.raises(VoxelMillError):
        normalize_contact_parameters([
            {'position_mm': [0, 0, 4], 'parameters': {'spacing_mm': 2}},
        ], settings)


def test_two_contacts_can_have_independent_radius_and_tip_geometry():
    settings, field = scene()
    points = [[-10, 0, 3.9], [-5, 0, 3.9]]
    overrides = [
        {'position_mm': points[0], 'parameters': {'pillar_diameter_mm': .6}},
        {'position_mm': points[1], 'parameters': {'pillar_diameter_mm': 1.6}},
    ]
    plan, _ = route_contacts(points, field, settings, contact_parameters=overrides)
    assert plan.metrics['contact_parameters'] == {'authored': 2, 'matched': 2, 'unmatched': 0}
    edges = [edge for edge in plan.graph.edges if edge.kind == 'vertical']
    assert sorted(edge.radius_mm for edge in edges) == pytest.approx([.3, .8])
    assert len(plan.solids) == 4  # shaft and tip per contact
    assert plan.graph.overrides[0]['reason'] == 'effective_parameters'


def test_unmatched_override_is_reported_and_not_applied():
    settings, field = scene()
    plan, _ = route_contacts([[-10, 0, 3.9]], field, settings, contact_parameters=[
        {'position_mm': [7, 7, 4], 'parameters': {'pillar_diameter_mm': .6}},
    ])
    assert plan.metrics['contact_parameters'] == {'authored': 1, 'matched': 0, 'unmatched': 1}
    assert any(d.code == 'contact_parameters_unmatched' for d in plan.diagnostics)


def test_cone_cylinder_and_break_point_are_closed_positive_solids():
    start, end = [0, 0, 0], [0, 0, 2]
    cone = tip_segment(start, end, .5, .2, 'cone')
    cylinder = tip_segment(start, end, .5, .2, 'cylinder')
    ball = tip_segment(start, end, .5, .2, 'cone', .8)
    for solid in (cone, cylinder, ball):
        assert solid.status() == m.Error.NoError
        assert solid.volume() > 0
        assert len(solid.decompose()) == 1
    assert cylinder.volume() < cone.volume()
    assert ball.volume() > cone.volume()
