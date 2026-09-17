"""Support base strategies built from explicit planar footprints.

Extruded footprints have open vertical holes, never roofed cavities. Geometry
and material measurements are exact for the emitted polygons; adhesion,
removal force, and peel stability still require printed calibration artifacts.
"""
from __future__ import annotations

import math
import numpy as np

from .contracts import CancellationToken, VoxelMillError
from .geometry import raft_from_feet

CIRCLE_SEGMENTS = 32
MAX_BASE_FEET = 8192
MAX_GRID_LINES = 2048
MAX_HEX_CELLS = 20000
MAX_TRIANGLE_CELLS = 20000


def minimum_spanning_edges(points, *, cancel=None):
    """Deterministic Euclidean Prim tree using O(n) memory, O(n²) work.

    Caller supplies lexicographically sorted unique points. No distance matrix,
    Qhull perturbation, or nearest-neighbour approximation is needed, including
    for collinear and two-point inputs. Ties retain the first sorted parent.
    """
    cancel = cancel or CancellationToken()
    points = np.unique(np.asarray(points, dtype=float).reshape(-1, 2), axis=0)
    count = len(points)
    if count > MAX_BASE_FEET:
        raise VoxelMillError('base_complexity', f'Base exceeds {MAX_BASE_FEET} unique feet')
    if count < 2:
        return []
    best = np.full(count, np.inf)
    parent = np.full(count, -1, dtype=int)
    used = np.zeros(count, dtype=bool)
    best[0] = 0
    edges = []
    for _ in range(count):
        cancel.check()
        node = int(np.argmin(best))
        if parent[node] >= 0:
            edges.append((int(parent[node]), node))
        used[node] = True
        delta = points - points[node]
        distance2 = np.einsum('ij,ij->i', delta, delta)
        better = (~used) & (distance2 < best)
        best[better], parent[better] = distance2[better], node
        best[used] = np.inf
    return edges


def triangulation_edges(points, *, cancel=None):
    """Return deterministic Delaunay edges, with an MST fallback for degeneracy.

    Delaunay triangles provide the open triangular bays of the ``triangle``
    base.  Qhull deliberately receives sorted points and no joggling option,
    so ordinary inputs produce repeatable edges.  A one-dimensional foot set
    has no triangles; its MST is the useful, connected geometry in that case.
    """
    from scipy.spatial import Delaunay, QhullError

    cancel = cancel or CancellationToken()
    points = np.unique(np.asarray(points, dtype=float).reshape(-1, 2), axis=0)
    count = len(points)
    if count > MAX_BASE_FEET:
        raise VoxelMillError('base_complexity', f'Base exceeds {MAX_BASE_FEET} unique feet')
    if count < 3:
        return minimum_spanning_edges(points, cancel=cancel), 0
    cancel.check()
    try:
        mesh = Delaunay(points, qhull_options='Qbb Qc Qz')
    except QhullError:
        # Collinear and otherwise rank-deficient foot layouts still need a
        # valid base.  The tree has no open triangular cells but joins every
        # pad with bounded O(n²) work.
        return minimum_spanning_edges(points, cancel=cancel), 0
    simplices = np.asarray(mesh.simplices, dtype=int)
    if len(simplices) > MAX_TRIANGLE_CELLS:
        raise VoxelMillError('base_complexity',
                        f'Triangle base exceeds {MAX_TRIANGLE_CELLS} cells',
                        {'candidate_cells': int(len(simplices))})
    edges = set()
    for face in simplices:
        cancel.check()
        for first, second in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edges.add(tuple(sorted((int(first), int(second)))))
    return sorted(edges), len(simplices)


def _circle(radius, position=(0, 0), segments=CIRCLE_SEGMENTS):
    import manifold3d as m
    return m.CrossSection.circle(radius, segments).translate(tuple(position))


def _capsule(start, end, radius):
    # Hull of two polygonal discs: the total length includes both end radii.
    return (_circle(radius, start) + _circle(radius, end)).hull()


def _union(shapes):
    import manifold3d as m
    return m.CrossSection.batch_boolean(shapes, m.OpType.Add)


def _record_footprint(record, footprint, solid):
    area = float(footprint.area())
    envelope_area = float(footprint.hull().area())
    open_area = max(0., envelope_area - area)
    record.update(contact_area_mm2=area, convex_envelope_area_mm2=envelope_area,
                  open_area_mm2=open_area,
                  open_area_fraction=open_area / envelope_area if envelope_area else 0.,
                  connected_components=len(solid.decompose()) if solid is not None else len(footprint.decompose()),
                  footprint_basis='union of emitted polygon sections at the plate')


