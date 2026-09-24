"""support.brace_model_pillars (VM-092): bracing pillars that stand on the model.

Off (the default) reproduces the pre-existing behavior exactly: grounding is a
support-only path to the plate or a generated base, and a model-anchored
pillar is never part of it. On admits a model pillar's own vertical shaft into
that grounding, seeded from the top of its bottom connector (the
'anchor_junction' node route_contacts places there), so it can send and
receive braces like a plate pillar. See ISSUES.md VM-092 and the comments in
supports._brace for the design.
"""
from copy import deepcopy
from pathlib import Path

import manifold3d as m
import numpy as np
import pytest

from voxelmill.config import fill_legacy_settings, resolve_settings
from voxelmill.contracts import SupportEdge, SupportGraph, SupportNode
from voxelmill.geometry import manifold_triangles
from voxelmill.pipeline import prepare
from voxelmill.supports import _brace, plan_supports, unbraced_lengths
from test_downward_bracing import branches

SHAPES = Path(__file__).resolve().parents[1] / 'fixtures' / 'shapes'
DEFAULT_FIXTURES = ('cube', 'cone', 'cylinder', 'sphere', 'torus', 'tetrahedron',
                    'overhang_bracket', 'pin_array', 'stepped_pyramid', 'hollow_cup',
                    'drained_cup', 'thin_wall')
# Every default fixture keeps plate pillars under a slenderness of 15. The
# bracket was the exception at 28.8 (one pillar 25.9 mm unbraced in a dense
# row) until _brace gained its rescue pass; it now measures 11.1.
PLATE_SLENDERNESS_BOUND = 15.0


def model_pillar_setup(pillars, **options):
    """Two model-anchored pillars, shaped the way route_contacts builds one:

    a buried foot ('model_anchor'), a bottom connector rising to the top of
    that connector ('anchor_junction' -- the node brace_model_pillars grounds
    from), a vertical shaft up to the shoulder ('junction'), and a tip
    ('contact'). ``pillars`` is ``[(x, y, height), ...]``, matching
    ``test_downward_bracing.setup`` so the two are directly comparable.
    """
    settings = resolve_settings(overrides={'support': {
        'brace_spacing_mm': 15, 'brace_max_length_mm': 8, 'brace_max_distance_mm': 12,
        'base_type': 'none', 'brace_destination': 'both', 'brace_pattern': 'single',
        'brace_min_height_mm': 0, 'brace_diameter_mm': 0, **options}})
    graph = SupportGraph()
    for i, (x, y, height) in enumerate(pillars):
        graph.nodes.extend([
            SupportNode(f'foot{i}', [x, y, -0.15], 'model_anchor'),
            SupportNode(f'anchor_joint{i}', [x, y, 0.], 'anchor_junction'),
            SupportNode(f'joint{i}', [x, y, height], 'junction'),
            SupportNode(f'contact{i}', [x, y, height + 2.], 'contact'),
        ])
        graph.edges.append(SupportEdge(f'foot{i}', f'anchor_joint{i}', .3, 'bottom'))
        graph.edges.append(SupportEdge(f'anchor_joint{i}', f'joint{i}', .6, 'model_anchor'))
        graph.edges.append(SupportEdge(f'joint{i}', f'contact{i}', .3, 'tip'))
    return settings, graph


def test_option_off_never_grounds_a_model_pillar():
    """Same shafts as the plate case in test_downward_bracing, but anchored on
    the model: with the option off (the default) nothing is admitted."""
    settings, graph = model_pillar_setup([(0, 0, 40), (5, 0, 40)], brace_model_pillars=False)
    evidence = {}
    assert _brace([], settings, [], graph=graph, evidence=evidence) == 0
    assert not branches(graph)
    assert evidence['ungrounded_rejected'] == 2  # one model_anchor shaft edge per pillar


