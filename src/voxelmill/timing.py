"""Uncalibrated print-time estimation from the exposure and motion schedule.

Sums configured per-layer exposure, process waits, and motion travel times.
Motion units and firmware delays are unverified; the result is labeled
uncalibrated and never invents overhead the profile does not carry.
"""
from __future__ import annotations

import math

from .config import layer_exposure
from .contracts import VoxelMillError

# Bottom vs normal motion stage names: (height_key, speed_key) without prefix.
_MOTION_STAGES = (
    ('lift_height', 'lift_speed'),
    ('lift_height2', 'lift_speed2'),
    ('retract_height', 'retract_speed'),
    ('retract_height2', 'retract_speed2'),
)

_MOTION_TRAVEL_KEYS = tuple(
    f'{prefix}{name}'
    for prefix in ('bottom_', '')
    for height_key, speed_key in _MOTION_STAGES
    for name in (height_key, speed_key)
)

_PROCESS_WAITS = (
    'settle_before_exposure_s',
    'rest_after_exposure_s',
    'wait_after_lift_s',
)


def _require_motion(motion):
    missing = [key for key in _MOTION_TRAVEL_KEYS if key not in motion]
    if missing:
        # Same refusal as GOO export: never invent a travel term.
        raise VoxelMillError(
            'goo_motion',
            'The printer profile must supply every machine motion value used by GOO; '
            'nothing here is invented',
            {'missing': missing, 'section': 'printer.motion'})


def _travel_s(height, speed, field, zero_terms):
    """height/speed seconds; a zero height contributes 0 without consulting speed."""
    height = float(height)
    speed = float(speed)
    if not math.isfinite(height) or not math.isfinite(speed) or height < 0 or speed < 0:
        raise VoxelMillError('timing_motion', f'printer.motion.{field} must be finite and nonnegative')
    if height == 0.0:
        zero_terms.append(field)
        return 0.0
    if speed == 0.0:
        raise VoxelMillError(
            'timing_motion',
            f'printer.motion.{field} has nonzero height with zero speed; refusing to invent travel time',
            {'field': field, 'height': height, 'speed': speed})
    return height / speed


def _layer_motion_s(motion, bottom, zero_terms):
    prefix = 'bottom_' if bottom else ''
    total = 0.0
    for height_key, speed_key in _MOTION_STAGES:
        field = f'{prefix}{height_key}'
        speed_field = f'{prefix}{speed_key}'
        total += _travel_s(motion[field], motion[speed_field], field, zero_terms)
    return total


def _layer_wait_s(process, bottom):
    prefix = 'bottom_' if bottom else 'normal_'
    total = 0.0
    for suffix in _PROCESS_WAITS:
        key = f'{prefix}{suffix}'
        if key not in process:
            raise VoxelMillError('timing_process', f'process.{key} is required for print-time estimation')
        value = float(process[key])
        if not math.isfinite(value) or value < 0:
            raise VoxelMillError('timing_process', f'process.{key} must be a finite nonnegative number')
        total += value
    return total


def estimate_print_time_s(settings, layer_count):
    """Sum the configured exposure + wait + motion schedule; ceil to whole seconds.

    Returns a dict with ``seconds``, ``basis``, ``uncalibrated``, ``establishes``,
    and ``does_not_establish``. A zero ``layer_count`` yields ``seconds`` 0.
    Zero motion heights contribute 0 s and are listed under ``zero_motion_terms``.
    """
    if type(layer_count) is not int or layer_count < 0:
        raise VoxelMillError('timing_layers', 'layer_count must be a nonnegative integer')
    process = settings['process']
    motion = settings.get('printer', {}).get('motion') or {}
    bottom_layers = int(process['bottom_layers'])
    zero_terms = []
    # Deduplicate notes: each zero-height field is recorded once even if many layers use it.
    seen_zero = set()

    def note_zeros(batch):
        for field in batch:
            if field not in seen_zero:
                seen_zero.add(field)
                zero_terms.append(field)

    total = 0.0
    if layer_count == 0:
        basis = (
            'no layers; schedule sum is 0 s. Would use layer_exposure plus '
            'bottom_/normal_ settle/rest/wait process keys and '
            'bottom_/normal lift/retract height÷speed (including *2 stages); '
            'zero-height motion terms contribute 0 s; no invented firmware delays'
        )
        return {
            'seconds': 0,
            'basis': basis,
            'uncalibrated': True,
            'zero_motion_terms': [],
            'layer_count': 0,
            'establishes': [
                'that the configured exposure, process waits and motion travel times sum to this schedule',
            ],
            'does_not_establish': [
                'firmware-calibrated machine duration',
                'tilt-release or data-loading delays absent from the profile',
                'that printer.motion units match physical millimeters and seconds',
            ],
        }

    _require_motion(motion)
    # Precompute bottom vs normal motion once; exposure still varies per layer.
    bottom_zeros, normal_zeros = [], []
    bottom_motion = _layer_motion_s(motion, True, bottom_zeros)
    normal_motion = _layer_motion_s(motion, False, normal_zeros)
    bottom_waits = _layer_wait_s(process, True)
    normal_waits = _layer_wait_s(process, False)
    note_zeros(bottom_zeros)
    note_zeros(normal_zeros)

    for index in range(layer_count):
        bottom = index < bottom_layers
        total += float(layer_exposure(settings, index))
        total += bottom_waits if bottom else normal_waits
        total += bottom_motion if bottom else normal_motion

    seconds = int(math.ceil(total)) if total > 0 else 0
    zero_note = (
        f'; zero-height motion fields treated as 0 s: {", ".join(zero_terms)}'
        if zero_terms else '; no zero-height motion fields'
    )
    basis = (
        'sum over layers of layer_exposure(settings, i) + '
        'bottom_/normal_settle_before_exposure_s + bottom_/normal_rest_after_exposure_s + '
        'bottom_/normal_wait_after_lift_s + '
        '(bottom_)lift_height/speed + (bottom_)lift_height2/speed2 + '
        '(bottom_)retract_height/speed + (bottom_)retract_height2/speed2'
        f'{zero_note}; no invented firmware delays; ceil to whole seconds'
    )
    return {
        'seconds': seconds,
        'basis': basis,
        'uncalibrated': True,
        'zero_motion_terms': list(zero_terms),
        'layer_count': layer_count,
        'raw_seconds': total,
        'establishes': [
            'that the configured exposure, process waits and motion travel times sum to this schedule',
        ],
        'does_not_establish': [
            'firmware-calibrated machine duration',
            'tilt-release or data-loading delays absent from the profile',
            'that printer.motion units match physical millimeters and seconds',
        ],
    }
