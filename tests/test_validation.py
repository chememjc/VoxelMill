import pytest
import math
import numpy as np
from scipy import ndimage as ndi
from voxelmill.validation import drainage_clearance, analyze_layers
from voxelmill.contracts import Layer
from voxelmill.raster import RasterGrid
from voxelmill.config import resolve_settings


@pytest.mark.parametrize('pitch,limit', [((.018, .018), .08), ((.02, .035), .14), ((1., 1.), 4.)])
def test_tiled_growth_matches_independent_full_distance(pitch, limit):
    from voxelmill.validation import _tiled_growth_pixels
    rng = np.random.default_rng(461)
    previous = rng.random((83, 97)) < .007
    # Put predecessors on tile borders and queries throughout empty halos.
    previous[16, 16] = previous[32, 48] = True
    mask = rng.random(previous.shape) < .4
    grid = RasterGrid(97, 83, 0, 0, *pitch)
    exact = ndi.distance_transform_edt(~previous, sampling=(grid.dy, grid.dx))
    expected = np.count_nonzero(mask & ~previous & (exact > limit + 1e-10))
    assert _tiled_growth_pixels(previous, mask, grid, limit, tile_size=16) == expected


def test_analysis_budget_rejects_full_panel_before_consuming_layers():
    from voxelmill.contracts import ResourceBudget, VoxelMillError
    def layers():
        pytest.fail('oversized panel must be rejected before allocating masks')
        yield
    with pytest.raises(VoxelMillError, match='layer analysis buffers'):
        analyze_layers(layers(), RasterGrid(8520, 4320, 0, 0, .018, .018),
                       resolve_settings(), budget=ResourceBudget(memory_gib=.25))


def test_void_overlap_across_scratch_tile_boundaries():
    masks = [np.zeros((150, 20), np.uint8) for _ in range(5)]
    for mask in masks:
        mask[2:148, 2:18] = 1
    for mask in masks[1:4]:
        mask[30:140, 5:15] = 0
    settings = resolve_settings()
    report = analyze_layers([Layer(i, i + .5, a) for i, a in enumerate(masks)],
                            RasterGrid(20, 150, 0, 0, 1., 1.), settings)
    voids = report.metrics['enclosed_voids']
    assert voids['count'] == 1
    assert voids['volume_mm3'] == pytest.approx(110 * 10 * 3 * .05)


def chamber(neck_half_width):
    empty = np.ones((35, 35, 45), bool)
    empty[4:31, 4:31, 4:38] = False
    empty[8:27, 8:27, 8:32] = True
    empty[17-neck_half_width:18+neck_half_width,
          17-neck_half_width:18+neck_half_width, 31:40] = True
    return empty


def test_chamber_behind_narrow_neck_is_not_drained_by_outside_air():
    empty = chamber(0)
    result = drainage_clearance(empty, ndi.distance_transform_edt(empty), 1., 3.)
    assert result['bottlenecked_components'] == 1
    assert result['enclosed_components'] == 0
    example = result['bottleneck_examples'][0]
    assert example['bottleneck_area_mm2'] <= math.pi + .01
    assert example['bottleneck_area_upper_mm2'] >= math.pi - .01


def test_clearance_equality_and_larger_openings_pass():
    for radius in (2., 3.):
        empty = chamber(2)
        result = drainage_clearance(empty, ndi.distance_transform_edt(empty), 1., radius)
        assert result['bottlenecked_components'] == 0


def test_enclosed_chamber_stays_separate_from_neck_failure():
    empty = chamber(0)
    empty[17, 17, 32:39] = False
    result = drainage_clearance(empty, ndi.distance_transform_edt(empty), 1., 3.)
    assert result['enclosed_components'] == 1
    assert result['bottlenecked_components'] == 0


def test_one_pixel_cavity_is_not_silently_ignored_by_default():
    masks = [np.zeros((7, 7), np.uint8) for _ in range(5)]
    for mask in masks:
        mask[1:6, 1:6] = 1
    masks[2][3, 3] = 0
    settings = resolve_settings()
    grid = RasterGrid(7, 7, 0, 0, .018, .018)
    result = analyze_layers([Layer(i, (i+.5)*.05, a) for i,a in enumerate(masks)], grid, settings)
    assert result.checks['enclosed_voids'] == 'fail'
    assert result.metrics['enclosed_voids']['count'] == 1


