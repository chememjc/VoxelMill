import math

import numpy as np
import pytest
import manifold3d as m

from voxelmill.config import resolve_settings
from voxelmill.contracts import CancellationToken, Canceled
from voxelmill.geometry import manifold_triangles
from voxelmill.supports import build_column_field, downward_contacts, plan_supports


def placed(solid):
    triangles = manifold_triangles(solid).astype(np.float32)
    flat = triangles.reshape(-1, 3)
    return triangles, np.stack((flat.min(axis=0), flat.max(axis=0)))


def assemble(solid, plan, raft):
    parts = plan.solids + ([raft] if raft is not None else [])
    return m.Manifold.batch_boolean([solid, m.Manifold.batch_boolean(parts, m.OpType.Add)], m.OpType.Add)


def test_column_runs_describe_a_hollow_stack_exactly():
    solid = (m.Manifold.cube((6, 6, 2), True).translate((0, 0, 1))
             + m.Manifold.cube((6, 6, 2), True).translate((0, 0, 5)))
    triangles, bounds = placed(solid)
    field = build_column_field(triangles, bounds, resolve_settings(), pitch_mm=0.5)
    column = field.index_of(0., 0.)
    lo, hi = field.runs(column)
    assert len(lo) == 2
    assert field.z_of(int(lo[0])) == pytest.approx(0.0, abs=0.05)
    assert field.z_of(int(hi[0])) == pytest.approx(2.0, abs=0.05)
    assert field.z_of(int(lo[1])) == pytest.approx(4.0, abs=0.05)
    assert field.blocked(column, int(lo[0]), int(hi[0]))
    assert not field.blocked(column, int(hi[0]), int(lo[1]))
    assert field.top_below(column, int(lo[1])) == int(hi[0])
    assert field.metrics['open_contour_rows'] == 0


def test_overhang_sampling_follows_the_configured_angle():
    # A pyramid: four faces at 45 degrees from the plate, apex down.
    solid = m.Manifold.cube((10, 10, 10), True).rotate((0, 45, 0)).translate((0, 0, 12))
    triangles, _ = placed(solid)
    steep = resolve_settings(overrides={'support': {'overhang_angle_deg': 30.}})
    shallow = resolve_settings(overrides={'support': {'overhang_angle_deg': 60.}})
    few, _ = downward_contacts(triangles, steep)
    many, _ = downward_contacts(triangles, shallow)
    assert len(few) < len(many)
    assert (few[:, 2] if len(few) else np.zeros(1)).min() >= 0


def test_lifted_sphere_is_supported_to_the_plate_as_one_solid():
    solid = m.Manifold.sphere(6, 64).translate((0, 0, 11))
    triangles, bounds = placed(solid)
    plan, raft = plan_supports(triangles, bounds, resolve_settings())
    assert plan.metrics['contacts_failed'] == 0
    assert plan.metrics['routing']['vertical'] == plan.metrics['contacts_routed']
    assert raft is not None
    full = assemble(solid, plan, raft)
    assert full.status() == m.Error.NoError
    assert len(full.decompose()) == 1
    assert full.bounding_box()[2] == pytest.approx(0.0)


def test_blocked_column_routes_around_or_anchors_on_the_model():
    blocked = (m.Manifold.cube((10, 10, 20), True).translate((0, 0, 10))
               + m.Manifold.cube((30, 10, 4), True).translate((0, 0, 27)))
    triangles, bounds = placed(blocked)
    plan, raft = plan_supports(triangles, bounds, resolve_settings(overrides={'support': {'allow_part_to_part': True}}))
    routing = plan.metrics['routing']
    assert routing['branched'] + routing['model_anchor'] > 0
    assert plan.metrics['contacts_failed'] == 0
    assert assemble(blocked, plan, raft).status() == m.Error.NoError
    # Every routed branch starts at the plate or on already-printed material.
    feet = {node.id: node for node in plan.graph.nodes if node.kind in ('foot', 'model_anchor')}
    for node in feet.values():
        assert node.position_mm[2] >= -1e-9
    wide = (m.Manifold.cube((40, 40, 20), True).translate((0, 0, 10))
            + m.Manifold.cube((20, 20, 4), True).translate((0, 0, 27)))
    triangles, bounds = placed(wide)
    plan, _ = plan_supports(triangles, bounds, resolve_settings(overrides={'support': {'allow_part_to_part': True}}))
    assert plan.metrics['routing']['model_anchor'] > 0