def _grid(footprint, pitch, width, angle, cancel):
    """Square grid clipped to a convex frame, with a connected perimeter rim."""
    import manifold3d as m
    frame = footprint.hull().rotate(-angle)
    x0, y0, x1, y1 = frame.bounds()
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    extents = [(x1 - x0) / (2 * pitch), (y1 - y0) / (2 * pitch)]
    if any(not math.isfinite(value) or value > MAX_GRID_LINES for value in extents):
        raise VoxelMillError('base_complexity', f'Grid exceeds {MAX_GRID_LINES} candidate lines; '
                        'increase base_cell_size_mm')
    counts = [math.ceil(value) for value in extents]
    line_count = 2 * sum(counts) + 2
    if line_count > MAX_GRID_LINES:
        raise VoxelMillError('base_complexity', f'Grid exceeds {MAX_GRID_LINES} candidate lines; '
                        'increase base_cell_size_mm', {'candidate_lines': line_count})
    strips = []
    for index in range(-counts[0], counts[0] + 1):
        cancel.check()
        strips.append(m.CrossSection.square((width, y1 - y0 + 2 * width), True).translate(
            (cx + index * pitch, cy)))
    for index in range(-counts[1], counts[1] + 1):
        cancel.check()
        strips.append(m.CrossSection.square((x1 - x0 + 2 * width, width), True).translate(
            (cx, cy + index * pitch)))
    # Every clipped strip meets this rim. The foot tree joins the rim through
    # hull-extreme foot pads, so a foot between grid lines is never isolated.
    rim = frame - frame.offset(-width, circular_segments=CIRCLE_SEGMENTS)
    result = ((_union(strips) ^ frame) + rim).rotate(angle)
    return result, line_count


def _hexagons(footprint, pitch, width, angle, cancel):
    """Honeycomb: hexagonal openings with `width` walls, clipped to a convex frame.

    Cell centers sit on the triangular lattice whose six neighbours are all
    `pitch` apart, so `base_cell_size_mm` means the same center-to-center
    distance it means for the square grid. Every opening is `width` clear of
    every other, so the walls are connected before the rim is added.
    """
    frame = footprint.hull().rotate(-angle)
    x0, y0, x1, y1 = frame.bounds()
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    column, row = pitch * math.sqrt(3) / 2, pitch
    extents = [(x1 - x0) / (2 * column), (y1 - y0) / (2 * row) + 1]
    if any(not math.isfinite(value) or value > MAX_HEX_CELLS for value in extents):
        raise VoxelMillError('base_complexity', f'Honeycomb exceeds {MAX_HEX_CELLS} candidate cells; '
                        'increase base_cell_size_mm')
    columns, rows = (math.ceil(value) for value in extents)
    cells = (2 * columns + 1) * (2 * rows + 1)
    if cells > MAX_HEX_CELLS:
        raise VoxelMillError('base_complexity', f'Honeycomb exceeds {MAX_HEX_CELLS} candidate cells; '
                        'increase base_cell_size_mm', {'candidate_cells': cells})
    # circle(r, 6) puts a vertex on +x, so flat-to-flat is sqrt(3) r vertically.
    opening = _circle((pitch - width) / math.sqrt(3), segments=6)
    shapes = []
    for index in range(-columns, columns + 1):
        cancel.check()
        offset = row / 2 if index % 2 else 0.
        for line in range(-rows, rows + 1):
            shapes.append(opening.translate((cx + index * column, cy + line * row + offset)))
    rim = frame - frame.offset(-width, circular_segments=CIRCLE_SEGMENTS)
    return ((frame - _union(shapes)) + rim).rotate(angle), cells


