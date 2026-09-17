import math

import numpy as np
import pytest
import manifold3d as m

from voxelmill import _native
from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError, ResourceBudget
from voxelmill.geometry import manifold_triangles, mesh_to_manifold
from voxelmill.repair import choose_voxel_size, voxel_repair


def aggressive(**repair):
    return resolve_settings(overrides={'repair': {'aggressiveness': 'aggressive', **repair}})


def volume(occupancy, pitch=1.0):
    nz, ny, nx = occupancy.shape
    grid = _native.VoxelVolume(nx, ny, nz, 0., 0., 0., pitch, pitch, pitch)
    for k in range(nz):
        grid.set_slice(k, occupancy[k].astype(np.uint8))
    return grid


def test_single_voxel_boundary_is_a_valid_unit_solid():
    occupancy = np.zeros((3, 3, 3), np.uint8)
    occupancy[1, 1, 1] = 1
    grid = volume(occupancy)
    assert grid.make_well_composed()['added_voxels'] == 0
    vertices, faces = grid.extract_surface()
    assert (len(vertices), len(faces)) == (8, 12)
    solid, _ = mesh_to_manifold(vertices[faces])
    assert solid.volume() == pytest.approx(1.0)
    assert solid.genus() == 0


@pytest.mark.parametrize('offsets,added', [
    ([(1, 1, 1), (2, 2, 2)], 2),   # corner-only pair: ambiguous marching cube
    ([(1, 1, 1), (1, 2, 2)], 1),   # face-diagonal pair: nonmanifold edge
    ([(1, 1, 1), (2, 1, 2)], 1),
])
def test_critical_configurations_are_repaired_by_adding_material(offsets, added):
    occupancy = np.zeros((4, 4, 4), np.uint8)
    for k, j, i in offsets:
        occupancy[k, j, i] = 1
    grid = volume(occupancy)
    report = grid.make_well_composed()
    assert report['added_voxels'] == added
    assert report['converged'] and not report['boundary_blocked']
    solid, _ = mesh_to_manifold(grid.extract_surface()[0][grid.extract_surface()[1]])
    assert solid.volume() == pytest.approx(len(offsets) + added)


def test_repair_never_removes_material():
    occupancy = np.zeros((6, 6, 6), np.uint8)
    occupancy[1:5, 1:5, 1:5] = 1
    occupancy[2, 2, 2] = 0
    grid = volume(occupancy)
    before = grid.occupied()
    grid.make_well_composed()
    assert grid.occupied() >= before
    vertices, faces = grid.extract_surface()
    solid, _ = mesh_to_manifold(vertices[faces])
    # Outer shell plus the inward-oriented cavity shell.
    assert len(solid.decompose()) == 2
    assert solid.volume() == pytest.approx(63.0)


def test_boundary_conflicts_are_counted_and_repair_reserves_a_margin():
    # A fix that would need a voxel outside the grid is counted, never applied
    # silently. voxel_repair sizes its grid with a two-voxel empty margin so the
    # counter stays zero; a nonzero counter is an error, not a warning.
    occupancy = np.zeros((2, 2, 2), np.uint8)
    occupancy[0, 0, 0] = occupancy[1, 1, 1] = 1
    assert volume(occupancy).make_well_composed()['boundary_blocked'] == 0
    triangles = manifold_triangles(m.Manifold.cube((4, 4, 4)))
    settings = aggressive(voxel_size_mm=0.5, max_deviation_mm=0.5)
    _, report = voxel_repair(triangles, [[0, 0, 0], [4, 4, 4]], settings)
    assert report['voxel_grid'] == [13, 13, 13]  # 8 model voxels plus 2 each side, plus a node
    assert report['well_composed_added_voxels'] == 0


def test_voxel_repair_requires_explicit_permission():
    triangles = manifold_triangles(m.Manifold.cube((4, 4, 4)))
    with pytest.raises(VoxelMillError, match='repair_not_permitted|aggressive'):
        voxel_repair(triangles, [[0, 0, 0], [4, 4, 4]], resolve_settings())


