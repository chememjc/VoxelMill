"""Full-resolution surface peel screening; an uncalibrated geometry advisory."""
from __future__ import annotations

import math

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components

from .config import DEFAULTS as SETTINGS_DEFAULTS
from .contracts import CancellationToken, VoxelMillError, Diagnostic, ResourceBudget, no_progress

DEFAULTS = SETTINGS_DEFAULTS['peel']
CHUNK = 65536
DETAIL_LIMIT = 64


def _speed(settings, bottom):
    motion = settings.get('printer', {}).get('motion', {})
    prefix = 'bottom_' if bottom else ''
    speeds = []
    for suffix in ('', '2'):
        height = float(motion.get(f'{prefix}lift_height{suffix}', 0.0))
        speed = float(motion.get(f'{prefix}lift_speed{suffix}', 0.0))
        if not math.isfinite(height) or not math.isfinite(speed) or height < 0 or speed < 0:
            raise VoxelMillError('peel_config', 'Lift heights and speeds must be finite and nonnegative')
        if height > 0:
            speeds.append(speed)
    return max(speeds, default=0.0)


def analyze_peel(triangles, bounds, settings, *, budget=None, cancel=None, progress=no_progress):
    """Group exact shared-edge downward faces, without sampling or repairing them."""
    cancel = cancel or CancellationToken()
    cancel.check()
    cfg = {**DEFAULTS, **settings.get('peel', {})}
    if not cfg['enabled']:
        return {'status': 'not_run', 'reason': 'peel check disabled', 'regions': [],
                'region_count': 0, 'threshold_region_count': 0}
    angle, threshold, reference = (float(cfg[k]) for k in
                                  ('max_angle_deg', 'area_threshold_mm2', 'reference_lift_speed'))
    if (not all(math.isfinite(v) for v in (angle, threshold, reference)) or
            not 0 <= angle < 90 or threshold <= 0 or reference <= 0):
        raise VoxelMillError('peel_config', 'Angle must be in [0,90); area threshold and reference speed must be positive and finite')
    triangles = np.asarray(triangles)
    bounds = np.asarray(bounds, dtype=float)
    if triangles.ndim != 3 or triangles.shape[1:] != (3, 3):
        raise VoxelMillError('peel_input', 'triangles must have shape (N, 3, 3)')
    if bounds.shape != (2, 3) or not np.isfinite(bounds).all():
        raise VoxelMillError('peel_input', 'bounds must be finite 2 by 3 coordinates')
    layer_height = float(settings.get('process', {}).get('layer_height_mm', .05))
    build_z = float(settings.get('printer', {}).get('build_mm', bounds[1])[2])
    if not math.isfinite(layer_height) or layer_height <= 0 or not math.isfinite(build_z) or build_z <= 0:
        raise VoxelMillError('peel_config', 'Layer height and build height must be positive and finite')
    bottom_top = max(0, int(settings.get('process', {}).get('bottom_layers', 0))) * layer_height
    bottom_speed, normal_speed = _speed(settings, True), _speed(settings, False)
    budget = budget or ResourceBudget(**settings.get('resources', {}))
    n = len(triangles)
    # Scan buffers plus worst-case chunk intermediates. The connectivity budget
    # below depends on selected faces, not on an invented lower-resolution mesh.
    scan_bytes = n * 24 + min(n, CHUNK) * 256
    budget.require(scan_bytes, 'peel face scan')
    areas = np.zeros(n, dtype=np.float64)
    projected = np.zeros(n, dtype=np.float64)
    cutoff = math.cos(math.radians(angle))
    for start in range(0, n, CHUNK):
        cancel.check()
        stop = min(n, start + CHUNK)
        chunk = np.asarray(triangles[start:stop], dtype=np.float64)
        if not np.isfinite(chunk).all():
            raise VoxelMillError('peel_input', 'triangles contain nonfinite coordinates')
        cross = np.cross(chunk[:, 1] - chunk[:, 0], chunk[:, 2] - chunk[:, 0])
        norm = np.linalg.norm(cross, axis=1)
        if not np.isfinite(norm).all():
            raise VoxelMillError('peel_numeric', 'Face area exceeded finite analysis range')
        selected = (norm > 0) & (-cross[:, 2] >= cutoff * norm)
        areas[start:stop] = np.where(selected, norm * .5, 0.)
        projected[start:stop] = np.where(selected, -cross[:, 2] * .5, 0.)
        progress('peel faces', stop, n)
    indices = np.flatnonzero(areas)
    common = {
        'status': 'complete', 'source_triangle_count': n, 'candidate_triangle_count': len(indices),
        'threshold_area_mm2': threshold, 'max_angle_deg': angle,
        'reference_lift_speed': reference, 'build_z_mm': build_z,
        'layer_height_mm': layer_height, 'bottom_layer_top_mm': bottom_top,
        'bottom_lift_speed': bottom_speed, 'normal_lift_speed': normal_speed,
        'heuristic': 'uncalibrated geometry score; not a force or failure prediction',
        'score_formula': '(projected_area_mm2 / threshold_area_mm2) * (1 + max(0, mean_z_mm) / build_z_mm) * (lift_speed / reference_lift_speed)',
        'basis': 'oriented STL winding; exact shared-edge connectivity; native profile speed units; physical plate Z=0',
        'limitations': ['summed projected surface area is not instantaneous peel contact area',
                       'does not model resin, film, tilt release or actual suction pressure',
                       'open, duplicate and nonmanifold surfaces require independent validation'],
    }
    if not len(indices):
        return {**common, 'region_count': 0, 'total_region_count': 0, 'threshold_region_count': 0,
                'maximum_projected_area_mm2': 0., 'maximum_score': 0.,
                'regions': [], 'regions_truncated': False}
    cancel.check()
    # Includes vertex sorting copies, edge keys, graph, aggregation and output
    # buffers. No per-edge Python object graph grows outside this estimate.
    budget.require(scan_bytes + len(indices) * 1024, 'peel face connectivity')
    faces = np.asarray(triangles[indices], dtype=np.float64)
    _, inverse = np.unique(faces.reshape(-1, 3), axis=0, return_inverse=True)
    cancel.check()
    vertices = inverse.reshape(-1, 3)
    edges = vertices[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2)
    edges.sort(axis=1)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    edges = edges[order]
    face_ids = np.repeat(np.arange(len(indices)), 3)[order]
    same = np.all(edges[1:] == edges[:-1], axis=1)
    # Chaining adjacent equal keys also handles >2 faces on a nonmanifold edge;
    # it doesn't claim that the topology itself is valid.
    graph = sparse.coo_matrix((np.ones(np.count_nonzero(same), dtype=np.uint8),
                               (face_ids[:-1][same], face_ids[1:][same])),
                              shape=(len(indices), len(indices))).tocsr()
    count, labels = connected_components(graph, directed=False)
    cancel.check()
    order = np.argsort(labels, kind='stable')
    starts = np.r_[0, np.flatnonzero(np.diff(labels[order])) + 1]
    sizes = np.diff(np.r_[starts, len(indices)])
    low = np.minimum.reduceat(faces.min(axis=1)[order], starts)
    high = np.maximum.reduceat(faces.max(axis=1)[order], starts)
    area = np.bincount(labels, weights=areas[indices], minlength=count)
    projection = np.bincount(labels, weights=projected[indices], minlength=count)
    mean_z = np.bincount(labels, weights=faces[:, :, 2].mean(axis=1) * areas[indices], minlength=count) / area
    # A region exactly on the normal-layer boundary uses the normal process.
    bottom = (low[:, 2] < bottom_top) & (high[:, 2] >= 0)
    normal = high[:, 2] >= bottom_top
    speed = np.maximum(np.where(bottom, bottom_speed, 0.), np.where(normal, normal_speed, 0.))
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        scores = projection / threshold * (1. + np.maximum(0., mean_z) / build_z) * speed / reference
    if not np.isfinite(scores).all():
        raise VoxelMillError('peel_numeric', 'Peel score exceeded finite analysis range')
    risky = np.flatnonzero(projection >= threshold)
    ranked = risky[np.lexsort((risky, -projection[risky], -scores[risky]))]
    regions = []
    for index in ranked[:DETAIL_LIMIT]:
        cancel.check()
        regions.append({
            'triangle_count': int(sizes[index]), 'area_mm2': float(area[index]),
            'projected_area_mm2': float(projection[index]),
            'bounds': [low[index].tolist(), high[index].tolist()],
            'z_layer_span': [int(math.floor(low[index, 2] / layer_height)),
                             int(math.floor(high[index, 2] / layer_height))],
            'bottom_layer_intersection': bool(bottom[index]), 'normal_layer_intersection': bool(normal[index]),
            'lift_speed': float(speed[index]), 'mean_z_mm': float(mean_z[index]),
            'score': float(scores[index]), 'threshold': True})
    cancel.check()
    return {**common, 'region_count': int(count), 'total_region_count': int(count),
            'threshold_region_count': len(risky),
            'maximum_projected_area_mm2': float(projection.max()), 'maximum_score': float(scores.max()),
            'regions': regions, 'regions_truncated': len(risky) > DETAIL_LIMIT}


