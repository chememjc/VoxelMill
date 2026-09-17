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


def test_ascii_shape_reads_as_ascii():
    entry = next(item for item in SHAPES if item['path'].endswith('cube_ascii.stl'))
    cube = next(item for item in SHAPES if item['path'].endswith('shapes/cube.stl'))
    with open_stl(ROOT / entry['path']) as mesh:
        assert mesh.asset.source_format == 'ascii_stl'
        assert mesh.asset.triangle_count == cube['triangle_count']