def _extrude(footprint, thickness, slope, step, record, cancel):
    """Extrude a footprint, tapering the wall inward from the plate if asked.

    The taper is a stack of whole printed layers rather than a smooth ramp,
    because a printed layer band is what the machine makes; a smooth cone in
    the STL would only be resampled to the same steps at slice time. The
    footprint at the plate is the untouched one, so the measured contact area
    is still the contact area.
    """
    if not slope:
        return footprint.extrude(thickness)
    import manifold3d as m
    setback = 1 / math.tan(math.radians(slope))
    steps = max(1, round(thickness / step))
    height = thickness / steps
    bands = []
    for index in range(steps):
        cancel.check()
        section = footprint if not index else footprint.offset(
            -index * height * setback, circular_segments=CIRCLE_SEGMENTS)
        if section.is_empty() or section.area() <= 0:
            raise VoxelMillError('invalid_support',
                            'support.base_edge_slope_deg consumes the base before its thickness; '
                            'use a steeper slope, a thinner base, or wider base geometry',
                            {'steps': steps, 'exhausted_at_step': index,
                             'height_reached_mm': index * height, 'thickness_mm': thickness})
        # Each band starts at the plate, not at its own step, so successive
        # bands overlap in volume. Stacking them face-to-face instead unions
        # into a solid decompose() splits into several bodies once the bands
        # are thin, which the caller's connectivity gate then rejects.
        bands.append(section.extrude((index + 1) * height))
    record.update(edge_slope_deg=slope, edge_steps=steps, edge_step_mm=height,
                  edge_setback_mm=thickness * setback,
                  edge_basis='taper quantised to whole printed layers, widest at the plate')
    return m.Manifold.batch_boolean(bands, m.OpType.Add)