def test_option_on_braces_model_pillars_on_the_same_schedule_as_plate_pillars():
    settings, graph = model_pillar_setup([(0, 0, 40), (5, 0, 40)], brace_model_pillars=True)
    evidence = {}
    joined = _brace([], settings, [], graph=graph, evidence=evidence)
    # Identical shoulder-derived schedule to the plate-pillar equivalent
    # (test_shoulders_downward_inclination_length_shared_spacing_and_no_duplicates):
    # the anchor_junction plays exactly the role a plate foot plays there.
    assert joined == 3
    actual = branches(graph)
    assert [a[2] for a, b, r in actual] == [40, 25, 10]
    assert evidence['ungrounded_rejected'] == 0
    nodes = {node.id: node for node in graph.nodes}
    for edge in graph.edges:
        if edge.kind in ('brace', 'brace_foot'):
            assert nodes[edge.start].kind not in ('contact', 'model_anchor')
            assert nodes[edge.end].kind not in ('contact', 'model_anchor')


def test_no_bottom_connector_is_never_grounded_even_with_the_option_on():
    """bottom_used == 0 in route_contacts means no anchor_junction node exists;
    the shaft then starts directly at the buried 'model_anchor' foot, which the
    node-kind filter excludes regardless of brace_model_pillars."""
    settings, graph = model_pillar_setup([(0, 0, 40), (5, 0, 40)], brace_model_pillars=True)
    for index in range(2):
        anchor_id = f'anchor_joint{index}'
        graph.nodes = [node for node in graph.nodes if node.id != anchor_id]
        for edge in graph.edges:
            if edge.start == anchor_id:
                edge.start = f'foot{index}'
    evidence = {}
    assert _brace([], settings, [], graph=graph, evidence=evidence) == 0
    assert evidence['ungrounded_rejected'] == 2


@pytest.mark.parametrize('brace_model_pillars', [False, True])
def test_a_small_model_pillar_is_never_admitted_either_way(brace_model_pillars):
    """A whole thin model-to-model pillar (small_pillar_mode='model') runs
    directly from a buried 'model_anchor' foot to the top 'contact' node.
    Admitting it would ground a brace through a contact, which the node-kind
    filter never allows, on or off."""
    settings = resolve_settings(overrides={'support': {'brace_model_pillars': brace_model_pillars}})
    graph = SupportGraph()
    graph.nodes.extend([SupportNode('foot0', [0, 0, 0.], 'model_anchor'),
                        SupportNode('contact0', [0, 0, 5.], 'contact')])
    graph.edges.append(SupportEdge('foot0', 'contact0', .2, 'small_model'))
    evidence = {}
    assert _brace([], settings, [], graph=graph, evidence=evidence) == 0
    assert evidence['ungrounded_rejected'] == 1


def test_minimum_height_is_measured_above_the_model_pillars_own_foot():
    """A model pillar's foot can stand far above the plate. Shifting the whole
    pillar up must not change which levels clear brace_min_height_mm: it is
    measured above the pillar's own foot (its anchor_junction), not Z=0. This
    reproduces test_bracing_options.test_minimum_height_omits_lower_levels
    (one brace, at the top level only) after a uniform +50 mm shift."""
    shift = 50.0
    settings, graph = model_pillar_setup([(0, 0, 40), (5, 0, 40)], brace_model_pillars=True,
                                         brace_min_height_mm=26)
    for node in graph.nodes:
        node.position_mm[2] += shift
    evidence = {}
    assert _brace([], settings, [], graph=graph, evidence=evidence) == 1
    actual = branches(graph)
    assert [a[2] for a, b, r in actual] == [40 + shift]


def test_option_absent_resolves_to_off_and_behaves_identically():
    """An older resolved settings table that predates this key must behave
    exactly like one with the key explicitly False."""
    settings = resolve_settings()
    assert settings['support']['brace_model_pillars'] is False
    legacy = deepcopy(settings)
    del legacy['support']['brace_model_pillars']
    filled = fill_legacy_settings(legacy)
    assert filled['support']['brace_model_pillars'] is False
    for pillars in ([(0, 0, 40), (5, 0, 40)],):
        off_settings, off_graph = model_pillar_setup(pillars, brace_model_pillars=False)
        absent_settings, absent_graph = model_pillar_setup(pillars)
        del absent_settings['support']['brace_model_pillars']
        absent_settings['support'] = fill_legacy_settings(
            {'support': absent_settings['support']})['support']
        assert _brace([], off_settings, [], graph=off_graph) == \
            _brace([], absent_settings, [], graph=absent_graph)
        assert [(a.tolist(), b.tolist(), r) for a, b, r in branches(off_graph)] == \
            [(a.tolist(), b.tolist(), r) for a, b, r in branches(absent_graph)]


