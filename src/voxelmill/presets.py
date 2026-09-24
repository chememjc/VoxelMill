"""Portable named support and process presets.

Presets contain one settings section only (support or process).  They are
deliberately independent of printer and resin discovery: callers choose a
preset by its built-in name or by passing an explicit JSON path.
"""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
import json
import os
from pathlib import Path
import tempfile
from typing import NoReturn

from .config import DEFAULTS, validate_settings
from .contracts import VoxelMillError
from .versioning import CURRENT_VERSIONS, upgrade


PRESET_SCHEMA_VERSION = CURRENT_VERSIONS['preset']
_SUPPORT = 'support'
_PROCESS = 'process'

_BUILTIN_SUPPORT = {
    # These are geometric starting points.  The remaining support values
    # inherit the defaults when the preset is applied.
    'light': {'spacing_mm': 5.0, 'pillar_diameter_mm': 0.9},
    # Medium is the resolved default support configuration.
    'medium': deepcopy(DEFAULTS['support']),
    'heavy': {'spacing_mm': 2.0, 'pillar_diameter_mm': 1.6},
    # The configuration known to print these parts on this machine, transcribed
    # from CHITUBOX and recorded in docs/support-presets.md. Every value below
    # appears in that table or has an explicit derivation; gaps are called out
    # in CHITUBOX_UNSET.
    #
    # Reading the table needs one piece of care. Its Raft tab says
    # "Raft Shape: None" while its Bottom tab says "Platform Touch Shape:
    # Skate, Touch Diameter 10.00, Thickness 0.80". Those are two different
    # things in CHITUBOX: the raft is the slab over every foot, the platform
    # touch shape is the foot each individual support lands on. So the print
    # uses no slab and a 10 mm skate per foot, which is base_type = 'skate'
    # here, not 'none'. ('none' is bare pillars with no foot at all.) The old
    # plan2.md summary line said "including base = none", meaning the raft.
    #
    # Grayed raft defaults include Thickness 1.00 mm and Slope 30°. With the
    # raft disabled those values do not apply as a slab, but the 30° putty-knife
    # taper maps onto base_edge_slope_deg (angle from the plate, matching
    # CHITUBOX's raft-slope definition). Elongation is still unknown, so
    # base_skate_length_mm stays 0 and the foot is a circular frustum.
    'chitubox-mars5': {
        'penetration_mm': 0.30,          # Top / Contact Depth
        'contact_diameter_mm': 0.30,     # Top / Tip Upper Diameter
        'tip_base_diameter_mm': 0.80,    # Top / Tip Down Diameter
        'tip_length_mm': 2.00,           # Top / Connection Length
        # CHITUBOX states no shortest-tip value. The resolved default pairs a
        # 0.15 mm penetration with a 0.30 mm floor, so the floor is twice the
        # penetration; 0.60 keeps that ratio against this 0.30 penetration
        # rather than picking a number. The validator requires it to exceed
        # the penetration, so it cannot simply be left at the default.
        'min_tip_length_mm': 0.60,
        'pillar_diameter_mm': 0.80,      # Middle / Diameter
        # Reference angle is from the vertical top; ours is from horizontal.
        'pillar_angle_deg': 20.0,        # 90 - Middle / Angle (70)
        'small_pillar_mode': 'model',
        'small_pillar_shape': 'cone',
        'small_pillar_diameter_mm': 0.40,
        'small_pillar_upper_depth_mm': 0.25,
        'small_pillar_lower_depth_mm': 0.25,
        'small_pillar_max_length_mm': 0.0,  # missing reference threshold: disabled
        'model_anchor_penetration_mm': 0.20,  # Bottom / Contact Depth
        'brace_spacing_mm': 15.0,        # vertical spacing from each support shoulder
        'brace_max_length_mm': 30.0,     # complete downward diagonal length
        'base_type': 'skate',            # Bottom / Platform Touch Shape = Skate
        'base_touch_diameter_mm': 10.0,  # Bottom / Touch Diameter
        'base_thickness_mm': 0.80,       # Bottom / Thickness
        'base_skate_length_mm': 0.0,     # unknown elongation: circular foot
        'base_edge_slope_deg': 30.0,     # grayed Raft / Slope (from the plate)
    },
}

