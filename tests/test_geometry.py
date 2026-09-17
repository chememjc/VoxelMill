import numpy as np
import pytest
import manifold3d as m
from voxelmill.contracts import CancellationToken, Canceled, VoxelMillError, ResourceBudget
from voxelmill.geometry import (auto_placement, cylinder_between, envelope_fits,
    fill_enclosed_cavities, iter_transformed_triangles, manifold_triangles,
    mesh_to_manifold, placement_for_triangles, raft_from_feet, triangle_bounds)


def settings(build=(153.36, 77.76, 165.)):
    return {'printer': {'build_mm': list(build), 'edge_clearance_mm': 2.},
            'support': {'pillar_diameter_mm': 1.2, 'raft_expansion_mm': 2.}}


def test_extrinsic_rotation_and_final_bbox_placement_are_exact_and_immutable():
    # The third point makes the vertex centroid differ from the bbox center.
    triangles = np.array([[[1., 2., 3.], [5., 2., 3.], [1., 8., 5.]]])
    original = triangles.copy()
    p = placement_for_triangles(triangles, settings(), (90, 0, 90), (7, -3), 5)
    transformed = np.concatenate(list(iter_transformed_triangles(triangles, p.matrix)))
    # Rx then Rz maps x,y,z to z,x,y, before final bbox translation.
    raw = triangles[..., [2, 0, 1]]
    expected = raw + np.array([7, -3, 5]) - np.array([4, 3, 2])
    np.testing.assert_allclose(transformed, expected, atol=1e-12)
    np.testing.assert_array_equal(triangles, original)
    np.testing.assert_allclose(np.array(p.bounds).mean(axis=0)[:2], [7, -3])
    assert p.bounds[0][2] == 5
    np.testing.assert_allclose(np.linalg.det(np.array(p.matrix)[:3, :3]), 1)


def test_placement_reserves_complete_vertical_raft_extent():
    cube = manifold_triangles(m.Manifold.cube((10, 10, 10)))
    # 10 + 2 * 2.6 reserve + 2 * 2 edge clearance = 19.2.
    kept = placement_for_triangles(cube, settings((19.2, 19.2, 20)))
    assert kept.search['reserve_relaxed'] is False
    assert kept.search['xy_support_reserve_mm'] == 2.6
    # Below that the conservative reserve is relaxed and the relaxation recorded;
    # the authoritative check is the envelope of the actual supported assembly.
    relaxed = placement_for_triangles(cube, settings((19.1, 19.2, 20)))
    assert relaxed.search['reserve_relaxed'] is True
    assert relaxed.search['requires_support_envelope_recheck']
    # Narrower than the model plus plate clearance is infeasible at any reserve.
    with pytest.raises(VoxelMillError, match='no feasible placement found'):
        placement_for_triangles(cube, settings((13.9, 19.2, 20)))
    assert not envelope_fits([[0, 0, 0], [10, 10, 21]], settings((40, 40, 20)))


def strip_timings(placement):
    """Drop wall-clock fields, which no repeated run can reproduce."""
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k != 'seconds'}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    return strip(placement.search)


def test_auto_fit_reproducible_without_scaling():
    triangles = manifold_triangles(m.Manifold.cube((100, 30, 10)))
    first = auto_placement(triangles, settings((60, 120, 150)))
    second = auto_placement(triangles, settings((60, 120, 150)))
    assert first.matrix == second.matrix
    assert first.bounds == second.bounds
    assert first.rotation_deg == second.rotation_deg
    # Every reported decision must repeat; only the stopwatch may differ.
    assert strip_timings(first) == strip_timings(second)
    assert first.search['full_resolution_bounds']
    assert first.search['requires_support_envelope_recheck']
    matrix = np.array(first.matrix)[:3, :3]
    np.testing.assert_allclose(matrix.T @ matrix, np.eye(3), atol=1e-12)


def test_empty_nonfinite_and_canceled_geometry_rejected():
    with pytest.raises(VoxelMillError):
        triangle_bounds(np.empty((0, 3, 3)))
    with pytest.raises(VoxelMillError):
        triangle_bounds(np.full((1, 3, 3), np.nan))
    token = CancellationToken(); token.cancel()
    with pytest.raises(Canceled):
        triangle_bounds(np.zeros((1, 3, 3)), cancel=token)


