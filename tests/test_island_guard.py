"""The correction loop the editor never ran, and the crop that makes it quick."""
from types import SimpleNamespace

import manifold3d as m
import numpy as np
import pytest

from voxelmill import geometry
from voxelmill.assembly import UnionLayerStream, assemble, prepare_model
from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.island_guard import local_window, route_without_islands, scan_assembly_islands
from voxelmill.raster import MeshLayerStream


def settings(**overrides):
    base = {'process': {'layer_height_mm': .2},
            'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [.1, .1], 'build_mm': [100., 80., 165.]}}
    for section, values in overrides.items():
        base.setdefault(section, {}).update(values)
    return resolve_settings(overrides=base)


def triangles(solid):
    return geometry.manifold_triangles(solid).astype(np.float32)


def block(size, position):
    return m.Manifold.cube(size).translate(position)


def floating_disc_model(s):
    """A grounded plinth with a separate slab floating above it."""
    solid = block((6, 6, 2), (-3, -3, 0)) + block((6, 6, 1), (-3, -3, 5))
    return prepare_model(triangles(solid), s)


def scattered_islands_model(s, count=4, pitch=12.0):
    """Several floating cubes far enough apart to need one pillar each."""
    solid = block((4, 4, 1), (-2, -2, 0))
    for index in range(count):
        solid = solid + block((2, 2, 2), (index * pitch, 0, 6))
    return prepare_model(triangles(solid), s)


def pillar_replan(limit=None):
    """A stand-in router: one plate-to-contact pillar per requested contact.

    ``limit`` is a callable given the call number, so a test can starve the
    router and watch the loop report what it could not fix.
    """
    calls = {'n': 0}

    def replan(extra):
        calls['n'] += 1
        points = list(extra)
        if limit is not None:
            points = points[:limit(calls['n'])]
        solids = [geometry.cylinder_between((x, y, 0.0), (x, y, float(z)), .6)
                  for x, y, z in points]
        return SimpleNamespace(solids=solids, metrics={}), None

    replan.calls = calls
    return replan


def test_layer_range_yields_the_requested_absolute_indices():
    s = settings()
    solid = block((4, 4, 6), (-2, -2, 0))
    model = prepare_model(triangles(solid), s)
    union = assemble(model, [], None)
    mesh = MeshLayerStream(model.triangles, model.bounds, s, layer_range=(3, 7))
    assert [layer.index for layer in mesh] == [3, 4, 5, 6, 7]
    grouped = UnionLayerStream(union.groups, union.bounds, s, layer_range=(3, 7))
    yielded = list(grouped)
    assert [layer.index for layer in yielded] == [3, 4, 5, 6, 7]
    # The z of a layer is a property of the build, not of the window.
    assert yielded[0].z_mm == pytest.approx(3.5 * s['process']['layer_height_mm'])
    # A range wider than the build clamps rather than inventing empty layers.
    whole = MeshLayerStream(model.triangles, model.bounds, s, layer_range=(-5, 10_000))
    assert [layer.index for layer in whole] == list(range(30))
    with pytest.raises(VoxelMillError, match='precedes'):
        MeshLayerStream(model.triangles, model.bounds, s, layer_range=(9, 2))


def test_a_floating_slab_gains_a_contact_and_the_last_word_is_a_full_scan():
    s = settings()
    model = floating_disc_model(s)
    result = route_without_islands(model, s, replan=pillar_replan(), max_passes=4)
    assert result['resolved'] and result['islands_remaining'] == 0
    assert len(result['contacts']) == 1
    kinds = [record['scan'] for record in result['passes']]
    # Full first, cropped in the middle, full again to confirm.
    assert kinds[0] == 'full' and 'local' in kinds and kinds[-1] == 'full'
    assert result['passes'][0]['islands'] == 1
    assert result['passes'][0]['contacts_added'] == 1
    assert result['passes'][-1]['stopped'] == 'confirmation'
    assert result['passes'][-1]['confirms_pass'] == result['passes'][-2]['pass']


def test_max_passes_defaults_to_the_configured_limit():
    s = settings(support={'max_island_passes': 3})
    model = prepare_model(triangles(block((4, 4, 4), (-2, -2, 0))), s)
    result = route_without_islands(model, s, replan=pillar_replan())
    assert result['max_passes'] == 3
    # A part that was never in trouble is settled by the first full scan.
    assert result['resolved'] and len(result['passes']) == 1


def test_an_unfixable_plate_stops_at_max_passes_and_reports_what_is_left():
    s = settings()
    model = scattered_islands_model(s)
    # Each call routes one more contact than the last, so every pass makes
    # progress and the budget still runs out before the plate is clean.
    result = route_without_islands(model, s, replan=pillar_replan(limit=lambda n: n - 1),
                                   max_passes=4)
    assert not result['resolved']
    assert result['islands_remaining'] >= 1
    assert len(result['passes']) == 4
    assert result['passes'][-1]['scan'] == 'full'
    assert result['passes'][-1]['stopped'] == 'max_passes'
    assert len(result['island_positions']) == result['islands_remaining']


def test_a_pass_that_does_not_reduce_islands_stops_instead_of_spinning():
    s = settings()
    model = floating_disc_model(s)
    # A router that ignores the corrections can never improve, so the loop must
    # notice rather than spend its whole budget proving it again.
    result = route_without_islands(model, s, replan=pillar_replan(limit=lambda n: 0),
                                   max_passes=5)
    assert not result['resolved'] and result['islands_remaining'] == 1
    assert len(result['passes']) < 5
    assert result['passes'][-1]['stopped'] == 'no_progress'
    assert result['passes'][-1]['scan'] == 'full'


def test_a_component_cut_by_the_crop_is_not_called_an_island():
    """The trap that makes naive cropping wrong, stated as a test.

    A slab that runs out of the crop is truncated at the crop edge and looks
    like floating material. A local pass must not report it; only the full scan
    can see that it continues.
    """
    s = settings()
    model = prepare_model(triangles(block((40, 4, 1), (-20, -2, 5))), s)
    union = assemble(model, [], None)
    full = scan_assembly_islands(union, s)
    assert full['scan'] == 'full' and full['islands'] == 1
    cropped = scan_assembly_islands(
        union, s, crop_bounds=np.array([[-2., -2., 0.], [2., 2., 6.]]))
    assert cropped['scan'] == 'local'
    assert cropped['islands'] == 0 and cropped['positions'] == []
    assert cropped['discarded_on_crop_edge'] >= 1
    assert cropped['grid'][0] < full['grid'][0]


def test_the_local_window_pads_the_new_pillars_and_stops_above_them():
    s = settings()
    window, layers = local_window([(0.0, 0.0, 4.0)], s, layer_count=100)
    pad = s['support']['spacing_mm'] * 4 + s['support']['pillar_diameter_mm']
    assert window[0][0] == pytest.approx(-pad) and window[1][0] == pytest.approx(pad)
    # From the plate, because a pillar is only as supported as what is under
    # it, to two layers above the highest new contact.
    assert layers == (0, int(4.0 / s['process']['layer_height_mm']) + 2)
    assert local_window([], s, layer_count=100) == (None, None)
