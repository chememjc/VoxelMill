"""Portable per-contact support parameters in final plate coordinates."""
from copy import deepcopy

import numpy as np

from .config import resolve_settings
from .contracts import VoxelMillError

PERSONAL_FIELDS = frozenset({
    'contact_diameter_mm', 'tip_base_diameter_mm', 'tip_length_mm', 'min_tip_length_mm',
    'penetration_mm', 'pillar_diameter_mm', 'pillar_angle_deg', 'support_clearance_mm',
    'max_slenderness', 'tip_shape', 'break_point_diameter_mm',
    'model_anchor_shape', 'model_anchor_diameter_mm', 'model_anchor_length_mm',
    'model_anchor_penetration_mm', 'small_pillar_mode', 'small_pillar_shape',
    'small_pillar_diameter_mm', 'small_pillar_max_length_mm',
    'small_pillar_upper_depth_mm', 'small_pillar_lower_depth_mm',
})


def contact_key(point):
    try:
        values = np.asarray(point, dtype=float)
    except (TypeError, ValueError):
        raise VoxelMillError('invalid_contact_parameters', 'Contact position needs three finite coordinates') from None
    if values.shape != (3,) or not np.isfinite(values).all():
        raise VoxelMillError('invalid_contact_parameters', 'Contact position needs three finite coordinates')
    # Stable through GUI/STL float conversions without attaching an override to
    # an arbitrary nearby contact. Duplicate keys are rejected, never merged.
    return tuple(round(float(value), 6) for value in values)


class ContactParameters(list):
    """JSON-compatible records with a derived O(1) routing lookup."""
    def __init__(self, records):
        super().__init__(records)
        self.by_position = {contact_key(row['position_mm']): row['parameters'] for row in records}


def normalize_contact_parameters(records, settings):
    if not isinstance(records, (list, tuple)):
        raise VoxelMillError('invalid_contact_parameters', 'Contact parameters must be an array')
    result, seen = [], set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {'position_mm', 'parameters'}:
            raise VoxelMillError('invalid_contact_parameters', 'Each override needs position_mm and parameters')
        key = contact_key(record['position_mm'])
        if key in seen:
            raise VoxelMillError('invalid_contact_parameters', 'Duplicate contact parameter positions')
        seen.add(key)
        parameters = record['parameters']
        if not isinstance(parameters, dict) or set(parameters) - PERSONAL_FIELDS:
            raise VoxelMillError('invalid_contact_parameters', 'Only individual support geometry parameters may be overridden',
                            {'allowed_fields': sorted(PERSONAL_FIELDS)})
        merged = deepcopy(settings)
        merged.setdefault('support', {}).update(deepcopy(parameters))
        resolve_settings(overrides=merged)  # Includes cross-field geometry constraints.
        result.append({'position_mm': [float(v) for v in record['position_mm']],
                       'parameters': deepcopy(parameters)})
    return ContactParameters(result)


def parameters_for_contact(records, point):
    if isinstance(records, ContactParameters):
        return deepcopy(records.by_position.get(contact_key(point), {}))
    key = contact_key(point)
    matches = [row['parameters'] for row in records if contact_key(row['position_mm']) == key]
    if len(matches) > 1:
        raise VoxelMillError('invalid_contact_parameters', 'Ambiguous contact parameters')
    return deepcopy(matches[0]) if matches else {}
