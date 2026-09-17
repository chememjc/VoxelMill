#!/usr/bin/env python3
"""Classify why ``route_contacts`` rejects a contact, contact by contact.

The three float-valve parts each fail roughly half their requested routes --
across both the exact and the raster assembly paths -- and the aggregate
``contacts_failed`` count says nothing about which of the router's three
strategies gave up or why. This reproduces the router's own decision sequence
for one placed part and records, per failed contact, the quantities that
decided it: the gap to the material below, the tip length that gap must
exceed, the base height an angled branch has to fit under, and the nearest
neighbouring column that is free to the plate.

It changes nothing and exports nothing. Run with
``.venv/bin/python scripts/routing_probe.py SOURCE --output REPORT.json``.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
import math
from pathlib import Path
import time

import numpy as np

from voxelmill.config import resolve_settings
from voxelmill.geometry import (iter_transformed_triangles, placement_for_triangles,
                               triangle_bounds)
from voxelmill.mesh import open_stl
from voxelmill.supports import (_free_to_plate, _segment_clear, build_column_field,
                               route_contacts, select_contacts)


def classify(contact, field, settings, branch_attempts=8):
    """Re-run the router's decisions for one contact, keeping the numbers."""
    support = settings['support']
    spacing = float(support['spacing_mm'])
    tip = float(support['tip_length_mm'])
    clearance = max(1, int(math.ceil(float(support['support_clearance_mm']) / field.dz)))
    x, y, z = (float(v) for v in contact)
    record = {'position_mm': [x, y, z], 'tip_length_mm': tip}
    column = field.index_of(x, y)
    if column is None:
        return {**record, 'outcome': 'outside_analysis'}
    index = field.layer_of(z)
    record['layer_index'] = int(index)
    if not field.reachable(column, index):
        return {**record, 'outcome': 'sealed_cavity'}
    base_z = z - tip
    record['base_z_mm'] = base_z
    if _free_to_plate(field, column, index, clearance):
        return {**record, 'outcome': 'vertical'}

    # Why not vertical: what is under this column, and how far below.
    below = field.top_below(column, index)
    record['material_below_layer'] = None if below is None else int(below)
    gap = None if below is None else z - field.z_of(below)
    record['gap_below_mm'] = gap

    # The branch search, with the numbers that decide it rather than a verdict.
    radius = max(1, int(math.ceil(min(2 * spacing, max(0.0, base_z)) / field.grid.dx)))
    row, col = divmod(column, field.grid.width)
    r0, r1 = max(0, row - radius), min(field.grid.height, row + radius + 1)
    c0, c1 = max(0, col - radius), min(field.grid.width, col + radius + 1)
    window = field.first_material[r0:r1, c0:c1] >= max(0, index - clearance)
    record['branch_search_radius_cells'] = int(radius)
    record['free_neighbours_in_window'] = int(np.count_nonzero(window))
    nearest = None
    blocked_segments = 0
    if window.any():
        rows, cols = np.nonzero(window)
        px = field.grid.x0 + (cols + c0 + .5) * field.grid.dx
        py = field.grid.y0 + (rows + r0 + .5) * field.grid.dy
        lateral = np.hypot(px - x, py - y)
        nearest = float(lateral.min())
        usable = (lateral > 1e-9) & (lateral <= 2 * spacing) & (lateral < base_z)
        record['neighbours_within_spacing'] = int(np.count_nonzero(
            (lateral > 1e-9) & (lateral <= 2 * spacing)))
        record['neighbours_under_45_deg'] = int(np.count_nonzero(usable))
        for pick in np.argsort(np.where(usable, lateral, np.inf))[:branch_attempts]:
            if not usable[pick]:
                break
            nx, ny, distance = float(px[pick]), float(py[pick]), float(lateral[pick])
            if _segment_clear(field, (nx, ny, base_z - distance), (x, y, base_z), clearance):
                return {**record, 'outcome': 'branched',
                        'branch_lateral_mm': distance}
            blocked_segments += 1
    record['nearest_free_neighbour_mm'] = nearest
    record['branch_segments_blocked'] = blocked_segments

    min_tip = float(support['min_tip_length_mm'])
    record['min_tip_length_mm'] = min_tip
    if below is not None and gap > tip:
        return {**record, 'outcome': 'model_anchor'}
    if below is not None and gap >= min_tip:
        return {**record, 'outcome': 'model_anchor_shortened_tip', 'tip_used_mm': gap}

    # Nothing worked. Name the binding constraint rather than the symptom.
    if below is None:
        reason = 'no material below and no vertical route: first_material disagrees with the contact'
    elif base_z <= 0:
        reason = 'contact sits below one tip length, so no branch and no anchor can fit'
    elif gap < min_tip:
        reason = 'material below is closer than the shortest permitted tip'
    elif record['neighbours_under_45_deg'] == 0:
        reason = 'no free neighbouring column within 2x spacing and under 45 degrees'
    else:
        reason = 'every candidate branch segment was blocked by model material'
    return {**record, 'outcome': 'failed', 'binding_constraint': reason}


