"""Strict exact-ingestion gate findings and rejection behavior."""

import numpy as np
import pytest
import manifold3d as m

from voxelmill import _native
from voxelmill.contracts import CancellationToken, Canceled, VoxelMillError
from voxelmill.geometry import manifold_triangles, mesh_to_manifold


def cube_triangles():
    return manifold_triangles(m.Manifold.cube((2, 3, 4)))


def test_degenerate_count_covers_all_chunks_and_skips_intersections(monkeypatch):
    triangles = np.zeros((65536 + 3, 3, 3), dtype=np.float32)
    calls = []

    def unexpected(*args):
        calls.append(args)
        raise AssertionError('intersection inspection must be skipped')

    monkeypatch.setattr(_native, 'inspect_intersections', unexpected)
    with pytest.raises(VoxelMillError) as raised:
        mesh_to_manifold(triangles)
    error = raised.value
    assert error.code == 'degenerate_triangles'
    assert error.details['count'] == 65539
    findings = error.details['findings']
    assert findings['degenerate_triangles'] == {'status': 'fail', 'count': 65539}
    assert findings['self_intersections']['status'] == 'not_run'
    assert 'degenerate triangles' in findings['self_intersections']['reason']
    assert calls == []


def test_invalid_manifold_skips_expensive_intersection_scan(monkeypatch):
    calls = []

    def unexpected(*args):
        calls.append(args)
        raise AssertionError('intersection inspection must be skipped')

    monkeypatch.setattr(_native, 'inspect_intersections', unexpected)
    with pytest.raises(VoxelMillError) as raised:
        mesh_to_manifold(cube_triangles()[:-1])
    error = raised.value
    assert error.code == 'invalid_solid'
    findings = error.details['findings']
    assert findings['manifold']['status'] == 'fail'
    assert findings['self_intersections']['status'] == 'not_run'
    assert 'Manifold rejected' in findings['self_intersections']['reason']
    assert calls == []


def test_native_weld_value_error_is_a_structured_rejection(monkeypatch):
    def rejected(*args):
        raise ValueError('vertex index overflow')

    monkeypatch.setattr(_native, 'weld_mesh', rejected)
    with pytest.raises(VoxelMillError) as raised:
        mesh_to_manifold(cube_triangles())
    error = raised.value
    assert error.code == 'weld_rejected'
    assert str(error) == 'Native mesh welding rejected input'
    assert error.details['error'] == 'vertex index overflow'
    assert error.details['findings']['weld']['status'] == 'fail'
    assert error.details['findings']['self_intersections']['status'] == 'not_run'


def test_self_intersections_remain_a_strict_rejection(monkeypatch):
    monkeypatch.setattr(_native, 'inspect_intersections',
                        lambda *args: {'self_intersections': 7})
    with pytest.raises(VoxelMillError) as raised:
        mesh_to_manifold(cube_triangles())
    error = raised.value
    assert error.code == 'self_intersections'
    assert str(error) == 'Input contains intersecting surfaces; choose explicit aggressive repair'
    assert error.details['findings']['self_intersections'] == {'status': 'fail', 'count': 7}


def test_cancellation_is_not_converted_to_a_gate_error():
    token = CancellationToken()
    token.cancel()
    with pytest.raises(Canceled):
        mesh_to_manifold(cube_triangles(), cancel=token)
