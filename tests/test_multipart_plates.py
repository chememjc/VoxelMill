"""Multi-part plates: shared columns fields, cross-part anchoring, refusal rules.

Every shape here is a small cantilever ("T bracket": a grounded post with a
floating arm) or a plain box, built directly with manifold3d so each test
controls exactly which surfaces need support and where the parts sit relative
to each other.
"""
from __future__ import annotations

import manifold3d as m
import numpy as np
import pytest

from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.geometry import manifold_triangles
from voxelmill.mesh import write_stl
from voxelmill.pipeline import prepare

PRINTER = {'pixels': [1000, 800], 'pixel_pitch_mm': [0.1, 0.1], 'build_mm': [100.0, 80.0, 165.0]}


def small(**overrides):
    base = {'process': {'layer_height_mm': 0.2}, 'printer': dict(PRINTER)}
    for section, values in overrides.items():
        base.setdefault(section, {}).update(values)
    return resolve_settings(overrides=base)


def cantilever(post_w=4.0, arm_w=8.0, post_h=16.0, arm_len=14.0, arm_t=3.0, arm_dir=1):
    """A post grounded at the origin with a floating arm needing support.

    ``arm_dir`` is +1 to extend the arm past the post (+x) or -1 to extend it
    back past the post's own footprint (-x), which is what lets one part's
    arm swing out over another part placed to the side.
    """
    post = m.Manifold.cube((post_w, arm_w, post_h)).translate((0, -arm_w / 2, 0))
    ax0, ax1 = (0, post_w + arm_len) if arm_dir == 1 else (-arm_len, post_w)
    arm = m.Manifold.cube((ax1 - ax0, arm_w, arm_t)).translate((ax0, -arm_w / 2, post_h - arm_t))
    return post + arm


def write_shape(path, solid):
    write_stl(path, manifold_triangles(solid))
    return path


def part_bounds(report):
    """Placement bounds for the primary part and every added part, in order."""
    bounds = [np.asarray(report['placement']['bounds'], dtype=float)]
    for part in report['stages'].get('extra_models', {}).get('parts', []):
        bounds.append(np.asarray(part['placement']['bounds'], dtype=float))
    return bounds


def contacts_in(report, bounds, pad=1e-6):
    low, high = bounds
    return [node for node in report['support_graph']['nodes'] if node['kind'] == 'contact'
            and np.all(np.asarray(node['position_mm']) >= low - pad)
            and np.all(np.asarray(node['position_mm']) <= high + pad)]


def graph_adjacency(graph):
    nodes = {node['id']: node for node in graph['nodes']}
    adjacency = {node_id: set() for node_id in nodes}
    for edge in graph['edges']:
        adjacency[edge['start']].add(edge['end'])
        adjacency[edge['end']].add(edge['start'])
    return nodes, adjacency


def reaches_ground_or_anchor(nodes, adjacency, start):
    """Walk from ``start`` and report whether a grounded foot or a model anchor is reachable."""
    seen, stack = {start}, [start]
    while stack:
        current = stack.pop()
        node = nodes[current]
        if node['kind'] == 'model_anchor':
            return True
        if node['kind'] == 'foot' and abs(node['position_mm'][2]) < 1e-6:
            return True
        for neighbor in adjacency[current]:
            if neighbor not in seen:
                seen.add(neighbor)
                stack.append(neighbor)
    return False


@pytest.fixture
def bracket_pair(tmp_path):
    shape = cantilever()
    first = write_shape(tmp_path / 'first.stl', shape)
    second = write_shape(tmp_path / 'second.stl', shape)
    return first, second


def test_two_adjacent_parts_each_get_routed_supports(bracket_pair):
    first, second = bracket_pair
    settings = small()
    report = prepare(first, settings,
                     extra_models=[{'path': str(second), 'center_offset': (0.0, 10.0), 'lift_mm': 5.0}])
    supports = report['passes'][-1]['supports']
    assert supports['contacts_failed'] == 0
    audit = report['validation']['metrics']['support_collisions']
    assert audit['intrusions'] == 0, audit['worst']
    assert audit['support_overlaps'] == 0, audit['support_overlap_examples']
    bounds_a, bounds_b = part_bounds(report)
    assert len(contacts_in(report, bounds_a)) > 0
    assert len(contacts_in(report, bounds_b)) > 0


