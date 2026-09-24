"""Default settings must produce an exportable, collision-free plate.

A failed validation withholds the export, so a default that fails on a plain
cylinder blocks a first print outright. These pin the pieces that make the
defaults pass: contacts that cover every downward point within reach, model
anchors that land on slopes, contacts beside walls counted as attached, and
braces that never land inside another support.
"""
from pathlib import Path

import manifold3d as m
import numpy as np
import pytest
from scipy.spatial import cKDTree

from voxelmill.collisions import segment_distances, support_model_intrusion, support_overlaps
from voxelmill.config import resolve_settings
from voxelmill.contracts import SupportEdge, SupportGraph, SupportNode
from voxelmill.geometry import cylinder_between, manifold_triangles
from voxelmill.pipeline import prepare
from voxelmill.supports import (_thin, attached_below, build_column_field, contact_reach_mm,
                                route_contacts)

SHAPES = Path(__file__).resolve().parents[1] / 'fixtures' / 'shapes'
VALID = ('cube', 'cone', 'cylinder', 'sphere', 'torus', 'tetrahedron', 'overhang_bracket',
         'pin_array', 'stepped_pyramid', 'hollow_cup', 'drained_cup', 'thin_wall')


@pytest.mark.parametrize('name', VALID)
def test_default_prepare_passes_every_valid_fixture(name):
    report = prepare(SHAPES / f'{name}.stl', resolve_settings())
    validation = report['validation']
    failing = {key: value for key, value in validation['checks'].items()
               if value not in ('pass', 'warn')}
    assert validation['passed'], failing
    supports = report['passes'][-1]['supports']
    assert supports['contacts_failed'] == 0
    audit = validation['metrics']['support_collisions']
    assert audit['intrusions'] == 0, audit['worst']
    assert audit['support_overlaps'] == 0, audit['support_overlap_examples']


def _disc_samples(radius=8.0, pitch=0.1, z=5.0):
    xs = np.arange(-radius, radius + pitch / 2, pitch)
    grid = np.array([(x, y) for x in xs for y in xs if x * x + y * y <= radius * radius])
    return np.column_stack([grid, np.full(len(grid), z)])


def test_thinning_covers_every_sample_within_reach():
    settings = resolve_settings()
    samples = _disc_samples()
    reach = contact_reach_mm(settings)
    contacts = _thin(samples, [], settings['support']['spacing_mm'], reach)
    distance = cKDTree(contacts).query(samples)[0]
    assert distance.max() <= reach + 1e-9
    # The reach itself keeps the first layer of a flat underside inside the
    # growth-span limit, measured from the tip's edge.
    support = settings['support']
    assert reach + support['contact_diameter_mm'] / 2 <= support['max_span_mm']


def test_thinning_keeps_contacts_apart_and_mandatory_ones_exactly():
    settings = resolve_settings()
    spacing = settings['support']['spacing_mm']
    reach = contact_reach_mm(settings)
    samples = _disc_samples()
    mandatory = np.array([[0.05, 0.05, 5.0], [0.3, 0.1, 5.0]])
    contacts = _thin(samples, mandatory, spacing, reach)
    assert np.array_equal(contacts[:2], mandatory)
    automatic = contacts[2:]
    pairs = cKDTree(automatic).query_pairs(min(spacing / 2, reach) - 1e-9)
    assert not pairs
    # Automatic contacts keep clear of the mandatory ones too.
    assert cKDTree(mandatory).query(automatic)[0].min() >= min(spacing / 2, reach) - 1e-9


def test_thinning_is_order_independent():
    settings = resolve_settings()
    samples = _disc_samples(radius=5.0)
    rng = np.random.default_rng(3)
    shuffled = samples[rng.permutation(len(samples))]
    first = _thin(samples, [], 3.0, contact_reach_mm(settings))
    second = _thin(shuffled, [], 3.0, contact_reach_mm(settings))
    assert np.array_equal(first, second)


