"""Route, reslice and add attachments until no islands are left.

The CLI's ``prepare`` has always iterated: route, assemble, reslice, feed the
islands the reslice found back in as extra contacts, repeat. The editor routed
once, so the same geometry could keep islands in the editor that it would never
keep through the CLI. This is that loop on its own, so both callers can run it.

Rasterizing and labeling the whole panel five times is far too slow to do while
someone waits, so the middle passes rasterize only the neighborhood of the
pillars that were just added; nothing else changed. That crop is a convenience
and never a verdict: the first and last passes always scan the whole build, and
``islands_remaining`` and ``resolved`` come from that last full scan alone.
"""
from __future__ import annotations

import math
import time

import numpy as np

from .assembly import UnionLayerStream, assemble
from .contracts import CancellationToken, ResourceBudget, no_progress
from .raster import RasterGrid
from .validation import analyze_layers

# Floor on the XY padding around new pillars. A crop tight enough to shave a
# pillar's own footprint would spend the whole pass discarding edge components.
MIN_PAD_MM = 3.0
# Same cap ``pipeline.prepare`` puts on the contacts one pass may feed back.
MAX_FEEDBACK = 4000


def _crop_pad_mm(settings):
    support = settings['support']
    return max(float(support['spacing_mm']) * 4 + float(support['pillar_diameter_mm']), MIN_PAD_MM)


def local_window(contacts, settings, layer_count):
    """XY crop bounds and an inclusive absolute layer range around new contacts.

    The range starts at the plate because a pillar is only as supported as the
    material under it, and ends two layers above the highest new contact so the
    layer that lands on a tip and the one after it are both judged.
    """
    points = np.asarray(contacts, dtype=float).reshape(-1, 3)
    if not len(points):
        return None, None
    pad = _crop_pad_mm(settings)
    low, high = points.min(axis=0), points.max(axis=0)
    bounds = np.array([[low[0] - pad, low[1] - pad, 0.0],
                       [high[0] + pad, high[1] + pad, float(high[2])]], dtype=float)
    layer_height = float(settings['process']['layer_height_mm'])
    last = min(layer_count - 1, int(math.floor(float(high[2]) / layer_height)) + 2)
    return bounds, (0, last)


def scan_assembly_islands(union, settings, *, crop_bounds=None, layer_range=None,
                          budget=None, cancel=None, progress=no_progress):
    """Label one assembly's islands, optionally over a cropped sub-volume.

    With no crop this is the same connectivity pass ``validate`` runs, minus
    void tracking. With a crop it answers the same question about part of the
    build and says so: a component that leaves the crop is cut off at the crop
    edge and would read as unsupported, so those components are dropped as
    unknown rather than counted. That is what makes a cropped scan usable at
    all, and why it can never be the last word.

    This also skips the growth/span check (``check_growth=False``): only
    ``island_components``, ``island_components_on_crop_edge`` and
    ``raster_island`` diagnostics are read out of the report below, and
    growth is a separate distance-transform pass over every layer pair that
    this function never looks at, on every one of the 1..max_passes+1 calls
    a `route_without_islands` search makes.
    """
    cancel = cancel or CancellationToken()
    budget = budget or ResourceBudget(**settings['resources'])
    local = crop_bounds is not None or layer_range is not None
    bounds = np.asarray(union.bounds, dtype=float).reshape(2, 3)
    grid_bounds = bounds
    if crop_bounds is not None:
        crop = np.asarray(crop_bounds, dtype=float).reshape(2, 3)
        # Padding that runs past the assembly's own extent only buys empty
        # pixels, so a crop is never allowed to be larger than a full scan.
        grid_bounds = np.stack((np.maximum(crop[0], bounds[0]), np.minimum(crop[1], bounds[1])))
    grid = RasterGrid.for_bounds(grid_bounds, settings)
    # The stream still measures the build from the real assembly bounds, so a
    # cropped scan's layer indices stay the build's own layer indices.
    started = time.monotonic()
    stream = UnionLayerStream(union.groups, bounds, settings, grid=grid,
                              layer_range=layer_range, budget=budget, cancel=cancel,
                              progress=progress)
    raster_seconds = time.monotonic() - started
    analyze_started = time.monotonic()
    report = analyze_layers(stream, grid, settings, cancel=cancel, budget=budget,
                            progress=progress, track_voids=False, check_growth=False)
    analyze_seconds = time.monotonic() - analyze_started
    diagnostics = [d for d in report.diagnostics if d.code == 'raster_island' and d.position_mm]
    if local:
        diagnostics = [d for d in diagnostics if not d.details.get('touches_crop_edge')]
    count = int(report.metrics.get('island_components') or 0)
    on_edge = int(report.metrics.get('island_components_on_crop_edge') or 0)
    if local:
        count = max(0, count - on_edge)
    positions = [tuple(float(v) for v in d.position_mm) for d in diagnostics[:MAX_FEEDBACK]]
    seconds = time.monotonic() - started
    return {
        'scan': 'local' if local else 'full',
        'islands': count,
        'positions': positions,
        'positions_truncated': count > len(positions),
        'discarded_on_crop_edge': on_edge if local else 0,
        'layers_scanned': int(stream.scan_count),
        'layer_range': [int(stream.first_index), int(stream.last_index)],
        'grid': [int(grid.width), int(grid.height)],
        'crop_bounds_mm': None if crop_bounds is None else grid_bounds.tolist(),
        'islands_by_layer': sorted({int(d.layer) for d in diagnostics if d.layer is not None}),
        'seconds': seconds,
        'timing': {'raster': raster_seconds, 'analyze': analyze_seconds},
    }