#: What the CHITUBOX table does not fully determine for this router.
CHITUBOX_UNSET = {
    'small_pillar_max_length_mm': (
        'the table omits the maximum whole model-to-model length. The 0.40 mm '
        'diameter, cone ends and 0.25 mm depths are recorded, but this mode '
        'stays disabled until a maximum length is supplied.'),
    'spacing_mm': (
        'the table records no support spacing, and spacing decides how much the '
        '10 mm skate feet overlap: at the resolved default of 3 mm they merge '
        'into something close to a slab, which is not what the reference prints.'),
    'base_skate_length_mm': (
        'the table gives no skate elongation. base_skate_length_mm=0 keeps a '
        'circular 10 mm frustum; that is an explicit unset, not a measured '
        'reproduction of the reference skate.'),
    'model_anchor_diameter_mm': (
        'the table gives 0.40 mm and Contact Shape None, but no bottom '
        'transition length. The independent connector requires a length; '
        'its diameter remains derived instead of inventing that transition.'),
}

CHITUBOX_SOURCE = ('https://docs.chitubox.com/en-US/chitubox-basic/v1.9.5/'
                   'setting-up/configure-support-parameters')

_PROCESS_UNCALIBRATED = (
    'Uncalibrated starting point. Layer height and bottom-layer count are '
    'geometric convenience values only; exposure, rest and settle times are '
    'not tuned for a specific resin or printer.')


def _fine_layer_height_mm(printer: Mapping) -> float:
    lo, hi = printer['layer_height_range_mm']
    return 0.03 if lo <= 0.03 <= hi else 0.05


def _fast_bottom_layers() -> int:
    # One step below the resolved default when that remains a valid integer.
    candidate = DEFAULTS['process']['bottom_layers'] - 1
    return candidate if type(candidate) is int and candidate >= 0 else DEFAULTS['process']['bottom_layers']


def _builtin_process_tables():
    return {
        'fine': {'layer_height_mm': _fine_layer_height_mm(DEFAULTS['printer'])},
        'default': deepcopy(DEFAULTS['process']),
        'fast': {
            'layer_height_mm': 0.1,
            'bottom_layers': _fast_bottom_layers(),
        },
    }


def list_presets() -> tuple[str, ...]:
    """Return the names of the built-in support presets in display order."""
    return tuple(_BUILTIN_SUPPORT)


def list_process_presets() -> tuple[str, ...]:
    """Return the names of the built-in process presets in display order."""
    return tuple(_builtin_process_tables())


def _fail(message) -> NoReturn:
    raise VoxelMillError('invalid_preset', message)


def _reject_constant(value):
    _fail(f'Nonfinite JSON value: {value}')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _preset_keys(section: str):
    return frozenset(('schema_version', 'name', section, 'notes'))


def _validate_document(value, section: str = _SUPPORT):
    if section not in (_SUPPORT, _PROCESS):
        _fail(f'Unknown preset section: {section}')
    if not isinstance(value, Mapping):
        _fail('Preset must be a JSON object')
    data = dict(deepcopy(value))
    unknown = set(data) - _preset_keys(section)
    if unknown:
        _fail(f'Unknown preset fields: {sorted(unknown)}')
    if not {'schema_version', 'name', section} <= set(data):
        _fail(f'Preset requires schema_version, name, and {section}')
    if 'notes' in data and (not isinstance(data['notes'], list) or
                          any(not isinstance(note, str) for note in data['notes'])):
        _fail('Preset notes must be an array of strings')
    data = upgrade('preset', data, code='invalid_preset')
    if not isinstance(data['name'], str) or not data['name'].strip():
        _fail('Preset name must be a nonempty string')
    data['name'] = data['name'].strip()
    if not isinstance(data[section], Mapping):
        _fail(f'Preset {section} must be an object')
    data[section] = dict(data[section])

    # validate_settings is the authoritative validator.  Merge the partial
    # preset over a complete defaults copy so omitted section keys are valid.
    probe = deepcopy(DEFAULTS)
    probe[section].update(deepcopy(data[section]))
    try:
        validate_settings(probe)
    except VoxelMillError as exc:
        raise VoxelMillError('invalid_preset', str(exc)) from exc
    return data


