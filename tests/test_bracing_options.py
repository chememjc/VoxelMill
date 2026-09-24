"""Configurable bracing topology, destination policy, and serialized controls."""
import math
import json
import numpy as np
import pytest
import manifold3d as m
from voxelmill.cli import main
from voxelmill.config import resolve_settings, fill_legacy_settings, validate_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.geometry import cylinder_between
from voxelmill.supports import _brace
from voxelmill.support_example import support_example
from test_downward_bracing import setup, branches
from test_support_policy import field_for


@pytest.mark.parametrize('mode,feet,joins', [('supports', 0, 1), ('base', 2, 2), ('both', 0, 1)])
def test_destination_policy(mode, feet, joins):
    settings, graph = setup([(0, 0, 10), (4, 0, 10)], brace_destination=mode,
                            brace_max_length_mm=30, brace_spacing_mm=100)
    evidence = {}
    assert _brace([], settings, [], graph=graph, evidence=evidence) == joins
    assert evidence['new_feet'] == feet
    for _a, b, _ in branches(graph):
        assert (b[2] < 1) == (mode == 'base')


@pytest.mark.parametrize('density,expected', [(1, 1), (2, 2), (8, 2)])
def test_density_connects_distinct_neighbors_and_counts_incoming(density, expected):
    settings, graph = setup([(0, 0, 40), (4, 0, 40), (0, 4, 40)],
                            brace_branches_per_node=density, brace_destination='supports',
                            brace_max_length_mm=6, brace_spacing_mm=100)
    assert _brace([], settings, [], graph=graph) == expected
    actual = branches(graph)
    assert all(np.allclose(a, [0, 0, 40]) for a, _, _ in actual)
    assert len({tuple(b) for _, b, _ in actual}) == expected


@pytest.mark.parametrize('angle', [20., 45., 70.])
@pytest.mark.parametrize('mode', ['supports', 'base'])
def test_angle_controls_actual_geometry_and_length(angle, mode):
    settings, graph = setup([(0, 0, 20), (4, 0, 20)], brace_destination=mode,
                            brace_angle_deg=angle, brace_max_length_mm=80,
                            brace_min_height_mm=19, brace_spacing_mm=100)
    assert _brace([], settings, [], graph=graph) > 0
    for a, b, _ in branches(graph):
        assert math.degrees(math.atan2(a[2] - b[2], np.linalg.norm(a[:2] - b[:2]))) == pytest.approx(angle)
        assert np.linalg.norm(a - b) <= 80


def test_base_fan_density_rotation_and_no_duplicate_spokes():
    settings, graph = setup([(0, 0, 10)], brace_destination='base', brace_max_length_mm=30,
                            brace_branches_per_node=8, brace_azimuth_deg=30, brace_spacing_mm=100)
    assert _brace([], settings, [], graph=graph) == 8
    actual = branches(graph)
    angles = sorted(round(math.degrees(math.atan2(b[1], b[0])) % 360, 6) for _, b, _ in actual)
    assert angles == pytest.approx(sorted((30 + i * 45) % 360 for i in range(8)))


def test_alternating_changes_direction_at_successive_levels():
    settings, graph = setup([(0, 0, 40), (4, 0, 40)], brace_destination='supports', brace_pattern='alternating')
    assert _brace([], settings, [], graph=graph) == 3
    actual = branches(graph)
    assert [a[2] for a, _, _ in actual] == [40, 25, 10]
    assert [np.sign(b[0] - a[0]) for a, b, _ in actual] == [1, -1, 1]


def test_minimum_height_omits_lower_levels():
    settings, graph = setup([(0, 0, 40), (4, 0, 40)], brace_destination='supports', brace_min_height_mm=26)
    assert _brace([], settings, [], graph=graph) == 1
    assert branches(graph)[0][0][2] == 40


