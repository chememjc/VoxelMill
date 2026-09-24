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


def test_check_growth_false_skips_growth_but_not_island_evidence():
    """The island guard's optimization: growth is pure waste for it.

    Layer 2 both fails overlap (it shares no columns with layer 1, so it is
    a raster island) and fails growth (its nearest layer-1 material is 15 mm
    away, far past the 3 mm default ``max_span_mm``). ``check_growth=False``
    must leave every island-derived accumulator, check and diagnostic exactly
    as it is with growth checked, and must never let the skipped check read
    as a verified 'pass'.
    """
    masks = [np.zeros((10, 40), np.uint8) for _ in range(3)]
    masks[0][:, 0:5] = 1
    masks[1][:, 0:5] = 1
    masks[2][:, 20:25] = 1  # 15 mm from the nearest layer-1 material
    settings = resolve_settings()
    grid = RasterGrid(40, 10, 0, 0, 1., 1.)
    layers = lambda: [Layer(i, i + .5, a) for i, a in enumerate(masks)]

    checked = analyze_layers(layers(), grid, settings, track_voids=False, check_growth=True)
    skipped = analyze_layers(layers(), grid, settings, track_voids=False, check_growth=False)

    # Growth really did fire in the checked run, and is honestly reported as
    # skipped -- never as a 'pass' it never verified -- in the other.
    assert checked.checks['growth_span'] == 'fail'
    assert checked.metrics['growth_violation_pixels'] > 0
    assert skipped.checks['growth_span'] == 'not_run'
    assert 'growth_violation_pixels' not in skipped.metrics

    # Every island-derived field is untouched by skipping growth.
    assert checked.checks['raster_connectivity'] == skipped.checks['raster_connectivity'] == 'fail'
    assert checked.checks['overlap'] == skipped.checks['overlap'] == 'fail'
    for key in ('island_components', 'island_components_on_crop_edge',
                'layer_count', 'nonempty_layers'):
        assert checked.metrics[key] == skipped.metrics[key]
    islands = lambda report: [(d.layer, tuple(d.position_mm), d.details)
                              for d in report.diagnostics if d.code == 'raster_island']
    assert islands(checked) == islands(skipped)


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


