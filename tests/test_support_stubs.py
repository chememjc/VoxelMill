"""Stubs for knife-edge islands, and the words an unroutable contact is reported with.

A slightly overhanging wall that meets a sloped roof at an acute corner, not
aligned with the pixel grid, prints one-pixel islands that touch the layer
below only at a corner. Material is beside or a fraction of a millimetre below
each one, so no pillar, branch or tip fits; before stubs such a plate could not
export. The wedge below reproduces that geometry class (it is not the user's
part, which is not in the tree).
"""
import math

import manifold3d as m
import numpy as np
import pytest

from voxelmill import pipeline, supports
from voxelmill.collisions import edge_kind_name, support_overlaps
from voxelmill.config import resolve_settings
from voxelmill.contracts import CancellationToken, SupportEdge, SupportGraph, SupportNode
from voxelmill.geometry import manifold_triangles
from voxelmill.gui.faults import DEFAULT_FAULT_COLOR, fault_color
from voxelmill.mesh import write_stl
from voxelmill.supports import (apply_support_validation, build_column_field, explain_unroutable,
                                route_contacts)
from voxelmill.validation import ValidationReport


def knife_edge_wedge(overhang_deg=3.0, roof_deg=37.0, roof_azimuth_deg=45.0, height=8.0):
    """A prism whose x=0 wall overhangs slightly and whose roof slopes up."""
    rise = math.tan(math.radians(roof_deg))
    sx, sy = math.sin(math.radians(roof_azimuth_deg)), math.cos(math.radians(roof_azimuth_deg))
    points = []
    for x, y in ((0, -3), (0, 3), (4, -3), (4, 3)):
        top = height - (x * sx + y * sy) * rise
        lean = top * math.tan(math.radians(overhang_deg)) if x == 0 else 0.0
        points += [(x, y, 0.0), (x - lean, y, top)]
    return m.Manifold.hull_points(points)


def prepare_wedge(tmp_path, rotate_z=7.0):
    source = tmp_path / 'wedge.stl'
    write_stl(source, manifold_triangles(knife_edge_wedge()).astype(np.float32),
              CancellationToken(), lambda *args: None)
    return pipeline.prepare(source, resolve_settings(), rotate=(0.0, 0.0, rotate_z), lift_mm=5.0,
                            output=tmp_path / 'out.stl')


def test_knife_edge_islands_get_stubs_and_the_plate_exports(tmp_path):
    report = prepare_wedge(tmp_path)
    supports_metrics = report['passes'][-1]['supports']
    assert report['export']['written'], report['export']
    assert report['passes'][0]['islands'] > 0          # the geometry class is present
    assert supports_metrics['contacts_failed'] == 0
    assert supports_metrics['model_anchor']['stubs'] > 0
    assert report['validation']['checks']['raster_connectivity'] == 'pass'
    assert report['validation']['checks']['support_routes'] == 'pass'
    # A stub is fused to the part on purpose; it must not read as a collision.
    assert report['validation']['checks']['support_collisions'] == 'pass'
    edges = report['support_graph']['edges']
    assert any(edge['kind'] == 'model_stub' for edge in edges)


def test_without_stubs_the_same_wedge_keeps_its_islands(tmp_path, monkeypatch):
    monkeypatch.setattr(supports, '_model_stub', lambda *args, **kwargs: (None, 'no_material_within_reach'))
    monkeypatch.setattr(supports, '_join_stub', lambda *args, **kwargs: (None, None))
    report = prepare_wedge(tmp_path)
    assert not report['export']['written']
    assert 'raster_connectivity' in report['export']['failed_checks']
    unroutable = [d for d in report['validation']['diagnostics'] if d['code'] == 'support_unroutable']
    assert unroutable
    for diagnostic in unroutable:
        details = diagnostic['details']
        assert details['reason'] in ('branch_exhausted', 'plate_blocked') or \
            details['reason'].startswith('anchor_rejected:')
        assert details['suggest'] and diagnostic['message'].startswith('No route: ')
        assert details['stub']['why'] == 'no_material_within_reach'
    # Each island is reported once, however many correction passes found it.
    keys = [tuple(round(v, 6) for v in d['position_mm']) for d in unroutable]
    assert len(keys) == len(set(keys))


