"""Full-resolution rigid placement and valid-solid geometry operations.

No function mutates source triangles. Preview searches never certify geometry.
"""
from __future__ import annotations
from itertools import product
import math
import numpy as np
from .contracts import CancellationToken, VoxelMillError, Placement, ResourceBudget

CHUNK_SIZE = 65536

# Orientation ranking weights. Downward-facing area drives support count, its
# product with height approximates support volume, height drives print time and
# pillar slenderness, and the plate footprint is a peel-force proxy. These are
# calibration starting points, not measured constants.
SUPPORT_WEIGHT = 1.0
SUPPORT_VOLUME_WEIGHT = 1.0
HEIGHT_WEIGHT = 0.6
PEEL_WEIGHT = 0.6
# Applied to the finalists only, where a coarse occupancy grid exists. Trapped
# resin and sealed cavities dominate: they spoil a print outright, while the
# cheap terms only make one more or less convenient. All remain uncalibrated.
TRAPPED_WEIGHT = 4.0
CAVITY_WEIGHT = 4.0
ACCESS_WEIGHT = 1.5
STABILITY_WEIGHT = 0.8


def _check_shape(triangles):
    if triangles.ndim != 3 or triangles.shape[1:] != (3, 3) or len(triangles) == 0:
        raise VoxelMillError('invalid_mesh', 'Expected a nonempty (N, 3, 3) triangle array')


def triangle_bounds(triangles, matrix=None, cancel=None):
    _check_shape(triangles)
    low, high = np.full(3, np.inf), np.full(3, -np.inf)
    for chunk in iter_transformed_triangles(triangles, matrix, cancel=cancel):
        low = np.minimum(low, chunk.min(axis=(0, 1)))
        high = np.maximum(high, chunk.max(axis=(0, 1)))
    return np.stack((low, high))


def iter_transformed_triangles(triangles, matrix=None, chunk_size=CHUNK_SIZE, cancel=None):
    _check_shape(triangles)
    if chunk_size < 1:
        raise ValueError('chunk_size must be positive')
    matrix = np.eye(4) if matrix is None else np.asarray(matrix, dtype=np.float64)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all() or not np.array_equal(matrix[3], [0, 0, 0, 1]):
        raise VoxelMillError('invalid_transform', 'Expected a finite affine 4 by 4 matrix')
    reverses = float(np.linalg.det(matrix[:3, :3])) < 0.0
    for start in range(0, len(triangles), chunk_size):
        if cancel:
            cancel.check()
        chunk = np.asarray(triangles[start:start + chunk_size], dtype=np.float64)
        if not np.isfinite(chunk).all():
            raise VoxelMillError('nonfinite_coordinates', 'Mesh contains nonfinite coordinates')
        transformed = chunk @ matrix[:3, :3].T + matrix[:3, 3]
        if reverses:
            # A mirror has a negative determinant, which turns every outward
            # normal inward.  Reversing the vertex order restores the winding,
            # without which the raster's nonzero-winding fill reads the whole
            # part as empty and the exact union reads it as a hole.
            transformed = transformed[:, ::-1, :]
        yield transformed


MAX_SCALE = 100.0
MIN_SCALE = 0.01


def scale_matrix(scale=(1.0, 1.0, 1.0), mirror=(False, False, False)):
    """Per-axis scale with mirrored axes folded in as negative factors.

    Scale is bounded rather than merely finite: a factor outside
    ``[MIN_SCALE, MAX_SCALE]`` is far more likely to be a unit mistake — a
    meter-to-millimeter slip, say — than an intention, and it would silently
    produce a part that no build volume can hold or that quantises to nothing.
    A caller that genuinely wants more scales twice and says so.
    """
    factors = np.asarray(scale, dtype=float)
    if factors.shape == ():
        factors = np.repeat(factors, 3)
    if factors.shape != (3,) or not np.isfinite(factors).all():
        raise VoxelMillError('invalid_scale', 'Scale must be one or three finite factors')
    if (factors <= 0).any():
        raise VoxelMillError('invalid_scale', 'Scale factors must be positive; use mirror to flip an axis')
    if (factors < MIN_SCALE).any() or (factors > MAX_SCALE).any():
        raise VoxelMillError('invalid_scale',
                        f'Scale factors must lie between {MIN_SCALE} and {MAX_SCALE}',
                        {'scale': factors.tolist()})
    flips = np.asarray(mirror, dtype=bool)
    if flips.shape != (3,):
        raise VoxelMillError('invalid_mirror', 'Mirror must be three booleans, one per axis')
    return np.diag(np.where(flips, -factors, factors)), factors, flips


