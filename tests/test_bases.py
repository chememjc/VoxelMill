"""Geometric, connectivity, and integration evidence for support base strategies."""
import itertools
import json
import math

import numpy as np
import pytest
import manifold3d as m

from voxelmill.bases import build_base, minimum_spanning_edges, MAX_GRID_LINES
from voxelmill.config import BASE_TYPES, resolve_settings
from voxelmill.contracts import VoxelMillError, CancellationToken, Canceled
from voxelmill.geometry import _reserve, manifold_triangles, raft_from_feet
from voxelmill.supports import plan_supports
from voxelmill.cli import main, build_parser
from test_supports import placed, assemble

FEET = np.array([[-10, -10], [-10, 10], [10, -10], [10, 10]], dtype=float)


def settings_for(kind, **parameters):
    return resolve_settings(overrides={'support': {'base_type': kind, **parameters}})


def base(kind, feet=FEET, **parameters):
    return build_base(feet, settings_for(kind, **parameters), .6)


def test_skate_total_length_width_thickness_and_rotation_are_independent():
    for angle, expected in [(0, [12, 3, .8]), (90, [3, 12, .8]), (-90, [3, 12, .8])]:
        result = base('skate', feet=[[0, 0]], base_touch_diameter_mm=3,
                      base_skate_length_mm=12, base_rotation_deg=angle, base_thickness_mm=.8)
        solid = result['solid']
        bounds = np.asarray(solid.bounding_box()).reshape(2, 3)
        np.testing.assert_allclose(bounds[1] - bounds[0], expected, atol=1e-7)
        assert bounds[0, 2] == pytest.approx(0)
        # A rectangle plus the polygonal circle formed by both capsule ends.
        expected_area = 9 * 3 + 16 * math.sin(math.pi / 16) * 1.5 ** 2
        assert result['record']['contact_area_mm2'] == pytest.approx(expected_area, rel=1e-7)
        assert solid.volume() == pytest.approx(expected_area * .8, rel=1e-7)


def test_zero_skate_length_means_a_round_foot_not_guessed_reference_elongation():
    result = base('skate', feet=[[0, 0]], base_touch_diameter_mm=4)
    bounds = np.asarray(result['solid'].bounding_box()).reshape(2, 3)
    np.testing.assert_allclose((bounds[1] - bounds[0])[:2], [4, 4], atol=1e-7)
    assert result['record']['skate_length_mm'] == 4


@pytest.mark.parametrize('kind', ['skeleton', 'grid'])
def test_connected_base_is_smaller_than_the_solid_plate_and_touches_every_foot(kind):
    result = base(kind, base_touch_diameter_mm=2.4, base_strut_width_mm=.8,
                  base_thickness_mm=.8, base_cell_size_mm=6)
    solid, record = result['solid'], result['record']
    plate = base('plate')['solid']
    assert solid.status() == m.Error.NoError
    assert len(solid.decompose()) == record['connected_components'] == 1
    assert record['skeleton_edges'] == 3
    assert record['skeleton_length_mm'] == pytest.approx(60)
    assert record['volume_mm3'] < plate.volume() * .6
    assert record['open_area_fraction'] > .4
    assert record['volume_mm3'] == pytest.approx(record['contact_area_mm2'] * .8)
    for x, y in FEET:
        contact = m.Manifold.cylinder(.8, .6, .6, 24).translate((x, y, 0))
        # Full lower pillar fits inside the connected base.
        assert (contact - solid).volume() == pytest.approx(0, abs=1e-8)


def test_grid_connections_reach_feet_between_lines_and_include_a_perimeter():
    feet = [[-10, -9], [9, -8], [11, 10], [-8, 11], [.37, 1.71]]
    result = base('grid', feet=feet, base_touch_diameter_mm=2,
                  base_strut_width_mm=.7, base_cell_size_mm=5, base_rotation_deg=27)
    assert result['record']['connected_components'] == 1
    assert result['record']['skeleton_edges'] == 4
    assert result['record']['perimeter_width_mm'] == .7
    assert result['record']['grid_candidate_lines'] > 0
    assert len(result['solid'].slice(.5).to_polygons()) > 1  # genuine through holes


