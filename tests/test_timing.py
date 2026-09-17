"""Uncalibrated print-time schedule estimation."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from voxelmill.config import resolve_settings
from voxelmill.timing import estimate_print_time_s


ROOT = Path(__file__).resolve().parents[1]


def small_settings():
    """Match test_goo.small_settings process shape: few layers, tiny panel."""
    return resolve_settings(
        ROOT / 'profiles/mars5-ultra.ptr', ROOT / 'profiles/sunlu-abs-like-gray.res',
        {'printer': {'build_mm': [3.2, 2.4, 10.0], 'pixels': [32, 24],
                     'pixel_pitch_mm': [0.1, 0.1], 'edge_clearance_mm': 0.0},
         'process': {'layer_height_mm': 0.1, 'bottom_layers': 1, 'transition_layers': 2,
                     'bottom_exposure_s': 8.0, 'normal_exposure_s': 2.0}})


def test_estimate_is_positive_integer_for_few_layers():
    result = estimate_print_time_s(small_settings(), 5)
    assert result['uncalibrated'] is True
    assert isinstance(result['seconds'], int)
    assert result['seconds'] > 0
    assert result['layer_count'] == 5
    assert 'layer_exposure' in result['basis']
    assert result['establishes']
    assert result['does_not_establish']


def test_zero_layer_count_returns_zero():
    result = estimate_print_time_s(small_settings(), 0)
    assert result['seconds'] == 0
    assert result['uncalibrated'] is True
    assert result['layer_count'] == 0


def test_zero_motion_heights_do_not_crash():
    settings = deepcopy(small_settings())
    motion = settings['printer']['motion']
    for key in list(motion):
        if 'height' in key:
            motion[key] = 0.0
    result = estimate_print_time_s(settings, 3)
    assert isinstance(result['seconds'], int)
    assert result['seconds'] > 0
    assert result['zero_motion_terms']
    assert all('height' in name for name in result['zero_motion_terms'])


def test_explicit_zero_speed_with_height_refuses():
    from voxelmill.contracts import VoxelMillError

    settings = deepcopy(small_settings())
    settings['printer']['motion']['lift_height'] = 1.0
    settings['printer']['motion']['lift_speed'] = 0.0
    with pytest.raises(VoxelMillError, match='zero speed'):
        estimate_print_time_s(settings, 1)


def test_slice_stl_fills_uncalibrated_estimate_unless_overridden(tmp_path):
    from voxelmill.goo import GooReader, slice_stl
    from voxelmill.mesh import write_stl
    from test_goo import cube_triangles

    settings = small_settings()
    source = tmp_path / 'cube.stl'
    write_stl(source, cube_triangles())
    estimated = slice_stl(source, tmp_path / 'est.goo', settings)
    assert estimated['written']
    assert estimated['estimated_print_time']['uncalibrated'] is True
    assert estimated['print_time_s'] == estimated['estimated_print_time']['seconds'] > 0
    with GooReader(tmp_path / 'est.goo') as reader:
        assert reader.header['print_time'] == estimated['print_time_s']

    overridden = slice_stl(source, tmp_path / 'ovr.goo', settings, print_time_s=12345)
    assert overridden['estimated_print_time'] is None
    assert overridden['print_time_s'] == 12345
    with GooReader(tmp_path / 'ovr.goo') as reader:
        assert reader.header['print_time'] == 12345
