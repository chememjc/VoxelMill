"""Exact Manifold boolean operations, planar trimming, and open-cut capping.

Boolean and trim run the same ``assembly.prepare_model`` ingestion used by
prepare, so repair policy and cavity sealing match the rest of the pipeline.
Those paths require closed solids: trim intersects with a large half-space cube
so the cut is capped.

``cap_open_cuts`` is separate: it welds an already-open mesh, finds boundary
loops, and fills only those that lie within ``repair.max_deviation_mm`` of a
best-fit plane. Non-planar holes are reported, never invented, and voxel repair
is never invoked.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import product

import numpy as np

from . import geometry
from .assembly import prepare_model
from .contracts import CancellationToken, VoxelMillError, ResourceBudget, no_progress
from .mesh import weld_mesh

OPS = frozenset(('union', 'intersect', 'subtract'))
KEEP = frozenset(('positive', 'negative', 'both'))


def _require_solid(model, *, what):
    if model.solid is not None:
        return model.solid
    raise VoxelMillError(
        'invalid_solid',
        f'{what} requires a closed solid; conversion failed under current repair settings',
        {'findings': list(model.blocked_by), 'repair': model.repair,
         'aggressiveness': model.settings['repair']['aggressiveness']})


def _run_boolean(a, b, op, m):
    table = {
        'union': m.OpType.Add,
        'intersect': m.OpType.Intersect,
        'subtract': m.OpType.Subtract,
    }
    try:
        result = m.Manifold.batch_boolean([a, b], table[op])
    except ValueError as error:
        raise VoxelMillError('boolean_failed', f'{op} rejected its inputs: {error}',
                        {'operation': op}) from error
    if result.status() != m.Error.NoError or result.is_empty() or not np.isfinite(result.volume()) or result.volume() <= 0:
        raise VoxelMillError('boolean_failed', f'{op} produced an empty or invalid solid',
                        {'operation': op, 'manifold_status': str(result.status()),
                         'empty': bool(result.is_empty())})
    return result


def _half_space_cube(point, unit_normal, solid, m):
    """Closed cube covering the half-space ``dot(x - point, normal) >= 0`` near ``solid``."""
    box = np.asarray(solid.bounding_box(), dtype=float).reshape(2, 3)
    corners = np.asarray(list(product(*zip(box[0], box[1]))), dtype=float)
    relative = corners - point
    along = relative @ unit_normal
    depth = max(float(along.max()), 0.0) + 1.0
    lateral = relative - along[:, None] * unit_normal
    radius = float(np.linalg.norm(lateral, axis=1).max()) + 1.0
    xy = max(2.0 * radius, 2.0)
    z = max(depth, 1.0)
    local = m.Manifold.cube((xy, xy, z), True).translate((0.0, 0.0, z / 2.0))
    reference = np.array([0.0, 1.0, 0.0]) if abs(unit_normal[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
    x_axis = np.cross(reference, unit_normal)
    x_axis /= np.linalg.norm(x_axis)
    y_axis = np.cross(unit_normal, x_axis)
    transform = np.column_stack((x_axis, y_axis, unit_normal, point))
    return local.transform(transform)


def _trim_one(solid, point, unit_normal, settings, *, budget, cancel, side):
    m = geometry._manifold()
    cancel.check()
    half = _half_space_cube(point, unit_normal, solid, m)
    before = float(solid.volume())
    result = _run_boolean(solid, half, 'intersect', m)
    after = float(result.volume())
    triangles = geometry.manifold_triangles(result).astype(np.float32)
    return triangles, {
        'side': side,
        'volume_before_mm3': before,
        'volume_mm3': after,
        'added_volume_mm3': max(0.0, after - before),
        'removed_volume_mm3': max(0.0, before - after),
        'triangles_before': int(solid.num_tri()),
        'triangles': int(result.num_tri()),
        'triangle_count': len(triangles),
    }


def boolean_mesh(a_triangles, b_triangles, op, settings, budget=None, cancel=None,
                 progress=no_progress):
    """Union, intersect or subtract two triangle meshes as Manifold solids.

    Each input is prepared with ``assembly.prepare_model``. Invalid solids that
    cannot be repaired under the current settings raise ``VoxelMillError``.
    """
    if op not in OPS:
        raise VoxelMillError('invalid_option', f'op must be one of {sorted(OPS)}', {'op': op})
    cancel = cancel or CancellationToken()
    budget = budget or ResourceBudget(**settings['resources'])
    cancel.check()
    a_model = prepare_model(a_triangles, settings, budget=budget, cancel=cancel, progress=progress)
    b_model = prepare_model(b_triangles, settings, budget=budget, cancel=cancel, progress=progress)
    a_solid = _require_solid(a_model, what='Boolean')
    b_solid = _require_solid(b_model, what='Boolean')
    m = geometry._manifold()
    cancel.check()
    vol_a, vol_b = float(a_solid.volume()), float(b_solid.volume())
    result = _run_boolean(a_solid, b_solid, op, m)
    after = float(result.volume())
    triangles = geometry.manifold_triangles(result).astype(np.float32)
    report = {
        'operation': op,
        'volume_a_mm3': vol_a,
        'volume_b_mm3': vol_b,
        'volume_mm3': after,
        'added_volume_mm3': max(0.0, after - vol_a),
        'removed_volume_mm3': max(0.0, vol_a - after),
        'triangles_a': int(a_solid.num_tri()),
        'triangles_b': int(b_solid.num_tri()),
        'triangles': int(result.num_tri()),
        'triangle_count': len(triangles),
        'repair_a': a_model.repair,
        'repair_b': b_model.repair,
        'cavity_fill_a': a_model.cavity_fill,
        'cavity_fill_b': b_model.cavity_fill,
    }
    return triangles, report


def trim_mesh(triangles, point, normal, settings, keep='positive', budget=None, cancel=None,
              progress=no_progress):
    """Keep one or both half-spaces of a plane and cap the cut with a planar face.

    ``keep='positive'`` retains ``dot(x - point, normal) >= 0``; ``'negative'``
    retains the other side; ``'both'`` returns ``((positive, negative), report)``.
    """
    if keep not in KEEP:
        raise VoxelMillError('invalid_option', f'keep must be one of {sorted(KEEP)}', {'keep': keep})
    point = np.asarray(point, dtype=float)
    normal = np.asarray(normal, dtype=float)
    if point.shape != (3,) or normal.shape != (3,) or not np.isfinite([*point, *normal]).all():
        raise VoxelMillError('invalid_option', 'point and normal must be finite XYZ triples')
    length = float(np.linalg.norm(normal))
    if length <= 0.0:
        raise VoxelMillError('invalid_option', 'normal must be a nonzero vector')
    unit = normal / length
    cancel = cancel or CancellationToken()
    budget = budget or ResourceBudget(**settings['resources'])
    cancel.check()
    model = prepare_model(triangles, settings, budget=budget, cancel=cancel, progress=progress)
    solid = _require_solid(model, what='Trim')
    before = float(solid.volume())
    if keep == 'both':
        pos, pos_report = _trim_one(solid, point, unit, settings, budget=budget, cancel=cancel,
                                    side='positive')
        neg, neg_report = _trim_one(solid, point, -unit, settings, budget=budget, cancel=cancel,
                                    side='negative')
        report = {
            'operation': 'trim',
            'keep': 'both',
            'point_mm': point.tolist(),
            'normal': unit.tolist(),
            'volume_before_mm3': before,
            'added_volume_mm3': 0.0,
            'removed_volume_mm3': max(0.0, before - pos_report['volume_mm3']
                                      - neg_report['volume_mm3']),
            'positive': pos_report,
            'negative': neg_report,
            'triangles_before': int(solid.num_tri()),
            'repair': model.repair,
            'cavity_fill': model.cavity_fill,
        }
        return (pos, neg), report
    side_normal = unit if keep == 'positive' else -unit
    out, side_report = _trim_one(solid, point, side_normal, settings, budget=budget, cancel=cancel,
                                 side=keep)
    report = {
        'operation': 'trim',
        'keep': keep,
        'point_mm': point.tolist(),
        'normal': unit.tolist(),
        'volume_before_mm3': before,
        'volume_mm3': side_report['volume_mm3'],
        'added_volume_mm3': side_report['added_volume_mm3'],
        'removed_volume_mm3': side_report['removed_volume_mm3'],
        'triangles_before': side_report['triangles_before'],
        'triangles': side_report['triangles'],
        'triangle_count': side_report['triangle_count'],
        'repair': model.repair,
        'cavity_fill': model.cavity_fill,
    }
    return out, report


def _directed_boundary_edges(faces):
    """Undirected edges used once, oriented as in the incident face."""
    occurrences = defaultdict(list)
    for face in faces:
        a, b, c = (int(face[0]), int(face[1]), int(face[2]))
        for u, v in ((a, b), (b, c), (c, a)):
            occurrences[(u, v) if u < v else (v, u)].append((u, v))
    directed = []
    for key, uses in occurrences.items():
        if len(uses) == 1:
            directed.append(uses[0])
        elif len(uses) == 2 and uses[0] == uses[1]:
            # Same directed edge twice is inconsistent winding, not a boundary.
            continue
    return directed, int(sum(1 for uses in occurrences.values() if len(uses) == 1))


def _boundary_loops(directed_edges):
    """Chain directed boundary edges into simple vertex cycles.

    Returns ``(loops, leftover_edges)``. A leftover means the boundary is not a
    disjoint union of simple loops (branching or incomplete chains).
    """
    succ = {}
    indeg = defaultdict(int)
    for a, b in directed_edges:
        if a in succ:
            return [], list(directed_edges)
        succ[a] = b
        indeg[b] += 1
    for vertex, outs in succ.items():
        if indeg[vertex] != 1:
            return [], list(directed_edges)
    seen = set()
    loops = []
    for start in list(succ):
        if start in seen:
            continue
        loop = [start]
        seen.add(start)
        cur = succ[start]
        while cur != start:
            if cur in seen or cur not in succ:
                return [], list(directed_edges)
            loop.append(cur)
            seen.add(cur)
            cur = succ[cur]
        if len(loop) < 3:
            return [], list(directed_edges)
        loops.append(loop)
    leftover = [edge for edge in directed_edges if edge[0] not in seen]
    return loops, leftover


def _fit_plane(points):
    """Best-fit plane via SVD. Returns centroid, unit normal, max abs deviation."""
    pts = np.asarray(points, dtype=float)
    centroid = pts.mean(axis=0)
    centered = pts - centroid
    if len(pts) < 3:
        return centroid, np.array([0.0, 0.0, 1.0]), float('inf')
    _, singular, vh = np.linalg.svd(centered, full_matrices=False)
    if singular.size < 3 or not np.isfinite(singular).all():
        normal = np.array([0.0, 0.0, 1.0])
    else:
        normal = vh[-1].astype(float)
        length = float(np.linalg.norm(normal))
        if length <= 0.0:
            normal = np.array([0.0, 0.0, 1.0])
        else:
            normal = normal / length
    deviation = float(np.max(np.abs(centered @ normal))) if len(pts) else 0.0
    return centroid, normal, deviation


def _project_loop(points, normal):
    """Orthonormal in-plane basis; returns (u, v, uv coords)."""
    reference = np.array([0.0, 1.0, 0.0]) if abs(normal[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u_axis = np.cross(reference, normal)
    u_len = float(np.linalg.norm(u_axis))
    if u_len <= 0.0:
        u_axis = np.array([1.0, 0.0, 0.0])
    else:
        u_axis = u_axis / u_len
    v_axis = np.cross(normal, u_axis)
    uv = np.column_stack((points @ u_axis, points @ v_axis))
    return u_axis, v_axis, uv


def _polygon_area2(uv):
    x, y = uv[:, 0], uv[:, 1]
    return float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _is_convex_ccw(uv):
    n = len(uv)
    for i in range(n):
        a, b, c = uv[i], uv[(i + 1) % n], uv[(i + 2) % n]
        if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < -1e-12:
            return False
    return True


def _ear_clip_indices(uv):
    """Ear-clip a simple polygon given in CCW ``uv`` order. Returns index triples."""
    n = len(uv)
    if n < 3:
        return []
    if n == 3:
        return [(0, 1, 2)]
    indices = list(range(n))
    triangles = []

    def area2(i0, i1, i2):
        a, b, c = uv[i0], uv[i1], uv[i2]
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def point_in_tri(p, i0, i1, i2):
        a0 = area2_pts(p, uv[i1], uv[i2])
        a1 = area2_pts(uv[i0], p, uv[i2])
        a2 = area2_pts(uv[i0], uv[i1], p)
        return a0 >= -1e-12 and a1 >= -1e-12 and a2 >= -1e-12

    def area2_pts(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    guard = n * n
    while len(indices) > 3 and guard > 0:
        guard -= 1
        clipped = False
        m = len(indices)
        for i in range(m):
            prev_i = indices[(i - 1) % m]
            cur_i = indices[i]
            next_i = indices[(i + 1) % m]
            if area2(prev_i, cur_i, next_i) <= 1e-12:
                continue
            ear = True
            for other in indices:
                if other in (prev_i, cur_i, next_i):
                    continue
                if point_in_tri(uv[other], prev_i, cur_i, next_i):
                    ear = False
                    break
            if not ear:
                continue
            triangles.append((prev_i, cur_i, next_i))
            del indices[i]
            clipped = True
            break
        if not clipped:
            break
    if len(indices) == 3:
        triangles.append((indices[0], indices[1], indices[2]))
    return triangles


def _triangulate_loop(points):
    """Triangulate a planar loop, preserving vertex order (outward for reversed boundary)."""
    pts = np.asarray(points, dtype=float)
    centroid, normal, _ = _fit_plane(pts)
    _, _, uv = _project_loop(pts - centroid, normal)
    if abs(_polygon_area2(uv)) <= 1e-18:
        raise VoxelMillError('cap_degenerate', 'Boundary loop has zero projected area',
                        {'vertex_count': len(pts)})
    # Ear clipping expects CCW in the projection; flip UV if the loop is CW there
    # without reversing 3D vertex order (winding is fixed by topology).
    if _polygon_area2(uv) < 0:
        uv = uv.copy()
        uv[:, 0] *= -1.0
    n = len(pts)
    if n == 3:
        faces = [(0, 1, 2)]
    elif _is_convex_ccw(uv):
        faces = [(0, i, i + 1) for i in range(1, n - 1)]
    else:
        faces = _ear_clip_indices(uv)
    if len(faces) != n - 2:
        raise VoxelMillError('cap_failed', 'Could not triangulate a planar boundary loop',
                        {'vertex_count': n, 'triangles': len(faces)})
    return np.asarray([pts[list(face)] for face in faces], dtype=np.float64)


def cap_open_cuts(triangles, settings, budget=None, cancel=None, progress=no_progress):
    """Fill near-planar boundary loops; refuse jagged holes.

    Welds with exact coordinate equality, groups boundary edges into loops, and
    for each loop whose vertices lie within ``repair.max_deviation_mm`` of a
    best-fit plane, appends a planar triangulation with winding opposite the
    directed boundary (consistent outward orientation with the existing faces).

    Loops that exceed the planarity tolerance are listed in the error details and
    are not filled. This path never runs voxel repair. With an explicit repair
    policy, zero-area or non-finite input triangles are dropped before welding
    and counted in the report; ``repair.aggressiveness = "none"`` keeps strict
    rejection. When every boundary loop is planar the result has
    ``boundary_edges == 0``.
    """
    cancel = cancel or CancellationToken()
    budget = budget or ResourceBudget(**settings['resources'])
    triangles = np.asarray(triangles)
    if triangles.ndim != 3 or triangles.shape[1:] != (3, 3) or len(triangles) == 0:
        raise VoxelMillError('invalid_mesh', 'Expected nonempty triangles with shape (n,3,3)')
    input_triangle_count = int(len(triangles))
    tol = float(settings['repair']['max_deviation_mm'])
    if not np.isfinite(tol) or tol < 0.0:
        raise VoxelMillError('invalid_option', 'repair.max_deviation_mm must be a finite non-negative number',
                        {'max_deviation_mm': tol})
    cancel.check()
    repair_policy = str(settings['repair'].get('aggressiveness', 'conservative'))
    # Match native weld semantics (double coordinates and an exactly-zero
    # cross product) without allocating float64 temporaries for the whole
    # original.  The input skulls contain millions of triangles.
    valid = np.ones(len(triangles), dtype=bool)
    chunk_size = 65536
    for start in range(0, len(triangles), chunk_size):
        cancel.check()
        stop = min(start + chunk_size, len(triangles))
        block = np.asarray(triangles[start:stop], dtype=np.float64)
        finite = np.isfinite(block).all(axis=(1, 2))
        edges = block[:, 1:] - block[:, :1]
        cross = np.cross(edges[:, 0], edges[:, 1])
        valid[start:stop] = finite & (np.einsum('ij,ij->i', cross, cross) != 0.0)
    dropped_invalid = int(np.count_nonzero(~valid))
    if dropped_invalid:
        if repair_policy == 'none':
            raise VoxelMillError('invalid_mesh',
                            'Cannot weld invalid triangles without an explicit repair policy',
                            {'invalid_triangles': dropped_invalid})
        budget.require(int(valid.nbytes + np.count_nonzero(valid) * triangles.itemsize),
                       'cap invalid-triangle filtering')
        triangles = np.asarray(triangles[valid])
        if len(triangles) == 0:
            raise VoxelMillError('invalid_mesh', 'All triangles are invalid after repair filtering',
                            {'invalid_triangles': dropped_invalid})
    progress('cap_weld', 0, 1)
    vertices, faces = weld_mesh(triangles, budget=budget, cancel=cancel, progress=progress)
    progress('cap_weld', 1, 1)
    cancel.check()
    directed, boundary_edges = _directed_boundary_edges(faces)
    loops, leftover = _boundary_loops(directed)
    loop_reports = []
    caps = []
    if leftover:
        raise VoxelMillError(
            'cap_incomplete',
            'Boundary edges do not form simple loops; planar capping refused',
            {'boundary_edges': boundary_edges, 'leftover_boundary_edges': len(leftover),
             'planarity_tolerance_mm': tol, 'capped_loops': 0, 'remaining_loops': 0,
             'boundary_loops': 0, 'dropped_invalid_triangles': dropped_invalid,
             'repair': repair_policy})
    for index, loop in enumerate(loops):
        cancel.check()
        progress('cap_loops', index, len(loops))
        pts = vertices[np.asarray(loop, dtype=np.int64)]
        _, normal, deviation = _fit_plane(pts)
        entry = {
            'vertex_count': len(loop),
            'max_deviation_mm': deviation,
            'normal': normal.tolist(),
            'capped': False,
        }
        if deviation <= tol + 1e-12:
            # Reverse directed boundary so new faces oppose existing edge winding.
            ordered = pts[::-1]
            try:
                filled = _triangulate_loop(ordered)
            except VoxelMillError as error:
                entry['error'] = error.code
                loop_reports.append(entry)
                continue
            caps.append(filled.astype(np.float32))
            entry['capped'] = True
            entry['triangles_added'] = len(filled)
        loop_reports.append(entry)
    progress('cap_loops', len(loops), max(len(loops), 1))
    capped = sum(1 for item in loop_reports if item['capped'])
    remaining = len(loops) - capped
    triangles_before = input_triangle_count
    if remaining or (boundary_edges and not loops):
        raise VoxelMillError(
            'cap_incomplete',
            f'Capped {capped} of {len(loops)} boundary loops; '
            f'{remaining} exceed repair.max_deviation_mm={tol}',
            {'boundary_edges': boundary_edges, 'boundary_loops': len(loops),
             'capped_loops': capped, 'remaining_loops': remaining,
             'planarity_tolerance_mm': tol, 'loops': loop_reports,
             'triangles_before': triangles_before,
             'dropped_invalid_triangles': dropped_invalid,
             'repair': repair_policy})
    if caps:
        out = np.concatenate([np.asarray(triangles, dtype=np.float32), *caps], axis=0)
    else:
        out = np.asarray(triangles, dtype=np.float32).copy()
    from . import _native
    cancel.check()
    inventory = _native.inspect_mesh(out)
    if int(inventory['boundary_edges']) != 0:
        raise VoxelMillError(
            'cap_incomplete',
            'Planar caps were added but the mesh still has boundary edges',
            {'boundary_edges_after': int(inventory['boundary_edges']),
             'capped_loops': capped, 'boundary_loops': len(loops),
             'planarity_tolerance_mm': tol, 'loops': loop_reports,
             'dropped_invalid_triangles': dropped_invalid,
             'repair': repair_policy})
    report = {
        'operation': 'cap_open_cuts',
        'planarity_tolerance_mm': tol,
        'planarity': 'best-fit plane via SVD; max vertex distance <= repair.max_deviation_mm',
        'boundary_edges_before': boundary_edges,
        'boundary_edges_after': 0,
        'boundary_loops': len(loops),
        'capped_loops': capped,
        'remaining_loops': 0,
        'triangles_before': triangles_before,
        'dropped_invalid_triangles': dropped_invalid,
        'repair': repair_policy,
        'triangles_added': int(sum(len(block) for block in caps)),
        'triangle_count': int(len(out)),
        'volume_mm3': float(inventory['signed_volume_mm3']),
        'loops': loop_reports,
    }
    return out, report
