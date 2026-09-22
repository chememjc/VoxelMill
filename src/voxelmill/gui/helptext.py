"""One place for the per-field explanations both GUI surfaces show.

The dedicated printer/resin/support editors and the generated settings table
used to document fields separately, which is how the part-to-part and
thin-pillar keys ended up with no explanation in either surface. Keys are the
bare field name, the same way the editors look them up.
"""
from __future__ import annotations

#: ``key -> one sentence saying what the field actually does``.
#:
#: A key is either a bare field name, which any section may match, or a
#: ``section.field`` path, which wins over the bare name. Four field names
#: repeat across sections (``id``, ``name``, ``enabled``, ``voxel_size_mm``),
#: and one sentence covering two meanings serves neither, so those are
#: qualified. Look entries up with :func:`help_for`, never by direct
#: subscript, or the qualified entries are silently skipped.
HELP = {
    'allow_part_to_part': 'Allow primary supports to anchor on model material. Brace networks always require a continuous support-only path to the plate or generated base.',
    'drop_attached_unroutable': 'After routing, drop contacts that will not fit if they already have material one printer layer below. Island and manual contacts are never dropped.',
    'tree_supports': 'Cluster nearby vertical plate supports onto one trunk with branches. Off keeps independent pillars.',
    'contour_supports': 'Also sample the outer perimeter of downward-face clusters, not just face centroids and interior lattices.',
    'boundary_supports': 'Also sample open mesh boundary edges (crop cuts). Closed solids add none.',
    'tree_cluster_mm': 'Tree cluster radius. 0 uses twice the support spacing.',
    'part_to_part_avoidance': '0: compare routes equally by length. 1: prefer any available plate route. '
                              'At 0.5 a model route must be less than half the plate route length.',
    'brace_destination': 'Supports only: grounded support network. Base only: new checked feet. Supports or base: try grounded supports first. Model parts never anchor braces.',
    'brace_pattern': 'Single diagonals; alternating XY directions at successive levels; or paired X diagonals between vertical shaft spans. X crossings share a junction. Base feet use fan branches in every pattern.',
    'brace_branches_per_node': 'Maximum distinct neighbour connections per vertical spacing interval, 1–8. Incoming connections count too. Each X pair consumes one slot. Clearance can reduce the result.',
    'brace_angle_deg': 'Downward angle from horizontal, between 0 and 90 degrees. Default 45 gives equal horizontal travel and vertical drop.',
    'brace_min_height_mm': 'Minimum origin height above the plate. 0 allows every shoulder-derived level.',
    'brace_azimuth_deg': 'Rotate base fans and the alternating direction axis around Z, in degrees.',
    'brace_diameter_mm': 'Brace diameter. 0 derives half the thinner adjoining pillar diameter.',
    'brace_max_distance_mm': 'Maximum neighbor distance for finding an existing support destination. 0 derives 1.5 times primary support spacing.',
    'brace_spacing_mm': 'Vertical spacing between downward brace origins, measured from each support shoulder. Default 15 mm.',
    'brace_max_length_mm': 'Maximum complete downward brace length, including the diagonal connection. Default 30 mm; candidates that cannot reach a valid grounded support or plate landing are omitted.',
    'tip_base_diameter_mm': 'Tip cone lower diameter. 0 uses the nominal pillar diameter.',
    'tip_shape': 'Top contact shape. Cone tapers from the tip-base diameter to the contact diameter; cylinder keeps the contact diameter.',
    'break_point_diameter_mm': 'Optional ball at the top contact for a controlled snap-off. 0 disables it. When set it must be at least the contact diameter and must fit in the tip length plus penetration.',
    # A part-to-part support has three pieces stacked bottom to top: a bottom
    # connector buried in the lower body, the middle pillar, and the tip at the
    # contact. These four keys describe only the bottom connector.
    #
    #        contact  ___/\___   <- tip, contact diameter
    #                    ||        <- middle pillar, pillar diameter
    #        junction  __/\__     <- model_anchor_length_mm above the surface
    #     ~~~~~~~~~~~~~~~~~~~~~~   lower model surface
    #                   \/         <- model_anchor_penetration_mm below it,
    #                                 model_anchor_diameter_mm across
    'model_anchor_shape': 'Shape of the bottom connector between the lower model surface and the middle pillar. Cone tapers from the middle pillar radius down to the buried endpoint; cylinder keeps one diameter throughout.',
    'model_anchor_length_mm': 'How far the bottom connector rises above the lower model surface before the middle pillar takes over. 0 removes the separate connector, so the middle pillar meets the surface directly.',
    'model_anchor_diameter_mm': 'Diameter of the bottom connector where it is buried in the lower model, which is the mark it leaves on that surface. 0 derives it from the middle pillar diameter. Needs a nonzero anchor length.',
    'model_anchor_penetration_mm': 'How far the bottom connector is buried below the lower model surface, so the two form one solid rather than touching shells. Separate from the top contact penetration. The surface height comes from the analysis raster, so it is only as exact as that pitch.',
    # The thin-pillar class is independent of part-to-part in "middle" mode,
    # even though the editor groups it nearby.
    'small_pillar_mode': 'Where the thin pillar class applies. Middle: the straight middle segment of any pillar short enough, part-to-part or not. Model: only the connector bridging a short model-to-model gap.',
    'small_pillar_diameter_mm': 'Diameter used instead of the nominal pillar diameter for runs within the thin pillar maximum length. 0 disables the thin pillar class entirely; it must be set together with that maximum length.',
    'small_pillar_max_length_mm': 'Runs no longer than this use the thin pillar diameter instead of the nominal pillar diameter. 0 disables the thin pillar class entirely; it must be set together with the thin diameter.',
    'small_pillar_shape': 'Buried end shape for a thin model connector: cone tapers to a point; cylinder keeps the shaft diameter. Both use the independent upper and lower depths below.',
    'small_pillar_upper_depth_mm': 'How far a thin model connector is buried into the upper model surface. Requires model mode; 0 stops at the surface.',
    'small_pillar_lower_depth_mm': 'How far a thin model connector is buried into the lower model surface. Requires model mode; 0 stops at the surface.',
    # Core support dimensions and routing limits. Every option the editor
    # shows needs an explanation, or the hover text is just its own name back.
    'automatic': 'Sample downward-facing surfaces automatically. Off leaves only manual, island and correction contacts.',
    'auto_bracing': 'Add the diagonal brace network between standing supports after routing. Off leaves unbraced pillars.',
    'spacing_mm': 'Nominal distance between automatic contacts on a downward surface. Also the scale for several derived limits, including the branch search radius and the default tree cluster.',
    'contact_diameter_mm': 'Diameter of the tip where it touches the model. The single biggest influence on how much of a mark a support leaves.',
    'penetration_mm': 'How far the tip is driven past the contact point into the model, so support and model union into one solid instead of touching shells.',
    'pillar_diameter_mm': 'Diameter of the straight middle segment of a support. The thin pillar keys can override it for short runs.',
    'tip_length_mm': 'Length of the tapered tip between the pillar and the contact. Shortened automatically when a contact sits closer to the material below than this.',
    'min_tip_length_mm': 'Shortest tip the router may emit when a contact sits too close to the material below for a full taper. The contact is still reached; the cone just starts lower.',
    'pillar_angle_deg': 'Shallowest angle from horizontal an angled branch may take when the column directly below a contact is blocked. 45 gives equal sideways travel and drop.',
    'overhang_angle_deg': 'Surfaces steeper than this from horizontal are treated as self-supporting and get no automatic contacts.',
    'support_clearance_mm': 'Sideways gap a support shaft or brace must keep from model material to be accepted. A collision check, not a length adjustment: it never shortens a strut.',
    'max_slenderness': 'Length-to-diameter limit above which a pillar is reported as slender. Guidance from geometry, not a fitted strength model.',
    'max_span_mm': 'Largest unsupported span reported between neighbouring contacts on one surface.',
    'max_contact_gap_mm': 'Largest gap allowed between neighbouring contacts before coverage is reported as failing. 0 derives the limit from the support spacing.',
    'max_contact_load_mm2': 'Largest downward area one contact is allowed to carry before coverage is reported as failing. 0 derives the limit from the support spacing.',
    'min_overlap_pixels': 'Fewest printed pixels a support must share with the model to count as attached in the raster check.',
    'max_island_passes': 'How many times routing may add contacts under newly found islands before reporting what is left. Each pass is a full route plus an assembly, so this is a time limit as much as a policy.',
    'raft_thickness_mm': 'Thickness of the raft the supports stand on.',
    'raft_expansion_mm': 'How far the raft extends beyond the outline of the support feet.',
    'base_skate_length_mm': 'Skate capsule total length. 0 derives the length from the touch diameter.',
    'base_rotation_deg': 'Skate orientation around each foot, or grid orientation, in degrees.',
    'base_strut_width_mm': 'Skeleton/grid strut width. 0 derives the width from the nominal pillar diameter.',
    'base_cell_size_mm': 'Lattice cell spacing, center to center, in mm. Grid and hex share it.',
    'base_edge_slope_deg': 'Base wall angle from the plate, widest where it touches. '
                           '0 keeps a vertical wall; the taper is quantised to printed layers.',
    'raft_slope_deg': 'Plate outer-perimeter wall from the plate, for a putty knife. 0 is a near-vertical rim.',
    'base_type': 'grid is the default porous lattice. plate is a solid hull with a 30 degree outer bevel. none is feet only.',
    'base_touch_diameter_mm': 'Pad and per-foot footprint diameter. 0 derives it from the raft expansion.',
    'base_thickness_mm': 'Pad and per-foot footprint thickness. 0 derives it from raft thickness.',
    'density_g_cm3': 'Resin density. 0 means unknown; no weight is estimated.',
    'cost_per_liter': 'Resin price per liter in the chosen currency. 0 means unknown.',
    'build_mm': 'Build width, depth, height in mm, as a JSON array. Width/depth must match pixels times pitch.',
    'pixels': 'LCD width and height in pixels, as a JSON array of integers.',
    'pixel_pitch_mm': 'Pixel width and height in mm, as a JSON array.',
    'layer_height_range_mm': 'Hard minimum and maximum layer height in mm, as a JSON array.',
    'motion': 'Reference motion fields as a JSON object; these are not a calibrated timing or strength model.',
    'schema_version': 'Version of the settings layout itself, so an older project or profile can be read and migrated. Not a print setting.',
    # Printer and resin profile identity. id and name are each shared by both
    # sections since HELP is keyed by the bare field name.
    'printer.id': 'Printer identifier, matched against the resin process table to select the exposure and motion settings for this machine.',
    'resin.id': 'Resin identifier, recording which resin these settings belong to in saved profiles and project files.',
    'printer.name': 'Machine name, written into the GOO or CTB header.',
    'resin.name': 'Resin profile name, written into the GOO or CTB header.',
    'edge_clearance_mm': 'Margin kept clear from the build plate edges when placing and validating parts, in mm on every side. Twice this value must be less than the shorter build dimension, or no usable build area remains.',
    'image_mirror_x': 'Mirror the exported raster left to right before writing it, to match the LCD orientation of this machine.',
    'image_mirror_y': 'Mirror the exported raster top to bottom before writing it, to match the LCD orientation of this machine.',
    'image_mirror_verified': 'Whether the mirror flags above have been checked against an actual printed part. False keeps a goo_orientation_unverified warning on every export; true suppresses it.',
    'output_formats': 'Export formats this printer profile supports, such as goo. Must be a nonempty list of strings; records the printer format, not hardware verification status.',
    'currency': 'Currency symbol or code used for cost figures. Must be a nonempty ASCII string of at most 8 bytes, the size of the GOO header currency field.',
    # Process: exposure timing and slice-time dimensional compensation.
    'layer_height_mm': 'Sliced layer thickness, in mm. Must fall within printer.layer_height_range_mm.',
    'bottom_exposure_s': 'Exposure time for each bottom layer, in seconds. Longer than the normal exposure so the first layers stick to the plate.',
    'normal_exposure_s': 'Exposure time for each normal layer after the bottom and transition layers, in seconds.',
    'bottom_layers': 'Number of initial layers exposed at bottom_exposure_s for plate adhesion.',
    'transition_layers': 'Number of layers after the bottom layers whose exposure ramps linearly from bottom_exposure_s to normal_exposure_s. 0 jumps straight to normal exposure on the next layer.',
    'bottom_rest_after_exposure_s': 'Dwell time after curing a bottom layer, before the plate lifts.',
    'normal_rest_after_exposure_s': 'Dwell time after curing a normal layer, before the plate lifts.',
    'bottom_settle_before_exposure_s': 'Dwell time after reaching a bottom layer print position, before exposure starts, to let resin settle.',
    'normal_settle_before_exposure_s': 'Dwell time after reaching a normal layer print position, before exposure starts, to let resin settle.',
    'bottom_wait_after_lift_s': 'Dwell time after the lift and retract motion for a bottom layer, before the next layer begins.',
    'normal_wait_after_lift_s': 'Dwell time after the lift and retract motion for a normal layer, before the next layer begins.',
    'elephant_foot_compensation_mm': 'Radius, in mm, shrunk from the exported bottom layers to counter elephant-foot overcure widening, ramping linearly to zero over elephant_foot_layers. 0 disables it; values above 1.0 mm are rejected because they would erase bottom geometry instead of compensating it.',
    'elephant_foot_layers': 'Number of layers the elephant-foot compensation ramps down to zero over. 0 derives the count from bottom_layers.',
    'shrink_percent_xy': 'Slice-time XY scale correction, as a percent, applied about the plate center without changing the source mesh. 0 disables it; uncalibrated.',
    'shrink_percent_z': 'Slice-time Z scale correction, as a percent. 0 disables it; uncalibrated.',
    'tolerance_offset_mm': 'Morphological radius applied to normal layers: positive erodes for over-cure, negative dilates. 0 disables it; uncalibrated.',
    'bottom_tolerance_offset_mm': 'Morphological radius applied to bottom layers only, same convention as tolerance_offset_mm. 0 disables it; uncalibrated.',
    'antialias_levels': 'Grayscale supersampling factor for the exported raster. 1 is binary occupancy; 2 or 4 supersample to coverage grayscale.',
    'antialias_supports': 'Grayscale-antialias support tips as well when antialias_levels is above 1, instead of keeping them binary. Only takes effect where model and support occupancy can be rasterized separately; otherwise the export records antialias_supports_unseparated instead of honoring it.',
    # Repair: mesh/solid conversion policy and voxel-grid parameters.
    # voxel_size_mm is shared with hollow by bare key.
    'seal_voids': 'Fill enclosed voids found in the assembled solid after an exact union, instead of only reporting them. Needs the exact solid path or repair.aggressiveness = aggressive; on the raster union path this stage does not run.',
    'min_orifice_area_mm2': 'Smallest circular-equivalent drainage opening area, in mm2, that counts as draining a chamber. 0 skips the drainage check entirely.',
    'aggressiveness': 'none skips solid conversion and keeps the authored triangles. conservative tries an exact, lossless Manifold conversion and falls back to a raster union per assembly.union. aggressive rebuilds the model from an occupancy voxelization when the exact conversion is rejected.',
    'max_deviation_mm': 'Largest surface deviation voxel repair may introduce, in mm, checked against the original triangles in both directions after repair.',
    'remove_tiny_features': 'Reserved and not implemented; must stay false, or settings validation rejects it.',
    'auto_drain_holes': 'Reserved and not implemented; must stay false, or settings validation rejects it.',
    'hollow.voxel_size_mm': 'Voxel pitch for hollowing, in mm. 0 derives it from the wall thickness.',
    'repair.voxel_size_mm': 'Voxel pitch for voxel-based repair, in mm. 0 derives it from the maximum deviation within the memory budget.',
    'smooth_iterations': 'Taubin smoothing passes applied after voxel repair, from 0 to 64. 0 disables smoothing; more passes need deviation headroom under max_deviation_mm.',
    'min_void_volume_mm3': 'Smallest enclosed void or drainage-bottleneck volume, in mm3, that is reported as a failure. 0 reports every enclosed void regardless of size.',
    'step_linear_deflection_mm': 'Tessellation tolerance used when importing STEP and other CAD files, in mm. Must be positive.',
    'weld_tolerance_mm': 'Radius within which near-duplicate STEP/CAD tessellation vertices are welded together on import, in mm. 0 keeps exact-coordinate matching only; capped at 0.05 mm.',
    'support_void_policy': 'fail blocks export on any enclosed void or drainage bottleneck. ignore drops support-class findings only; a hollow model cavity still fails. fill seals enclosed shells after an exact union and re-validates; drainage necks still need tip geometry, not a volume floor.',
    # Hollow cavity carving. enabled is shared with peel by bare key.
    'hollow.enabled': 'Voxel-hollow the solid before export, leaving a shell of the configured wall thickness.',
    'peel.enabled': 'Run the peel-risk advisory check. Uncalibrated: it screens geometry and is not fitted to measured peel forces.',
    'wall_thickness_mm': 'Depth eroded inward from the outer surface to carve the hollow cavity, in mm.',
    'mode': 'inner keeps the cavity fully enclosed. bottom_open extends it through the base of the part so it opens to the exterior there instead of being sealed.',
    'min_wall_thickness_mm': 'Threshold the thickness command checks measured wall regions against. A report floor, not a parameter hollowing itself enforces.',
    'drain_diameter_mm': 'Diameter of the drain hole punched per enclosed cavity so trapped resin can escape, in mm.',
    'vent_diameter_mm': 'Diameter of the vent hole punched per enclosed cavity alongside the drain hole, in mm.',
    'infill': 'none leaves the hollow cavity open. grid, hex, and gyroid fill it with that lattice at infill_pitch_mm, unioned in before the drain and vent holes are punched.',
    'infill_pitch_mm': 'Lattice cell spacing for the hollow infill, in mm. Only applies when infill is not none.',
    # Peel-risk advisory: uncalibrated screening, not a measured safe process.
    'max_angle_deg': 'Downward-facing angle, from straight down, within which triangle normals are grouped into a peel-risk region. Must be less than 90.',
    'area_threshold_mm2': 'Projected area a grouped peel-risk region must reach to produce an advisory warning, in mm2.',
    'reference_lift_speed': 'Baseline lift speed the peel-risk score speed ratio is normalized against, in the same native units as the motion table fields. Uncalibrated: a starting assumption, not a measured safe process.',
    # Assembly union path and validation gates.
    'union': 'auto tries an exact solid union first and falls back to raster occupancy on a geometric rejection. exact requires the solid path and refuses that fallback.',
    'require_raster_parity': 'Require the raster union path to be checked against reopened STL group masks. Disabling it records the parity check as not_run, which cannot pass validation.',
    'max_parity_examples': 'Most raster/mask mismatch examples recorded in the parity report, from 0 to 256.',
    'clip_to_build_volume': 'Off refuses a part outside the reachable envelope of the printer with goo_envelope, changing nothing. On, prepare and slice proceed against only the reachable geometry and record how much was discarded, but the export still needs --allow-unresolved because the clipped_geometry diagnostic is error severity.',
    # Resources: worker and acceleration budget.
    'memory_gib': 'Memory budget assumed available for analysis and repair, in GiB. Minimum 0.25; several voxel and grid operations size themselves against it.',
    'workers': 'Number of worker CPUs to use, from 0 to 32. 0 derives the count from the physical cores of the machine.',
    'worker_policy': 'performance picks P-cores first. efficiency picks E-cores first, for background work that must not stall an interactive session. all uses every allowed CPU with no restriction.',
    'scratch_dir': 'Directory used for temporary files during prepare and STL repair. Null uses the system default temp location.',
    'acceleration': 'auto uses CUDA only after a successful runtime and device probe and otherwise falls back to CPU. cpu is the deterministic fallback. cuda is refused outright when no usable device is found.',
    'cuda_device': 'CUDA device index to probe and use when acceleration is auto or cuda. Must be a nonnegative integer.',
    'post_slice_hook': 'Optional shell command run after a successful slice/GOO export, given the output and report paths as VOXELMILL_OUTPUT and VOXELMILL_REPORT environment variables with a 120 second timeout. Null runs nothing; never inherited from a printer or resin profile.',
}


