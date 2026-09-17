"""Routing choices and brace geometry must respond to the exposed policy."""
import json
import numpy as np
import pytest
import manifold3d as m

from voxelmill.cli import main
from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError, ValidationReport
from voxelmill.supports import route_contacts, build_column_field, _brace, apply_support_validation
from voxelmill.support_example import support_example
from test_supports import placed


def field_for(solid, settings):
    triangles, bounds = placed(solid)
    return build_column_field(triangles, bounds, settings, pitch_mm=.25)


def test_forbidden_model_anchor_is_an_export_failure_even_for_manual_contact():
    model = (m.Manifold.cube((40, 40, 5)).translate((-20, -20, 0)) +
             m.Manifold.cube((20, 20, 2)).translate((-10, -10, 10)))
    settings = resolve_settings()
    field = field_for(model, settings)
    permitted, _ = route_contacts([[0, 0, 10]], field, settings)
    settings['support']['allow_part_to_part'] = False
    forbidden, _ = route_contacts([[0, 0, 10]], field, settings)
    assert permitted.metrics['routing']['model_anchor'] == 1
    assert forbidden.metrics['routing']['model_anchor'] == 0
    assert forbidden.metrics['contacts_failed'] == 1
    assert forbidden.metrics['contacts_blocked_by_policy'] == 1
    report = ValidationReport()
    apply_support_validation(report, forbidden, settings)
    assert report.checks['support_routes'] == 'fail'
    assert any(d.code == 'incomplete_support_routes' for d in report.diagnostics)


def test_avoidance_changes_candidate_selection_with_both_routes_available():
    model = (m.Manifold.cube((2, 2, 15)).translate((-1, -1, 0)) +
             m.Manifold.cube((10, 10, 2)).translate((-5, -5, 20)))
    settings = resolve_settings()
    field = field_for(model, settings)
    counts = []
    for avoidance in (0, .5, .9, 1):
        settings['support']['part_to_part_avoidance'] = avoidance
        plan, _ = route_contacts([[0, 0, 20]], field, settings)
        assert plan.metrics['contacts_failed'] == 0
        counts.append(plan.metrics['routing']['model_anchor'])
    assert counts == [1, 1, 0, 0]
    settings['support']['allow_part_to_part'] = False
    settings['support']['part_to_part_avoidance'] = 0
    plan, _ = route_contacts([[0, 0, 20]], field, settings)
    assert plan.metrics['routing']['branched'] == 1


@pytest.mark.parametrize('changes', [
    {'allow_part_to_part': 1}, {'part_to_part_avoidance': -0.1},
    {'part_to_part_avoidance': 1.1}, {'brace_diameter_mm': -1},
    {'brace_max_distance_mm': float('nan')},
])
def test_invalid_policy_and_brace_parameters_fail(changes):
    with pytest.raises(VoxelMillError):
        resolve_settings(overrides={'support': changes})


def test_brace_diameter_and_neighbour_distance_change_real_solids():
    settings = resolve_settings(overrides={'support': {
        'brace_start_height_mm': 3, 'brace_spacing_mm': 10,
        'brace_diameter_mm': .8, 'brace_max_distance_mm': 6}})
    pillars = [(-2.5, 0, 15, .6), (2.5, 0, 15, .6)]
    solids = []
    assert _brace(pillars, settings, solids) == 2
    assert [s.bounding_box()[5] - s.bounding_box()[2] for s in solids] == pytest.approx([.8, .8])
    settings['support']['brace_max_distance_mm'] = 4
    assert _brace(pillars, settings, []) == 0


def test_braces_do_not_cut_through_model_even_when_the_centerline_misses():
    # The obstacle is offset in Y so the strut centerline is free while its
    # 0.8 mm diameter overlaps. Only the first brace intersects it.
    obstacle = m.Manifold.cube((1, .2, 2)).translate((-.5, .25, 2))
    settings = resolve_settings(overrides={'support': {
        'brace_start_height_mm': 3, 'brace_spacing_mm': 10,
        'brace_diameter_mm': .8, 'brace_max_distance_mm': 6}})
    field = field_for(obstacle, settings)
    evidence, solids = {}, []
    assert _brace([(-2.5, 0, 15, .6), (2.5, 0, 15, .6)], settings, solids,
                  field=field, evidence=evidence) == 1
    assert evidence['collision_rejected'] == 1
    assert solids[0].bounding_box()[2] == pytest.approx(12.6)


def test_cli_policy_preset_roundtrip_and_example(tmp_path, capsys):
    output = tmp_path / 'support.json'
    assert main(['preset', 'save', '--output', str(output), '--no-part-to-part-supports',
                 '--part-to-part-avoidance', '.25', '--set', 'support.brace_diameter_mm=0.8']) == 0
    capsys.readouterr()
    stl = tmp_path / 'sample.stl'
    assert main(['support-example', '--support-preset', str(output), '--output', str(stl)]) == 0
    payload = json.loads(capsys.readouterr().out)
    expected = support_example(resolve_settings(overrides={'support': {
        'allow_part_to_part': False, 'part_to_part_avoidance': .25, 'brace_diameter_mm': .8}}))
    assert payload['metrics']['routing'] == expected['metrics']['routing']
    assert payload['metrics']['allow_part_to_part'] is False
    assert payload['triangle_counts']['supports'] > 0 and stl.stat().st_size > 84


def test_rejected_braces_count_toward_the_work_limit(monkeypatch):
    import voxelmill.supports as supports
    monkeypatch.setattr(supports, '_brace_clear', lambda *args: False)
    settings = resolve_settings(overrides={'support': {
        'brace_start_height_mm': 1, 'brace_spacing_mm': .1}})
    evidence = {}
    assert _brace([(0, 0, 15), (3, 0, 15)], settings, [], limit=3,
                  field=object(), evidence=evidence) == 0
    assert evidence == {'collision_rejected': 3, 'examined': 3, 'capped': True}


def test_sub_float_precision_brace_interval_fails_instead_of_looping():
    settings = resolve_settings(overrides={'support': {
        'brace_start_height_mm': 1, 'brace_spacing_mm': 1e-100}})
    with pytest.raises(VoxelMillError, match='too small to advance'):
        _brace([(0, 0, 15), (3, 0, 15)], settings, [])


def test_example_refuses_a_report_path_that_would_overwrite_its_stl(tmp_path, capsys):
    target = tmp_path / 'existing.stl'
    target.write_text('keep me')
    assert main(['support-example', '--output', str(target), '--report', str(target)]) != 0
    assert 'different paths' in capsys.readouterr().err
    assert target.read_text() == 'keep me'
