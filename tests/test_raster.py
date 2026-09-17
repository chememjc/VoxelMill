import numpy as np
import pytest
import manifold3d as m
from voxelmill import _native
from voxelmill.geometry import manifold_triangles
from voxelmill.mesh import write_stl,open_stl
from voxelmill.raster import RasterGrid,MeshLayerStream
from voxelmill.config import resolve_settings
from voxelmill.contracts import CancellationToken,Canceled,Layer,VoxelMillError,ResourceBudget
from voxelmill.validation import analyze_layers


def test_native_cube_and_hole_parity():
    solid=m.Manifold.cube((4,4,3))-m.Manifold.cube((2,2,5)).translate((1,1,-1))
    raster=_native.Rasterizer(manifold_triangles(solid))
    result=raster.slice(1.,6,6,-1.,-1.,1.,1.)
    expected=np.zeros((6,6),dtype=np.uint8);expected[1:5,1:5]=1;expected[2:4,2:4]=0
    np.testing.assert_array_equal(result['mask'],expected)
    assert result['odd_rows']==0
    with pytest.raises(ValueError):raster.slice(.5,6,6,-1.,-1.,1.,1.)


def test_native_mmap_and_half_open_vertices(tmp_path):
    p=tmp_path/'cube.stl';write_stl(p,manifold_triangles(m.Manifold.cube((2,3,4))))
    with open_stl(p) as mesh:
        raster=_native.Rasterizer(mesh.triangles)
        assert raster.slice(0.,2,3,0.,0.,1.,1.)['mask'].sum()==6
        assert raster.slice(4.,2,3,0.,0.,1.,1.)['mask'].sum()==0


def test_asymmetric_native_grid():
    raster=_native.Rasterizer(manifold_triangles(m.Manifold.cube((1,2,1)).translate((2,-3,0))))
    result=raster.slice(.5,8,8,-4.,-4.,1.,1.)['mask']
    expected=np.zeros((8,8),dtype=np.uint8);expected[1:3,6]=1
    np.testing.assert_array_equal(result,expected)


def test_single_pixel_island_and_diagonal_only_are_not_ignored():
    settings=resolve_settings();g=RasterGrid(6,6,0,0,1,1)
    a=np.zeros((6,6),np.uint8);a[1,1]=1
    b=a.copy();b[2,2]=1
    r=analyze_layers([Layer(0,.025,a),Layer(1,.075,b)],g,settings)
    assert r.metrics['island_components']==1
    assert r.checks['raster_connectivity']=='fail'
    assert any(d.details.get('pixels')==1 for d in r.diagnostics)
    assert not r.passed


def test_plate_supported_cube_passes_every_layer_check():
    settings=resolve_settings();g=RasterGrid(6,6,0,0,1,1)
    a=np.zeros((6,6),np.uint8);a[1:4,1:4]=1
    r=analyze_layers([Layer(i,.025+i*.05,a) for i in range(4)],g,settings)
    assert r.checks['raster_connectivity']=='pass'
    assert r.metrics['enclosed_voids']['count']==0
    assert r.metrics['transient_traps']['peak_volume_mm3']==0
    assert r.passed


def test_not_run_check_never_reports_as_passed():
    r=analyze_layers([Layer(0,.025,np.ones((6,6),np.uint8))],RasterGrid(6,6,0,0,1,1),resolve_settings())
    assert r.passed
    r.checks['drainage_bottlenecks']='not_run'
    assert not r.passed


def test_enclosed_void_across_multiple_layers_and_future_opening():
    settings=resolve_settings();g=RasterGrid(7,7,0,0,1,1)
    cap=np.zeros((7,7),np.uint8);cap[1:6,1:6]=1
    ring=cap.copy();ring[2:5,2:5]=0
    closed=analyze_layers([Layer(i,float(i),x) for i,x in enumerate([cap,ring,ring,cap])],g,settings)
    assert closed.metrics['enclosed_voids']['count']==1
    assert closed.metrics['enclosed_voids']['volume_mm3']==pytest.approx(.9)
    # A future opening is not present drainage: the pocket is still reported as
    # a live trap for the layers during which it is enclosed.
    open_top=analyze_layers([Layer(i,float(i),x) for i,x in enumerate([cap,ring,ring,ring])],g,settings)
    assert open_top.metrics['enclosed_voids']['count']==0
    assert open_top.checks['transient_traps']=='warn'
    # Three ring layers of nine pixels at the 0.05 mm layer height.
    assert open_top.metrics['transient_traps']['peak_volume_mm3']==pytest.approx(1.35)
    assert open_top.passed


def test_raster_cancel_and_memory():
    settings=resolve_settings();tri=manifold_triangles(m.Manifold.cube((100,60,1)))
    c=CancellationToken();c.cancel()
    with pytest.raises(Canceled):_native.Rasterizer(tri,c.check)
    with pytest.raises(VoxelMillError,match='memory budget'):
        MeshLayerStream(tri,[[0,0,0],[100,60,1]],settings,budget=ResourceBudget(.25))


def test_native_sweep_reset_matches_fresh_raster():
    triangles = manifold_triangles(m.Manifold.sphere(3, 32).translate((0, 0, 4)))
    raster = _native.Rasterizer(triangles)
    for z in [6.5, 2.5, 5.5, 1.5, 7.5, 0.5]:
        raster.reset()
        actual = raster.slice(z, 10, 10, -5., -5., 1., 1.)
        expected = _native.Rasterizer(triangles).slice(z, 10, 10, -5., -5., 1., 1.)
        np.testing.assert_array_equal(actual['mask'], expected['mask'])
        assert actual['odd_rows'] == expected['odd_rows']
