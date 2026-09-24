"""Every setting, declared once.

``FIELDS`` maps each ``section.key`` path of the resolved settings tree to a
:class:`Field`: how to validate its value, and how the editor and help present
it. ``config.DEFAULTS`` holds the default values; everything else about a
setting lives here:

* ``config.validate_settings`` checks every field with :func:`check_field`,
  then applies the rules that relate several fields to each other;
* ``gui/settings_table.py`` builds the editor's descriptors (tier, risk, unit,
  slider range, choices, CLI flag) from these entries;
* ``gui/helptext.py`` reads the help text from here.

Adding a setting means a default in ``config.DEFAULTS`` and an entry here
(``tests/test_settings_schema.py`` fails until both exist), plus a cross-field
rule in ``validate_settings`` if it constrains another setting.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from .contracts import VoxelMillError

BASE_TYPES = ('plate', 'none', 'pad', 'skate', 'skeleton', 'grid', 'hex', 'triangle')
MODEL_ANCHOR_SHAPES = ('cone', 'cylinder')
SMALL_PILLAR_MODES = ('middle', 'model')
SMALL_PILLAR_SHAPES = ('cone', 'cylinder')
TIP_SHAPES = ('cone', 'cylinder')
BRACE_DESTINATIONS = ('supports', 'base', 'both')
BRACE_PATTERNS = ('single', 'alternating', 'x')
SUPPORT_VOID_POLICIES = ('fail', 'ignore', 'fill')


@dataclass(frozen=True)
class Field:
    """One setting's rule and presentation.

    ``kind`` is one of:

    * ``number``: finite, at least ``minimum`` (strictly above it when
      ``positive``), at most ``maximum``, below ``below``; ``integer`` also
      requires an int;
    * ``int``: an int. With a ``message`` and no ``count`` the type and range
      are checked together and share that message;
    * ``bool``, ``text`` (nonempty string), ``optional_text`` (None or a
      nonempty string), ``text_list`` (nonempty list of nonempty strings),
      ``choice`` (one of ``choices``), ``currency``;
    * ``motion``: validated separately by ``config._validate_motion``.

    ``count`` makes the value a list of exactly that many elements, each
    checked as above. ``message`` replaces the generated error text where a
    specific wording is part of the contract.
    """
    kind: str
    minimum: float = 0
    positive: bool = False
    maximum: float | None = None
    below: float | None = None
    integer: bool = False
    count: int | None = None
    choices: tuple | None = None
    message: str | None = None
    # Presentation
    tier: str = 'advanced'
    risk: str = 'normal'
    unit: str | None = None
    label: str | None = None
    value_type: str | None = None
    range: tuple | None = None
    flag: str | None = None
    help: str | None = None


def _error(message):
    raise VoxelMillError('invalid_profile', message)


def _number(value, name, minimum=0, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        _error(f'{name} must be a finite number')
    if value < minimum or (positive and value == minimum):
        _error(f'{name} must be {">" if positive else ">="} {minimum}')


def check_field(path, value, field):
    """Raise ``invalid_profile`` when ``value`` breaks ``field``'s rule."""
    kind = field.kind
    if field.count is not None:
        if not isinstance(value, list) or len(value) != field.count:
            _error(f'{path} must contain {field.count} values')
        for item in value:
            _number(item, path, field.minimum, field.positive)
            if kind == 'int' and type(item) is not int:
                _error(field.message or f'{path} must be an integer')
        return
    if kind == 'number':
        _number(value, path, field.minimum, field.positive)
        if field.maximum is not None and value > field.maximum:
            _error(field.message or f'{path} must be <= {field.maximum}')
        if field.below is not None and not value < field.below:
            _error(field.message or f'{path} must be less than {field.below}')
        if field.integer and type(value) is not int:
            _error(f'{path} must be an integer')
    elif kind == 'int':
        if field.message is not None:
            upper = math.inf if field.maximum is None else field.maximum
            if type(value) is not int or not field.minimum <= value <= upper:
                _error(field.message)
        else:
            _number(value, path, field.minimum, field.positive)
            if type(value) is not int:
                _error(f'{path} must be an integer')
    elif kind == 'bool':
        if type(value) is not bool:
            _error(f'{path} must be boolean')
    elif kind == 'text':
        if not isinstance(value, str) or not value.strip():
            _error(f'{path} must be a nonempty string')
    elif kind == 'optional_text':
        if value is not None and (not isinstance(value, str) or not value.strip()):
            _error(field.message or f'{path} must be null or a nonempty string')
    elif kind == 'text_list':
        if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
            _error(f'{path} must be a nonempty list of strings')
    elif kind == 'choice':
        numeric = all(type(c) is int for c in field.choices)
        if (numeric and type(value) is not int) or value not in field.choices:
            _error(field.message or f'{path} must be one of {", ".join(map(str, field.choices))}')
    elif kind == 'currency':
        if not isinstance(value, str) or not value.strip():
            _error(f'{path} must be a nonempty string')
        # The GOO header stores the currency in an 8-byte ASCII field, so a
        # value that cannot be written there is rejected when it is set.
        try:
            encoded = value.encode('ascii', 'strict')
        except UnicodeError:
            _error(f'{path} must be ASCII; the GOO header field is 8 ASCII bytes')
        if len(encoded) > 8:
            _error(f'{path} must be at most 8 ASCII bytes')
    elif kind != 'motion':
        raise ValueError(f'unknown field kind {kind!r} for {path}')


