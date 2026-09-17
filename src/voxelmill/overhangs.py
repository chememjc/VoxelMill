"""Downward-face samples that no routed contact reaches.

This compares two things the pipeline already computes separately: the samples
``supports.downward_contacts`` says need support, and the contacts routing
actually placed. A sample counts as reached when a contact lies within
``support.spacing_mm`` of it, which is the same distance the sampler used to
space those samples in the first place.

The finding is advisory. A gap means no pillar was placed within reach of that
sample; it does not prove the part will fail, and a covered sample does not
prove the pillar is strong enough. Both are geometry, not mechanics. What it is
good for is telling the difference between a plate that was routed and a plate
whose overhangs were quietly left alone, which is a question about orientation
and support density rather than about slicing.
"""
from __future__ import annotations

import math

import numpy as np

from .contracts import CancellationToken, VoxelMillError, Diagnostic, no_progress
from .supports import downward_contacts

# Matches the ``max_examples`` cap ``validation.analyze_layers`` uses. A mesh
# with a million unsupported samples must not produce a million diagnostics;
# the count in the metrics stays the true one either way.
MAX_EXAMPLES = 128


def analyze_overhangs(triangles, settings, contacts, *, budget=None, cancel=None,
                      progress=no_progress):
    """Downward samples farther than ``support.spacing_mm`` from any contact.

    ``triangles`` are placed model triangles and ``contacts`` an ``(N, 3)``
    array of routed contact positions, both in plate coordinates. A sample
    sitting within one layer height of the plate is not reported: it is already
    resting on the build plate, and nothing can be routed under it.
    """
    cancel = cancel or CancellationToken()
    cancel.check()
    support = settings['support']
    spacing = float(support['spacing_mm'])
    layer_height = float(settings['process']['layer_height_mm'])
    if not math.isfinite(spacing) or spacing <= 0 or not math.isfinite(layer_height) or layer_height <= 0:
        raise VoxelMillError('overhang_config',
                             'support.spacing_mm and process.layer_height_mm must be positive and finite')
    triangles = np.asarray(triangles)
    if triangles.ndim != 3 or triangles.shape[1:] != (3, 3):
        raise VoxelMillError('overhang_input', 'triangles must have shape (N, 3, 3)')
    contacts = np.asarray(contacts, dtype=float)
    contacts = contacts.reshape(-1, 3) if contacts.size else np.empty((0, 3))
    if len(contacts) and not np.isfinite(contacts).all():
        raise VoxelMillError('overhang_input', 'contacts contain nonfinite coordinates')
    common = {
        'status': 'complete',
        'spacing_mm': spacing,
        'overhang_angle_deg': float(support['overhang_angle_deg']),
        'layer_height_mm': layer_height,
        'contact_count': int(len(contacts)),
        'basis': 'downward-face samples at the configured overhang angle, matched to routed '
                 'contacts by distance; plate-height samples are excluded',
        'does_not_establish': 'that a covered sample is strong enough, or that an uncovered one fails',
    }
    # The same sampler the router consumes, so a gap here is a gap the router
    # itself saw and did not fill, never a difference of definition.
    samples, area = downward_contacts(triangles, settings, cancel=cancel, progress=progress)
    samples = np.asarray(samples, dtype=float).reshape(-1, 3)
    common['downward_area_mm2'] = float(area)
    common['sample_count'] = int(len(samples))
    if not len(samples):
        return {**common, 'unsupported_count': 0, 'unsupported_fraction': 0.0,
                'on_plate_count': 0, 'max_gap_mm': None, 'samples': [],
                'samples_truncated': False, 'diagnostic_examples_capped': MAX_EXAMPLES}
    cancel.check()
    if budget is not None:
        budget.require(len(samples) * 96 + len(contacts) * 96 + 64 * 1024**2, 'overhang coverage')
    # Resting on the plate is support; there is no room under it for a pillar.
    on_plate = samples[:, 2] <= layer_height + 1e-9
    if len(contacts):
        # Unbounded query, as ``supports.contact_coverage`` does, so the gap
        # reported for an uncovered sample is the real distance rather than
        # the search radius it exceeded.
        from scipy.spatial import cKDTree
        distance = cKDTree(contacts).query(samples)[0]
    else:
        distance = np.full(len(samples), np.inf)
    reached = distance <= spacing + 1e-9
    unsupported = np.flatnonzero(~reached & ~on_plate)
    cancel.check()
    # Worst first, so a truncated list still shows the widest gaps. With no
    # contacts every distance is infinite and the sample order decides.
    ranked = unsupported[np.lexsort((unsupported, -distance[unsupported]))]
    examples = []
    for index in ranked[:MAX_EXAMPLES]:
        point = samples[index]
        gap = float(distance[index])
        examples.append({
            'position_mm': [float(point[0]), float(point[1]), float(point[2])],
            'layer': max(0, int(math.floor(float(point[2]) / layer_height))),
            'gap_mm': None if not math.isfinite(gap) else gap,
        })
    finite = distance[np.isfinite(distance)]
    return {**common,
            'unsupported_count': int(len(unsupported)),
            'unsupported_fraction': float(len(unsupported)) / len(samples),
            'on_plate_count': int(np.count_nonzero(on_plate)),
            'max_gap_mm': float(finite.max()) if len(finite) else None,
            'samples': examples,
            'samples_truncated': len(unsupported) > len(examples),
            'diagnostic_examples_capped': MAX_EXAMPLES}


def apply_overhang_check(report, triangles, settings, contacts, **kwargs):
    """Attach the coverage evidence; analysis that did not run cannot pass."""
    try:
        result = analyze_overhangs(triangles, settings, contacts, **kwargs)
    except VoxelMillError as error:
        if error.code == 'canceled':
            raise
        result = {'status': 'not_run', 'reason': str(error), 'error_code': error.code,
                  'error_details': error.details, 'samples': [], 'unsupported_count': 0,
                  'sample_count': 0}
    metrics = report.metrics if hasattr(report, 'metrics') else report.setdefault('metrics', {})
    checks = report.checks if hasattr(report, 'checks') else report.setdefault('checks', {})
    metrics['unsupported_overhangs'] = result
    checks['unsupported_overhangs'] = ('not_run' if result['status'] != 'complete' else
                                       'warn' if result['unsupported_count'] else 'pass')
    if checks['unsupported_overhangs'] != 'warn':
        return report
    for sample in result['samples']:
        diagnostic = Diagnostic(
            'unsupported_overhang',
            'No routed contact lies within the support spacing of this downward sample; '
            'review orientation and support density',
            severity='warning', layer=sample['layer'], position_mm=sample['position_mm'],
            details={'gap_mm': sample['gap_mm'], 'spacing_mm': result['spacing_mm'],
                     'unsupported_count': result['unsupported_count'],
                     'sample_count': result['sample_count'],
                     'samples_truncated': result['samples_truncated']})
        if hasattr(report, 'diagnostics'):
            report.diagnostics.append(diagnostic)
        else:
            report.setdefault('diagnostics', []).append({
                'code': diagnostic.code, 'message': diagnostic.message,
                'severity': diagnostic.severity, 'layer': diagnostic.layer,
                'position_mm': diagnostic.position_mm, 'details': diagnostic.details})
    return report