def test_grid_spacing_and_width_change_measured_geometry():
    dense = base('grid', base_touch_diameter_mm=2.4, base_cell_size_mm=3)['record']
    coarse = base('grid', base_touch_diameter_mm=2.4, base_cell_size_mm=8)['record']
    thin = base('grid', base_touch_diameter_mm=2.4, base_cell_size_mm=8,
                base_strut_width_mm=.5)['record']
    assert dense['volume_mm3'] > coarse['volume_mm3'] > thin['volume_mm3']
    assert dense['open_area_fraction'] < coarse['open_area_fraction'] < thin['open_area_fraction']


def test_tree_is_minimum_length_against_independent_dense_graph_solution():
    from scipy.spatial.distance import cdist
    from scipy.sparse.csgraph import minimum_spanning_tree
    points = np.unique([[0, 0], [3, 2], [1, 7], [6, -1], [8, 5], [5, 5]], axis=0)
    edges = minimum_spanning_edges(points)
    length = sum(np.linalg.norm(points[a] - points[b]) for a, b in edges)
    expected = minimum_spanning_tree(cdist(points, points)).sum()
    assert len(edges) == len(points) - 1
    assert length == pytest.approx(float(expected))


@pytest.mark.parametrize('kind', ['skate', 'skeleton', 'grid'])
@pytest.mark.parametrize('feet,unique', [([[1, 2]], 1), ([[1, 2], [1, 2]], 1),
                                        ([[0, 0], [5, 0]], 2),
                                        ([[0, 0], [5, 0], [10, 0]], 3)])
def test_single_duplicate_two_and_collinear_feet_are_real_geometry(kind, feet, unique):
    result = base(kind, feet=feet, base_touch_diameter_mm=2.4)
    assert result['solid'].status() == m.Error.NoError
    assert result['solid'].volume() > 0
    assert result['record']['unique_feet'] == unique
    if kind != 'skate':
        assert result['record']['skeleton_edges'] == unique - 1
        assert result['record']['connected_components'] == 1


def test_tree_and_base_are_invariant_to_contact_input_order():
    one = base('skeleton', base_touch_diameter_mm=2.4)
    two = base('skeleton', feet=FEET[::-1], base_touch_diameter_mm=2.4)
    np.testing.assert_array_equal(manifold_triangles(one['solid']), manifold_triangles(two['solid']))
    assert one['record'] == two['record']


def test_bare_feet_count_overlap_once_and_use_the_actual_short_pillar_radius():
    settings = settings_for('none')
    result = build_base([[0, 0], [0, 0]], settings, .6, foot_radii=[.2, .3])
    # Concentric duplicate footprints are just the larger polygon, not two discs.
    assert result['record']['contact_area_mm2'] == pytest.approx(12 * math.sin(math.pi / 12) * .3**2)
    assert result['record']['connected_components'] == 1
    solid = m.Manifold.sphere(4, 32).translate((0, 0, 10))
    triangles, bounds = placed(solid)
    settings = settings_for('none', small_pillar_diameter_mm=.4, small_pillar_max_length_mm=50)
    plan, _ = plan_supports(triangles, bounds, settings)
    assert plan.metrics['small_pillars'] == plan.metrics['contacts_routed']
    assert plan.metrics['base']['contact_area_mm2'] == pytest.approx(
        plan.metrics['base']['feet'] * 12 * math.sin(math.pi / 12) * .2**2)


def test_original_plate_geometry_stays_bit_identical():
    expected = raft_from_feet(FEET, .6, 2, 1, .25, slope_deg=0)
    actual = base('plate', raft_slope_deg=0)['solid']
    np.testing.assert_array_equal(manifold_triangles(actual), manifold_triangles(expected))


def test_plate_outer_bevel_keeps_plate_contact_and_insets_the_top():
    vertical = base('plate', raft_slope_deg=0)
    sloped = base('plate', raft_slope_deg=30)
    assert vertical['record']['contact_area_mm2'] == pytest.approx(sloped['record']['contact_area_mm2'])
    assert sloped['solid'].volume() < vertical['solid'].volume()
    assert sloped['record']['raft_slope_deg'] == 30
    assert sloped['solid'].slice(0.95).area() < vertical['solid'].slice(0.95).area()


