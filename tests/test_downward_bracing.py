"""Downward bracing geometry and support-only grounding regressions."""
import numpy as np
import pytest
import manifold3d as m

from voxelmill.config import resolve_settings
from voxelmill.contracts import CancellationToken, Canceled, SupportNode, SupportEdge, SupportGraph
from voxelmill.geometry import cylinder_between
from voxelmill.supports import _brace, _cone_intersections
from test_support_policy import field_for


def setup(pillars, **options):
    settings = resolve_settings(overrides={'support': {'brace_spacing_mm': 15,
        'brace_max_length_mm': 8, 'brace_max_distance_mm': 12, 'base_type': 'none', **options}})
    graph = SupportGraph()
    for i, (x, y, height) in enumerate(pillars):
        graph.nodes.extend([SupportNode(f'f{i}', [x, y, 0.], 'foot'),
                            SupportNode(f'j{i}', [x, y, height], 'junction')])
        graph.edges.append(SupportEdge(f'f{i}', f'j{i}', .6))
    return settings, graph


def branches(graph):
    nodes = {node.id: np.array(node.position_mm) for node in graph.nodes}
    return [(nodes[e.start], nodes[e.end], e.radius_mm) for e in graph.edges if e.kind == 'brace']


def test_shoulders_downward_inclination_length_shared_spacing_and_no_duplicates():
    settings, graph = setup([(0, 0, 40), (5, 0, 40)])
    solids, evidence = [], {}
    assert _brace([], settings, solids, graph=graph, evidence=evidence) == 3
    actual = branches(graph)
    assert [a[2] for a, b, r in actual] == [40, 25, 10]
    assert [b[2] for a, b, r in actual] == [35, 20, 5]
    for a, b, r in actual:
        assert np.linalg.norm(a[:2] - b[:2]) == pytest.approx(a[2] - b[2])
        assert np.linalg.norm(a - b) <= 8
        assert r == pytest.approx(.3)
    assert evidence['spacing_rejected'] == 3
    assert evidence['new_feet'] == 0
    # Each joint is a volumetric overlap with a retained primary shaft.
    shafts = [cylinder_between((x, 0, 0), (x, 0, 40), .6) for x in (0, 5)]
    joined = m.Manifold.batch_boolean(solids + shafts, m.OpType.Add)
    assert len(joined.decompose()) == 1


def test_unequal_heights_use_first_feasible_shoulder():
    settings, graph = setup([(0, 0, 40), (5, 0, 30)])
    _brace([], settings, [], graph=graph)
    assert [(a[2], b[2]) for a, b, r in branches(graph)] == [(30, 25), (15, 10)]


def test_shorter_intersection_wins_and_counts_for_both_supports():
    settings, graph = setup([(0, 0, 40), (4, 0, 40), (7, 0, 40)], brace_spacing_mm=100)
    _brace([], settings, [], graph=graph)
    first = branches(graph)[0]
    assert first[1] == pytest.approx([4, 0, 36])
    assert not any(np.allclose(a, [4, 0, 40]) for a, b, r in branches(graph))


def test_existing_grounded_branch_is_a_destination_and_graph_is_split():
    settings, graph = setup([(0, 0, 30)], brace_spacing_mm=100, brace_max_length_mm=30)
    graph.nodes.extend([SupportNode('other_foot', [10, 0, 0], 'foot'),
                        SupportNode('other_top', [10, 0, 20], 'junction')])
    graph.edges.append(SupportEdge('other_foot', 'other_top', .6, 'tree_branch'))
    _brace([], settings, [], graph=graph)
    assert branches(graph)[0][1] == pytest.approx([10, 0, 20])
    names = {node.id for node in graph.nodes}
    assert all(edge.start in names and edge.end in names for edge in graph.edges)


@pytest.mark.parametrize('allow', [False, True])
def test_part_only_and_disconnected_destinations_never_ground_braces(allow):
    settings, graph = setup([(0, 0, 40), (4, 0, 40)], allow_part_to_part=allow,
                            brace_spacing_mm=100)
    graph.nodes[0].kind = 'model_anchor'
    graph.edges[0].kind = 'model_anchor'
    graph.nodes[2].kind = 'junction'  # disconnected, even though its Z is zero
    evidence = {}
    assert _brace([], settings, [], graph=graph, evidence=evidence) == 0
    assert evidence['ungrounded_rejected'] == 2


@pytest.mark.parametrize('base_type', ['none', 'pad', 'plate', 'skate', 'skeleton', 'triangle', 'grid', 'hex'])
def test_new_feet_use_base_style_and_stay_in_build_volume(base_type):
    settings, graph = setup([(0, 0, 10)], brace_max_length_mm=30, base_type=base_type,
                            brace_spacing_mm=100)
    solids, feet, radii, evidence = [], [], [], {}
    assert _brace([], settings, solids, graph=graph, feet=feet, foot_radii=radii,
                  evidence=evidence) == 1
    assert len(feet) == 1 and evidence['new_feet'] == 1
    assert len(solids) == 2  # horizontal-bottom stem and diagonal branch
    assert min(s.bounding_box()[2] for s in solids) >= -1e-8
    joined = m.Manifold.batch_boolean(solids + [cylinder_between((0, 0, 0), (0, 0, 10), .6)], m.OpType.Add)
    assert len(joined.decompose()) == 1


def test_whole_base_footprint_rejected_instead_of_clipped():
    settings, graph = setup([(0, 0, 10)], brace_max_length_mm=30, base_type='pad',
                            base_touch_diameter_mm=200, brace_spacing_mm=100)
    solids, evidence = [], {}
    assert _brace([], settings, solids, graph=graph, evidence=evidence) == 0
    assert evidence['foot_rejected'] == 16
    assert not solids