def test_slender_pillars_are_braced_and_reported():
    solid = m.Manifold.sphere(4, 48).translate((0, 0, 64))
    triangles, bounds = placed(solid)
    plan, raft = plan_supports(triangles, bounds, resolve_settings())
    assert plan.metrics['braces'] > 0
    assert not plan.metrics['braces_capped']
    codes = {d.code for d in plan.diagnostics}
    assert 'support_slenderness' in codes
    assert all(d.severity == 'warning' for d in plan.diagnostics)
    assert plan.metrics['max_pillar_length_mm'] > resolve_settings()['support']['max_slenderness'] * 1.2
    assert assemble(solid, plan, raft).status() == m.Error.NoError


def test_extra_contacts_from_a_correction_pass_are_always_kept():
    solid = m.Manifold.sphere(6, 64).translate((0, 0, 11))
    triangles, bounds = placed(solid)
    settings = resolve_settings()
    base, _ = plan_supports(triangles, bounds, settings)
    extra = [(4.5, 4.5, 8.0), (-4.5, -4.5, 8.0)]
    more, _ = plan_supports(triangles, bounds, settings, extra_contacts=extra)
    assert more.metrics['contacts_requested'] >= base.metrics['contacts_requested']
    heads = {(round(n.position_mm[0], 3), round(n.position_mm[1], 3), round(n.position_mm[2], 3))
             for n in more.graph.nodes if n.kind == 'contact'}
    for point in extra:
        assert tuple(round(v, 3) for v in point) in heads


def test_support_planning_is_deterministic_and_cancellable():
    solid = m.Manifold.sphere(6, 48).translate((0, 0, 11))
    triangles, bounds = placed(solid)
    settings = resolve_settings()
    first, _ = plan_supports(triangles, bounds, settings)
    second, _ = plan_supports(triangles, bounds, settings)
    assert [n.position_mm for n in first.graph.nodes] == [n.position_mm for n in second.graph.nodes]
    token = CancellationToken()
    token.cancel()
    with pytest.raises(Canceled):
        plan_supports(triangles, bounds, settings, cancel=token)


def test_manual_only_contact_selection_and_bracing_switch():
    solid = m.Manifold.sphere(4, 32).translate((0, 0, 64))
    triangles, bounds = placed(solid)
    settings = resolve_settings(overrides={'support': {'automatic': False, 'auto_bracing': False}})
    empty, raft = plan_supports(triangles, bounds, settings)
    assert empty.metrics['contacts_requested'] == 0 and raft is None
    manual, raft = plan_supports(triangles, bounds, settings, extra_contacts=[[0., 0., 60.]])
    assert manual.metrics['contacts_requested'] == 1
    assert manual.metrics['braces'] == 0


def stacked_with_gap(gap):
    """Two slabs with the same footprint, separated by ``gap`` millimeters.

    The upper slab's underside is a downward face whose column is blocked by
    the lower slab, and every free column is farther away than a 45 degree
    branch can reach, so the router has to anchor on the model or give up.
    """
    lower = m.Manifold.cube((10, 10, 3), True).translate((0, 0, 1.5))
    upper = m.Manifold.cube((10, 10, 3), True).translate((0, 0, 3 + gap + 1.5))
    return lower + upper


def test_a_contact_closer_than_one_tip_anchors_on_a_shortened_tip():
    """This is what left roughly half of every float-valve contact unrouted.

    Measured on the three OpenSCAD parts before the change: nut 110 of 229
    routed, cover 389 of 773, body 516 of 1,085. After it: 195, 722 and 994,
    with the remainder a different failure class entirely.
    """
    settings = resolve_settings(overrides={'support': {'allow_part_to_part': True}})
    tip = settings['support']['tip_length_mm']
    gap = 1.0
    assert settings['support']['min_tip_length_mm'] <= gap < tip
    solid = stacked_with_gap(gap)
    triangles, bounds = placed(solid)
    plan, raft = plan_supports(triangles, bounds, settings)
    assert plan.metrics['contacts_failed'] == 0
    assert plan.metrics['routing']['model_anchor'] > 0
    assert plan.metrics['contacts_with_shortened_tip'] > 0
    assert plan.metrics['min_tip_used_mm'] < tip
    middles = [e for e in plan.graph.edges if e.kind == 'model_anchor']
    assert middles
    assert max(e.radius_mm for e in middles) < settings['support']['pillar_diameter_mm'] / 2 - 1e-9
    assert assemble(solid, plan, raft).status() == m.Error.NoError

    # Two min-length ends no longer fit: the same contacts become unroutable.
    strict = resolve_settings(overrides={'support': {'min_tip_length_mm': 1.5}})
    plan, _ = plan_supports(triangles, bounds, strict)
    assert plan.metrics['contacts_failed'] > 0
    assert plan.metrics['contacts_with_shortened_tip'] == 0