def pedestal(gap_top=10.0):
    """A pedestal under a wide slab: no plate route from the slab's underside."""
    solid = (m.Manifold.cube((10, 10, gap_top), True).translate((0, 0, gap_top / 2))
             + m.Manifold.cube((20, 20, 2), True).translate((0, 0, gap_top + 2)))
    triangles = manifold_triangles(solid).astype(np.float32)
    return triangles, np.asarray(solid.bounding_box()).reshape(2, 3)


def route_close(exempt, **support):
    triangles, bounds = pedestal()
    settings = resolve_settings(overrides={'support': {'allow_part_to_part': True, **support}})
    field = build_column_field(triangles, bounds, settings, pitch_mm=.25)
    contact = [0.1, 0.1, 10.3]            # 0.3 mm over the pedestal top
    plan, _ = route_contacts([contact], field, settings, density_exempt=[contact] if exempt else ())
    return plan, settings


def test_material_too_close_for_a_tip_explains_itself():
    plan, _settings = route_close(exempt=False)
    assert plan.metrics['contacts_failed'] == 1
    assert plan.metrics['unroutable_reasons'] == {'anchor_rejected:gap_too_short': 1}
    diagnostic = next(d for d in plan.diagnostics if d.code == 'support_unroutable')
    details = diagnostic.details
    assert details['reason'] == 'anchor_rejected:gap_too_short'
    assert details['anchor']['gap_mm'] == pytest.approx(.3, abs=.05)
    assert details['obstruction_z_mm'] == pytest.approx(10.0, abs=.05)
    assert details['plate']['blocked_by'] == 'model_below'
    assert set(details['suggest']) == {'min_tip_length_mm', 'model_anchor_length_mm'}
    assert '0.30 mm below' in diagnostic.message and 'Z 10.00' in diagnostic.message


def test_an_island_contact_over_close_material_gets_a_stub():
    plan, settings = route_close(exempt=True)
    assert plan.metrics['contacts_failed'] == 0
    assert plan.metrics['routing']['model_anchor'] == 1
    assert plan.metrics['model_anchor']['stubs'] == 1
    edge = next(e for e in plan.graph.edges if e.kind == 'model_stub')
    assert edge.radius_mm == pytest.approx(settings['support']['contact_diameter_mm'] / 2)
    stub = plan.solids[-1]
    low, high = np.asarray(stub.bounding_box()).reshape(2, 3)
    # Sunk into the pedestal and through the contact, and one closed body.
    assert low[2] < 10.0 - .1 and high[2] > 10.3
    assert len(stub.decompose()) == 1


def test_a_stub_obeys_allow_part_to_part():
    plan, _settings = route_close(exempt=True, allow_part_to_part=False)
    assert plan.metrics['contacts_failed'] == 1
    assert plan.metrics['contacts_blocked_by_policy'] == 1
    details = next(d for d in plan.diagnostics if d.code == 'support_unroutable').details
    assert details['reason'] == 'policy_blocked'
    assert details['suggest'] == ['allow_part_to_part']


def test_duplicate_contacts_route_and_fail_once():
    triangles, bounds = pedestal()
    settings = resolve_settings(overrides={'support': {'allow_part_to_part': True}})
    field = build_column_field(triangles, bounds, settings, pitch_mm=.25)
    contact = [0.1, 0.1, 10.3]
    noisy = [0.1 + 1e-12, 0.1 - 1e-12, 10.3]
    plan, _ = route_contacts([contact, noisy], field, settings)
    assert plan.metrics['contacts_duplicate'] == 1
    assert plan.metrics['contacts_failed'] == 1
    assert sum(d.code == 'support_unroutable' for d in plan.diagnostics) == 1


def test_island_guard_does_not_add_a_known_island_twice(monkeypatch):
    from types import SimpleNamespace
    from voxelmill import island_guard
    requested = []

    def replan(extra):
        requested.append(list(extra))
        return SimpleNamespace(solids=[], metrics={}), None

    scans = iter([(8.451, 16.749, 53.425), (8.451 + 1e-14, 16.749 - 1e-14, 53.425)])

    def fake_scan(union, settings, **kwargs):
        return {'scan': 'full', 'islands': 1, 'positions': [next(scans)]}

    monkeypatch.setattr(island_guard, 'scan_assembly_islands', fake_scan)
    monkeypatch.setattr(island_guard, 'assemble',
                        lambda *args, **kwargs: SimpleNamespace(bounds=[[0, 0, 0], [1, 1, 60]]))
    # Two scans of one island differ only in float noise (a crop moves the grid).
    result = island_guard.route_without_islands(SimpleNamespace(), resolve_settings(),
                                                replan=replan, max_passes=3)
    assert len(result['contacts']) == 1
    assert result['passes'][1]['contacts_added'] == 0
    assert len(requested[-1]) == 1


