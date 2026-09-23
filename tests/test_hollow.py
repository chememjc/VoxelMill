"""Hollowing, drain/vent holes, wall thickness, and lattice infill."""
from __future__ import annotations

import json

import manifold3d as m
import numpy as np
import pytest

from voxelmill.cli import main
from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.geometry import manifold_triangles, triangle_bounds
from voxelmill.hollow import (_bottom_open, _infill_mask, analyze_wall_thickness, hollow_mesh)
from voxelmill.mesh import write_stl
from voxelmill.validation import analyze_drainage, drainage_check


def small(**hollow_overrides):
    hollow = {
        'enabled': True,
        'wall_thickness_mm': 1.5,
        'voxel_size_mm': 0.5,
        'mode': 'inner',
        'min_wall_thickness_mm': 1.0,
        'drain_diameter_mm': 2.5,
        'vent_diameter_mm': 2.5,
        'infill': 'none',
        'infill_pitch_mm': 4.0,
    }
    hollow.update(hollow_overrides)
    return resolve_settings(overrides={
        'printer': {'build_mm': [40.0, 40.0, 40.0], 'pixels': [40, 40],
                    'pixel_pitch_mm': [1.0, 1.0], 'edge_clearance_mm': 0.0},
        'process': {'layer_height_mm': 0.1},
        'repair': {'aggressiveness': 'conservative', 'seal_voids': False,
                   'min_orifice_area_mm2': 1.0},
        'hollow': hollow,
        'resources': {'memory_gib': 1.0},
    })


def solid_cube(size=10.0):
    return manifold_triangles(m.Manifold.cube([size, size, size], True)).astype(np.float32)


def test_enabled_false_is_noop():
    settings = small(enabled=False)
    triangles = solid_cube(8.0)
    out, report = hollow_mesh(triangles, settings, add_holes=False, force=False)
    assert report['status'] == 'skipped'
    assert len(out) == len(triangles)
    assert np.allclose(out, triangles)


def test_hollow_closed_cube_creates_cavity_and_drops_volume():
    settings = small(wall_thickness_mm=1.5, voxel_size_mm=0.5, min_wall_thickness_mm=1.0)
    triangles = solid_cube(10.0)
    before = float(m.Manifold.cube([10, 10, 10], True).volume())
    out, report = hollow_mesh(triangles, settings, add_holes=False, force=True)
    assert report['status'] == 'complete'
    assert report['cavity_volume_mm3'] > 100.0
    assert report['volume_after_mm3'] < before - 50.0
    measured = report['measured_min_wall_thickness_mm']
    assert measured == pytest.approx(1.5, abs=0.6)
    # Enclosed cavity: drainage must still see it.
    drain = analyze_drainage(out, triangle_bounds(out), settings)
    assert drain['enclosed_components'] >= 1
    assert drainage_check(drain) == 'fail'


def test_min_wall_thickness_below_achievable_fails():
    # One erosion step at 1.5 mm pitch yields ~1.5 mm walls; floor is 1.9 mm.
    settings = small(wall_thickness_mm=2.0, voxel_size_mm=1.5, min_wall_thickness_mm=1.9)
    with pytest.raises(VoxelMillError) as error:
        hollow_mesh(solid_cube(10.0), settings, add_holes=False, force=True)
    assert error.value.code == 'thin_wall'


def test_drain_and_vent_make_drainage_pass():
    settings = small(wall_thickness_mm=1.5, voxel_size_mm=0.5, min_wall_thickness_mm=1.0,
                     drain_diameter_mm=3.0, vent_diameter_mm=3.0)
    out, report = hollow_mesh(solid_cube(10.0), settings, add_holes=True, force=True)
    assert report['holes']['enclosed_voids_before_holes'] >= 1
    assert len(report['holes']['holes']) >= 1
    assert report['drainage_check'] == 'pass'
    assert drainage_check(report['drainage']) == 'pass'