def test_manifold_exact_round_trip_and_invalid_boundary_rejected():
    triangles = manifold_triangles(m.Manifold.cube((3, 4, 5)))
    solid, report = mesh_to_manifold(triangles)
    assert solid.volume() == pytest.approx(60)
    assert report['max_displacement_mm'] == 0
    assert report['self_intersections'] == 'checked_none'
    with pytest.raises(VoxelMillError, match='accepted positively oriented'):
        mesh_to_manifold(triangles[:-1])


def test_closed_cavity_filled_but_through_hole_preserved():
    outer = m.Manifold.cube((10, 10, 10))
    closed = outer - m.Manifold.cube((2, 2, 2)).translate((4, 4, 4))
    filled, report = fill_enclosed_cavities(closed)
    assert report['filled_shell_count'] == 1
    assert report['added_volume_mm3'] == pytest.approx(8)
    assert filled.volume() == pytest.approx(1000)
    passage = outer - m.Manifold.cube((2, 2, 12)).translate((4, 4, -1))
    kept, report = fill_enclosed_cavities(passage)
    assert report['filled_shell_count'] == 0
    assert kept.volume() == pytest.approx(960)
    assert report['drainage_bottlenecks'] == 'not_checked'


def test_tapered_pillar_union_and_connected_bevelled_raft():
    raft = raft_from_feet([[0, 0], [10, 0]])
    pillar = cylinder_between([0, 0, .5], [0, 0, 7], .6)
    tip = cylinder_between([0, 0, 7], [0, 0, 9], .6, .2)
    model = m.Manifold.cube((2, 2, 2)).translate((-1, -1, 8.85))
    solid = raft + pillar + tip + model
    assert solid.status() == m.Error.NoError
    assert len(solid.decompose()) == 1
    np.testing.assert_allclose(raft.bounding_box(), [-2.6, -2.6, 0, 12.6, 2.6, 1])
    # Bevel removes material compared to a straight hull extrusion.
    unbevelled = raft_from_feet([[0, 0], [10, 0]], bevel_mm=0, slope_deg=0)
    assert raft.volume() < unbevelled.volume()
    tilted = cylinder_between([1, 2, 3], [4, 6, 3], 1)
    assert tilted.volume() == pytest.approx(5 * 12 * np.sin(np.pi / 12), rel=1e-8)


def test_no_zero_area_faces_silently_removed():
    triangles = manifold_triangles(m.Manifold.cube((1, 1, 1)))
    triangles = np.concatenate((triangles, np.zeros((1, 3, 3))))
    with pytest.raises(VoxelMillError, match='zero-area'):
        mesh_to_manifold(triangles)


def test_auto_final_bounds_include_unsampled_extreme():
    # Sample stride two skips the last triangle, which is impossible to fit.
    triangles = np.zeros((20002, 3, 3))
    triangles[:, 1] = [1, 0, 0]
    triangles[:, 2] = [0, 1, 0]
    triangles[-1, 2] = [1000, 1000, 1000]
    with pytest.raises(VoxelMillError, match='no feasible placement found'):
        auto_placement(triangles, settings((30, 30, 30)))


# --- orientation assessment ------------------------------------------------

def open_cup():
    """A 20 mm box hollowed from one side; the opening faces +Z unrotated."""
    return manifold_triangles(
        m.Manifold.cube([20., 20., 20.], True)
        - m.Manifold.cube([16., 16., 16.], True).translate([0, 0, 3.]))


def cup_settings():
    return settings((50, 50, 150))


def test_assessment_separates_a_bowl_from_a_cup_that_drains():
    """Gravity points toward +Z, so an opening facing +Z sheds resin.

    The trapped volume is the whole 16 by 16 by 15 mm cavity, which is what the
    coarse grid should measure once the opening is turned away from gravity.
    """
    from voxelmill.geometry import rotation_matrix, triangle_bounds
    from voxelmill.validation import orientation_assessment
    triangles = np.asarray(open_cup())
    drains = orientation_assessment(triangles, triangle_bounds(triangles),
                                    cup_settings(), pitch_mm=0.5)
    assert drains['trapped_resin_mm3'] == 0
    flipped = np.ascontiguousarray(triangles @ rotation_matrix((180., 0., 0.)).T)
    holds = orientation_assessment(flipped, triangle_bounds(flipped),
                                   cup_settings(), pitch_mm=0.5)
    assert holds['trapped_resin_mm3'] == pytest.approx(16 * 16 * 15, rel=0.02)
    assert holds['enclosed_cavity_count'] == 0  # a bowl is open, just not draining


