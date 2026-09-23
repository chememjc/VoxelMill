"""Unroutable already-attached contacts are dropped, true overhangs are not."""
import numpy as np
import manifold3d as m

from voxelmill.config import resolve_settings
from voxelmill.contracts import Diagnostic, SupportGraph, ValidationReport
from voxelmill.supports import (SupportPlan, apply_attached_unroutable_drops,
                               apply_support_validation, attached_below,
                               plan_supports)
from test_supports import placed, stacked_with_gap


def test_attached_below_is_printer_pitch_neighbourhood_one_layer_under():
    solid = m.Manifold.cube((10, 10, 10), True).translate((0, 0, 5))
    triangles, _ = placed(solid)
    settings = resolve_settings()
    assert attached_below(triangles, [[0, 0, 10]], settings) == [True]
    assert attached_below(triangles, [[0, 0, 20]], settings) == [False]
    assert attached_below(triangles, [[0, 0, 0.02]], settings) == [False]


def test_attached_unroutable_drop_does_not_touch_true_overhangs_or_protected():
    solid = m.Manifold.cube((10, 10, 10), True).translate((0, 0, 5))
    triangles, _ = placed(solid)
    settings = resolve_settings()
    plan = SupportPlan(SupportGraph(), np.empty((0, 2)), [], [
        Diagnostic('support_unroutable', 'blocked', position_mm=[0, 0, 10]),
        Diagnostic('support_unroutable', 'blocked', position_mm=[0, 0, 20]),
    ], {'contacts_failed': 2, 'unroutable_positions': [[0, 0, 10], [0, 0, 20]]})
    apply_attached_unroutable_drops(plan, triangles, settings)
    assert plan.metrics['contacts_dropped_attached'] == 1
    assert plan.metrics['contacts_failed'] == 1
    assert [d.code for d in plan.diagnostics] == ['support_dropped_attached', 'support_unroutable']
    report = ValidationReport()
    apply_support_validation(report, plan, settings)
    assert report.checks['support_routes'] == 'fail'

    protected = SupportPlan(SupportGraph(), np.empty((0, 2)), [], [
        Diagnostic('support_unroutable', 'blocked', position_mm=[0, 0, 10]),
    ], {'contacts_failed': 1, 'unroutable_positions': [[0, 0, 10]]})
    apply_attached_unroutable_drops(protected, triangles, settings, protected=[[0, 0, 10]])
    assert protected.metrics['contacts_dropped_attached'] == 0
    assert protected.metrics['contacts_failed'] == 1


def test_short_gap_overhangs_still_fail_when_the_tip_cannot_fit():
    settings = resolve_settings(overrides={'support': {'min_tip_length_mm': 1.5}})
    solid = stacked_with_gap(1.0)
    triangles, bounds = placed(solid)
    plan, _ = plan_supports(triangles, bounds, settings)
    assert plan.metrics['contacts_failed'] > 0
    assert plan.metrics.get('contacts_dropped_attached', 0) == 0
    assert any(d.code == 'support_unroutable' for d in plan.diagnostics)


def test_plan_supports_honours_the_drop_switch():
    settings = resolve_settings(overrides={'support': {'drop_attached_unroutable': False}})
    assert settings['support']['drop_attached_unroutable'] is False
    solid = m.Manifold.cube((10, 10, 10), True).translate((0, 0, 5))
    triangles, bounds = placed(solid)
    plan, _ = plan_supports(triangles, bounds, settings)
    assert plan.metrics.get('contacts_dropped_attached', 0) == 0