#: The eighteen ``printer.motion`` leaves are one shape repeated: a height, a
#: speed or a lamp level, optionally for bottom layers and optionally for a
#: second stage. Describing them by rule keeps the claim about them identical
#: everywhere, which matters more here than anywhere else: they are reference
#: values read from a machine profile, not a fitted model of that machine.
_MOTION_KINDS = (
    ('lift_height', 'How far the platform rises after a layer, in mm'),
    ('lift_speed', 'How fast the platform rises after a layer, in mm/min'),
    ('retract_height', 'How far the platform returns before the next layer, in mm'),
    ('retract_speed', 'How fast the platform returns before the next layer, in mm/min'),
    ('light_pwm', 'Lamp power level for the exposure, 0 to 255'),
)


def _motion_help(field):
    """Generated explanation for one ``printer.motion`` leaf."""
    stage = ' Second stage of a two-stage move.' if field.endswith('2') else ''
    core = field[:-1] if field.endswith('2') else field
    bottom = core.startswith('bottom_')
    if bottom:
        core = core[len('bottom_'):]
    for name, text in _MOTION_KINDS:
        if core == name:
            scope = ' Applies to the bottom layers only.' if bottom else ''
            return (f'{text}.{scope}{stage} Reference value carried from the machine '
                    'profile: uncalibrated, and not a timing or strength model.')
    return None


def help_for(path):
    """Explanation for a ``section.field`` path, or None.

    A qualified entry wins over a bare field name, so a name that repeats
    across sections can say the right thing in each one. The repetitive
    ``printer.motion`` leaves are described by rule instead of by entry.
    """
    if not path:
        return None
    path = str(path)
    if path in HELP:
        return HELP[path]
    if path.startswith('printer.motion.'):
        generated = _motion_help(path.split('.')[-1])
        if generated:
            return generated
    return HELP.get(path.split('.')[-1])