def _model_pillar_geometry(size=30.0, gap=20.0):
    """A real (small, fast) fixture with several model-standing pillars: two
    large slabs share a footprint far wider than the branch search radius, so
    every contact on the upper slab's underside fails to find a free plate
    column and anchors on the lower slab instead -- unlike
    test_supports.stacked_with_gap, whose 10 mm footprint lets most contacts
    escape sideways to the plate once the gap (and so the branch's reach) is
    large enough for a full-diameter model pillar to form.
    """
    lower = m.Manifold.cube((size, size, 3), True).translate((0, 0, 1.5))
    upper = m.Manifold.cube((size, size, 3), True).translate((0, 0, 3 + gap + 1.5))
    solid = lower + upper
    triangles = manifold_triangles(solid).astype('float32')
    flat = triangles.reshape(-1, 3)
    return triangles, np.stack((flat.min(axis=0), flat.max(axis=0)))


def test_model_pillars_brace_in_a_real_plan_without_touching_tip_or_contact():
    triangles, bounds = _model_pillar_geometry()
    off = resolve_settings(overrides={'support': {'brace_model_pillars': False}})
    on = resolve_settings(overrides={'support': {'brace_model_pillars': True}})
    plan_off, _raft_off = plan_supports(triangles, bounds, off)
    plan_on, _raft_on = plan_supports(triangles, bounds, on)
    assert plan_off.metrics['contacts_failed'] == 0
    assert plan_on.metrics['contacts_failed'] == 0
    off_model = unbraced_lengths(plan_off.graph)['model']
    on_model = unbraced_lengths(plan_on.graph)['model']
    assert off_model['braced_pillars'] == 0
    assert on_model['braced_pillars'] > 0
    nodes = {node.id: node for node in plan_on.graph.nodes}
    braces = [edge for edge in plan_on.graph.edges if edge.kind in ('brace', 'brace_foot')]
    assert braces
    for edge in braces:
        assert nodes[edge.start].kind not in ('contact', 'model_anchor')
        assert nodes[edge.end].kind not in ('contact', 'model_anchor')


@pytest.mark.parametrize('name', ['overhang_bracket'])
def test_model_pillars_still_pass_full_validation_with_lower_slenderness(name):
    """The bracket is the fixture VM-092 measured: 17 model pillars, unbraced
    up to 22.6 mm (slenderness ~25) with the option off. On braces most of
    them and lowers the worst slenderness, without adding a collision or
    failing validation."""
    off_settings = resolve_settings(overrides={'support': {'brace_model_pillars': False}})
    on_settings = resolve_settings(overrides={'support': {'brace_model_pillars': True}})
    off = prepare(SHAPES / f'{name}.stl', off_settings)
    on = prepare(SHAPES / f'{name}.stl', on_settings)
    for report in (off, on):
        assert report['validation']['passed']
        audit = report['validation']['metrics']['support_collisions']
        assert audit['intrusions'] == 0, audit['worst']
        assert audit['support_overlaps'] == 0, audit['support_overlap_examples']
    off_model = off['passes'][-1]['supports']['unbraced']['model']
    on_model = on['passes'][-1]['supports']['unbraced']['model']
    assert off_model['pillars'] > 0  # the fixture actually has model pillars
    assert on_model['braced_pillars'] > off_model['braced_pillars']
    assert on_model['max_slenderness'] < off_model['max_slenderness']


@pytest.mark.parametrize('name', DEFAULT_FIXTURES)
def test_plate_slenderness_stays_within_the_measured_bound(name):
    """VM-092's slenderness target: plate bracing keeps every pillar's longest
    unbraced run under 15 diameters on the default fixtures."""
    report = prepare(SHAPES / f'{name}.stl', resolve_settings())
    assert report['validation']['passed']
    plate = report['passes'][-1]['supports']['unbraced']['plate']
    if plate['pillars'] == 0:
        return
    assert plate['max_slenderness'] <= PLATE_SLENDERNESS_BOUND
