"""Nearby vertical supports can share one trunk."""
import pytest
import manifold3d as m

from voxelmill.config import resolve_settings
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


def _tree_plan(**support):
    solid = m.Manifold.cube((12, 12, 3), True).translate((0, 0, 10))
    triangles, bounds = placed(solid)
    settings = resolve_settings(overrides={'support': {
        'automatic': False, 'auto_bracing': False, 'base_type': 'none',
        'tree_supports': True, 'tree_cluster_mm': 8.0, **support}})
    plan, _ = plan_supports(triangles, bounds, settings,
                            extra_contacts=[[-3., 0., 8.5], [3., 0., 8.5]])
    return plan, settings


@pytest.mark.parametrize('trunk_mm,expected_r', [(1.2, .6), (2.0, 1.0), (.5, .45), (0., .45)])
def test_trunk_edge_carries_the_trunk_radius_never_thinner_than_its_branches(trunk_mm, expected_r):
    plan, settings = _tree_plan(trunk_diameter_mm=trunk_mm)
    assert plan.metrics['tree']['trunks'] == 1
    trunk = next(edge for edge in plan.graph.edges if edge.kind == 'tree_trunk')
    branches = [edge for edge in plan.graph.edges if edge.kind == 'tree_branch']
    pillar_r = settings['support']['pillar_diameter_mm'] / 2
    assert trunk.radius_mm == pytest.approx(expected_r)
    assert all(edge.radius_mm == pytest.approx(pillar_r) for edge in branches)
    # The emitted trunk is that thick: the foot sits in the base's radius list.
    assert plan.metrics['base']['unique_feet'] == 1
    nodes = {node.id: node for node in plan.graph.nodes}
    top = nodes[trunk.end].position_mm[2]
    trunk_solid = next(s for s in plan.solids
                       if abs(s.bounding_box()[2]) < 1e-9 and abs(s.bounding_box()[5] - top) < 1e-6)
    width = trunk_solid.bounding_box()[3] - trunk_solid.bounding_box()[0]
    assert width == pytest.approx(2 * expected_r, rel=.02)


def test_default_trunk_is_thicker_than_the_default_pillar():
    settings = resolve_settings()
    assert settings['support']['trunk_diameter_mm'] == pytest.approx(1.2)
    assert settings['support']['trunk_diameter_mm'] > settings['support']['pillar_diameter_mm']


def _trunk_scene(obstacle=None, **support):
    import numpy as np
    from voxelmill.supports import build_column_field
    solid = obstacle if obstacle is not None else m.Manifold.cube((.1, .1, .1), True).translate((4.5, 4.5, 9.))
    triangles, _ = placed(solid)
    settings = resolve_settings(overrides={'support': {
        'tree_supports': True, 'tree_cluster_mm': 8., **support}})
    field = build_column_field(triangles, np.array([[-5., -5., 0.], [5., 5., 10.]]),
                               settings, pitch_mm=.05)
    jobs = [{'x': x, 'y': 0., 'base_z': 8., 'middle_z': 0., 'run_r': .45, 'order': i}
            for i, x in enumerate((-3., 3.))]
    return jobs, field, settings


def _emit(jobs, field, settings):
    from voxelmill.supports import _emit_tree_supports
    solids, pillars, feet, radii = [], [], [], []
    metrics = _emit_tree_supports(jobs, solids, pillars, feet, radii, field, settings)
    return metrics, feet, radii


def test_a_thick_trunk_that_hits_the_model_falls_back_and_says_the_trunk_did_it():
    # The post's near face is 0.65 mm from the trunk axis: a 0.9 mm trunk plus
    # 0.1 mm clearance misses it, a 1.6 mm one does not.
    post = m.Manifold.cube((.2, .2, 1.), True).translate((.75, 0., 2.))
    jobs, field, settings = _trunk_scene(post, trunk_diameter_mm=1.6, support_clearance_mm=.1)
    metrics, feet, _radii = _emit(jobs, field, settings)
    assert metrics['trunks'] == 0 and metrics['kept_independent'] == 2 and len(feet) == 2
    assert metrics['fallbacks'] == {'trunk_diameter': 2}
    assert metrics['trunk_limited_clusters'] == 1
    thin_jobs, thin_field, thin_settings = _trunk_scene(post, trunk_diameter_mm=0.,
                                                        support_clearance_mm=.1)
    thin, _feet, radii = _emit(thin_jobs, thin_field, thin_settings)
    assert thin['trunks'] == 1 and thin['fallbacks'] == {}
    assert radii == [pytest.approx(.45)]