def scale_note(factors, flips):
    """A plain statement of what a scale or mirror did, for the report.

    Returns ``None`` when the pose is the identity, so an untransformed part
    carries no note at all rather than a note saying nothing happened.
    """
    factors = np.asarray(factors, dtype=float)
    flips = np.asarray(flips, dtype=bool)
    if np.allclose(factors, 1.0) and not flips.any():
        return None
    axes = 'XYZ'
    parts = []
    if not np.allclose(factors, 1.0):
        uniform = np.allclose(factors, factors[0])
        parts.append(f'scaled by {factors[0]:g}' if uniform else
                     'scaled per axis by ' + ', '.join(f'{a}={v:g}' for a, v in zip(axes, factors)))
        if not uniform:
            parts.append('non-uniform scaling changes every fit, thread pitch and '
                         'clearance in the part and is never a dimensional correction')
    if flips.any():
        parts.append('mirrored on ' + ', '.join(a for a, f in zip(axes, flips) if f)
                     + '; a mirrored threaded or keyed part will not assemble')
    return '; '.join(parts)


def rotation_matrix(rotation_deg):
    angles = np.asarray(rotation_deg, dtype=float)
    if angles.shape != (3,) or not np.isfinite(angles).all():
        raise VoxelMillError('invalid_rotation', 'Rotation must contain three finite angles in degrees')
    x, y, z = np.radians(angles)
    cx, cy, cz = np.cos([x, y, z]); sx, sy, sz = np.sin([x, y, z])
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rz @ ry @ rx


def wrap_rotation_deg(angles):
    """Wrap Euler degrees into ``(-180, 180]``; never clamp -- rotation is periodic.

    A rotation of 200 degrees is the same orientation as -160, so a control
    with a -180..180 range must fold the angle back rather than truncate it:
    truncating at the boundary silently discards whatever pushed it past 180.
    This is the one wrap point every angle should pass through -- the
    document's stored pose, an added model's pose, and any GUI control that
    accumulates a delta (a nudge, a gizmo commit) -- rather than each one
    rounding its own way. Convention: 180 and -180 both name the same
    orientation and both canonicalize to +180 (181 wraps to -179, 200 to
    -160). The CLI's pose handling has the same periodicity and should share
    this rather than grow a second copy of the formula.

    Accepts a single angle or a sequence; returns the same shape.
    """
    single = np.ndim(angles) == 0
    values = np.atleast_1d(np.asarray(angles, dtype=float))
    # An angle already in range is returned bit-for-bit. The modular formula
    # below is exact only in principle: 180 - ((180 - 34.567) % 360) comes back
    # as 34.567000000000007, and an orientation candidate the editor promises
    # to apply exactly would stop comparing equal to the one it was offered.
    wrapped = np.where((values > -180.0) & (values <= 180.0),
                       values, 180.0 - np.mod(180.0 - values, 360.0))
    return float(wrapped[0]) if single else [float(v) for v in wrapped]


def envelope_fits(bounds, settings, reserve_mm=0.):
    bounds = np.asarray(bounds, dtype=float)
    build = np.asarray(settings['printer']['build_mm'], dtype=float)
    clearance = settings['printer'].get('edge_clearance_mm', 2.)
    if bounds.shape != (2, 3) or not np.isfinite(bounds).all() or np.any(bounds[1] < bounds[0]):
        return False
    xy_limit = build[:2] / 2 - clearance
    return bool(np.all(bounds[0, :2] - reserve_mm >= -xy_limit - 1e-8)
                and np.all(bounds[1, :2] + reserve_mm <= xy_limit + 1e-8)
                and bounds[0, 2] >= -1e-8 and bounds[1, 2] <= build[2] + 1e-8)


def envelope_overflow_mm(bounds, settings, reserve_mm=0.):
    """Per-axis millimeters by which ``bounds`` leave the usable envelope.

    Each entry is the larger of the two single-side excursions on that axis, so
    it answers "how far past the edge does this reach", not "how much total
    width is spare". Zero on an axis that fits. X and Y are measured against
    the edge-clearance-reduced half extents; Z against the plate at zero and
    the machine height. A model that does not fit is described rather than
    refused, because the number is what tells a user whether to rotate, move or
    give up.
    """
    bounds = np.asarray(bounds, dtype=float)
    build = np.asarray(settings['printer']['build_mm'], dtype=float)
    clearance = settings['printer'].get('edge_clearance_mm', 2.)
    if bounds.shape != (2, 3) or not np.isfinite(bounds).all():
        raise VoxelMillError('invalid_bounds', 'Expected finite (2, 3) bounds')
    limit = build[:2] / 2 - clearance
    low = np.maximum(0.0, (-limit - (bounds[0, :2] - reserve_mm)))
    high = np.maximum(0.0, (bounds[1, :2] + reserve_mm) - limit)
    z_low = max(0.0, -float(bounds[0, 2]))
    z_high = max(0.0, float(bounds[1, 2]) - float(build[2]))
    overflow = np.maximum(low, high)
    return [float(overflow[0]), float(overflow[1]), float(max(z_low, z_high))]