def test_thickness_reports_thin_fin_below_threshold():
    # 0.3 mm fin on thin_wall.stl; threshold 1 mm must warn.
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'fixtures' / 'shapes' / 'thin_wall.stl'
    from voxelmill.mesh import open_stl
    from voxelmill.contracts import ResourceBudget
    settings = small(voxel_size_mm=0.15, min_wall_thickness_mm=1.0, wall_thickness_mm=2.0)
    with open_stl(path, ResourceBudget(**settings['resources'])) as mesh:
        report = analyze_wall_thickness(mesh.triangles, settings, threshold_mm=1.0)
    assert report['min_thickness_mm'] < 1.0
    assert report['status'] == 'warn'
    assert report['regions_below_count'] >= 1


def test_thickness_on_synthetic_thin_wall():
    # Explicit 0.4 mm wall cube shell.
    outer = m.Manifold.cube([6, 6, 6], True)
    inner = m.Manifold.cube([5.2, 5.2, 5.2], True)  # 0.4 mm walls
    triangles = manifold_triangles(outer - inner).astype(np.float32)
    settings = small(voxel_size_mm=0.2, wall_thickness_mm=2.0, min_wall_thickness_mm=1.0)
    report = analyze_wall_thickness(triangles, settings, threshold_mm=1.0)
    assert report['min_thickness_mm'] < 1.0
    assert report['status'] == 'warn'


def test_infill_grid_between_empty_hollow_and_solid():
    settings_empty = small(wall_thickness_mm=1.5, voxel_size_mm=0.5, infill='none')
    settings_grid = small(wall_thickness_mm=1.5, voxel_size_mm=0.5, infill='grid',
                          infill_pitch_mm=3.0)
    cube = solid_cube(10.0)
    solid_volume = float(m.Manifold.cube([10, 10, 10], True).volume())
    empty, empty_report = hollow_mesh(cube, settings_empty, add_holes=False, force=True)
    grid, grid_report = hollow_mesh(cube, settings_grid, add_holes=False, force=True)
    assert grid_report['infill_voxels'] > 0
    assert grid_report['volume_after_mm3'] > empty_report['volume_after_mm3']
    assert grid_report['volume_after_mm3'] < solid_volume
    assert len(grid) > len(empty)


@pytest.mark.parametrize('kind', ['grid', 'hex', 'gyroid'])
def test_every_infill_lattice_hollows_a_cube(kind):
    empty, empty_report = hollow_mesh(solid_cube(10.0), small(infill='none'),
                                      add_holes=False, force=True)
    out, report = hollow_mesh(solid_cube(10.0), small(infill=kind, infill_pitch_mm=3.0),
                              add_holes=False, force=True)
    assert report['infill_voxels'] > 0
    assert report['volume_after_mm3'] > empty_report['volume_after_mm3']


def _bottom_open_reference(occupancy, core):
    """The original per-column loop, kept as the definition the vector form must match."""
    removal = core.copy()
    for j in range(occupancy.shape[1]):
        for i in range(occupancy.shape[2]):
            column = core[:, j, i]
            if column.any():
                top = int(np.flatnonzero(column)[-1])
                removal[:top + 1, j, i] |= occupancy[:top + 1, j, i]
    removal[0] |= occupancy[0]
    return removal


