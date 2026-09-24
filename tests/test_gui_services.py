"""The editor's background services, called directly without Qt."""
import numpy as np
import manifold3d as m

from voxelmill.config import resolve_settings
from voxelmill.contracts import CancellationToken, no_progress
from voxelmill.geometry import manifold_triangles
from voxelmill.gui import services
from voxelmill.gui.document import Document
from voxelmill.mesh import write_stl


def _document(tmp_path, solid):
    path = tmp_path / 'part.stl'
    write_stl(path, manifold_triangles(solid))
    settings = resolve_settings(overrides={
        'printer': {'build_mm': [60., 60., 60.], 'pixels': [300, 300], 'pixel_pitch_mm': [.2, .2]},
        'process': {'layer_height_mm': 0.2},
        'resources': {'workers': 1, 'scratch_dir': str(tmp_path)}})
    document = Document(settings)
    document.source = path
    return document


def test_load_and_place_writes_placed_triangles_into_its_own_scratch(tmp_path):
    document = _document(tmp_path, m.Manifold.cube((10, 8, 6)))
    result = services.load_and_place(document, CancellationToken(), no_progress)
    placed = np.asarray(result['placed'])
    assert placed.shape == (result['asset'].triangle_count, 3, 3)
    assert result['fits'] is True
    # Placement lifts the part: nothing sits below the configured lift.
    assert float(placed[..., 2].min()) >= document.model_lift_mm - 1e-6
    assert str(result['scratch']).startswith(str(tmp_path))


def test_route_attachments_names_each_pass_and_returns_the_exact_union(tmp_path):
    document = _document(tmp_path, m.Manifold.cube((12, 12, 3), True).translate((0, 0, 6)))
    token = CancellationToken()
    placed = services.load_and_place(document, token, no_progress)
    built = services.build_model(document, placed['placed'], placed['placement'], token, no_progress)
    document.derived.solid = built['solid']
    labels = []
    result = services.route_attachments(document, built['triangles'], built['bounds'], None, token,
                                        lambda label, done, total: labels.append(label))
    guard = result['guard']
    assert guard['passes'] and result['plan'] is not None
    assert result['union'] is guard['union'] and result['union'].solid is not None
    assert any(label.startswith('pass 1/') and 'routing' in label for label in labels)
    assert any('assembling' in label for label in labels)
    assert len(result['contacts']) > 0
    assert all(len(contact) == 3 for contact in result['contacts'])