# --- controlled cavity and orifice fixtures -------------------------------
#
# The tests above drive drainage_clearance on hand-built voxel arrays. These
# start from real meshes so the rasterizer, the Z subsampling and the
# area-to-radius conversion are all in the path, which is where a sealed
# cavity was previously reported as merely bottlenecked.

def hollow_box(*drain_diameters_mm):
    """A 9 mm cube with a 6 mm cavity and vertical bores of the given diameters."""
    import manifold3d as mf
    from voxelmill.geometry import manifold_triangles
    solid = mf.Manifold.cube([9., 9., 9.], True) - mf.Manifold.cube([6., 6., 6.], True)
    for index, diameter in enumerate(drain_diameters_mm):
        bore = mf.Manifold.cylinder(6., diameter / 2, diameter / 2, 96, True)
        solid -= bore.translate([-2. + 4. * index, 0., 3.])
    return manifold_triangles(solid)


def drainage_of(*diameters, **kwargs):
    from voxelmill.geometry import triangle_bounds
    from voxelmill.validation import analyze_drainage
    triangles = hollow_box(*diameters)
    return analyze_drainage(triangles, triangle_bounds(triangles), resolve_settings(), **kwargs)


THRESHOLD_DIAMETER_MM = 2 * math.sqrt(1.0 / math.pi)  # the 1 mm2 default


def test_sealed_cavity_is_enclosed_not_merely_bottlenecked():
    """A wall with no bore must read as enclosed.

    This is the regression for the rasterizer punching a one-row hole through
    solid material where a shared triangulation edge met a sample row center.
    """
    result = drainage_of()
    assert result['enclosed_components'] == 1
    assert result['bottlenecked_components'] == 0
    assert result['enclosed_volume_mm3'] > 100


def test_a_bore_below_the_threshold_is_reported_with_its_bottleneck_area():
    result = drainage_of(0.8)
    assert result['enclosed_components'] == 0
    assert result['bottlenecked_components'] == 1
    area = result['bottleneck_examples'][0]['bottleneck_area_mm2']
    assert 0 < area < 1.0
    assert result['bottleneck_examples'][0]['core_volume_mm3'] > 100


def test_a_bore_well_above_the_threshold_drains():
    result = drainage_of(2.0)
    assert result['bottlenecked_components'] == 0
    assert result['enclosed_components'] == 0


def test_a_threshold_bore_fails_conservatively_and_converges_with_resolution():
    """Equality passes on the grid; a curved bore still under-measures on it.

    ``drainage_clearance`` accepts ``clearance >= radius``, but sampling a round
    bore at cell centers reports less clearance than it has. The error shrinks
    as the analysis grid is refined and always understates the opening, so a
    marginal part fails rather than passes. A bore must be roughly 15% over the
    nominal diameter to clear the default grid.
    """
    areas = []
    for per_radius in (3.0, 6.0, 10.0):
        result = drainage_of(THRESHOLD_DIAMETER_MM, voxels_per_radius=per_radius)
        assert result['bottlenecked_components'] == 1
        areas.append(result['bottleneck_examples'][0]['bottleneck_area_mm2'])
    assert all(area < 1.0 for area in areas)
    assert areas[0] < areas[1] < areas[2]
    assert drainage_of(1.3)['bottlenecked_components'] == 0


def test_two_sub_threshold_openings_do_not_add_up_to_a_drain():
    """Drainage is a clearance path, not a total area; two narrow bores fail."""
    result = drainage_of(0.8, 0.8)
    assert result['bottlenecked_components'] == 1


def test_one_adequate_opening_drains_a_cavity_that_also_has_a_narrow_one():
    result = drainage_of(0.8, 2.0)
    assert result['bottlenecked_components'] == 0
    assert result['enclosed_components'] == 0