def test_assessment_finds_a_sealed_cavity_and_the_face_no_pillar_can_reach():
    from voxelmill.geometry import triangle_bounds
    from voxelmill.validation import orientation_assessment
    sealed = np.asarray(manifold_triangles(
        m.Manifold.cube([20., 20., 20.], True) - m.Manifold.cube([16., 16., 16.], True)))
    report = orientation_assessment(sealed, triangle_bounds(sealed), cup_settings(), pitch_mm=0.5)
    assert report['enclosed_cavity_count'] == 1
    assert report['enclosed_cavity_mm3'] == pytest.approx(16 ** 3, rel=0.02)
    assert report['trapped_resin_mm3'] == 0  # sealing fixes this, not rotating
    # The cavity ceiling faces downward but no pillar from the plate reaches it.
    assert report['support_accessible_fraction'] < 0.8


def test_finalists_are_reranked_on_trapped_resin_not_on_arrival_order():
    from voxelmill.geometry import placement_for_triangles, _rank_finalists
    triangles = open_cup()
    resolved = cup_settings()
    draining = placement_for_triangles(triangles, resolved, rotation_deg=(0, 0, 0))
    trapping = placement_for_triangles(triangles, resolved, rotation_deg=(180, 0, 0))
    for placement in (draining, trapping):
        placement.search['score'] = [0.5, 0., 0., 0.]  # tie the cheap score
    # The trapping candidate is offered first; the assessment must overrule it.
    best = _rank_finalists(triangles, [trapping, draining], resolved, True, None, None)
    assert best.rotation_deg == draining.rotation_deg
    assert trapping.search['assessment']['trapped_resin_mm3'] > 3000
    assert best.search['finalist_score']['trapped_fraction'] == 0


def test_auto_placement_reports_a_real_assessment_instead_of_unassessed():
    from voxelmill.geometry import auto_placement
    placement = auto_placement(open_cup(), cup_settings())
    report = placement.search['assessment']
    assert report['status'] == 'complete'
    assert placement.search['unassessed'] == ['actual_support_volume']
    for field in ('trapped_resin_mm3', 'enclosed_cavity_mm3',
                  'support_accessible_fraction', 'center_of_mass_overhang'):
        assert field in report
    assert placement.search['finalist_score']['weights']['trapped'] > 0


def test_an_unclosed_grid_forfeits_the_void_terms_it_cannot_support():
    """Holes understate trapped resin, so they must not become an advantage."""
    from voxelmill.geometry import placement_for_triangles, _rank_finalists
    resolved = cup_settings()
    intact = np.asarray(open_cup())
    holed = np.ascontiguousarray(intact[~np.all(np.isclose(intact[:, :, 0], 10.), axis=1)])

    def rank(triangles):
        places = [placement_for_triangles(triangles, resolved, rotation_deg=angles)
                  for angles in ((180, 0, 0), (0, 0, 0))]
        for placement in places:
            placement.search['score'] = [0.5, 0., 0., 0.]
        return _rank_finalists(triangles, places, resolved, True, None, None), places

    _, closed_places = rank(intact)
    assert closed_places[0].search['assessment']['occupancy_closed'] is True
    assert closed_places[0].search['finalist_score']['void_terms_counted'] is True
    assert closed_places[0].search['finalist_score']['trapped_fraction'] > 0

    _, open_places = rank(holed)
    trapping = open_places[0]
    assert trapping.search['assessment']['occupancy_closed'] is False
    score = trapping.search['finalist_score']
    assert score['void_terms_counted'] is False
    # The void terms are still reported as measured, just not scored.
    assert score['total'] == pytest.approx(
        score['cheap_composite']
        + 1.5 * score['inaccessible_fraction']
        + 0.8 * min(score['center_of_mass_overhang'], 4.0))
