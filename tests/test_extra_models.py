"""Multiple models share a support field; only solid intersections are collisions."""
import pytest
import manifold3d as m

from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.geometry import manifold_triangles
from voxelmill.mesh import write_stl
from voxelmill.pipeline import prepare
from voxelmill.cli import _extra_models


def cube_stl(path, size=8):
    write_stl(path, manifold_triangles(m.Manifold.cube((size, size, size), True).translate((0, 0, size / 2))))


def test_offset_models_share_supports_without_a_collision(tmp_path):
    first, second = tmp_path / 'a.stl', tmp_path / 'b.stl'
    cube_stl(first)
    cube_stl(second)
    settings = resolve_settings(overrides={
        'printer': {'build_mm': [80., 80., 80.], 'pixels': [400, 400], 'pixel_pitch_mm': [.2, .2]},
        'support': {'automatic': False, 'auto_bracing': False, 'base_type': 'none'},
        'resources': {'workers': 1}})
    report = prepare(first, settings, output=tmp_path / 'out.stl', allow_unresolved=True, max_passes=1,
                     extra_models=[{'path': str(second), 'center_offset': (25.0, 0.0), 'lift_mm': 5.0}])
    extra = report['stages']['extra_models']
    assert extra['collision'] == 'model_solids_only'
    assert extra['parts'][0]['triangles'] > 0
    assert report['stages']['model']['triangles'] > extra['parts'][0]['triangles']


def test_overlapping_models_are_refused(tmp_path):
    first, second = tmp_path / 'a.stl', tmp_path / 'b.stl'
    cube_stl(first)
    cube_stl(second)
    settings = resolve_settings(overrides={
        'printer': {'build_mm': [80., 80., 80.], 'pixels': [400, 400], 'pixel_pitch_mm': [.2, .2]},
        'support': {'base_type': 'none'},
        'resources': {'workers': 1}})
    with pytest.raises(VoxelMillError, match='collides with another model solid'):
        prepare(first, settings, output=tmp_path / 'out.stl', allow_unresolved=True, max_passes=1,
                extra_models=[{'path': str(second), 'center_offset': (0.0, 0.0), 'lift_mm': 5.0}])


def test_cli_added_model_spec_exposes_every_gui_pose_field(tmp_path):
    spec = tmp_path / 'models.json'
    spec.write_text('[{"path":"part.stl","rotate":[1,2,3],'
                    '"center_offset":[4,5],"lift_mm":6,"scale":[1,2,1],'
                    '"mirror":[false,true,false]}]')
    assert _extra_models([], [spec]) == [{
        'path': 'part.stl', 'rotate': [1., 2., 3.], 'center_offset': [4., 5.],
        'lift_mm': 6., 'scale': [1., 2., 1.], 'mirror': [False, True, False],
        'overrides': {}, 'name': 'part.stl'}]


def test_added_model_support_overrides_are_scoped(tmp_path):
    spec = tmp_path / 'models.json'
    spec.write_text('[{"path":"part.stl","overrides":{"support":{"pillar_diameter_mm":1.6,'
                    '"spacing_mm":2.0}}}]')
    extra = _extra_models([], [spec])
    assert extra[0]['overrides']['support']['pillar_diameter_mm'] == 1.6
    from voxelmill.pipeline import normalize_extra_model
    from voxelmill.contracts import VoxelMillError
    with pytest.raises(VoxelMillError, match='support table'):
        normalize_extra_model({'path': 'part.stl', 'overrides': {'process': {'layer_height_mm': 0.1}}})
