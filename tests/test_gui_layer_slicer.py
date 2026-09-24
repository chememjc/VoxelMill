
import numpy as np
import manifold3d as m
import pytest
pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules')
from voxelmill import _native
from voxelmill.assembly import assemble, prepare_model
from voxelmill.config import resolve_settings
from voxelmill.contracts import CancellationToken, Canceled, ResourceBudget
from voxelmill.geometry import manifold_triangles
from voxelmill.gui.services import LayerSlicer, slice_layer


@pytest.mark.parametrize('policy', ['none', 'conservative'])
def test_cached_indices_match_fresh_layers_in_both_directions(policy, monkeypatch):
    settings = resolve_settings(overrides={'repair': {'aggressiveness': policy},
        'printer': {'pixels': [200, 160], 'pixel_pitch_mm': [.5, .5], 'build_mm': [100, 80, 165]},
        'process': {'layer_height_mm': .2}})
    triangles = manifold_triangles(m.Manifold.sphere(3, 32).translate((0, 0, 4)))
    model = prepare_model(triangles, settings)
    union = assemble(model, [m.Manifold.cube((1, 1, 3))], None)
    indices = [0, 5, 20, 30, 10, 10, 0]
    expected = [slice_layer(union, settings, i, ResourceBudget(), CancellationToken()) for i in indices]
    real = _native.Rasterizer
    built = []
    def counted(*args):
        built.append(True)
        return real(*args)
    monkeypatch.setattr(_native, 'Rasterizer', counted)
    slicer = LayerSlicer(union, settings, ResourceBudget())
    for index, reference in zip(indices, expected):
        actual = slicer.slice(index, CancellationToken())
        np.testing.assert_array_equal(actual['mask'], reference['mask'])
        assert actual['open_rows'] == reference['open_rows']
    assert len(built) == (2 if policy == 'none' else 1)
    # Interrupt inside a native callback after initialization, then reuse it.
    class Interrupt(CancellationToken):
        calls = 0
        def check(self):
            self.calls += 1
            if self.calls >= 3:
                raise Canceled()
    with pytest.raises(Canceled):
        slicer.slice(20, Interrupt())
    np.testing.assert_array_equal(slicer.slice(5, CancellationToken())['mask'], expected[1]['mask'])
