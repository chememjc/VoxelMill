"""Strict versioned profile loading and deterministic process resolution."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import math
from typing import NoReturn
try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

from .contracts import VoxelMillError

BASE_TYPES = ('plate', 'none', 'pad', 'skate', 'skeleton', 'grid', 'hex', 'triangle')
MODEL_ANCHOR_SHAPES = ('cone', 'cylinder')
SMALL_PILLAR_MODES = ('middle', 'model')
SMALL_PILLAR_SHAPES = ('cone', 'cylinder')
TIP_SHAPES = ('cone', 'cylinder')
BRACE_DESTINATIONS = ('supports', 'base', 'both')
BRACE_PATTERNS = ('single', 'alternating', 'x')
SUPPORT_VOID_POLICIES = ('fail', 'ignore', 'fill')
#: Layouts of the illustrative support fixture. Here rather than in
#: ``support_example`` so the CLI can offer them without importing the router.
EXAMPLE_LAYOUTS = ('array', 'part-to-part', 'showcase')

DEFAULTS = {
    'schema_version': 1,
    'printer': {
        'id': 'mars5-ultra', 'name': 'Elegoo Mars 5 Ultra',
        'build_mm': [153.36, 77.76, 165.0], 'pixels': [8520, 4320],
        'pixel_pitch_mm': [0.018, 0.018], 'edge_clearance_mm': 2.0,
        'image_mirror_x': False, 'image_mirror_y': False,
        # False until someone confirms the LCD orientation against a printed
        # part. The two reference GOO files for this machine disagree
        # (CHITUBOX mirror_x=1, ELEGOO SatelLite mirror_y=1), and a mirrored
        # threaded or keyed part is scrap, so every export says so until this
        # is set true deliberately.
        'image_mirror_verified': False,
        'layer_height_range_mm': [0.01, 0.2], 'output_formats': ['goo'],
        # Reference-observed Mars tilt fields; hardware calibration is pending.
        'motion': {'bottom_lift_height': 0.05, 'bottom_lift_speed': 0.05, 'lift_height': 0.05, 'lift_speed': 0.05, 'bottom_retract_height': 0.05, 'bottom_retract_speed': 0.05, 'retract_height': 0.05, 'retract_speed': 0.05, 'bottom_lift_height2': 0.0, 'bottom_lift_speed2': 0.0, 'lift_height2': 0.0, 'lift_speed2': 0.0, 'bottom_retract_height2': 0.0, 'bottom_retract_speed2': 0.0, 'retract_height2': 0.0, 'retract_speed2': 0.0, 'bottom_light_pwm': 255, 'light_pwm': 255},
    },
    # Density and price are 0 for "not supplied". A guessed density would put a
    # fabricated weight in every report, so an unset value reports null rather
    # than a plausible number nobody entered.
    'resin': {'id': 'sunlu-abs-like-gray', 'name': 'Sunlu ABS-like gray',
              'density_g_cm3': 0.0, 'cost_per_liter': 0.0, 'currency': '$'},
    'process': {
        'layer_height_mm': 0.05, 'bottom_exposure_s': 35.0,
        'normal_exposure_s': 3.5, 'bottom_layers': 4, 'transition_layers': 5,
        'bottom_rest_after_exposure_s': 1.0, 'normal_rest_after_exposure_s': 1.0,
        'bottom_settle_before_exposure_s': 0.5, 'normal_settle_before_exposure_s': 0.5,
        'bottom_wait_after_lift_s': 0.0, 'normal_wait_after_lift_s': 0.0,
        # Bottom layers are overexposed to stick, so they cure wider than the
        # model.  This shrinks the exported frame, in millimeters of radius, on
        # the layers below elephant_foot_layers, ramping linearly to zero.
        # 0 mm disables it; 0 layers derives the count from bottom_layers.
        'elephant_foot_compensation_mm': 0.0, 'elephant_foot_layers': 0,
        # Slice-time dimensional compensation. 0 means off and uncalibrated.
        # shrink_percent_xy scales each layer about the plate center without
        # changing the source mesh. tolerance_offset_mm is a morphological
        # radius: positive erodes (over-cure), negative dilates.
        'shrink_percent_xy': 0.0, 'shrink_percent_z': 0.0,
        'tolerance_offset_mm': 0.0, 'bottom_tolerance_offset_mm': 0.0,
        # 1 is binary occupancy. 2 or 4 supersample coverage grayscale.
        # Support tips stay binary unless antialias_supports is true.
        'antialias_levels': 1, 'antialias_supports': False,
    },
    'hollow': {
        'enabled': False, 'wall_thickness_mm': 2.0, 'voxel_size_mm': 0.0,
        'mode': 'inner', 'min_wall_thickness_mm': 1.0,
        'drain_diameter_mm': 2.0, 'vent_diameter_mm': 2.0,
        'infill': 'none', 'infill_pitch_mm': 4.0,
    },
    'support': {
        'automatic': True, 'auto_bracing': True,
        # Primary supports may anchor on another model only when explicitly
        # enabled.  Brace destinations are always support-network nodes and
        # never inherit this policy.
        'allow_part_to_part': False,
        # After routing, drop unroutable contacts that already have material in
        # a 3x3 printer-pitch neighbourhood one layer below. Near-vertical walls
        # sampled on both sides of the surface do not need a pillar. Island and
        # manual/correction contacts are never dropped.
        'drop_attached_unroutable': True,
        'tree_supports': False,
        # Extra automatic samples along the outer perimeter of downward-face
        # clusters. Off keeps the existing face-centroid/lattice sampling.
        'contour_supports': False,
        # Extra automatic samples along open mesh boundary edges (crop cuts).
        # Closed solids produce none. Off by default.
        'boundary_supports': False,
        # 0 clusters vertical plate supports within 2 * spacing_mm.
        'tree_cluster_mm': 0.0,
        # 0 scores model and plate routes by length equally. 1 always chooses
        # an available plate route. Intermediate values require a model route
        # to be proportionally shorter: model <= plate * (1 - avoidance).
        'part_to_part_avoidance': 1.0,
        'spacing_mm': 3.0, 'contact_diameter_mm': 0.4, 'penetration_mm': 0.15,
        'tip_shape': 'cone', 'break_point_diameter_mm': 0.8,
        'pillar_diameter_mm': 1.2, 'tip_length_mm': 2.0,
        # Diameter where the tip cone meets the pillar. CHITUBOX calls this
        # "Tip Down Diameter" and keeps it independent of the middle segment's
        # diameter; they merely happen to be equal in the known-good profile.
        # 0 derives it from pillar_diameter_mm, which is the old behavior.
        'tip_base_diameter_mm': 0.0,
        # Independent bottom connector for part-to-part supports. Both ends
        # are a ball plus a short cone; zero length keeps the historical
        # direct middle/tip attachment with no bottom ball. Zero diameter
        # derives the chosen middle diameter. Penetration is independent of
        # the top tip. The 0.8 mm break-point ball must fit in the 2 mm tip.
        'model_anchor_shape': 'cone', 'model_anchor_length_mm': 2.0,
        'model_anchor_diameter_mm': 0.4, 'model_anchor_penetration_mm': 0.15,
        # Steepest-to-shallowest limit for an angled branch, measured from
        # horizontal. 45 is what the router hard-coded before this existed.
        'pillar_angle_deg': 45.0,
        # A second, thinner pillar class for short runs. Both 0 disables it
        # as a length-gated class; a pillar no longer than
        # small_pillar_max_length_mm uses the thin diameter instead.
        # Short model-anchor gaps have a separate VoxelMill rule in
        # route_contacts: they stay point-to-point at this diameter if set,
        # else at the contact diameter, and do not swell to pillar_diameter_mm.
        'small_pillar_diameter_mm': 0.0, 'small_pillar_max_length_mm': 0.0,
        # The original thin-middle class is separate from an entire short
        # model-to-model connector, whose two ends penetrate independently.
        'small_pillar_mode': 'middle', 'small_pillar_shape': 'cone',
        'small_pillar_upper_depth_mm': 0.0, 'small_pillar_lower_depth_mm': 0.0,
        # Downward brace geometry (45 degrees by default).  Origins are sampled from the
        # shoulder below each tip taper; brace_spacing_mm is vertical spacing
        # between those origins.  brace_max_length_mm is the complete diagonal
        # length limit, while brace_max_distance_mm independently limits the
        # neighboring support search.
        'brace_spacing_mm': 15.0, 'brace_diameter_mm': 0.0,
        'brace_max_distance_mm': 0.0, 'brace_max_length_mm': 30.0,
        'brace_destination': 'both', 'brace_pattern': 'single',
        'brace_branches_per_node': 1, 'brace_angle_deg': 45.0,
        'brace_min_height_mm': 0.0, 'brace_azimuth_deg': 0.0,
        # What the supports land on. 'grid' is the default: less resin and
        # less suction than a solid slab, still one connected base. 'plate' is
        # the convex hull raft, with a 30 degree outer putty-knife bevel.
        # 'none' is feet only; 'pad' gives each foot its own disc.
        'base_type': 'grid',
        # Outer-perimeter wall angle of a plate raft, from the plate, so a
        # putty knife fits under the rim. 0 keeps the old near-vertical wall
        # with a tiny top chamfer. Other base types use base_edge_slope_deg.
        'raft_slope_deg': 30.0,
        # Pad diameter and thickness. 0 derives them from the raft values.
        'base_touch_diameter_mm': 0.0, 'base_thickness_mm': 0.0,
        # Skate is a capsule: total length, including its rounded ends. Zero
        # derives a circular foot from the touch diameter, without inventing
        # an elongation for the CHITUBOX reference that does not specify one.
        'base_skate_length_mm': 0.0, 'base_rotation_deg': 0.0,
        'base_strut_width_mm': 0.0, 'base_cell_size_mm': 6.0,
        # Wall angle from the plate for the added bases, so the base is widest
        # where it touches. 0 keeps the vertical wall every base had before.
        # The taper is quantised to whole printed layers because that is what
        # the machine can actually make; see docs/algorithms.md.
        'base_edge_slope_deg': 0.0,
        # Shortest tip cone the router may emit when a contact sits closer to
        # the material below it than a full tip. 0.30 mm is the contact depth
        # from the known-good CHITUBOX configuration recorded in plan2.
        'min_tip_length_mm': 0.3,
        'raft_thickness_mm': 1.0, 'raft_expansion_mm': 2.0,
        'max_slenderness': 40.0, 'max_span_mm': 3.0, 'min_overlap_pixels': 1,
        'overhang_angle_deg': 45.0, 'support_clearance_mm': 0.3,
        # How many times routing may add contacts under the islands a reslice
        # found before it reports what is left. Each pass is a full route plus
        # an assembly, so the cap is a time limit as much as a policy.
        'max_island_passes': 5,
        # 0 derives the limit from spacing_mm; see docs/algorithms.md.
        'max_contact_gap_mm': 0.0, 'max_contact_load_mm2': 0.0,
    },
    # Peel analysis is advisory until calibrated against measured prints.
    'peel': {'enabled': True, 'max_angle_deg': 10.0,
             'area_threshold_mm2': 100.0, 'reference_lift_speed': 0.05},
    'repair': {
        'seal_voids': True, 'min_orifice_area_mm2': 1.0,
        'aggressiveness': 'conservative', 'max_deviation_mm': 0.05,
        'remove_tiny_features': False, 'auto_drain_holes': False,
        'voxel_size_mm': 0.0, 'smooth_iterations': 0, 'min_void_volume_mm3': 0.0,
        'step_linear_deflection_mm': 0.1,
        # 0 = exact-coordinate weld; positive (capped at 0.05 mm) is an explicit
        # STEP/CAD import repair for near-duplicate tessellation vertices.
        'weld_tolerance_mm': 0.0,
        # fail: any enclosed void or drainage bottleneck blocks export (default).
        # ignore: support-class findings are recorded and do not fail; model-class
        # still fails. fill: exact-path enclosed shells after union are filled;
        # drainage necks are not shells and still need tip geometry.
        'support_void_policy': 'fail',
    },
    'assembly': {'union': 'auto', 'require_raster_parity': True, 'max_parity_examples': 16,
                 # Off by default: clipping destroys geometry the printer
                 # cannot reach, and that must be asked for, never assumed.
                 'clip_to_build_volume': False},
    'resources': {'memory_gib': 32.0,
                  # 0 derives the count from the machine's physical cores.
                  'workers': 0, 'worker_policy': 'performance',
                  'scratch_dir': None,
                  # auto selects CUDA only after a successful runtime/device
                  # probe. cpu is deterministic fallback; cuda is strict.
                  'acceleration': 'auto', 'cuda_device': 0,
                  # Opt-in only; never inherited from a printer/resin profile.
                  'post_slice_hook': None},
}


# Omitted from older archives, these used to mean "off". Filling the current
# nonzero DEFAULTS would sprout break-point balls and bottom connectors on
# projects that never stored those keys. Explicit zeros in a saved table are
# kept as zeros; only an absent key takes the historical off values.
_LEGACY_SUPPORT_OFF = {
    'break_point_diameter_mm': 0.0,
    'model_anchor_length_mm': 0.0,
    'model_anchor_diameter_mm': 0.0,
    'model_anchor_penetration_mm': 0.0,
}


def fill_legacy_settings(settings):
    """Fill sections and support keys that older schema-1 archives omit.

    An absent ``peel`` or ``assembly`` table is filled from defaults. A present
    but incomplete table of either is left untouched so ``validate_settings``
    still rejects it. Keys added later to the already-universal ``support``
    table cannot be distinguished from omissions, so missing support keys take
    their current defaults — except the point-to-point ball/anchor keys, which
    fill historical zeros. Unknown keys are not invented and still fail.
    """
    if not isinstance(settings, dict):
        return settings
    for section in ('assembly', 'peel', 'hollow'):
        settings.setdefault(section, deepcopy(DEFAULTS[section]))
    support = settings.get('support')
    if isinstance(support, dict):
        for key, value in DEFAULTS['support'].items():
            if key in support:
                continue
            support[key] = deepcopy(_LEGACY_SUPPORT_OFF.get(key, value))
    resources = settings.get('resources')
    if isinstance(resources, dict):
        for key in ('acceleration', 'cuda_device'):
            resources.setdefault(key, deepcopy(DEFAULTS['resources'][key]))
    repair = settings.get('repair')
    if isinstance(repair, dict):
        repair.setdefault('weld_tolerance_mm', deepcopy(DEFAULTS['repair']['weld_tolerance_mm']))
    process = settings.get('process')
    if isinstance(process, dict):
        for key in ('antialias_levels', 'antialias_supports'):
            process.setdefault(key, deepcopy(DEFAULTS['process'][key]))
    return settings


def _error(message) -> NoReturn:
    raise VoxelMillError('invalid_profile', message)


def _keys(value, allowed, context):
    if not isinstance(value, dict):
        _error(f'{context} must be a table')
    unknown = set(value) - set(allowed)
    if unknown:
        _error(f'Unknown {context} fields: {sorted(unknown)}')


def _merge(target, changes, context):
    _keys(changes, target, context)
    for key, value in changes.items():
        if isinstance(target[key], dict):
            if key == 'motion':
                if not isinstance(value, dict):
                    _error('printer.motion must be a table')
                for name, setting in value.items():
                    if not isinstance(name, str) or not isinstance(setting, (str, bool, int, float)):
                        _error('Motion settings must be named scalar values')
                    if isinstance(setting, (int, float)) and not math.isfinite(setting):
                        _error('Motion settings must be finite')
                target[key].update(deepcopy(value))
            else:
                _merge(target[key], value, f'{context}.{key}')
        else:
            target[key] = deepcopy(value)


def _read(path):
    try:
        if Path(path).stat().st_size > 1024 * 1024:
            _error('Profile exceeds 1 MiB limit')
        with open(path, 'rb') as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise VoxelMillError('invalid_profile', f'Cannot read profile {path}: {exc}') from exc
    if type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        _error('Profile schema_version must be integer 1')
    return data


def _number(value, name, minimum=0, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        _error(f'{name} must be a finite number')
    if value < minimum or (positive and value == minimum):
        _error(f'{name} must be {">" if positive else ">="} {minimum}')


def validate_settings(settings):
    """Validate a fully resolved dictionary; return it unchanged."""
    _keys(settings, DEFAULTS, 'settings')
    if set(settings) != set(DEFAULTS) or type(settings['schema_version']) is not int or settings['schema_version'] != 1:
        _error('Resolved settings require all sections and schema_version 1')
    for section in ('printer', 'resin', 'process', 'support', 'peel', 'repair', 'assembly', 'resources', 'hollow'):
        _keys(settings[section], DEFAULTS[section], section)
        if set(settings[section]) != set(DEFAULTS[section]):
            _error(f'Incomplete resolved {section} settings')
    p = settings['printer']
    for section in ('printer', 'resin'):
        for key in ('id', 'name'):
            if not isinstance(settings[section][key], str) or not settings[section][key].strip():
                _error(f'{section}.{key} must be a nonempty string')
    resin = settings['resin']
    for key in ('density_g_cm3', 'cost_per_liter'):
        # Zero means "not supplied"; usage figures derived from it are null.
        _number(resin[key], f'resin.{key}')
    currency = resin['currency']
    if not isinstance(currency, str) or not currency.strip():
        _error('resin.currency must be a nonempty string')
    # The GOO header stores the currency in an 8-byte ASCII field, so a value
    # that cannot be written there is rejected when it is set rather than at
    # export time.
    try:
        encoded = currency.encode('ascii', 'strict')
    except UnicodeError:
        _error('resin.currency must be ASCII; the GOO header field is 8 ASCII bytes')
    if len(encoded) > 8:
        _error('resin.currency must be at most 8 ASCII bytes')
    for key, count in (('build_mm', 3), ('pixels', 2), ('pixel_pitch_mm', 2), ('layer_height_range_mm', 2)):
        if not isinstance(p[key], list) or len(p[key]) != count:
            _error(f'printer.{key} must contain {count} values')
        for value in p[key]:
            _number(value, f'printer.{key}', positive=True)
            if key == 'pixels' and type(value) is not int:
                _error('Pixel dimensions must be integers')
    for axis in range(2):
        if not math.isclose(p['pixels'][axis] * p['pixel_pitch_mm'][axis], p['build_mm'][axis], rel_tol=1e-6, abs_tol=1e-6):
            _error('Build dimensions must match pixel dimensions times pixel pitch')
    _number(p['edge_clearance_mm'], 'printer.edge_clearance_mm')
    if 2 * p['edge_clearance_mm'] >= min(p['build_mm'][:2]):
        _error('Plate edge clearance leaves no usable build area')
    for key in ('image_mirror_x', 'image_mirror_y', 'image_mirror_verified'):
        if type(p[key]) is not bool:
            _error(f'printer.{key} must be boolean')
    if not isinstance(p['output_formats'], list) or not p['output_formats'] or not all(isinstance(x, str) and x for x in p['output_formats']):
        _error('printer.output_formats must be a nonempty list of strings')
    _merge({'motion': {}}, {'motion': p['motion']}, 'printer')
    _validate_motion(p['motion'])
    for key, value in settings['process'].items():
        if key in ('antialias_levels', 'antialias_supports'):
            continue
        if key in ('shrink_percent_xy', 'shrink_percent_z', 'tolerance_offset_mm',
                   'bottom_tolerance_offset_mm'):
            _number(value, f'process.{key}', minimum=-2.0)
            continue
        _number(value, f'process.{key}', positive=key in ('layer_height_mm', 'bottom_exposure_s', 'normal_exposure_s'))
        if key in ('bottom_layers', 'transition_layers', 'elephant_foot_layers') \
                and type(value) is not int:
            _error(f'process.{key} must be an integer')
    # A compensation wider than a bottom layer's own footprint would erase it.
    if settings['process']['elephant_foot_compensation_mm'] > 1.0:
        _error('process.elephant_foot_compensation_mm above 1.0 mm would erase bottom '
               'geometry rather than compensate it')
    if type(settings['process']['antialias_supports']) is not bool:
        _error('process.antialias_supports must be boolean')
    if type(settings['process']['antialias_levels']) is not int \
            or settings['process']['antialias_levels'] not in (1, 2, 4):
        _error('process.antialias_levels must be 1, 2 or 4')
    lo, hi = p['layer_height_range_mm']
    if not lo <= settings['process']['layer_height_mm'] <= hi:
        _error('Layer height outside printer layer range')
    for key, value in settings['support'].items():
        if key in ('automatic', 'auto_bracing', 'allow_part_to_part',
                   'drop_attached_unroutable', 'tree_supports',
                   'contour_supports', 'boundary_supports'):
            if type(value) is not bool:
                _error(f'support.{key} must be boolean')
            continue
        if key in ('brace_destination', 'brace_pattern'):
            choices = BRACE_DESTINATIONS if key == 'brace_destination' else BRACE_PATTERNS
            if value not in choices:
                _error(f'support.{key} must be one of {", ".join(choices)}')
            continue
        if key == 'brace_branches_per_node':
            if type(value) is not int or not 1 <= value <= 8:
                _error('support.brace_branches_per_node must be an integer from 1 to 8')
            continue
        if key == 'base_type':
            # An enum needs its own branch; the catch-all below requires a
            # positive finite number and would reject any string.
            if value not in BASE_TYPES:
                _error(f'support.base_type must be one of {", ".join(BASE_TYPES)}')
            continue
        if key == 'tip_shape':
            if value not in TIP_SHAPES:
                _error(f'support.tip_shape must be one of {", ".join(TIP_SHAPES)}')
            continue
        if key == 'model_anchor_shape':
            if value not in MODEL_ANCHOR_SHAPES:
                _error(f'support.model_anchor_shape must be one of {", ".join(MODEL_ANCHOR_SHAPES)}')
            continue
        if key in ('small_pillar_mode', 'small_pillar_shape'):
            choices = SMALL_PILLAR_MODES if key == 'small_pillar_mode' else SMALL_PILLAR_SHAPES
            if value not in choices:
                _error(f'support.{key} must be one of {", ".join(choices)}')
            continue
        if key == 'max_island_passes':
            if type(value) is not int or not 1 <= value <= 10:
                _error('support.max_island_passes must be an integer from 1 to 10')
            continue
        if key in ('base_rotation_deg', 'brace_azimuth_deg'):
            _number(value, f'support.{key}', minimum=-360)
            if value > 360:
                _error(f'support.{key} must be between -360 and 360')
            continue
        _number(value, f'support.{key}', positive=key not in (
            'penetration_mm', 'raft_expansion_mm', 'max_contact_gap_mm', 'max_contact_load_mm2',
            'tip_base_diameter_mm', 'small_pillar_diameter_mm', 'small_pillar_max_length_mm',
            'model_anchor_length_mm', 'model_anchor_diameter_mm', 'model_anchor_penetration_mm',
            'small_pillar_upper_depth_mm', 'small_pillar_lower_depth_mm',
            'brace_diameter_mm', 'brace_max_distance_mm', 'brace_min_height_mm', 'part_to_part_avoidance',
            'base_touch_diameter_mm', 'base_thickness_mm', 'break_point_diameter_mm',
            'base_skate_length_mm', 'base_strut_width_mm', 'base_edge_slope_deg',
            'raft_slope_deg', 'tree_cluster_mm'))
    s = settings['support']
    if s['break_point_diameter_mm'] and s['break_point_diameter_mm'] < s['contact_diameter_mm']:
        _error('support.break_point_diameter_mm must be at least contact_diameter_mm when enabled')
    if s['break_point_diameter_mm'] > s['tip_length_mm'] + s['penetration_mm']:
        _error('support.break_point_diameter_mm must fit within the tip length and penetration')
    if (s['break_point_diameter_mm'] and s['model_anchor_length_mm']
            and s['break_point_diameter_mm'] > s['model_anchor_length_mm'] + s['model_anchor_penetration_mm']):
        _error('support.break_point_diameter_mm must fit within the model-anchor length and penetration')
    if s['model_anchor_diameter_mm'] and not s['model_anchor_length_mm']:
        _error('support.model_anchor_diameter_mm requires a positive model_anchor_length_mm')
    if s['part_to_part_avoidance'] > 1:
        _error('support.part_to_part_avoidance must be between 0 and 1')
    if not 0 < s['overhang_angle_deg'] < 90:
        _error('support.overhang_angle_deg must be between 0 and 90 exclusive')
    if not 0 < s['brace_angle_deg'] < 90:
        _error('support.brace_angle_deg must be between 0 and 90 exclusive')
    if not 0 < s['pillar_angle_deg'] < 90:
        _error('support.pillar_angle_deg must be between 0 and 90 exclusive')
    if s['tip_base_diameter_mm'] and s['contact_diameter_mm'] > s['tip_base_diameter_mm']:
        _error('support.contact_diameter_mm must not exceed tip_base_diameter_mm')
    if ((s['small_pillar_mode'] == 'middle' and
         bool(s['small_pillar_diameter_mm']) != bool(s['small_pillar_max_length_mm'])) or
        (s['small_pillar_max_length_mm'] and not s['small_pillar_diameter_mm'])):
        _error('support.small_pillar_diameter_mm and small_pillar_max_length_mm must both be '
               'set or both be zero; a thin pillar with no length limit would replace every pillar')
    if s['small_pillar_mode'] == 'middle' and (s['small_pillar_upper_depth_mm'] or s['small_pillar_lower_depth_mm']):
        _error('Small-pillar upper/lower depths require support.small_pillar_mode=model')
    if s['small_pillar_diameter_mm'] > s['pillar_diameter_mm']:
        _error('support.small_pillar_diameter_mm must not exceed pillar_diameter_mm')
    if s['base_type'] in ('plate', 'none') and (s['base_touch_diameter_mm'] or s['base_thickness_mm']):
        _error('support.base_touch_diameter_mm and base_thickness_mm apply to pad, skate, skeleton, grid, '
               'hex, or triangle; the plate raft is sized by raft_expansion_mm and raft_thickness_mm')
    if s['base_edge_slope_deg'] and not 0 < s['base_edge_slope_deg'] < 90:
        _error('support.base_edge_slope_deg must be between 0 and 90 exclusive, or 0 for a vertical wall')
    if s['raft_slope_deg'] and not 0 < s['raft_slope_deg'] < 90:
        _error('support.raft_slope_deg must be between 0 and 90 exclusive, or 0 for a vertical plate wall')
    if s['base_type'] in ('none',) and s['base_edge_slope_deg']:
        _error('support.base_edge_slope_deg applies to pad, skate, skeleton, grid, hex, triangle, or plate; '
               'bare feet have no base to taper')
    if s['base_type'] == 'plate' and s['base_edge_slope_deg']:
        _error('support.base_edge_slope_deg does not apply to plate; use support.raft_slope_deg for the outer rim')
    diameter = s['base_touch_diameter_mm'] or s['pillar_diameter_mm'] + 2 * s['raft_expansion_mm']
    if s['base_type'] not in ('plate', 'none') and diameter <= s['pillar_diameter_mm']:
        _error('support.base_touch_diameter_mm must exceed the pillar diameter')
    if s['base_type'] == 'skate' and s['base_skate_length_mm'] and s['base_skate_length_mm'] < diameter:
        _error('support.base_skate_length_mm must be at least the resolved touch diameter')
    if s['base_type'] in ('grid', 'hex'):
        width = s['base_strut_width_mm'] or s['pillar_diameter_mm']
        if width >= s['base_cell_size_mm']:
            _error('support.base_strut_width_mm must be less than base_cell_size_mm for an open lattice')
    if type(s['min_overlap_pixels']) is not int:
        _error('support.min_overlap_pixels must be an integer')
    if s['contact_diameter_mm'] > s['pillar_diameter_mm'] or s['penetration_mm'] >= s['tip_length_mm']:
        _error('Contact must fit pillar; penetration must be shorter than tip')
    if not s['penetration_mm'] < s['min_tip_length_mm'] <= s['tip_length_mm']:
        _error('support.min_tip_length_mm must be longer than penetration and no longer than tip_length_mm')
    r = settings['repair']
    if r['remove_tiny_features'] or r['auto_drain_holes']:
        _error('Tiny-feature removal and drilled drains are not implemented; these options must remain false')
    for key in ('seal_voids', 'remove_tiny_features', 'auto_drain_holes'):
        if type(r[key]) is not bool:
            _error(f'repair.{key} must be boolean')
    if r['aggressiveness'] not in ('none', 'conservative', 'aggressive'):
        _error('repair.aggressiveness must be none, conservative, or aggressive')
    if r['support_void_policy'] not in SUPPORT_VOID_POLICIES:
        _error('repair.support_void_policy must be fail, ignore, or fill')
    _number(r['min_orifice_area_mm2'], 'repair.min_orifice_area_mm2')
    _number(r['max_deviation_mm'], 'repair.max_deviation_mm')
    # Zero means "derive the voxel pitch from repair.max_deviation_mm".
    _number(r['voxel_size_mm'], 'repair.voxel_size_mm')
    _number(r['min_void_volume_mm3'], 'repair.min_void_volume_mm3')
    _number(r['step_linear_deflection_mm'], 'repair.step_linear_deflection_mm', positive=True)
    _number(r['weld_tolerance_mm'], 'repair.weld_tolerance_mm')
    if r['weld_tolerance_mm'] > 0.05:
        _error('repair.weld_tolerance_mm must be <= 0.05')
    if type(r['smooth_iterations']) is not int or not 0 <= r['smooth_iterations'] <= 64:
        _error('repair.smooth_iterations must be an integer from 0 to 64')
    peel = settings['peel']
    if type(peel['enabled']) is not bool:
        _error('peel.enabled must be boolean')
    _number(peel['max_angle_deg'], 'peel.max_angle_deg')
    if not peel['max_angle_deg'] < 90:
        _error('peel.max_angle_deg must be less than 90')
    _number(peel['area_threshold_mm2'], 'peel.area_threshold_mm2', positive=True)
    _number(peel['reference_lift_speed'], 'peel.reference_lift_speed', positive=True)
    a = settings['assembly']
    if a['union'] not in ('auto', 'exact'):
        _error('assembly.union must be auto or exact')
    if type(a['require_raster_parity']) is not bool:
        _error('assembly.require_raster_parity must be boolean')
    if type(a['max_parity_examples']) is not int or not 0 <= a['max_parity_examples'] <= 256:
        _error('assembly.max_parity_examples must be an integer from 0 to 256')
    if type(a['clip_to_build_volume']) is not bool:
        _error('assembly.clip_to_build_volume must be boolean')
    resources = settings['resources']
    _number(resources['memory_gib'], 'resources.memory_gib', minimum=0.25)
    if type(resources['workers']) is not int or not 0 <= resources['workers'] <= 32:
        _error('resources.workers must be an integer from 0 to 32, where 0 derives it from the machine')
    if resources['worker_policy'] not in ('performance', 'efficiency', 'all'):
        _error('resources.worker_policy must be performance, efficiency, or all')
    if resources['acceleration'] not in ('auto', 'cpu', 'cuda'):
        _error('resources.acceleration must be auto, cpu, or cuda')
    if type(resources['cuda_device']) is not int or resources['cuda_device'] < 0:
        _error('resources.cuda_device must be a nonnegative integer')
    if resources['scratch_dir'] is not None and (not isinstance(resources['scratch_dir'], str) or not resources['scratch_dir']):
        _error('resources.scratch_dir must be null or a nonempty path string')
    hook = resources['post_slice_hook']
    if hook is not None and (not isinstance(hook, str) or not hook.strip()):
        _error('resources.post_slice_hook must be null or a nonempty command string')
    hollow = settings['hollow']
    if type(hollow['enabled']) is not bool:
        _error('hollow.enabled must be boolean')
    if hollow['mode'] not in ('inner', 'bottom_open'):
        _error('hollow.mode must be inner or bottom_open')
    if hollow['infill'] not in ('none', 'grid', 'hex', 'gyroid'):
        _error('hollow.infill must be none, grid, hex or gyroid')
    for key in ('wall_thickness_mm', 'min_wall_thickness_mm', 'drain_diameter_mm',
                'vent_diameter_mm', 'infill_pitch_mm'):
        _number(hollow[key], f'hollow.{key}', positive=True)
    _number(hollow['voxel_size_mm'], 'hollow.voxel_size_mm')
    if hollow['min_wall_thickness_mm'] > hollow['wall_thickness_mm']:
        _error('hollow.min_wall_thickness_mm cannot exceed hollow.wall_thickness_mm')
    return settings


def _validate_motion(motion):
    """TSMC: second-stage travel needs a speed; retract sums match lift sums."""
    for prefix in ('bottom_', ''):
        lift = float(motion[f'{prefix}lift_height']) + float(motion[f'{prefix}lift_height2'])
        retract = float(motion[f'{prefix}retract_height']) + float(motion[f'{prefix}retract_height2'])
        if not math.isclose(lift, retract, rel_tol=0.0, abs_tol=1e-9):
            _error(f'printer.motion.{prefix}retract heights must sum to the matching lift heights')
        for stage in ('lift', 'retract'):
            height2 = float(motion[f'{prefix}{stage}_height2'])
            speed2 = float(motion[f'{prefix}{stage}_speed2'])
            if height2 > 0 and speed2 <= 0:
                _error(f'printer.motion.{prefix}{stage}_height2 needs a positive {prefix}{stage}_speed2')


def _validate_embedded_support_presets(presets, base_settings, context):
    """Validate named support blocks on a resin process; never resolve them here."""
    if not isinstance(presets, dict):
        _error(f'{context} must be a table')
    for name, values in presets.items():
        if not isinstance(name, str) or not name.strip():
            _error(f'{context} names must be nonempty strings')
        if not isinstance(values, dict):
            _error(f'{context}.{name} must be a table')
        probe = deepcopy(base_settings)
        _merge(probe['support'], values, f'{context}.{name}')
        validate_settings(probe)


def _support_overlay_from_preset(source, embedded):
    """Prefer a resin-embedded named preset; otherwise load builtin/JSON."""
    if isinstance(source, str):
        needle = source.lower()
        for name, values in embedded.items():
            if isinstance(name, str) and name.lower() == needle:
                return deepcopy(values)
    from .presets import load_preset
    return load_preset(source)['support']


def resolve_settings(printer_path=None, resin_path=None, overrides=None, *,
                     support_preset=None):
    """Resolve defaults → printer → resin process → named support preset → overrides."""
    settings = deepcopy(DEFAULTS)
    embedded_presets = {}
    if printer_path is not None:
        data = _read(printer_path)
        _keys(data, ('schema_version', 'printer', 'process', 'support', 'peel', 'repair', 'assembly', 'resources', 'hollow'), 'printer profile')
        if 'printer' not in data:
            _error('Printer profile requires a printer table')
        _merge(settings, data, 'settings')
        # A hook is arbitrary code; printer files must not carry one.
        settings['resources']['post_slice_hook'] = None
    if resin_path is not None:
        data = _read(resin_path)
        _keys(data, ('schema_version', 'resin', 'processes'), 'resin profile')
        if 'resin' not in data or 'processes' not in data:
            _error('Resin profile requires resin and processes tables')
        _merge(settings['resin'], data['resin'], 'resin')
        if not isinstance(data['processes'], dict):
            _error('Resin processes must be a table')
        # Validate every process, including unmatched entries: physical overrides never pass.
        for printer_id, entry in data['processes'].items():
            _keys(entry, ('process', 'support', 'support_presets'),
                  f'resin processes.{printer_id}')
            for section, values in entry.items():
                if section == 'support_presets':
                    _validate_embedded_support_presets(
                        values, settings, f'resin processes.{printer_id}.support_presets')
                    continue
                probe = deepcopy(settings)
                _merge(probe[section], values, f'resin processes.{printer_id}.{section}')
                validate_settings(probe)
        printer_id = settings['printer']['id']
        if printer_id not in data['processes']:
            _error(f'Resin has no process profile matching printer {printer_id!r}')
        matched = data['processes'][printer_id]
        if isinstance(matched.get('support_presets'), dict):
            embedded_presets = deepcopy(matched['support_presets'])
        clean = {key: value for key, value in matched.items() if key != 'support_presets'}
        _merge(settings, clean, 'settings')
    if support_preset is not None:
        _merge(settings['support'],
               _support_overlay_from_preset(support_preset, embedded_presets),
               'support')
    if overrides is not None:
        _merge(settings, overrides, 'settings')
    return validate_settings(settings)


def layer_exposure(settings, index):
    """Exposure seconds for a zero-based layer; ramp has N intermediate values."""
    if type(index) is not int or index < 0:
        raise VoxelMillError('invalid_layer', 'Layer index must be a nonnegative integer')
    p = settings['process']
    if index < p['bottom_layers']:
        return p['bottom_exposure_s']
    step = index - p['bottom_layers'] + 1
    if step <= p['transition_layers']:
        return p['bottom_exposure_s'] + (p['normal_exposure_s'] - p['bottom_exposure_s']) * step / (p['transition_layers'] + 1)
    return p['normal_exposure_s']


def resin_usage(settings, volume_mm3):
    """Cured resin volume, and the weight and cost it implies.

    ``volume_mm3`` is the cured volume the raster analysis measured, so it
    already includes supports and any base.  Weight and cost are ``None``
    unless the resin profile supplies a density or a price: a default density
    would put a fabricated number in every report.
    """
    if volume_mm3 is None:
        return {'volume_mm3': None, 'volume_ml': None, 'mass_g': None,
                'cost': None, 'currency': settings['resin']['currency'],
                'density_g_cm3': None, 'cost_per_liter': None,
                'source': 'unavailable'}
    _number(volume_mm3, 'volume_mm3')
    resin = settings['resin']
    milliliters = float(volume_mm3) / 1000.0
    density = float(resin['density_g_cm3'])
    price = float(resin['cost_per_liter'])
    return {
        'volume_mm3': float(volume_mm3),
        'volume_ml': milliliters,
        'mass_g': milliliters * density if density > 0 else None,
        'cost': milliliters / 1000.0 * price if price > 0 else None,
        'currency': resin['currency'],
        'density_g_cm3': density if density > 0 else None,
        'cost_per_liter': price if price > 0 else None,
        'source': 'raster_volume_mm3',
    }
