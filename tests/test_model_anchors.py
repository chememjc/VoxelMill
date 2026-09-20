"""Independent bottom segments for model anchored supports."""
import math

import manifold3d as m
import numpy as np
import pytest

from voxelmill.config import resolve_settings
from voxelmill.geometry import manifold_triangles
from voxelmill.supports import build_column_field, route_contacts


def pedestal_scene():
    # The overhead block is reached through a solid pedestal.  A contact at
    # the center therefore cannot use a vertical plate route, but has printed
    # material immediately below it to which a model anchor can attach.
    solid = (m.Manifold.cube((10, 10, 8), True).translate((0, 0, 4))
             + m.Manifold.cube((20, 20, 2), True).translate((0, 0, 15)))
    triangles = manifold_triangles(solid).astype(np.float32)
    bounds = np.asarray(solid.bounding_box()).reshape(2, 3)
    return triangles, bounds


def routed(**support):
    triangles, bounds = pedestal_scene()
    settings = resolve_settings(overrides={'support': {
        'allow_part_to_part': True,
        'part_to_part_avoidance': 0.0,
        **support,
    }})
    field = build_column_field(triangles, bounds, settings, pitch_mm=.5)
    return route_contacts([[0., 0., 14.]], field, settings)[0], settings


def test_cone_anchor_has_independent_endpoint_and_depth():
    plan, _ = routed(model_anchor_shape='cone', model_anchor_length_mm=1.0,
                     model_anchor_diameter_mm=.55, model_anchor_penetration_mm=.2,
                     break_point_diameter_mm=0.0)
    assert plan.metrics['routing'] == {'vertical': 0, 'branched': 0, 'model_anchor': 1}
    nodes = {n.id: n for n in plan.graph.nodes}
    assert nodes['foot0'].position_mm[2] == pytest.approx(8.0)
    assert nodes['anchor_joint0'].position_mm[2] == pytest.approx(9.0)
    assert nodes['joint0'].position_mm[2] == pytest.approx(12.0)
    assert nodes['contact0'].position_mm[2] == pytest.approx(14.0)
    edge = next(e for e in plan.graph.edges if e.kind == 'bottom')
    assert edge.radius_mm == pytest.approx(.275)
    assert plan.metrics['model_anchor']['penetration_mm'] == pytest.approx(.2)
    assert plan.metrics['model_anchor']['length_mm'] == pytest.approx(1.0)
    assert plan.metrics['model_anchor']['diameter_mm'] == pytest.approx(.55)
    assert any(abs(s.bounding_box()[2] - 7.8) < 1e-6 and
               abs(s.bounding_box()[5] - 9.0) < 1e-6 for s in plan.solids)


def test_cylinder_anchor_keeps_endpoint_diameter_through_bottom_segment():
    cone, _ = routed(model_anchor_shape='cone', model_anchor_length_mm=1.0,
                     model_anchor_diameter_mm=.55, model_anchor_penetration_mm=.2,
                     break_point_diameter_mm=0.0)
    cylinder, _ = routed(model_anchor_shape='cylinder', model_anchor_length_mm=1.0,
                         model_anchor_diameter_mm=.55, model_anchor_penetration_mm=.2,
                         break_point_diameter_mm=0.0)
    cone_bottom = next(s for s in cone.solids
                       if abs(s.bounding_box()[2] - 7.8) < 1e-6 and
                       .9 < s.bounding_box()[5] - s.bounding_box()[2] < 1.3)
    cylinder_bottom = next(s for s in cylinder.solids
                           if abs(s.bounding_box()[2] - 7.8) < 1e-6 and
                           .9 < s.bounding_box()[5] - s.bounding_box()[2] < 1.3)
    assert cone_bottom.slice(7.81).area() > cylinder_bottom.slice(7.81).area()
    assert cylinder_bottom.slice(7.81).area() == pytest.approx(
        16 * math.sin(math.pi / 16) * (.55 / 2) ** 2, rel=1e-6)


def test_anchor_length_must_fit_with_the_minimum_tip():
    plan, _ = routed(spacing_mm=1.0, pillar_angle_deg=89.0, tip_length_mm=7.0,
                     model_anchor_length_mm=6.8, min_tip_length_mm=3.5)
    assert plan.metrics['contacts_failed'] == 1
    assert plan.metrics['routing']['model_anchor'] == 0
    assert any(d.code == 'support_unroutable' for d in plan.diagnostics)


def test_anchor_depth_cannot_pass_through_the_pedestal():
    plan, _ = routed(model_anchor_length_mm=1.0, model_anchor_penetration_mm=8.1)
    assert plan.metrics['contacts_failed'] == 1
    assert plan.metrics['routing']['model_anchor'] == 0