def _builtin_support(name: str):
    if not isinstance(name, str) or name.lower() not in _BUILTIN_SUPPORT:
        return None
    canonical = name.lower()
    document = {
        'schema_version': PRESET_SCHEMA_VERSION,
        'name': canonical,
        'support': deepcopy(_BUILTIN_SUPPORT[canonical]),
    }
    if canonical == 'chitubox-mars5':
        document['notes'] = [
            'Partial transcription, not equivalent print settings. Parameter definitions: ' + CHITUBOX_SOURCE,
            '70 degrees from the vertical top maps to 20 degrees from horizontal.',
            'Top Touch Shape None adds no contact bead; the top connector is a cone. '
            'Middle is a cylinder. Bottom Contact Point 1 matches one model anchor per route.',
            'Raft Shape None excludes the disabled raft-tab defaults. Per-foot skate '
            'feet remain enabled (circular frustum at 10 mm / 0.80 mm / 30° from the plate).',
            'min_tip_length_mm=0.60 is a voxelmill guardrail derived as twice the '
            '0.30 mm top penetration, not a measured CHITUBOX shortest-tip setting.',
            *[f'{key}: {reason}' for key, reason in CHITUBOX_UNSET.items()],
        ]
    return _validate_document(document, _SUPPORT)


def _builtin_process(name: str):
    tables = _builtin_process_tables()
    if not isinstance(name, str) or name.lower() not in tables:
        return None
    canonical = name.lower()
    document = {
        'schema_version': PRESET_SCHEMA_VERSION,
        'name': canonical,
        'process': deepcopy(tables[canonical]),
        'notes': [_PROCESS_UNCALIBRATED],
    }
    return _validate_document(document, _PROCESS)


def _read_json(path: Path, section: str):
    try:
        if path.stat().st_size > 1024 * 1024:
            _fail('Preset exceeds 1 MiB limit')
        with path.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
    except VoxelMillError:
        raise
    except (OSError, ValueError) as exc:
        raise VoxelMillError('invalid_preset', f'Cannot read preset {path}: {exc}') from exc
    if len(raw) > 1024 * 1024:
        _fail('Preset exceeds 1 MiB limit')
    try:
        data = json.loads(raw, parse_constant=_reject_constant,
                          object_pairs_hook=_unique_object)
        # This also rejects exponent overflow (for example, 1e999).
        json.dumps(data, allow_nan=False)
    except VoxelMillError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise VoxelMillError('invalid_preset', f'Invalid preset JSON: {exc}') from exc
    return _validate_document(data, section)


def load_preset(source: str | Path) -> dict:
    """Load a built-in support preset name or an explicit JSON file path.

    The returned dictionary is independent and safe for callers to modify.
    Built-in names are case-insensitive; any other string is interpreted as a
    path, with no implicit user profile search.
    """
    builtin = _builtin_support(source) if isinstance(source, str) else None
    if builtin is not None:
        return builtin
    if not isinstance(source, (str, Path)):
        _fail('Preset source must be a built-in name or JSON path')
    return _read_json(Path(source), _SUPPORT)


def load_process_preset(source: str | Path) -> dict:
    """Load a built-in process preset name or an explicit JSON file path.

    Same path rules as :func:`load_preset`.  Process documents carry a
    ``process`` table only (no printer hardware).
    """
    builtin = _builtin_process(source) if isinstance(source, str) else None
    if builtin is not None:
        return builtin
    if not isinstance(source, (str, Path)):
        _fail('Preset source must be a built-in name or JSON path')
    return _read_json(Path(source), _PROCESS)


def _coerce_preset(preset, section: str) -> dict:
    if isinstance(preset, (str, Path)):
        return load_preset(preset) if section == _SUPPORT else load_process_preset(preset)
    return _validate_document(preset, section)


