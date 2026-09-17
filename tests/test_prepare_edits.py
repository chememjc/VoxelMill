"""Preparation persistence and contact-edit regression tests."""
import json

import manifold3d as m
import pytest

pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules')

from voxelmill.cli import _contacts
from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.geometry import manifold_triangles
from voxelmill.gui.document import Document
from voxelmill.mesh import write_stl
from voxelmill.pipeline import prepare


@pytest.fixture
def sphere(tmp_path):
    path = tmp_path / 'sphere.stl'
    write_stl(path, manifold_triangles(m.Manifold.sphere(4, 32)))
    return path


def small_settings():
    return resolve_settings(overrides={
        'process': {'layer_height_mm': 0.2},
        'printer': {
            'pixels': [1000, 800],
            'pixel_pitch_mm': [0.1, 0.1],
            'build_mm': [100.0, 80.0, 165.0],
        },
    })


def test_prepare_project_round_trip_preserves_pose_lift_and_edits(tmp_path, sphere):
    project = tmp_path / 'prepared.voxmil'
    rotation = (17.0, -8.0, 23.0)
    offset = (1.25, -2.5)
    manual = [[1.25, -2.5, 0.0]]
    removed = [[0.0, 0.0, 3.0]]

    prepare(sphere, small_settings(), rotate=rotation, center_offset=offset, lift_mm=0.0,
            project=project, manual_contacts=manual, removed_contacts=removed,
            drainage=False)
    reopened = Document.load(project, tmp_path / 'extract')

    assert reopened.rotation_deg == pytest.approx(rotation)
    assert reopened.center_offset_mm == pytest.approx(offset)
    assert reopened.model_lift_mm == 0.0
    assert reopened.manual_contacts == manual
    assert reopened.removed_contacts == removed
    assert reopened.source is not None and reopened.source.exists()
    assert reopened.dirty is False


def test_prepare_preserves_removed_contact_in_support_metrics(tmp_path, sphere, monkeypatch):
    import voxelmill.pipeline as pipeline

    removed = [[0.0, 0.0, 3.0]]
    calls = []
    real_plan = pipeline.plan_supports

    def capture(*args, **kwargs):
        plan, raft = real_plan(*args, **kwargs)
        calls.append((list(kwargs.get('removed_contacts', ())), plan.metrics.copy()))
        return plan, raft

    monkeypatch.setattr(pipeline, 'plan_supports', capture)
    report = prepare(sphere, small_settings(), removed_contacts=removed, drainage=False)

    assert calls
    assert all(call[0] == removed for call in calls)
    assert all(metrics['suppressed_contacts'] == 1 for _, metrics in calls)
    assert report['validation']['metrics']['supports']['suppressed_contacts'] == 1


def test_contacts_accepts_empty_json_array_and_rejects_bad_points(tmp_path):
    empty = tmp_path / 'empty.json'
    empty.write_text('[]')
    assert _contacts(empty) == []

    malformed = [
        [1.0, 2.0, 3.0],
        [[1.0, 2.0]],
        [[1.0, 2.0, 3.0, 4.0]],
        [[1.0, 2.0, float('nan')]],
        {'x': 1.0},
    ]
    for index, points in enumerate(malformed):
        path = tmp_path / f'bad-{index}.json'
        path.write_text(json.dumps(points))
        with pytest.raises(VoxelMillError):
            _contacts(path)
