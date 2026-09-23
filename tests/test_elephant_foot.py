"""First-layer (elephant-foot) compensation on the exported frames."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.goo import GooReader, compensate_mask, compensation_radius_px, slice_stl
from voxelmill.mesh import write_stl

from test_goo import cube_triangles, small_settings

ROOT = Path(__file__).resolve().parents[1]


def compensated(mm=0.2, layers=3, **process):
    settings = small_settings()
    settings['process'].update(
        {'elephant_foot_compensation_mm': mm, 'elephant_foot_layers': layers}, **process)
    return settings


def test_compensation_is_off_by_default():
    settings = small_settings()
    assert settings['process']['elephant_foot_compensation_mm'] == 0.0
    assert settings['process']['elephant_foot_layers'] == 0
    radii, record = compensation_radius_px(settings, 0)
    assert radii == (0, 0) and not record['applied'] and record['reason'] == 'disabled'


def test_the_ramp_reaches_zero_at_the_configured_layer():
    settings = compensated(mm=0.3, layers=3)  # 0.1 mm pixels
    assert compensation_radius_px(settings, 0)[0] == (3, 3)
    assert compensation_radius_px(settings, 1)[0] == (2, 2)
    assert compensation_radius_px(settings, 2)[0] == (1, 1)
    radii, record = compensation_radius_px(settings, 3)
    assert radii == (0, 0) and record['reason'] == 'above the compensated layers'


def test_zero_layers_derives_the_count_from_the_bottom_layer_count():
    settings = compensated(mm=0.2, layers=0)
    assert settings['process']['bottom_layers'] == 1
    assert compensation_radius_px(settings, 0)[1]['layers'] == 1
    assert compensation_radius_px(settings, 1)[0] == (0, 0)


def test_a_request_that_rounds_to_no_pixels_says_so_instead_of_going_quiet():
    """0.02 mm on a 0.1 mm pitch is a fifth of a pixel, which cannot be removed."""
    settings = compensated(mm=0.02, layers=1)
    radii, record = compensation_radius_px(settings, 0)
    assert radii == (0, 0) and not record['applied']
    assert 'rounds to zero pixels' in record['reason']


def test_anisotropic_pitch_gets_its_own_radius_per_axis():
    settings = compensated(mm=0.3, layers=1)
    settings['printer']['pixel_pitch_mm'] = [0.1, 0.3]
    settings['printer']['build_mm'] = [3.2, 7.2, 10.0]
    assert compensation_radius_px(settings, 0)[0] == (3, 1)


def test_erosion_removes_a_ring_and_reports_exactly_how_much():
    settings = compensated(mm=0.2, layers=1)
    mask = np.ones((9, 9), dtype=np.uint8)
    out, record = compensate_mask(mask, settings, 0)
    assert record['applied'] and record['radius_px'] == [2, 2]
    # A 9x9 block eroded by a radius-2 disk keeps its 5x5 core.
    assert out.sum() == 25 and record['pixels_before'] == 81
    assert record['pixels_after'] == 25 and record['pixels_removed'] == 56
    assert not record['erased_layer']
    assert out.dtype == np.uint8


def test_erosion_of_the_crop_equals_erosion_of_the_whole_frame():
    """The crop is padded with empty space, so eroding it is the same answer."""
    settings = compensated(mm=0.2, layers=1)
    mask = np.zeros((7, 7), dtype=np.uint8)
    mask[2:5, 2:5] = 1
    framed = np.zeros((21, 21), dtype=np.uint8)
    framed[8:11, 8:11] = 1
    small = compensate_mask(mask, settings, 0)[0]
    large = compensate_mask(framed, settings, 0)[0]
    assert np.array_equal(large[7:14, 7:14], small)
    assert large.sum() == small.sum()


def test_a_compensated_export_verifies_and_records_every_shrunk_layer(tmp_path):
    source = tmp_path / 'cube.stl'
    write_stl(source, cube_triangles())
    plain = slice_stl(source, tmp_path / 'plain.goo', small_settings())
    settings = compensated(mm=0.2, layers=3)
    result = slice_stl(source, tmp_path / 'shrunk.goo', settings)
    assert result['written']
    # Pass three re-derives the compensated frames, so the file still matches
    # the exposure it was validated against.
    assert result['verification']['decoded_pixels'] == 'pass'
    record = result['elephant_foot']
    assert record['compensation_mm'] == 0.2 and record['layers_requested'] == 3
    assert record['layers_compensated'] == 3
    assert record['pixels_removed'] > 0
    assert [entry['layer'] for entry in record['per_layer']] == [0, 1, 2]
    # The ramp is quantised to whole pixels, so it is non-increasing rather
    # than strictly decreasing: 0.2 mm over three layers is 2, 1 and 1 pixels
    # on a 0.1 mm pitch, and the last two layers are shrunk by the same ring.
    assert [entry['radius_px'] for entry in record['per_layer']] == [[2, 2], [1, 1], [1, 1]]
    removed = [entry['pixels_removed'] for entry in record['per_layer']]
    assert removed[0] > removed[1] == removed[2] > 0
    assert plain['elephant_foot']['layers_compensated'] == 0

    with GooReader(tmp_path / 'shrunk.goo') as shrunk, \
            GooReader(tmp_path / 'plain.goo') as reference:
        assert len(shrunk.layers) == len(reference.layers)
        for index in range(3):
            small = np.count_nonzero(shrunk.decode(index))
            big = np.count_nonzero(reference.decode(index))
            assert 0 < small < big
        # Nothing above the ramp is touched.
        for index in range(3, len(shrunk.layers)):
            assert np.array_equal(shrunk.decode(index), reference.decode(index))


def test_compensation_that_erases_a_layer_is_refused_rather_than_exported(tmp_path):
    source = tmp_path / 'cube.stl'
    write_stl(source, cube_triangles())
    output = tmp_path / 'erased.goo'
    output.write_bytes(b'previous-good-output')
    settings = compensated(mm=0.9, layers=2)
    with pytest.raises(VoxelMillError, match='removed an entire layer'):
        slice_stl(source, output, settings)
    assert output.read_bytes() == b'previous-good-output'


def test_settings_reject_a_compensation_wider_than_any_bottom_feature():
    with pytest.raises(VoxelMillError, match='would erase bottom geometry'):
        resolve_settings(ROOT / 'profiles/mars5-ultra.ptr', None,
                         {'process': {'elephant_foot_compensation_mm': 1.5}})
    with pytest.raises(VoxelMillError, match='must be an integer'):
        resolve_settings(ROOT / 'profiles/mars5-ultra.ptr', None,
                         {'process': {'elephant_foot_layers': 2.5}})
    with pytest.raises(VoxelMillError):
        resolve_settings(ROOT / 'profiles/mars5-ultra.ptr', None,
                         {'process': {'elephant_foot_compensation_mm': -0.1}})


def test_the_cli_flags_reach_the_resolved_settings():
    from voxelmill.cli import _settings, build_parser
    args = build_parser().parse_args([
        'slice', 'in.stl', '--output', 'out.goo',
        '--printer', str(ROOT / 'profiles/mars5-ultra.ptr'),
        '--elephant-foot-mm', '0.08', '--elephant-foot-layers', '6'])
    settings = _settings(args)
    assert settings['process']['elephant_foot_compensation_mm'] == 0.08
    assert settings['process']['elephant_foot_layers'] == 6
    # 0.08 mm on the real 0.018 mm pitch is four pixels on the first layer.
    assert compensation_radius_px(settings, 0)[0] == (4, 4)
