import hashlib
from pathlib import Path
import struct
import numpy as np
import pytest
from voxelmill.mesh import open_stl, write_stl, inspect_mesh, weld_mesh
from voxelmill.contracts import VoxelMillError, CancellationToken, Canceled, ResourceBudget


def tetra():
    v = np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]], dtype=np.float32)
    return v[[[0,2,1],[0,1,3],[0,3,2],[1,2,3]]]


def test_tetra_roundtrip_topology(tmp_path):
    p=tmp_path/'tetra.stl'
    write_stl(p,tetra())
    original=p.read_bytes()
    with open_stl(p) as mesh:
        assert isinstance(mesh._records,np.memmap)
        np.testing.assert_array_equal(mesh.triangles,tetra())
        assert mesh.asset.sha256==hashlib.sha256(original).hexdigest()
        r=inspect_mesh(mesh)
    assert p.read_bytes()==original
    assert r['triangle_count']==4 and r['unique_vertices']==4
    assert r['connected_components']==1 and r['boundary_edges']==0
    assert r['nonmanifold_edges']==r['inconsistent_winding_edges']==0
    assert r['self_intersections']==0 and r['self_intersections_status']=='complete'
    assert r['signed_volume_mm3']==pytest.approx(1/6)


def test_ascii_strict_and_scratch_cleanup(tmp_path):
    p=tmp_path/'ascii.stl'
    p.write_text('solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid x\n')
    with open_stl(p,ResourceBudget(scratch_dir=str(tmp_path))) as m:
        scratch=m._scratch
        assert scratch.exists() and m.asset.source_format=='ascii_stl'
        assert inspect_mesh(m)['boundary_edges']==3
    assert not scratch.exists()
    p.write_text(p.read_text().replace('endloop','endbad'))
    with pytest.raises(VoxelMillError):open_stl(p,ResourceBudget(scratch_dir=str(tmp_path)))
    assert not list(tmp_path.glob('voxelmill-stl-*'))


def _one_triangle():
    return np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0]]], dtype=np.float32)