def placement_or_overflow(triangles, settings, rotation_deg=(0, 0, 0), center_offset=(0, 0),
                          lift_mm=5., cancel=None, scale=(1.0, 1.0, 1.0),
                          mirror=(False, False, False)):
    """Place a part whether or not it fits, and say by how much it does not.

    ``placement_for_triangles`` refuses an oversized part, which is correct for
    an export and useless for an editor: a model that cannot be printed as
    posed still has to be visible before anyone can rotate it. This returns
    ``(placement, fits, overflow_mm)`` and leaves the decision to the caller.
    The pose is identical to the one the raising variant would produce.
    """
    bounds = triangle_bounds(triangles, cancel=cancel)
    placement = _placement(triangles, bounds, rotation_deg, center_offset, lift_mm, cancel,
                           scale, mirror)
    reserve = _reserve(settings)
    for relaxed in (False, True):
        margin = 0. if relaxed else reserve
        if envelope_fits(placement.bounds, settings, margin):
            placement.search = {'mode': 'explicit', 'full_resolution_bounds': True,
                                'xy_support_reserve_mm': margin, 'reserve_relaxed': relaxed,
                                'nominal_xy_support_reserve_mm': reserve,
                                'requires_support_envelope_recheck': True}
            return placement, True, [0.0, 0.0, 0.0]
    overflow = envelope_overflow_mm(placement.bounds, settings)
    placement.search = {'mode': 'explicit', 'full_resolution_bounds': True,
                        'xy_support_reserve_mm': 0.0, 'reserve_relaxed': True,
                        'nominal_xy_support_reserve_mm': reserve,
                        'requires_support_envelope_recheck': True,
                        'fits': False, 'overflow_mm': overflow}
    return placement, False, overflow


def _reserve(settings):
    support = settings.get('support', {})
    pillar_r = support.get('pillar_diameter_mm', 1.2) / 2
    legacy = pillar_r + support.get('raft_expansion_mm', 2.)
    kind = support.get('base_type', 'plate')
    if kind == 'plate':
        return legacy
    if kind == 'none':
        return max(pillar_r, support.get('tip_base_diameter_mm', 0.) / 2)
    radius = support.get('base_touch_diameter_mm', 0.) / 2 or legacy
    if kind == 'skate':
        # Bounding circle covers every configured skate rotation. Final assembly
        # envelope checks remain authoritative after branches choose their feet.
        return max(radius, support.get('base_skate_length_mm', 0.) / 2)
    if kind in ('grid', 'skeleton', 'hex'):
        return max(radius, support.get('base_strut_width_mm', 0.) / 2 or pillar_r)
    return radius


def _placement(triangles, original_bounds, angles, center_offset, lift_mm, cancel,
               scale=(1.0, 1.0, 1.0), mirror=(False, False, False)):
    offset = np.asarray(center_offset, dtype=float)
    if offset.shape != (2,) or not np.isfinite(offset).all() or not np.isfinite(lift_mm) or lift_mm < 0:
        raise VoxelMillError('invalid_placement', 'Center offset must be finite and lift must be nonnegative')
    scaling, factors, flips = scale_matrix(scale, mirror)
    # Scale and mirror act on the model about its own center, then the pose
    # rotates it; centering and lifting then use the transformed bounds, so the
    # part still lands on the plate however it was resized.
    linear = rotation_matrix(angles) @ scaling
    center = original_bounds.mean(axis=0)
    matrix = np.eye(4)
    matrix[:3, :3] = linear
    matrix[:3, 3] = -linear @ center
    rotated = triangle_bounds(triangles, matrix, cancel)
    shift = np.array([*(offset - rotated.mean(axis=0)[:2]), lift_mm - rotated[0, 2]])
    matrix[:3, 3] += shift
    bounds = rotated + shift
    return Placement(matrix.tolist(), list(map(float, angles)), offset.tolist(), float(lift_mm),
                     bounds.tolist(), scale=factors.tolist(), mirror=flips.tolist())


def placement_for_triangles(triangles, settings, rotation_deg=(0, 0, 0), center_offset=(0, 0),
                            lift_mm=5., cancel=None, scale=(1.0, 1.0, 1.0),
                            mirror=(False, False, False)):
    """Place at the requested scale; reserve room for vertical feet and raft.

Scale and mirror default to the identity, so nothing is ever resized unless a
caller asks. The caller must recheck the actual boolean-unioned support/raft
envelope.
"""
    bounds = triangle_bounds(triangles, cancel=cancel)
    placement = _placement(triangles, bounds, rotation_deg, center_offset, lift_mm, cancel,
                           scale, mirror)
    reserve = _reserve(settings)
    for relaxed in (False, True):
        margin = 0. if relaxed else reserve
        if envelope_fits(placement.bounds, settings, margin):
            placement.search = {'mode': 'explicit', 'full_resolution_bounds': True,
                                'xy_support_reserve_mm': margin, 'reserve_relaxed': relaxed,
                                'nominal_xy_support_reserve_mm': reserve,
                                'requires_support_envelope_recheck': True}
            return placement
    raise VoxelMillError('no_feasible_placement', 'no feasible placement found',
                    {'bounds': placement.bounds, 'xy_support_reserve_mm': reserve})


def matrix_to_euler_deg(rotation):
    """Extrinsic X, Y, Z degrees for a rotation matrix built as Rz @ Ry @ Rx."""
    r = np.asarray(rotation, dtype=float)
    sy = float(np.clip(-r[2, 0], -1.0, 1.0))
    y = math.asin(sy)
    if abs(sy) < 1 - 1e-9:
        x = math.atan2(r[2, 1], r[2, 2])
        z = math.atan2(r[1, 0], r[0, 0])
    else:  # gimbal lock: fold the free rotation into X
        x = math.atan2(-r[1, 2], r[1, 1])
        z = 0.0
    return [math.degrees(x), math.degrees(y), math.degrees(z)]


