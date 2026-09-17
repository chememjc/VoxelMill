"""Grouped STL export remains ordered, atomic, and cancellable."""

import numpy as np
import pytest

from voxelmill.contracts import Canceled, CancellationToken, VoxelMillError
from voxelmill.mesh import open_stl, write_stl


def triangle(offset):
    return np.asarray([[[offset, 0., 0.], [offset + 1., 0., 0.],
                        [offset, 1., 0.]]], dtype=np.float32)


def test_grouped_export_reopens_with_count_and_order(tmp_path):
    target = tmp_path / 'grouped.stl'
    groups = [np.concatenate((triangle(0), triangle(1))),
              np.empty((0, 3, 3), dtype=np.float32), triangle(10)]
    write_stl(target, groups)
    with open_stl(target) as opened:
        assert opened.asset.triangle_count == 3
        np.testing.assert_array_equal(opened.triangles, np.concatenate(groups))


def test_invalid_later_group_preserves_existing_destination(tmp_path):
    target = tmp_path / 'existing.stl'
    target.write_bytes(b'keep this output')
    invalid = np.zeros((1, 3, 2), dtype=np.float32)
    with pytest.raises(VoxelMillError, match='shape'):
        write_stl(target, [triangle(0), invalid])
    assert target.read_bytes() == b'keep this output'


def test_grouped_export_progress_is_cumulative(tmp_path):
    target = tmp_path / 'progress.stl'
    seen = []
    write_stl(target, [triangle(0), triangle(10)],
              progress=lambda stage, done, total: seen.append((stage, done, total)))
    assert seen == [('write_stl', 1, 2), ('write_stl', 2, 2)]


def test_grouped_export_cancellation_preserves_destination(tmp_path):
    target = tmp_path / 'canceled.stl'
    target.write_bytes(b'keep this output')
    token = CancellationToken()

    def cancel_after_first(stage, done, total):
        token.cancel()

    with pytest.raises(Canceled):
        write_stl(target, [triangle(0), triangle(10)], token, cancel_after_first)
    assert target.read_bytes() == b'keep this output'