@pytest.mark.parametrize('changes', [
    {'base_type': 'skate', 'base_skate_length_mm': 1},
    {'base_type': 'grid', 'base_cell_size_mm': 1},
    {'base_type': 'grid', 'base_strut_width_mm': 6},
    {'base_rotation_deg': 361}, {'base_rotation_deg': -361},
    {'base_rotation_deg': float('nan')}, {'base_cell_size_mm': 0},
])
def test_invalid_base_settings_fail_before_geometry(changes):
    with pytest.raises(VoxelMillError):
        resolve_settings(overrides={'support': changes})


def test_excessive_grid_complexity_is_refused_without_partial_geometry():
    settings = settings_for('grid', base_cell_size_mm=.00001, base_strut_width_mm=.000001)
    with pytest.raises(VoxelMillError, match='Grid exceeds'):
        build_base(FEET, settings, .6)


def test_base_work_honours_cancellation():
    token = CancellationToken()
    token.cancel()
    for kind in BASE_TYPES:
        with pytest.raises(Canceled):
            build_base(FEET, settings_for(kind), .6, cancel=token)
    with pytest.raises(Canceled):
        minimum_spanning_edges(FEET, cancel=token)


def test_placement_reserve_covers_large_skate_and_wide_struts():
    assert _reserve(settings_for('skate', base_skate_length_mm=12)) == 6
    assert _reserve(settings_for('skeleton', base_strut_width_mm=8)) == 4
    assert _reserve(settings_for('plate')) == 2.6


def test_cli_and_gui_share_choices_and_cli_exports_each_geometry(tmp_path, capsys):
    action = next(a for a in build_parser()._actions if a.dest == 'command')
    choices = next(a for a in action.choices['prepare']._actions if a.dest == 'base_type').choices
    assert tuple(choices) == BASE_TYPES
    for kind in ('skate', 'skeleton', 'grid'):
        output = tmp_path / (kind + '.stl')
        assert main(['support-example', '--base-type', kind, '--output', str(output),
                     '--set', 'support.spacing_mm=8', '--set', 'support.base_touch_diameter_mm=2.4']) == 0
        report = json.loads(capsys.readouterr().out)
        assert report['metrics']['base']['type'] == kind
        assert output.stat().st_size > 84


@pytest.mark.parametrize('kind', ['skate', 'skeleton', 'grid'])
def test_new_base_survives_preparation_reopen_and_raster_validation(kind, tmp_path):
    from voxelmill.pipeline import prepare
    from voxelmill.mesh import write_stl, open_stl
    from test_pipeline import small
    source, output = tmp_path / 'sphere.stl', tmp_path / 'supported.stl'
    write_stl(source, manifold_triangles(m.Manifold.sphere(6, 48)))
    settings = small(support={'base_type': kind, 'base_touch_diameter_mm': 2.4,
                             'base_thickness_mm': .8, 'base_skate_length_mm': 5})
    report = prepare(source, settings, output=output, components=True, drainage=True)
    assert report['validation']['passed'], report['validation']['checks']
    assert report['export']['written']
    record = report['validation']['metrics']['supports']['base']
    assert record['type'] == kind
    with open_stl(output) as reopened:
        assert report['validation']['metrics']['reopened']['sha256'] == reopened.asset.sha256
    assert (tmp_path / 'supported-raft.stl').is_file()


def test_dense_grid_reports_its_actual_lack_of_open_area():
    result = base('grid', feet=[[0, 0], [1, 0]], base_touch_diameter_mm=10)
    # No space remains for useful pores in this layout. The report must not
    # advertise the nominal grid holes as measured porosity.
    assert result['record']['open_area_fraction'] < .01


# ---- honeycomb --------------------------------------------------------------

