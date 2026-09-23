"""Opt-in evidence over every immutable full-resolution original.

Run VOXELMILL_SAMPLES=1 .venv/bin/python -m pytest -m samples -q.
This does not invent printable repairs for the original invalid solids.
"""
import json
import os
from pathlib import Path
import numpy as np
import pytest
from voxelmill import _native
from voxelmill.mesh import open_stl
from voxelmill.geometry import auto_placement, envelope_fits
from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.ops import cap_open_cuts

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / 'fixtures/manifest.json').read_text())
# The manifest is the acceptance boundary for this opt-in suite.  Additional
# inputstl files are handled separately until their full-resolution evidence is
# recorded there; they must not be tested with fabricated hashes or pose claims.
ORIGINALS = tuple(ROOT / item['path'] for item in MANIFEST['meshes'])
pytestmark = [pytest.mark.samples, pytest.mark.skipif(os.getenv('VOXELMILL_SAMPLES') != '1', reason='opt-in original fixture suite')]


@pytest.mark.parametrize('path', ORIGINALS, ids=lambda path: path.stem)
def test_full_original_hash_placement_and_native_scanlines(path):
    expected = next(item for item in MANIFEST['meshes'] if Path(item['path']).name == path.name)
    settings = resolve_settings()
    with open_stl(path) as mesh:
        assert mesh.asset.sha256 == expected['sha256']
        assert mesh.asset.triangle_count == expected['triangle_count']
        try:
            placement = auto_placement(mesh.triangles, settings)
        except VoxelMillError as error:
            assert error.code == 'no_feasible_placement'
            assert path.name.startswith('skull_slab')
        else:
            assert path.name.startswith(('left_temporal', 'right_temporal'))
            assert envelope_fits(placement.bounds, settings)
            assert placement.search['full_resolution_bounds']
        # Full original triangles and native XY pitch, a source-space crop.
        bounds = np.asarray(mesh.asset.bounds)
        dx, dy = settings['printer']['pixel_pitch_mm']
        nx, ny = np.ceil((bounds[1,:2] - bounds[0,:2]) / [dx,dy]).astype(int) + 2
        native = _native.Rasterizer(mesh.triangles)
        for fraction in (.25, .5, .75):
            z = bounds[0,2] + fraction * (bounds[1,2]-bounds[0,2])
            result = native.slice(float(z), int(nx), int(ny), float(bounds[0,0]-dx),
                                  float(bounds[0,1]-dy), dx, dy, None, 'nonzero')
            assert result['mask'].shape == (ny, nx)
            assert result['mask'].dtype == np.uint8


@pytest.mark.parametrize('filename', ['skull_slab_q00_mars5_oriented.stl'])
def test_real_skull_cap_refuses_non_loop_boundary_honestly(filename):
    """The real cut surface must report cap_incomplete, never fake a cap."""
    path = ROOT / 'inputstl' / filename
    settings = resolve_settings()
    with open_stl(path) as mesh:
        with pytest.raises(VoxelMillError) as error:
            cap_open_cuts(mesh.triangles, settings)
    assert error.value.code == 'cap_incomplete'
    assert error.value.details['boundary_edges'] > 0
    assert error.value.details['boundary_loops'] == 0
    assert error.value.details['leftover_boundary_edges'] > 0
