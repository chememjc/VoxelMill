"""Boolean, planar-trim, and open-cut capping over Manifold cubes."""
from __future__ import annotations

import json

import numpy as np
import pytest
import manifold3d as m

from voxelmill import _native
from voxelmill.cli import main
from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.geometry import manifold_triangles
from voxelmill.mesh import write_stl
from voxelmill.ops import boolean_mesh, cap_open_cuts, trim_mesh


def small_settings(**overrides):
    base = {
        'printer': {'build_mm': [40.0, 40.0, 40.0], 'pixels': [40, 40],
                    'pixel_pitch_mm': [1.0, 1.0], 'edge_clearance_mm': 0.0},
        'process': {'layer_height_mm': 0.1},
        'repair': {'aggressiveness': 'conservative', 'seal_voids': False},
    }
    for section, values in overrides.items():
        base.setdefault(section, {}).update(values)
    return resolve_settings(overrides=base)


def cube_tri(size=(4.0, 4.0, 4.0), center=(0.0, 0.0, 0.0)):
    solid = m.Manifold.cube(size, True).translate(center)
    return manifold_triangles(solid).astype(np.float32), float(solid.volume())


def open_box_missing_faces(*axes_signs):
    """Cube with the listed face(s) deleted. Each item is ('z', +1) etc."""
    triangles, volume = cube_tri()
    keep = np.ones(len(triangles), dtype=bool)
    for axis, sign in axes_signs:
        index = 'xyz'.index(axis)
        mean = triangles[:, :, index].mean(axis=1)
        keep &= mean * sign < 1.99
    opened = triangles[keep].copy()
    assert _native.inspect_mesh(opened)['boundary_edges'] > 0
    return opened, volume


def test_boolean_union_intersect_subtract_volumes():
    settings = small_settings()
    a, vol_a = cube_tri((4, 4, 4), (0, 0, 0))
    b, vol_b = cube_tri((4, 4, 4), (2, 0, 0))
    overlap = 2.0 * 4.0 * 4.0
    union, report_u = boolean_mesh(a, b, 'union', settings)
    assert report_u['operation'] == 'union'
    assert report_u['volume_mm3'] == pytest.approx(vol_a + vol_b - overlap, rel=1e-6)
    assert report_u['added_volume_mm3'] == pytest.approx(vol_b - overlap, rel=1e-6)
    assert report_u['removed_volume_mm3'] == pytest.approx(0.0, abs=1e-9)
    assert report_u['triangle_count'] == len(union) > 0

    inter, report_i = boolean_mesh(a, b, 'intersect', settings)
    assert report_i['operation'] == 'intersect'
    assert report_i['volume_mm3'] == pytest.approx(overlap, rel=1e-6)
    assert report_i['removed_volume_mm3'] == pytest.approx(vol_a - overlap, rel=1e-6)
    assert report_i['triangle_count'] == len(inter) > 0

    diff, report_s = boolean_mesh(a, b, 'subtract', settings)
    assert report_s['operation'] == 'subtract'
    assert report_s['volume_mm3'] == pytest.approx(vol_a - overlap, rel=1e-6)
    assert report_s['removed_volume_mm3'] == pytest.approx(overlap, rel=1e-6)
    assert report_s['triangle_count'] == len(diff) > 0


def test_trim_keeps_half_and_caps_both_sides():
    settings = small_settings()
    triangles, volume = cube_tri((4, 4, 4), (0, 0, 0))
    positive, report = trim_mesh(triangles, (0, 0, 0), (0, 0, 1), settings, keep='positive')
    assert report['operation'] == 'trim'
    assert report['keep'] == 'positive'
    assert report['volume_mm3'] == pytest.approx(volume / 2.0, rel=1e-5)
    assert report['removed_volume_mm3'] == pytest.approx(volume / 2.0, rel=1e-5)
    assert report['triangle_count'] == len(positive) > 0

    negative, report_n = trim_mesh(triangles, (0, 0, 0), (0, 0, 1), settings, keep='negative')
    assert report_n['volume_mm3'] == pytest.approx(volume / 2.0, rel=1e-5)

    (pos, neg), both = trim_mesh(triangles, (0, 0, 0), (1, 0, 0), settings, keep='both')
    assert both['keep'] == 'both'
    assert both['positive']['volume_mm3'] == pytest.approx(volume / 2.0, rel=1e-5)
    assert both['negative']['volume_mm3'] == pytest.approx(volume / 2.0, rel=1e-5)
    assert len(pos) > 0 and len(neg) > 0


def test_boolean_rejects_unrepaired_invalid_solid():
    settings = small_settings(repair={'aggressiveness': 'none'})
    a, _ = cube_tri()
    b, _ = cube_tri(center=(1, 0, 0))
    with pytest.raises(VoxelMillError, match='closed solid') as error:
        boolean_mesh(a, b, 'union', settings)
    assert error.value.code == 'invalid_solid'


def test_trim_rejects_zero_normal_and_empty_keep():
    settings = small_settings()
    triangles, _ = cube_tri()
    with pytest.raises(VoxelMillError, match='nonzero'):
        trim_mesh(triangles, (0, 0, 0), (0, 0, 0), settings)
    # Plane entirely above the cube: positive keep is empty.
    with pytest.raises(VoxelMillError) as error:
        trim_mesh(triangles, (0, 0, 10), (0, 0, 1), settings, keep='positive')
    assert error.value.code == 'boolean_failed'