def test_model_anchor_lands_on_a_45_degree_ramp():
    # A shelf over a ramp: no plate route exists under the shelf, so every
    # contact there must anchor on the ramp's sloped surface.
    ramp = m.CrossSection([[(0, 0), (20, 0), (0, 20)]]).extrude(20).rotate((90, 0, 0)).translate(
        (0, 20, 0))
    shelf = m.Manifold.cube((20, 20, 3)).translate((0, 0, 30))
    column = m.Manifold.cube((2, 20, 33)).translate((-2, 0, 0))
    solid = m.Manifold.batch_boolean([ramp, shelf, column], m.OpType.Add).translate((0, 0, 5))
    triangles = manifold_triangles(solid).astype(np.float32)
    bounds = np.asarray(solid.bounding_box()).reshape(2, 3)
    settings = resolve_settings()
    field = build_column_field(triangles, bounds, settings)
    contacts = [[x, y, 35.0] for x in (4.0, 8.0, 12.0) for y in (6.0, 10.0, 14.0)]
    plan, _ = route_contacts(contacts, field, settings)
    assert plan.metrics['contacts_failed'] == 0
    assert plan.metrics['routing']['model_anchor'] == len(contacts)


def test_contact_beside_a_wall_counts_as_attached():
    settings = resolve_settings()
    wall = m.Manifold.cube((4, 20, 20)).translate((0, 0, 0))
    shelf = m.Manifold.cube((10, 20, 2)).translate((4, 0, 20))
    triangles = manifold_triangles(wall + shelf).astype(np.float32)
    beside = 4.0 + settings['support']['pillar_diameter_mm'] / 2
    far = 4.0 + 3.0
    assert attached_below(triangles, [[beside, 10, 20]], settings) == [True]
    assert attached_below(triangles, [[far, 10, 20]], settings) == [False]


def _graph(nodes, edges):
    return SupportGraph(nodes=[SupportNode(name, list(map(float, point)), kind)
                               for name, point, kind in nodes],
                        edges=[SupportEdge(a, b, r, kind) for a, b, r, kind in edges])


def test_support_overlaps_ignore_joined_edges_and_report_the_rest():
    graph = _graph([('f0', (0, 0, 0), 'foot'), ('j0', (0, 0, 10), 'junction'),
                    ('f1', (0.5, 0, 0), 'foot'), ('j1', (0.5, 0, 10), 'junction'),
                    ('f2', (5, 0, 0), 'foot'), ('j2', (5, 0, 10), 'junction')],
                   [('f0', 'j0', .6, 'vertical'), ('f1', 'j1', .6, 'vertical'),
                    ('f2', 'j2', .6, 'vertical'), ('j0', 'j2', .3, 'brace')])
    result = support_overlaps(graph)
    # The two close pillars overlap with no shared node, and the brace from
    # j0 to j2 runs straight through j1's shaft top. The brace's own ends and
    # the pillars it joins are not counted.
    pairs = {frozenset((tuple(e['edges'][0][:2]), tuple(e['edges'][1][:2])))
             for e in result['examples']}
    assert pairs == {frozenset({('f0', 'j0'), ('f1', 'j1')}),
                     frozenset({('j0', 'j2'), ('f1', 'j1')})}


def test_segment_distances_match_brute_force():
    rng = np.random.default_rng(5)
    p0, p1, q0, q1 = (rng.normal(size=(64, 3)) for _ in range(4))
    got = segment_distances(p0, p1, q0, q1)
    t = np.linspace(0, 1, 401)
    for index in range(64):
        a = p0[index] + np.outer(t, p1[index] - p0[index])
        b = q0[index] + np.outer(t, q1[index] - q0[index])
        brute = np.linalg.norm(a[:, None] - b[None], axis=2).min()
        assert got[index] <= brute + 1e-9
        assert brute - got[index] < 5e-3


def test_intrusion_separates_tips_from_shafts_through_a_part():
    settings = resolve_settings()
    part = m.Manifold.cube((10, 10, 4), True).translate((0, 0, 10))
    tip = cylinder_between((3, 0, 6), (3, 0, 8.15), 0.2)
    through = cylinder_between((-3, 0, 0), (-3, 0, 20), 0.6)
    graph = _graph([('c', (3, 0, 8.0), 'contact')], [])
    result = support_model_intrusion([tip, through], [part], graph, settings)
    assert result['expected_pieces'] == 1
    assert result['intrusions'] == 1
    assert result['worst']['center_mm'][0] == pytest.approx(-3, abs=0.7)


@pytest.mark.parametrize('name', ('overhang_bracket', 'pin_array', 'torus'))
def test_tree_supports_never_collide(name):
    report = prepare(SHAPES / f'{name}.stl',
                     resolve_settings(overrides={'support': {'tree_supports': True}}))
    audit = report['validation']['metrics']['support_collisions']
    assert audit['intrusions'] == 0, audit['worst']
    assert audit['support_overlaps'] == 0, audit['support_overlap_examples']
    assert report['validation']['passed']
