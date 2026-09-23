"""Native parallel loops: present and bit-identical with or without oneTBB."""
import numpy as np
import pytest
import scipy.ndimage as ndi

from voxelmill import _native


def test_worker_limit_exists_in_every_build():
    assert isinstance(_native.HAS_TBB, bool)
    for bad in (0, 33):
        with pytest.raises(ValueError):
            _native.WorkerLimit(bad)
    limit = _native.WorkerLimit(2)
    del limit


@pytest.mark.parametrize('workers', [1, 4])
def test_edt_is_identical_for_any_worker_count(workers):
    empty = np.random.default_rng(3).random((24, 40, 36)) < 0.9
    sampling = (0.05, 0.018, 0.018)
    limit = _native.WorkerLimit(workers)
    try:
        got = _native.distance_transform_edt(np.ascontiguousarray(empty), sampling)
    finally:
        del limit
    assert np.array_equal(got, ndi.distance_transform_edt(empty, sampling=sampling))