def test_cap_open_box_restores_cube_volume():
    settings = small_settings()
    opened, volume = open_box_missing_faces(('z', 1))
    capped, report = cap_open_cuts(opened, settings)
    inventory = _native.inspect_mesh(capped)
    assert report['operation'] == 'cap_open_cuts'
    assert report['capped_loops'] == 1
    assert report['remaining_loops'] == 0
    assert report['triangles_added'] == 2
    assert inventory['boundary_edges'] == 0
    assert inventory['inconsistent_winding_edges'] == 0
    assert inventory['signed_volume_mm3'] == pytest.approx(volume, rel=1e-6)
    assert report['volume_mm3'] == pytest.approx(volume, rel=1e-6)


def test_cap_explicit_repair_drops_zero_area_triangles():
    settings = small_settings()
    opened, volume = open_box_missing_faces(('z', 1))
    invalid = np.array([opened[0, 0], opened[0, 0], opened[0, 1]], dtype=np.float32)
    with_invalid = np.concatenate([opened, invalid[None]], axis=0)
    capped, report = cap_open_cuts(with_invalid, settings)
    assert report['dropped_invalid_triangles'] == 1
    assert report['repair'] == 'conservative'
    assert _native.inspect_mesh(capped)['boundary_edges'] == 0
    assert report['volume_mm3'] == pytest.approx(volume, rel=1e-6)
    strict = dict(settings)
    strict['repair'] = dict(settings['repair'], aggressiveness='none')
    with pytest.raises(VoxelMillError, match='invalid triangles'):
        cap_open_cuts(with_invalid, strict)


def test_cap_filter_uses_double_cross_for_tiny_float32_triangles():
    settings = small_settings()
    opened, _ = open_box_missing_faces(('z', 1))
    tiny = np.array([[[0.0, 0.0, 0.0], [1e-20, 0.0, 0.0],
                      [0.0, 1e-20, 0.0]]], dtype=np.float32)
    with pytest.raises(VoxelMillError) as error:
        cap_open_cuts(np.concatenate([opened, tiny]), settings)
    assert error.value.code == 'cap_incomplete'
    assert error.value.details['dropped_invalid_triangles'] == 0


def test_cap_two_opposite_faces():
    settings = small_settings()
    opened, volume = open_box_missing_faces(('z', 1), ('z', -1))
    capped, report = cap_open_cuts(opened, settings)
    inventory = _native.inspect_mesh(capped)
    assert report['capped_loops'] == 2
    assert report['triangles_added'] == 4
    assert inventory['boundary_edges'] == 0
    assert inventory['signed_volume_mm3'] == pytest.approx(volume, rel=1e-6)


def test_cap_refuses_nonplanar_hole_without_writing(tmp_path):
    settings = small_settings()
    opened, _ = open_box_missing_faces(('z', 1))
    warped = opened.copy()
    corner = np.array([-2.0, -2.0, 2.0], dtype=np.float32)
    for tri in warped:
        for vertex in tri:
            if np.allclose(vertex, corner):
                vertex += np.array([0.0, 0.0, 1.0], dtype=np.float32)
    assert _native.inspect_mesh(warped)['boundary_edges'] > 0
    source = tmp_path / 'warped.stl'
    destination = tmp_path / 'out.stl'
    write_stl(source, warped)
    original = source.read_bytes()
    with pytest.raises(VoxelMillError) as error:
        cap_open_cuts(warped, settings)
    assert error.value.code == 'cap_incomplete'
    assert error.value.details['capped_loops'] == 0
    assert error.value.details['remaining_loops'] == 1
    assert error.value.details['loops'][0]['max_deviation_mm'] > settings['repair']['max_deviation_mm']
    code = main(['cap', str(source), '--output', str(destination),
                 '--report', str(tmp_path / 'cap.json')])
    assert code == 3
    assert not destination.exists()
    assert source.read_bytes() == original


def test_cap_closed_cube_is_noop():
    settings = small_settings()
    triangles, volume = cube_tri()
    capped, report = cap_open_cuts(triangles, settings)
    assert report['capped_loops'] == 0
    assert report['triangles_added'] == 0
    assert report['boundary_loops'] == 0
    assert len(capped) == len(triangles)
    assert _native.inspect_mesh(capped)['signed_volume_mm3'] == pytest.approx(volume, rel=1e-6)


def test_cli_cap_writes_closed_solid(tmp_path):
    opened, volume = open_box_missing_faces(('z', 1))
    source = tmp_path / 'open.stl'
    destination = tmp_path / 'closed.stl'
    report_path = tmp_path / 'cap.json'
    write_stl(source, opened)
    assert main(['cap', str(source), '--output', str(destination),
                 '--report', str(report_path)]) == 0
    report = json.loads(report_path.read_text())
    assert report['command'] == 'cap'
    assert report['capped_loops'] == 1
    assert report['boundary_edges_after'] == 0
    assert report['volume_mm3'] == pytest.approx(volume, rel=1e-6)
    from voxelmill.mesh import inspect_mesh, open_stl
    with open_stl(destination) as mesh:
        inventory = inspect_mesh(mesh, self_intersections=False)
    assert inventory['boundary_edges'] == 0
    assert inventory['signed_volume_mm3'] == pytest.approx(volume, rel=1e-6)
