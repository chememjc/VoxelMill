"""Integration checks for peel screening on reopened STL geometry."""
import numpy as np
import manifold3d as m

from voxelmill.config import resolve_settings
from voxelmill.geometry import manifold_triangles, rotation_matrix, iter_transformed_triangles
from voxelmill.mesh import write_stl
from voxelmill.pipeline import validate_stl


def settings(**peel):
    return resolve_settings(overrides={
        'printer': {'pixels': [200, 200], 'pixel_pitch_mm': [0.2, 0.2],
                    'build_mm': [40., 40., 40.]},
        'process': {'layer_height_mm': 0.1, 'bottom_layers': 2,
                    'transition_layers': 0},
        'peel': peel,
        'resources': {'workers': 1},
    })


def write_box(path, size=(20., 10., 2.)):
    write_stl(path, manifold_triangles(m.Manifold.cube(size)))


def test_validate_stl_reports_large_flat_region_separately_from_drainage(tmp_path):
    source = tmp_path / 'flat.stl'
    write_box(source)
    report = validate_stl(source, settings(area_threshold_mm2=100.0), drainage=True)
    assert report.metrics['peel_risk']['status'] == 'complete'
    assert report.metrics['peel_risk']['threshold_region_count'] >= 1
    assert report.checks['peel_risk'] == 'warn'
    assert report.checks['drainage_bottlenecks'] == 'pass'


def test_rotated_thin_plate_has_no_flat_peel_region(tmp_path):
    source = tmp_path / 'edge_on.stl'
    triangles = manifold_triangles(m.Manifold.cube((20., 10., 2.)))
    rotated = np.concatenate(list(iter_transformed_triangles(
        triangles, np.block([[rotation_matrix([90., 0., 0.]), np.zeros((3, 1))],
                             [np.zeros((1, 3)), np.ones((1, 1))]]))))
    write_stl(source, rotated)
    report = validate_stl(source, settings(area_threshold_mm2=100.0), drainage=False)
    assert report.metrics['peel_risk']['status'] == 'complete'
    assert report.metrics['peel_risk']['threshold_region_count'] == 0
    assert report.checks['peel_risk'] == 'pass'


def test_disabled_peel_is_explicitly_not_run_and_blocks_pass(tmp_path):
    source = tmp_path / 'disabled.stl'
    write_box(source)
    report = validate_stl(source, settings(enabled=False), drainage=False)
    assert report.metrics['peel_risk']['status'] == 'not_run'
    assert report.checks['peel_risk'] == 'not_run'
    assert report.passed is False


def test_prepare_reopened_export_contains_peel_evidence(tmp_path):
    from voxelmill.pipeline import prepare
    source, output = tmp_path / 'flat.stl', tmp_path / 'prepared.stl'
    write_box(source)
    cfg = settings(area_threshold_mm2=100.)
    cfg['support']['automatic'] = False
    report = prepare(source, cfg, output=output, allow_unresolved=True, max_passes=1)
    validation = report['validation']
    assert validation['checks']['peel_risk'] == 'warn'
    assert validation['metrics']['reopened']['path']
    assert validation['metrics']['peel_risk']['source_triangle_count'] > 0


def test_goo_source_keeps_surface_advisory_but_masks_do_not_claim_it(tmp_path):
    from voxelmill.goo import slice_stl, verify_goo
    source, output = tmp_path / 'tiny.stl', tmp_path / 'tiny.goo'
    write_box(source, (2, 2, .2))
    cfg = settings(area_threshold_mm2=1.)
    report = slice_stl(source, output, cfg, allow_unresolved=True)
    assert report['written']
    assert report['validation']['checks']['peel_risk'] == 'warn'
    reopened = verify_goo(output, cfg)
    assert reopened['report']['metrics']['peel_risk']['status'] == 'not_run'
    assert 'peel_risk' not in reopened['report']['checks']