def test_a_trunk_avoids_other_routed_shafts_with_clearance():
    jobs, field, settings = _trunk_scene(trunk_diameter_mm=1.6, support_clearance_mm=.1)
    # Another route's shaft 0.8 mm from the trunk axis: clear of a 0.9 mm
    # trunk (0.45 + 0.1 + 0.2), not of a 1.6 mm one (0.8 + 0.1 + 0.2).
    field.occupied_capsules.add((.8, 0., 0.), (.8, 0., 4.), .2)
    metrics, _feet, _radii = _emit(jobs, field, settings)
    assert metrics['fallbacks'] == {'trunk_diameter': 2}
    jobs, field, settings = _trunk_scene(trunk_diameter_mm=1.6, support_clearance_mm=.1)
    # One through the trunk's own column blocks any tree at all.
    field.occupied_capsules.add((.3, 0., 0.), (.3, 0., 4.), .2)
    metrics, _feet, _radii = _emit(jobs, field, settings)
    assert metrics['fallbacks'] == {'hits_support': 2}
    assert metrics['trunk_limited_clusters'] == 0


def test_its_own_reserved_verticals_do_not_block_a_tree():
    jobs, field, settings = _trunk_scene(trunk_diameter_mm=1.2)
    for job in jobs:
        field.occupied_capsules.add((job['x'], 0., 0.), (job['x'], 0., job['base_z']), job['run_r'])
        job['capsule'] = field.occupied_capsules.capsules[-1]
    metrics, _feet, radii = _emit(jobs, field, settings)
    assert metrics['trunks'] == 1
    assert radii == [pytest.approx(.6)]


def test_old_projects_keep_trunks_as_thick_as_their_branches():
    from copy import deepcopy
    from voxelmill.config import DEFAULTS, fill_legacy_settings, validate_settings
    stored = deepcopy(resolve_settings())
    del stored['support']['trunk_diameter_mm']
    filled = fill_legacy_settings(stored)
    assert filled['support']['trunk_diameter_mm'] == 0.0
    validate_settings(filled)
    assert DEFAULTS['support']['trunk_diameter_mm'] == pytest.approx(1.2)


def test_base_feet_must_be_wider_than_the_trunk():
    from voxelmill.contracts import VoxelMillError
    narrow = {'base_type': 'pad', 'base_touch_diameter_mm': 1.1, 'trunk_diameter_mm': 1.2}
    resolve_settings(overrides={'support': narrow})           # no trees: no trunk to carry
    with pytest.raises(VoxelMillError, match='must exceed the tree trunk diameter'):
        resolve_settings(overrides={'support': {**narrow, 'tree_supports': True}})
    # A trunk below the pillar diameter is raised to it, and only that is compared.
    resolve_settings(overrides={'support': {**narrow, 'trunk_diameter_mm': .5, 'tree_supports': True}})


def test_contact_and_penetration_errors_are_reported_separately():
    from voxelmill.contracts import VoxelMillError
    with pytest.raises(VoxelMillError, match='contact_diameter_mm .* must not exceed pillar_diameter_mm'):
        resolve_settings(overrides={'support': {'contact_diameter_mm': .95,
                                                'break_point_diameter_mm': 0.}})
    with pytest.raises(VoxelMillError, match='penetration_mm .* must be shorter than tip_length_mm'):
        resolve_settings(overrides={'support': {'penetration_mm': 2.0, 'min_tip_length_mm': 2.0}})


def test_trunk_flag_and_presets():
    from voxelmill.cli import _overrides, build_parser
    from voxelmill.presets import apply_preset, list_presets
    args = build_parser().parse_args(['prepare', 'part.stl', '--trunk-diameter-mm', '1.5'])
    assert _overrides(args)['support']['trunk_diameter_mm'] == 1.5
    settings = resolve_settings()
    for name in list_presets():
        applied = apply_preset(settings, name)
        # Every built-in preset still resolves with trees switched on.
        resolve_settings(overrides={'support': {**applied['support'], 'tree_supports': True}})
    heavy = apply_preset(settings, 'heavy')['support']
    assert heavy['trunk_diameter_mm'] > heavy['pillar_diameter_mm']