def test_x_pairs_share_junction_and_cannot_duplicate_with_higher_quota():
    settings, graph = setup([(0, 0, 40), (4, 0, 40)], brace_destination='supports',
                            brace_pattern='x', brace_branches_per_node=8, brace_spacing_mm=100)
    solids = []
    assert _brace([], settings, solids, graph=graph) == 2
    centers = [n for n in graph.nodes if np.allclose(n.position_mm, [2, 0, 38])]
    assert len(centers) == 1
    assert sum(e.start == centers[0].id or e.end == centers[0].id for e in graph.edges) == 4
    assert len(branches(graph)) == 4
    shafts = [cylinder_between([x, 0, 0], [x, 0, 40], .6) for x in (0, 4)]
    assert len(m.Manifold.batch_boolean(solids + shafts, m.OpType.Add).decompose()) == 1


def test_x_checks_reciprocal_thickness_before_emitting_either_diagonal():
    settings, graph = setup([(0, 0, 40), (4, 0, 40)], brace_destination='supports',
                            brace_pattern='x', brace_spacing_mm=100)
    obstacle = m.Manifold.cube((.25, .25, .25), True).translate((1., .25, 37.))
    evidence, solids = {}, []
    assert _brace([], settings, solids, graph=graph, field=field_for(obstacle, settings), evidence=evidence) == 0
    assert not solids and not branches(graph)
    assert evidence['collision_rejected'] >= 1


def test_x_rejects_incomplete_reciprocal_span():
    settings, graph = setup([(0, 0, 40), (4, 0, 38)], brace_destination='supports',
                            brace_pattern='x', brace_spacing_mm=100, brace_min_height_mm=39)
    evidence = {}
    assert _brace([], settings, [], graph=graph, evidence=evidence) == 0
    assert evidence['pattern_rejected'] > 0


@pytest.mark.parametrize('limit', [1, 2, 3])
def test_dense_x_candidate_limit_is_preserved(limit):
    settings, graph = setup([(0, 0, 40), (4, 0, 40), (0, 4, 40)], brace_destination='supports',
                            brace_pattern='x', brace_branches_per_node=8, brace_spacing_mm=100)
    evidence = {}
    _brace([], settings, [], graph=graph, evidence=evidence, limit=limit)
    assert evidence['examined'] <= limit
    assert evidence['capped']


@pytest.mark.parametrize('key,value', [('brace_destination', 'model'), ('brace_pattern', 'spiral'),
    ('brace_angle_deg', 0), ('brace_angle_deg', 90), ('brace_branches_per_node', 0),
    ('brace_branches_per_node', 9), ('brace_branches_per_node', True), ('brace_branches_per_node', 2.5),
    ('brace_min_height_mm', -1), ('brace_azimuth_deg', 361)])
def test_invalid_options_rejected(key, value):
    with pytest.raises(VoxelMillError):
        resolve_settings(overrides={'support': {key: value}})


def test_cli_and_legacy_defaults(capsys):
    args = ['profile', '--brace-destination', 'supports', '--brace-pattern', 'x',
            '--brace-branches-per-node', '3', '--brace-angle-deg', '60',
            '--brace-min-height-mm', '5', '--brace-azimuth-deg', '25',
            '--brace-spacing-mm', '8', '--brace-max-distance-mm', '17']
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    settings = payload.get('settings', payload)
    assert settings['support']['brace_branches_per_node'] == 3
    assert settings['support']['brace_destination'] == 'supports'
    assert settings['support']['brace_angle_deg'] == 60
    for key in ('brace_destination', 'brace_pattern', 'brace_branches_per_node',
                'brace_angle_deg', 'brace_min_height_mm', 'brace_azimuth_deg'):
        del settings['support'][key]
    restored = validate_settings(fill_legacy_settings(settings))
    assert restored['support']['brace_destination'] == 'supports'
    assert restored['support']['brace_branches_per_node'] == 1


@pytest.mark.parametrize('allowed,expected', [(False, 0), (True, 4)])
def test_model_gap_example_exposes_primary_policy_without_changing_it(allowed, expected):
    settings = resolve_settings(overrides={'support': {'allow_part_to_part': allowed,
                                                       'part_to_part_avoidance': 0}})
    example = support_example(settings, layout='part-to-part')
    assert example['metrics']['routing']['model_anchor'] == expected
    assert settings['support']['allow_part_to_part'] is allowed
    if allowed:
        assert example['metrics']['braces'] == 0
