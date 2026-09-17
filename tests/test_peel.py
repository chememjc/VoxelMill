"""Full-resolution geometric peel-risk screening tests."""

import numpy as np
import pytest

from voxelmill.contracts import Canceled, VoxelMillError, CancellationToken, ValidationReport
from voxelmill.peel import analyze_peel, apply_peel_check


def settings(**peel):
    return {'peel': {'enabled': True, 'max_angle_deg': 10.0,
                     'area_threshold_mm2': 0.5, 'reference_lift_speed': 0.05,
                     **peel},
            'process': {'layer_height_mm': 0.05, 'bottom_layers': 4},
            'printer': {'motion': {'lift_height': 1, 'lift_speed': .05,
                                   'lift_height2': 0, 'lift_speed2': .01,
                                   'bottom_lift_height': 1, 'bottom_lift_speed': .1,
                                   'bottom_lift_height2': 1, 'bottom_lift_speed2': .2}}}


def square(z=1.0, x=0.0, y=0.0, size=1.0):
    # Reverse winding gives a downward normal.
    return np.array([[[x, y, z], [x, y + size, z], [x + size, y, z]],
                     [[x + size, y, z], [x, y + size, z],
                      [x + size, y + size, z]]], dtype=float)


def test_shared_edge_region_and_speed_depth_change():
    triangles = square()
    result = analyze_peel(triangles, [[0, 0, 0], [10, 10, 10]], settings())
    assert result['region_count'] == 1
    region = result['regions'][0]
    assert region['triangle_count'] == 2
    assert region['area_mm2'] == pytest.approx(1.0)
    assert region['projected_area_mm2'] == pytest.approx(1.0)
    assert region['bottom_layer_intersection'] is False
    assert region['lift_speed'] == pytest.approx(.05)
    assert region['z_layer_span'] == [20, 20]

    deep = analyze_peel(square(z=.1), [[0, 0, 0], [10, 10, 10]], settings())
    assert deep['regions'][0]['bottom_layer_intersection'] is True
    assert deep['regions'][0]['lift_speed'] == pytest.approx(.2)
    assert deep['regions'][0]['score'] > region['score']
    lifted = analyze_peel(square(z=.3), [[0, 0, 0], [10, 10, 10]], settings())
    assert lifted['regions'][0]['bottom_layer_intersection'] is False
    assert lifted['regions'][0]['lift_speed'] == pytest.approx(.05)


def test_shared_vertex_does_not_merge_and_rotated_plate_is_not_flat():
    separate = np.concatenate((square(x=0), square(x=2)), axis=0)
    result = analyze_peel(separate, [[0, 0, 0], [10, 10, 10]], settings())
    assert result['region_count'] == 2
    tilted = square().copy()
    tilted[:, :, 2] += tilted[:, :, 0] * np.tan(np.radians(20))
    assert analyze_peel(tilted, [[0, 0, 0], [10, 10, 10]], settings())['candidate_triangle_count'] == 0


def test_threshold_filters_details_but_counts_all_and_caps_details():
    triangles = np.concatenate([square(x=i * 2) for i in range(70)], axis=0)
    result = analyze_peel(triangles, [[0, 0, 0], [150, 10, 10]], settings(area_threshold_mm2=0.5))
    assert result['total_region_count'] == 70
    assert result['threshold_region_count'] == 70
    assert result['region_count'] == 70
    assert len(result['regions']) == 64
    assert result['regions_truncated'] is True
    assert result['maximum_projected_area_mm2'] == pytest.approx(1.0)
    unthresholded = analyze_peel(triangles, [[0, 0, 0], [150, 10, 10]], settings(area_threshold_mm2=1.1))
    assert unthresholded['threshold_region_count'] == 0
    assert unthresholded['regions'] == []


def test_disabled_and_apply_report_paths():
    disabled = analyze_peel(square(), [[0, 0, 0], [10, 10, 10]],
                            {'peel': {'enabled': False}})
    assert disabled['status'] == 'not_run'
    report = ValidationReport()
    apply_peel_check(report, square(), [[0, 0, 0], [10, 10, 10]], settings())
    assert report.checks['peel_risk'] == 'warn'
    assert report.diagnostics[0].severity == 'warning'

    report = {'metrics': {}, 'checks': {}, 'diagnostics': []}
    apply_peel_check(report, square(), [[0, 0, 0], [10, 10, 10]],
                     {'peel': {'enabled': False}})
    assert report['checks']['peel_risk'] == 'not_run'


def test_zero_speed_still_warns_and_noncancel_errors_become_not_run():
    zero = settings()
    zero['printer']['motion'] = {}
    report = analyze_peel(square(), [[0, 0, 0], [10, 10, 10]], zero)
    assert report['regions'][0]['lift_speed'] == 0
    validation = {'metrics': {}, 'checks': {}, 'diagnostics': []}
    apply_peel_check(validation, square(), [[0, 0, 0], [10, 10, 10]], zero)
    assert validation['checks']['peel_risk'] == 'warn'

    bad = {'metrics': {}, 'checks': {}, 'diagnostics': []}
    apply_peel_check(bad, square(), [[0, 0, 0], [10, 10, 10]],
                     {'peel': {'enabled': True, 'reference_lift_speed': 0}})
    assert bad['checks']['peel_risk'] == 'not_run'


def test_cancellation_and_budget_are_checked():
    token = CancellationToken(); token.cancel()
    with pytest.raises(Canceled):
        analyze_peel(square(), [[0, 0, 0], [10, 10, 10]], settings(), cancel=token)

    class Budget:
        def require(self, *_args):
            raise VoxelMillError('memory_budget', 'synthetic budget')
    with pytest.raises(VoxelMillError, match='synthetic budget'):
        analyze_peel(square(), [[0, 0, 0], [10, 10, 10]], settings(), budget=Budget())


def test_physical_layer_origin_translation_and_boundary():
    cfg = settings()
    cfg['printer']['build_mm'] = [10, 10, 10]
    low = analyze_peel(square(z=1), [[0, 0, 1], [1, 1, 1]], cfg)['regions'][0]
    high = analyze_peel(square(z=2), [[0, 0, 2], [1, 1, 2]], cfg)['regions'][0]
    assert low['bottom_layer_intersection'] is False
    assert low['z_layer_span'] == [20, 20]
    assert high['score'] > low['score']
    boundary = analyze_peel(square(z=.2), [[0, 0, .2], [1, 1, .2]], cfg)['regions'][0]
    assert boundary['bottom_layer_intersection'] is False
    assert boundary['lift_speed'] == .05


def test_vertex_touch_and_subthreshold_regions_never_merge():
    touching = np.concatenate((square(), square(x=1, y=1)))
    result = analyze_peel(touching, [[0, 0, 1], [2, 2, 1]], settings(area_threshold_mm2=1.5))
    assert result['region_count'] == 2
    assert result['threshold_region_count'] == 0
    assert result['maximum_projected_area_mm2'] == 1
