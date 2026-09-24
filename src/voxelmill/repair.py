"""Explicitly permitted volumetric repair by occupancy voxelization.

This is the ``aggressive`` repair path. It rebuilds the model as the boundary of
a well-composed occupancy volume, which is a valid, closed, orientable,
non-self-intersecting solid by construction, so Manifold accepts it. Every
supplied original fixture is rejected by Manifold without it.

Voxel pitch is a quantisation scale, not a surface-distance guarantee. Every
result is independently checked in both directions against the original
triangles using adaptive triangle coverings and nearest-surface distances. It
is never enabled implicitly; ``repair.aggressiveness`` must be ``aggressive``.
"""
from __future__ import annotations

import math
import time

import numpy as np

from .contracts import CancellationToken, VoxelMillError, ResourceBudget, no_progress

MAX_VOXEL_BYTES_FRACTION = 0.35


def _grid(bounds, voxel_mm, margin=2):
    low = np.asarray(bounds, dtype=float)[0] - margin * voxel_mm
    extent = np.ptp(np.asarray(bounds, dtype=float), axis=0) + 2 * margin * voxel_mm
    dims = np.maximum(1, np.ceil(extent / voxel_mm - 1e-9)).astype(np.int64) + 1
    return low, dims


def _grid_bytes(bounds, pitch):
    _, dims = _grid(bounds, pitch)
    return dims, float(np.prod(dims + 2))


def _finest_fitting_pitch(bounds, fine, coarse, ceiling):
    """Smallest pitch in [fine, coarse] whose padded grid fits ``ceiling`` bytes.

    ``coarse`` must already fit. Returns ``(pitch, dims, needed)``.
    """
    dims, needed = _grid_bytes(bounds, fine)
    if needed <= ceiling:
        return fine, dims, needed
    best_dims, best_needed = _grid_bytes(bounds, coarse)
    lo, hi = fine, coarse
    for _ in range(48):
        mid = (lo + hi) * 0.5
        mid_dims, mid_needed = _grid_bytes(bounds, mid)
        if mid_needed <= ceiling:
            hi, best_dims, best_needed = mid, mid_dims, mid_needed
        else:
            lo = mid
    return hi, best_dims, best_needed


def choose_voxel_size(bounds, settings, budget):
    """Largest size within budget whose half-diagonal meets max_deviation_mm."""
    repair = settings['repair']
    ceiling = budget.memory_gib * 1024**3 * MAX_VOXEL_BYTES_FRACTION
    requested = float(repair.get('voxel_size_mm') or 0.0)
    # Half the voxel diagonal equals max_deviation with no headroom: any
    # well-composedness addition then exceeds the stated bound. Derive 10%
    # finer so the bound is a ceiling, not an equality. When the derived
    # pitch does not fit the memory budget, coarsen toward — but never past —
    # the no-headroom deviation ceiling; an explicit voxel_size_mm never coarsens.
    deviation_ceiling = 2.0 * float(repair['max_deviation_mm']) / math.sqrt(3.0)
    derived = deviation_ceiling * 0.9
    size = requested if requested > 0 else derived
    if not math.isfinite(size) or size <= 0:
        raise VoxelMillError('invalid_repair',
                        'Voxel repair needs a positive repair.voxel_size_mm or repair.max_deviation_mm',
                        {'voxel_size_mm': repair.get('voxel_size_mm'),
                         'max_deviation_mm': repair['max_deviation_mm']})
    dims, needed = _grid_bytes(bounds, size)
    if needed <= ceiling:
        return size, dims, needed
    fitting = size * (needed / ceiling) ** (1 / 3)
    if requested > 0:
        raise VoxelMillError('repair_budget', 'Voxel repair grid exceeds the memory budget', {
            'voxel_size_mm': size, 'voxel_bytes': needed, 'budget_bytes': ceiling,
            'smallest_affordable_voxel_size_mm': fitting,
            'implied_max_deviation_mm': fitting * math.sqrt(3.0) / 2.0,
            'remedy': 'raise repair.max_deviation_mm, set repair.voxel_size_mm, or raise resources.memory_gib'})
    # Derived pitch: coarsen just enough to fit, capped at the deviation ceiling.
    _, needed_cap = _grid_bytes(bounds, deviation_ceiling)
    if needed_cap > ceiling:
        raise VoxelMillError('repair_budget', 'Voxel repair grid exceeds the memory budget', {
            'voxel_size_mm': size, 'voxel_bytes': needed, 'budget_bytes': ceiling,
            'smallest_affordable_voxel_size_mm': fitting,
            'implied_max_deviation_mm': fitting * math.sqrt(3.0) / 2.0,
            'remedy': 'raise repair.max_deviation_mm, set repair.voxel_size_mm, or raise resources.memory_gib'})
    # The finest pitch in (derived, deviation_ceiling] that fits.
    return _finest_fitting_pitch(bounds, size, deviation_ceiling, ceiling)