def build_base(feet, settings, pillar_radius, *, foot_radii=None, cancel=None):
    """Return the base solid (or None) and measured geometry evidence.

    ``foot_radii`` carries actual plate-touch radii, including short-pillar
    classes and cones that start at the plate. It only affects bare-foot area;
    explicit pad geometry always uses its configured dimensions.
    """
    import manifold3d as m
    cancel = cancel or CancellationToken()
    cancel.check()
    support = settings['support']
    kind = support['base_type']
    feet = np.asarray(feet, dtype=float).reshape(-1, 2)
    if not np.isfinite(feet).all() or not math.isfinite(pillar_radius) or pillar_radius <= 0:
        raise VoxelMillError('invalid_support', 'Base needs finite feet and a positive pillar radius')
    record = {'type': kind, 'feet': int(len(feet)), 'solid': False,
              'volume_mm3': None, 'contact_area_mm2': None,
              'removal': None, 'establishes': 'the shape and size of the base',
              'does_not_establish': 'plate adhesion, removal force, or print stability'}
    if not len(feet):
        record['reason'] = 'no support anchored on the plate'
        return {'solid': None, 'record': record}
    unique = np.unique(feet, axis=0)
    record['unique_feet'] = len(unique)
    if kind not in ('none', 'plate', 'pad') and len(unique) > MAX_BASE_FEET:
        raise VoxelMillError('base_complexity', f'Base exceeds {MAX_BASE_FEET} unique feet')
    if kind == 'none':
        radii = (np.full(len(feet), pillar_radius) if foot_radii is None
                 else np.asarray(foot_radii, dtype=float))
        if radii.shape != (len(feet),) or not np.isfinite(radii).all() or (radii <= 0).any():
            raise VoxelMillError('invalid_support', 'Every plate foot requires a positive finite radius')
        shapes = []
        for point, radius in zip(feet, radii):
            cancel.check()
            # Pillars/tips use 24-sided rings; count overlaps only once.
            shapes.append(_circle(float(radius), point, segments=24))
        footprint = _union(shapes)
        _record_footprint(record, footprint, None)
        record.update(volume_mm3=0., removal='no added base; individual support feet',
                      nominal_disc_area_mm2=float(np.sum(math.pi * radii ** 2)))
        return {'solid': None, 'record': record}
    if kind == 'plate':
        thickness = float(support['raft_thickness_mm'])
        slope = float(support.get('raft_slope_deg', 30.0))
        solid = raft_from_feet(feet, pillar_radius, float(support['raft_expansion_mm']),
                               thickness, min(.25, thickness / 4), slope)
        footprint = solid.project()
        record.update(thickness_mm=thickness, expansion_mm=float(support['raft_expansion_mm']),
                      raft_slope_deg=slope,
                      removal='one connected convex slab covering every foot; outer rim sloped for a putty knife')
    else:
        thickness = float(support['base_thickness_mm']) or float(support['raft_thickness_mm'])
        diameter = float(support['base_touch_diameter_mm']) or 2 * (
            pillar_radius + float(support['raft_expansion_mm']))
        radius = diameter / 2
        if radius <= pillar_radius:
            raise VoxelMillError('invalid_support', 'support.base_touch_diameter_mm must exceed the pillar diameter')
        record.update(pad_diameter_mm=diameter, pad_thickness_mm=thickness)
        shapes = []
        for point in unique:
            cancel.check()
            if kind == 'skate':
                length = float(support['base_skate_length_mm']) or diameter
                if length < diameter:
                    raise VoxelMillError('invalid_support', 'Skate length must be at least the touch diameter')
                half = (length - diameter) / 2
                shape = _capsule((-half, 0), (half, 0), radius).rotate(
                    float(support['base_rotation_deg'])).translate(tuple(point))
            else:
                shape = _circle(radius, point, segments=24 if kind == 'pad' else CIRCLE_SEGMENTS)
            shapes.append(shape)
        footprint = _union(shapes)
        if kind == 'pad':
            record.update(removal='individual round pads; overlapping pads merge',
                          note='circular foot pads, not an inferred CHITUBOX skate shape')
        elif kind == 'skate':
            # With base_edge_slope_deg the shared extrude turns this capsule into
            # a frustum (wider at the plate); slope 0 keeps the vertical wall.
            record.update(skate_length_mm=length, rotation_deg=float(support['base_rotation_deg']),
                          removal='individual skate feet; overlapping feet merge',
                          note='circular or elongated frustum when sloped, vertical capsule when not; '
                               'CHITUBOX elongation is not known')
        elif kind in ('skeleton', 'triangle', 'grid', 'hex'):
            width = float(support['base_strut_width_mm']) or 2 * pillar_radius
            if kind == 'triangle':
                edges, triangle_cells = triangulation_edges(unique, cancel=cancel)
            else:
                edges = minimum_spanning_edges(unique, cancel=cancel)
            tethers = []
            for a, b in edges:
                cancel.check()
                tethers.append(_capsule(unique[a], unique[b], width / 2))
            record.update(strut_width_mm=width, skeleton_edges=len(edges),
                          skeleton_length_mm=sum(float(np.linalg.norm(unique[a] - unique[b]))
                                                 for a, b in edges))
            if kind != 'triangle':
                record['tree_basis'] = 'Euclidean minimum spanning tree; sorted ties'
            if kind == 'triangle':
                frame = footprint.hull()
                rim = frame - frame.offset(-width, circular_segments=CIRCLE_SEGMENTS)
                shapes.append(rim)
                record.update(triangle_edges=len(edges), triangle_cells=triangle_cells,
                              removal='connected Delaunay triangle struts, perimeter rim and foot pads; '
                                      'vertical triangular openings',
                              triangle_basis='sorted-point Delaunay edges; MST fallback for degenerate feet')
            if kind in ('grid', 'hex'):
                pitch = float(support['base_cell_size_mm'])
                if width >= pitch:
                    raise VoxelMillError('invalid_support', 'Lattice strut width must be less than cell size')
                rotation = float(support['base_rotation_deg'])
                lattice, count = (_grid if kind == 'grid' else _hexagons)(
                    footprint, pitch, width, rotation, cancel)
                shapes.append(lattice)
                record.update(cell_size_mm=pitch, rotation_deg=rotation, perimeter_width_mm=width,
                              cell_basis='center-to-center spacing of adjacent cells')
                if kind == 'grid':
                    record.update(grid_candidate_lines=count,
                                  removal='connected grid, perimeter rim and foot tree; vertical openings')
                else:
                    record.update(hex_candidate_cells=count,
                                  removal='connected honeycomb, perimeter rim and foot tree; '
                                          'vertical openings')
            elif kind == 'skeleton':
                record.update(removal='connected foot tree; open space between its branches')
            footprint = _union([*shapes, *tethers])
        else:
            raise VoxelMillError('invalid_support', f'Unknown base type {kind!r}')
        cancel.check()
        solid = _extrude(footprint, thickness, float(support['base_edge_slope_deg']),
                         float(settings['process']['layer_height_mm']), record, cancel)
    cancel.check()
    if solid.status() != m.Error.NoError or solid.is_empty():
        raise VoxelMillError('invalid_support', 'Base construction did not produce valid geometry')
    _record_footprint(record, footprint, solid)
    if kind in ('skeleton', 'triangle', 'grid', 'hex') and record['connected_components'] != 1:
        raise VoxelMillError('invalid_support', 'Base connections are below geometry precision; '
                        'increase base_strut_width_mm')
    record.update(solid=True, volume_mm3=float(solid.volume()))
    return {'solid': solid, 'record': record}