def test_incomplete_routes_count_reasons_and_list_positions():
    plan, settings = route_close(exempt=False)
    report = ValidationReport()
    apply_support_validation(report, plan, settings)
    diagnostic = next(d for d in report.diagnostics if d.code == 'incomplete_support_routes')
    assert diagnostic.details['by_reason'] == {'anchor_rejected:gap_too_short': 1}
    assert diagnostic.details['positions_mm'] == [[0.1, 0.1, 10.3]]
    assert diagnostic.position_mm == pytest.approx([0.1, 0.1, 10.3])
    assert '1 anchor_rejected:gap_too_short' in diagnostic.message


def test_explanations_name_branches_that_start_inside_the_part():
    evidence = {'plate': {'blocked_by': 'model_below', 'material_from_z_mm': 35.0,
                          'material_top_z_mm': 52.85},
                'branch': {'tip_base_z_mm': 51.4, 'search_radius_mm': 6.0, 'free_columns': 600,
                           'within_angle': 600, 'tested': 256, 'blocked_near_elbow': 0,
                           'shaft_blocked': 256},
                'anchor': {'why': 'no_material_below'}}
    reason, message, details = explain_unroutable(evidence, resolve_settings()['support'])
    assert reason == 'branch_exhausted'
    assert 'tip base (Z 51.40' in message and 'inside that material' in message
    assert details['obstruction_z_mm'] == 52.85
    # allow_part_to_part is already on by default, so it is not suggested.
    assert 'allow_part_to_part' not in details['suggest']


def test_overlap_examples_carry_a_position_and_plain_names():
    graph = SupportGraph()
    graph.nodes.extend([SupportNode('a0', [0, 0, 0], 'foot'), SupportNode('a1', [0, 0, 5], 'junction'),
                        SupportNode('b0', [.3, 0, 1], 'junction'), SupportNode('b1', [.3, 0, 4], 'contact')])
    graph.edges.extend([SupportEdge('a0', 'a1', .45, 'vertical'), SupportEdge('b0', 'b1', .175, 'tip')])
    result = support_overlaps(graph)
    assert result['overlaps'] == 1
    example = result['examples'][0]
    assert example['kinds'] == ['vertical pillar', 'contact tip']
    assert example['position_mm'] == pytest.approx([.15, 0, 2.5])
    assert 'vertical pillar' in example['description'] and '0.625 mm' in example['description']
    assert edge_kind_name('model_stub') == 'stub on the model'


def test_joined_stubs_are_not_reported_as_overlaps():
    graph = SupportGraph()
    graph.nodes.extend([SupportNode('f', [0, 0, 0], 'model_anchor'), SupportNode('j1', [0, 0, .1], 'junction'),
                        SupportNode('j2', [0, 0, .2], 'junction'), SupportNode('c1', [0, 0, .3], 'contact'),
                        SupportNode('c2', [.1, 0, .35], 'contact'), SupportNode('c3', [.15, 0, .4], 'contact')])
    graph.edges.extend([SupportEdge('f', 'j1', .175, 'model_stub'), SupportEdge('j1', 'j2', .175, 'model_stub'),
                        SupportEdge('j2', 'c1', .175, 'model_stub'), SupportEdge('j1', 'c2', .175, 'model_stub'),
                        SupportEdge('j2', 'c3', .175, 'model_stub')])
    assert support_overlaps(graph)['overlaps'] == 0


def test_support_collision_faults_have_their_own_colours():
    assert fault_color('support_overlap') != DEFAULT_FAULT_COLOR
    assert fault_color('support_model_intrusion') != DEFAULT_FAULT_COLOR
    assert fault_color('support_overlap') != fault_color('support_model_intrusion')