def _taubin(vertices, faces, iterations, lam, mu, max_displacement):
    """Volume-preserving smoothing, hard-capped against the blocky surface."""
    from scipy import sparse
    n = len(vertices)
    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    edges = np.concatenate([edges, edges[:, ::-1]])
    weights = np.ones(len(edges))
    adjacency = sparse.csr_matrix((weights, (edges[:, 0], edges[:, 1])), shape=(n, n))
    adjacency.data[:] = 1.0
    degree = np.asarray(adjacency.sum(axis=1)).ravel()
    degree[degree == 0] = 1.0
    original = vertices
    current = vertices.copy()
    for _ in range(iterations):
        for factor in (lam, mu):
            current += factor * ((adjacency @ current) / degree[:, None] - current)
    delta = current - original
    length = np.linalg.norm(delta, axis=1)
    scale = np.ones_like(length)
    over = length > max_displacement
    scale[over] = max_displacement / length[over]
    moved = original + delta * scale[:, None]
    return moved, float(np.linalg.norm(moved - original, axis=1).max(initial=0.0))


def voxel_repair(triangles, bounds, settings, *, budget=None, cancel=None, progress=no_progress,
                 source_volume_mm3=None):
    """Return ``(manifold_solid, report)`` for an explicitly requested repair."""
    from . import _native
    from .geometry import _manifold

    budget = budget or ResourceBudget(**settings['resources'])
    cancel = cancel or CancellationToken()
    repair = settings['repair']
    if repair['aggressiveness'] != 'aggressive':
        raise VoxelMillError('repair_not_permitted',
                        'Volumetric voxel repair requires repair.aggressiveness = "aggressive"',
                        {'aggressiveness': repair['aggressiveness']})
    started = time.monotonic()
    deviation_ceiling = 2.0 * float(repair['max_deviation_mm']) / math.sqrt(3.0)
    derived = deviation_ceiling * 0.9
    requested = float(repair.get('voxel_size_mm') or 0.0)
    size, dims, voxel_bytes = choose_voxel_size(bounds, settings, budget)
    coarsened = bool(requested <= 0 and size > derived + 1e-15)
    low, dims = _grid(bounds, size)
    nx, ny, nz = (int(v) for v in dims)
    budget.require(int(voxel_bytes) + len(triangles) * 96, 'voxel repair volume')
    volume = _native.VoxelVolume(nx, ny, nz, float(low[0]), float(low[1]), float(low[2]), size, size, size)
    raster = _native.Rasterizer(np.asarray(triangles), cancel.check)
    odd_rows = negative = filled = 0
    for k in range(nz):
        cancel.check()
        result = raster.slice(float(low[2] + (k + 0.5) * size), nx, ny,
                              float(low[0]), float(low[1]), size, size, cancel.check, 'nonzero')
        odd_rows += result['odd_rows']
        negative += result['negative_winding_crossings']
        filled += result['filled_pixels']
        volume.set_slice(k, result['mask'])
        progress('voxelize', k + 1, nz)
    voxel_volume_mm3 = filled * size ** 3
    composed = volume.make_well_composed(64, lambda stage, done, total: cancel.check())
    if composed['boundary_blocked']:
        raise VoxelMillError('repair_boundary', 'Well-composedness repair reached the volume border',
                        {'boundary_blocked': composed['boundary_blocked']})
    if not composed['converged']:
        raise VoxelMillError('repair_not_converged', 'Well-composedness repair did not converge', dict(composed))
    vertices, faces = volume.extract_surface(lambda stage, done, total: cancel.check())
    if not len(faces):
        raise VoxelMillError('repair_empty', 'Voxel repair produced no surface; the model rasterized empty')
    del volume, raster  # release dense occupancy before constructing distance trees
    half_diagonal = size * math.sqrt(3.0) / 2.0
    smoothing = int(repair.get('smooth_iterations', 0) or 0)
    displacement = 0.0
    allowance = float(repair['max_deviation_mm']) - half_diagonal
    if smoothing:
        if allowance <= 0:
            raise VoxelMillError('repair_deviation',
                            'Smoothing needs deviation headroom; lower repair.voxel_size_mm',
                            {'half_voxel_diagonal_mm': half_diagonal,
                             'max_deviation_mm': repair['max_deviation_mm']})
        vertices, displacement = _taubin(vertices, faces, smoothing, 0.5, -0.53, allowance)
    m = _manifold()
    solid = m.Manifold(m.Mesh64(np.ascontiguousarray(vertices, dtype=np.float64),
                                np.ascontiguousarray(faces, dtype=np.uint64)))
    if solid.status() != m.Error.NoError or solid.is_empty() or not solid.volume() > 0:
        raise VoxelMillError('repair_invalid_solid', 'Voxel repair did not produce a valid solid',
                        {'manifold_status': str(solid.status())})
    # Filling missing contours and composing voxels can move surfaces much
    # farther than half a voxel diagonal. Verify the actual output geometry.
    budget.require(len(triangles) * 240 + len(faces) * 240 + int(voxel_bytes),
                   'bidirectional repair deviation verification')
    surface = _native.verify_surface_deviation(
        np.asarray(triangles), np.ascontiguousarray(vertices[faces]),
        float(repair['max_deviation_mm']),
        lambda done, total: (cancel.check(), progress('repair_deviation', done, total)))
    if smoothing:
        # float32 on purpose: check the geometry the STL will actually store.
        intersection = _native.inspect_intersections(np.asarray(vertices[faces], dtype=np.float32))
        if intersection['self_intersections']:
            raise VoxelMillError('repair_self_intersection', 'Smoothing introduced surface intersections', dict(intersection))
    deviation = surface['certified_upper_bound_mm']
    report = {
        'operation': 'occupancy_voxel_repair',
        'aggressiveness': 'aggressive',
        'voxel_size_mm': size,
        'voxel_size_derived_mm': derived,
        'voxel_size_coarsened': coarsened,
        'voxel_grid': [nx, ny, nz],
        'voxel_grid_bytes': int(voxel_bytes),
        'rasterized_volume_mm3': voxel_volume_mm3,
        'well_composed_added_voxels': composed['added_voxels'],
        'well_composed_added_volume_mm3': composed['added_voxels'] * size ** 3,
        'well_composed_passes': composed['passes'],
        'open_contour_rows': odd_rows,
        'negative_winding_crossings': negative,
        'smoothing_iterations': smoothing,
        'smoothing_max_displacement_mm': displacement,
        'quantisation_half_diagonal_mm': half_diagonal,
        'surface_deviation': surface,
        'max_deviation_bound_mm': deviation,
        'max_deviation_mm': float(repair['max_deviation_mm']),
        'deviation_within_limit': bool(surface['passed']),
        'vertices': int(len(vertices)),
        'triangles': int(len(faces)),
        'volume_after_mm3': float(solid.volume()),
        'genus': int(solid.genus()),
        'seconds': time.monotonic() - started,
        'self_intersections': 'checked_none' if smoothing else 'none_by_construction',
        'volumetric_repair': 'occupancy_voxel_boundary',
    }
    if source_volume_mm3 is not None:
        report['source_signed_volume_mm3'] = float(source_volume_mm3)
        report['volume_comparison_note'] = 'Net signed-volume difference; invalid or open source volume is not physical resin volume'
        report['volume_before_mm3'] = float(source_volume_mm3)
        report['added_volume_mm3'] = max(0.0, report['volume_after_mm3'] - float(source_volume_mm3))
        report['removed_volume_mm3'] = max(0.0, float(source_volume_mm3) - report['volume_after_mm3'])
    if not report['deviation_within_limit']:
        raise VoxelMillError('repair_deviation', 'Voxel repair deviation bound exceeds repair.max_deviation_mm', report)
    return solid, report
