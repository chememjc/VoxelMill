"""Whole small model connectors select on the gap and retain route failures."""
from copy import deepcopy
import json

import manifold3d as m
import numpy as np
import pytest

from voxelmill.config import resolve_settings
from voxelmill.contracts import ValidationReport
from voxelmill.geometry import manifold_triangles
from voxelmill.presets import load_preset, save_preset
from voxelmill.supports import build_column_field, route_contacts, apply_support_validation


def scene(**changes):
    settings = resolve_settings(overrides={'support': {
        'allow_part_to_part': True,
        'small_pillar_mode': 'model', 'small_pillar_diameter_mm': .4,
        'small_pillar_max_length_mm': 5, 'small_pillar_upper_depth_mm': .25,
        'small_pillar_lower_depth_mm': .25, 'part_to_part_avoidance': 0,
        **changes}})
    model = (m.Manifold.cube((40, 40, 5)).translate((-20, -20, 0)) +
             m.Manifold.cube((20, 20, 2)).translate((-10, -10, 10)))
    field = build_column_field(manifold_triangles(model).astype(np.float32),
                              np.array(model.bounding_box()).reshape(2, 3), settings, pitch_mm=.5)
    return settings, model, field


def test_small_model_mode_spans_the_whole_gap_with_independent_buried_ends():
    settings, model, field = scene()
    plan, base = route_contacts([[0, 0, 10]], field, settings)
    assert base is None
    assert plan.metrics['small_model_pillars'] == 1
    assert len(plan.solids) == 1
    assert plan.solids[0].bounding_box() == pytest.approx((-.2, -.2, 4.75, .2, .2, 10.25))
    assert [edge.kind for edge in plan.graph.edges] == ['small_model']
    # Both initially separate pieces of model actually join the support.
    assert len((model + plan.solids[0]).decompose()) == 1
    other = deepcopy(settings)
    other['support'].update(small_pillar_upper_depth_mm=.5, small_pillar_lower_depth_mm=.1)
    deeper, _ = route_contacts([[0, 0, 10]], field, other)
    assert deeper.solids[0].bounding_box()[2::3] == pytest.approx((4.9, 10.5))


@pytest.mark.parametrize('limit, expected', [(0, 0), (4.99, 0), (5, 1), (5.01, 1)])
def test_selection_uses_whole_gap_including_the_top_segment(limit, expected):
    settings, _, field = scene(small_pillar_max_length_mm=limit)
    plan, _ = route_contacts([[0, 0, 10]], field, settings)
    assert plan.metrics['small_model_pillars'] == expected
    assert plan.metrics['contacts_failed'] == 0


def test_model_mode_never_replaces_a_plate_pillar():
    settings, _, field = scene(small_pillar_max_length_mm=100)
    # Use a separate suspended model with a genuinely clear plate column.
    model = m.Manifold.cube((4, 4, 2)).translate((-2, -2, 10))
    field = build_column_field(manifold_triangles(model).astype(np.float32),
                              np.array(model.bounding_box()).reshape(2, 3), settings, pitch_mm=.5)
    plan, _ = route_contacts([[0, 0, 10]], field, settings)
    assert plan.metrics['routing']['vertical'] == 1
    assert plan.metrics['small_pillars'] == 0


@pytest.mark.parametrize('changes', [
    {'small_pillar_upper_depth_mm': 2.1}, {'small_pillar_lower_depth_mm': 5.1},
    {'allow_part_to_part': False},
])
def test_impossible_or_forbidden_small_connector_retains_failed_route(changes):
    settings, _, field = scene(**changes)
    plan, _ = route_contacts([[0, 0, 10]], field, settings)
    assert plan.metrics['contacts_failed'] == 1
    assert plan.metrics['small_model_pillars'] == 0
    report = ValidationReport()
    apply_support_validation(report, plan, settings)
    assert report.checks['support_routes'] == 'fail'


def test_partial_reference_preset_keeps_its_explanations_when_shared(tmp_path):
    preset = load_preset('chitubox-mars5')
    path = tmp_path / 'reference.json'
    save_preset(path, preset)
    assert load_preset(path) == preset
    assert any('20 degrees' in note for note in preset['notes'])
    assert any('small_pillar_max_length_mm' in note for note in preset['notes'])
    assert any('model_anchor_diameter_mm' in note for note in preset['notes'])


def test_cli_settings_and_portable_files_keep_new_geometry(tmp_path, capsys):
    from voxelmill.cli import main
    path = tmp_path / 'model.json'
    assert main(['preset', 'save', '--output', str(path), '--small-pillar-mode', 'model',
                 '--small-pillar-shape', 'cylinder', '--model-anchor-shape', 'cylinder',
                 '--set', 'support.small_pillar_diameter_mm=0.4',
                 '--set', 'support.small_pillar_max_length_mm=5',
                 '--set', 'support.small_pillar_upper_depth_mm=0.25']) == 0
    capsys.readouterr()
    assert load_preset(path)['support']['small_pillar_shape'] == 'cylinder'
    assert main(['profile', '--support-preset', str(path)]) == 0
    settings = json.loads(capsys.readouterr().out)['settings']
    assert settings['support']['small_pillar_upper_depth_mm'] == .25