def test_an_open_surface_cannot_certify_drainage():
    """Four of the seven originals are open surfaces, so this path matters.

    Unfilled rows leave holes in the occupancy grid; a hole joins any chamber to
    outside air and the analysis then finds no bottleneck at all. Reporting that
    as a pass would certify drainage from a grid that cannot show it.
    """
    import numpy as np
    from voxelmill.validation import analyze_drainage, drainage_check
    from voxelmill.geometry import triangle_bounds
    triangles = np.asarray(hollow_box())
    # Drop the outer +X wall to leave a surface with a boundary.
    triangles = triangles[~np.all(np.isclose(triangles[:, :, 0], 4.5), axis=1)]
    result = analyze_drainage(triangles, triangle_bounds(triangles), resolve_settings())
    assert result['unclosed_rows'] > 0 and result['occupancy_closed'] is False
    assert result['bottlenecked_components'] == 0  # the holes hid the chamber
    assert drainage_check(result) == 'not_run'


def test_a_sealed_cavity_fails_the_drainage_check_rather_than_passing_it():
    from voxelmill.validation import drainage_check
    assert drainage_check(drainage_of()) == 'fail'
    assert drainage_check(drainage_of(2.0)) == 'pass'


def test_a_volume_floor_counts_the_pockets_it_ignores():
    """Raising the floor must report the ignored population, never drop it.

    The crevice where a support pillar meets the raft is a genuine sub-threshold
    pocket, so a supported assembly reaches this path routinely.
    """
    from voxelmill.validation import drainage_clearance
    empty = chamber(0)
    distance = ndi.distance_transform_edt(empty)
    strict = drainage_clearance(empty, distance, 1., 3.)
    assert strict['bottlenecked_components'] == 1
    assert strict['ignored_bottlenecked_components'] == 0
    volume = strict['bottleneck_examples'][0]['core_volume_mm3']

    lenient = drainage_clearance(empty, distance, 1., 3., min_volume_mm3=volume * 2)
    assert lenient['bottlenecked_components'] == 0
    assert lenient['ignored_bottlenecked_components'] == 1
    assert lenient['ignored_bottlenecked_volume_mm3'] == pytest.approx(volume)
    assert lenient['min_void_volume_mm3'] == pytest.approx(volume * 2)


def test_model_only_absence_classifies_support_drainage_and_policy_ignore():
    from voxelmill.contracts import Diagnostic, ValidationReport
    from voxelmill.validation import (attribute_drainage_by_model, attribute_enclosed_voids_by_model,
                                     apply_support_void_policy, drainage_check)

    union = {'status': 'complete', 'occupancy_closed': True,
             'bottlenecked_components': 2, 'enclosed_components': 0,
             'bottleneck_examples': [
                 {'component': 1, 'core_volume_mm3': 0.02},
                 {'component': 2, 'core_volume_mm3': 0.01}]}
    model = {'status': 'complete', 'bottlenecked_components': 0, 'enclosed_components': 0}
    attributed = attribute_drainage_by_model(dict(union), model)
    assert attributed['support_bottlenecked_components'] == 2
    assert attributed['model_bottlenecked_components'] == 0
    assert all(ex['void_class'] == 'support' for ex in attributed['bottleneck_examples'])
    assert drainage_check(attributed) == 'fail'
    assert drainage_check(attributed, model_only=True) == 'pass'

    report = ValidationReport(checks={'drainage_bottlenecks': 'fail', 'enclosed_voids': 'pass'})
    report.metrics['drainage'] = attributed
    report.diagnostics.append(Diagnostic('drainage_bottleneck', 'neck', details=attributed))
    apply_support_void_policy(report, resolve_settings(overrides={
        'repair': {'support_void_policy': 'ignore'}}))
    assert report.checks['drainage_bottlenecks'] == 'pass'
    assert report.metrics['ignored_support_bottlenecks']['bottlenecked_components'] == 2
    assert any(d.code == 'ignored_support_bottlenecks' for d in report.diagnostics)

    voids = attribute_enclosed_voids_by_model(
        {'count': 1, 'volume_mm3': 216.0, 'examples': [{'component': 0, 'volume_mm3': 216.0}]},
        {'count': 1, 'volume_mm3': 216.0})
    assert voids['model_count'] == 1 and voids['support_count'] == 0
    support_only = attribute_enclosed_voids_by_model(
        {'count': 1, 'volume_mm3': 0.5, 'examples': [{'component': 0, 'volume_mm3': 0.5}]},
        {'count': 0, 'volume_mm3': 0.0})
    assert support_only['support_count'] == 1 and support_only['model_count'] == 0