def apply_preset(settings: Mapping, preset) -> dict:
    """Return settings with a validated preset overlaid on ``support``.

    Every unrelated settings section is copied unchanged.  The input settings
    and preset are never mutated.
    """
    if not isinstance(settings, Mapping):
        _fail('Settings must be a dictionary')
    document = _coerce_preset(preset, _SUPPORT)
    result = deepcopy(dict(settings))
    if not isinstance(result.get('support'), Mapping):
        _fail('Settings support must be an object')
    result['support'] = dict(result['support'])
    result['support'].update(deepcopy(document['support']))
    try:
        return validate_settings(result)
    except VoxelMillError as exc:
        raise VoxelMillError('invalid_preset', str(exc)) from exc


def apply_process_preset(settings: Mapping, preset) -> dict:
    """Return settings with a validated preset overlaid on ``process``.

    Every unrelated settings section is copied unchanged.  The built-in
    ``fine`` preset resolves ``layer_height_mm`` against the target printer
    range (0.03 when allowed, otherwise 0.05).
    """
    if not isinstance(settings, Mapping):
        _fail('Settings must be a dictionary')
    document = _coerce_preset(preset, _PROCESS)
    result = deepcopy(dict(settings))
    if not isinstance(result.get('process'), Mapping):
        _fail('Settings process must be an object')
    if not isinstance(result.get('printer'), Mapping):
        _fail('Settings printer must be an object')
    result['process'] = dict(result['process'])
    result['process'].update(deepcopy(document['process']))
    if document['name'] == 'fine':
        result['process']['layer_height_mm'] = _fine_layer_height_mm(result['printer'])
    try:
        return validate_settings(result)
    except VoxelMillError as exc:
        raise VoxelMillError('invalid_preset', str(exc)) from exc


def _document_for_save(preset, section_values=None, name=None, *, section: str):
    if section_values is not None:
        chosen_name = name
        if chosen_name is None and isinstance(preset, str):
            chosen_name = preset
        if chosen_name is None:
            _fail('A preset name is required when support is supplied' if section == _SUPPORT
                  else 'A preset name is required when process is supplied')
        return _validate_document({
            'schema_version': PRESET_SCHEMA_VERSION,
            'name': chosen_name,
            section: section_values,
        }, section)
    if preset is None:
        _fail('Preset data is required')
    document = _coerce_preset(preset, section)
    if name is not None:
        document['name'] = name
        document = _validate_document(document, section)
    return document


def _atomic_write(destination: Path, document: dict) -> dict:
    try:
        encoded = json.dumps(document, allow_nan=False, ensure_ascii=False,
                             sort_keys=True, indent=2).encode('utf-8') + b'\n'
    except (TypeError, ValueError, RecursionError) as exc:
        raise VoxelMillError('invalid_preset', f'Preset must be finite JSON: {exc}') from exc

    temporary = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=f'.{destination.name}.', suffix='.tmp',
                                         dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
    except (OSError, ValueError) as exc:
        raise VoxelMillError('invalid_preset', f'Cannot save preset {destination}: {exc}') from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return deepcopy(document)


def save_preset(path: str | Path, preset=None, support=None, *, name=None) -> dict:
    """Atomically save a validated support preset as portable UTF-8 JSON.

    ``preset`` may be a preset dictionary, built-in name, or JSON path.  For a
    convenient custom form, pass ``save_preset(path, name, support)``.
    Returns the normalized document written to disk.
    """
    destination = Path(path)
    document = _document_for_save(preset, support, name, section=_SUPPORT)
    return _atomic_write(destination, document)


def save_process_preset(path: str | Path, preset=None, process=None, *, name=None) -> dict:
    """Atomically save a validated process preset as portable UTF-8 JSON.

    ``preset`` may be a preset dictionary, built-in name, or JSON path.  For a
    convenient custom form, pass ``save_process_preset(path, name, process)``.
    Returns the normalized document written to disk.
    """
    destination = Path(path)
    document = _document_for_save(preset, process, name, section=_PROCESS)
    return _atomic_write(destination, document)


# A descriptive alias for callers that want to make the source explicit.
load_support_preset = load_preset
