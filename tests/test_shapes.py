"""The committed generic shapes must stay exactly what fixtures/shapes/manifest.json says.

Regenerate them with .venv/bin/python scripts/make_test_shapes.py.
"""
import hashlib
import json
from pathlib import Path
import pytest
from voxelmill.mesh import inspect_mesh, open_stl

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / 'fixtures/shapes/manifest.json').read_text())
SHAPES = MANIFEST['shapes']
COUNTERS = ('triangle_count', 'unique_vertices', 'connected_components', 'boundary_edges',
            'nonmanifold_edges', 'inconsistent_winding_edges', 'degenerate_triangles',
            'invalid_triangles', 'self_intersections')
VALID = [entry for entry in SHAPES if '/invalid/' not in entry['path']]


@pytest.mark.parametrize('entry', SHAPES, ids=lambda entry: Path(entry['path']).stem)
def test_shape_matches_manifest(entry):
    path = ROOT / entry['path']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256']
    result = inspect_mesh(path)
    for key in COUNTERS:
        assert result[key] == entry[key], key
    assert result['self_intersections_status'] == entry['self_intersections_status']
    assert result['signed_volume_mm3'] == pytest.approx(entry['signed_volume_mm3'], abs=1e-6)
    flat = [value for corner in result['bounds'] for value in corner]
    assert flat == pytest.approx([value for corner in entry['bounds'] for value in corner], abs=1e-6)
    assert entry['note']


@pytest.mark.parametrize('entry', VALID, ids=lambda entry: Path(entry['path']).stem)
def test_valid_shapes_are_closed_solids(entry):
    result = inspect_mesh(ROOT / entry['path'])
    assert result['boundary_edges'] == result['nonmanifold_edges'] == 0
    assert result['inconsistent_winding_edges'] == result['invalid_triangles'] == 0
    assert result['self_intersections'] == 0 and result['signed_volume_mm3'] > 0
    assert result['bounds'][0][2] == 0.  # every shape rests on the build plate


def test_invalid_shapes_report_their_defect():
    defects = {Path(entry['path']).stem: entry for entry in SHAPES if '/invalid/' in entry['path']}
    assert defects['open_box']['boundary_edges'] > 0
    assert defects['flipped_winding']['inconsistent_winding_edges'] > 0
    assert defects['nonmanifold_edge']['nonmanifold_edges'] > 0
    assert defects['degenerate']['degenerate_triangles'] > 0
    assert defects['self_intersecting']['self_intersections'] > 0


INVALID = [entry for entry in SHAPES if '/invalid/' in entry['path']]
# README defect each invalid fixture must surface (inspect counter and/or error).
INVALID_DEFECT = {
    'open_box': 'boundary_edges',
    'flipped_winding': 'inconsistent_winding_edges',
    'nonmanifold_edge': 'nonmanifold_edges',
    'degenerate': 'degenerate_triangles',
    'self_intersecting': 'self_intersections',
}


@pytest.mark.parametrize('entry', INVALID, ids=lambda entry: Path(entry['path']).stem)
def test_invalid_fixtures_fail_inspect_and_solid_conversion(entry):
    """Each invalid STL names its defect on inspect and refuses solid conversion."""
    from voxelmill.contracts import VoxelMillError
    from voxelmill.geometry import mesh_to_manifold
    from voxelmill.mesh import open_stl

    path = ROOT / entry['path']
    stem = Path(entry['path']).stem
    defect = INVALID_DEFECT[stem]
    result = inspect_mesh(path)
    assert result[defect] > 0, f'{stem} must report {defect}'

    with open_stl(path) as mesh:
        triangles = mesh.triangles.copy()
    with pytest.raises(VoxelMillError) as error:
        mesh_to_manifold(triangles)
    named = {defect, 'degenerate_triangles', 'invalid_triangles', 'invalid_solid',
             'self_intersections', 'boundary_edges', 'nonmanifold_edges',
             'inconsistent_winding_edges'}
    blob = f'{error.value.code} {error.value}'.lower()
    assert error.value.code in named or any(key in blob for key in named), (
        f'{stem}: conversion error must name a mesh defect, got {error.value.code!r}')


def test_ascii_shape_reads_as_ascii():
    entry = next(item for item in SHAPES if item['path'].endswith('cube_ascii.stl'))
    cube = next(item for item in SHAPES if item['path'].endswith('shapes/cube.stl'))
    with open_stl(ROOT / entry['path']) as mesh:
        assert mesh.asset.source_format == 'ascii_stl'
        assert mesh.asset.triangle_count == cube['triangle_count']