def test_honeycomb_cells_are_hexagons_of_the_configured_pitch_and_wall():
    pitch, width = 6.0, .8
    result = base('hex', base_touch_diameter_mm=2.4, base_strut_width_mm=width,
                  base_cell_size_mm=pitch, base_thickness_mm=.8)
    record = result['record']
    assert record['connected_components'] == 1
    assert record['cell_size_mm'] == pitch and record['perimeter_width_mm'] == width
    assert record['hex_candidate_cells'] > 0
    sections = result['solid'].slice(.4).to_polygons()
    # A hexagonal opening whose flat-to-flat is pitch - width has this area.
    ideal = math.sqrt(3) / 2 * (pitch - width) ** 2
    interior = [abs(float(np.abs(np.cross(np.asarray(p), np.roll(np.asarray(p), -1, 0)).sum()) / 2))
                for p in sections]
    holes = [area for area in interior if abs(area - ideal) < ideal * 1e-6]
    assert len(holes) == 7, sorted(round(a, 3) for a in interior)


def test_a_honeycomb_and_a_square_grid_open_the_same_fraction_at_equal_pitch():
    """Hex is a different wall layout, not a cheaper one. Do not sell it as resin savings."""
    pitch, width = 3.0, .6
    feet = [[-40, -40], [-40, 40], [40, -40], [40, 40]]
    kinds = {kind: base(kind, feet=feet, base_touch_diameter_mm=1.4, base_strut_width_mm=width,
                        base_cell_size_mm=pitch, base_thickness_mm=.8)['record']
             for kind in ('grid', 'hex')}
    ideal = ((pitch - width) / pitch) ** 2
    for record in kinds.values():
        # Below ideal because the rim, the foot pads and the tree are solid.
        assert ideal - .04 < record['open_area_fraction'] < ideal
    assert abs(kinds['grid']['open_area_fraction'] - kinds['hex']['open_area_fraction']) < .02
    assert abs(kinds['grid']['volume_mm3'] - kinds['hex']['volume_mm3']) < kinds['grid']['volume_mm3'] * .04


def test_honeycomb_reaches_feet_between_cells_and_refuses_impossible_density():
    feet = [[-10, -9], [9, -8], [11, 10], [-8, 11], [.37, 1.71]]
    result = base('hex', feet=feet, base_touch_diameter_mm=2, base_strut_width_mm=.7,
                  base_cell_size_mm=5, base_rotation_deg=27)
    assert result['record']['connected_components'] == 1
    assert result['record']['skeleton_edges'] == 4
    assert len(result['solid'].slice(.5).to_polygons()) > 1
    with pytest.raises(VoxelMillError, match='Honeycomb exceeds'):
        build_base(FEET, settings_for('hex', base_cell_size_mm=.002,
                                      base_strut_width_mm=.001), .6)


# ---- sloped base edges ------------------------------------------------------

@pytest.mark.parametrize('kind', ['pad', 'skate', 'skeleton', 'grid', 'hex'])
def test_a_sloped_base_keeps_its_plate_contact_and_loses_only_volume(kind):
    common = dict(base_touch_diameter_mm=2.4, base_thickness_mm=.8, base_strut_width_mm=.8,
                  base_cell_size_mm=6, base_skate_length_mm=6)
    upright = base(kind, **common)
    sloped = base(kind, base_edge_slope_deg=70, **common)
    assert sloped['record']['contact_area_mm2'] == pytest.approx(upright['record']['contact_area_mm2'])
    assert sloped['record']['volume_mm3'] < upright['record']['volume_mm3']
    assert sloped['record']['edge_slope_deg'] == 70
    assert sloped['record']['edge_setback_mm'] == pytest.approx(.8 / math.tan(math.radians(70)))
    # One printed layer per step at the resolved layer height, and the same
    # overall envelope: the taper takes material off the top, not the plate.
    assert sloped['record']['edge_steps'] == round(.8 / resolve_settings()['process']['layer_height_mm'])
    assert sloped['record']['edge_step_mm'] == pytest.approx(.8 / sloped['record']['edge_steps'])
    np.testing.assert_allclose(np.asarray(sloped['solid'].bounding_box()).reshape(2, 3),
                               np.asarray(upright['solid'].bounding_box()).reshape(2, 3), atol=1e-7)
    assert sloped['record']['connected_components'] == upright['record']['connected_components']


