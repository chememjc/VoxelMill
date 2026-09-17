"""Deterministic bottom-left shelf packer for laying out plate footprints.

This is pure XY geometry: each object contributes an axis-aligned footprint
(the bounding box of its current rotation) and gets back a plate-center
offset. There is no mesh handling, no rotation search and no rendering here;
callers own turning the result into placements.
"""
from .contracts import VoxelMillError

_EPS = 1e-9


def _validate_footprints(footprints):
    parsed = []
    for index, item in enumerate(footprints):
        try:
            width, depth = item
            width = float(width)
            depth = float(depth)
        except (TypeError, ValueError):
            raise VoxelMillError('invalid_arrange', 'Each footprint needs a finite width and depth',
                                  {'index': index}) from None
        if not (_finite(width) and _finite(depth)) or width <= 0 or depth <= 0:
            raise VoxelMillError('invalid_arrange', 'Footprint dimensions must be finite and positive',
                                  {'index': index, 'width_mm': width, 'depth_mm': depth})
        parsed.append((width, depth))
    return parsed


def _finite(value):
    return value == value and value not in (float('inf'), float('-inf'))


def _validate_envelope(envelope):
    try:
        width, depth = envelope
        width = float(width)
        depth = float(depth)
    except (TypeError, ValueError):
        raise VoxelMillError('invalid_arrange', 'Envelope needs a finite width and depth') from None
    if not (_finite(width) and _finite(depth)) or width <= 0 or depth <= 0:
        raise VoxelMillError('invalid_arrange', 'Envelope dimensions must be finite and positive',
                              {'width_mm': width, 'depth_mm': depth})
    return width, depth


def _validate_clearance(clearance_mm):
    clearance_mm = float(clearance_mm)
    if not _finite(clearance_mm) or clearance_mm < 0:
        raise VoxelMillError('invalid_arrange', 'clearance_mm must be finite and non-negative',
                              {'clearance_mm': clearance_mm})
    return clearance_mm


def _validate_fixed(fixed):
    parsed = []
    for index, item in enumerate(fixed):
        try:
            x, y, width, depth = (float(v) for v in item)
        except (TypeError, ValueError):
            raise VoxelMillError('invalid_arrange', 'Each fixed obstacle needs x, y, width and depth',
                                  {'fixed_index': index}) from None
        if not all(_finite(v) for v in (x, y, width, depth)) or width <= 0 or depth <= 0:
            raise VoxelMillError('invalid_arrange', 'Fixed obstacle dimensions must be finite and positive',
                                  {'fixed_index': index})
        parsed.append((x, y, width, depth))
    return parsed


def _overlaps(candidate, other, clearance_mm):
    """True if two center-form rectangles are closer than clearance_mm on both axes."""
    cx, cy, cw, cd = candidate
    ox, oy, ow, od = other
    gap_x = abs(cx - ox) - (cw + ow) / 2
    gap_y = abs(cy - oy) - (cd + od) / 2
    return gap_x < clearance_mm - _EPS and gap_y < clearance_mm - _EPS


def _fits_envelope(candidate, envelope):
    cx, cy, cw, cd = candidate
    width, depth = envelope
    half_w, half_d = (width - cw) / 2, (depth - cd) / 2
    return -half_w - _EPS <= cx <= half_w + _EPS and -half_d - _EPS <= cy <= half_d + _EPS


def _candidate_positions(width, depth, envelope, clearance_mm, obstacles):
    """Candidate x, y pairs to try: envelope corner plus one edge past each obstacle."""
    env_w, env_d = envelope
    left = -(env_w - width) / 2
    bottom = -(env_d - depth) / 2
    xs = {left}
    ys = {bottom}
    for ox, oy, ow, od in obstacles:
        xs.add(ox + (ow + width) / 2 + clearance_mm)
        ys.add(oy + (od + depth) / 2 + clearance_mm)
    for x in sorted(xs):
        for y in sorted(ys):
            yield x, y


def arrange_footprints(footprints, envelope, *, clearance_mm=0.0, fixed=()):
    """Return one (x_mm, y_mm) plate-center offset per footprint.

    Places footprints on a deterministic shelf/skyline packer: indices are
    sorted by decreasing depth then decreasing width (original index breaks
    ties), and each is dropped into the lowest-then-leftmost legal spot,
    checked against the envelope, every already-placed rectangle and every
    fixed obstacle. Results are returned in the caller's original order.
    """
    parsed_footprints = _validate_footprints(footprints)
    envelope = _validate_envelope(envelope)
    clearance_mm = _validate_clearance(clearance_mm)
    obstacles = _validate_fixed(fixed)

    order = sorted(range(len(parsed_footprints)),
                    key=lambda i: (-parsed_footprints[i][1], -parsed_footprints[i][0], i))

    placed = list(obstacles)
    positions = [None] * len(parsed_footprints)
    placed_count = 0
    for index in order:
        width, depth = parsed_footprints[index]
        if width > envelope[0] + _EPS or depth > envelope[1] + _EPS:
            raise VoxelMillError('arrange_no_fit', 'A footprint does not fit the envelope',
                                  {'placed': placed_count, 'index': index, 'width_mm': width, 'depth_mm': depth})
        chosen = None
        for x, y in _candidate_positions(width, depth, envelope, clearance_mm, placed):
            candidate = (x, y, width, depth)
            if not _fits_envelope(candidate, envelope):
                continue
            if any(_overlaps(candidate, other, clearance_mm) for other in placed):
                continue
            if chosen is None or (y, x) < (chosen[1], chosen[0]):
                chosen = (x, y)
        if chosen is None:
            raise VoxelMillError('arrange_no_fit', 'Ran out of room while packing footprints',
                                  {'placed': placed_count, 'index': index, 'width_mm': width, 'depth_mm': depth})
        placed.append((chosen[0], chosen[1], width, depth))
        positions[index] = chosen
        placed_count += 1

    return _recentered(positions, parsed_footprints, obstacles)


def _recentered(positions, footprints, obstacles):
    """Slide the packed group so it sits centered on the plate.

    The packer fills from one corner, which is the right way to pack but the
    wrong way to present a plate: a user pressing Arrange expects the parts
    centered, not shoved into a corner. Shifting the whole group by the same
    vector cannot introduce an overlap, and the group's bounding box is no
    larger than the envelope, so nothing leaves the envelope either.

    Fixed obstacles are the exception. They cannot move, so the packed
    footprints are positioned relative to them and shifting would collide.
    """
    if obstacles or not positions:
        return positions
    lows_x = [x - width / 2 for (x, _), (width, _) in zip(positions, footprints)]
    highs_x = [x + width / 2 for (x, _), (width, _) in zip(positions, footprints)]
    lows_y = [y - depth / 2 for (_, y), (_, depth) in zip(positions, footprints)]
    highs_y = [y + depth / 2 for (_, y), (_, depth) in zip(positions, footprints)]
    shift_x = (min(lows_x) + max(highs_x)) / 2
    shift_y = (min(lows_y) + max(highs_y)) / 2
    return [(x - shift_x, y - shift_y) for x, y in positions]
