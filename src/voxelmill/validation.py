"""Independent layer connectivity, empty-space and drainage analysis.

Three empty-space questions are kept separate on purpose, because they fail for
different reasons and are fixed differently:

``enclosed_voids``
    Empty components that never reach the exterior over the whole build. Trapped
    resin, always an error unless the caller explicitly seals or accepts them.
``transient_traps``
    Components that are, at some layer, enclosed by already-printed material
    except toward the not-yet-printed direction, and only reach the exterior
    later. A future opening is not present drainage, so these are reported with
    the volume and the layer span over which the trap is live.
``drainage_bottlenecks``
    Exterior-connected voids whose effective connection to the exterior is
    narrower than ``repair.min_orifice_area_mm2``. Measured by clearance
    erosion on its own analysis grid, so it has its own reported resolution.

Connectivity and island findings are exact for the documented binary masks.
Growth/span uses conservative coarse upper bounds with exact pixel-distance
fallback; the configured distance rule is not a strength proof.
"""
from __future__ import annotations

import math
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from scipy import ndimage as ndi

from .contracts import CancellationToken, VoxelMillError, Diagnostic, ResourceBudget, ValidationReport, no_progress

CROSS = ndi.generate_binary_structure(2, 1)
CROSS3 = ndi.generate_binary_structure(3, 1)

def occupancy_mask(mask):
    """Binary material mask for connectivity / growth / voids.

    Any nonzero sample is material. Coverage grayscale (1–255) and CTB 7-bit
    grays must not punch holes in a connected blob; AA fringe expands slightly
    rather than birthing islands.
    """
    return np.asarray(mask) != 0