def what_if(contact, field, settings, *, branch_attempts, min_tip_mm, nudge_cells):
    """Would a wider branch search, a shorter tip or a nudged column route this?

    Each variation is measured independently against the same failed contact,
    so the counts say what each change would buy on its own rather than what
    all three together would buy. None of them is applied anywhere.
    """
    support = settings['support']
    spacing = float(support['spacing_mm'])
    tip = float(support['tip_length_mm'])
    clearance = max(1, int(math.ceil(float(support['support_clearance_mm']) / field.dz)))
    x, y, z = (float(v) for v in contact)
    column = field.index_of(x, y)
    if column is None:
        return set()
    index = field.layer_of(z)
    base_z = z - tip
    gained = set()

    # 1. A wider branch search: the router tries only the eight nearest free
    #    columns, which all sit in the immediate neighbourhood of the blocked
    #    one and are therefore blocked by the same feature.
    radius = max(1, int(math.ceil(min(2 * spacing, max(0.0, base_z)) / field.grid.dx)))
    row, col = divmod(column, field.grid.width)
    r0, r1 = max(0, row - radius), min(field.grid.height, row + radius + 1)
    c0, c1 = max(0, col - radius), min(field.grid.width, col + radius + 1)
    window = field.first_material[r0:r1, c0:c1] >= max(0, index - clearance)
    if window.any():
        rows, cols = np.nonzero(window)
        px = field.grid.x0 + (cols + c0 + .5) * field.grid.dx
        py = field.grid.y0 + (rows + r0 + .5) * field.grid.dy
        lateral = np.hypot(px - x, py - y)
        usable = (lateral > 1e-9) & (lateral <= 2 * spacing) & (lateral < base_z)
        for pick in np.argsort(np.where(usable, lateral, np.inf))[:branch_attempts]:
            if not usable[pick]:
                break
            nx, ny, distance = float(px[pick]), float(py[pick]), float(lateral[pick])
            if _segment_clear(field, (nx, ny, base_z - distance), (x, y, base_z), clearance):
                gained.add('wider_branch_search')
                break

    # 2. A tip shortened to the gap that is actually available.
    below = field.top_below(column, index)
    if below is not None:
        gap = z - field.z_of(below)
        if gap < min_tip_mm:
            gained.add('would_need_a_tip_shorter_than_the_minimum')

    # 3. The sample attributed to the nearest free column instead of the one it
    #    lands in, for a contact sitting on a wall the coarse grid reads solid.
    if below is None and not _free_to_plate(field, column, index, clearance):
        n0, n1 = max(0, row - nudge_cells), min(field.grid.height, row + nudge_cells + 1)
        m0, m1 = max(0, col - nudge_cells), min(field.grid.width, col + nudge_cells + 1)
        patch = field.first_material[n0:n1, m0:m1] >= max(0, index - clearance)
        if patch.any():
            gained.add('nudge_to_free_column')
    return gained