def test_tree_supports_merge_across_parts_and_stay_grounded(bracket_pair):
    first, second = bracket_pair
    settings = small(support={'tree_supports': True})
    report = prepare(first, settings,
                     extra_models=[{'path': str(second), 'center_offset': (0.0, 10.0), 'lift_mm': 5.0}])
    audit = report['validation']['metrics']['support_collisions']
    assert audit['intrusions'] == 0, audit['worst']
    tree = report['passes'][-1]['supports']['tree']
    assert tree['enabled'] and tree['trunks'] > 0
    graph = report['support_graph']
    nodes, adjacency = graph_adjacency(graph)
    contacts = [node['id'] for node in graph['nodes'] if node['kind'] == 'contact']
    assert contacts
    unreached = [cid for cid in contacts if not reaches_ground_or_anchor(nodes, adjacency, cid)]
    assert not unreached


def test_tree_supports_shared_plate_has_no_pillar_overlaps(bracket_pair):
    first, second = bracket_pair
    settings = small(support={'tree_supports': True})
    report = prepare(first, settings,
                     extra_models=[{'path': str(second), 'center_offset': (0.0, 10.0), 'lift_mm': 5.0}])
    audit = report['validation']['metrics']['support_collisions']
    assert audit['support_overlaps'] == 0, audit['support_overlap_examples']


def test_upper_part_anchors_on_the_lower_part_it_overhangs(tmp_path):
    # A: a plain grounded slab, wide enough that its interior is far from
    # every edge (no plate route can sneak around it within the router's
    # branch-search reach).
    lower = m.Manifold.cube((40.0, 10.0, 15.0)).translate((0.0, -5.0, 0.0))
    lower_path = write_shape(tmp_path / 'lower.stl', lower)
    # B: a tall post grounded well clear of A, with an arm swinging back to
    # float 6 mm above A's top, deep inside A's footprint.
    upper = cantilever(post_w=4.0, arm_w=10.0, post_h=25.0, arm_len=30.0, arm_t=4.0, arm_dir=-1)
    upper_path = write_shape(tmp_path / 'upper.stl', upper)

    settings = small()
    report = prepare(lower_path, settings, lift_mm=0.0,
                     extra_models=[{'path': str(upper_path), 'center_offset': (10.0, 0.0), 'lift_mm': 0.0}])
    supports = report['passes'][-1]['supports']
    assert supports['routing']['model_anchor'] > 0
    audit = report['validation']['metrics']['support_collisions']
    assert audit['intrusions'] == 0, audit['worst']

    blocked_settings = small(support={'allow_part_to_part': False})
    blocked = prepare(lower_path, blocked_settings, lift_mm=0.0,
                      extra_models=[{'path': str(upper_path), 'center_offset': (10.0, 0.0), 'lift_mm': 0.0}])
    blocked_supports = blocked['passes'][-1]['supports']
    assert blocked_supports['routing']['model_anchor'] == 0
    assert (blocked_supports['contacts_blocked_by_policy'] > 0
            or blocked_supports['contacts_failed'] > 0)


def test_touching_parts_are_refused_but_a_half_millimeter_gap_is_accepted(bracket_pair):
    first, second = bracket_pair
    settings = small()
    with pytest.raises(VoxelMillError) as excinfo:
        prepare(first, settings,
               extra_models=[{'path': str(second), 'center_offset': (0.0, 0.0), 'lift_mm': 5.0}])
    assert excinfo.value.code == 'models_intersect'

    # The parts are 8 mm wide in Y; 8.5 mm of offset leaves a 0.5 mm gap.
    report = prepare(first, settings,
                     extra_models=[{'path': str(second), 'center_offset': (0.0, 8.5), 'lift_mm': 5.0}])
    assert report['stages']['extra_models']['collision'] == 'model_solids_only'