def test_voxel_size_is_derived_from_max_deviation_and_budget_is_actionable():
    from voxelmill.repair import MAX_VOXEL_BYTES_FRACTION, _grid

    max_deviation_mm = 0.2
    headroom = 2 * max_deviation_mm / math.sqrt(3) * 0.9
    deviation_ceiling = 2 * max_deviation_mm / math.sqrt(3)
    settings = aggressive(max_deviation_mm=max_deviation_mm, voxel_size_mm=0.0)

    # Small volume still derives the 0.9-headroom pitch.
    size, dims, needed = choose_voxel_size([[0, 0, 0], [10, 10, 10]], settings, ResourceBudget())
    assert size == pytest.approx(headroom)
    assert size * math.sqrt(3) / 2 <= max_deviation_mm + 1e-12
    assert needed == float(np.prod(dims + 2))

    # Fits at the deviation ceiling but not at the headroom pitch → coarsen.
    budget = ResourceBudget(memory_gib=0.5)
    ceiling_bytes = budget.memory_gib * 1024**3 * MAX_VOXEL_BYTES_FRACTION
    bounds = [[0, 0, 0], [120, 120, 120]]
    _, derived_dims = _grid(bounds, headroom)
    assert float(np.prod(derived_dims + 2)) > ceiling_bytes
    _, cap_dims = _grid(bounds, deviation_ceiling)
    assert float(np.prod(cap_dims + 2)) <= ceiling_bytes
    size, dims, needed = choose_voxel_size(bounds, settings, budget)
    assert headroom <= size <= deviation_ceiling + 1e-12
    assert size > headroom
    assert needed <= ceiling_bytes
    assert needed == float(np.prod(dims + 2))

    # Cannot fit even at the deviation ceiling → still raise repair_budget.
    with pytest.raises(VoxelMillError, match='memory budget') as error:
        choose_voxel_size([[0, 0, 0], [200, 200, 200]], aggressive(max_deviation_mm=0.01),
                          ResourceBudget(memory_gib=0.5))
    assert error.value.code == 'repair_budget'
    assert error.value.details['smallest_affordable_voxel_size_mm'] > 0
    assert 'remedy' in error.value.details

    # Explicit voxel_size_mm that does not fit never coarsens.
    explicit = aggressive(max_deviation_mm=2.0, voxel_size_mm=headroom)
    with pytest.raises(VoxelMillError, match='memory budget') as error:
        choose_voxel_size(bounds, explicit, budget)
    assert error.value.code == 'repair_budget'
    assert error.value.details['voxel_size_mm'] == pytest.approx(headroom)


def test_voxel_repair_records_voxel_size_coarsening(monkeypatch):
    triangles = manifold_triangles(m.Manifold.cube((4, 4, 4)))
    derived = 2 * 0.5 / math.sqrt(3) * 0.9
    settings = aggressive(max_deviation_mm=0.5, voxel_size_mm=0.0)
    _, report = voxel_repair(triangles, [[0, 0, 0], [4, 4, 4]], settings)
    assert report['voxel_size_derived_mm'] == pytest.approx(derived)
    assert report['voxel_size_mm'] == pytest.approx(derived)
    assert report['voxel_size_coarsened'] is False

    # choose_voxel_size already covered real coarsening; here confirm voxel_repair
    # copies a coarsened choice into the report without re-deriving.
    coarsened_pitch = derived * 1.05
    from voxelmill import repair as repair_mod

    def fake_choose(bounds, settings, budget):
        _, dims = repair_mod._grid(bounds, coarsened_pitch)
        return coarsened_pitch, dims, float(np.prod(dims + 2))

    monkeypatch.setattr(repair_mod, 'choose_voxel_size', fake_choose)
    _, report = voxel_repair(triangles, [[0, 0, 0], [4, 4, 4]], settings)
    assert report['voxel_size_derived_mm'] == pytest.approx(derived)
    assert report['voxel_size_mm'] == pytest.approx(coarsened_pitch)
    assert report['voxel_size_coarsened'] is True