def apply_peel_check(report, triangles, bounds, settings, **kwargs):
    """Attach advisory evidence; unavailable analysis cannot become a pass."""
    try:
        result = analyze_peel(triangles, bounds, settings, **kwargs)
    except VoxelMillError as error:
        if error.code == 'canceled':
            raise
        result = {'status': 'not_run', 'reason': str(error), 'error_code': error.code,
                  'error_details': error.details, 'regions': [], 'region_count': 0, 'threshold_region_count': 0}
    metrics = report.metrics if hasattr(report, 'metrics') else report.setdefault('metrics', {})
    checks = report.checks if hasattr(report, 'checks') else report.setdefault('checks', {})
    metrics['peel_risk'] = result
    checks['peel_risk'] = ('not_run' if result['status'] != 'complete' else
                           'warn' if result['threshold_region_count'] else 'pass')
    if checks['peel_risk'] == 'warn':
        diagnostic = Diagnostic('peel_risk',
                                'Large downward surface regions warrant review of orientation and release settings',
                                severity='warning', details=result)
        if hasattr(report, 'diagnostics'):
            report.diagnostics.append(diagnostic)
        else:
            report.setdefault('diagnostics', []).append({
                'code': diagnostic.code, 'message': diagnostic.message,
                'severity': diagnostic.severity, 'details': result})
    return report
