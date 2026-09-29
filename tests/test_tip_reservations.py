"""Routed tips are reserved in the routing occupancy (VM-097).

A later shaft, anchor or trunk must not pass through an earlier contact's tip,
and a new tip must not pass through a support routed before it. The rule is
the collision audit's: the tip's graph edge (junction to contact, contact
radius) against the other capsule's radius, no clearance.

Scene: a slab with its underside at Z 9. Contact A routes a vertical pillar
first; contact B, an island-like (mandatory) contact beside it, used to get a
vertical right next to A's pillar and tip, which the audit reported.
"""
import manifold3d as m
import numpy as np
import pytest

from voxelmill.collisions import support_overlaps
from voxelmill.config import resolve_settings
from voxelmill.contracts import SupportEdge
from voxelmill.geometry import manifold_triangles
from voxelmill.supports import (CapsuleIndex, _hits_tips, build_column_field, explain_unroutable,
                                route_contacts)


def route(contacts, exempt=()):
    solid = m.Manifold.cube((10, 10, 1)).translate((-5, -5, 9))
    triangles = manifold_triangles(solid).astype(np.float32)
    bounds = np.asarray(solid.bounding_box()).reshape(2, 3)
    bounds[0][2] = 0.0
    settings = resolve_settings(overrides={'support': {
        'auto_bracing': False, 'base_type': 'none', 'tree_supports': False}})
    field = build_column_field(triangles, bounds, settings, pitch_mm=.05)
    plan, _ = route_contacts(contacts, field, settings, density_exempt=exempt)
    return plan


A = [0.0, 0.0, 9.0]


@pytest.mark.parametrize('offset', [.4, .6])
def test_a_mandatory_contact_beside_a_pillar_is_routed_without_overlap(offset):
    b = [offset, 0.0, 9.0]
    plan = route([A, b], exempt=[b])
    assert plan.metrics['contacts_routed'] == 2
    assert plan.metrics['contacts_failed'] == 0
    assert plan.metrics['contacts_skipped_density'] == 0
    assert support_overlaps(plan.graph)['overlaps'] == 0


def test_a_contact_can_hang_from_the_pillar_beside_it():
    b = [.6, 0.0, 9.0]
    plan = route([A, b], exempt=[b])
    assert plan.metrics['tips_joined_to_supports'] == 1
    nodes = {node.id: node for node in plan.graph.nodes}
    joined = next(edge for edge in plan.graph.edges
                  if edge.kind == 'tip' and edge.start.startswith('tip_joint'))
    junction = nodes[joined.start].position_mm
    # On A's pillar axis, below B, and the pillar edge is split there.
    assert junction[:2] == pytest.approx([0.0, 0.0])
    assert junction[2] < b[2]
    pillar = [edge for edge in plan.graph.edges if edge.kind == 'vertical']
    assert sorted(edge.end == joined.start or edge.start == joined.start for edge in pillar) == [True, True]


def test_without_the_reservation_the_audit_flags_the_same_plate(monkeypatch):
    from voxelmill import supports
    monkeypatch.setattr(supports, '_hits_tips', lambda *args, **kwargs: False)
    monkeypatch.setattr(supports, '_tip_blocked', lambda *args, **kwargs: False)
    b = [.6, 0.0, 9.0]
    plan = route([A, b], exempt=[b])
    assert plan.metrics['routing']['vertical'] == 2
    kinds = {tuple(sorted(example['kinds']))
             for example in support_overlaps(plan.graph)['examples']}
    assert ('contact tip', 'vertical pillar') in kinds


def test_tip_capsules_follow_the_audit_rule():
    from types import SimpleNamespace
    field = SimpleNamespace(occupied_capsules=CapsuleIndex(), tip_capsules=CapsuleIndex())
    field.tip_capsules.add((0, 0, 7), (0, 0, 9), .175)
    # A pillar (0.45) is blocked closer than 0.625 mm, clear beyond it.
    assert _hits_tips(field, (.6, 0, 0), (.6, 0, 8), .45)
    assert not _hits_tips(field, (.65, 0, 0), (.65, 0, 8), .45)
    # A joint at the tip's own base (the shaft it stands on) is not a hit.
    assert not _hits_tips(field, (0, 0, 0), (0, 0, 7), .45)


def test_a_blocked_tip_is_explained():
    evidence = {'tip': {'blocked': True, 'tip_base_z_mm': 7.0, 'route': 'vertical'},
                'plate': {}, 'branch': {}, 'anchor': {}}
    reason, message, details = explain_unroutable(evidence, resolve_settings()['support'])
    assert reason == 'tip_blocked'
    assert 'own tip, from Z 7.00' in message
    assert details['suggest'] == ['spacing_mm', 'tip_base_diameter_mm', 'tip_length_mm']


def test_an_anchor_through_a_tip_is_explained():
    evidence = {'plate': {'blocked_by': 'model_below', 'material_from_z_mm': 1.0,
                          'material_top_z_mm': 4.0},
                'branch': {'tip_base_z_mm': 3.0, 'search_radius_mm': 3.0},
                'anchor': {'why': 'existing_tip', 'gap_mm': 3.0, 'surface_z_mm': 4.0}}
    reason, message, _details = explain_unroutable(evidence, resolve_settings()['support'])
    assert reason == 'anchor_rejected:existing_tip'
    assert "another contact's tip" in message


def test_graph_edges_are_split_not_duplicated():
    b = [.6, 0.0, 9.0]
    plan = route([A, b], exempt=[b])
    ids = [(edge.start, edge.end) for edge in plan.graph.edges]
    assert len(ids) == len(set(ids))
    assert all(isinstance(edge, SupportEdge) for edge in plan.graph.edges)