def test_routed_support_dimensions_match_the_configured_segments():
    """Dimensional regression: nothing else pins the tip geometry numerically."""
    settings = resolve_settings(overrides={'support': {'auto_bracing': False}})
    support = settings['support']
    tip, penetration = support['tip_length_mm'], support['penetration_mm']
    solid = m.Manifold.sphere(6, 64).translate((0, 0, 11))
    triangles, bounds = placed(solid)
    plan, _raft = plan_supports(triangles, bounds, settings)
    nodes = {node.id: node for node in plan.graph.nodes}
    edges = {(edge.start, edge.end): edge for edge in plan.graph.edges}
    checked = 0
    for name, node in nodes.items():
        if node.kind != 'contact':
            continue
        order = name[len('contact'):]
        junction = nodes[f'joint{order}']
        # A full-length tip starts exactly one tip below the contact.
        assert junction.position_mm[2] == pytest.approx(node.position_mm[2] - tip, abs=1e-9)
        assert junction.position_mm[:2] == pytest.approx(node.position_mm[:2])
        assert edges[(f'joint{order}', name)].radius_mm == pytest.approx(
            support['contact_diameter_mm'] / 2)
        assert edges[(f'foot{order}', f'joint{order}')].radius_mm == pytest.approx(
            support['pillar_diameter_mm'] / 2)
        checked += 1
    assert checked > 0
    assert plan.metrics['contacts_with_shortened_tip'] == 0
    assert plan.metrics['min_tip_used_mm'] == pytest.approx(tip)
    # The tip is buried in the model by exactly the configured penetration.
    top = max(s.bounding_box()[5] for s in plan.solids)
    contacts = [n.position_mm[2] for n in nodes.values() if n.kind == 'contact']
    assert top == pytest.approx(max(contacts) + penetration, abs=1e-6)


def test_brace_interval_and_radius_match_the_derived_spacing():
    settings = resolve_settings()
    solid = m.Manifold.sphere(4, 48).translate((0, 0, 64))
    triangles, bounds = placed(solid)
    plan, _raft = plan_supports(triangles, bounds, settings)
    assert plan.metrics['brace_spacing_mm'] == pytest.approx(15.0)
    assert plan.metrics['brace_max_length_mm'] == pytest.approx(30.0)
    nodes = {node.id: np.asarray(node.position_mm) for node in plan.graph.nodes}
    braces = [edge for edge in plan.graph.edges if edge.kind == 'brace']
    assert braces
    for edge in braces:
        start, end = nodes[edge.start], nodes[edge.end]
        assert start[2] - end[2] == pytest.approx(np.linalg.norm(start[:2] - end[:2]))
        assert np.linalg.norm(start - end) <= 30.0 + 1e-8
        # A later branch can split an earlier one; all retain derived thickness.
        assert edge.radius_mm <= settings['support']['pillar_diameter_mm'] / 4


def test_twenty_millimetre_lift_gets_downward_braces():
    solid = m.Manifold.sphere(6, 48).translate((0, 0, 26))
    triangles, bounds = placed(solid)
    plan, _raft = plan_supports(triangles, bounds, resolve_settings())
    assert plan.metrics['brace_spacing_mm'] == pytest.approx(15.0)
    assert plan.metrics['braces'] >= 1


def test_plate_touch_footprint_matches_the_configured_raft():
    """Dimensional regression on the base: one convex hull over every foot.

    voxelmill always builds this, and the known-good CHITUBOX configuration for
    this printer uses no raft at all, so the shape and its dimensions have to
    be pinned before a base strategy can replace them.
    """
    settings = resolve_settings()
    support = settings['support']
    pillar_r = support['pillar_diameter_mm'] / 2
    reach = pillar_r + support['raft_expansion_mm']
    solid = m.Manifold.sphere(6, 64).translate((0, 0, 11))
    triangles, bounds = placed(solid)
    plan, raft = plan_supports(triangles, bounds, settings)
    assert raft is not None

    x0, y0, z0, x1, y1, z1 = raft.bounding_box()
    assert z0 == pytest.approx(0.0, abs=1e-9)
    assert z1 == pytest.approx(support['raft_thickness_mm'], abs=1e-9)
    feet = np.asarray(plan.feet, dtype=float)
    assert len(feet) > 0
    # The hull reaches exactly one foot radius plus the expansion past the
    # outermost foot, on an inscribed 32-gon so the reserve stays conservative.
    for axis, (low, high) in enumerate(((x0, x1), (y0, y1))):
        assert low == pytest.approx(feet[:, axis].min() - reach, abs=1e-6)
        assert high == pytest.approx(feet[:, axis].max() + reach, abs=1e-6)
    # It is one connected convex body, not a pad per foot.
    assert len(raft.decompose()) == 1
    assert raft.volume() > 0