def exact_attachment(triangles, contacts, settings, cancel_check=lambda: None):
    """Is each contact's own pixel already occupied one printer layer below?

    The router works on a 0.15 mm analysis grid, so "the column is solid here"
    can be a cell that merely touches a wall the contact sits just outside of.
    This asks the same question on the printer's own lattice, at the exact
    pixel under the sample, which is the lattice the layer analysis certifies
    connectivity on. An occupied pixel one layer below means the overhang rests
    on material that is printed before it -- the same criterion
    ``raster_connectivity`` uses -- and a support there would be holding up
    something already held.

    Measurement only. Nothing here drops a sample or changes a plan.
    """
    from voxelmill import _native
    from voxelmill.raster import RasterGrid

    height = float(settings['process']['layer_height_mm'])
    bounds = triangle_bounds(triangles)
    grid = RasterGrid.for_bounds(bounds, settings, crop=True)
    order = sorted(range(len(contacts)), key=lambda i: contacts[i][2])
    raster = _native.Rasterizer(np.asarray(triangles), cancel_check)
    results = {}
    cache_index, cache_mask = None, None
    for position in order:
        x, y, z = (float(v) for v in contacts[position])
        index = max(0, int(math.floor(z / height)))
        below = index - 1
        if below < 0:
            results[position] = {'layer_below': None, 'occupied_below': False,
                                 'reason': 'contact is on the first printed layer'}
            continue
        if cache_index != below:
            cache_mask = raster.slice((below + 0.5) * height, grid.width, grid.height,
                                      grid.x0, grid.y0, grid.dx, grid.dy,
                                      cancel_check, 'nonzero')['mask']
            cache_index = below
        col = int(math.floor((x - grid.x0) / grid.dx))
        row = int(math.floor((y - grid.y0) / grid.dy))
        if not (0 <= row < grid.height and 0 <= col < grid.width):
            results[position] = {'layer_below': below, 'occupied_below': False,
                                 'reason': 'contact is outside the printer crop'}
            continue
        # A 3x3 neighbourhood, because the sample sits on the surface and its
        # exact pixel can fall on either side of the boundary.
        r0, r1 = max(0, row - 1), min(grid.height, row + 2)
        c0, c1 = max(0, col - 1), min(grid.width, col + 2)
        results[position] = {
            'layer_below': below,
            'occupied_below': bool(cache_mask[row, col]),
            'occupied_below_3x3': bool(cache_mask[r0:r1, c0:c1].any()),
        }
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--lift-mm', type=float, default=5.0)
    parser.add_argument('--examples', type=int, default=12,
                        help='failed contacts recorded in full per binding constraint')
    parser.add_argument('--whatif-branch-attempts', type=int, default=64)
    parser.add_argument('--whatif-min-tip-mm', type=float, default=0.3)
    parser.add_argument('--whatif-nudge-cells', type=int, default=2)
    parser.add_argument('--exact-attachment', action='store_true',
                        help='also ask, at printer pitch, whether each failed contact already '
                             'rests on material printed one layer below it')
    args = parser.parse_args()
    settings = resolve_settings()
    started = time.monotonic()
    with open_stl(args.source) as mesh:
        source_hash = mesh.asset.sha256
        placement = placement_for_triangles(mesh.triangles, settings, lift_mm=args.lift_mm)
        triangles = np.concatenate(list(iter_transformed_triangles(
            mesh.triangles, np.asarray(placement.matrix)))).astype(np.float32)
    bounds = triangle_bounds(triangles)
    field = build_column_field(triangles, bounds, settings)
    contacts, selection = select_contacts(triangles, field, settings)
    plan, _raft = route_contacts(contacts, field, settings)

    records = [classify(contact, field, settings) for contact in contacts]
    outcomes = Counter(record['outcome'] for record in records)
    constraints = Counter(record.get('binding_constraint') for record in records
                          if record['outcome'] == 'failed')
    examples = {}
    for record in records:
        if record['outcome'] != 'failed':
            continue
        bucket = examples.setdefault(record['binding_constraint'], [])
        if len(bucket) < args.examples:
            bucket.append(record)
    failed = [r for r in records if r['outcome'] == 'failed']
    gains = Counter()
    combined = 0
    for record, contact in zip(records, contacts):
        if record['outcome'] != 'failed':
            continue
        gained = what_if(contact, field, settings,
                         branch_attempts=args.whatif_branch_attempts,
                         min_tip_mm=args.whatif_min_tip_mm,
                         nudge_cells=args.whatif_nudge_cells)
        gains.update(gained)
        combined += bool(gained)
    gaps = [r['gap_below_mm'] for r in failed if r.get('gap_below_mm') is not None]
    attachment = None
    if args.exact_attachment:
        failed_positions = [i for i, r in enumerate(records) if r['outcome'] == 'failed']
        exact = exact_attachment(triangles, [contacts[i] for i in failed_positions], settings)
        occupied = sum(1 for v in exact.values() if v.get('occupied_below'))
        occupied3 = sum(1 for v in exact.values() if v.get('occupied_below_3x3'))
        attachment = {
            'basis': 'printer-pitch raster of the placed model, one layer below each contact',
            'failed_contacts_examined': len(failed_positions),
            'own_pixel_occupied_below': occupied,
            'any_of_3x3_occupied_below': occupied3,
            'means': 'an occupied pixel one layer below is the same attachment '
                     'raster_connectivity certifies; such an overhang rests on material '
                     'printed before it',
            'does_not_establish': 'that the attached material is strong enough, or that '
                                  'the sample should be dropped from the coverage basis',
        }

    payload = {
        'schema_version': 1, 'probe': 'routing', 'source': str(args.source),
        'source_sha256': source_hash, 'lift_mm': args.lift_mm,
        'placement': asdict(placement), 'settings': settings,
        'field': field.metrics, 'selection': selection,
        'router_metrics': plan.metrics,
        'reclassified': dict(outcomes),
        'agrees_with_router': {
            'router_routed': plan.metrics['contacts_routed'],
            'probe_routed': (outcomes['vertical'] + outcomes['branched'] + outcomes['model_anchor']
                             + outcomes['model_anchor_shortened_tip']),
            'router_failed': plan.metrics['contacts_failed'],
            'probe_failed': outcomes['failed'] + outcomes['outside_analysis'],
        },
        'binding_constraints': dict(constraints),
        'failed_gap_below_mm': {
            'count': len(gaps),
            'min': min(gaps, default=None), 'max': max(gaps, default=None),
            'median': float(np.median(gaps)) if gaps else None,
            'under_tip_length': int(sum(g <= settings['support']['tip_length_mm'] for g in gaps)),
        },
        'what_if': {
            'basis': 'each variation measured alone against the same failed contacts',
            'branch_attempts': args.whatif_branch_attempts,
            'min_tip_mm': args.whatif_min_tip_mm,
            'nudge_cells': args.whatif_nudge_cells,
            'failed_contacts': len(failed),
            'recovered_by': dict(gains),
            'recovered_by_any': combined,
            'still_failing': len(failed) - combined,
            'caveat': 'a recovered route is a geometric route, not a proof that the '
                      'support holds or can be removed',
        },
        'exact_attachment': attachment,
        'examples': examples,
        'seconds': time.monotonic() - started,
        'establishes': 'which router constraint rejected each contact, on this pose and these settings',
        'does_not_establish': 'that a rejected contact is or is not physically supportable',
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, default=str) + '\n')
    print(json.dumps({'outcomes': dict(outcomes), 'constraints': dict(constraints),
                      'agrees': payload['agrees_with_router'],
                      'what_if': payload['what_if'],
                      'exact_attachment': payload['exact_attachment']}, indent=2))


if __name__ == '__main__':
    main()