def _orientation_matrix(up, phi):
    """Rotation mapping body direction ``up`` to world +Z, spun by ``phi``."""
    up = np.asarray(up, dtype=float)
    up = up / np.linalg.norm(up)
    helper = np.array([0., 0., 1.]) if abs(up[2]) < .9 else np.array([1., 0., 0.])
    e1 = np.cross(helper, up); e1 /= np.linalg.norm(e1)
    e2 = np.cross(up, e1)
    c, s = math.cos(phi), math.sin(phi)
    return np.stack((c * e1 + s * e2, -s * e1 + c * e2, up))


def _directions(count):
    """Deterministic Fibonacci hemisphere; antipodal pairs are redundant."""
    indices = np.arange(count) + .5
    z = 1 - indices / count
    radius = np.sqrt(np.maximum(0., 1 - z * z))
    angle = np.pi * (1 + 5 ** .5) * indices
    return np.column_stack((radius * np.cos(angle), radius * np.sin(angle), z))


def _hull_points(triangles, limit=200000):
    """Extreme points of a regular sample; a bounding box needs only the hull."""
    step = max(1, math.ceil(len(triangles) * 3 / limit))
    points = np.asarray(triangles[::step], dtype=np.float64).reshape(-1, 3)
    if not np.isfinite(points).all():
        raise VoxelMillError('nonfinite_coordinates', 'Mesh contains nonfinite coordinates')
    if len(points) < 4:
        return points
    try:
        from scipy.spatial import ConvexHull
        return points[np.unique(ConvexHull(points).vertices)]
    except Exception:
        return points


def _assessment_pitch(bounds, cells=150):
    """Coarse pitch that keeps the ranking grid near ``cells`` on its long axis."""
    extent = float(np.max(np.asarray(bounds)[1] - np.asarray(bounds)[0]))
    return max(0.5, extent / cells)


def _rank_finalists(triangles, accepted, settings, assess, cancel, progress):
    """Rank every feasible finalist and preserve the evidence for every pose.

    Missing assessments sort after assessed candidates. If none can be
    assessed, the cheap ordering stands. An open occupancy grid cannot supply
    void penalties; those terms stay visibly uncounted instead of looking zero.
    """
    from copy import deepcopy
    ranked = []
    for index, placement in enumerate(accepted):
        if cancel:
            cancel.check()
        report = _attach_assessment(triangles, placement, settings, cancel, progress) if assess else None
        if not assess:
            placement.search['assessment'] = {'status': 'not_run', 'reason': 'assessment disabled'}
        cheap = placement.search['score'][0]
        terms = deepcopy(placement.search.get('score_terms', {
            'cheap_composite': {'value': cheap, 'weight': 1., 'contribution': cheap, 'counted': True}}))
        total = None
        if report is not None:
            extent = np.asarray(placement.bounds, dtype=float)
            volume = max(float(np.prod(extent[1] - extent[0])), 1e-9)
            trapped = report['trapped_resin_mm3'] / volume
            cavity = report['enclosed_cavity_mm3'] / volume
            void_evidence = bool(report['occupancy_closed'])
            values = {'trapped': (trapped, TRAPPED_WEIGHT, void_evidence),
                      'enclosed_cavity': (cavity, CAVITY_WEIGHT, void_evidence),
                      'support_access': (1. - report['support_accessible_fraction'], ACCESS_WEIGHT, True),
                      'stability': (min(report['center_of_mass_overhang'], 4.), STABILITY_WEIGHT, True)}
            for name, (value, weight, counted) in values.items():
                terms[name] = {'value': value, 'weight': weight,
                               'contribution': value * weight if counted else None, 'counted': counted}
                if not counted:
                    terms[name]['reason'] = 'occupancy grid is not closed; void evidence is unreliable'
            total = cheap + sum(value * weight for value, weight, counted in values.values() if counted)
            placement.search['finalist_score'] = {
                'total': total, 'cheap_composite': cheap,
                'trapped_fraction': trapped, 'enclosed_fraction': cavity,
                'void_terms_counted': void_evidence,
                'inaccessible_fraction': 1. - report['support_accessible_fraction'],
                'center_of_mass_overhang': report['center_of_mass_overhang'],
                'weights': {'trapped': TRAPPED_WEIGHT, 'enclosed_cavity': CAVITY_WEIGHT,
                            'support_access': ACCESS_WEIGHT, 'stability': STABILITY_WEIGHT}}
        else:
            for name, weight in (('trapped', TRAPPED_WEIGHT), ('enclosed_cavity', CAVITY_WEIGHT),
                                 ('support_access', ACCESS_WEIGHT), ('stability', STABILITY_WEIGHT)):
                terms[name] = {'value': None, 'weight': weight, 'contribution': None,
                               'counted': False, 'reason': 'assessment not available'}
        placement.search['score_terms'] = terms
        ranked.append((total is None, cheap if total is None else total, index, placement))
    ranked.sort(key=lambda item: item[:3])
    records = []
    for rank, (missing, total, _index, placement) in enumerate(ranked, 1):
        records.append({
            'rank': rank, 'rotation_deg': list(placement.rotation_deg),
            'matrix': deepcopy(placement.matrix), 'bounds': deepcopy(placement.bounds),
            'center_offset_mm': list(placement.center_offset_mm), 'model_lift_mm': placement.model_lift_mm,
            'total': None if missing else total, 'cheap_composite': placement.search['score'][0],
            'score_terms': deepcopy(placement.search['score_terms']),
            'assessment': deepcopy(placement.search.get('assessment', {})),
            'assessment_status': 'not_run' if missing else 'complete',
            'ranking_basis': 'cheap_fallback' if missing else 'assessed',
            'full_resolution_bounds': True,
            # Snapshot before the ranked list is attached: no recursive report.
            'search': deepcopy(placement.search),
        })
    best = ranked[0][3]
    best.search.update(ranked_candidates=records, selected_rank=1,
                       finalists_considered=len(records),
                       finalist_totals=[record['total'] for record in records],
                       weight_calibration='unfitted heuristic weights; not calibrated to physical prints',
                       ranking_note='lower scores rank first; unavailable assessments follow assessed poses; '
                                    'finite search, sampled scoring, full-resolution model bounds; '
                                    'supports still need an envelope and routing check')
    return best