class _ReferenceVoidForest:
    """The v0.1.0 ``VoidForest.merge`` loop, verbatim, as an accumulation reference.

    ``VoidForest.union`` accumulates volumes and ``trapped`` in floating point,
    and floating-point addition is not associative, so the *sequence* of union
    calls -- the same pairs, in the same order, with the same cross-chunk
    duplicates -- is part of the reported number, not an implementation detail.
    Anything that replaces the ``np.unique`` dedup has to reproduce this loop
    call for call; ``test_merge_union_sequence_matches_reference`` is what says
    so, and it is why this copy stays here rather than being deleted as dead.
    """

    def merge(self, components, index, cancel):
        labels, total, counts, outside = components
        if index == 0:
            outside = np.ones(total + 1, dtype=bool)
        self._grow(self.count + total + 1)
        ids = np.zeros(total + 1, dtype=np.int64)
        if total:
            ids[1:] = np.arange(self.count, self.count + total, dtype=np.int64)
            slice_ = slice(self.count, self.count + total)
            self.parent[slice_] = np.arange(self.count, self.count + total)
            self.volume[slice_] = counts[1:] * self.voxel_volume
            self.exterior[slice_] = outside[1:]
            self.born[slice_] = index
            self.trapped += float(self.volume[slice_][~outside[1:]].sum())
            self.count += total
        if self.previous is not None and total:
            cancel.check()
            for row in range(0, labels.shape[0], 64):
                cancel.check()
                before = self.previous[row:row + 64].ravel()
                after = labels[row:row + 64].ravel()
                both = (before > 0) & (after > 0)
                if both.any():
                    keys = before[both].astype(np.int64) * (total + 1) + after[both]
                    for key in np.unique(keys):
                        self.union(int(self.previous_ids[key // (total + 1)]),
                                   int(ids[key % (total + 1)]))
        self.previous, self.previous_ids = labels, ids
        self.history.append(self.trapped)
        return labels, ids


class _RecordingUnion:
    """Capture every ``union`` argument pair, in call order, duplicates kept."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = []

    def union(self, a, b):
        self.calls.append((a, b))
        return super().union(a, b)


def _void_merge_stack(layers=7, height=200, width=61):
    """Masks whose empty space spans several 64-row chunks and repeats in each.

    Full-height void stripes make the same ``(before, after)`` label pair occur
    in every one of the four row chunks, which is the duplicate-preservation
    case a per-layer dedup would silently drop. The corridor, the drifting blob
    and the speckle keep label numbering from being trivially stable, so the
    emission order matters too.
    """
    rng = np.random.default_rng(20240917)
    masks = []
    for index in range(layers):
        mask = np.ones((height, width), np.uint8)
        for column in (7, 19, 31, 43):
            mask[:, column:column + 3] = 0
        if index % 2:
            mask[90:94, 5:50] = 0  # joins the stripes into one component
        row = 20 + 9 * index
        mask[row:row + 12, 50:57] = 0  # a blob that is born, drifts and dies
        mask[rng.random((height, width)) < .01] = 0
        masks.append(mask)
    return masks


def _record_merges(forest_class, masks, *, dense_components=False):
    from voxelmill.validation import void_components, occupancy_mask, _dense_void_components
    from voxelmill.contracts import CancellationToken
    cancel = CancellationToken()
    forest = forest_class(0.0018 * 0.0018 * 0.05)
    # `_ReferenceVoidForest` is a verbatim v0.1.0 dense merge and unpacks the
    # dense 4-tuple. The current VoidForest consumes whatever `void_components`
    # now returns (run payloads when density allows).
    label = _dense_void_components if dense_components else void_components
    for index, mask in enumerate(masks):
        forest.merge(label(occupancy_mask(mask)), index, cancel)
    return forest


def test_merge_union_sequence_matches_reference():
    """The union call sequence is the result; it may never be reordered.

    Same pairs, same order, same count including the duplicates that arise when
    one label pair straddles more than one row chunk. Rewrites of the dedup are
    allowed; changes to this sequence are not, because they move float
    accumulation and the v0.1.0 equivalence gate compares those volumes.
    """
    from voxelmill.validation import VoidForest

    class Recorded(_RecordingUnion, VoidForest):
        pass

    class Reference(_RecordingUnion, _ReferenceVoidForest, VoidForest):
        pass

    masks = _void_merge_stack()
    assert masks[0].shape[0] > 64, 'a single-chunk fixture would not guard duplicates'
    current = _record_merges(Recorded, masks)
    reference = _record_merges(Reference, masks, dense_components=True)
    assert current.calls == reference.calls
    assert current.calls, 'fixture produced no overlaps to union'
    assert len(current.calls) > len(set(current.calls)), \
        'fixture produced no cross-chunk duplicate unions to preserve'
    assert current.history == reference.history
    assert current.trapped == reference.trapped
    assert current.finish() == reference.finish()


def test_merge_union_sequence_matches_reference_under_key_table_cap():
    """The sorting fallback, taken when the key table would be too large, agrees.

    `MERGE_KEY_TABLE_CAP` only gates the dense scatter. Run-space pair
    extraction has no cap equivalent (`run_pairs` is exact at any component
    count); the current forest still has to match the reference sequence
    whichever representation `void_components` chose.
    """
    import voxelmill.validation as validation

    class Recorded(_RecordingUnion, validation.VoidForest):
        pass

    class Reference(_RecordingUnion, _ReferenceVoidForest, validation.VoidForest):
        pass

    masks = _void_merge_stack()
    reference = _record_merges(Reference, masks, dense_components=True)
    saved = validation.MERGE_KEY_TABLE_CAP
    try:
        validation.MERGE_KEY_TABLE_CAP = 0  # every dense chunk takes the fallback
        capped = _record_merges(Recorded, masks)
    finally:
        validation.MERGE_KEY_TABLE_CAP = saved
    assert capped.calls == reference.calls
    assert capped.finish() == reference.finish()


# --- island diagnostics: the per-component pixel count ---------------------
#
# `_label_occupancy` no longer returns a full-panel `np.bincount` of the label
# image; the `pixels` field of a `raster_island` diagnostic is computed per
# diagnostic instead, from the same `labels == component` mask the diagnostic's
# position already needed. These tests pin that the published numbers are
# exactly what the eager bincount produced -- the bracket fixture reports zero
# islands, so nothing else in the suite exercises the materialization path.

def _island_masks():
    """Layers whose islands have distinct, hand-countable pixel areas."""
    base = np.zeros((40, 40), np.uint8)
    base[0:6, 0:6] = 1                      # supported column, never an island
    masks = [base.copy(), base.copy()]

    third = base.copy()
    third[10, 10] = 1                       # 1 px
    third[10:12, 20:22] = 1                 # 4 px
    third[20:23, 10:14] = 1                 # 12 px
    third[30:34, 20] = 1                    # 7 px L
    third[33, 21:24] = 1                    # ...
    masks.append(third)

    fourth = base.copy()
    fourth[5, 20:29] = 1                    # 15 px plus
    fourth[2:9, 24] = 1                     # ...
    fourth[25:30, 30:34] = 1                # 20 px
    masks.append(fourth)
    return masks


def _eager_island_pixels(masks, grid):
    """What the removed full-panel bincount would have said, per (layer, position)."""
    from voxelmill.validation import CROSS
    expected = {}
    for index, mask in enumerate(masks):
        labels, count = ndi.label(np.asarray(mask) != 0, CROSS)
        counts = np.bincount(labels.ravel(), minlength=count + 1)
        for component in range(1, count + 1):
            row, col = np.argwhere(labels == component)[0]
            expected[(index, tuple(grid.xy(int(row), int(col))))] = int(counts[component])
    return expected


def test_island_diagnostic_pixels_match_the_eager_bincount():
    masks = _island_masks()
    grid = RasterGrid(40, 40, 0, 0, 1., 1.)
    expected = _eager_island_pixels(masks, grid)
    report = analyze_layers([Layer(i, i + .5, m) for i, m in enumerate(masks)],
                            grid, resolve_settings(), track_voids=False)

    islands = [d for d in report.diagnostics if d.code == 'raster_island']
    assert report.checks['raster_connectivity'] == 'fail'
    assert len(islands) == 6, 'the fixture must actually materialize the lazy path'
    # Every diagnostic names a real component, at the component's first pixel in
    # C order, carrying that component's exact area.
    for diagnostic in islands:
        key = (diagnostic.layer, tuple(diagnostic.position_mm[:2]))
        assert key in expected, f'{key} is not the first pixel of any component'
        assert diagnostic.details['pixels'] == expected[key]
    assert sorted(d.details['pixels'] for d in islands) == [1, 4, 7, 12, 15, 20]
    assert report.metrics['island_components'] == 6


def test_island_diagnostic_pixels_survive_the_worker_pool_and_the_example_cap():
    """Same numbers off the pool, and when only the first few slots are filled."""
    masks = _island_masks()
    grid = RasterGrid(40, 40, 0, 0, 1., 1.)
    expected = _eager_island_pixels(masks, grid)
    layers = lambda: [Layer(i, i + .5, m) for i, m in enumerate(masks)]

    def islands(workers, **kwargs):
        settings = resolve_settings(overrides={'resources': {'workers': workers}})
        report = analyze_layers(layers(), grid, settings, track_voids=False, **kwargs)
        return [(d.layer, tuple(d.position_mm[:2]), d.details['pixels'])
                for d in report.diagnostics if d.code == 'raster_island'], report

    pooled, pooled_report = islands(8)
    serial, serial_report = islands(1)
    capped, capped_report = islands(8, max_examples=2)
    assert pooled_report.metrics['analysis_workers'] > 1, 'the pool path must be taken'
    assert serial_report.metrics['analysis_workers'] == 1, 'the serial path must be taken'
    assert serial == pooled
    assert len(capped) == 2
    assert capped == pooled[:2]
    # The cap bounds the diagnostics, never the accumulated evidence.
    assert capped_report.metrics['island_components'] == pooled_report.metrics['island_components'] == 6
    for layer, position, pixels in pooled:
        assert pixels == expected[(layer, position)]


def test_native_runs_off_matches_default_on_island_masks(monkeypatch):
    """``VOXELMILL_NATIVE_RUNS=0`` must not change published island/void evidence."""
    masks = _island_masks()
    grid = RasterGrid(40, 40, 0, 0, 1., 1.)
    layers = lambda: [Layer(i, i + .5, m.copy()) for i, m in enumerate(masks)]

    def report(flag):
        monkeypatch.setenv('VOXELMILL_NATIVE_RUNS', flag)
        return analyze_layers(layers(), grid, resolve_settings(), track_voids=True)

    dense = report('0')
    native = report('1')
    assert dense.checks == native.checks
    assert dense.metrics['island_components'] == native.metrics['island_components'] == 6
    assert dense.metrics['enclosed_voids'] == native.metrics['enclosed_voids']
    assert dense.metrics['transient_traps'] == native.metrics['transient_traps']
    island_pixels = lambda r: sorted(
        (d.layer, tuple(d.position_mm), d.details['pixels'])
        for d in r.diagnostics if d.code == 'raster_island')
    assert island_pixels(dense) == island_pixels(native)


def test_fixture_hollow_cup_fails_drainage_and_drained_cup_passes():
    """Committed cup fixtures: sealed cavity fails, drain hole passes."""
    from pathlib import Path
    from voxelmill.geometry import triangle_bounds
    from voxelmill.mesh import open_stl
    from voxelmill.validation import analyze_drainage, drainage_check

    root = Path(__file__).resolve().parents[1] / 'fixtures/shapes'
    settings = resolve_settings(overrides={'resources': {'workers': 1}})

    def drain(name):
        with open_stl(root / name) as mesh:
            triangles = np.asarray(mesh.triangles, dtype=np.float32).copy()
        return analyze_drainage(triangles, triangle_bounds(triangles), settings,
                                voxels_per_radius=2.0)

    sealed = drain('hollow_cup.stl')
    assert sealed['occupancy_closed'] is True
    assert sealed['enclosed_components'] == 1
    assert sealed['bottlenecked_components'] == 0
    assert drainage_check(sealed) == 'fail'

    open_ = drain('drained_cup.stl')
    assert open_['occupancy_closed'] is True
    assert open_['enclosed_components'] == 0
    assert open_['bottlenecked_components'] == 0
    assert drainage_check(open_) == 'pass'


def _assert_edt_matches_scipy(mask, sampling):
    from voxelmill import _native
    mask = np.ascontiguousarray(mask)
    got = _native.distance_transform_edt(mask, sampling)
    ref = ndi.distance_transform_edt(mask, sampling=sampling)
    np.testing.assert_array_equal(got, ref)


def test_native_edt_matches_scipy_on_random_3d_fields():
    """Felzenszwalb–Huttenlocher distances must equal scipy bit for bit."""
    rng = np.random.default_rng(20260917)
    pitch = 0.18806319451591877
    for _ in range(24):
        shape = tuple(int(rng.integers(1, 12)) for _ in range(3))
        p = float(rng.choice([0.0, 0.1, 0.35, 0.7, 0.95, 1.0]))
        mask = rng.random(shape) < p
        _assert_edt_matches_scipy(mask, (1.0, 1.0, 1.0))
        _assert_edt_matches_scipy(mask, (pitch, pitch, pitch))
        _assert_edt_matches_scipy(mask, (0.1, 0.2, 0.3))
    _assert_edt_matches_scipy(np.ones((4, 5, 6), bool), (1.0, 1.0, 1.0))
    _assert_edt_matches_scipy(np.zeros((4, 5, 6), bool), (pitch, pitch, pitch))
    single = np.ones((6, 7, 8), bool)
    single[2, 3, 4] = False
    _assert_edt_matches_scipy(single, (pitch, pitch, pitch))


def _drainage_empty(triangles, bounds, settings, voxels_per_radius=3.0):
    """The occupancy volume `analyze_drainage` feeds to the 3-D EDT."""
    from voxelmill import _native
    from voxelmill.contracts import CancellationToken
    cancel = CancellationToken()
    area = float(settings['repair']['min_orifice_area_mm2'])
    radius = math.sqrt(area / math.pi)
    pitch = radius / float(voxels_per_radius)
    bounds = np.asarray(bounds, dtype=float)
    dims = np.maximum(1, np.ceil((bounds[1] - bounds[0]) / pitch)).astype(int) + 4
    nx, ny, nz = (int(v) for v in dims)
    x0, y0, z0 = bounds[0] - 2 * pitch
    raster = _native.Rasterizer(np.asarray(triangles), cancel.check)
    occupancy = np.zeros((nz, ny, nx), dtype=bool)
    subsamples = max(1, int(round(pitch / min(settings['printer']['pixel_pitch_mm']) / 8)))
    for k in range(nz):
        cell = np.zeros((ny, nx), dtype=bool)
        for sub in range(subsamples):
            z = z0 + (k + (sub + 0.5) / subsamples) * pitch
            if z < bounds[0][2] or z > bounds[1][2]:
                continue
            sliced = raster.slice(float(z), nx, ny, float(x0), float(y0), pitch, pitch,
                                  cancel.check, 'nonzero')
            cell |= sliced['mask'] != 0
        occupancy[k] = cell
    empty = ~occupancy
    empty[0] = empty[-1] = True
    empty[:, 0] = empty[:, -1] = True
    empty[:, :, 0] = empty[:, :, -1] = True
    return empty, (pitch, pitch, pitch)


def test_native_edt_matches_scipy_on_real_drainage_grids():
    from pathlib import Path
    from voxelmill.geometry import triangle_bounds
    from voxelmill.mesh import open_stl
    from voxelmill.validation import _native_edt
    assert _native_edt() is not None
    settings = resolve_settings()
    triangles = hollow_box()
    empty, sampling = _drainage_empty(triangles, triangle_bounds(triangles), settings)
    _assert_edt_matches_scipy(empty, sampling)
    root = Path(__file__).resolve().parents[1] / 'fixtures/shapes'
    with open_stl(root / 'overhang_bracket.stl') as mesh:
        triangles = np.asarray(mesh.triangles, dtype=np.float32).copy()
        bounds = np.asarray(mesh.asset.bounds, dtype=float)
    empty, sampling = _drainage_empty(triangles, bounds, settings)
    _assert_edt_matches_scipy(empty, sampling)


def test_native_edt_off_falls_back_to_scipy(monkeypatch):
    from voxelmill.validation import _distance_transform_edt, _native_edt
    rng = np.random.default_rng(7)
    mask = rng.random((9, 8, 11)) < 0.4
    mask[0] = False
    sampling = (0.2, 0.2, 0.2)
    monkeypatch.setenv('VOXELMILL_NATIVE_EDT', '0')
    assert _native_edt() is None
    got = _distance_transform_edt(mask, sampling)
    ref = ndi.distance_transform_edt(mask, sampling=sampling)
    np.testing.assert_array_equal(got, ref)


def test_island_extent_matches_argwhere_and_bincount_on_a_random_label_field():
    """The two expressions `_island_extent` replaced, checked component by component."""
    from voxelmill.validation import CROSS, _island_extent
    rng = np.random.default_rng(20260917)
    field = rng.random((61, 47)) < .35
    labels, count = ndi.label(field, CROSS)
    counts = np.bincount(labels.ravel(), minlength=count + 1)
    assert count > 30, 'the field must contain many components to be worth checking'
    for component in range(1, count + 1):
        row, col, pixels = _island_extent(labels, component)
        assert [row, col] == list(np.argwhere(labels == component)[0])
        assert pixels == int(counts[component])


def _per_pocket_bottlenecks(empty, clearance, radius):
    """The original search: eight full labelings per pocket."""
    import math
    import scipy.ndimage as ndi
    from voxelmill.validation import CROSS3, _border_ids
    core, count = ndi.label(empty & (clearance >= radius - 1e-9), CROSS3)
    outside = _border_ids(core)
    voids, _ = ndi.label(empty, CROSS3)
    open_voids = set(_border_ids(voids)) - {0}
    boxes = ndi.find_objects(core)
    found = []
    for component in np.setdiff1d(np.arange(1, count + 1), outside):
        box = boxes[component - 1]
        local = np.argwhere(core[box] == component)[0]
        seed = tuple(int(local[a] + box[a].start) for a in range(3))
        if int(voids[seed]) not in open_voids:
            continue
        low, high = 0.0, radius
        for _ in range(8):
            threshold = (low + high) / 2
            labels, _ = ndi.label(empty & (clearance >= threshold - 1e-9), CROSS3)
            identity = int(labels[seed])
            if identity and identity in _border_ids(labels):
                low = threshold
            else:
                high = threshold
        found.append((int(component), math.pi * low**2, math.pi * high**2, list(seed)))
    return found


def test_lockstep_bottleneck_search_matches_the_per_pocket_search():
    import scipy.ndimage as ndi
    from voxelmill.validation import drainage_clearance
    # Chambers behind necks of different widths, all opening to the same air.
    empty = np.zeros((14, 40, 60), dtype=bool)
    empty[:, :, :4] = True                                # the outside, at the grid border
    for index, neck in enumerate((1, 2, 3, 5)):
        y0 = 2 + index * 9
        empty[3:11, y0:y0 + 7, 12:22] = True              # a chamber
        empty[6:6 + neck, y0 + 3:y0 + 3 + neck, 4:12] = True   # its neck to the outside
    clearance = ndi.distance_transform_edt(empty, sampling=0.5)
    radius = 1.6
    got = drainage_clearance(empty, clearance, 0.5, radius)
    expected = _per_pocket_bottlenecks(empty, clearance, radius)
    # Several pockets whose searches diverge, or lockstep proves nothing.
    assert len({area for _, area, _, _ in expected}) >= 2
    assert sorted((p['component'], p['bottleneck_area_mm2'], p['bottleneck_area_upper_mm2'],
                   p['seed_zyx']) for p in got['bottleneck_examples']) == sorted(expected)


def test_growth_count_does_not_depend_on_tile_size():
    from types import SimpleNamespace
    import scipy.ndimage as ndi
    from voxelmill.validation import _tiled_growth_pixels
    rng = np.random.default_rng(1)
    grid = SimpleNamespace(dx=0.05, dy=0.05)
    for _ in range(4):
        previous = ndi.binary_dilation(rng.random((300, 420)) < 0.002, iterations=2)
        mask = rng.random((300, 420)) < 0.3
        for limit in (0.5, 2.0):
            counts = {_tiled_growth_pixels(previous, mask, grid, limit, tile_size=size)
                      for size in (48, 256, None, 1000)}
            assert len(counts) == 1
