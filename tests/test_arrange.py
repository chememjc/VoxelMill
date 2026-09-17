"""Plate footprint packing: no overlaps, no escapes, deterministic order."""
import math

import pytest

from voxelmill.arrange import arrange_footprints
from voxelmill.contracts import VoxelMillError


def assert_layout_is_legal(positions, footprints, envelope, clearance_mm=0.0, fixed=()):
    """Every placed footprint stays in bounds and clears every other rectangle and obstacle."""
    width, depth = envelope
    eps = 1e-6
    rects = [(x, y, w, d) for (x, y), (w, d) in zip(positions, footprints)]
    for x, y, w, d in rects:
        assert -width / 2 - eps <= x - w / 2 and x + w / 2 <= width / 2 + eps
        assert -depth / 2 - eps <= y - d / 2 and y + d / 2 <= depth / 2 + eps
    obstacles = list(fixed)
    for i, a in enumerate(rects):
        for b in rects[i + 1:] + obstacles:
            gap_x = abs(a[0] - b[0]) - (a[2] + b[2]) / 2
            gap_y = abs(a[1] - b[1]) - (a[3] + b[3]) / 2
            assert gap_x >= clearance_mm - eps or gap_y >= clearance_mm - eps


def test_a_single_footprint_centers_at_the_origin():
    positions = arrange_footprints([(10.0, 5.0)], (100.0, 100.0))
    assert positions == [(0.0, 0.0)]


def test_four_equal_squares_exactly_fill_a_2x2_grid():
    footprints = [(10.0, 10.0)] * 4
    envelope = (20.0, 20.0)
    positions = arrange_footprints(footprints, envelope)
    assert len(positions) == 4
    assert_layout_is_legal(positions, footprints, envelope)


def test_clearance_is_honoured_so_a_tight_layout_fails_at_a_large_clearance():
    footprints = [(10.0, 10.0)] * 4
    envelope = (20.0, 20.0)
    # Fits with no clearance between the four squares.
    positions = arrange_footprints(footprints, envelope, clearance_mm=0.0)
    assert_layout_is_legal(positions, footprints, envelope, clearance_mm=0.0)
    # The same squares cannot all keep a large gap inside the same envelope.
    with pytest.raises(VoxelMillError) as excinfo:
        arrange_footprints(footprints, envelope, clearance_mm=15.0)
    assert excinfo.value.code == 'arrange_no_fit'


def test_a_footprint_bigger_than_the_envelope_raises_arrange_no_fit():
    with pytest.raises(VoxelMillError) as excinfo:
        arrange_footprints([(50.0, 50.0)], (40.0, 40.0))
    assert excinfo.value.code == 'arrange_no_fit'
    assert excinfo.value.details['index'] == 0


def test_too_many_footprints_to_fit_raises_and_names_the_failing_index():
    footprints = [(10.0, 10.0)] * 5
    envelope = (20.0, 20.0)
    with pytest.raises(VoxelMillError) as excinfo:
        arrange_footprints(footprints, envelope)
    assert excinfo.value.code == 'arrange_no_fit'
    details = excinfo.value.details
    assert details['placed'] == 4
    assert details['index'] in range(5)
    assert details['width_mm'] == 10.0 and details['depth_mm'] == 10.0


def test_fixed_obstacles_are_avoided():
    fixed = [(0.0, 0.0, 20.0, 20.0)]
    footprints = [(10.0, 10.0)]
    envelope = (40.0, 40.0)
    positions = arrange_footprints(footprints, envelope, fixed=fixed)
    assert_layout_is_legal(positions, footprints, envelope, fixed=fixed)
    x, y = positions[0]
    # The fixed 20x20 block covers the whole center, so the footprint must
    # clear it by at least half of each rectangle's width or depth.
    assert abs(x) >= 15.0 - 1e-9 or abs(y) >= 15.0 - 1e-9