def select_orientation_candidate(best, rank=1):
    """Select an already measured finalist without recomputing or rounding its pose."""
    from copy import deepcopy
    candidates = best.search.get('ranked_candidates', [])
    if type(rank) is not int or not 1 <= rank <= len(candidates):
        raise VoxelMillError('invalid_candidate', f'Candidate rank must be between 1 and {len(candidates)}',
                        {'requested_rank': rank, 'available_candidates': len(candidates)})
    record = candidates[rank - 1]
    search = deepcopy(record['search'])
    for key in ('ranked_candidates', 'finalists_considered', 'finalist_totals',
                'weight_calibration', 'ranking_note'):
        search[key] = deepcopy(best.search[key])
    search['selected_rank'] = rank
    return Placement(matrix=deepcopy(record['matrix']), rotation_deg=list(record['rotation_deg']),
                     center_offset_mm=list(record['center_offset_mm']),
                     model_lift_mm=record['model_lift_mm'], bounds=deepcopy(record['bounds']), search=search)


def _attach_assessment(triangles, placement, settings, cancel, progress):
    """Assess one placement in place; a failed assessment is recorded, not fatal."""
    from .contracts import CancellationToken, no_progress
    from .validation import orientation_assessment
    cancel = cancel or CancellationToken()
    pitch = _assessment_pitch(placement.bounds)
    moved = np.concatenate([chunk for chunk in iter_transformed_triangles(
        triangles, placement.matrix, cancel=cancel)])
    try:
        report = orientation_assessment(moved, placement.bounds, settings, pitch_mm=pitch,
                                        cancel=cancel, progress=progress or no_progress)
    except VoxelMillError as error:
        if error.code == 'canceled':
            raise
        placement.search['assessment'] = {'status': 'not_run', 'reason': str(error)}
        return None
    placement.search['assessment'] = report
    placement.search['unassessed'] = ['actual_support_volume']
    return report


def rotation_separation_deg(first, second):
    """Geodesic angle between two rotations, in degrees."""
    relative = np.asarray(first)[:3, :3] @ np.asarray(second)[:3, :3].T
    cosine = (np.trace(relative) - 1.) / 2.
    return float(np.degrees(np.arccos(np.clip(cosine, -1., 1.))))


