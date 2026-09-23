"""Manual paint-on support blockers and enforcers, in final plate coordinates.

Nothing here is automatic: a face is blocked or enforced only when the user
paints it. Island births still receive contacts even on a blocked region,
because an unsupported island will not print.
"""

import numpy as np

from .contracts import VoxelMillError


def paint_key(point):
    try:
        values = np.asarray(point, dtype=float)
    except (TypeError, ValueError):
        raise VoxelMillError('invalid_paint', 'Paint mark needs three finite coordinates') from None
    if values.shape != (3,) or not np.isfinite(values).all():
        raise VoxelMillError('invalid_paint', 'Paint mark needs three finite coordinates')
    return tuple(round(float(value), 6) for value in values)


def empty_paint():
    return {'blocked': [], 'enforced': []}


def normalize_paint(records):
    """Return ``{blocked, enforced}`` lists of plate-coordinate centroids."""
    if records in (None, {}):
        return empty_paint()
    if not isinstance(records, dict):
        raise VoxelMillError('invalid_paint', 'Paint records must be an object with blocked and enforced arrays')
    unknown = set(records) - {'blocked', 'enforced'}
    if unknown:
        raise VoxelMillError('invalid_paint', 'Unknown paint fields', {'unknown': sorted(unknown)})
    result = empty_paint()
    for kind in ('blocked', 'enforced'):
        values = records.get(kind) or []
        if not isinstance(values, (list, tuple)):
            raise VoxelMillError('invalid_paint', f'Paint {kind} must be an array of positions')
        seen = set()
        for point in values:
            key = paint_key(point)
            if key in seen:
                continue
            seen.add(key)
            result[kind].append([float(v) for v in key])
    return result


def brush_centroids(triangles, point, radius):
    """Centroids of triangles whose centroid falls inside the brush."""
    triangles = np.asarray(triangles, dtype=float).reshape(-1, 3, 3)
    point = np.asarray(point, dtype=float)
    radius = float(radius)
    if triangles.size == 0 or point.shape != (3,) or not np.isfinite(point).all() or not np.isfinite(radius) or radius <= 0:
        raise VoxelMillError('invalid_paint', 'Brush needs a mesh, a finite point and a positive radius')
    centroids = triangles.mean(axis=1)
    nearby = np.linalg.norm(centroids - point, axis=1) <= radius
    if not nearby.any():
        nearest = int(np.argmin(np.linalg.norm(centroids - point, axis=1)))
        nearby[nearest] = True
    return centroids[nearby]


def _near_mask(points, marks, radius):
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    marks = np.asarray(marks, dtype=float).reshape(-1, 3)
    if not len(points) or not len(marks):
        return np.zeros(len(points), dtype=bool)
    from scipy.spatial import cKDTree
    distance = cKDTree(marks).query(points, distance_upper_bound=float(radius))[0]
    return np.isfinite(distance)


def apply_paint(samples, paint, spacing):
    """Drop blocked samples and return enforced centroids as extra contacts.

    Block wins over enforce on the same mark. Coverage should use the filtered
    samples so a blocked sealing face does not fail for lack of a pillar.
    """
    paint = normalize_paint(paint)
    samples = np.asarray(samples, dtype=float).reshape(-1, 3)
    radius = float(spacing) / 2
    blocked = _near_mask(samples, paint['blocked'], radius)
    kept = samples[~blocked] if len(samples) else samples
    enforced = np.asarray(paint['enforced'], dtype=float).reshape(-1, 3)
    if len(enforced) and paint['blocked']:
        enforced = enforced[~_near_mask(enforced, paint['blocked'], radius)]
    return kept, enforced, {
        'blocked_marks': len(paint['blocked']),
        'enforced_marks': len(paint['enforced']),
        'samples_blocked': int(blocked.sum()) if len(samples) else 0,
        'enforced_contacts': int(len(enforced)),
        'automatic': False,
        'block_wins_over_enforce': True,
    }

def empty_object_paint(count=1):
    """One empty record per plate object."""
    return [empty_paint() for _ in range(max(1, int(count)))]


def normalize_object_paint(records, count=None):
    """Per-object paint, each record local to its own object's mesh.

    Marks used to be stored in final plate coordinates, which meant moving a
    part left its paint behind in space. Storing them in the object's own frame
    is what makes paint a property of the part rather than of the plate.
    """
    if records in (None, {}, []):
        return empty_object_paint(count or 1)
    if isinstance(records, dict):
        raise VoxelMillError(
            'invalid_paint',
            'Paint is one record per plate object; a single blocked/enforced table is '
            'the superseded plate-coordinate form and is not converted, because its '
            'marks cannot be attributed to a part after the fact')
    if not isinstance(records, (list, tuple)):
        raise VoxelMillError('invalid_paint', 'Paint must be an array of per-object records')
    result = [normalize_paint(record) for record in records]
    if count is not None:
        count = max(1, int(count))
        result = result[:count]
        result.extend(empty_paint() for _ in range(count - len(result)))
    return result


def transform_marks(points, matrix):
    """Move paint marks between frames with a 4x4 homogeneous matrix."""
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    if not len(points):
        return points.reshape(0, 3)
    matrix = np.asarray(matrix, dtype=float)
    if matrix.shape != (4, 4):
        raise VoxelMillError('invalid_paint', 'A paint transform needs a 4x4 matrix')
    padded = np.concatenate([points, np.ones((len(points), 1))], axis=1)
    return (padded @ matrix.T)[:, :3]


def to_plate(object_paint, matrices):
    """Combine per-object local marks into one plate-coordinate record.

    Routing and display both work in plate coordinates, so this is the single
    place the per-object frames are collapsed. An object with no matrix yet --
    the preview has not caught up -- contributes nothing rather than
    contributing marks in the wrong frame.
    """
    combined = empty_paint()
    for index, record in enumerate(object_paint or ()):
        if index >= len(matrices or ()):
            break
        matrix = matrices[index]
        if matrix is None:
            continue
        for kind in ('blocked', 'enforced'):
            points = record.get(kind) or []
            if not points:
                continue
            combined[kind].extend(
                [float(v) for v in point] for point in transform_marks(points, matrix))
    return normalize_paint(combined)


def to_local(points, matrix):
    """Plate-coordinate picks expressed in one object's own frame."""
    matrix = np.asarray(matrix, dtype=float)
    if matrix.shape != (4, 4):
        raise VoxelMillError('invalid_paint', 'A paint transform needs a 4x4 matrix')
    try:
        inverse = np.linalg.inv(matrix)
    except np.linalg.LinAlgError:
        raise VoxelMillError('invalid_paint', 'This part\'s placement cannot be inverted') from None
    return transform_marks(points, inverse)
