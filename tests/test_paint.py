"""Paint-on blockers and enforcers are manual and honoured by contact selection."""
import numpy as np
import pytest
import manifold3d as m

from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.geometry import manifold_triangles
from voxelmill.paint import apply_paint, brush_centroids, normalize_paint
from voxelmill.supports import build_column_field, plan_supports, select_contacts
from test_supports import placed


def box():
    solid = m.Manifold.cube((20, 20, 4), True).translate((0, 0, 7))
    return placed(solid)


def test_normalize_paint_deduplicates_and_rejects_unknown_fields():
    paint = normalize_paint({'blocked': [[1, 2, 3], [1.0000004, 2, 3]], 'enforced': [[4, 5, 6]]})
    assert paint['blocked'] == [[1.0, 2.0, 3.0]]
    with pytest.raises(VoxelMillError) as error:
        normalize_paint({'blocked': [], 'mystery': []})
    assert error.value.code == 'invalid_paint'


def test_blocked_faces_are_not_automatic_contacts_and_do_not_fail_coverage():
    triangles, bounds = box()
    settings = resolve_settings(overrides={'support': {'base_type': 'none', 'automatic': True}})
    field = build_column_field(triangles, bounds, settings, pitch_mm=.5)
    plain, metrics = select_contacts(triangles, field, settings)
    assert len(plain) > 0
    from voxelmill.contact_parameters import contact_key
    islands = {contact_key([*island['position_mm'], island['z_mm']]) for island in field.islands}
    target = next(row for row in plain if contact_key(row) not in islands)
    paint = {'blocked': [list(target)], 'enforced': []}
    filtered, painted_metrics = select_contacts(triangles, field, settings, paint=paint)
    assert painted_metrics['paint']['samples_blocked'] >= 1
    from scipy.spatial import cKDTree
    distance = cKDTree(np.asarray(paint['blocked'])).query(np.asarray(filtered).reshape(-1, 3),
                                                           distance_upper_bound=0.2)[0]
    assert not np.isfinite(distance).any()


def test_enforced_marks_become_contacts_even_when_automatic_is_off():
    triangles, bounds = box()
    settings = resolve_settings(overrides={'support': {'base_type': 'none', 'automatic': False}})
    field = build_column_field(triangles, bounds, settings, pitch_mm=.5)
    mark = [0.0, 0.0, 9.0]
    contacts, metrics = select_contacts(triangles, field, settings, paint={'enforced': [mark]})
    assert len(contacts) == 1
    np.testing.assert_allclose(contacts[0], mark)
    assert metrics['paint']['enforced_contacts'] == 1


def test_block_wins_over_enforce_on_the_same_mark():
    samples = np.array([[0.0, 0.0, 5.0], [3.0, 0.0, 5.0]])
    kept, enforced, metrics = apply_paint(samples, {
        'blocked': [[0.0, 0.0, 5.0]], 'enforced': [[0.0, 0.0, 5.0], [3.0, 0.0, 5.0]],
    }, spacing=3.0)
    assert len(kept) == 1
    np.testing.assert_allclose(kept[0], [3.0, 0.0, 5.0])
    assert len(enforced) == 1
    np.testing.assert_allclose(enforced[0], [3.0, 0.0, 5.0])
    assert metrics['block_wins_over_enforce'] is True


def test_island_contacts_are_not_dropped_by_block_paint():
    triangles, bounds = box()
    settings = resolve_settings(overrides={'support': {'base_type': 'none'}})
    field = build_column_field(triangles, bounds, settings, pitch_mm=.5)
    if not field.islands:
        pytest.skip('this solid has no raster islands')
    island = [*field.islands[0]['position_mm'], field.islands[0]['z_mm']]
    contacts, _ = select_contacts(triangles, field, settings, paint={'blocked': [island]})
    from voxelmill.contact_parameters import contact_key
    assert contact_key(island) in {contact_key(row) for row in contacts}


def test_brush_always_paints_at_least_the_nearest_triangle():
    triangles = manifold_triangles(m.Manifold.cube((4, 4, 4), True))
    marks = brush_centroids(triangles, [0, 0, 2], 0.01)
    assert len(marks) >= 1


def test_plan_supports_honours_paint(tmp_path):
    triangles, bounds = box()
    settings = resolve_settings(overrides={'support': {'base_type': 'none', 'automatic': False}})
    plan, _ = plan_supports(triangles, bounds, settings, extra_contacts=(),
                            paint={'enforced': [[0.0, 0.0, 9.0]]})
    assert plan.metrics['contacts_requested'] >= 1
    assert plan.metrics['paint']['enforced_contacts'] == 1