def _hex_reference(cavity, period):
    """The original per-cell hex loop, with its Z decks broadcast correctly."""
    nz, ny, nx = cavity.shape
    col_pitch = max(1, int(round(period * np.sqrt(3) / 2)))
    struts = np.zeros_like(cavity)
    for j in range(ny):
        phase = (j // period) % 2
        for i in range(nx):
            if (i - phase * (col_pitch // 2)) % col_pitch == 0 or j % period == 0:
                struts[:, j, i] = True
    struts[np.arange(nz) % period == 0] = True
    return cavity & struts


def test_vectorized_voxel_loops_match_their_reference():
    rng = np.random.default_rng(7)
    for shape in [(1, 1, 1), (5, 7, 9), (12, 13, 11)]:
        occupancy = rng.random(shape) < 0.6
        core = occupancy & (rng.random(shape) < 0.2)
        assert np.array_equal(_bottom_open(occupancy, core),
                              _bottom_open_reference(occupancy, core))
        for pitch_mm in (1.0, 1.5, 3.0):
            got = _infill_mask(occupancy, 0.5, (0.0, 0.0, 0.0), 'hex', pitch_mm)
            period = max(1, int(round(pitch_mm / 0.5)))
            assert np.array_equal(got, _hex_reference(occupancy, period))


def test_thickness_refinement_stays_inside_the_memory_ceiling(monkeypatch):
    """A small threshold used to refine the pitch past the budget unchecked."""
    import voxelmill.hollow as hollow_module
    settings = small(wall_thickness_mm=3.0, voxel_size_mm=0.0)
    budget_voxels = 40_000
    fraction = budget_voxels / (settings['resources']['memory_gib'] * 1024**3)
    monkeypatch.setattr(hollow_module, 'MAX_VOXEL_BYTES_FRACTION', fraction)
    field = analyze_wall_thickness(solid_cube(20.0), settings, threshold_mm=0.2)
    padded = np.prod(np.asarray(field['voxel_grid']) + 2)
    assert padded <= budget_voxels
    # Finer than the hollow pitch it started from, but no finer than affordable.
    assert 0.05 < field['voxel_size_mm'] < 0.75


def test_prepare_honours_hollow_enabled_false(tmp_path):
    from voxelmill.pipeline import prepare
    path = tmp_path / 'cube.stl'
    write_stl(path, solid_cube(6.0))
    settings = small(enabled=False)
    settings['support']['automatic'] = False
    report = prepare(path, settings, lift_mm=0.0, drainage=False, track_voids=False,
                     allow_unresolved=True)
    assert 'hollow' not in report.get('stages', {})


def test_prepare_runs_hollow_when_enabled(tmp_path):
    from voxelmill.pipeline import prepare
    path = tmp_path / 'cube.stl'
    write_stl(path, solid_cube(8.0))
    settings = small(enabled=True, wall_thickness_mm=1.5, voxel_size_mm=0.5,
                     min_wall_thickness_mm=1.0, drain_diameter_mm=3.0, vent_diameter_mm=3.0)
    settings['support']['automatic'] = False
    report = prepare(path, settings, lift_mm=0.0, drainage=False, track_voids=False,
                     allow_unresolved=True)
    hollow = report['stages']['hollow']
    assert hollow['status'] == 'complete'
    assert hollow['cavity_volume_mm3'] > 0
    assert hollow['volume_after_mm3'] < hollow['volume_before_mm3']


def test_cli_hollow_and_thickness(tmp_path):
    source = tmp_path / 'cube.stl'
    out = tmp_path / 'hollow.stl'
    report_path = tmp_path / 'hollow.json'
    write_stl(source, solid_cube(8.0))
    code = main([
        'hollow', str(source), '--output', str(out), '--report', str(report_path),
        '--no-holes',
        '--set', 'hollow.wall_thickness_mm=1.5',
        '--set', 'hollow.voxel_size_mm=0.5',
        '--set', 'hollow.min_wall_thickness_mm=1.0',
        '--set', 'repair.seal_voids=false',
        '--set', 'resources.memory_gib=1.0',
    ])
    assert code == 0
    payload = json.loads(report_path.read_text())
    assert payload['status'] == 'complete'
    assert payload['cavity_volume_mm3'] > 0
    assert out.is_file()

    thickness_report = tmp_path / 'thick.json'
    # Build a 0.4 mm wall part for the thickness CLI.
    thin = tmp_path / 'thin.stl'
    shell = m.Manifold.cube([5, 5, 5], True) - m.Manifold.cube([4.2, 4.2, 4.2], True)
    write_stl(thin, manifold_triangles(shell))
    code = main([
        'thickness', str(thin), '--report', str(thickness_report),
        '--threshold-mm', '1.0',
        '--set', 'hollow.voxel_size_mm=0.2',
        '--set', 'hollow.wall_thickness_mm=2.0',
        '--set', 'resources.memory_gib=1.0',
    ])
    assert code == 0
    thick = json.loads(thickness_report.read_text())
    assert thick['checks']['wall_thickness'] == 'warn'
    assert thick['min_thickness_mm'] < 1.0
