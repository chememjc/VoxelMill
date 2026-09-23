"""XY shrinkage and tolerance compensation on exported frames."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voxelmill.contracts import VoxelMillError
from voxelmill.goo import (
    GooReader,
    apply_dimensional_compensation,
    apply_tolerance,
    export_mask,
    shrink_mask_xy,
    slice_stl,
    tolerance_radius_px,
)
from voxelmill.mesh import write_stl

from test_goo import cube_triangles, small_settings

ROOT = Path(__file__).resolve().parents[1]


def dimensional(**process):
    settings = small_settings()
    settings['process'].update(process)
    return settings


def test_zero_shrink_and_tolerance_are_no_ops():
    settings = small_settings()
    assert settings['process']['shrink_percent_xy'] == 0.0
    assert settings['process']['tolerance_offset_mm'] == 0.0
    mask = np.ones((9, 9), dtype=np.uint8)
    shrunk, shrink = shrink_mask_xy(mask, 0.0, (4.0, 4.0))
    assert shrunk is mask and not shrink['applied']
    out, record = apply_dimensional_compensation(mask, settings, 0)
    assert out is mask and not record['applied'] and record['reason'] == 'disabled'
    assert not record['uncalibrated']
    assert record['shrink_z']['applied'] is False
    assert record['shrink_z']['reason'] is None


def test_positive_tolerance_removes_pixels():
    settings = dimensional(tolerance_offset_mm=0.2)  # 2 px on 0.1 mm pitch
    mask = np.ones((9, 9), dtype=np.uint8)
    out, record = apply_tolerance(mask, settings, 0)
    assert record['applied'] and record['operation'] == 'erode'
    assert record['radius_px'] == [2, 2]
    assert out.sum() == 25
    assert record['pixels_before'] == 81 and record['pixels_changed'] == 56
    assert record['uncalibrated']


def test_negative_tolerance_dilates():
    settings = dimensional(tolerance_offset_mm=-0.2)
    mask = np.zeros((11, 11), dtype=np.uint8)
    mask[4:7, 4:7] = 1
    out, record = apply_tolerance(mask, settings, 0)
    assert record['operation'] == 'dilate' and record['applied']
    assert out.sum() > mask.sum()


def test_bottom_tolerance_overrides_on_bottom_layers_only():
    settings = dimensional(tolerance_offset_mm=0.1, bottom_tolerance_offset_mm=0.3,
                           bottom_layers=2)
    radii0, record0 = tolerance_radius_px(settings, 0)
    radii2, record2 = tolerance_radius_px(settings, 2)
    assert radii0 == (3, 3) and record0['source'] == 'bottom_tolerance_offset_mm'
    assert radii2 == (1, 1) and record2['source'] == 'tolerance_offset_mm'


def test_shrink_percent_xy_changes_occupancy():
    mask = np.zeros((21, 21), dtype=np.uint8)
    mask[6:15, 6:15] = 1
    enlarged, up = shrink_mask_xy(mask, 20.0, (10.0, 10.0))
    reduced, down = shrink_mask_xy(mask, -20.0, (10.0, 10.0))
    assert up['applied'] and up['uncalibrated'] and up['pixels_changed'] > 0
    assert enlarged.sum() > mask.sum() > reduced.sum()
    assert down['applied'] and down['pixels_changed'] > 0


def test_shrink_z_is_reported_not_applied():
    settings = dimensional(shrink_percent_z=1.5)
    mask = np.ones((5, 5), dtype=np.uint8)
    out, record = apply_dimensional_compensation(mask, settings, 0)
    assert out is mask or np.array_equal(out, mask)
    assert not record['applied']
    assert record['shrink_z'] == {
        'requested': 1.5, 'applied': False, 'uncalibrated': True,
        'reason': ('Z shrinkage cannot be applied without changing layer count; '
                   'layers left unchanged'),
    }
    assert record['uncalibrated']


def test_compensation_that_erases_a_layer_is_refused(tmp_path):
    source = tmp_path / 'cube.stl'
    write_stl(source, cube_triangles())
    output = tmp_path / 'erased.goo'
    output.write_bytes(b'previous-good-output')
    settings = dimensional(tolerance_offset_mm=0.9)
    with pytest.raises(VoxelMillError, match='removed an entire layer'):
        slice_stl(source, output, settings)
    assert output.read_bytes() == b'previous-good-output'


def test_write_and_verify_match_with_shrink_and_tolerance(tmp_path):
    source = tmp_path / 'cube.stl'
    write_stl(source, cube_triangles())
    settings = dimensional(shrink_percent_xy=10.0, tolerance_offset_mm=0.1,
                           shrink_percent_z=2.0)
    result = slice_stl(source, tmp_path / 'compensated.goo', settings)
    assert result['written']
    assert result['verification']['decoded_pixels'] == 'pass'
    dim = result['dimensional_compensation']
    assert dim['uncalibrated']
    assert dim['shrink_percent_xy'] == 10.0
    assert dim['tolerance_offset_mm'] == 0.1
    assert dim['shrink_percent_z']['applied'] is False
    assert 'layer count' in dim['shrink_percent_z']['reason']
    assert dim['layers_changed'] > 0
    assert dim['pixels_changed'] > 0
    slice_stl(source, tmp_path / 'plain.goo', small_settings())
    with GooReader(tmp_path / 'compensated.goo') as compensated, \
            GooReader(tmp_path / 'plain.goo') as reference:
        assert len(compensated.layers) == len(reference.layers)
        # At least one layer's occupancy differs once compensation is on.
        assert any(
            np.count_nonzero(compensated.decode(i)) != np.count_nonzero(reference.decode(i))
            for i in range(len(compensated.layers)))


def test_export_mask_composes_dimensional_then_elephant_foot():
    settings = dimensional(tolerance_offset_mm=0.1, elephant_foot_compensation_mm=0.2,
                           elephant_foot_layers=1)
    mask = np.ones((11, 11), dtype=np.uint8)
    out, dim_record, elephant, erased = export_mask(mask, settings, 0)
    assert dim_record['applied'] and elephant['applied']
    assert not erased
    assert out.sum() < mask.sum()