def test_output_order_matches_input_order_even_when_placement_order_differs():
    # Depth-sorted placement order is 2, 1, 0 - the return must still follow the input order.
    footprints = [(5.0, 5.0), (5.0, 10.0), (5.0, 20.0)]
    envelope = (30.0, 30.0)
    positions = arrange_footprints(footprints, envelope)
    assert_layout_is_legal(positions, footprints, envelope)
    sizes_by_position = {pos: size for pos, size in zip(positions, footprints)}
    assert len(sizes_by_position) == 3
    for (x, y), (w, d) in zip(positions, footprints):
        half_w, half_d = (envelope[0] - w) / 2, (envelope[1] - d) / 2
        assert -half_w - 1e-6 <= x <= half_w + 1e-6
        assert -half_d - 1e-6 <= y <= half_d + 1e-6


def test_calling_twice_returns_identical_results():
    footprints = [(8.0, 6.0), (6.0, 8.0), (4.0, 4.0), (12.0, 3.0)]
    envelope = (50.0, 50.0)
    first = arrange_footprints(footprints, envelope, clearance_mm=1.0)
    second = arrange_footprints(footprints, envelope, clearance_mm=1.0)
    assert first == second


@pytest.mark.parametrize('footprints', [
    [(float('nan'), 5.0)],
    [(0.0, 5.0)],
    [(-1.0, 5.0)],
    [(5.0,)],
    ['not-a-pair'],
])
def test_invalid_footprints_raise_invalid_arrange(footprints):
    with pytest.raises(VoxelMillError) as excinfo:
        arrange_footprints(footprints, (100.0, 100.0))
    assert excinfo.value.code == 'invalid_arrange'


@pytest.mark.parametrize('envelope', [
    (float('nan'), 10.0),
    (0.0, 10.0),
    (-5.0, 10.0),
    (10.0,),
])
def test_invalid_envelope_raises_invalid_arrange(envelope):
    with pytest.raises(VoxelMillError) as excinfo:
        arrange_footprints([(5.0, 5.0)], envelope)
    assert excinfo.value.code == 'invalid_arrange'


@pytest.mark.parametrize('clearance_mm', [float('nan'), float('inf'), -1.0])
def test_invalid_clearance_raises_invalid_arrange(clearance_mm):
    with pytest.raises(VoxelMillError) as excinfo:
        arrange_footprints([(5.0, 5.0)], (100.0, 100.0), clearance_mm=clearance_mm)
    assert excinfo.value.code == 'invalid_arrange'


def test_malformed_fixed_obstacle_raises_invalid_arrange():
    with pytest.raises(VoxelMillError) as excinfo:
        arrange_footprints([(5.0, 5.0)], (100.0, 100.0), fixed=[(0.0, 0.0, -1.0, 5.0)])
    assert excinfo.value.code == 'invalid_arrange'


def test_returned_positions_are_plain_float_tuples():
    positions = arrange_footprints([(5.0, 5.0)], (10.0, 10.0))
    x, y = positions[0]
    assert isinstance(x, float) and isinstance(y, float)
    assert math.isfinite(x) and math.isfinite(y)


def test_the_packed_group_is_centered_on_the_plate_not_pushed_into_a_corner():
    """A corner fill is the right pack and the wrong presentation."""
    footprints = [(10.0, 10.0), (10.0, 10.0)]
    envelope = (100.0, 100.0)
    positions = arrange_footprints(footprints, envelope, clearance_mm=2.0)
    assert_layout_is_legal(positions, footprints, envelope)
    span_x = [x for x, _ in positions]
    span_y = [y for _, y in positions]
    assert sum(span_x) / 2 == pytest.approx(0.0)
    assert sum(span_y) / 2 == pytest.approx(0.0)


def test_centering_is_skipped_when_fixed_obstacles_pin_the_layout():
    """Obstacles cannot move, so shifting the group would collide with them."""
    fixed = [(0.0, 0.0, 20.0, 20.0)]
    footprints = [(10.0, 10.0), (10.0, 10.0)]
    envelope = (60.0, 60.0)
    positions = arrange_footprints(footprints, envelope, fixed=fixed)
    assert_layout_is_legal(positions, footprints, envelope, fixed=fixed)
