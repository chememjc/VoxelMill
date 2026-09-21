"""Nearby vertical supports can share one trunk."""
import pytest
import manifold3d as m

from voxelmill.config import resolve_settings
from voxelmill.geometry import manifold_triangles
from voxelmill.supports import plan_supports
from test_supports import placed


def test_tree_clusters_nearby_verticals_onto_one_trunk():
    solid = m.Manifold.cube((12, 12, 3), True).translate((0, 0, 10))
    triangles, bounds = placed(solid)
    off = resolve_settings(overrides={'support': {
        'automatic': False, 'auto_bracing': False, 'base_type': 'none',
        'tree_supports': False, 'drop_attached_unroutable': False}})
    on = resolve_settings(overrides={'support': {
        'automatic': False, 'auto_bracing': False, 'base_type': 'none',
        'tree_supports': True, 'tree_cluster_mm': 8.0,
        'drop_attached_unroutable': False}})
    contacts = [[-3.0, 0.0, 8.5], [3.0, 0.0, 8.5]]
    plain, _ = plan_supports(triangles, bounds, off, extra_contacts=contacts)
    tree, _ = plan_supports(triangles, bounds, on, extra_contacts=contacts)
    assert plain.metrics['contacts_routed'] == 2
    assert tree.metrics['contacts_routed'] == 2
    assert tree.metrics['tree']['trunks'] == 1
    assert tree.metrics['tree']['contacts_in_trees'] == 2
    assert tree.metrics['base']['unique_feet'] == 1
    assert plain.metrics['base']['unique_feet'] == 2


def test_tree_off_keeps_independent_feet():
    solid = m.Manifold.cube((12, 12, 3), True).translate((0, 0, 10))
    triangles, bounds = placed(solid)
    settings = resolve_settings(overrides={'support': {
        'automatic': False, 'auto_bracing': False, 'base_type': 'none',
        'tree_supports': False, 'drop_attached_unroutable': False}})
    plan, _ = plan_supports(triangles, bounds, settings,
                            extra_contacts=[[-3.0, 0.0, 8.5], [3.0, 0.0, 8.5]])
    assert plan.metrics['tree']['trunks'] == 0
    assert plan.metrics['base'].get('unique_feet', plan.metrics['base'].get('feet')) == 2


def test_tree_graph_matches_sloped_connected_geometry():
    import math
    solid = m.Manifold.cube((12, 12, 3), True).translate((0, 0, 10))
    triangles, bounds = placed(solid)
    settings = resolve_settings(overrides={'support': {
        'automatic': False, 'auto_bracing': False, 'base_type': 'none',
        'tree_supports': True, 'tree_cluster_mm': 8.0}})
    plan, _ = plan_supports(triangles, bounds, settings,
                            extra_contacts=[[-3., 0., 8.5], [3., 0., 8.5]])
    nodes = {node.id: node for node in plan.graph.nodes}
    branches = [edge for edge in plan.graph.edges if edge.kind == 'tree_branch']
    assert len(branches) == 2
    assert len([node for node in nodes.values() if node.kind == 'foot']) == 1
    for edge in branches:
        a, b = nodes[edge.start].position_mm, nodes[edge.end].position_mm
        angle = math.degrees(math.atan2(b[2] - a[2], math.hypot(b[0] - a[0], b[1] - a[1])))
        assert angle >= settings['support']['pillar_angle_deg'] - 1e-8
    combined = m.Manifold.batch_boolean([solid, *plan.solids], m.OpType.Add)
    assert combined.status() == m.Error.NoError
    assert len(combined.decompose()) == 1


@pytest.mark.parametrize('start,end,obstacle', [
    ((0., 0., 0.), (0., 0., 8.), (.25, 0., 4.)),
    ((-3., 0., 1.), (3., 0., 7.), (0., .25, 4.)),
])
def test_capsule_clearance_catches_thickness_on_vertical_and_sloped_segments(start, end, obstacle):
    from voxelmill.supports import _brace_clear, build_column_field
    import numpy as np
    solid = m.Manifold.cube((.2, .2, .2), True).translate(obstacle)
    triangles, bounds = placed(solid)
    bounds = np.array([[-5., -5., 0.], [5., 5., 10.]])
    field = build_column_field(triangles, bounds, resolve_settings(), pitch_mm=.1)
    assert not _brace_clear(field, start, end, .4, 0.)
    assert _brace_clear(field, start, end, .01, 0.)


def test_tree_rejects_a_trunk_whose_thickness_hits_the_model():
    import numpy as np
    from voxelmill.supports import _emit_tree_supports, build_column_field
    obstacle = m.Manifold.cube((.2, .2, 1.), True).translate((.35, 0., 2.))
    triangles, _ = placed(obstacle)
    settings = resolve_settings(overrides={'support': {
        'tree_supports': True, 'tree_cluster_mm': 8., 'support_clearance_mm': .01}})
    field = build_column_field(triangles, np.array([[-5., -5., 0.], [5., 5., 10.]]),
                               settings, pitch_mm=.1)
    jobs = [{'x': x, 'y': 0., 'base_z': 8., 'middle_z': 0., 'run_r': .6, 'order': i}
            for i, x in enumerate((-3., 3.))]
    solids, pillars, feet, radii = [], [], [], []
    metrics = _emit_tree_supports(jobs, solids, pillars, feet, radii, field, settings)
    assert metrics['trunks'] == 0
    assert metrics['kept_independent'] == 2
    assert len(feet) == 2
    for support in solids:
        overlap = support ^ obstacle
        assert overlap.is_empty() or overlap.volume() == pytest.approx(0.)


@pytest.mark.parametrize('tip_shape', ['cone', 'cylinder'])
@pytest.mark.parametrize('angle', [20., 45., 70.])
def test_shoulder_blend_fills_angled_cap_notch_without_widening_tip(tip_shape, angle):
    import math
    import numpy as np
    from voxelmill.geometry import cylinder_between
    from voxelmill.support_segments import tip_segment, shoulder_joint
    shoulder = np.array([0., 0., 8.])
    start = shoulder - [3., 0., 3 * math.tan(math.radians(angle))]
    shaft = cylinder_between(start, shoulder, .6)
    tip = tip_segment(shoulder, shoulder + [0., 0., 2.], .4, .2, tip_shape)
    joint = shoulder_joint(shoulder, .6, .2, 2.)
    # This region sits directly below the horizontal tip base, outside the
    # angled cylinder's end disc: the visible crescent notch in the old mesh.
    probe = m.Manifold.cube((.04, .04, .02), True).translate((.15, 0., 7.99))
    assert ((shaft + tip) ^ probe).volume() < probe.volume() * .05
    assert ((shaft + tip + joint) ^ probe).volume() == pytest.approx(probe.volume())
    assert len((shaft + tip + joint).decompose()) == 1
    # Above the shoulder the blend is a tiny collar buried within the tip.
    above = m.Manifold.cube((4., 4., 4.)).translate((-2., -2., 8.))
    assert ((joint ^ above) - tip).volume() == pytest.approx(0., abs=1e-10)