def auto_placement(triangles, settings, center_offset=(0, 0), lift_mm=5., cancel=None,
                   directions=192, spins=12, refinements=3, assess=True, finalists=5,
                   min_separation_deg=20., max_verifications=128, progress=None, candidate_rank=1):
    """Deterministic multi-resolution orientation search with a full-resolution check.

    Candidates are scored on a convex hull of a regular vertex sample, which approximates
    candidate bounding boxes, and on sampled face normals, which is not exact for
    support volume. Refinement continues from the least infeasible candidates as
    well as the feasible ones, so a part that fits only at an oblique angle is
    still found. The accepted candidate is re-measured against every original
    triangle.

    The hull score cannot see whether an orientation traps resin, seals a
    cavity, or leaves a face no pillar can reach, so the feasible finalists are
    re-ranked on a coarse occupancy grid that can. That grid may miss features
    thinner than its pitch, which is why it only orders candidates that have
    already passed the full-resolution envelope check.
    """
    if type(finalists) is not int or not 1 <= finalists <= 32:
        raise VoxelMillError('invalid_candidate', 'Candidate count must be an integer from 1 to 32')
    if type(candidate_rank) is not int or not 1 <= candidate_rank <= finalists:
        raise VoxelMillError('invalid_candidate', 'Candidate rank must be between 1 and the requested count')
    bounds = triangle_bounds(triangles, cancel=cancel)
    hull = _hull_points(triangles)
    step = max(1, math.ceil(len(triangles) / 20000))
    sample = np.asarray(triangles[::step], dtype=float)
    sample_normals = np.cross(sample[:, 1] - sample[:, 0], sample[:, 2] - sample[:, 0])
    build = np.asarray(settings['printer']['build_mm'], dtype=float)
    clearance = settings['printer'].get('edge_clearance_mm', 2.)
    reserve = _reserve(settings)
    available = np.array([build[0] - 2 * clearance - 2 * reserve,
                          build[1] - 2 * clearance - 2 * reserve, build[2]])
    evaluated = 0

    total_area = float(np.linalg.norm(sample_normals, axis=1).sum() / 2) or 1.
    plate_area = float(build[0] * build[1])

    def score(matrix):
        """Dimensionless composite; the weights are calibration starting points."""
        nonlocal evaluated
        if cancel:
            cancel.check()
        evaluated += 1
        rotated = hull @ matrix.T
        extent = rotated.max(axis=0) - rotated.min(axis=0)
        overflow = float(np.max(extent - available))
        normals = sample_normals @ matrix.T
        downward = float(np.maximum(-normals[:, 2], 0).sum() / 2)
        supported = downward / total_area
        height = float(extent[2]) / float(build[2])
        peel = float(extent[0] * extent[1]) / plate_area
        composite = (SUPPORT_WEIGHT * supported + SUPPORT_VOLUME_WEIGHT * supported * height
                     + HEIGHT_WEIGHT * height + PEEL_WEIGHT * peel)
        return overflow, (composite, supported, height, peel)

    candidates = []
    for up in _directions(directions):
        for spin in range(spins):
            matrix = _orientation_matrix(up, 2 * math.pi * spin / spins)
            overflow, rank = score(matrix)
            candidates.append((overflow, rank, matrix))
    span = math.pi / spins
    offsets = [np.array(c, dtype=float) for c in product((-1., 0., 1.), repeat=3) if any(c)]
    for _ in range(refinements):
        candidates.sort(key=lambda item: (max(item[0], 0.), item[1]))
        seeds = [entry[2] for entry in candidates[:8]]
        span /= 3
        for matrix in seeds:
            for offset in offsets:
                refined = rotation_matrix(np.degrees(offset * span)) @ matrix
                overflow, rank = score(refined)
                candidates.append((overflow, rank, refined))
    candidates.sort(key=lambda item: (max(item[0], 0.), item[1]))
    best_overflow = candidates[0][0]
    # The reserve is a conservative pre-filter over the whole bounding box, but a
    # raft only surrounds the actual feet. When nothing clears it, retry without
    # it and let the union envelope check decide; the placement says so.
    for relaxed in (False, True):
        margin = 0. if relaxed else reserve
        allowance = 2 * reserve if relaxed else 1e-9
        verified = 0
        accepted = []
        for overflow, rank, matrix in candidates:
            if overflow > allowance or verified >= max_verifications:
                continue
            verified += 1
            angles = matrix_to_euler_deg(matrix)
            # Refinement clusters around the same few seeds, so consecutive
            # feasible candidates are near-duplicates and there is nothing for
            # the assessment to choose between. Keep the finalists apart, and
            # test that before the full-resolution bounds pass, which is what
            # this loop actually spends its time on.
            if any(rotation_separation_deg(matrix, np.asarray(kept.matrix)) < min_separation_deg
                   for kept in accepted):
                continue
            placement = _placement(triangles, bounds, angles, center_offset, lift_mm, cancel)
            if not envelope_fits(placement.bounds, settings, margin):
                continue
            placement.search = {
                'mode': 'auto', 'candidate_count': evaluated, 'sample_triangle_count': len(sample),
                'hull_points': int(len(hull)), 'full_resolution_bounds': True,
                'xy_support_reserve_mm': margin, 'reserve_relaxed': relaxed,
                'nominal_xy_support_reserve_mm': reserve, 'score': list(rank),
                'score_fields': ['composite', 'downward_area_fraction', 'height_fraction',
                                 'plate_footprint_fraction'],
                'score_weights': {'downward_area': SUPPORT_WEIGHT,
                                  'support_volume': SUPPORT_VOLUME_WEIGHT,
                                  'height': HEIGHT_WEIGHT, 'peel_footprint': PEEL_WEIGHT},
                'score_terms': {
                    name: {'value': value, 'weight': weight, 'contribution': value * weight, 'counted': True}
                    for name, value, weight in (
                        ('downward_area', rank[1], SUPPORT_WEIGHT),
                        ('support_volume_proxy', rank[1] * rank[2], SUPPORT_VOLUME_WEIGHT),
                        ('height', rank[2], HEIGHT_WEIGHT), ('peel_footprint', rank[3], PEEL_WEIGHT))},
                'unassessed': ['actual_support_volume'],
                'finalist_min_separation_deg': min_separation_deg,
                'verifications_used': verified, 'verification_limit': max_verifications,
                'requires_support_envelope_recheck': True}
            accepted.append(placement)
            if len(accepted) >= max(1, finalists):
                break
        if accepted:
            best = _rank_finalists(triangles, accepted, settings, assess, cancel, progress)
            return select_orientation_candidate(best, candidate_rank)
    raise VoxelMillError('no_feasible_placement', 'no feasible placement found', {
        'candidate_count': evaluated,
        'smallest_overflow_mm': best_overflow,
        'available_xyz_mm': available.tolist(),
        'available_without_reserve_xyz_mm': (available + [2 * reserve, 2 * reserve, 0]).tolist(),
        'note': 'a finite search is not an impossibility proof; the reserve covers pillar radius and raft expansion'})