#: Every leaf of ``config.DEFAULTS`` except ``schema_version``, in its order.
FIELDS: dict[str, Field] = {
    'printer.id': Field('text', tier='expert', risk='caution', help='Printer identifier, matched against the resin process table to select the exposure and motion settings for this machine.'),
    'printer.name': Field('text', tier='expert', help='Machine name, written into the GOO or CTB header.'),
    'printer.build_mm': Field('number', count=3, positive=True, tier='expert', risk='caution', unit='mm', help='Build width, depth, height in mm, as a JSON array. Width/depth must match pixels times pitch.'),
    'printer.pixels': Field('int', count=2, positive=True, message='Pixel dimensions must be integers', tier='expert', risk='caution', help='LCD width and height in pixels, as a JSON array of integers.'),
    'printer.pixel_pitch_mm': Field('number', count=2, positive=True, tier='expert', risk='caution', unit='mm', help='Pixel width and height in mm, as a JSON array.'),
    'printer.edge_clearance_mm': Field('number', tier='advanced', unit='mm', range=(0.0, 50.0), help='Margin kept clear from the build plate edges when placing and validating parts, in mm on every side. Twice this value must be less than the shorter build dimension, or no usable build area remains.'),
    'printer.image_mirror_x': Field('bool', tier='expert', risk='caution', help='Mirror the exported raster left to right before writing it, to match the LCD orientation of this machine.'),
    'printer.image_mirror_y': Field('bool', tier='expert', risk='caution', help='Mirror the exported raster top to bottom before writing it, to match the LCD orientation of this machine.'),
    'printer.image_mirror_verified': Field('bool', tier='expert', risk='caution', help='Whether the mirror flags above have been checked against an actual printed part. False keeps a goo_orientation_unverified warning on every export; true suppresses it.'),
    'printer.layer_height_range_mm': Field('number', count=2, positive=True, tier='expert', unit='mm', help='Hard minimum and maximum layer height in mm, as a JSON array.'),
    'printer.output_formats': Field('text_list', tier='expert', help='Export formats this printer profile supports, such as goo. Must be a nonempty list of strings; records the printer format, not hardware verification status.'),
    'printer.motion': Field('motion', help='Reference motion fields as a JSON object; these are not a calibrated timing or strength model.'),
    'resin.id': Field('text', tier='advanced', help='Resin identifier, recording which resin these settings belong to in saved profiles and project files.'),
    'resin.name': Field('text', tier='advanced', help='Resin profile name, written into the GOO or CTB header.'),
    'resin.density_g_cm3': Field('number', tier='advanced', unit='g/cm³', range=(0.0, 5.0), help='Resin density. 0 means unknown; no weight is estimated.'),
    'resin.cost_per_liter': Field('number', tier='advanced', range=(0.0, 1000000.0), help='Resin price per liter in the chosen currency. 0 means unknown.'),
    'resin.currency': Field('currency', tier='advanced', help='Currency symbol or code used for cost figures. Must be a nonempty ASCII string of at most 8 bytes, the size of the GOO header currency field.'),
    'process.layer_height_mm': Field('number', positive=True, tier='simple', unit='mm', range=(0.001, 1.0), flag='--layer-height-mm', help='Sliced layer thickness, in mm. Must fall within printer.layer_height_range_mm.'),
    'process.bottom_exposure_s': Field('number', positive=True, tier='simple', unit='s', range=(0.01, 300.0), help='Exposure time for each bottom layer, in seconds. Longer than the normal exposure so the first layers stick to the plate.'),
    'process.normal_exposure_s': Field('number', positive=True, tier='simple', unit='s', range=(0.01, 300.0), help='Exposure time for each normal layer after the bottom and transition layers, in seconds.'),
    'process.bottom_layers': Field('int', tier='simple', range=(0, 1000), help='Number of initial layers exposed at bottom_exposure_s for plate adhesion.'),
    'process.transition_layers': Field('int', tier='simple', range=(0, 1000), help='Number of layers after the bottom layers whose exposure ramps linearly from bottom_exposure_s to normal_exposure_s. 0 jumps straight to normal exposure on the next layer.'),
    'process.bottom_rest_after_exposure_s': Field('number', help='Dwell time after curing a bottom layer, before the plate lifts.'),
    'process.normal_rest_after_exposure_s': Field('number', help='Dwell time after curing a normal layer, before the plate lifts.'),
    'process.bottom_settle_before_exposure_s': Field('number', help='Dwell time after reaching a bottom layer print position, before exposure starts, to let resin settle.'),
    'process.normal_settle_before_exposure_s': Field('number', help='Dwell time after reaching a normal layer print position, before exposure starts, to let resin settle.'),
    'process.bottom_wait_after_lift_s': Field('number', help='Dwell time after the lift and retract motion for a bottom layer, before the next layer begins.'),
    'process.normal_wait_after_lift_s': Field('number', help='Dwell time after the lift and retract motion for a normal layer, before the next layer begins.'),
    'process.elephant_foot_compensation_mm': Field('number', tier='advanced', risk='caution', unit='mm', flag='--elephant-foot-mm', help='Radius, in mm, shrunk from the exported bottom layers to counter elephant-foot overcure widening, ramping linearly to zero over elephant_foot_layers. 0 disables it; values above 1.0 mm are rejected because they would erase bottom geometry instead of compensating it.'),
    'process.elephant_foot_layers': Field('int', flag='--elephant-foot-layers', help='Number of layers the elephant-foot compensation ramps down to zero over. 0 derives the count from bottom_layers.'),
    'process.shrink_percent_xy': Field('number', minimum=-2.0, tier='expert', risk='uncalibrated', unit='%', help='Slice-time XY scale correction, as a percent, applied about the plate center without changing the source mesh. 0 disables it; uncalibrated.'),
    'process.shrink_percent_z': Field('number', minimum=-2.0, tier='expert', risk='uncalibrated', unit='%', help='Slice-time Z scale correction, as a percent. 0 disables it; uncalibrated.'),
    'process.tolerance_offset_mm': Field('number', minimum=-2.0, tier='expert', risk='uncalibrated', unit='mm', help='Morphological radius applied to normal layers: positive erodes for over-cure, negative dilates. 0 disables it; uncalibrated.'),
    'process.bottom_tolerance_offset_mm': Field('number', minimum=-2.0, tier='expert', risk='uncalibrated', unit='mm', help='Morphological radius applied to bottom layers only, same convention as tolerance_offset_mm. 0 disables it; uncalibrated.'),
    'process.antialias_levels': Field('choice', choices=(1, 2, 4), message='process.antialias_levels must be 1, 2 or 4', help='Grayscale supersampling factor for the exported raster. 1 is binary occupancy; 2 or 4 supersample to coverage grayscale.'),
    'process.antialias_supports': Field('bool', help='Grayscale-antialias support tips as well when antialias_levels is above 1, instead of keeping them binary. Only takes effect where model and support occupancy can be rasterized separately; otherwise the export records antialias_supports_unseparated instead of honoring it.'),
    'hollow.enabled': Field('bool', tier='advanced', risk='caution', help='Voxel-hollow the solid before export, leaving a shell of the configured wall thickness.'),
    'hollow.wall_thickness_mm': Field('number', positive=True, help='Depth eroded inward from the outer surface to carve the hollow cavity, in mm.'),
    'hollow.voxel_size_mm': Field('number', tier='expert', risk='caution', unit='mm', help='Voxel pitch for hollowing, in mm. 0 derives it from the wall thickness.'),
    'hollow.mode': Field('choice', choices=('inner', 'bottom_open'), message='hollow.mode must be inner or bottom_open', help='inner keeps the cavity fully enclosed. bottom_open extends it through the base of the part so it opens to the exterior there instead of being sealed.'),
    'hollow.min_wall_thickness_mm': Field('number', positive=True, help='Threshold the thickness command checks measured wall regions against. A report floor, not a parameter hollowing itself enforces.'),
    'hollow.drain_diameter_mm': Field('number', positive=True, help='Diameter of the drain hole punched per enclosed cavity so trapped resin can escape, in mm.'),
    'hollow.vent_diameter_mm': Field('number', positive=True, help='Diameter of the vent hole punched per enclosed cavity alongside the drain hole, in mm.'),
    'hollow.infill': Field('choice', choices=('none', 'grid', 'hex', 'gyroid'), message='hollow.infill must be none, grid, hex or gyroid', help='none leaves the hollow cavity open. grid, hex, and gyroid fill it with that lattice at infill_pitch_mm, unioned in before the drain and vent holes are punched.'),
    'hollow.infill_pitch_mm': Field('number', positive=True, help='Lattice cell spacing for the hollow infill, in mm. Only applies when infill is not none.'),
    'support.automatic': Field('bool', tier='simple', flag='--auto-supports', help='Sample downward-facing surfaces automatically. Off leaves only manual, island and correction contacts.'),
    'support.auto_bracing': Field('bool', tier='simple', label='Enable bracing', flag='--auto-bracing', help='Add the diagonal brace network between standing supports after routing. Off leaves unbraced pillars.'),
    'support.allow_part_to_part': Field('bool', tier='advanced', risk='caution', flag='--part-to-part-supports', help='Allow primary supports to anchor on model material. Brace networks require a continuous support-only path to the plate or generated base, unless brace_model_pillars also admits a model-anchored pillar\'s own bottom connector into that grounding.'),
    'support.drop_attached_unroutable': Field('bool', flag='--drop-attached-unroutable', help='After routing, drop contacts that will not fit if they already have material one printer layer below. Island and manual contacts are never dropped.'),
    'support.tree_supports': Field('bool', tier='advanced', risk='caution', flag='--tree-supports', help='Cluster nearby vertical plate supports onto one trunk with branches. Off keeps independent pillars.'),
    'support.contour_supports': Field('bool', tier='advanced', risk='caution', flag='--contour-supports', help='Also sample the outer perimeter of downward-face clusters, not just face centroids and interior lattices.'),
    'support.boundary_supports': Field('bool', tier='advanced', risk='caution', flag='--boundary-supports', help='Also sample open mesh boundary edges (crop cuts). Closed solids add none.'),
    'support.tree_cluster_mm': Field('number', help='Tree cluster radius. 0 uses twice the support spacing.'),
    'support.part_to_part_avoidance': Field('number', tier='advanced', risk='caution', range=(0.0, 1.0), flag='--part-to-part-avoidance', help='0: compare routes equally by length. 1: prefer any available plate route. At 0.5 a model route must be less than half the plate route length.'),
    'support.spacing_mm': Field('number', positive=True, tier='simple', unit='mm', range=(0.2, 50.0), flag='--support-spacing-mm', help='Nominal distance between automatic contacts on a downward surface. Also the scale for several derived limits, including the branch search radius and the default tree cluster.'),
    'support.contact_diameter_mm': Field('number', positive=True, help='Diameter of the tip where it touches the model. The single biggest influence on how much of a mark a support leaves.'),
    'support.penetration_mm': Field('number', help='How far the tip is driven past the contact point into the model, so support and model union into one solid instead of touching shells.'),
    'support.tip_shape': Field('choice', choices=TIP_SHAPES, flag='--tip-shape', help='Top contact shape. Cone tapers from the tip-base diameter to the contact diameter; cylinder keeps the contact diameter.'),
    'support.break_point_diameter_mm': Field('number', flag='--break-point-diameter-mm', help='Optional ball at the top contact for a controlled snap-off. 0 disables it. When set it must be at least the contact diameter and must fit in the tip length plus penetration.'),
    'support.pillar_diameter_mm': Field('number', positive=True, help='Diameter of the straight middle segment of a support. The thin pillar keys can override it for short runs.'),
    'support.tip_length_mm': Field('number', positive=True, help='Length of the tapered tip between the pillar and the contact. Shortened automatically when a contact sits closer to the material below than this.'),
    'support.tip_base_diameter_mm': Field('number', help='Tip cone lower diameter. 0 uses the nominal pillar diameter.'),
    'support.model_anchor_shape': Field('choice', choices=TIP_SHAPES, flag='--model-anchor-shape', help='Shape of the bottom connector between the lower model surface and the middle pillar. Cone tapers from the middle pillar radius down to the buried endpoint; cylinder keeps one diameter throughout.'),
    'support.model_anchor_length_mm': Field('number', help='How far the bottom connector rises above the lower model surface before the middle pillar takes over. 0 removes the separate connector, so the middle pillar meets the surface directly.'),
    'support.model_anchor_diameter_mm': Field('number', help='Diameter of the bottom connector where it is buried in the lower model, which is the mark it leaves on that surface. 0 derives it from the middle pillar diameter. Needs a nonzero anchor length.'),
    'support.model_anchor_penetration_mm': Field('number', help='How far the bottom connector is buried below the lower model surface, so the two form one solid rather than touching shells. Separate from the top contact penetration. The surface height comes from the analysis raster, so it is only as exact as that pitch.'),
    'support.pillar_angle_deg': Field('number', positive=True, flag='--pillar-angle-deg', help='Shallowest angle from horizontal an angled branch may take when the column directly below a contact is blocked. 45 gives equal sideways travel and drop.'),
    'support.small_pillar_diameter_mm': Field('number', help='Diameter used instead of the nominal pillar diameter for runs within the thin pillar maximum length. 0 disables the thin pillar class entirely; it must be set together with that maximum length.'),
    'support.small_pillar_max_length_mm': Field('number', help='Runs no longer than this use the thin pillar diameter instead of the nominal pillar diameter. 0 disables the thin pillar class entirely; it must be set together with the thin diameter.'),
    'support.small_pillar_mode': Field('choice', choices=SMALL_PILLAR_MODES, flag='--small-pillar-mode', help='Where the thin pillar class applies. Middle: the straight middle segment of any pillar short enough, part-to-part or not. Model: only the connector bridging a short model-to-model gap.'),
    'support.small_pillar_shape': Field('choice', choices=TIP_SHAPES, flag='--small-pillar-shape', help='Buried end shape for a thin model connector: cone tapers to a point; cylinder keeps the shaft diameter. Both use the independent upper and lower depths below.'),
    'support.small_pillar_upper_depth_mm': Field('number', help='How far a thin model connector is buried into the upper model surface. Requires model mode; 0 stops at the surface.'),
    'support.small_pillar_lower_depth_mm': Field('number', help='How far a thin model connector is buried into the lower model surface. Requires model mode; 0 stops at the surface.'),
    'support.brace_spacing_mm': Field('number', positive=True, tier='simple', risk='caution', unit='mm', range=(0.1, 200.0), flag='--brace-spacing-mm', help='Vertical spacing between downward brace origins, measured from each support shoulder. Default 5 mm.'),
    'support.brace_diameter_mm': Field('number', tier='advanced', risk='caution', unit='mm', range=(0.0, 20.0), flag='--brace-diameter-mm', help='Brace diameter. 0 derives half the thinner adjoining pillar diameter.'),
    'support.brace_max_distance_mm': Field('number', tier='simple', risk='caution', unit='mm', range=(0.0, 200.0), flag='--brace-max-distance-mm', help='Maximum neighbor distance for finding an existing support destination. 0 derives 1.5 times primary support spacing.'),
    'support.brace_max_length_mm': Field('number', positive=True, tier='simple', risk='caution', unit='mm', range=(0.1, 200.0), flag='--brace-max-length-mm', help='Maximum complete downward brace length, including the diagonal connection. Default 30 mm; candidates that cannot reach a valid grounded support or plate landing are omitted.'),
    'support.brace_destination': Field('choice', choices=BRACE_DESTINATIONS, tier='simple', label='Brace destinations (supports / base / both)', flag='--brace-destination', help='Supports only: grounded support network. Base only: new checked feet. Supports or base: try grounded supports first. Model parts never anchor braces.'),
    'support.brace_pattern': Field('choice', choices=BRACE_PATTERNS, tier='simple', label='Bracing pattern', flag='--brace-pattern', help='Single diagonals; alternating XY directions at successive levels; or paired X diagonals between vertical shaft spans. X crossings share a junction. Base feet use fan branches in every pattern.'),
    'support.brace_branches_per_node': Field('int', minimum=1, maximum=8, message='support.brace_branches_per_node must be an integer from 1 to 8', tier='simple', label='Brace connections per node', range=(1, 8), flag='--brace-branches-per-node', help='Maximum distinct neighbour connections per vertical spacing interval, 1–8. Incoming connections count too. Each X pair consumes one slot. Clearance can reduce the result.'),
    'support.brace_angle_deg': Field('number', positive=True, tier='simple', unit='deg', range=(0.1, 89.9), flag='--brace-angle-deg', help='Downward angle from horizontal, between 0 and 90 degrees. Default 45 gives equal horizontal travel and vertical drop.'),
    'support.brace_min_height_mm': Field('number', tier='advanced', unit='mm', range=(0.0, 200.0), flag='--brace-min-height-mm', help='Minimum origin height above the plate, or, for a model-standing pillar with brace_model_pillars on, above that pillar\'s own foot on the model. 0 allows every shoulder-derived level; default 3 mm.'),
    'support.brace_azimuth_deg': Field('number', minimum=-360, maximum=360, message='support.brace_azimuth_deg must be between -360 and 360', tier='advanced', unit='deg', range=(-360.0, 360.0), flag='--brace-azimuth-deg', help='Rotate base fans and the alternating direction axis around Z, in degrees.'),
    'support.brace_model_pillars': Field('bool', tier='simple', label='Allow braces to join pillars that stand on the model', flag='--brace-model-pillars', help='Off (default): bracing only reaches vertical shaft edges grounded through a support-only path to the plate or generated base, so a pillar anchored on the model is never braced. On: a model-anchored pillar that has a bottom connector is also treated as grounded, starting from the top of that connector, so it can send and receive braces like a plate pillar. A model pillar with no separate bottom connector, and a thin whole model-to-model pillar, are still never braced.'),
    'support.base_type': Field('choice', choices=BASE_TYPES, tier='simple', flag='--base-type', help='grid is the default porous lattice. plate is a solid hull with a 30 degree outer bevel. none is feet only.'),
    'support.raft_slope_deg': Field('number', help='Plate outer-perimeter wall from the plate, for a putty knife. 0 is a near-vertical rim.'),
    'support.base_touch_diameter_mm': Field('number', help='Pad and per-foot footprint diameter. 0 derives it from the raft expansion.'),
    'support.base_thickness_mm': Field('number', help='Pad and per-foot footprint thickness. 0 derives it from raft thickness.'),
    'support.base_skate_length_mm': Field('number', help='Skate capsule total length. 0 derives the length from the touch diameter.'),
    'support.base_rotation_deg': Field('number', minimum=-360, maximum=360, message='support.base_rotation_deg must be between -360 and 360', help='Skate orientation around each foot, or grid orientation, in degrees.'),
    'support.base_strut_width_mm': Field('number', help='Skeleton/grid strut width. 0 derives the width from the nominal pillar diameter.'),
    'support.base_cell_size_mm': Field('number', positive=True, help='Lattice cell spacing, center to center, in mm. Grid and hex share it.'),
    'support.base_edge_slope_deg': Field('number', help='Base wall angle from the plate, widest where it touches. 0 keeps a vertical wall; the taper is quantised to printed layers.'),
    'support.min_tip_length_mm': Field('number', positive=True, help='Shortest tip the router may emit when a contact sits too close to the material below for a full taper. The contact is still reached; the cone just starts lower.'),
    'support.raft_thickness_mm': Field('number', positive=True, help='Thickness of the raft the supports stand on.'),
    'support.raft_expansion_mm': Field('number', help='How far the raft extends beyond the outline of the support feet.'),
    'support.max_slenderness': Field('number', positive=True, help='Length-to-diameter limit above which a pillar is reported as slender. Guidance from geometry, not a fitted strength model.'),
    'support.max_span_mm': Field('number', positive=True, help='Largest unsupported span reported between neighbouring contacts on one surface.'),
    'support.min_overlap_pixels': Field('number', positive=True, integer=True, help='Fewest printed pixels a support must share with the model to count as attached in the raster check.'),
    'support.overhang_angle_deg': Field('number', positive=True, tier='simple', unit='deg', range=(1.0, 89.0), flag='--overhang-angle-deg', help='Surfaces steeper than this from horizontal are treated as self-supporting and get no automatic contacts.'),
    'support.support_clearance_mm': Field('number', positive=True, help='Sideways gap a support shaft or brace must keep from model material to be accepted. A collision check, not a length adjustment: it never shortens a strut.'),
    'support.max_island_passes': Field('int', minimum=1, maximum=10, message='support.max_island_passes must be an integer from 1 to 10', help='How many times routing may add contacts under newly found islands before reporting what is left. Each pass is a full route plus an assembly, so this is a time limit as much as a policy.'),
    'support.max_contact_gap_mm': Field('number', help='Largest gap allowed between neighbouring contacts before coverage is reported as failing. 0 derives the limit from the support spacing.'),
    'support.max_contact_load_mm2': Field('number', help='Largest downward area one contact is allowed to carry before coverage is reported as failing. 0 derives the limit from the support spacing.'),
    'peel.enabled': Field('bool', tier='advanced', risk='uncalibrated', flag='--peel-analysis', help='Run the peel-risk advisory check. Uncalibrated: it screens geometry and is not fitted to measured peel forces.'),
    'peel.max_angle_deg': Field('number', below=90, message='peel.max_angle_deg must be less than 90', tier='advanced', risk='uncalibrated', unit='deg', help='Downward-facing angle, from straight down, within which triangle normals are grouped into a peel-risk region. Must be less than 90.'),
    'peel.area_threshold_mm2': Field('number', positive=True, tier='advanced', risk='uncalibrated', unit='mm²', help='Projected area a grouped peel-risk region must reach to produce an advisory warning, in mm2.'),
    'peel.reference_lift_speed': Field('number', positive=True, tier='expert', risk='uncalibrated', help='Baseline lift speed the peel-risk score speed ratio is normalized against, in the same native units as the motion table fields. Uncalibrated: a starting assumption, not a measured safe process.'),
    'repair.seal_voids': Field('bool', tier='simple', flag='--seal-voids', help='Fill enclosed voids found in the assembled solid after an exact union, instead of only reporting them. Needs the exact solid path or repair.aggressiveness = aggressive; on the raster union path this stage does not run.'),
    'repair.min_orifice_area_mm2': Field('number', tier='simple', unit='mm²', range=(0.0, 100.0), flag='--min-orifice-area-mm2', help='Smallest circular-equivalent drainage opening area, in mm2, that counts as draining a chamber. 0 skips the drainage check entirely.'),
    'repair.aggressiveness': Field('choice', choices=('none', 'conservative', 'aggressive'), message='repair.aggressiveness must be none, conservative, or aggressive', tier='simple', flag='--repair', help='none skips solid conversion and keeps the authored triangles. conservative tries an exact, lossless Manifold conversion and falls back to a raster union per assembly.union. aggressive rebuilds the model from an occupancy voxelization when the exact conversion is rejected.'),
    'repair.max_deviation_mm': Field('number', flag='--max-deviation-mm', help='Largest surface deviation voxel repair may introduce, in mm, checked against the original triangles in both directions after repair.'),
    'repair.remove_tiny_features': Field('bool', tier='expert', risk='caution', help='Reserved and not implemented; must stay false, or settings validation rejects it.'),
    'repair.auto_drain_holes': Field('bool', help='Reserved and not implemented; must stay false, or settings validation rejects it.'),
    'repair.voxel_size_mm': Field('number', tier='expert', risk='caution', unit='mm', range=(0.0, 5.0), flag='--repair-voxel-mm', help='Voxel pitch for voxel-based repair, in mm. 0 derives it from the maximum deviation within the memory budget.'),
    'repair.smooth_iterations': Field('int', minimum=0, maximum=64, message='repair.smooth_iterations must be an integer from 0 to 64', tier='expert', risk='caution', range=(0, 50), help='Taubin smoothing passes applied after voxel repair, from 0 to 64. 0 disables smoothing; more passes need deviation headroom under max_deviation_mm.'),
    'repair.min_void_volume_mm3': Field('number', tier='expert', risk='caution', unit='mm³', help='Smallest enclosed void or drainage-bottleneck volume, in mm3, that is reported as a failure. 0 reports every enclosed void regardless of size.'),
    'repair.step_linear_deflection_mm': Field('number', positive=True, help='Tessellation tolerance used when importing STEP and other CAD files, in mm. Must be positive.'),
    'repair.weld_tolerance_mm': Field('number', maximum=0.05, message='repair.weld_tolerance_mm must be <= 0.05', tier='expert', risk='caution', unit='mm', range=(0.0, 0.05), help='Radius within which near-duplicate STEP/CAD tessellation vertices are welded together on import, in mm. 0 keeps exact-coordinate matching only; capped at 0.05 mm.'),
    'repair.support_void_policy': Field('choice', choices=SUPPORT_VOID_POLICIES, message='repair.support_void_policy must be fail, ignore, or fill', flag='--support-void-policy', help='fail blocks export on any enclosed void or drainage bottleneck. ignore drops support-class findings only; a hollow model cavity still fails. fill seals enclosed shells after an exact union and re-validates; drainage necks still need tip geometry, not a volume floor.'),
    'assembly.union': Field('choice', choices=('auto', 'exact'), message='assembly.union must be auto or exact', help='auto tries an exact solid union first and falls back to raster occupancy on a geometric rejection. exact requires the solid path and refuses that fallback.'),
    'assembly.require_raster_parity': Field('bool', tier='expert', risk='caution', help='Require the raster union path to be checked against reopened STL group masks. Disabling it records the parity check as not_run, which cannot pass validation.'),
    'assembly.max_parity_examples': Field('int', minimum=0, maximum=256, message='assembly.max_parity_examples must be an integer from 0 to 256', help='Most raster/mask mismatch examples recorded in the parity report, from 0 to 256.'),
    'assembly.clip_to_build_volume': Field('bool', tier='simple', risk='caution', flag='--clip-to-build-volume', help='Off refuses a part outside the reachable envelope of the printer with goo_envelope, changing nothing. On, prepare and slice proceed against only the reachable geometry and record how much was discarded, but the export still needs --allow-unresolved because the clipped_geometry diagnostic is error severity.'),
    'resources.memory_gib': Field('number', minimum=0.25, tier='advanced', unit='GiB', range=(0.25, 1024.0), flag='--memory-gib', help='Memory budget assumed available for analysis and repair, in GiB. Minimum 0.25; several voxel and grid operations size themselves against it.'),
    'resources.workers': Field('int', minimum=0, maximum=32, message='resources.workers must be an integer from 0 to 32, where 0 derives it from the machine', tier='advanced', range=(0, 32), flag='--workers', help='Number of worker CPUs to use, from 0 to 32. 0 derives the count from the physical cores of the machine.'),
    'resources.worker_policy': Field('choice', choices=('performance', 'efficiency', 'all'), message='resources.worker_policy must be performance, efficiency, or all', tier='advanced', flag='--worker-policy', help='performance picks P-cores first. efficiency picks E-cores first, for background work that must not stall an interactive session. all uses every allowed CPU with no restriction.'),
    'resources.scratch_dir': Field('optional_text', message='resources.scratch_dir must be null or a nonempty path string', tier='expert', flag='--scratch-dir', help='Directory used for temporary files during prepare and STL repair. Null uses the system default temp location.'),
    'resources.acceleration': Field('choice', choices=('auto', 'cpu', 'cuda'), message='resources.acceleration must be auto, cpu, or cuda', tier='advanced', flag='--acceleration', help='auto uses CUDA only after a successful runtime and device probe and otherwise falls back to CPU. cpu is the deterministic fallback. cuda is refused outright when no usable device is found.'),
    'resources.cuda_device': Field('int', minimum=0, message='resources.cuda_device must be a nonnegative integer', tier='advanced', range=(0, 16), flag='--cuda-device', help='CUDA device index to probe and use when acceleration is auto or cuda. Must be a nonnegative integer.'),
    'resources.post_slice_hook': Field('optional_text', message='resources.post_slice_hook must be null or a nonempty command string', tier='expert', risk='caution', help='Optional shell command run after a successful slice/GOO export, given the output and report paths as VOXELMILL_OUTPUT and VOXELMILL_REPORT environment variables with a 120 second timeout. Null runs nothing; never inherited from a printer or resin profile.'),
}
