"""Contour/boundary sampling and per-object support overlays."""
from copy import deepcopy

import numpy as np
import manifold3d as m

from voxelmill.config import resolve_settings
from voxelmill.supports import (build_column_field, downward_contacts, select_contacts)
from test_supports import placed


def overhang_box():
    solid = m.Manifold.cube((20, 20, 4), True).translate((0, 0, 7))
    return placed(solid)


def test_contour_supports_add_perimeter_samples():
    triangles, _bounds = overhang_box()
    settings = resolve_settings(overrides={'support': {'spacing_mm': 4.0}})
    plain, _ = downward_contacts(triangles, settings)
    contour = deepcopy(settings)
    contour['support']['contour_supports'] = True
    extra, _ = downward_contacts(triangles, contour)
    assert len(extra) > len(plain)


def test_boundary_supports_sample_open_edges_not_closed_solids():
    closed, _ = overhang_box()
    settings = resolve_settings(overrides={'support': {'boundary_supports': True, 'spacing_mm': 4.0}})
    closed_points, _ = downward_contacts(closed, settings)
    off = resolve_settings(overrides={'support': {'boundary_supports': False, 'spacing_mm': 4.0}})
    off_points, _ = downward_contacts(closed, off)
    assert len(closed_points) == len(off_points)

    # Two triangles sharing an edge, no opposite face — an open ribbon.
    open_mesh = np.array([
        [[0, 0, 5], [10, 0, 5], [10, 4, 5]],
        [[0, 0, 5], [10, 4, 5], [0, 4, 5]],
    ], dtype=np.float32)
    open_on, _ = downward_contacts(open_mesh, settings)
    open_off, _ = downward_contacts(open_mesh, off)
    assert len(open_on) > len(open_off)


def test_object_groups_apply_per_part_pillar_overrides():
    left = m.Manifold.cube((8, 8, 3), True).translate((-12, 0, 6))
    right = m.Manifold.cube((8, 8, 3), True).translate((12, 0, 6))
    left_tri, _ = placed(left)
    right_tri, _ = placed(right)
    combined = np.concatenate((left_tri, right_tri))
    flat = combined.reshape(-1, 3)
    bounds = np.stack((flat.min(axis=0), flat.max(axis=0)))
    settings = resolve_settings(overrides={
        'support': {'automatic': True, 'base_type': 'none', 'spacing_mm': 4.0,
                    'pillar_diameter_mm': 1.2}})
    field = build_column_field(combined, bounds, settings, pitch_mm=.5)
    heavy = deepcopy(settings)
    heavy['support']['pillar_diameter_mm'] = 1.6
    groups = [
        {'triangles': left_tri, 'settings': settings, 'override_keys': ()},
        {'triangles': right_tri, 'settings': heavy, 'override_keys': ('pillar_diameter_mm',)},
    ]
    _contacts, metrics = select_contacts(
        combined, field, settings, object_groups=groups)
    assert metrics['object_groups'] == 2
    records = metrics['object_contact_parameters']
    assert records
    assert all(row['parameters']['pillar_diameter_mm'] == 1.6 for row in records)


def _reference_perimeter(faces, spacing):
    """The original per-edge loop; the vector form must reproduce it exactly."""
    def segment(a, b):
        steps = max(1, int(np.ceil(np.linalg.norm(b - a) / spacing)))
        ts = np.linspace(0.0, 1.0, steps + 1)
        return (1.0 - ts)[:, None] * a + ts[:, None] * b
    counts, segments = {}, []
    for face in faces:
        for i in range(3):
            a, b = face[i], face[(i + 1) % 3]
            ra, rb = tuple(np.round(a, 5)), tuple(np.round(b, 5))
            key = (ra, rb) if ra <= rb else (rb, ra)
            counts[key] = counts.get(key, 0) + 1
            segments.append((key, a, b))
    points = [segment(a, b) for key, a, b in segments if counts[key] == 1]
    return np.concatenate(points) if points else np.empty((0, 3))


def test_vectorized_perimeter_samples_match_the_edge_loop():
    from voxelmill.supports import _perimeter_samples
    rng = np.random.default_rng(5)
    # Rounded coordinates force shared and reversed edges; the zero face is degenerate.
    for faces in (np.round(rng.normal(size=(400, 3, 3)), 1), rng.normal(size=(50, 3, 3)),
                  np.zeros((2, 3, 3))):
        for spacing in (0.25, 2.0):
            assert np.array_equal(_perimeter_samples(faces, spacing),
                                  _reference_perimeter(faces, spacing))