def _two_triangles():
    return np.array(
        [[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
         [[0, 0, 0], [0, 1, 0], [0, 0, 1]]],
        dtype=np.float32,
    )


def test_stl_count_mismatch_uses_size_implied(tmp_path):
    p = tmp_path / 'mismatch.stl'
    write_stl(p, _two_triangles())
    body = p.read_bytes()
    p.write_bytes(body[:80] + struct.pack('<I', 99) + body[84:])
    with open_stl(p) as mesh:
        assert mesh.asset.triangle_count == 2
        assert len(mesh.diagnostics) == 1
        assert mesh.diagnostics[0].code == 'stl_count_mismatch'
        assert mesh.diagnostics[0].severity == 'warning'
        np.testing.assert_array_equal(mesh.triangles, _two_triangles())
        assert inspect_mesh(mesh)['diagnostics'][0]['code'] == 'stl_count_mismatch'


def test_stl_trailing_garbage_ignored(tmp_path):
    p = tmp_path / 'trailing.stl'
    write_stl(p, _one_triangle())
    p.write_bytes(p.read_bytes() + b'junk\x00\xff')
    with open_stl(p) as mesh:
        assert mesh.asset.triangle_count == 1
        assert mesh.diagnostics[0].code == 'stl_trailing_garbage'
        assert mesh.diagnostics[0].details['trailing_bytes'] == 6
        np.testing.assert_array_equal(mesh.triangles, _one_triangle())


def test_stl_truncated_reads_complete_triangles(tmp_path):
    p = tmp_path / 'truncated.stl'
    write_stl(p, _two_triangles())
    # Drop the last byte of the second triangle so only one complete record remains.
    p.write_bytes(p.read_bytes()[:-1])
    with open_stl(p) as mesh:
        assert mesh.asset.triangle_count == 1
        assert mesh.diagnostics[0].code == 'stl_truncated'
        assert mesh.diagnostics[0].details['used_triangles'] == 1
        np.testing.assert_array_equal(mesh.triangles, _one_triangle())


def test_stl_truncated_to_zero_still_fails(tmp_path):
    p = tmp_path / 'empty-trunc.stl'
    write_stl(p, _one_triangle())
    p.write_bytes(p.read_bytes()[:100])  # header + partial first triangle
    with pytest.raises(VoxelMillError) as raised:
        open_stl(p)
    assert raised.value.code == 'empty_stl'


def test_solid_binary_header(tmp_path):
    p=tmp_path/'binary.stl';write_stl(p,tetra());b=p.read_bytes();p.write_bytes(b'solid binary'.ljust(80,b' ')+b[80:])
    with open_stl(p) as m:assert m.asset.source_format=='binary_stl'


def test_nonmanifold_invalid_and_components(tmp_path):
    p=tmp_path/'test.stl'
    tri=np.concatenate([tetra(),tetra()+3,tetra()[0:1]])
    write_stl(p,tri)
    with open_stl(p) as m:r=inspect_mesh(m)
    assert r['connected_components']==2
    assert r['nonmanifold_edges']==3
    assert r['self_intersections']==1
    from voxelmill import _native
    bad=np.concatenate([tetra(),np.zeros((1,3,3),dtype=np.float32)])
    bad[0,0,0]=np.nan
    r=_native.inspect_mesh(bad)
    assert r['invalid_triangles']==2
    with pytest.raises(VoxelMillError):weld_mesh(bad)


def test_intersection_shared_vertex_and_edge():
    from voxelmill import _native
    a=np.array([[0,0,0],[2,0,0],[0,2,0]],dtype=np.float32)
    cases=[
      (np.array([[0,0,0],[-1,0,0],[0,-1,0]]),0),
      (np.array([[0,0,0],[1,0,0],[0,1,0]]),1),
      (np.array([[0,0,0],[2,0,0],[0,-1,0]]),0),
      (np.array([[0,0,0],[2,0,0],[1,1,0]]),1),
      (np.array([[0,0,0],[2,0,0],[0,0,1]]),0),
      (np.array([[.5,.5,-1],[.5,.5,1],[1,1,1]]),1),
    ]
    for b,expected in cases:
        for dtype in (np.float32, np.float64):
            assert _native.inspect_intersections(np.array([a,b],dtype=dtype))['self_intersections']==expected


def test_weld_float64_preserved():
    tri=tetra().astype(np.float64)+0.1234567890123
    vertices,faces=weld_mesh(tri)
    np.testing.assert_array_equal(vertices[faces],tri)


def _near_duplicate_pair(gap_mm):
    """Two triangles that should share an edge except for a ``gap_mm`` mismatch."""
    a = [0.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    c = [0.0, 1.0, 0.0]
    b2 = [1.0 + gap_mm, 0.0, 0.0]
    d = [1.0, 1.0, 0.0]
    return np.array([[a, b, c], [b2, d, c]], dtype=np.float64)


def test_weld_identical_vertices_exact_and_tolerant():
    tri = tetra().astype(np.float64)
    v0, f0 = weld_mesh(tri, weld_tolerance_mm=0.0)
    v1, f1 = weld_mesh(tri, weld_tolerance_mm=1e-5)
    assert len(v0) == len(v1) == 4
    np.testing.assert_array_equal(v0[f0], tri)
    np.testing.assert_array_equal(v1[f1], tri)


def test_weld_near_duplicates_need_tolerance(tmp_path):
    tris = _near_duplicate_pair(1e-6)
    v_exact, _ = weld_mesh(tris, weld_tolerance_mm=0.0)
    assert len(v_exact) == 5
    p = tmp_path / 'near.stl'
    write_stl(p, tris.astype(np.float32))
    with open_stl(p) as mesh:
        inventory = inspect_mesh(mesh, self_intersections=False)
    assert inventory['unique_vertices'] == 5
    assert inventory['boundary_edges'] > 0

    v_tol, f_tol = weld_mesh(tris, weld_tolerance_mm=1e-5)
    assert len(v_tol) == 4
    assert len({tuple(idx) for idx in f_tol}) == 2


def test_weld_cap_does_not_merge_large_gap():
    tris = _near_duplicate_pair(0.1)
    vertices, _ = weld_mesh(tris, weld_tolerance_mm=0.05)
    assert len(vertices) == 5


def test_weld_tolerance_rejected_above_cap_and_invalid():
    tris = tetra().astype(np.float64)
    with pytest.raises(VoxelMillError, match='0.05') as over:
        weld_mesh(tris, weld_tolerance_mm=0.06)
    assert over.value.code == 'invalid_mesh'
    with pytest.raises(VoxelMillError) as negative:
        weld_mesh(tris, weld_tolerance_mm=-1e-9)
    assert negative.value.code == 'invalid_mesh'
    with pytest.raises(VoxelMillError) as nonfinite:
        weld_mesh(tris, weld_tolerance_mm=float('nan'))
    assert nonfinite.value.code == 'invalid_mesh'


def test_inspect_cube_stays_exact():
    cube = Path(__file__).resolve().parents[1] / 'fixtures' / 'shapes' / 'cube.stl'
    with open_stl(cube) as mesh:
        inventory = inspect_mesh(mesh, self_intersections=False)
    assert inventory['triangle_count'] == 12
    assert inventory['unique_vertices'] == 8
    assert inventory['boundary_edges'] == 0
    assert inventory['topology_connectivity'].startswith('shared exact-coordinate')


def test_cancel_and_budget(tmp_path):
    p=tmp_path/'x.stl';write_stl(p,tetra());token=CancellationToken();token.cancel()
    with pytest.raises(Canceled):open_stl(p,cancel=token)
    with open_stl(p) as mesh:
        def cancel(stage,done,total):token.cancel()
        with pytest.raises(Canceled):inspect_mesh(mesh,cancel=token,progress=cancel)


def test_invalid_export_preserves_destination(tmp_path):
    p=tmp_path/'existing.stl';p.write_bytes(b'original')
    bad=tetra().astype(np.float64);bad[0,0,0]=1e100
    with pytest.raises(VoxelMillError):write_stl(p,bad)
    assert p.read_bytes()==b'original'