def test_anchor_clearance_rejects_a_sideways_obstacle():
    triangles, bounds = pedestal_scene()
    obstacle = m.Manifold.cube((2, 2, 2), True).translate((1.5, 0, 8.5))
    triangles = manifold_triangles(
        m.Manifold.batch_boolean([m.Manifold.cube((10, 10, 8), True).translate((0, 0, 4)),
                                  m.Manifold.cube((20, 20, 2), True).translate((0, 0, 15)),
                                  obstacle], m.OpType.Add)).astype(np.float32)
    bounds = np.stack((triangles.reshape(-1, 3).min(axis=0), triangles.reshape(-1, 3).max(axis=0)))
    settings = resolve_settings(overrides={'support': {
        'allow_part_to_part': True, 'part_to_part_avoidance': 0.0, 'model_anchor_length_mm': 1.0,
        'model_anchor_diameter_mm': .55, 'model_anchor_penetration_mm': .2,
    }})
    field = build_column_field(triangles, bounds, settings, pitch_mm=.5)
    plan, _ = route_contacts([[0., 0., 14.]], field, settings)
    assert plan.metrics['contacts_failed'] == 1
    assert plan.metrics['model_anchor']['candidates_rejected'] == 1


def test_zero_bottom_settings_preserve_direct_anchor_and_depth_extends_it():
    direct, _ = routed(model_anchor_length_mm=0.0, model_anchor_diameter_mm=0.0,
                       model_anchor_penetration_mm=0.0, break_point_diameter_mm=0.0)
    explicit, _ = routed(model_anchor_shape='cone', model_anchor_length_mm=0.0,
                         model_anchor_diameter_mm=0.0, model_anchor_penetration_mm=0.0,
                         break_point_diameter_mm=0.0)
    assert np.array_equal(
        np.concatenate([manifold_triangles(s) for s in direct.solids]),
        np.concatenate([manifold_triangles(s) for s in explicit.solids]))
    deeper, _ = routed(model_anchor_length_mm=0.0, model_anchor_penetration_mm=.2,
                       model_anchor_diameter_mm=0.0, break_point_diameter_mm=0.0)
    assert min(s.bounding_box()[2] for s in deeper.solids) < min(s.bounding_box()[2] for s in direct.solids)


def test_default_model_anchor_is_point_to_point_with_balls_on_both_ends():
    plan, settings = routed()
    support = settings['support']
    assert support['model_anchor_length_mm'] == pytest.approx(2.0)
    assert support['break_point_diameter_mm'] == pytest.approx(0.8)
    assert plan.metrics['routing']['model_anchor'] == 1
    nodes = {n.id: n for n in plan.graph.nodes}
    assert nodes['foot0'].position_mm[2] == pytest.approx(8.0)
    assert nodes['anchor_joint0'].position_mm[2] == pytest.approx(10.0)
    assert nodes['joint0'].position_mm[2] == pytest.approx(12.0)
    without, _ = routed(break_point_diameter_mm=0.0)
    assert sum(s.volume() for s in plan.solids) > sum(s.volume() for s in without.solids)
    combined = m.Manifold.batch_boolean(plan.solids, m.OpType.Add)
    assert combined.status() == m.Error.NoError
    assert len(combined.decompose()) == 1


def test_short_gap_stays_thin_point_to_point():
    # 3 mm gap: full 2 mm + 2 mm tips leave no real middle at pillar diameter.
    solid = (m.Manifold.cube((10, 10, 8), True).translate((0, 0, 4))
             + m.Manifold.cube((20, 20, 2), True).translate((0, 0, 12)))
    triangles = manifold_triangles(solid).astype(np.float32)
    bounds = np.asarray(solid.bounding_box()).reshape(2, 3)
    settings = resolve_settings(overrides={'support': {
        'allow_part_to_part': True, 'part_to_part_avoidance': 0.0}})
    from voxelmill.supports import build_column_field, route_contacts
    field = build_column_field(triangles, bounds, settings, pitch_mm=.5)
    plan, _ = route_contacts([[0., 0., 11.]], field, settings)
    assert plan.metrics['routing']['model_anchor'] == 1
    middle = next(e for e in plan.graph.edges if e.kind == 'model_anchor')
    assert middle.radius_mm == pytest.approx(settings['support']['contact_diameter_mm'] / 2)
    assert middle.radius_mm < settings['support']['pillar_diameter_mm'] / 2 - 1e-9


def test_part_to_part_false_emits_no_model_anchor_solids():
    plan, _ = routed(allow_part_to_part=False)
    assert plan.metrics['routing']['model_anchor'] == 0
    assert plan.metrics['contacts_failed'] == 1
    assert not any(e.kind in ('model_anchor', 'bottom', 'small_model') for e in plan.graph.edges)


def test_shortened_anchor_tip_and_small_pillar_radius_are_derived_from_run():
    plan, _ = routed(model_anchor_length_mm=4.5, model_anchor_diameter_mm=.55,
                     model_anchor_penetration_mm=.2, min_tip_length_mm=.3,
                     small_pillar_diameter_mm=.4, small_pillar_max_length_mm=5.0)
    assert plan.metrics['contacts_with_shortened_tip'] == 1
    assert plan.metrics['min_tip_used_mm'] == pytest.approx(1.5)
    assert plan.metrics['small_pillars'] == 1
    middle = next(e for e in plan.graph.edges if e.kind == 'model_anchor')
    assert middle.radius_mm == pytest.approx(.2)