def route_without_islands(model, settings, *, replan, budget=None, cancel=None,
                          progress=no_progress, max_passes=None, extra_contacts=()):
    """Plan supports, then add attachments under every island until none remain.

    ``replan(extra_contacts)`` returns ``(plan, raft)``; it is a callable so the
    caller keeps its own routing arguments (paint, per-object parameters, the
    column field it already built) instead of this function inventing them.
    ``max_passes`` defaults to ``support.max_island_passes``.

    The returned ``resolved`` is only ever true on the evidence of a full scan.
    When passes run out with islands left, that is reported as it is, with the
    count and the positions, rather than presented as a routing that worked.
    """
    budget = budget or ResourceBudget(**settings['resources'])
    cancel = cancel or CancellationToken()
    if max_passes is None:
        max_passes = settings['support'].get('max_island_passes', 5)
    max_passes = max(1, int(max_passes))
    layer_height = float(settings['process']['layer_height_mm'])
    extra = [tuple(float(v) for v in point) for point in extra_contacts]
    passes, previous, added = [], {}, []
    plan = raft = union = record = None
    for attempt in range(1, max_passes + 1):
        cancel.check()
        plan, raft = replan(extra)
        # Scans read only the raster groups; the exact union is built once,
        # below, for the plan that is actually returned.
        union = assemble(model, plan.solids, raft, budget=budget, cancel=cancel, exact=False)
        bounds = np.asarray(union.bounds, dtype=float).reshape(2, 3)
        layer_count = max(0, math.ceil(bounds[1][2] / layer_height))
        window = (None, None)
        # The first pass has nothing to compare against and the last pass is the
        # verdict, so both scan everything. A middle pass crops only when the
        # pass before it actually added pillars; with nothing new to look at,
        # a local scan would be looking in the wrong place.
        if 1 < attempt < max_passes and added:
            window = local_window(added, settings, layer_count)
        record = scan_assembly_islands(union, settings, crop_bounds=window[0],
                                       layer_range=window[1], budget=budget, cancel=cancel,
                                       progress=progress)
        record['pass'] = attempt
        record['contacts_added'] = 0
        passes.append(record)
        if not record['islands']:
            record['stopped'] = 'no_islands_found'
            break
        # Local counts cover part of the build and full counts cover all of it,
        # so the no-progress guard only compares a pass against the last pass
        # of the same kind. Comparing across kinds would read a smaller window
        # as progress.
        prior = previous.get(record['scan'])
        if prior is not None and record['islands'] >= prior:
            record['stopped'] = 'no_progress'
            break
        previous[record['scan']] = record['islands']
        added = [point for point in record['positions'] if point not in extra]
        record['contacts_added'] = len(added)
        extra.extend(added)
    else:
        if record is not None:
            record['stopped'] = 'max_passes'
    # A cropped pass never gets the last word, even a clean one: it did not look
    # at the rest of the build.
    if record is not None and record['scan'] == 'local':
        confirm = scan_assembly_islands(union, settings, budget=budget, cancel=cancel,
                                        progress=progress)
        confirm['pass'] = len(passes) + 1
        confirm['contacts_added'] = 0
        confirm['confirms_pass'] = record['pass']
        confirm['stopped'] = 'confirmation'
        passes.append(confirm)
        record = confirm
    if plan is not None:
        union = assemble(model, plan.solids, raft, budget=budget, cancel=cancel)
    remaining = int(record['islands']) if record is not None else 0
    return {
        'plan': plan,
        'raft': raft,
        'union': union,
        'contacts': list(extra),
        'passes': passes,
        'islands_remaining': remaining,
        'island_positions': list(record['positions']) if record is not None else [],
        'max_passes': max_passes,
        'resolved': bool(record is not None and record['scan'] == 'full' and not remaining),
    }
