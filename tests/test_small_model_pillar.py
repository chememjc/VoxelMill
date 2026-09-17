"""Dimensions and topology of model-to-model support connectors."""
import math
import numpy as np
import pytest
import manifold3d as m

from voxelmill.contracts import VoxelMillError
from voxelmill.support_segments import small_model_pillar


def polygon_area(radius, segments=32):
    return segments * math.sin(2 * math.pi / segments) * radius ** 2 / 2


def test_conical_connector_has_surface_contacts_and_unequal_buried_depths():
    start, end, radius = np.array([0., 0., 2.]), np.array([0., 0., 7.]), 1.2
    solid = small_model_pillar(start, end, radius, 'cone', upper_depth=2., lower_depth=1.)
    area = polygon_area(radius)
    expected = area * 5 + area * (1. + 2.) / 3
    assert solid.status() == m.Error.NoError
    assert len(solid.decompose()) == 1
    assert solid.volume() == pytest.approx(expected, rel=1e-6)
    bounds = np.asarray(solid.bounding_box()).reshape(2, 3)
    np.testing.assert_allclose(bounds[:, 2], [1., 9.], atol=1e-8)


@pytest.mark.parametrize('shape', ['cone', 'cylinder'])
def test_zero_depths_make_a_flat_capped_surface_to_surface_connector(shape):
    solid = small_model_pillar([0, 0, 1], [0, 0, 4], .75, shape)
    area = polygon_area(.75)
    assert solid.volume() == pytest.approx(area * 3, rel=1e-6)
    bounds = np.asarray(solid.bounding_box()).reshape(2, 3)
    np.testing.assert_allclose(bounds[:, 2], [1, 4], atol=1e-8)


def test_connector_supports_arbitrary_positive_orientation():
    start = np.array([1., -2., 3.])
    direction = np.array([2., 3., 6.])
    end = start + direction
    solid = small_model_pillar(start, end, .5, 'cylinder', upper_depth=.4, lower_depth=.7)
    unit = direction / np.linalg.norm(direction)
    vertices = np.asarray(solid.to_mesh64().vert_properties)[:, :3]
    projection = vertices @ unit
    assert projection.min() == pytest.approx((start - unit * .7) @ unit, abs=1e-7)
    assert projection.max() == pytest.approx((end + unit * .4) @ unit, abs=1e-7)
    assert len(solid.decompose()) == 1


@pytest.mark.parametrize('args', [
    ([0, 0, 0], [0, 0, 0], 1, 'cone', 0, 0),
    ([0, 0, 0], [0, 0, 1], 0, 'cone', 0, 0),
    ([0, 0, 0], [0, 0, 1], 1, 'bad', 0, 0),
    ([0, 0, 0], [0, 0, 1], 1, 'cone', -1, 0),
])
def test_connector_rejects_invalid_geometry(args):
    with pytest.raises(VoxelMillError):
        small_model_pillar(*args)
