"""End-to-end evidence that model-anchor segments survive preparation/export."""
from pathlib import Path

import manifold3d as m
import pytest

from voxelmill.config import resolve_settings
from voxelmill.geometry import manifold_triangles, mesh_to_manifold
from voxelmill.mesh import open_stl, write_stl
from voxelmill.pipeline import prepare


def small_settings(**support):
    return resolve_settings(overrides={
        'process': {'layer_height_mm': .2},
        'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [.1, .1],
                    'build_mm': [100., 80., 165.]},
        'support': support,
    })


@pytest.fixture
def pedestal_source(tmp_path):
    # Two broad model surfaces leave a real 6 mm air gap at the contact
    # column.  The pedestal is deliberately below the upper slab, so the
    # routed bottom segment has a measurable buried portion.
    solid = (m.Manifold.cube((10, 10, 8), True).translate((0, 0, 4))
             + m.Manifold.cube((20, 20, 2), True).translate((0, 0, 15)))
    source = tmp_path / 'pedestal.stl'
    write_stl(source, manifold_triangles(solid))
    return source


@pytest.mark.parametrize('shape', ['cone', 'cylinder'])
def test_model_anchor_shape_survives_full_prepare_export_and_reopen(
        tmp_path, pedestal_source, shape):
    output = tmp_path / f'prepared-{shape}.stl'
    settings = small_settings(
        automatic=False, base_type='none', allow_part_to_part=True, part_to_part_avoidance=0.0,
        model_anchor_shape=shape, model_anchor_length_mm=1.0,
        model_anchor_diameter_mm=.55, model_anchor_penetration_mm=.2,
        penetration_mm=.15)
    report = prepare(pedestal_source, settings, lift_mm=0.0, output=output,
                     components=True,
                     manual_contacts=[[0., 0., 14.]], allow_unresolved=True,
                     drainage=False)

    routing = report['passes'][0]['supports']['routing']
    assert routing['model_anchor'] == 1
    assert report['passes'][0]['supports']['base']['type'] == 'none'
    assert output.is_file()
    assert report['export']['written'] is True
    assert report['export']['warned'] is True  # manual-only contact leaves coverage unresolved
    assert report['validation']['checks']['closed_surface'] == 'pass'
    assert report['validation']['metrics']['reopened']['sha256']
    nodes = {node['id']: node for node in report['support_graph']['nodes']}
    assert nodes['foot0']['position_mm'][2] == pytest.approx(8.0)
    assert nodes['anchor_joint0']['position_mm'][2] == pytest.approx(9.0)
    bottom = next(edge for edge in report['support_graph']['edges'] if edge['kind'] == 'bottom')
    assert bottom['radius_mm'] == pytest.approx(.275)

    with open_stl(output) as reopened:
        assert reopened.asset.sha256 == report['validation']['metrics']['reopened']['sha256']
        assert reopened.asset.triangle_count == report['validation']['metrics']['reopened']['triangles']


@pytest.mark.parametrize('shape', ['cone', 'cylinder'])
def test_whole_small_model_pillar_survives_full_prepare_export_and_reopen(
        tmp_path, pedestal_source, shape):
    output = tmp_path / f'prepared-small-{shape}.stl'
    settings = small_settings(
        automatic=False, base_type='none', allow_part_to_part=True, part_to_part_avoidance=0.0,
        small_pillar_mode='model', small_pillar_shape=shape,
        small_pillar_diameter_mm=.4, small_pillar_max_length_mm=30.0,
        small_pillar_upper_depth_mm=.25, small_pillar_lower_depth_mm=.25)
    report = prepare(pedestal_source, settings, lift_mm=0.0, output=output,
                     components=True,
                     manual_contacts=[[0., 0., 14.]], allow_unresolved=True,
                     drainage=False)

    supports = report['passes'][0]['supports']
    assert supports['routing']['model_anchor'] == 1
    assert supports['small_pillars'] == 1
    assert supports['small_model_pillars'] == 1
    assert supports['base']['type'] == 'none'
    assert output.is_file() and report['export']['written'] is True
    support_path = Path(report['export']['components']['supports'])
    assert support_path.is_file()
    with open_stl(support_path) as support_mesh:
        support_solid, _ = mesh_to_manifold(support_mesh.triangles)
    assert len(support_solid.decompose()) == 1
    assert report['validation']['metrics']['reopened']['sha256']
    assert report['validation']['checks']['closed_surface'] == 'pass'
    assert report['validation']['checks']['raster_connectivity'] == 'pass'
    assert report['validation']['checks']['overlap'] == 'pass'
    graph = report['support_graph']
    small_edge = next(edge for edge in graph['edges'] if edge['kind'] == 'small_model')
    assert small_edge['radius_mm'] == pytest.approx(.2)
    assert len([edge for edge in graph['edges'] if edge['kind'] == 'small_model']) == 1
    with open_stl(output) as reopened:
        assert reopened.asset.sha256 == report['validation']['metrics']['reopened']['sha256']
        assert reopened.asset.triangle_count == report['validation']['metrics']['reopened']['triangles']