def _manifold():
    try:
        import manifold3d
        return manifold3d
    except ImportError as exc:
        raise VoxelMillError('missing_dependency', 'Install manifold3d to perform solid booleans') from exc


def mesh_to_manifold(triangles, budget=None, repair=None, cancel=None):
    """Exact vertex deduplication only; invalid topology is rejected.

Manifold requires a valid oriented solid. Its status is not a self-intersection
certificate. No voxel repair or hole insertion is performed by this function.
"""
    _check_shape(triangles)
    budget = budget or ResourceBudget()
    budget.require(len(triangles) * 600, 'indexed solid construction')
    repair = repair or {}
    cancel = cancel or CancellationToken()
    if repair.get('aggressiveness', 'conservative') not in ('none', 'conservative', 'aggressive'):
        raise VoxelMillError('invalid_repair', 'Unknown repair aggressiveness')
    # Reject zero-area input explicitly: Manifold may otherwise discard faces.
    # Scan every chunk before rejecting so a caller gets a useful total rather
    # than the size-dependent count from the first chunk only.
    findings = {
        'degenerate_triangles': {'status': 'pass', 'count': 0},
        'weld': {'status': 'not_run'},
        'manifold': {'status': 'not_run'},
        'self_intersections': {'status': 'not_run'},
    }
    degenerate_count = 0
    for chunk in iter_transformed_triangles(triangles, cancel=cancel):
        cross = np.cross(chunk[:, 1] - chunk[:, 0], chunk[:, 2] - chunk[:, 0])
        invalid = np.all(cross == 0, axis=1)
        degenerate_count += int(invalid.sum())
    findings['degenerate_triangles']['count'] = degenerate_count
    if degenerate_count:
        findings['degenerate_triangles']['status'] = 'fail'
        findings['self_intersections'] = {
            'status': 'not_run', 'reason': 'skipped because degenerate triangles were found'}
        raise VoxelMillError(
            'degenerate_triangles',
            'Input contains zero-area triangles; no triangles were silently removed',
            {'count': degenerate_count, 'findings': findings})
    try:
        from . import _native
        vertices, faces = _native.weld_mesh(triangles, lambda *args: cancel.check())
    except (ImportError, AttributeError):
        vertices, inverse = np.unique(np.asarray(triangles, dtype=np.float64).reshape(-1, 3), axis=0, return_inverse=True)
        faces = inverse.reshape(-1, 3)
    except ValueError as exc:
        findings['weld'] = {'status': 'fail', 'reason': 'native weld rejected input'}
        findings['self_intersections'] = {
            'status': 'not_run', 'reason': 'skipped because native welding rejected input'}
        raise VoxelMillError('weld_rejected', 'Native mesh welding rejected input',
                        {'error': str(exc), 'findings': findings}) from exc
    findings['weld'] = {'status': 'pass'}
    m = _manifold()
    solid = m.Manifold(m.Mesh64(np.ascontiguousarray(vertices, dtype=np.float64), np.ascontiguousarray(faces, dtype=np.uint64)))
    if solid.status() != m.Error.NoError or solid.is_empty() or not np.isfinite(solid.volume()) or solid.volume() <= 0:
        findings['manifold'] = {'status': 'fail', 'manifold_status': str(solid.status())}
        findings['self_intersections'] = {
            'status': 'not_run', 'reason': 'skipped because Manifold rejected the solid'}
        raise VoxelMillError(
            'invalid_solid',
            'Input is not an accepted positively oriented Manifold solid; choose explicit aggressive voxel repair for invalid input',
            {'manifold_status': str(solid.status()),
             'repair_aggressiveness': repair.get('aggressiveness', 'conservative'),
             'findings': findings})
    findings['manifold'] = {'status': 'pass'}
    intersections = _native.inspect_intersections(np.asarray(triangles, dtype=np.float32),
                                                   lambda *args: cancel.check())
    findings['self_intersections'] = {
        'status': 'fail' if intersections['self_intersections'] else 'pass',
        'count': int(intersections['self_intersections']),
    }
    if intersections['self_intersections']:
        details = dict(intersections)
        details['findings'] = findings
        raise VoxelMillError('self_intersections', 'Input contains intersecting surfaces; choose explicit aggressive repair', details)
    return solid, {
        'operation': 'exact_vertex_deduplication', 'max_displacement_mm': 0.,
        'added_volume_mm3': 0., 'removed_volume_mm3': 0.,
        'self_intersections': 'checked_none', 'volumetric_repair': 'not_run',
        'findings': findings}


def manifold_triangles(solid):
    mesh = solid.to_mesh64()
    return np.asarray(mesh.vert_properties)[:, :3][np.asarray(mesh.tri_verts)]