def test_defective_solid_is_rejected_conservatively_and_repaired_aggressively():
    triangles = manifold_triangles(m.Manifold.cube((6, 6, 6)))
    holed = np.delete(triangles, 0, axis=0)
    with pytest.raises(VoxelMillError, match='accepted positively oriented'):
        mesh_to_manifold(holed)
    settings = aggressive(voxel_size_mm=0.25, max_deviation_mm=0.25)
    with pytest.raises(VoxelMillError) as error:
        voxel_repair(holed, [[0, 0, 0], [6, 6, 6]], settings, source_volume_mm3=216.0)
    assert error.value.code == 'repair_deviation'
    assert not error.value.details['surface_deviation']['passed']
    settings = aggressive(voxel_size_mm=.25, max_deviation_mm=2.)
    solid, report = voxel_repair(holed, [[0, 0, 0], [6, 6, 6]], settings, source_volume_mm3=216.0)
    assert solid.status() == m.Error.NoError
    assert report['volume_after_mm3'] == pytest.approx(216.0, rel=0.02)
    assert report['deviation_within_limit']
    assert report['removed_volume_mm3'] >= 0
    assert report['self_intersections'] == 'none_by_construction'


def test_repair_reports_open_contours_of_an_open_surface():
    # Removing a whole wall opens every scanline in the affected layers.
    triangles = manifold_triangles(m.Manifold.cube((6, 6, 6)))
    keep = triangles.reshape(-1, 9)[:, [0, 3, 6]].max(axis=1) > 0.001
    settings = aggressive(voxel_size_mm=0.5, max_deviation_mm=0.5)
    with pytest.raises(VoxelMillError, match='rasterized empty'):
        voxel_repair(triangles[keep], [[0, 0, 0], [6, 6, 6]], settings)


def test_repair_closes_a_local_hole_without_inventing_bulk_material():
    triangles = manifold_triangles(m.Manifold.cube((6, 6, 6)).refine_to_length(1.0))
    holed = np.delete(triangles, [0, 1], axis=0)
    settings = aggressive(voxel_size_mm=0.25, max_deviation_mm=0.5)
    solid, report = voxel_repair(holed, [[0, 0, 0], [6, 6, 6]], settings, source_volume_mm3=216.0)
    assert solid.status() == m.Error.NoError
    assert report['volume_after_mm3'] == pytest.approx(216.0, rel=0.02)


def test_smoothing_needs_deviation_headroom():
    triangles = manifold_triangles(m.Manifold.cube((4, 4, 4)))
    settings = aggressive(voxel_size_mm=0.5, max_deviation_mm=0.4, smooth_iterations=2)
    with pytest.raises(VoxelMillError, match='headroom'):
        voxel_repair(triangles, [[0, 0, 0], [4, 4, 4]], settings)
    settings = aggressive(voxel_size_mm=0.25, max_deviation_mm=0.4, smooth_iterations=2)
    solid, report = voxel_repair(triangles, [[0, 0, 0], [4, 4, 4]], settings)
    assert 0 < report['smoothing_max_displacement_mm'] <= 0.4 - 0.25 * math.sqrt(3) / 2 + 1e-12
    assert report['max_deviation_bound_mm'] <= 0.4 + 1e-12
    assert solid.status() == m.Error.NoError


def test_surface_distance_checks_triangle_interiors_and_missing_features():
    original = manifold_triangles(m.Manifold.cube((6, 6, 6)))
    holed = np.delete(original, 0, axis=0)
    # All remaining vertices coincide; a vertices-only comparison misses the hole.
    result = _native.verify_surface_deviation(original, holed, .25)
    assert not result['passed'] and result['sampled_lower_bound_mm'] > .25
    assert result['certified_upper_bound_mm'] is None
    moved = original + [0, 0, .1]
    result = _native.verify_surface_deviation(original, moved, .2)
    assert result['passed'] and result['certified_upper_bound_mm'] <= .2 + 1e-10


def test_surface_distance_is_cancellable():
    from voxelmill.contracts import CancellationToken, Canceled
    triangles = manifold_triangles(m.Manifold.cube((4, 4, 4)))
    token = CancellationToken()
    token.cancel()
    with pytest.raises(Canceled):
        _native.verify_surface_deviation(triangles, triangles, .1, lambda *args: token.check())