def test_branch_thickness_catches_offset_second_part():
    settings, graph = setup([(0, 0, 40), (5, 0, 40)], brace_spacing_mm=100,
                            brace_diameter_mm=.8)
    obstacle = m.Manifold.cube((.5, .25, 1)).translate((2.25, .25, 37))
    remote = m.Manifold.cube((1, 1, 1)).translate((-10, -10, 2))
    field = field_for(obstacle + remote, settings)
    evidence = {}
    assert _brace([], settings, [], graph=graph, field=field, evidence=evidence) == 0
    assert evidence['collision_rejected'] == 2


def test_branch_envelope_boundary_rejection():
    settings, graph = setup([(74.6, 0, 40), (74.6, 5, 40)], brace_spacing_mm=100,
                            brace_diameter_mm=.8)
    evidence = {}
    assert _brace([], settings, [], graph=graph, evidence=evidence) == 0
    assert evidence['bounds_rejected'] == 2


def test_length_limit_is_actual_not_horizontal_length():
    settings, graph = setup([(0, 0, 40), (6, 0, 40)], brace_spacing_mm=100)
    evidence = {}
    assert _brace([], settings, [], graph=graph, evidence=evidence) == 0
    assert evidence['length_rejected'] >= 2


def test_cancellation_before_geometry():
    settings, graph = setup([(0, 0, 40), (5, 0, 40)])
    cancel = CancellationToken(); cancel.cancel()
    with pytest.raises(Canceled):
        _brace([], settings, [], graph=graph, cancel=cancel)


def test_cone_intersections_with_slanted_existing_branch():
    origin = np.array([0., 0., 20.])
    points = _cone_intersections(origin, np.array([4., 0., 0.]), np.array([12., 0., 16.]))
    assert len(points) == 1
    assert points[0] == pytest.approx([28/3, 0, 32/3])


def test_shared_junction_splits_existing_diagonal_branch():
    settings, graph = setup([(0, 0, 20)], brace_spacing_mm=100, brace_max_length_mm=18,
                            brace_max_distance_mm=16)
    graph.nodes.extend([SupportNode('oldfoot', [4, 0, 0], 'foot'),
                        SupportNode('oldtop', [20, 0, 16], 'junction')])
    graph.edges.append(SupportEdge('oldtop', 'oldfoot', .6, 'brace'))
    solids = []
    assert _brace([], settings, solids, graph=graph) == 1
    assert len(branches(graph)) == 3
    joints = [n for n in graph.nodes if n.kind == 'brace_junction']
    assert any(np.allclose(n.position_mm, [12, 0, 8]) for n in joints)
    old = cylinder_between((20, 0, 16), (4, 0, 0), .6)
    shaft = cylinder_between((0, 0, 0), (0, 0, 20), .6)
    assert len(m.Manifold.batch_boolean([old, shaft, *solids], m.OpType.Add).decompose()) == 1


def test_elbow_origins_keep_one_shoulder_schedule_across_both_edges():
    settings, graph = setup([(0, 0, 40), (7, 0, 40)], brace_spacing_mm=15,
                            brace_max_length_mm=12)
    graph.nodes[1].position_mm = [2, 0, 40]
    graph.nodes.append(SupportNode('elbow', [0, 0, 32], 'elbow'))
    graph.edges[0] = SupportEdge('f0', 'elbow', .6, 'branched')
    graph.edges.insert(1, SupportEdge('elbow', 'j0', .6, 'branched'))
    _brace([], settings, [], graph=graph)
    origins = [a[2] for a, b, r in branches(graph) if a[0] < 3]
    assert origins == [40, 25, 10]
    assert 32 not in origins


def test_cancellation_during_rejected_candidate_loop(monkeypatch):
    import voxelmill.supports as supports
    settings, graph = setup([(0, 0, 40), (5, 0, 40)])
    token = CancellationToken()
    calls = []
    def reject(*args):
        calls.append(True)
        token.cancel()
        return False
    monkeypatch.setattr(supports, '_brace_clear', reject)
    with pytest.raises(Canceled):
        _brace([], settings, [], graph=graph, cancel=token, field=object())
    assert len(calls) == 1


def test_pad_model_collision_uses_whole_base_envelope():
    settings, graph = setup([(0, 0, 10)], brace_max_length_mm=30, base_type='pad',
                            base_touch_diameter_mm=8, brace_spacing_mm=100)
    # A broad plate-level ring catches every candidate pad while its inner hole
    # leaves the primary pillar and branch centerlines clear above the ground.
    ring = (m.Manifold.cylinder(.5, 14, 14, 64) - m.Manifold.cylinder(1, 11, 11, 64))
    field = field_for(ring, settings)
    evidence = {}
    assert _brace([], settings, [], graph=graph, field=field, evidence=evidence) == 0
    assert evidence['foot_rejected'] == 16


def test_derived_radius_respects_thinner_destination():
    settings, graph = setup([(0, 0, 40), (5, 0, 40)], brace_spacing_mm=100)
    graph.edges[1].radius_mm = .2
    _brace([], settings, [], graph=graph)
    assert branches(graph)[0][2] == pytest.approx(.1)


def test_origin_budget_bounds_unreachable_tiny_spacing():
    settings, graph = setup([(0, 0, 40)], brace_spacing_mm=1e-10, brace_max_length_mm=1)
    evidence = {}
    assert _brace([], settings, [], graph=graph, evidence=evidence, limit=3) == 0
    assert evidence['capped']
    assert evidence['origins_examined'] == 3
    assert evidence['no_destination_rejected'] == 3
