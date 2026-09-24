"""Audit a routed support plan for collisions it was not meant to make.

Routing clears shafts against a sampled model field and a capsule index, so
it avoids collisions by construction, up to the field's pitch. This module
checks the result exactly, independently of how the router decided:

* **support into model**: the exact Manifold intersection of the support
  solids with each model part, split into connected pieces. A piece is
  *expected* when it sits at a contact tip or a model anchor, where the
  support is meant to penetrate; every other piece is an intrusion.
* **support into support**: pairs of graph edges whose capsules overlap
  although the graph never joins them. Branches that merge share a node and
  are not counted.

The report is evidence for troubleshooting multi-part plates and routing
changes; it does not repair anything.
"""
from __future__ import annotations

import numpy as np

from .contracts import CancellationToken

#: Node kinds where a support is meant to enter model material.
PENETRATING_KINDS = frozenset({'contact', 'model_anchor'})
#: Pieces smaller than this are boolean noise, not collisions.
MIN_VOLUME_MM3 = 1e-6


def tip_allowance_mm(settings):
    """Radius around a contact or anchor node where model intersection is expected.

    A tip cone enters the surface ``penetration`` deep and widens to its base
    diameter over ``tip_length``; on a sloped face the whole tip can graze the
    surface, so the allowance covers the tip's full extent plus its widest
    radius.
    """
    support = settings['support']
    pillar = float(support['pillar_diameter_mm'])
    tip_base = float(support.get('tip_base_diameter_mm') or 0.0) or pillar
    reach = max(float(support['tip_length_mm']) + float(support['penetration_mm']),
                float(support['model_anchor_length_mm'])
                + float(support['model_anchor_penetration_mm']))
    return reach + max(tip_base, pillar, float(support['contact_diameter_mm'])) / 2.0


def support_model_intrusion(support_solids, part_solids, graph, settings, *, cancel=None):
    """Intersect the supports with every part and classify each piece.

    ``part_solids`` is a list of Manifolds (``None`` entries are skipped and
    reported as unchecked). Returns a JSON-ready dict.
    """
    import manifold3d as m
    cancel = cancel or CancellationToken()
    solids = [solid for solid in support_solids if solid is not None]
    result = {'parts': [], 'intrusions': 0, 'intrusion_volume_mm3': 0.0,
              'expected_pieces': 0, 'unchecked_parts': 0,
              'allowance_mm': tip_allowance_mm(settings), 'worst': None}
    if not solids:
        return result
    supports = m.Manifold.batch_boolean(solids, m.OpType.Add)
    cancel.check()
    anchors = np.asarray([node.position_mm for node in graph.nodes
                          if node.kind in PENETRATING_KINDS], dtype=float).reshape(-1, 3)
    allowance = result['allowance_mm']
    for index, part in enumerate(part_solids):
        if part is None:
            result['unchecked_parts'] += 1
            result['parts'].append({'part': index, 'checked': False})
            continue
        overlap = m.Manifold.batch_boolean([supports, part], m.OpType.Intersect)
        cancel.check()
        entry = {'part': index, 'checked': True, 'expected_pieces': 0, 'intrusions': 0,
                 'intrusion_volume_mm3': 0.0}
        if overlap.status() == m.Error.NoError and not overlap.is_empty():
            for piece in overlap.decompose():
                volume = float(piece.volume())
                if volume < MIN_VOLUME_MM3:
                    continue
                low, high = (np.asarray(corner, dtype=float) for corner in
                             np.asarray(piece.bounding_box(), dtype=float).reshape(2, 3))
                reach = _farthest_corner_distance(anchors, low, high)
                if reach is not None and reach <= allowance:
                    entry['expected_pieces'] += 1
                    continue
                entry['intrusions'] += 1
                entry['intrusion_volume_mm3'] += volume
                record = {'part': index, 'volume_mm3': volume,
                          'center_mm': ((low + high) / 2).round(4).tolist(),
                          'nearest_tip_mm': None if reach is None else round(reach, 4)}
                if result['worst'] is None or volume > result['worst']['volume_mm3']:
                    result['worst'] = record
        result['parts'].append(entry)
        result['expected_pieces'] += entry['expected_pieces']
        result['intrusions'] += entry['intrusions']
        result['intrusion_volume_mm3'] += entry['intrusion_volume_mm3']
    return result


