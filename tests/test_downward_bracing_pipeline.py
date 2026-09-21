"""Prepared and decoded geometry must retain grounded brace joints."""
import math

import manifold3d as m
import pytest

from voxelmill.config import resolve_settings
from voxelmill.geometry import manifold_triangles, mesh_to_manifold
from voxelmill.goo import slice_stl, verify_goo
from voxelmill.mesh import open_stl, write_stl
from voxelmill.pipeline import prepare
from voxelmill.project import load_project


@pytest.mark.parametrize('base_type,contacts,options', [
    ('none', [[-3., 0., 20.], [3., 0., 20.]], {}),
    ('triangle', [[0., 0., 20.]], {}),
    ('none', [[-3., 0., 20.], [3., 0., 20.]], {'brace_pattern': 'x',
        'brace_branches_per_node': 2, 'brace_angle_deg': 60., 'brace_destination': 'supports'}),
    ('none', [[-3., 0., 20.], [3., 0., 20.]], {'brace_pattern': 'alternating',
        'brace_spacing_mm': 8., 'brace_angle_deg': 30., 'brace_destination': 'supports'}),
    ('pad', [[0., 0., 20.]], {'brace_destination': 'base', 'brace_branches_per_node': 4}),
    ('none', [[-3., 0., 20.], [3., 0., 20.]], {'tree_supports': True,
        'tree_cluster_mm': 8., 'tip_base_diameter_mm': .8, 'auto_bracing': False}),
])
def test_braced_prepare_reopen_and_decoded_layers_stay_connected(tmp_path, base_type, contacts, options):
    source = tmp_path / 'beam.stl'
    output = tmp_path / 'prepared.stl'
    project = tmp_path / 'braced.voxmil'
    write_stl(source, manifold_triangles(m.Manifold.cube((8., 4., 2.), True)))
    settings = resolve_settings(overrides={
        'printer': {'pixels': [800, 600], 'pixel_pitch_mm': [.1, .1],
                    'build_mm': [80., 60., 165.]},
        'process': {'layer_height_mm': .1},
        'support': {'automatic': False, 'base_type': base_type,
                    'brace_max_distance_mm': 10., 'brace_diameter_mm': .8,
                    'brace_spacing_mm': 15., 'brace_max_length_mm': 30., **options},
    })
    report = prepare(source, settings, rotate=(0., 0., 0.), lift_mm=20.,
                     output=output, project=project, components=True,
                     manual_contacts=contacts, allow_unresolved=True)
    metrics = report['passes'][0]['supports']
    assert metrics['contacts_routed'] == len(contacts)
    if settings['support']['auto_bracing']:
        assert metrics['braces'] > 0
    else:
        assert metrics['tree']['trunks'] == 1
    assert report['export']['written']
    assert report['validation']['checks']['raster_connectivity'] == 'pass'
    assert report['validation']['checks']['overlap'] == 'pass'
    graph = report['support_graph']
    nodes = {node['id']: node for node in graph['nodes']}
    braces = [edge for edge in graph['edges'] if edge['kind'] == 'brace']
    assert bool(braces) == settings['support']['auto_bracing']
    adjacency = {node: set() for node in nodes}
    for edge in graph['edges']:
        # Model contacts cannot provide grounding to the brace network.
        if edge['kind'] in ('tip', 'bottom', 'small_model', 'model_anchor'):
            continue
        adjacency[edge['start']].add(edge['end'])
        adjacency[edge['end']].add(edge['start'])
    grounded = {key for key, node in nodes.items()
                if node['kind'] == 'foot' and node['position_mm'][2] == pytest.approx(0.)}
    pending = list(grounded)
    while pending:
        for neighbor in adjacency[pending.pop()] - grounded:
            grounded.add(neighbor)
            pending.append(neighbor)
    for edge in braces:
        assert edge['start'] in grounded and edge['end'] in grounded
        a, b = nodes[edge['start']]['position_mm'], nodes[edge['end']]['position_mm']
        assert abs(a[2] - b[2]) == pytest.approx(math.dist(a[:2], b[:2]) * math.tan(math.radians(settings['support']['brace_angle_deg'])))
        assert math.dist(a, b) <= 30. + 1e-8
    with open_stl(output) as reopened:
        solid, _ = mesh_to_manifold(reopened.triangles)
        assert reopened.asset.sha256 == report['validation']['metrics']['reopened']['sha256']
        assert len(solid.decompose()) == 1
        assert solid.bounding_box()[2] >= -1e-7
    state = load_project(project)
    assert state['settings']['support'] == settings['support']
    assert state['settings']['support']['brace_max_length_mm'] == 30.
    assert 'brace_start_height_mm' not in state['settings']['support']
    sliced = tmp_path / 'braced.goo'
    result = slice_stl(output, sliced, settings, allow_unresolved=True)
    assert result['written']
    decoded = verify_goo(sliced, settings)
    assert decoded['report']['checks']['raster_connectivity'] == 'pass'
    assert decoded['report']['checks']['overlap'] == 'pass'
