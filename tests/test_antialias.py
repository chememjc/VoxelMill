"""B4 coverage grayscale AA and F1 analyze_layers worker parity."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from voxelmill.config import resolve_settings
from voxelmill.contracts import Layer, ResourceBudget
from voxelmill.goo import slice_stl
from voxelmill.mesh import write_stl
from voxelmill.raster import MeshLayerStream, box_average_coverage
from voxelmill.validation import analyze_layers, occupancy_mask


ROOT = Path(__file__).resolve().parents[1]


def small_settings(**process):
    overrides = {
        'printer': {'build_mm': [3.2, 2.4, 10.0], 'pixels': [32, 24],
                    'pixel_pitch_mm': [0.1, 0.1], 'edge_clearance_mm': 0.0},
        'process': {'layer_height_mm': 0.1, 'bottom_layers': 1, 'transition_layers': 2,
                    'bottom_exposure_s': 8.0, 'normal_exposure_s': 2.0},
        'resources': {'workers': 1},
    }
    overrides['process'].update(process)
    return resolve_settings(
        ROOT / 'profiles/mars5-ultra.ptr', ROOT / 'profiles/sunlu-abs-like-gray.res',
        overrides)


def cube_triangles():
    points = np.array([
        [-0.4, -0.4, 0.0], [0.4, -0.4, 0.0], [0.4, 0.4, 0.0], [-0.4, 0.4, 0.0],
        [-0.4, -0.4, 0.4], [0.4, -0.4, 0.4], [0.4, 0.4, 0.4], [-0.4, 0.4, 0.4],
    ], dtype=np.float32)
    faces = ((0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
             (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
             (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7))
    return points[np.asarray(faces)]


def rotated_cube_triangles():
    """45° about Z so XY slices have diagonal edges for grayscale coverage."""
    tris = cube_triangles().copy()
    c = s = np.float32(np.sqrt(0.5))
    x, y = tris[..., 0].copy(), tris[..., 1].copy()
    tris[..., 0] = c * x - s * y
    tris[..., 1] = s * x + c * y
    return tris


def test_occupancy_mask_keeps_ctb_seven_bit_gray():
    mask = np.zeros((4, 4), np.uint8)
    mask[1:3, 1:3] = 127
    assert occupancy_mask(mask).sum() == 4
    mask[0, 0] = 1
    assert occupancy_mask(mask).sum() == 5


def test_box_average_full_and_partial_coverage():
    fine = np.array([[1, 1, 0, 1],
                     [1, 1, 1, 0],
                     [0, 0, 0, 0],
                     [0, 0, 0, 0]], dtype=np.uint8)
    out = box_average_coverage(fine, 2)
    assert out.shape == (2, 2)
    assert out[0, 0] == 255
    assert out[0, 1] == 128  # round(2/4 * 255)
    assert out[1, 0] == 0


def test_levels_1_matches_binary_stream():
    settings = small_settings(antialias_levels=1)
    bounds = [[-0.5, -0.5, 0.0], [0.5, 0.5, 0.4]]
    binary = list(MeshLayerStream(cube_triangles(), bounds, settings,
                                  budget=ResourceBudget(memory_gib=1, workers=1)))
    assert binary
    for layer in binary:
        uniq = set(np.unique(layer.mask).tolist())
        assert uniq <= {0, 1}


def test_levels_2_produces_gray_on_diagonal_edge():
    settings = small_settings(antialias_levels=2)
    bounds = [[-0.7, -0.7, 0.0], [0.7, 0.7, 0.4]]
    layers = list(MeshLayerStream(rotated_cube_triangles(), bounds, settings,
                                  budget=ResourceBudget(memory_gib=1, workers=1)))
    assert layers
    grays = []
    for layer in layers:
        values = np.unique(layer.mask)
        grays.extend(int(v) for v in values if 0 < int(v) < 255)
    assert grays, 'expected partial coverage grays on a diagonal edge'
    assert any(0 < v < 255 for v in grays)


def test_analyze_layers_grayscale_stays_connected():
    settings = small_settings(antialias_levels=2)
    # Solid interior 255 with a gray fringe — one component, not edge islands.
    mask = np.zeros((12, 12), np.uint8)
    mask[3:9, 3:9] = 255
    mask[2, 3:9] = 64
    mask[9, 3:9] = 64
    mask[3:9, 2] = 64
    mask[3:9, 9] = 64
    from voxelmill.raster import RasterGrid
    grid = RasterGrid(12, 12, 0, 0, 0.1, 0.1)
    layers = [Layer(i, (i + 0.5) * 0.1, mask.copy()) for i in range(3)]
    report = analyze_layers(layers, grid, settings, budget=ResourceBudget(workers=1),
                            track_voids=False)
    assert report.checks['raster_connectivity'] == 'pass'
    assert report.metrics['island_components'] == 0
    # 6x6 interior plus the 64-gray fringe: any nonzero sample is material, so
    # a CTB 7-bit gray cannot punch a hole and AA edges expand rather than
    # birth islands.
    assert occupancy_mask(mask).sum() == 60


def test_slice_stl_levels_2_writes_and_verifies(tmp_path):
    settings = small_settings(antialias_levels=2)
    source = tmp_path / 'cube.stl'
    write_stl(source, rotated_cube_triangles())
    output = tmp_path / 'cube.goo'
    result = slice_stl(source, output, settings)
    assert result['written']
    assert result['verification']['decoded_pixels'] == 'pass'
    assert result['verification']['pixel_comparison'] == 'atol_1_grayscale'
    assert result['antialias']['levels'] == 2
    assert result['antialias']['supports_unseparated'] is True
    assert output.exists()


def test_antialias_refuses_when_supersample_exceeds_budget():
    from voxelmill.contracts import VoxelMillError
    from voxelmill.raster import RasterGrid, require_supersample_buffer
    # Full-panel 4× supersample must raise rather than silently stay binary.
    grid = RasterGrid(8520, 4320, 0.0, 0.0, 0.018, 0.018)
    with pytest.raises(VoxelMillError, match='memory budget'):
        require_supersample_buffer(ResourceBudget(memory_gib=0.25, workers=1), grid, 4)


def _diag_sort_key(d):
    return (d.code, d.layer if d.layer is not None else -1, d.message, repr(sorted(d.details.items())))


def test_analyze_layers_workers_1_and_2_match():
    settings = small_settings()
    from voxelmill.raster import RasterGrid
    grid = RasterGrid(8, 8, 0, 0, 0.1, 0.1)
    a = np.zeros((8, 8), np.uint8)
    a[2:6, 2:6] = 1
    b = a.copy()
    b[1, 1] = 1  # island birth
    layers = [Layer(0, 0.05, a), Layer(1, 0.15, b), Layer(2, 0.25, a.copy())]
    r1 = analyze_layers(deepcopy(layers), grid, settings,
                        budget=ResourceBudget(workers=1), track_voids=True)
    r2 = analyze_layers(deepcopy(layers), grid, settings,
                        budget=ResourceBudget(workers=2), track_voids=True)
    assert r1.checks == r2.checks
    assert r1.metrics['island_components'] == r2.metrics['island_components']
    assert r1.metrics['enclosed_voids']['count'] == r2.metrics['enclosed_voids']['count']
    d1 = sorted(r1.diagnostics, key=_diag_sort_key)
    d2 = sorted(r2.diagnostics, key=_diag_sort_key)
    assert [(d.code, d.layer, d.message) for d in d1] == [(d.code, d.layer, d.message) for d in d2]
    assert r2.metrics['analysis_workers'] == 2


def test_analyze_layers_workers_1_matches_default_budget():
    """Serial path agrees with the settings-derived worker budget."""
    from voxelmill.raster import RasterGrid
    settings = small_settings()
    # Force a multi-worker default: small_settings pins workers=1 in resources.
    settings = resolve_settings(
        ROOT / 'profiles/mars5-ultra.ptr', ROOT / 'profiles/sunlu-abs-like-gray.res',
        {'printer': {'build_mm': [3.2, 2.4, 10.0], 'pixels': [32, 24],
                     'pixel_pitch_mm': [0.1, 0.1], 'edge_clearance_mm': 0.0},
         'process': {'layer_height_mm': 0.1},
         'resources': {'workers': 0}})
    grid = RasterGrid(8, 8, 0, 0, 0.1, 0.1)
    a = np.zeros((8, 8), np.uint8)
    a[2:6, 2:6] = 1
    b = a.copy()
    b[1, 1] = 1
    # Tiny enclosed cavity so void / trapped arithmetic is in the path.
    c = a.copy()
    c[3:5, 3:5] = 0
    layers = [Layer(0, 0.05, a), Layer(1, 0.15, b), Layer(2, 0.25, c), Layer(3, 0.35, a.copy())]
    serial = analyze_layers(deepcopy(layers), grid, settings,
                            budget=ResourceBudget(workers=1), track_voids=True)
    default = analyze_layers(deepcopy(layers), grid, settings, track_voids=True)
    assert default.metrics['analysis_workers'] > 1
    assert serial.metrics['analysis_workers'] == 1
    assert serial.checks == default.checks
    assert serial.metrics['island_components'] == default.metrics['island_components']
    assert serial.metrics['enclosed_voids'] == default.metrics['enclosed_voids']
    assert serial.metrics['transient_traps'] == default.metrics['transient_traps']
    d1 = sorted(serial.diagnostics, key=_diag_sort_key)
    d2 = sorted(default.diagnostics, key=_diag_sort_key)
    assert [(d.code, d.layer, d.message, d.details) for d in d1] == [
        (d.code, d.layer, d.message, d.details) for d in d2]
