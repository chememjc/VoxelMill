"""Standalone parametric support connectors."""
from __future__ import annotations

import math
import numpy as np

from .contracts import VoxelMillError


def tip_segment(start, end, base_radius, contact_radius, shape='cone',
                break_point_diameter_mm=0.0, *, segments=32):
    """Build a tip, optionally blending a ball at its top contact."""
    from .geometry import cylinder_between
    start, end = np.asarray(start, dtype=float), np.asarray(end, dtype=float)
    values = np.r_[start, end, base_radius, contact_radius, break_point_diameter_mm]
    if (start.shape != (3,) or end.shape != (3,) or not np.isfinite(values).all()
            or base_radius <= 0 or contact_radius <= 0
            or shape not in ('cone', 'cylinder') or break_point_diameter_mm < 0):
        raise VoxelMillError('invalid_support', 'Invalid tip segment parameters')
    if shape == 'cone':
        solid = cylinder_between(start, end, base_radius, contact_radius, segments)
    else:
        # A cylindrical tip has one radius throughout; contact diameter is
        # the effective radius for this shape, while base diameter remains a
        # cone-only control.
        solid = cylinder_between(start, end, contact_radius, contact_radius, segments)
    if break_point_diameter_mm:
        axis = end - start
        length = float(np.linalg.norm(axis))
        if break_point_diameter_mm > length:
            raise VoxelMillError('invalid_support', 'Break-point ball diameter must fit within the tip segment')
        axis /= length
        # The ball is centered at the top-contact break point and overlaps the
        # shaft, making one closed solid rather than touching shells.
        center = end - axis * (break_point_diameter_mm / 2)
        ball = __import__('manifold3d').Manifold.sphere(
            break_point_diameter_mm / 2, segments).translate(center)
        solid = solid + ball
    return solid


def small_model_pillar(start, end, radius, shape='cone', upper_depth=0.0,
                       lower_depth=0.0, *, segments=32):
    """Build one watertight model-to-model connector around a surface segment.

    ``start`` and ``end`` are the two surface contacts.  A conical connector
    has apexes at the requested buried depths and a cylindrical shaft between
    the surfaces.  A cylindrical connector keeps one radius through those
    extensions.  The complete shell is assembled as one mesh, avoiding the
    unreliable face-only joins produced by unions of touching primitives.
    """
    import manifold3d as m

    try:
        start, end = np.asarray(start, dtype=float), np.asarray(end, dtype=float)
        values = np.concatenate((start.reshape(-1), end.reshape(-1),
                                 [radius, upper_depth, lower_depth]))
    except (TypeError, ValueError):
        raise VoxelMillError('invalid_support',
                        'Model pillar endpoints, radius, and depths must be finite') from None
    if start.shape != (3,) or end.shape != (3,) or not np.isfinite(values).all():
        raise VoxelMillError('invalid_support', 'Model pillar endpoints, radius, and depths must be finite')
    if radius <= 0 or upper_depth < 0 or lower_depth < 0 or shape not in ('cone', 'cylinder'):
        raise VoxelMillError('invalid_support', 'Model pillar needs a positive radius, nonnegative depths, and a valid shape')
    if not isinstance(segments, (int, np.integer)) or segments < 8:
        raise VoxelMillError('invalid_support', 'Model pillar needs at least eight circular segments')
    axis = end - start
    length = float(np.linalg.norm(axis))
    if length <= 1e-9:
        raise VoxelMillError('invalid_support', 'Model pillar endpoints must define a positive segment')
    z = axis / length
    reference = np.array([0., 1., 0.]) if abs(z[1]) < .9 else np.array([1., 0., 0.])
    x = np.cross(reference, z); x /= np.linalg.norm(x)
    y = np.cross(z, x)

    if shape == 'cone':
        positions = [start - z * lower_depth, start, end, end + z * upper_depth]
        radii = [0., radius, radius, 0.]
        # Remove zero-length end cones, leaving a flat cap at that surface.
        if lower_depth == 0:
            positions.pop(0); radii.pop(0)
        if upper_depth == 0:
            positions.pop(); radii.pop()
    else:
        positions = [start - z * lower_depth, end + z * upper_depth]
        radii = [radius, radius]

    vertices = []
    rings = []
    for position, ring_radius in zip(positions, radii):
        if ring_radius == 0:
            rings.append([len(vertices)])
            vertices.append(position)
        else:
            ring = []
            for index in range(segments):
                angle = 2 * math.pi * index / segments
                ring.append(len(vertices))
                vertices.append(position + ring_radius * (math.cos(angle) * x + math.sin(angle) * y))
            rings.append(ring)

    faces = []
    for before, after in zip(rings, rings[1:]):
        if len(before) == 1:
            apex = before[0]
            for index in range(segments):
                faces.append((apex, after[(index + 1) % segments], after[index]))
        elif len(after) == 1:
            apex = after[0]
            for index in range(segments):
                faces.append((before[index], before[(index + 1) % segments], apex))
        else:
            for index in range(segments):
                following = (index + 1) % segments
                faces.extend(((before[index], after[following], after[index]),
                              (before[index], before[following], after[following])))

    # Flat ends occur only for a zero-depth cone or for every cylinder.
    if len(rings[0]) > 1:
        center = len(vertices); vertices.append(positions[0])
        for index in range(segments):
            faces.append((center, rings[0][(index + 1) % segments], rings[0][index]))
    if len(rings[-1]) > 1:
        center = len(vertices); vertices.append(positions[-1])
        for index in range(segments):
            faces.append((center, rings[-1][index], rings[-1][(index + 1) % segments]))

    solid = m.Manifold(m.Mesh64(np.asarray(vertices, dtype=np.float64),
                                np.asarray(faces, dtype=np.uint64)))
    if solid.status() != m.Error.NoError or solid.is_empty() or solid.volume() <= 0:
        raise VoxelMillError('invalid_support', 'Model pillar construction did not produce valid geometry')
    return solid