def holed_shelf():
    """Lifted block with a vertical hole; a shaft through the bore clips the wall."""
    body = m.Manifold.cube((20, 20, 8), True).translate((0, 0, 24))
    hole = m.Manifold.cylinder(12, 2.0, 2.0, 48).translate((0, 0, 18))
    return body - hole


def test_shaft_through_a_hole_is_rejected_or_rerouted():
    solid = holed_shelf()
    triangles, bounds = placed(solid)
    settings = resolve_settings(overrides={'support': {
        'automatic': False, 'auto_bracing': False, 'base_type': 'none',
        'drop_attached_unroutable': False}})
    # On the solid ring next to the bore. A 45° branch into the empty hole
    # column would run a shaft through the wall.
    contact = [4.0, 0.0, 20.0]
    plan, _ = plan_supports(triangles, bounds, settings, extra_contacts=[contact])
    hole = m.Manifold.cylinder(12, 2.0, 2.0, 48).translate((0, 0, 18))
    for part in plan.solids:
        overlap = part ^ hole
        if overlap.is_empty() or overlap.volume() <= 1e-6:
            continue
        raise AssertionError('support solid occupies the mounting hole')
    feet = [n.position_mm for n in plan.graph.nodes if n.kind == 'foot']
    for foot in feet:
        assert math.hypot(foot[0], foot[1]) >= 2.0 - 1e-3


def test_overlapping_branch_candidates_emit_only_one_shaft():
    solid = m.Manifold.cube((12, 12, 4), True).translate((0, 0, 20))
    triangles, bounds = placed(solid)
    settings = resolve_settings(overrides={'support': {
        'automatic': False, 'auto_bracing': False, 'base_type': 'none',
        'drop_attached_unroutable': False}})
    contacts = [[0.0, 0.0, 18.0], [0.35, 0.0, 18.0]]
    plan, _ = plan_supports(triangles, bounds, settings, extra_contacts=contacts)
    shafts = [e for e in plan.graph.edges if e.kind in ('vertical', 'branched')]
    # Extra/island contacts are density-exempt, so both may plant. They must
    # not weave a 45° branch to dodge each other; that is what failed drainage.
    assert plan.metrics['routing'].get('branched', 0) == 0
    assert len(shafts) >= 1
    assert plan.metrics['contacts_routed'] >= 1


def test_dense_tilted_overhang_keeps_spacing_but_an_island_still_lands():
    solid = m.Manifold.cube((24, 24, 2), True).rotate((0, 25, 0)).translate((0, 0, 16))
    triangles, bounds = placed(solid)
    settings = resolve_settings(overrides={'support': {
        'auto_bracing': False, 'base_type': 'none', 'spacing_mm': 4.0}})
    plan, _ = plan_supports(triangles, bounds, settings)
    contacts = np.array([n.position_mm for n in plan.graph.nodes if n.kind == 'contact'])
    assert len(contacts) > 1
    from scipy.spatial import cKDTree
    tree = cKDTree(contacts)
    # Automatic contacts are not a forest inside one spacing cell.
    counts = tree.query_ball_point(contacts, r=0.5)
    assert max(len(n) for n in counts) == 1

    plate = m.Manifold.cube((20, 8, 2), True).translate((0, 0, 12))
    triangles, bounds = placed(plate)
    settings = resolve_settings(overrides={'support': {
        'automatic': False, 'auto_bracing': False, 'base_type': 'none',
        'drop_attached_unroutable': False, 'spacing_mm': 4.0}})
    spaced = [[-4.0, 0.0, 11.0], [4.0, 0.0, 11.0]]
    island = [1.6, 0.0, 11.0]
    denser, _ = plan_supports(triangles, bounds, settings, extra_contacts=spaced + [island])
    heads = np.array([n.position_mm for n in denser.graph.nodes if n.kind == 'contact'])
    assert np.min(np.linalg.norm(heads - island, axis=1)) < 0.2
    auto = heads[np.linalg.norm(heads - island, axis=1) > 0.2]
    if len(auto) > 1:
        from scipy.spatial.distance import pdist
        assert pdist(auto).min() > 3.0