def cylinder_between(start, end, radius_start, radius_end=None, segments=24):
    start, end = np.asarray(start, dtype=float), np.asarray(end, dtype=float)
    radius_end = radius_start if radius_end is None else radius_end
    if start.shape != (3,) or end.shape != (3,) or not np.isfinite([*start, *end, radius_start, radius_end]).all() or radius_start <= 0 or radius_end < 0:
        raise VoxelMillError('invalid_support', 'Support endpoints and radii must be finite and radii nonnegative')
    axis = end - start
    height = np.linalg.norm(axis)
    if height <= 1e-9 or segments < 8:
        raise VoxelMillError('invalid_support', 'Support must have positive length and at least eight circular segments')
    z = axis / height
    reference = np.array([0., 1., 0.]) if abs(z[1]) < .9 else np.array([1., 0., 0.])
    x = np.cross(reference, z); x /= np.linalg.norm(x)
    y = np.cross(z, x)
    transform = np.column_stack((x, y, z, start))
    return _manifold().Manifold.cylinder(float(height), float(radius_start), float(radius_end), int(segments)).transform(transform)


def raft_from_feet(feet_xy, pillar_radius=.6, expansion_mm=2., thickness_mm=1.,
                   bevel_mm=.25, slope_deg=30.0):
    """Connected convex raft with an outer putty-knife bevel, no hidden cavities.

    ``slope_deg`` is the wall angle from the plate on the outer perimeter only:
    the plate contact is the full hull, the top is inset by
    ``thickness / tan(slope)``. ``0`` keeps a near-vertical wall with a tiny
    top chamfer (``bevel_mm``). Circular foot polygons are inscribed so plate
    reserve remains conservative up to 1/cos(pi/32).
    """
    import math
    feet = np.asarray(feet_xy, dtype=float)
    if feet.ndim != 2 or feet.shape[1] != 2 or len(feet) == 0 or not np.isfinite(feet).all():
        raise VoxelMillError('invalid_raft', 'Raft needs finite foot XY coordinates')
    values = np.asarray([pillar_radius, expansion_mm, thickness_mm, bevel_mm, slope_deg])
    if not np.isfinite(values).all() or pillar_radius <= 0 or expansion_mm < 0 or thickness_mm <= 0:
        raise VoxelMillError('invalid_raft', 'Invalid raft radius, expansion, thickness, or slope')
    reserve = pillar_radius + expansion_mm
    angles = np.arange(32) * (2 * np.pi / 32)
    unit = np.column_stack((np.cos(angles), np.sin(angles)))
    outer = (feet[:, None, :] + unit[None] * reserve).reshape(-1, 2)
    if slope_deg:
        if not 0 < slope_deg < 90:
            raise VoxelMillError('invalid_raft', 'Raft slope must be between 0 and 90 exclusive, or 0')
        inset = thickness_mm / math.tan(math.radians(float(slope_deg)))
        if inset >= reserve:
            raise VoxelMillError('invalid_raft',
                            'Raft slope eats the plate contact before reaching the configured thickness',
                            {'inset_mm': inset, 'reserve_mm': reserve, 'thickness_mm': thickness_mm})
        top = (feet[:, None, :] + unit[None] * (reserve - inset)).reshape(-1, 2)
        points = np.concatenate((np.column_stack((outer, np.zeros(len(outer)))),
                                 np.column_stack((top, np.full(len(top), thickness_mm)))))
    else:
        if not 0 <= bevel_mm < min(thickness_mm, reserve):
            raise VoxelMillError('invalid_raft', 'Invalid raft radius, expansion, thickness, or bevel')
        top = (feet[:, None, :] + unit[None] * (reserve - bevel_mm)).reshape(-1, 2)
        points = np.concatenate((np.column_stack((outer, np.zeros(len(outer)))),
                                 np.column_stack((outer, np.full(len(outer), thickness_mm - bevel_mm))),
                                 np.column_stack((top, np.full(len(top), thickness_mm)))))
    return _manifold().Manifold.hull_points(points)


def fill_enclosed_cavities(solid):
    """Fill inward closed shell components of an already valid solid.

Connected narrow necks, drainage bottlenecks, and transient cups need separate
raster analysis and are not repaired here.
"""
    m = _manifold()
    fills = []
    for component in solid.decompose():
        if component.volume() < 0:
            mesh = component.to_mesh64()
            faces = np.asarray(mesh.tri_verts)[:, ::-1].copy()
            fills.append(m.Manifold(m.Mesh64(np.array(mesh.vert_properties, copy=True), faces)))
    before = solid.volume()
    for fill in fills:
        solid = solid + fill
    if solid.status() != m.Error.NoError:
        raise VoxelMillError('cavity_fill_failed', 'Closed-shell cavity fill produced an invalid solid')
    return solid, {'operation': 'fill_enclosed_shells', 'filled_shell_count': len(fills),
                   'added_volume_mm3': max(0., solid.volume() - before), 'removed_volume_mm3': 0.,
                   'drainage_bottlenecks': 'not_checked', 'transient_cups': 'not_checked'}