def test_per_part_support_override_changes_contact_density(bracket_pair):
    first, second = bracket_pair
    settings = small()
    report = prepare(first, settings, extra_models=[
        {'path': str(second), 'center_offset': (0.0, 10.0), 'lift_mm': 5.0,
         'overrides': {'support': {'spacing_mm': 8.0}}}])
    bounds_a, bounds_b = part_bounds(report)
    dense = contacts_in(report, bounds_a)
    sparse = contacts_in(report, bounds_b)
    assert len(dense) > len(sparse) > 0
    audit = report['validation']['metrics']['support_collisions']
    assert audit['intrusions'] == 0
    assert audit['support_overlaps'] == 0


def test_hollowed_part_and_a_solid_extra_part_share_the_plate(tmp_path):
    # Hollowing runs on the whole merged plate (settings['hollow'] has no
    # per-part scope), so this exercises the whole-plate path with a second,
    # solid part added beside it.
    cube = m.Manifold.cube((16.0, 16.0, 16.0), True)
    cube_path = write_shape(tmp_path / 'cube.stl', cube)
    block = m.Manifold.cube((10.0, 10.0, 10.0), True)
    block_path = write_shape(tmp_path / 'block.stl', block)
    settings = small(hollow={'enabled': True, 'wall_thickness_mm': 1.5, 'voxel_size_mm': 0.5,
                              'min_wall_thickness_mm': 1.0, 'drain_diameter_mm': 2.0,
                              'vent_diameter_mm': 2.0},
                     repair={'seal_voids': True})
    report = prepare(cube_path, settings,
                     extra_models=[{'path': str(block_path), 'center_offset': (30.0, 0.0), 'lift_mm': 5.0}])
    assert report['stages']['hollow']['status'] == 'complete'
    assert report['validation']['passed'], report['validation']['checks']
    audit = report['validation']['metrics']['support_collisions']
    assert audit['intrusions'] == 0, audit['worst']


def test_rotated_part_near_the_edge_and_a_part_outside_the_build_volume(bracket_pair):
    first, second = bracket_pair
    settings = small()
    rotated = prepare(first, settings, rotate=(0.0, 0.0, 45.0), center_offset=(35.0, 25.0))
    assert rotated['stages']['build_volume']['fits']
    assert rotated['validation']['passed']

    clipped_settings = small(assembly={'clip_to_build_volume': True})
    outside = prepare(first, clipped_settings, center_offset=(60.0, 0.0), allow_unresolved=True)
    assert not outside['stages']['build_volume']['fits']
    assert not outside['validation']['passed']


def test_same_plate_prepared_twice_is_identical_and_order_independent(bracket_pair):
    first, second = bracket_pair
    settings = small()
    spec = [{'path': str(second), 'center_offset': (0.0, 10.0), 'lift_mm': 5.0}]
    once = prepare(first, settings, extra_models=spec)
    again = prepare(first, settings, extra_models=spec)
    assert once['support_graph'] == again['support_graph']

    def contact_positions(report):
        return sorted(tuple(round(value, 3) for value in node['position_mm'])
                      for node in report['support_graph']['nodes'] if node['kind'] == 'contact')

    swapped = prepare(second, settings, center_offset=(0.0, 10.0),
                      extra_models=[{'path': str(first), 'center_offset': (0.0, 0.0), 'lift_mm': 5.0}])
    assert contact_positions(once) == contact_positions(swapped)


def test_three_parts_in_a_row_middle_supports_may_branch_toward_neighbors(tmp_path):
    shape = cantilever()
    paths = [write_shape(tmp_path / f'part{i}.stl', shape) for i in range(3)]
    settings = small()
    report = prepare(paths[0], settings, extra_models=[
        {'path': str(paths[1]), 'center_offset': (0.0, 10.0), 'lift_mm': 5.0},
        {'path': str(paths[2]), 'center_offset': (0.0, 20.0), 'lift_mm': 5.0},
    ])
    supports = report['passes'][-1]['supports']
    assert supports['contacts_failed'] == 0
    audit = report['validation']['metrics']['support_collisions']
    assert audit['intrusions'] == 0, audit['worst']
    assert audit['support_overlaps'] == 0, audit['support_overlap_examples']