def test_skate_at_thirty_degrees_keeps_plate_contact_and_insets_the_top():
    """CHITUBOX grayed raft Slope 30° maps onto base_edge_slope_deg from the plate."""
    common = dict(feet=[[0, 0]], base_touch_diameter_mm=10, base_thickness_mm=.8,
                  base_skate_length_mm=0)
    upright = base('skate', **common)
    sloped = base('skate', base_edge_slope_deg=30, **common)
    assert upright['record']['skate_length_mm'] == 10
    assert 'edge_slope_deg' not in upright['record']
    assert sloped['record']['contact_area_mm2'] == pytest.approx(upright['record']['contact_area_mm2'])
    assert sloped['record']['volume_mm3'] < upright['record']['volume_mm3']
    assert sloped['record']['edge_slope_deg'] == 30
    setback = .8 / math.tan(math.radians(30))
    assert sloped['record']['edge_setback_mm'] == pytest.approx(setback)
    bottom, top = sloped['solid'].slice(1e-6), sloped['solid'].slice(.8 - 1e-6)
    assert bottom.area() == pytest.approx(sloped['record']['contact_area_mm2'], rel=1e-9)
    # Last band is inset by one printed step less than the full setback.
    expected = bottom.offset(-setback + sloped['record']['edge_step_mm'] / math.tan(math.radians(30)),
                             circular_segments=32)
    assert top.area() == pytest.approx(float(expected.area()), rel=1e-6)
    assert top.area() < bottom.area()
    # Vertical skate (slope 0) stays the pre-taper capsule/disc.
    np.testing.assert_allclose(np.asarray(upright['solid'].bounding_box()).reshape(2, 3),
                               np.asarray(sloped['solid'].bounding_box()).reshape(2, 3), atol=1e-7)


def test_the_taper_insets_by_the_slope_and_stays_one_solid():
    result = base('skeleton', base_touch_diameter_mm=4, base_thickness_mm=.8,
                  base_strut_width_mm=2, base_edge_slope_deg=70)
    solid, record = result['solid'], result['record']
    assert len(solid.decompose()) == record['connected_components'] == 1
    setback = record['edge_setback_mm']
    bottom, top = solid.slice(1e-6), solid.slice(.8 - 1e-6)
    assert bottom.area() == pytest.approx(record['contact_area_mm2'], rel=1e-9)
    # The last band is inset by one step less than the full setback.
    expected = bottom.offset(-setback + record['edge_step_mm'] / math.tan(math.radians(70)),
                             circular_segments=32)
    assert top.area() == pytest.approx(float(expected.area()), rel=1e-6)


def test_a_slope_that_consumes_the_base_fails_with_the_height_it_reached():
    with pytest.raises(VoxelMillError) as raised:
        base('skeleton', base_touch_diameter_mm=2.4, base_thickness_mm=.8,
             base_strut_width_mm=.5, base_edge_slope_deg=20)
    assert 'consumes the base' in str(raised.value)
    details = raised.value.details
    assert 0 < details['height_reached_mm'] < details['thickness_mm']
    assert details['exhausted_at_step'] < details['steps']


def test_slope_and_hex_reach_the_cli_the_editor_and_the_placement_reserve(tmp_path, capsys):
    assert 'hex' in BASE_TYPES
    assert _reserve(settings_for('hex', base_strut_width_mm=8, base_cell_size_mm=12)) == 4
    assert main(['support-example', '--base-type', 'hex', '--output', str(tmp_path / 'hex.stl'),
                 '--set', 'support.spacing_mm=8', '--set', 'support.base_touch_diameter_mm=2.4',
                 '--set', 'support.base_edge_slope_deg=75']) == 0
    record = json.loads(capsys.readouterr().out)['metrics']['base']
    assert record['type'] == 'hex' and record['edge_slope_deg'] == 75
    assert record['hex_candidate_cells'] > 0