def block_any(mask, factor):
    """Decimate by block maximum, never by striding.

    Striding drops features narrower than the factor. A 0.4 mm support tip is
    about five pixels wide at the printer pitch, so a stride of eight erases it
    and every pixel above it then measures as unsupported. Block maximum keeps
    the tip and slightly grows the reference material instead, which makes the
    growth check marginally lenient rather than wrong by whole millimeters.
    """
    if factor <= 1:
        return mask
    height, width = mask.shape
    pad_y = (-height) % factor
    pad_x = (-width) % factor
    if pad_y or pad_x:
        mask = np.pad(mask, ((0, pad_y), (0, pad_x)))
    return mask.reshape(mask.shape[0] // factor, factor,
                        mask.shape[1] // factor, factor).any(axis=(1, 3))


class VoidForest:
    """In-memory union-find over per-layer empty components.

    One node per component per layer, so the node count is bounded by the total
    component count across the build, not by the pixel count. Roots carry the
    accumulated volume, whether the component has ever reached the exterior, and
    the layer at which it was first seen.
    """

    def __init__(self, voxel_volume, budget=None):
        self.voxel_volume = float(voxel_volume)
        self.parent = np.zeros(1024, dtype=np.int64)
        self.volume = np.zeros(1024, dtype=np.float64)
        self.exterior = np.zeros(1024, dtype=bool)
        self.born = np.zeros(1024, dtype=np.int64)
        self.count = 0
        self.trapped = 0.0
        self.previous = None
        self.previous_ids = np.zeros(1, dtype=np.int64)
        self.history = []
        self.budget = budget

    def _grow(self, needed):
        if needed <= len(self.parent):
            return
        size = max(needed, len(self.parent) * 2)
        if self.budget is not None:
            self.budget.require(size * 32, 'empty-space component forest')
        for name in ('parent', 'volume', 'exterior', 'born'):
            array = getattr(self, name)
            grown = np.zeros(size, dtype=array.dtype)
            grown[:len(array)] = array
            setattr(self, name, grown)

    def find(self, node):
        parent = self.parent
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return int(node)

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a == b:
            return
        if b < a:
            a, b = b, a
        before = (0.0 if self.exterior[a] else self.volume[a]) + (0.0 if self.exterior[b] else self.volume[b])
        self.parent[b] = a
        self.volume[a] += self.volume[b]
        self.exterior[a] = self.exterior[a] or self.exterior[b]
        self.born[a] = min(self.born[a], self.born[b])
        self.trapped += (0.0 if self.exterior[a] else self.volume[a]) - before
        return

    def add(self, mask, index, cancel):
        """Label this layer's empty space and merge it, in one step.

        `analyze_layers` splits these two halves apart so the labeling can run
        on a worker thread; this entry point stays for every other caller.
        """
        return self.merge(void_components(occupancy_mask(mask)), index, cancel)

    def merge(self, components, index, cancel):
        """Union this layer's empty-space components into the forest.

        The order-dependent half of `add`: void identity is temporal, so this
        has to see layers in sequence even when they were labeled out of order.
        """
        labels, total, counts, outside = components
        if index == 0:
            outside = np.ones(total + 1, dtype=bool)  # open plate-side air
        self._grow(self.count + total + 1)
        ids = np.zeros(total + 1, dtype=np.int64)
        if total:
            ids[1:] = np.arange(self.count, self.count + total, dtype=np.int64)
            slice_ = slice(self.count, self.count + total)
            self.parent[slice_] = np.arange(self.count, self.count + total)
            self.volume[slice_] = counts[1:] * self.voxel_volume
            self.exterior[slice_] = outside[1:]
            self.born[slice_] = index
            self.trapped += float(self.volume[slice_][~outside[1:]].sum())
            self.count += total
        if self.previous is not None and total:
            cancel.check()
            # Bound overlap-key scratch instead of allocating several int64
            # full-panel arrays (hundreds of MiB each on a 9K printer).
            for row in range(0, labels.shape[0], 64):
                cancel.check()
                before = self.previous[row:row + 64].ravel()
                after = labels[row:row + 64].ravel()
                both = (before > 0) & (after > 0)
                if both.any():
                    keys = before[both].astype(np.int64) * (total + 1) + after[both]
                    for key in np.unique(keys):
                        self.union(int(self.previous_ids[key // (total + 1)]), int(ids[key % (total + 1)]))
        self.previous, self.previous_ids = labels, ids
        self.history.append(self.trapped)
        return labels, ids

    def finish(self, min_volume_mm3=0.0):
        """Air beyond the last layer connects every still-open region outside.

        Components at or below ``min_volume_mm3`` are counted and reported
        separately rather than dropped: a single unexposed pixel between two
        cured surfaces traps a volume that no drain could ever clear, and
        calling that an export blocker hides the cavities that matter.
        """
        for node in np.unique(self.previous_ids[1:]) if self.previous is not None else ():
            root = self.find(int(node))
            if not self.exterior[root]:
                self.trapped -= float(self.volume[root])
                self.exterior[root] = True
        live = np.arange(self.count)
        roots = np.array([self.find(int(n)) for n in live], dtype=np.int64) if self.count else np.empty(0, np.int64)
        is_root = roots == live if self.count else np.empty(0, bool)
        sealed = is_root & ~self.exterior[:self.count] if self.count else np.empty(0, bool)
        indices = np.flatnonzero(sealed)
        volumes = self.volume[indices] if len(indices) else np.empty(0)
        significant = indices[volumes > min_volume_mm3]
        negligible = indices[volumes <= min_volume_mm3]
        order = significant[np.argsort(-self.volume[significant])] if len(significant) else significant
        peak = float(max(self.history, default=0.0))
        return {
            'count': int(len(significant)),
            'volume_mm3': float(self.volume[significant].sum()) if len(significant) else 0.0,
            'examples': [{'component': int(i), 'volume_mm3': float(self.volume[i]),
                          'first_layer': int(self.born[i])} for i in order[:64]],
            'negligible_count': int(len(negligible)),
            'negligible_volume_mm3': float(self.volume[negligible].sum()) if len(negligible) else 0.0,
            'min_void_volume_mm3': float(min_volume_mm3),
            'nodes': int(self.count),
            'peak_present_trapped_volume_mm3': peak,
            'peak_present_trapped_layer': int(np.argmax(self.history)) if self.history else 0,
        }


def _label_occupancy(occupied):
    """Connected components on one binary layer; safe to run off the main thread."""
    labels, count = ndi.label(occupied, CROSS)
    counts = np.bincount(labels.ravel(), minlength=count + 1)
    return labels, count, counts


def _border_flags(labels, size):
    """Which component ids touch the grid border.

    Scattering `True` through the concatenated border rows and columns lands on
    duplicate indices, which is exactly what `np.unique` was being used to
    remove first. The flags are identical and the sort is gone; that sort was
    the single largest NumPy cost in the run.
    """
    flags = np.zeros(size, dtype=bool)
    flags[np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1]))] = True
    return flags


def void_components(occupied):
    """Label one layer's empty space; the expensive, order-free half of VoidForest.add.

    Pure in its input, so it can run on a worker thread while the union-find
    merge that consumes it stays in layer order.
    """
    labels, total = ndi.label(~occupied, CROSS)
    counts = np.bincount(labels.ravel(), minlength=total + 1)
    return labels, total, counts, _border_flags(labels, total + 1)


def _layer_worker_cap(budget, grid):
    """Cap concurrency so workers do not hold more full-panel copies than fit."""
    workers = max(1, min(int(budget.workers), 32))
    panel = max(1, int(grid.width) * int(grid.height))
    # Account for the consumer's previous mask, void labels, EDT scratch,
    # component counts and source masks before reserving prefetch slots.
    # Counts can themselves be large for a checkerboard of tiny islands.
    base = panel * 48
    budget.require(base + panel * 24, 'layer analysis buffers')
    # Each in-flight layer now carries more than its island labels: the void
    # labels, both count vectors, its own occupancy and a reference to its
    # predecessor's, plus the growth transform's decimated scratch.
    per_worker = panel * 24
    ceiling = int(budget.memory_gib * 1024 ** 3 * 0.8)
    return max(1, min(workers, (ceiling - base) // per_worker))


def _tiled_growth_pixels(previous, mask, grid, limit, tile_size=256):
    """Exact distance-threshold count with bounded tile-local EDT scratch.

    A predecessor farther than the threshold cannot change the verdict, so
    each tile needs only a threshold-wide halo, including equality pixels.
    Empty halos are handled explicitly: scipy's EDT otherwise measures to
    an implicit point outside an all-foreground array.
    """
    threshold = limit + 1e-10
    hy = min(previous.shape[0], int(math.ceil(threshold / grid.dy)))
    hx = min(previous.shape[1], int(math.ceil(threshold / grid.dx)))
    growth = 0
    for y in range(0, mask.shape[0], tile_size):
        y1 = min(y + tile_size, mask.shape[0])
        for x in range(0, mask.shape[1], tile_size):
            x1 = min(x + tile_size, mask.shape[1])
            new = mask[y:y1, x:x1] & ~previous[y:y1, x:x1]
            if not new.any():
                continue
            ya, yb = max(0, y - hy), min(mask.shape[0], y1 + hy)
            xa, xb = max(0, x - hx), min(mask.shape[1], x1 + hx)
            reference = previous[ya:yb, xa:xb]
            if not reference.any():
                growth += int(np.count_nonzero(new))
                continue
            distance = ndi.distance_transform_edt(~reference, sampling=(grid.dy, grid.dx))
            growth += int(np.count_nonzero(new &
                (distance[y - ya:y1 - ya, x - xa:x1 - xa] > threshold)))
    return growth


def _growth_pixels(previous, mask, grid, settings, decimate):
    if not (previous.any() and mask.any()):
        return 0
    coarse_previous = block_any(previous, decimate)
    coarse_mask = block_any(mask & ~previous, decimate)
    if coarse_previous.any():
        distance = ndi.distance_transform_edt(~coarse_previous,
                                              sampling=(grid.dy * decimate, grid.dx * decimate))
        # Each occupied block has an actual preceding pixel within
        # one block diagonal. The query pixel is likewise at most
        # one diagonal from its block center. Accept only a proven
        # upper bound; otherwise compute exact pixel distances.
        uncertainty = 2 * math.hypot(grid.dx * decimate, grid.dy * decimate)
        upper = distance + uncertainty
        if np.any(coarse_mask & (upper > settings['support']['max_span_mm'] + 1e-10)):
            return _tiled_growth_pixels(previous, mask, grid,
                                        settings['support']['max_span_mm'])
        return 0
    return int(np.count_nonzero(mask & ~previous))


def _analyze_layer(previous, mask, grid, settings, decimate, min_overlap, track_voids, cancel):
    """Everything about one layer that does not depend on layer order.

    The only cross-layer input is the immediately preceding layer's occupancy,
    so this is a sliding *pair*, not a prefix: given mask[i-1] and mask[i],
    every quantity here is independent of every other layer. That is what lets
    a worker pool cover the expensive work — both labelings and the growth
    distance transform — instead of just the island labeling, and scipy
    releases the GIL throughout, so plain threads do scale here.

    Returns raw evidence only. Every accumulator, diagnostic and union-find
    merge stays with the caller, in layer order.
    """
    cancel.check()
    labels, count, counts = _label_occupancy(mask)
    pixels = int(mask.sum())
    overlap = bad = on_edge = None
    growth = 0
    if previous is not None:
        # Bound the bincount key scratch rather than allocating a full-panel
        # int64 array, which is hundreds of MiB on a 9K printer.
        overlap = np.zeros(count + 1, dtype=np.int64)
        for row in range(0, mask.shape[0], 64):
            cancel.check()
            overlap += np.bincount(labels[row:row + 64][previous[row:row + 64]],
                                   minlength=count + 1)
        bad = np.flatnonzero((overlap < min_overlap) & (np.arange(count + 1) > 0))
        if len(bad):
            # A cropped grid cannot see past its own edge: a component that
            # continues outside the crop is cut off there and reads as
            # unsupported. Recording which components touch the border lets a
            # caller scanning a sub-volume treat those as unknown rather than
            # as islands. On a full-panel grid this is only evidence.
            on_edge = _border_flags(labels, count + 1)
            on_edge[0] = False
        growth = _growth_pixels(previous, mask, grid, settings, decimate)
    voids = void_components(mask) if track_voids else None
    return labels, count, counts, pixels, overlap, bad, on_edge, growth, voids


def analyze_layers(layers, grid, settings, *, cancel=None, budget=None, progress=no_progress,
                   growth_decimation=8, max_examples=128, track_voids=True):
    """Exact per-layer connectivity plus empty-space tracking over the build.

    Layer i depends only on layer i-1, never on the whole prefix, so almost all
    of the work runs concurrently across layers (``resources.workers``): both
    labelings, the overlap bincount and the growth distance transform all sit
    in `_analyze_layer` on the pool. What stays ordered here is the arithmetic
    — accumulators, bounded diagnostics, and the VoidForest union-find, whose
    identity is temporal. Concurrency is capped so the in-flight full-panel
    copies fit the memory budget. Every nonzero grayscale AA sample is material
    for topology.
    """
    cancel = cancel or CancellationToken()
    budget = budget or ResourceBudget(**settings.get('resources', {}))
    started = time.monotonic()
    report = ValidationReport(checks={'raster_connectivity': 'pass', 'overlap': 'pass',
                                      'growth_span': 'pass', 'enclosed_voids': 'pass',
                                      'transient_traps': 'pass'})
    layer_height = settings['process']['layer_height_mm']
    total_pixels = layer_count = births = growth_count = nonempty = edge_births = 0
    forest = VoidForest(grid.dx * grid.dy * layer_height, budget)
    decimate = max(1, int(growth_decimation))
    workers = _layer_worker_cap(budget, grid)
    report.metrics['analysis_workers'] = workers

    def _consume(layer, prepared):
        """Fold one layer's evidence into the report, strictly in layer order."""
        nonlocal total_pixels, layer_count, births, growth_count, nonempty, edge_births
        cancel.check()
        labels, count, counts, pixels, overlap, bad, on_edge, growth, voids = prepared
        total_pixels += pixels
        nonempty += int(count > 0)
        if bad is not None:
            births += len(bad)
            if len(bad):
                report.checks['raster_connectivity'] = 'fail'
                report.checks['overlap'] = 'fail'
                edge_births += int(on_edge[bad].sum())
                for component in bad[:max(0, max_examples - len(report.diagnostics))]:
                    row, col = np.argwhere(labels == component)[0]
                    report.diagnostics.append(Diagnostic(
                        'raster_island', 'Component has insufficient face overlap with preceding layer',
                        layer=layer.index, position_mm=grid.xy(int(row), int(col)) + [layer.z_mm],
                        details={'pixels': int(counts[component]), 'overlap_pixels': int(overlap[component]),
                                 'touches_crop_edge': bool(on_edge[component])}))
        growth_count += growth
        if growth:
            report.checks['growth_span'] = 'fail'
            if len(report.diagnostics) < max_examples:
                report.diagnostics.append(Diagnostic(
                    'growth_span',
                    'New material exceeds the configured distance to preceding material; '
                    'this is a configured distance rule at printer pitch, not a strength proof',
                    layer=layer.index,
                    details={'pixels': growth, 'limit_mm': settings['support']['max_span_mm'],
                             'decimation': decimate}))
        if track_voids:
            forest.merge(voids, layer.index, cancel)
        layer_count += 1
        progress('validate', layer_count, layer_count)

    def _occupancy(layer):
        mask = occupancy_mask(layer.mask)
        if mask.shape != (grid.height, grid.width):
            raise VoxelMillError('layer_shape', 'Layer dimensions differ from raster grid')
        return mask

    min_overlap = settings['support']['min_overlap_pixels']
    args = (grid, settings, decimate, min_overlap, track_voids, cancel)
    if workers <= 1:
        previous = None
        for layer in layers:
            cancel.check()
            mask = _occupancy(layer)
            _consume(layer, _analyze_layer(previous, mask, *args))
            previous = mask
    else:
        # Both labelings and the growth distance transform run on the pool;
        # only the accumulators, the diagnostics and the VoidForest union-find
        # stay ordered here. Holding the previous mask alongside each in-flight
        # layer is what makes that split legal, and `_layer_worker_cap` prices
        # those extra copies into the memory budget.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending, previous = deque(), None
            for layer in layers:
                cancel.check()
                mask = _occupancy(layer)
                pending.append((layer, pool.submit(_analyze_layer, previous, mask, *args)))
                previous = mask
                while len(pending) >= workers:
                    layer_i, future_i = pending.popleft()
                    _consume(layer_i, future_i.result())
            while pending:
                layer_i, future_i = pending.popleft()
                _consume(layer_i, future_i.result())

    if not track_voids:
        report.checks['enclosed_voids'] = 'not_run'
        report.checks['transient_traps'] = 'not_run'
        report.metrics.update(
            layer_count=layer_count, nonempty_layers=nonempty, island_components=births,
            island_components_on_crop_edge=edge_births,
            growth_violation_pixels=growth_count, exposed_pixels=total_pixels,
            raster_volume_mm3=total_pixels * grid.dx * grid.dy * layer_height,
            growth_decimation=decimate, diagnostic_examples_capped=max_examples,
            pixel_pitch_mm=[grid.dx, grid.dy], layer_height_mm=layer_height,
            seconds=time.monotonic() - started)
        if not nonempty:
            report.fail('empty_raster', 'No material was encoded at the configured pixel centers and layer heights')
        return report
    voids = forest.finish(float(settings['repair'].get('min_void_volume_mm3', 0.0)))
    report.metrics['enclosed_voids'] = voids
    if voids['count']:
        report.fail('enclosed_voids', 'Enclosed empty-space components remain in rasterized output', details=voids)
    trapped = voids['peak_present_trapped_volume_mm3']
    report.metrics['transient_traps'] = {
        'peak_volume_mm3': trapped, 'peak_layer': voids['peak_present_trapped_layer'],
        'definition': 'empty space enclosed by already-printed material except toward the growth direction',
    }
    if trapped > 0:
        report.checks['transient_traps'] = 'warn'
        report.diagnostics.append(Diagnostic(
            'transient_trap', 'Empty space is enclosed by already-printed material during part of the build',
            severity='warning', layer=voids['peak_present_trapped_layer'],
            details=report.metrics['transient_traps']))
    if not nonempty:
        report.fail('empty_raster', 'No material was encoded at the configured pixel centers and layer heights')
    report.metrics.update(
        layer_count=layer_count, nonempty_layers=nonempty, island_components=births,
        island_components_on_crop_edge=edge_births,
        growth_violation_pixels=growth_count, exposed_pixels=total_pixels,
        raster_volume_mm3=total_pixels * grid.dx * grid.dy * layer_height,
        growth_decimation=decimate, diagnostic_examples_capped=max_examples,
        pixel_pitch_mm=[grid.dx, grid.dy], layer_height_mm=layer_height,
        seconds=time.monotonic() - started)
    return report


def analyze_drainage(triangles, bounds, settings, *, budget=None, cancel=None, progress=no_progress,
                     voxels_per_radius=3.0):
    """Erosion-based effective-drainage check on its own analysis grid.

    A chamber drains when a clearance-``r`` path reaches the exterior, where
    ``pi * r**2`` is ``repair.min_orifice_area_mm2``. Equality passes on the
    sampled grid. XY cell-center samples can miss sub-grid walls.
    """
    from . import _native
    from .contracts import ResourceBudget
    cancel = cancel or CancellationToken()
    budget = budget or ResourceBudget(**settings['resources'])
    started = time.monotonic()
    area = float(settings['repair']['min_orifice_area_mm2'])
    if area <= 0:
        return {'status': 'not_applicable', 'reason': 'repair.min_orifice_area_mm2 is zero'}
    radius = math.sqrt(area / math.pi)
    pitch = radius / float(voxels_per_radius)
    bounds = np.asarray(bounds, dtype=float)
    extent = bounds[1] - bounds[0]
    dims = np.maximum(1, np.ceil(extent / pitch)).astype(int) + 4
    needed = int(np.prod(dims)) * 30
    if needed > budget.memory_gib * 1024**3 * 0.5:
        coarse = pitch * (needed / (budget.memory_gib * 1024**3 * 0.5)) ** (1 / 3)
        raise VoxelMillError('drainage_budget', 'Drainage analysis grid exceeds the memory budget',
                        {'pitch_mm': pitch, 'estimated_bytes': needed,
                         'smallest_affordable_pitch_mm': coarse})
    budget.require(needed, 'drainage analysis volume')
    nx, ny, nz = (int(v) for v in dims)
    x0, y0, z0 = bounds[0] - 2 * pitch
    raster = _native.Rasterizer(np.asarray(triangles), cancel.check)
    occupancy = np.zeros((nz, ny, nx), dtype=bool)
    subsamples = max(1, int(round(pitch / min(settings['printer']['pixel_pitch_mm']) / 8)))
    unclosed_rows = unclosed_slices = 0
    for k in range(nz):
        cancel.check()
        # Conservative decimation: any material in the cell marks the cell solid.
        cell = np.zeros((ny, nx), dtype=bool)
        open_here = 0
        for sub in range(subsamples):
            z = z0 + (k + (sub + 0.5) / subsamples) * pitch
            if z < bounds[0][2] or z > bounds[1][2]:
                continue
            sliced = raster.slice(float(z), nx, ny, float(x0), float(y0), pitch, pitch,
                                  cancel.check, 'nonzero')
            cell |= sliced['mask'] != 0
            open_here += int(sliced['odd_rows'])
        # A row whose winding never closes leaves unfilled cells inside solid
        # material, and one such cell joins a sealed chamber to outside air.
        # Carrying the count out is what keeps that from reading as drainage.
        if open_here:
            unclosed_rows += open_here
            unclosed_slices += 1
        occupancy[k] = cell
        progress('drainage', k + 1, nz)
    empty = ~occupancy
    empty[0] = empty[-1] = True
    empty[:, 0] = empty[:, -1] = True
    empty[:, :, 0] = empty[:, :, -1] = True
    clearance = ndi.distance_transform_edt(empty, sampling=(pitch, pitch, pitch))
    result = drainage_clearance(empty, clearance, pitch, radius, cancel,
                                float(settings['repair']['min_void_volume_mm3']))
    result.update(status='complete', analysis_pitch_mm=pitch, analysis_grid=[nx, ny, nz],
                  min_orifice_area_mm2=area, clearance_radius_mm=radius,
                  z_subsamples_per_cell=subsamples,
                  unclosed_rows=unclosed_rows, unclosed_slices=unclosed_slices,
                  occupancy_closed=not unclosed_rows,
                  sampling='XY cell centers and Z subsamples; sub-grid walls may be missed',
                  seconds=time.monotonic() - started)
    return result


def drainage_check(result, *, model_only=False):
    """Map a drainage analysis onto a check value; never invent a pass.

    An unclosed cross-section punches holes in the occupancy grid, and a single
    hole lets a sealed chamber reach outside air, so such a grid cannot certify
    drainage even when it finds no bottleneck. An enclosed chamber is a drainage
    failure in its own right; the layer analysis reports it separately, and
    agreeing here keeps the two from contradicting each other.

    ``model_only=True`` counts only model-class findings after classification;
    support-class necks still fail under the default and fill policies.
    """
    if result.get('status') != 'complete':
        return 'not_run'
    if model_only and result.get('classified'):
        if result.get('model_bottlenecked_components') or result.get('model_enclosed_components'):
            return 'fail'
    elif result.get('bottlenecked_components') or result.get('enclosed_components'):
        return 'fail'
    return 'pass' if result.get('occupancy_closed', True) else 'not_run'


def attribute_drainage_by_model(union_drain, model_drain):
    """Classify union drainage findings by absence from a model-only analysis.

    Excess bottlenecked or enclosed components relative to the model-only result
    are support-class. Tip/model crevices appear only after supports are added,
    so they classify as support; a hollow model cavity remains model-class.
    """
    if union_drain.get('status') != 'complete':
        return union_drain
    model_ok = model_drain is not None and model_drain.get('status') == 'complete'
    model_bn = int(model_drain.get('bottlenecked_components', 0)) if model_ok else 0
    model_enc = int(model_drain.get('enclosed_components', 0)) if model_ok else 0
    union_bn = int(union_drain.get('bottlenecked_components', 0))
    union_enc = int(union_drain.get('enclosed_components', 0))
    keep_bn = min(union_bn, model_bn) if model_ok else union_bn
    keep_enc = min(union_enc, model_enc) if model_ok else union_enc
    support_bn = max(0, union_bn - keep_bn)
    support_enc = max(0, union_enc - keep_enc)
    examples = list(union_drain.get('bottleneck_examples') or [])
    # Largest cores first; model cavities dominate tip crevices, so the first
    # keep_bn examples stay model-class and the remainder are support-class.
    support_volume = 0.0
    for index, example in enumerate(examples):
        kind = 'model' if index < keep_bn else 'support'
        example['void_class'] = kind
        if kind == 'support':
            support_volume += float(example.get('core_volume_mm3', 0.0))
    union_drain['bottleneck_examples'] = examples
    union_drain['classified'] = True
    union_drain['classification'] = 'model_only_absence'
    union_drain['model_bottlenecked_components'] = keep_bn
    union_drain['support_bottlenecked_components'] = support_bn
    union_drain['model_enclosed_components'] = keep_enc
    union_drain['support_enclosed_components'] = support_enc
    union_drain['support_bottlenecked_volume_mm3'] = support_volume
    union_drain['model_only'] = {
        'bottlenecked_components': model_bn if model_ok else None,
        'enclosed_components': model_enc if model_ok else None,
        'status': model_drain.get('status') if model_drain is not None else 'not_run',
    }
    return union_drain


def attribute_enclosed_voids_by_model(union_voids, model_voids):
    """Classify layer enclosed voids by absence from a model-only analysis."""
    if not union_voids:
        return union_voids
    model_count = int((model_voids or {}).get('count', 0))
    union_count = int(union_voids.get('count', 0))
    keep = min(union_count, model_count)
    support = max(0, union_count - keep)
    union_volume = float(union_voids.get('volume_mm3', 0.0))
    model_volume = float((model_voids or {}).get('volume_mm3', 0.0))
    # Volume attribution follows counts when the model is empty; otherwise keep
    # the model-reported volume as model-class and the remainder as support.
    if keep == 0:
        support_volume = union_volume
        kept_volume = 0.0
    elif support == 0:
        support_volume = 0.0
        kept_volume = union_volume
    else:
        kept_volume = min(union_volume, model_volume)
        support_volume = max(0.0, union_volume - kept_volume)
    examples = list(union_voids.get('examples') or [])
    for index, example in enumerate(examples):
        example['void_class'] = 'model' if index < keep else 'support'
    union_voids['examples'] = examples
    union_voids['classified'] = True
    union_voids['classification'] = 'model_only_absence'
    union_voids['model_count'] = keep
    union_voids['support_count'] = support
    union_voids['model_volume_mm3'] = kept_volume
    union_voids['support_volume_mm3'] = support_volume
    return union_voids


def apply_support_void_policy(report, settings):
    """Apply ``repair.support_void_policy`` to classified void and drainage findings.

    ``fail`` (default) leaves checks unchanged. ``ignore`` records support-class
    findings as warnings and fails only on model-class. ``fill`` does not ignore
    drainage necks: filling enclosed shells is a separate assembly-time step and
    is not a crevice fix.
    """
    policy = settings['repair'].get('support_void_policy', 'fail')
    report.metrics['support_void_policy'] = policy
    if policy == 'fail':
        return report

    voids = report.metrics.get('enclosed_voids')
    if policy == 'ignore' and voids and voids.get('classified'):
        support_count = int(voids.get('support_count', 0))
        model_count = int(voids.get('model_count', voids.get('count', 0)))
        ignored = {
            'count': support_count,
            'volume_mm3': float(voids.get('support_volume_mm3', 0.0)),
            'policy': policy,
        }
        report.metrics['ignored_support_voids'] = ignored
        if support_count and model_count == 0:
            report.checks['enclosed_voids'] = 'pass'
            report.diagnostics = [d for d in report.diagnostics if d.code != 'enclosed_voids']
            report.diagnostics.append(Diagnostic(
                'ignored_support_voids',
                'Support-class enclosed voids were recorded and ignored under support_void_policy',
                severity='warning', details=ignored))
        elif support_count:
            for diagnostic in report.diagnostics:
                if diagnostic.code == 'enclosed_voids':
                    diagnostic.details = {**(diagnostic.details or {}),
                                          'ignored_support_voids': ignored}

    drain = report.metrics.get('drainage')
    if drain and drain.get('classified') and drain.get('status') == 'complete':
        support_bn = int(drain.get('support_bottlenecked_components', 0))
        support_enc = int(drain.get('support_enclosed_components', 0))
        ignored = {
            'bottlenecked_components': support_bn,
            'enclosed_components': support_enc,
            'bottlenecked_volume_mm3': float(drain.get('support_bottlenecked_volume_mm3', 0.0)),
            'policy': policy,
        }
        report.metrics['ignored_support_bottlenecks'] = ignored
        if policy == 'ignore':
            report.checks['drainage_bottlenecks'] = drainage_check(drain, model_only=True)
            if support_bn or support_enc:
                report.diagnostics = [d for d in report.diagnostics
                                      if d.code != 'drainage_bottleneck']
                if report.checks['drainage_bottlenecks'] == 'pass':
                    report.diagnostics.append(Diagnostic(
                        'ignored_support_bottlenecks',
                        'Support-class drainage findings were recorded and ignored under support_void_policy',
                        severity='warning', details=ignored))
                else:
                    report.diagnostics.append(Diagnostic(
                        'drainage_bottleneck',
                        'Void connects to the exterior only through an orifice below the configured area',
                        details={**drain, 'ignored_support_bottlenecks': ignored}))
        # fill leaves drainage necks failing; shell fill is assembly-time only.
    return report


def _border_ids(labels):
    return np.unique(np.concatenate((labels[0].ravel(), labels[-1].ravel(),
                                    labels[:, 0].ravel(), labels[:, -1].ravel(),
                                    labels[:, :, 0].ravel(), labels[:, :, -1].ravel())))


def drainage_clearance(empty, clearance, pitch, radius, cancel=None, min_volume_mm3=0.0):
    """Find chambers disconnected by clearance erosion, even within exterior air.

    Labeling raw air alone loses bottlenecks: the chamber and outside share one
    label. Eroded cores retain the chamber behind a constriction. Bottleneck
    estimates use eight bisections of the threshold on this fixed sampled grid.
    The quantity is circular-equivalent clearance area, not arbitrary aperture
    cross-sectional area. Narrow slots and sub-grid features need finer analysis.
    """
    cancel = cancel or CancellationToken()
    core_labels, core_count = ndi.label(empty & (clearance >= radius - 1e-9), CROSS3)
    outside_cores = _border_ids(core_labels)
    pocket_ids = np.setdiff1d(np.arange(1, core_count + 1), outside_cores)
    void_labels, void_count = ndi.label(empty, CROSS3)
    outside_voids = set(_border_ids(void_labels)) - {0}
    sizes = np.bincount(void_labels.ravel(), minlength=void_count + 1)
    core_sizes = np.bincount(core_labels.ravel(), minlength=core_count + 1)
    boxes = ndi.find_objects(core_labels)
    pockets = []
    for component in pocket_ids:
        cancel.check()
        box = boxes[component - 1]
        local = np.argwhere(core_labels[box] == component)[0]
        seed = tuple(int(local[axis] + box[axis].start) for axis in range(3))
        # Fully enclosed chambers are handled by the separate enclosure check.
        if int(void_labels[seed]) not in outside_voids:
            continue
        low, high = 0.0, radius
        for _ in range(8):
            cancel.check()
            threshold = (low + high) / 2
            labels, _ = ndi.label(empty & (clearance >= threshold - 1e-9), CROSS3)
            identity = int(labels[seed])
            if identity and identity in _border_ids(labels):
                low = threshold
            else:
                high = threshold
        pockets.append({'component': int(component),
                        'core_volume_mm3': float(core_sizes[component] * pitch**3),
                        'bottleneck_area_mm2': math.pi * low**2,
                        'bottleneck_area_upper_mm2': math.pi * high**2,
                        'seed_zyx': list(seed)})
    enclosed = [label for label in range(1, void_count + 1) if label not in outside_voids]
    floor = float(min_volume_mm3)
    reported = [p for p in pockets if p['core_volume_mm3'] >= floor] if floor > 0 else pockets
    ignored = [p for p in pockets if p['core_volume_mm3'] < floor] if floor > 0 else []
    return {'void_components': int(void_count),
            'bottlenecked_components': len(reported),
            'bottlenecked_volume_mm3': sum(p['core_volume_mm3'] for p in reported),
            'bottleneck_volume_definition': 'eroded chamber cores; excludes narrow neck and boundary shell',
            'bottleneck_examples': sorted(reported, key=lambda p: -p['core_volume_mm3'])[:32],
            'min_void_volume_mm3': floor,
            'ignored_bottlenecked_components': len(ignored),
            'ignored_bottlenecked_volume_mm3': sum(p['core_volume_mm3'] for p in ignored),
            'enclosed_components': len(enclosed),
            'enclosed_volume_mm3': float(sum(sizes[label] for label in enclosed) * pitch**3),
            'area_definition': 'circular-equivalent maximum inscribed path clearance on sampled grid'}


def _coarse_occupancy(triangles, bounds, pitch, cancel, progress=no_progress, label='orientation'):
    """Solid-cell grid at ``pitch``, padded by one air cell on every side.

    Cells are marked from a single slice through the cell center, so features
    thinner than the pitch can be missed entirely. This grid ranks candidate
    orientations against each other; it is never the geometry that is checked.
    """
    from . import _native
    bounds = np.asarray(bounds, dtype=float)
    dims = np.maximum(1, np.ceil((bounds[1] - bounds[0]) / pitch).astype(int)) + 2
    nx, ny, nz = (int(dims[0]), int(dims[1]), int(dims[2]))
    x0, y0, z0 = bounds[0] - pitch
    raster = _native.Rasterizer(np.asarray(triangles), cancel.check)
    occupancy = np.zeros((nz, ny, nx), dtype=bool)
    unclosed = 0
    for k in range(nz):
        cancel.check()
        z = z0 + (k + 0.5) * pitch
        if bounds[0][2] <= z <= bounds[1][2]:
            sliced = raster.slice(float(z), nx, ny, float(x0), float(y0), pitch, pitch,
                                  cancel.check, 'nonzero')
            occupancy[k] = sliced['mask'] != 0
            unclosed += int(sliced['odd_rows'])
        progress(label, k + 1, nz)
    return occupancy, unclosed, (nx, ny, nz)


def gravity_drained(empty, cancel=None):
    """Air cells that can shed resin, sweeping from the gravity end back.

    Gravity points toward increasing Z, so resin leaves along a path whose Z
    never decreases. Drainage therefore propagates from a cell to the air
    directly beneath it in index order, and sideways through any air component
    on one level, since a level-wise step changes no height. Everything left is
    resin the part keeps when it comes off the plate.
    """
    cancel = cancel or CancellationToken()
    drained = np.zeros_like(empty)
    drained[-1] = empty[-1]  # the far Z boundary is open air
    for k in range(empty.shape[0] - 2, -1, -1):
        cancel.check()
        level = empty[k]
        seeded = level & drained[k + 1]
        if not seeded.any():
            drained[k] = False
            continue
        labels, count = ndi.label(level, CROSS)
        if not count:
            continue
        escaping = np.unique(labels[seeded])
        drained[k] = np.isin(labels, escaping[escaping > 0])
    return drained


def orientation_assessment(triangles, bounds, settings, *, pitch_mm=1.0, cancel=None,
                           progress=no_progress):
    """Orientation-dependent terms the cheap candidate score cannot see.

    Returns trapped resin, enclosed cavities, how much of the downward-facing
    area a vertical support could actually reach, and a stability proxy. All of
    it is measured on a coarse grid whose pitch is reported; these numbers rank
    orientations against one another and are not a validation result.
    """
    cancel = cancel or CancellationToken()
    started = time.monotonic()
    bounds = np.asarray(bounds, dtype=float)
    occupancy, unclosed, dims = _coarse_occupancy(triangles, bounds, pitch_mm, cancel, progress)
    cell = pitch_mm ** 3
    empty = ~occupancy
    labels, count = ndi.label(empty, CROSS3)
    outside = set(_border_ids(labels)) - {0}
    sizes = np.bincount(labels.ravel(), minlength=count + 1)
    enclosed = [label for label in range(1, count + 1) if label not in outside]
    exterior = np.isin(labels, list(outside)) if outside else np.zeros_like(empty)

    # Trapped resin: air that cannot shed toward +Z, ignoring sealed cavities,
    # which are counted on their own and fixed by sealing rather than rotating.
    trapped = exterior & ~gravity_drained(empty, cancel)
    trapped_volume = float(trapped.sum()) * cell

    # Support accessibility. A vertical pillar rises from the plate, so it can
    # only reach a cell with nothing but air beneath it in the same column.
    solid_below = np.cumsum(occupancy, axis=0) - occupancy
    downward_face = occupancy & np.pad(empty[:-1], ((1, 0), (0, 0), (0, 0)),
                                       constant_values=True)
    needs_support = downward_face & (np.arange(dims[2])[:, None, None] > 0)
    reachable = needs_support & (solid_below == 0)
    face_cells = int(needs_support.sum())
    accessible = float(reachable.sum()) / face_cells if face_cells else 1.0

    # Stability proxy: the lever arm between the solid's center of mass and the
    # centroid of the area that will carry pillars, as a fraction of the part's
    # own XY half-extent. The model hangs from supports rather than standing on
    # the plate, so its lowest layer is often a single cell; normalizing by that
    # footprint's radius divides by nearly nothing and saturates the term.
    occupied = np.argwhere(occupancy)
    if len(occupied):
        center = occupied.mean(axis=0)[[2, 1]] * pitch_mm
        carrying = np.argwhere(reachable if reachable.any() else needs_support)
        anchor = (carrying.mean(axis=0)[[2, 1]] * pitch_mm) if len(carrying) else center
        half_extent = max(float(np.max(occupied.max(axis=0) - occupied.min(axis=0))) * pitch_mm / 2,
                          pitch_mm)
        overhang = float(np.linalg.norm(center - anchor)) / half_extent
        footprint_mm2 = float(len(np.unique(carrying[:, [2, 1]], axis=0))) * pitch_mm ** 2 \
            if len(carrying) else 0.0
    else:
        overhang, footprint_mm2 = 0.0, 0.0
    return {'status': 'complete', 'analysis_pitch_mm': pitch_mm, 'analysis_grid': list(dims),
            'trapped_resin_mm3': trapped_volume,
            'enclosed_cavity_count': len(enclosed),
            'enclosed_cavity_mm3': float(sum(sizes[label] for label in enclosed)) * cell,
            'support_accessible_fraction': accessible,
            'downward_face_cells': face_cells,
            'center_of_mass_overhang': overhang,
            'overhang_basis': 'lever arm to supportable area, over the part XY half-extent',
            'footprint_mm2': footprint_mm2,
            'unclosed_rows': unclosed,
            'occupancy_closed': not unclosed,
            'basis': 'coarse single-sample occupancy; a ranking aid, not validation',
            'seconds': time.monotonic() - started}


def island_summary(report, settings=None, *, limit=64):
    """Compact island evidence pulled out of a completed layer analysis.

    This reads an existing ``ValidationReport``; it never recomputes anything,
    so the badge in the editor and the ``islands`` command cannot disagree with
    the validation they came from. ``truncated`` says whether positions were
    dropped, because a silently shortened list of failures reads as a shorter
    list of failures.
    """
    islands = [d for d in report.diagnostics if d.code == 'raster_island']
    positions = [{'layer': d.layer, 'position_mm': d.position_mm,
                  'message': d.message, 'details': d.details}
                 for d in islands[:max(0, int(limit))]]
    return {
        'check': report.checks.get('raster_connectivity', 'not_run'),
        'island_count': len(islands),
        'island_components': report.metrics.get('island_components'),
        'layers': report.metrics.get('layer_count'),
        'nonempty_layers': report.metrics.get('nonempty_layers'),
        # The threshold the finding was made against, so a count read later
        # cannot be compared with one taken under a different rule.
        'min_overlap_pixels': (None if settings is None
                               else settings['support']['min_overlap_pixels']),
        'layer_height_mm': (None if settings is None
                            else settings['process']['layer_height_mm']),
        'islands': positions,
        'truncated': len(islands) > len(positions),
        # The same pass computes overlap and growth for free; void tracking
        # reports itself as not_run when it was switched off, so every check
        # states its own status rather than being described from outside.
        'other_checks': {name: value for name, value in report.checks.items()
                         if name != 'raster_connectivity'},
    }