def _farthest_corner_distance(points, low, high):
    """Smallest, over ``points``, of the distance to the box's farthest corner."""
    if not len(points):
        return None
    corners = np.array([[x, y, z] for x in (low[0], high[0]) for y in (low[1], high[1])
                        for z in (low[2], high[2])])
    distances = np.linalg.norm(points[:, None, :] - corners[None, :, :], axis=2).max(axis=1)
    return float(distances.min())


def segment_distances(p0, p1, q0, q1):
    """Closest distance between segment pairs, vectorized over the first axis."""
    d1, d2, r = p1 - p0, q1 - q0, p0 - q0
    a = np.einsum('ij,ij->i', d1, d1)
    e = np.einsum('ij,ij->i', d2, d2)
    f = np.einsum('ij,ij->i', d2, r)
    c = np.einsum('ij,ij->i', d1, r)
    b = np.einsum('ij,ij->i', d1, d2)
    denom = a * e - b * b
    tiny = 1e-12
    s = np.where(denom > tiny, np.clip((b * f - c * e) / np.where(denom > tiny, denom, 1.0), 0, 1), 0.0)
    t = np.where(e > tiny, (b * s + f) / np.where(e > tiny, e, 1.0), 0.0)
    s = np.where(t < 0, np.where(a > tiny, np.clip(-c / np.where(a > tiny, a, 1.0), 0, 1), 0.0), s)
    s = np.where(t > 1, np.where(a > tiny, np.clip((b - c) / np.where(a > tiny, a, 1.0), 0, 1), 0.0), s)
    t = np.clip(t, 0, 1)
    delta = (p0 + d1 * s[:, None]) - (q0 + d2 * t[:, None])
    return np.linalg.norm(delta, axis=1)


def support_overlaps(graph, *, tolerance_mm=1e-3, limit=32):
    """Pairs of graph edges whose capsules overlap without a shared node.

    Edges meeting at a node, one edge apart through a shared neighbour, or at
    nodes that coincide in space, are joined on purpose. Everything else that overlaps is a collision between two
    supports the router believed were apart.
    """
    from scipy.spatial import cKDTree
    positions = {node.id: np.asarray(node.position_mm, dtype=float) for node in graph.nodes}
    edges = [edge for edge in graph.edges if edge.start in positions and edge.end in positions]
    result = {'edges': len(edges), 'overlaps': 0, 'examples': []}
    if len(edges) < 2:
        return result
    # Nodes one edge apart. A pillar split around a brace junction, or a tip
    # meeting its shaft through a short collar, joins two edges through a
    # node neither of them owns; those meet on purpose too.
    neighbours = {}
    for edge in edges:
        neighbours.setdefault(edge.start, set()).add(edge.end)
        neighbours.setdefault(edge.end, set()).add(edge.start)
    starts = np.array([positions[edge.start] for edge in edges])
    ends = np.array([positions[edge.end] for edge in edges])
    radii = np.array([float(edge.radius_mm) for edge in edges])
    mids = (starts + ends) / 2
    halves = np.linalg.norm(ends - starts, axis=1) / 2
    reach = float((halves + radii).max()) * 2
    pairs = np.array(sorted(cKDTree(mids).query_pairs(reach)), dtype=int).reshape(-1, 2)
    if not len(pairs):
        return result
    i, j = pairs[:, 0], pairs[:, 1]
    bound = halves[i] + halves[j] + radii[i] + radii[j]
    keep = np.linalg.norm(mids[i] - mids[j], axis=1) <= bound
    i, j = i[keep], j[keep]
    distance = segment_distances(starts[i], ends[i], starts[j], ends[j])
    hit = distance < radii[i] + radii[j] - tolerance_mm
    for a, b, gap in zip(i[hit], j[hit], distance[hit]):
        first, second = edges[a], edges[b]
        ends_a = (starts[a], ends[a])
        ends_b = (starts[b], ends[b])
        near_first = {first.start, first.end} | neighbours[first.start] | neighbours[first.end]
        if near_first & {second.start, second.end}:
            continue
        if any(np.linalg.norm(x - y) <= tolerance_mm for x in ends_a for y in ends_b):
            continue
        result['overlaps'] += 1
        if len(result['examples']) < limit:
            result['examples'].append({
                'edges': [[first.start, first.end, first.kind], [second.start, second.end, second.kind]],
                'distance_mm': round(float(gap), 4),
                'radii_mm': [round(float(radii[a]), 4), round(float(radii[b]), 4)]})
    return result
