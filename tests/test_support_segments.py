"""The parametric support segments and base strategies.

Every setting here has to change measurable geometry. A config key that
resolves and validates but moves nothing is exactly the dead flag this project
refuses to ship, so each test asserts a dimension rather than a metric.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
import manifold3d as m

from voxelmill.config import BASE_TYPES, resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.presets import CHITUBOX_UNSET, apply_preset, load_preset
from voxelmill.supports import brace_geometry, build_base, plan_supports

from test_supports import assemble, placed, stacked_with_gap


def sphere_on_stilts():
    return m.Manifold.sphere(6, 64).translate((0, 0, 11))


def settings_with(**support):
    return resolve_settings(overrides={'support': support})


def plan_for(solid, settings):
    triangles, bounds = placed(solid)
    return plan_supports(triangles, bounds, settings)


# ---- top segment: the tip cone ---------------------------------------------

def test_the_tip_cone_base_is_its_own_diameter_not_the_pillar_diameter():
    """These are equal in the reference profile, which is why conflating them hid.

    The cone's lower radius came from ``pillar_diameter_mm``. CHITUBOX calls it
    Tip Down Diameter and keeps it separate from the middle segment's diameter;
    both happen to be 0.80 mm in the known-good configuration.
    """
    solid = sphere_on_stilts()
    wide = settings_with(pillar_diameter_mm=1.2, tip_base_diameter_mm=1.0)
    plan, _raft = plan_for(solid, wide)

    # The tip is the only tapered solid: its lower radius is now 0.5, not 0.6.
    contacts = sorted(n.position_mm[2] for n in plan.graph.nodes if n.kind == 'contact')
    tip_length = wide['support']['tip_length_mm']
    tips = [s for s in plan.solids
            if abs((s.bounding_box()[5] - s.bounding_box()[2])
                   - (tip_length + wide['support']['penetration_mm'])) < 1e-6]
    assert tips, 'no full-length tip solid found'
    for tip in tips:
        x0, _y0, _z0, x1, _y1, _z1 = tip.bounding_box()
        assert (x1 - x0) == pytest.approx(1.0, rel=0.02)
    assert plan.metrics['tip_base_diameter_mm'] == pytest.approx(1.0)
    assert contacts

    # Zero keeps the historical behavior exactly.
    default = settings_with(pillar_diameter_mm=1.2)
    plan, _raft = plan_for(solid, default)
    assert plan.metrics['tip_base_diameter_mm'] == pytest.approx(1.2)


def test_a_contact_wider_than_the_tip_base_is_refused():
    with pytest.raises(VoxelMillError, match='must not exceed tip_base_diameter_mm'):
        settings_with(contact_diameter_mm=0.9, tip_base_diameter_mm=0.5,
                      break_point_diameter_mm=0.0)


# ---- middle segment: angle and the thin pillar class ------------------------

def test_the_branch_angle_is_a_setting_and_costs_reach_when_raised():
    """A steeper branch drops further for the same sideways reach.

    At the historical 45 degrees a branch trades one millimeter of drop per
    millimeter sideways. At 70 it costs tan(70) = 2.75, so the same available
    height reaches less far and fewer contacts can branch.
    """
    solid = stacked_with_gap(6.0)
    shallow, _ = plan_for(solid, settings_with(pillar_angle_deg=30.0))
    steep, _ = plan_for(solid, settings_with(pillar_angle_deg=80.0))
    assert shallow.metrics['pillar_angle_deg'] == 30.0
    assert steep.metrics['pillar_angle_deg'] == 80.0
    assert shallow.metrics['routing']['branched'] >= steep.metrics['routing']['branched']

    # The elbow sits exactly lateral * tan(angle) below the tip base.
    for angle in (35.0, 60.0):
        plan, _raft = plan_for(solid, settings_with(pillar_angle_deg=angle))
        nodes = {n.id: n for n in plan.graph.nodes}
        elbows = [name for name in nodes if name.startswith('elbow')]
        for name in elbows:
            order = name[len('elbow'):]
            elbow, junction = nodes[name], nodes[f'joint{order}']
            lateral = math.dist(elbow.position_mm[:2], junction.position_mm[:2])
            drop = junction.position_mm[2] - elbow.position_mm[2]
            assert drop == pytest.approx(lateral * math.tan(math.radians(angle)), rel=1e-6)


def test_an_out_of_range_branch_angle_is_refused():
    """Two different rules catch it, and each says which one it was.

    Zero and negatives are caught by the generic positive-number rule every
    support value shares; only a finite positive angle reaches the range test.
    """
    for bad in (0.0, -10.0):
        with pytest.raises(VoxelMillError, match='pillar_angle_deg must be > 0'):
            settings_with(pillar_angle_deg=bad)
    for bad in (90.0, 120.0):
        with pytest.raises(VoxelMillError, match='pillar_angle_deg must be between'):
            settings_with(pillar_angle_deg=bad)


def test_a_short_pillar_uses_the_thin_class_and_a_long_one_does_not():
    solid = sphere_on_stilts()
    plain, _raft = plan_for(solid, settings_with(pillar_diameter_mm=1.2))
    assert plain.metrics['small_pillars'] == 0

    # Every pillar under this sphere is longer than 1 mm and shorter than 30.
    thin = settings_with(pillar_diameter_mm=1.2, small_pillar_diameter_mm=0.4,
                         small_pillar_max_length_mm=30.0)
    plan, _raft = plan_for(solid, thin)
    assert plan.metrics['small_pillars'] > 0
    radii = {round(e.radius_mm, 6) for e in plan.graph.edges if e.kind in ('vertical', 'branched')}
    assert radii == {0.2}

    # A threshold below every run leaves the nominal diameter in place.
    unused = settings_with(pillar_diameter_mm=1.2, small_pillar_diameter_mm=0.4,
                           small_pillar_max_length_mm=0.05)
    plan, _raft = plan_for(solid, unused)
    assert plan.metrics['small_pillars'] == 0
    radii = {round(e.radius_mm, 6) for e in plan.graph.edges if e.kind in ('vertical', 'branched')}
    assert radii == {0.6}


def test_the_thin_pillar_class_needs_both_halves_or_neither():
    """A thin pillar with no length limit would silently replace every pillar."""
    for partial in ({'small_pillar_diameter_mm': 0.4}, {'small_pillar_max_length_mm': 5.0}):
        with pytest.raises(VoxelMillError, match='must both be set or both be zero'):
            settings_with(**partial)
    with pytest.raises(VoxelMillError, match='must not exceed pillar_diameter_mm'):
        settings_with(small_pillar_diameter_mm=2.0, small_pillar_max_length_mm=5.0,
                      pillar_diameter_mm=1.2)


# ---- bracing: two numbers, not one -----------------------------------------

def test_brace_spacing_and_length_are_independent():
    settings = resolve_settings()
    pillar_r = settings['support']['pillar_diameter_mm'] / 2
    assert brace_geometry(settings, pillar_r) == (5.0, 30.0)
    assert brace_geometry(settings_with(brace_spacing_mm=12.0), pillar_r) == (12.0, 30.0)
    assert brace_geometry(settings_with(brace_max_length_mm=8.0), pillar_r) == (5.0, 8.0)
    with pytest.raises(VoxelMillError):
        settings_with(brace_spacing_mm=0)


def test_smaller_vertical_spacing_produces_more_downward_braces():
    solid = m.Manifold.sphere(4, 48).translate((0, 0, 64))
    triangles, bounds = placed(solid)
    # Pin the pre-CHITUBOX-Light (looser) brace spacing as the baseline: the
    # current default (5mm) is already tighter than the 8mm case below.
    default, _raft = plan_supports(triangles, bounds, settings_with(brace_spacing_mm=15.0))
    close = settings_with(brace_spacing_mm=8.0, brace_max_length_mm=30.0)
    plan, raft = plan_supports(triangles, bounds, close)
    assert plan.metrics['brace_spacing_mm'] == 8.0
    assert plan.metrics['brace_max_length_mm'] == 30.0
    assert plan.metrics['braces'] > default.metrics['braces']
    assert assemble(solid, plan, raft).status() == m.Error.NoError


# ---- bottom segment: what the supports land on ------------------------------

def test_every_base_strategy_builds_what_it_says_and_measures_itself():
    solid = sphere_on_stilts()
    results = {}
    for kind, extra in (('plate', {}), ('none', {}),
                        ('pad', {'base_touch_diameter_mm': 3.0, 'base_thickness_mm': 0.8})):
        # Pin the pre-CHITUBOX-Light pillar diameter: the bare-feet contact-area
        # formula below assumes the old 1.2mm/0.6mm-radius pillar.
        plan, raft = plan_for(solid, settings_with(base_type=kind, auto_bracing=False,
                                                    pillar_diameter_mm=1.2, **extra))
        results[kind] = (plan.metrics['base'], raft)

    plate, plate_raft = results['plate']
    assert plate['solid'] and plate_raft is not None
    assert len(plate_raft.decompose()) == 1

    bare, bare_raft = results['none']
    assert bare_raft is None and bare['solid'] is False
    assert bare['volume_mm3'] == 0.0
    # Feet only still has a real plate contact: one pillar section each.
    assert bare['contact_area_mm2'] == pytest.approx(
        bare['feet'] * 12 * math.sin(math.pi / 12) * 0.6 ** 2, rel=1e-6)

    pad, pad_raft = results['pad']
    assert pad['solid'] and pad_raft is not None
    assert pad['pad_diameter_mm'] == 3.0 and pad['pad_thickness_mm'] == 0.8
    assert pad_raft.bounding_box()[5] == pytest.approx(0.8, abs=1e-9)
    # The pad base is the middle option on both axes that matter.
    assert 0 < pad['volume_mm3'] < plate['volume_mm3']
    assert bare['contact_area_mm2'] < pad['contact_area_mm2'] < plate['contact_area_mm2']


def test_every_base_strategy_still_assembles_into_one_solid():
    solid = sphere_on_stilts()
    for kind, extra in (('plate', {}), ('pad', {'base_touch_diameter_mm': 4.0}),):
        plan, raft = plan_for(solid, settings_with(base_type=kind, **extra))
        full = assemble(solid, plan, raft)
        assert full.status() == m.Error.NoError, kind
        assert len(full.decompose()) == 1, kind


def test_a_pad_narrower_than_its_pillar_is_refused_rather_than_built():
    solid = sphere_on_stilts()
    with pytest.raises(VoxelMillError, match='must exceed the pillar diameter'):
        plan_for(solid, settings_with(base_type='pad', base_touch_diameter_mm=0.5,
                                      pillar_diameter_mm=1.2))


def test_pad_dimensions_are_refused_on_a_base_that_cannot_use_them():
    for kind in ('plate', 'none'):
        with pytest.raises(VoxelMillError, match='base_touch_diameter_mm and base_thickness_mm apply to'):
            settings_with(base_type=kind, base_touch_diameter_mm=10.0)
    with pytest.raises(VoxelMillError, match='bare feet have no base to taper'):
        settings_with(base_type='none', base_edge_slope_deg=45.0)
    with pytest.raises(VoxelMillError, match='raft_slope_deg'):
        settings_with(base_type='plate', base_edge_slope_deg=45.0)
    with pytest.raises(VoxelMillError, match='base_type must be one of'):
        settings_with(base_type='unknown')


def test_build_base_reports_rather_than_guesses_when_nothing_landed():
    record = build_base([], resolve_settings(), 0.6)
    assert record['solid'] is None
    assert record['record']['feet'] == 0
    assert 'no support anchored on the plate' in record['record']['reason']


# ---- the known-good configuration as a preset -------------------------------

def test_the_chitubox_preset_resolves_to_the_recorded_table():
    preset = load_preset('chitubox-mars5')
    resolved = apply_preset(resolve_settings(), preset)['support']
    assert resolved['penetration_mm'] == 0.30          # Top / Contact Depth
    assert resolved['contact_diameter_mm'] == 0.30     # Top / Tip Upper Diameter
    assert resolved['tip_base_diameter_mm'] == 0.80    # Top / Tip Down Diameter
    assert resolved['tip_length_mm'] == 2.00           # Top / Connection Length
    assert resolved['pillar_diameter_mm'] == 0.80      # Middle / Diameter
    assert resolved['pillar_angle_deg'] == 20.0        # 70 from vertical = 20 from horizontal
    assert resolved['brace_spacing_mm'] == 15.0        # Downward branch vertical interval
    assert resolved['brace_max_length_mm'] == 30.0     # Actual diagonal branch length
    assert resolved['base_touch_diameter_mm'] == 10.0  # Bottom / Touch Diameter
    assert resolved['base_thickness_mm'] == 0.80       # Bottom / Thickness
    assert resolved['base_skate_length_mm'] == 0.0     # elongation still unknown
    assert resolved['base_edge_slope_deg'] == 30.0     # grayed Raft / Slope
    # Raft Shape = None plus Platform Touch Shape = Skate.
    assert resolved['base_type'] == 'skate'


def test_the_chitubox_preset_leaves_undetermined_values_out_and_says_why():
    """A guess inside a preset named after a known-good print is the worst guess."""
    preset = load_preset('chitubox-mars5')
    resolved = apply_preset(resolve_settings(), preset)['support']
    assert set(CHITUBOX_UNSET) == {'small_pillar_max_length_mm', 'spacing_mm',
                                 'base_skate_length_mm', 'model_anchor_diameter_mm'}
    assert resolved['base_type'] == 'skate'
    assert resolved['base_edge_slope_deg'] == 30.0
    assert resolved['base_touch_diameter_mm'] == 10.0
    assert resolved['base_thickness_mm'] == 0.80
    assert resolved['base_skate_length_mm'] == 0.0  # circular; elongation unknown
    assert resolved['small_pillar_diameter_mm'] == 0.4
    assert resolved['small_pillar_mode'] == 'model'
    assert resolved['small_pillar_upper_depth_mm'] == 0.25
    assert resolved['small_pillar_lower_depth_mm'] == 0.25
    assert resolved['small_pillar_max_length_mm'] == 0.0  # still disabled
    assert resolved['spacing_mm'] == resolve_settings()['support']['spacing_mm']
    assert 'spacing_mm' not in preset['support']
    notes = '\n'.join(preset['notes'])
    for key, reason in CHITUBOX_UNSET.items():
        assert len(reason) > 40
        assert key in notes and reason in notes
        assert 'approximation of the reference skate' not in reason.lower()
        assert 'circular pad approximation' not in reason.lower()


def test_the_chitubox_preset_plans_and_assembles_a_real_part():
    solid = sphere_on_stilts()
    settings = apply_preset(resolve_settings(), 'chitubox-mars5')
    plan, raft = plan_for(solid, settings)
    assert plan.metrics['contacts_routed'] > 0
    assert plan.metrics['base']['type'] == 'skate'
    assert plan.metrics['base']['pad_diameter_mm'] == 10.0
    assert plan.metrics['base']['edge_slope_deg'] == 30.0
    assert plan.metrics['tip_base_diameter_mm'] == 0.80
    assert plan.metrics['pillar_angle_deg'] == 20.0
    assert assemble(solid, plan, raft).status() == m.Error.NoError


def test_elbow_sphere_unions_a_45_degree_joint_into_one_solid():
    from voxelmill.support_segments import elbow_sphere
    from voxelmill.geometry import cylinder_between
    elbow = (0.0, 0.0, 4.0)
    run_r = 0.6
    vertical = cylinder_between((0.0, 0.0, 0.0), elbow, run_r)
    angled = cylinder_between(elbow, (4.0, 0.0, 8.0), run_r)
    joint = elbow_sphere(elbow, run_r)
    combined = m.Manifold.batch_boolean([vertical, angled, joint], m.OpType.Add)
    assert combined.status() == m.Error.NoError
    assert combined.volume() > 0
    assert len(combined.decompose()) == 1
    blocked = (m.Manifold.cube((8, 8, 8), True).translate((0, 0, 4))
               + m.Manifold.cube((30, 10, 4), True).translate((0, 0, 22)))
    triangles, bounds = placed(blocked)
    plan, _raft = plan_supports(triangles, bounds, settings_with(
        pillar_angle_deg=45.0, allow_part_to_part=False, automatic=False,
        auto_bracing=False, base_type='none', drop_attached_unroutable=False),
        extra_contacts=[[0.0, 0.0, 20.0]])
    elbows = [n for n in plan.graph.nodes if n.kind == 'elbow']
    assert elbows, 'blocked overhang should branch through an elbow'
    elbow_node = elbows[0]
    spheres = []
    for part in plan.solids:
        x0, y0, z0, x1, y1, z1 = part.bounding_box()
        cx, cy, cz = (x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2
        if (abs(cx - elbow_node.position_mm[0]) < 0.2 and abs(cy - elbow_node.position_mm[1]) < 0.2
                and abs(cz - elbow_node.position_mm[2]) < 0.2):
            spheres.append(part)
    assert spheres, 'no elbow sphere solid at the joint'
    unioned = m.Manifold.batch_boolean(plan.solids, m.OpType.Add)
    assert unioned.status() == m.Error.NoError
    assert len(unioned.decompose()) == 1


def test_the_preset_is_reachable_from_the_cli_by_name(tmp_path, capsys):
    from voxelmill.cli import main
    assert main(['preset', 'show', 'chitubox-mars5']) == 0
    import json
    shown = json.loads(capsys.readouterr().out)
    assert shown['name'] == 'chitubox-mars5'
    assert shown['support']['base_type'] == 'skate'
    assert shown['support']['base_edge_slope_deg'] == 30.0
    assert main(['preset', 'list']) == 0
    assert 'chitubox-mars5' in json.loads(capsys.readouterr().out)['presets']


# ---- reachable from both interfaces ----------------------------------------

def test_the_base_and_branch_angle_reach_the_settings_from_the_command_line():
    from voxelmill.cli import _settings, build_parser
    args = build_parser().parse_args(['prepare', 'in.stl', '--base-type', 'pad',
                                      '--pillar-angle-deg', '70',
                                      '--set', 'support.base_touch_diameter_mm=10'])
    resolved = _settings(args)['support']
    assert resolved['base_type'] == 'pad'
    assert resolved['pillar_angle_deg'] == 70.0
    assert resolved['base_touch_diameter_mm'] == 10.0


def test_the_brace_sizing_flags_reach_the_settings_from_the_command_line():
    from voxelmill.cli import _settings, build_parser
    args = build_parser().parse_args(['prepare', 'in.stl',
                                      '--brace-spacing-mm', '30',
                                      '--brace-max-length-mm', '3',
                                      '--brace-diameter-mm', '0.8',
                                      '--brace-max-distance-mm', '12'])
    resolved = _settings(args)['support']
    assert resolved['brace_spacing_mm'] == 30.0
    assert resolved['brace_max_length_mm'] == 3.0
    assert resolved['brace_diameter_mm'] == 0.8
    assert resolved['brace_max_distance_mm'] == 12.0
    assert all(isinstance(resolved[key], float) for key in (
        'brace_spacing_mm', 'brace_max_length_mm',
        'brace_diameter_mm', 'brace_max_distance_mm'))


def test_the_editor_exposes_the_base_type_and_the_new_preset():
    pytest.importorskip('PySide6')
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6 import QtWidgets
    from voxelmill.gui.window import MainWindow
    from voxelmill.presets import list_presets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert app is not None
    window = MainWindow(resolve_settings(), None, headless=True)
    assert tuple(window.base_type.itemText(i) for i in range(window.base_type.count())) == BASE_TYPES
    # The Setup control names its config key and its flag, like every other.
    assert 'support.base_type' in window.base_type.toolTip()
    assert '--base-type' in window.base_type.toolTip()
    # The preset menu is built from list_presets, so the new one is offered.
    for name in list_presets():
        assert f'preset_{name}' in window.actions_map

    window.base_type.setCurrentText('none')
    window.apply_settings()
    assert window.document.settings['support']['base_type'] == 'none'
    assert window.markers['base_type'].text() == '●'
    window.revert_setting('base_type')
    assert window.document.settings['support']['base_type'] == 'grid'
    window.close()


def test_applying_the_chitubox_preset_in_the_editor_changes_the_base():
    pytest.importorskip('PySide6')
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6 import QtWidgets
    from voxelmill.gui.window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert app is not None
    window = MainWindow(resolve_settings(), None, headless=True)
    window.apply_support_preset('chitubox-mars5')
    support = window.document.settings['support']
    assert support['base_type'] == 'skate' and support['pillar_angle_deg'] == 20.0
    assert support['base_edge_slope_deg'] == 30.0
    assert support['base_touch_diameter_mm'] == 10.0
    assert support['base_thickness_mm'] == 0.80
    assert window.base_type.currentText() == 'skate'
    window.close()


# ---- what the base cannot fix ----------------------------------------------

def test_the_support_drainage_bottleneck_belongs_to_the_tip_not_the_base():
    """Recorded as a pillar/raft crevice for a long time. It is not.

    With or without a base the chambers sit at the model's underside, next to
    a contact, and none sits at the base, so the base is not forming them; the
    bottleneck responds to the tip cone's dimensions instead. A plate can add
    a single-voxel core there, a sampling difference of the analysis grid.
    See gotchas.md.
    """
    from voxelmill.validation import analyze_drainage

    solid = sphere_on_stilts()
    triangles, bounds = placed(solid)

    def bottlenecks(support):
        settings = settings_with(break_point_diameter_mm=0.0, auto_bracing=False,
                                 model_anchor_length_mm=0.0, model_anchor_diameter_mm=0.0,
                                 model_anchor_penetration_mm=0.0, **support)
        plan, raft = plan_supports(triangles, bounds, settings)
        parts = plan.solids + ([raft] if raft is not None else [])
        full = m.Manifold.batch_boolean(
            [solid, m.Manifold.batch_boolean(parts, m.OpType.Add)], m.OpType.Add)
        box = np.asarray(full.bounding_box()).reshape(2, 3)
        from voxelmill.geometry import manifold_triangles
        drain = analyze_drainage(manifold_triangles(full).astype(np.float32), box, settings)
        return drain

    plate = bottlenecks({'base_type': 'plate'})
    bare = bottlenecks({'base_type': 'none'})
    assert bare['bottlenecked_components'] <= plate['bottlenecked_components']
    for drain in (plate, bare):
        pitch = drain['analysis_pitch_mm']
        z0 = 0.0 - 2 * pitch
        for example in drain['bottleneck_examples']:
            z = z0 + (example['seed_zyx'][0] + 0.5) * pitch
            assert 4.0 < z < 6.0, 'bottleneck is not at the sphere underside near z=5'
    # A tip with no taper (base diameter equal to the contact) leaves no neck.
    # Pin the pre-CHITUBOX-Light contact diameter so it still equals the tip
    # base diameter asserted below.
    assert bottlenecks({'base_type': 'none', 'contact_diameter_mm': 0.4,
                        'tip_base_diameter_mm': 0.4})['bottlenecked_components'] == 0
    # And the model on its own has none at all.
    from voxelmill.geometry import manifold_triangles
    box = np.asarray(solid.bounding_box()).reshape(2, 3)
    alone = analyze_drainage(manifold_triangles(solid).astype(np.float32), box,
                             resolve_settings())
    assert alone['bottlenecked_components'] == 0
