"""Support detection, routing and geometry for a single placed part.

The analysis is driven by two independent sources of evidence, because neither
alone is sufficient:

* the mesh itself, for downward-facing area that must be contacted at the
  configured spacing, and
* an internal raster of the placed model, for pixel islands that are born with
  no material beneath them at all, which a face sampler can miss on spikes.

Routing prefers a vertical pillar to the plate, then an angled branch from a
free neighbouring column, then a contact onto model material that is already
printed lower down. Unroutable contacts that already have material in a 3x3
printer-pitch neighbourhood one layer below are dropped when
``support.drop_attached_unroutable`` is on (the default): they are near-vertical
walls sampled on both sides of the surface and do not need a pillar. Island
births and manual/correction contacts are never dropped. Everything else the
router could not solve is reported. Mechanical limits (slenderness, span,
bracing, anchor load) are configured heuristics and are labeled as such; only
the raster island and connectivity findings are exact for the documented masks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import time

import numpy as np
from scipy import ndimage as ndi

from .contracts import (CancellationToken, VoxelMillError, Diagnostic, ResourceBudget,
                        SupportEdge, SupportGraph, SupportNode, no_progress)
from .geometry import cylinder_between
from .bases import build_base
from .raster import RasterGrid
from .contact_parameters import (contact_key, normalize_contact_parameters,
                                  parameters_for_contact)
from .paint import apply_paint, normalize_paint

CROSS = ndi.generate_binary_structure(2, 1)


@dataclass
class ColumnField:
    """Run-length occupancy of the placed model on a coarse analysis grid.

    ``lo``/``hi`` are half-open layer indices per column, stored CSR-style so a
    column's runs are ``lo[ptr[c]:ptr[c+1]]``. Runs are ascending and disjoint.
    """
    grid: RasterGrid
    z0: float
    dz: float
    layers: int
    ptr: np.ndarray
    lo: np.ndarray
    hi: np.ndarray
    islands: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    first_material: np.ndarray | None = None
    #: Per run, whether the empty space immediately ABOVE it ever reaches the
    #: exterior. A contact on the underside of run r sits in the gap above run
    #: r-1; runs with index 0 sit above the open plate side.
    gap_exterior: np.ndarray | None = None
    #: Shaft capsules already emitted in this routing pass, as
    #: ``(start, end, radius)``. Later candidates that intersect one (except
    #: at an endpoint / planned brace joint) are skipped or rerouted.
    occupied_capsules: list = field(default_factory=list)

    def __post_init__(self):
        # Layer index of the lowest material in each column, or ``layers`` when
        # the column is empty. A column is free to the plate below layer k
        # exactly when this value is at least k, which makes the routing test a
        # single array lookup instead of a run search.
        if self.first_material is None:
            first = np.full(self.grid.width * self.grid.height, self.layers, dtype=np.int32)
            counts = np.diff(self.ptr)
            occupied = np.flatnonzero(counts > 0)
            if len(occupied):
                first[occupied] = self.lo[self.ptr[occupied]]
            self.first_material = first.reshape(self.grid.height, self.grid.width)

    def column(self, row, col):
        return int(row) * self.grid.width + int(col)

    def index_of(self, x, y):
        c = int(math.floor((x - self.grid.x0) / self.grid.dx))
        r = int(math.floor((y - self.grid.y0) / self.grid.dy))
        if not (0 <= c < self.grid.width and 0 <= r < self.grid.height):
            return None
        return r * self.grid.width + c

    def layer_of(self, z):
        return int(math.floor((z - self.z0) / self.dz))

    def z_of(self, index):
        return self.z0 + index * self.dz

    def runs(self, column):
        start, stop = int(self.ptr[column]), int(self.ptr[column + 1])
        return self.lo[start:stop], self.hi[start:stop]

    def reachable(self, column, layer_index):
        """True when the empty space just below ``layer_index`` drains outside.

        A contact inside a sealed cavity can be neither reached nor removed, so
        no support may be routed to it however printable the route looks.
        """
        if self.gap_exterior is None:
            return True
        start, stop = int(self.ptr[column]), int(self.ptr[column + 1])
        lo = self.lo[start:stop]
        position = int(np.searchsorted(lo, layer_index, side='right'))
        if position <= 1:
            return True  # open plate-side air below the lowest run
        return bool(self.gap_exterior[start + position - 2])

    def blocked(self, column, lo_index, hi_index):
        """True when any run overlaps the half-open layer span."""
        lo, hi = self.runs(column)
        if not len(lo) or hi_index <= lo_index:
            return False
        position = np.searchsorted(lo, hi_index, side='left')
        return bool(position > 0 and hi[position - 1] > lo_index)

    def top_below(self, column, index):
        """Highest run top that is at or below ``index``; None when free."""
        lo, hi = self.runs(column)
        position = np.searchsorted(hi, index, side='right')
        return int(hi[position - 1]) if position else None


def build_column_field(triangles, bounds, settings, *, pitch_mm=None, budget=None,
                       cancel=None, progress=no_progress):
    """Raster the placed model once and keep only per-column runs and islands."""
    from . import _native
    budget = budget or ResourceBudget(**settings['resources'])
    cancel = cancel or CancellationToken()
    bounds = np.asarray(bounds, dtype=float)
    printer_pitch = min(settings['printer']['pixel_pitch_mm'])
    pitch = float(pitch_mm) if pitch_mm else max(printer_pitch, min(settings['support']['spacing_mm'] / 20.0, 0.15))
    dz = float(settings['process']['layer_height_mm'])
    x0 = bounds[0][0] - 2 * pitch
    y0 = bounds[0][1] - 2 * pitch
    width = int(math.ceil((bounds[1][0] - bounds[0][0]) / pitch)) + 4
    height = int(math.ceil((bounds[1][1] - bounds[0][1]) / pitch)) + 4
    grid = RasterGrid(width, height, x0, y0, pitch, pitch)
    layers = max(1, int(math.ceil((bounds[1][2] - 1e-9) / dz)))
    budget.require(width * height * 40 + len(triangles) * 80 + 128 * 1024**2, 'support column analysis')
    raster = _native.Rasterizer(np.asarray(triangles), cancel.check)
    previous = np.zeros(width * height, dtype=bool)
    open_columns, open_layers, close_columns, close_layers, close_nodes = [], [], [], [], []
    islands, odd_rows, negative = [], 0, 0
    started = time.monotonic()
    from .validation import VoidForest
    forest = VoidForest(pitch * pitch * dz, budget)
    for index in range(layers):
        cancel.check()
        result = raster.slice(bounds[0][2] + (index + 0.5) * dz if bounds[0][2] > 0 else (index + 0.5) * dz,
                              width, height, x0, y0, pitch, pitch, cancel.check, 'nonzero')
        odd_rows += result['odd_rows']
        negative += result['negative_winding_crossings']
        mask = result['mask'] != 0
        flat = mask.ravel()
        opened = np.flatnonzero(flat & ~previous)
        closed = np.flatnonzero(~flat & previous)
        if len(opened):
            open_columns.append(opened)
            open_layers.append(np.full(len(opened), index, dtype=np.int32))
        empty_labels, empty_ids = forest.add(result['mask'], index, cancel)
        if len(closed):
            close_columns.append(closed)
            close_layers.append(np.full(len(closed), index, dtype=np.int32))
            # The component a column falls into the moment its run ends is the
            # empty region directly above that run.
            close_nodes.append(empty_ids[empty_labels.ravel()[closed]])
        if len(opened):
            labels, count = ndi.label(mask, CROSS)
            if count:
                overlap = np.bincount(labels.ravel()[previous], minlength=count + 1)
                for component in np.flatnonzero(overlap[1:] == 0) + 1:
                    pixels = np.argwhere(labels == component)
                    row, col = pixels[len(pixels) // 2]
                    islands.append({'layer': index, 'z_mm': bounds[0][2] + index * dz if bounds[0][2] > 0 else index * dz,
                                    'pixels': int(len(pixels)),
                                    'position_mm': grid.xy(int(row), int(col))})
        previous = flat
        progress('support_analysis', index + 1, layers)
    remaining = np.flatnonzero(previous)
    if len(remaining):
        close_columns.append(remaining)
        close_layers.append(np.full(len(remaining), layers, dtype=np.int32))
        # Air beyond the last layer is open, so those gaps drain by definition.
        close_nodes.append(np.full(len(remaining), -1, dtype=np.int64))
    voids = forest.finish()

    def _stack(columns, values):
        if not columns:
            return np.empty(0, np.int64), np.empty(0, np.int32)
        return np.concatenate(columns).astype(np.int64), np.concatenate(values)

    opened_c, opened_k = _stack(open_columns, open_layers)
    closed_c, closed_k = _stack(close_columns, close_layers)
    order = np.lexsort((opened_k, opened_c))
    opened_c, opened_k = opened_c[order], opened_k[order]
    nodes = np.concatenate(close_nodes).astype(np.int64) if close_nodes else np.empty(0, np.int64)
    order = np.lexsort((closed_k, closed_c))
    closed_c, closed_k, nodes = closed_c[order], closed_k[order], nodes[order]
    exterior = np.ones(len(nodes), dtype=bool)
    for position, node in enumerate(nodes):
        if node >= 0:
            exterior[position] = bool(forest.exterior[forest.find(int(node))])
    if len(opened_c) != len(closed_c) or not np.array_equal(opened_c, closed_c):
        raise VoxelMillError('support_analysis', 'Column run bookkeeping did not balance')
    counts = np.bincount(opened_c, minlength=width * height)
    ptr = np.zeros(width * height + 1, dtype=np.int64)
    np.cumsum(counts, out=ptr[1:])
    metrics = {'analysis_pitch_mm': pitch, 'layer_height_mm': dz, 'layers': layers,
               'columns': width * height, 'runs': int(len(opened_c)),
               'open_contour_rows': odd_rows, 'negative_winding_crossings': negative,
               'raster_islands': len(islands), 'sealed_gaps': int((~exterior).sum()),
               'enclosed_void_volume_mm3': voids['volume_mm3'],
               'seconds': time.monotonic() - started}
    return ColumnField(grid, bounds[0][2] if bounds[0][2] > 0 else 0.0, dz, layers, ptr,
                       opened_k.astype(np.int32), closed_k.astype(np.int32), islands, metrics,
                       gap_exterior=exterior)


def downward_contacts(triangles, settings, *, cancel=None, progress=no_progress,
                      max_lattice_faces=200000):
    """Samples on downward faces shallower than the configured overhang angle.

    ``overhang_angle_deg`` is measured from the plate: a face inclined at alpha
    has vertical normal component ``-cos(alpha)``, so a face needs support
    exactly when ``alpha`` is below the setting. A vertical wall (alpha = 90)
    never qualifies; a horizontal ceiling (alpha = 0) always does.
    """
    cancel = cancel or CancellationToken()
    support = settings['support']
    limit = math.cos(math.radians(float(support['overhang_angle_deg'])))
    spacing = float(support['spacing_mm'])
    points, areas = [], 0.0
    for start in range(0, len(triangles), 65536):
        cancel.check()
        chunk = np.asarray(triangles[start:start + 65536], dtype=np.float64)
        normals = np.cross(chunk[:, 1] - chunk[:, 0], chunk[:, 2] - chunk[:, 0])
        norm = np.linalg.norm(normals, axis=1)
        valid = norm > 0
        unit_z = np.zeros(len(chunk))
        unit_z[valid] = normals[valid, 2] / norm[valid]
        selected = valid & (unit_z < -limit)
        if not selected.any():
            continue
        faces = chunk[selected]
        areas += float(norm[selected].sum() / 2)
        points.append(faces.mean(axis=1))
        # A face wider than the spacing needs a lattice of contacts, not a
        # centroid: a single flat underside is often one pair of triangles, and
        # sampling it once leaves its whole perimeter unsupported.
        areas_selected = norm[selected] / 2
        big = np.flatnonzero(areas_selected > spacing * spacing / 2)
        for index in big[:max_lattice_faces]:
            cancel.check()
            steps = int(min(64, max(1, math.ceil(math.sqrt(2 * areas_selected[index]) / spacing))))
            lattice = np.array([(i, j, steps - i - j) for i in range(steps + 1)
                                for j in range(steps - i + 1)], dtype=np.float64) / steps
            points.append(lattice @ faces[index])
        if support.get('contour_supports'):
            contour = _perimeter_samples(faces, spacing)
            if len(contour):
                points.append(contour)
        progress('overhang_scan', min(start + 65536, len(triangles)), len(triangles))
    if support.get('boundary_supports'):
        boundary = _open_boundary_samples(triangles, spacing)
        if len(boundary):
            points.append(boundary)
    if not points:
        return np.empty((0, 3)), areas
    return np.concatenate(points), areas


def _sample_segment(start, end, spacing):
    start = np.asarray(start, dtype=np.float64)
    end = np.asarray(end, dtype=np.float64)
    length = float(np.linalg.norm(end - start))
    steps = max(1, int(math.ceil(length / max(float(spacing), 1e-9))))
    ts = np.linspace(0.0, 1.0, steps + 1)
    return (1.0 - ts)[:, None] * start + ts[:, None] * end


def _perimeter_samples(faces, spacing):
    """Samples along edges that belong to only one downward face (the contour)."""
    faces = np.asarray(faces, dtype=np.float64).reshape(-1, 3, 3)
    if not len(faces):
        return np.empty((0, 3))
    counts = {}
    segments = []
    for face in faces:
        for i in range(3):
            a, b = face[i], face[(i + 1) % 3]
            ra, rb = tuple(np.round(a, 5)), tuple(np.round(b, 5))
            key = (ra, rb) if ra <= rb else (rb, ra)
            counts[key] = counts.get(key, 0) + 1
            segments.append((key, a, b))
    points = []
    for key, a, b in segments:
        if counts[key] != 1:
            continue
        points.append(_sample_segment(a, b, spacing))
    if not points:
        return np.empty((0, 3))
    return np.concatenate(points)


def _open_boundary_samples(triangles, spacing, min_z=0.2):
    """Samples along open mesh boundary edges that sit above the plate."""
    from .ops import _directed_boundary_edges
    tris = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
    if not len(tris):
        return np.empty((0, 3))
    rounded = np.round(tris.reshape(-1, 3), 5)
    uniq, inverse = np.unique(rounded, axis=0, return_inverse=True)
    directed, _count = _directed_boundary_edges(inverse.reshape(-1, 3))
    points = []
    for a, b in directed:
        pa, pb = uniq[a], uniq[b]
        if (float(pa[2]) + float(pb[2])) / 2 < min_z:
            continue
        points.append(_sample_segment(pa, pb, spacing))
    if not points:
        return np.empty((0, 3))
    return np.concatenate(points)


def _cells(points, spacing):
    """XY spacing cells. Z is ignored so a tilted face cannot stack a forest."""
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    if not len(pts):
        return np.empty((0, 2), dtype=np.int64)
    return np.rint(pts[:, :2] / spacing).astype(np.int64)


def _thin(candidates, mandatory, spacing):
    """Keep the lowest automatic candidate per XY spacing cell; mandatory always kept.

    A 3D cell grid used to plant a contact every ``spacing`` along Z inside one
    XY cell on a tilted overhang. Automatic survivors are then greedily spaced
    at least ``spacing`` apart in 3D so diagonal cell neighbours cannot sit on
    top of each other. Mandatory contacts (raster islands, island-guard extra
    contacts, manual/paint enforcers) bypass the density cap.
    """
    mandatory = np.asarray(mandatory, dtype=float).reshape(-1, 3)
    candidates = np.asarray(candidates, dtype=float).reshape(-1, 3)
    if not len(candidates):
        return mandatory
    order = np.argsort(candidates[:, 2], kind='stable')
    ordered = candidates[order]
    cells = _cells(ordered, spacing)
    if len(mandatory):
        taken = _cells(mandatory, spacing)
        keys = np.concatenate((taken, cells))
        _, first = np.unique(keys, axis=0, return_index=True)
        chosen = np.sort(first[first >= len(taken)]) - len(taken)
    else:
        _, first = np.unique(cells, axis=0, return_index=True)
        chosen = np.sort(first)
    automatic = ordered[chosen]
    return np.concatenate((mandatory, automatic)) if len(mandatory) else automatic


@dataclass
class SupportPlan:
    graph: SupportGraph
    feet: np.ndarray
    solids: list
    diagnostics: list
    metrics: dict


def _free_to_plate(field, column, top_index, clearance):
    row, col = divmod(int(column), field.grid.width)
    return bool(field.first_material[row, col] >= max(0, top_index - clearance))


def _exclude_spheres(contact, penetration, break_point, extra=()):
    """Tip/anchor volumes the shaft capsule must not treat as collisions."""
    radius = max(float(penetration), float(break_point) / 2)
    spheres = [(np.asarray(contact, dtype=float), radius)]
    for item in extra:
        if item is None:
            continue
        spheres.append((np.asarray(item[0], dtype=float), float(item[1])))
    return spheres


def _cell_in_exclude(x, y, z_lo, z_hi, exclude):
    if not exclude:
        return False
    for center, radius in exclude:
        cz = min(max(float(center[2]), z_lo), z_hi)
        dx = x - float(center[0])
        dy = y - float(center[1])
        dz = cz - float(center[2])
        if dx * dx + dy * dy + dz * dz <= radius * radius:
            return True
    return False


def _segment_clear(field, start, end, clearance, samples=16, radius=0.0, exclude=None):
    """Capsule clearance against the column runs of the placed model.

    ``clearance`` is still in analysis layers for callers that used the old
    centerline sampler; a nonzero ``radius`` (mm) is added to that envelope.
    """
    clearance_mm = float(clearance) * float(field.dz) + float(radius)
    return _brace_clear(field, start, end, 0.0, clearance_mm, exclude=exclude)


def _shaft_clear(field, start, end, radius, clearance_mm, cancel=None, exclude=None):
    """True when the shaft capsule misses the model (intended contacts excluded)."""
    return _brace_clear(field, start, end, radius, clearance_mm, cancel=cancel, exclude=exclude)


def _hits_occupied(field, start, end, radius):
    """True when this capsule overlaps an already-routed shaft along its length.

    Interior samples skip the ends, so a T-joint or shared foot is not an
    overlap. Parallel shafts that share a long run are.
    """
    start, end = np.asarray(start, dtype=float), np.asarray(end, dtype=float)
    axis = end - start
    if float(axis @ axis) < 1e-20:
        return False
    for other_start, other_end, other_r in field.occupied_capsules:
        other_start = np.asarray(other_start, dtype=float)
        other_end = np.asarray(other_end, dtype=float)
        other_axis = other_end - other_start
        other_len2 = float(other_axis @ other_axis)
        limit2 = (float(radius) + float(other_r)) ** 2
        for step in (2, 3, 4, 5, 6):
            point = start + axis * (step / 8)
            if other_len2 < 1e-20:
                delta = point - other_start
            else:
                t = max(0.0, min(1.0, float((point - other_start) @ other_axis / other_len2)))
                delta = point - (other_start + t * other_axis)
            if float(delta @ delta) < limit2:
                return True
    return False


def _mark_occupied(field, start, end, radius):
    field.occupied_capsules.append((np.asarray(start, dtype=float),
                                    np.asarray(end, dtype=float), float(radius)))


def _fit_anchor_tips(gap, tip, anchor_length, min_tip, pillar_r):
    """Choose top/bottom tip lengths and whether a full-diameter middle fits.

    VoxelMill short-gap rule (not a bit-identical CHITUBOX transcription): a
    model-anchor run may swell to ``pillar_diameter_mm`` only when the gap
    fits a full top tip, a full bottom tip, and a remaining middle at least
    as long as that diameter. Shorter gaps stay a thin point-to-point; both
    ends remain a ball+cone, shortened no further than ``min_tip_length_mm``.
    """
    gap, tip, anchor_length, min_tip, pillar_r = (float(v) for v in
        (gap, tip, anchor_length, min_tip, pillar_r))
    if gap < 2 * min_tip:
        return None
    full = tip + anchor_length
    real_middle = 2 * pillar_r
    if gap >= full + real_middle:
        return tip, anchor_length, True
    if gap >= full:
        return tip, anchor_length, False
    # Prefer the configured bottom length; shorten the top down to min_tip,
    # then the bottom if the gap still cannot hold both.
    bottom = min(anchor_length, gap - min_tip)
    top = gap - bottom
    if top < min_tip - 1e-12 or bottom < min_tip - 1e-12:
        return None
    return top, bottom, False


def select_contacts(triangles, field, settings, *, cancel=None, progress=no_progress,
                    extra_contacts=(), removed_contacts=(), removal_radius_mm=None,
                    paint=None, object_groups=None):
    """Choose contact points. Returns ``(contacts, metrics)``.

    Automatic candidates come from downward faces and from raster island births.
    ``extra_contacts`` are kept unconditionally, which is what a correction pass
    and a manual GUI edit both need; ``removed_contacts`` suppress automatic ones
    within ``removal_radius_mm`` so a deletion survives a regeneration.
    Painted blockers drop automatic samples (and their coverage requirement);
    painted enforcers become extra contacts. Island births are never blocked.
    ``object_groups`` samples each part with its own support overlay, then the
    combined field still routes and validates the whole plate.
    """
    cancel = cancel or CancellationToken()
    paint = normalize_paint(paint)
    override_records = []
    if object_groups:
        sample_blocks, candidate_blocks, spacings, enforced_blocks = [], [], [], []
        downward_area = 0.0
        automatic_any = False
        paint_metrics = {'blocked_marks': 0, 'enforced_marks': 0, 'samples_blocked': 0,
                         'enforced_contacts': 0, 'automatic': False, 'block_wins_over_enforce': True}
        from .contact_parameters import PERSONAL_FIELDS
        for group in object_groups:
            group_settings = group['settings']
            spacing = float(group_settings['support']['spacing_mm'])
            spacings.append(spacing)
            samples, area = downward_contacts(
                group['triangles'], group_settings, cancel=cancel, progress=progress)
            samples, enforced, group_paint = apply_paint(samples, paint, spacing)
            paint_metrics['blocked_marks'] = group_paint['blocked_marks']
            paint_metrics['enforced_marks'] = group_paint['enforced_marks']
            paint_metrics['samples_blocked'] += group_paint['samples_blocked']
            paint_metrics['enforced_contacts'] += group_paint['enforced_contacts']
            original = int(len(samples) + group_paint['samples_blocked'])
            if original:
                area *= len(samples) / original
            sample_blocks.append(samples)
            downward_area += area
            enforced_blocks.append(enforced)
            if group_settings['support']['automatic']:
                automatic_any = True
                thinned = _thin(samples, [], spacing)
                candidate_blocks.append(thinned)
                keys = tuple(group.get('override_keys') or ())
                params = {key: group_settings['support'][key]
                          for key in keys if key in PERSONAL_FIELDS}
                if params:
                    for point in np.asarray(thinned, dtype=float).reshape(-1, 3):
                        override_records.append({
                            'position_mm': [float(value) for value in point],
                            'parameters': dict(params)})
        samples = (np.concatenate(sample_blocks) if sample_blocks
                   else np.empty((0, 3)))
        candidates = (np.concatenate(candidate_blocks) if candidate_blocks
                      else np.empty((0, 3)))
        enforced = (np.concatenate(enforced_blocks) if enforced_blocks
                    else np.empty((0, 3)))
        spacing = min(spacings) if spacings else float(settings['support']['spacing_mm'])
    else:
        spacing = float(settings['support']['spacing_mm'])
        automatic_any = bool(settings['support']['automatic'])
        # Sample the downward faces even when automatic selection is off. They
        # are what coverage is measured against, and a manual-only run is
        # exactly when an uncovered overhang is likely, so the check must still
        # be answerable.
        samples, downward_area = downward_contacts(
            triangles, settings, cancel=cancel, progress=progress)
        samples, enforced, paint_metrics = apply_paint(samples, paint, spacing)
        original = int(len(samples) + paint_metrics['samples_blocked'])
        if original:
            downward_area *= len(samples) / original
        candidates = samples if automatic_any else np.empty((0, 3))
    if automatic_any:
        mandatory = [np.array([*island['position_mm'], island['z_mm']]) for island in field.islands]
    else:
        candidates, mandatory = np.empty((0, 3)), []
    mandatory.extend(np.asarray(point, dtype=float) for point in extra_contacts)
    mandatory.extend(np.asarray(point, dtype=float) for point in enforced)
    contacts = _thin(candidates, mandatory, spacing)
    removed = np.asarray(list(removed_contacts), dtype=float).reshape(-1, 3)
    kept = np.asarray(list(extra_contacts), dtype=float).reshape(-1, 3)
    if len(removed) and len(contacts):
        radius = float(removal_radius_mm if removal_radius_mm is not None else spacing / 2)
        from scipy.spatial import cKDTree
        near = cKDTree(removed).query(contacts, distance_upper_bound=radius)[0]
        survives = ~np.isfinite(near)
        if len(kept):
            protected = cKDTree(kept).query(contacts, distance_upper_bound=1e-9)[0]
            survives |= np.isfinite(protected)
        contacts = contacts[survives]
    metrics = {'downward_face_area_mm2': downward_area, 'downward_samples': int(len(samples)),
               'manual_contacts': int(len(kept)), 'suppressed_contacts': int(len(removed)),
               'paint': paint_metrics, 'object_contact_parameters': override_records,
               'object_groups': int(len(object_groups) if object_groups else 0),
               'density_exempt_positions': [list(map(float, point)) for point in mandatory]}
    metrics.update(contact_coverage(samples, contacts, settings, downward_area))
    return contacts, metrics


def contact_coverage(samples, contacts, settings, downward_area_mm2):
    """How far the downward faces sit from a contact, and what each one carries.

    Coverage is the distance from every sampled downward face point to its
    nearest contact; the load is that face area divided among contacts by
    nearest assignment. Both are geometric: they say a contact was placed within
    reach of an overhang and how much area leans on it, never that the resulting
    pillar is strong enough. That needs calibration against real prints.
    """
    support = settings['support']
    spacing = float(support['spacing_mm'])
    gap_limit = float(support['max_contact_gap_mm']) or spacing
    load_limit = float(support['max_contact_load_mm2']) or 4 * spacing ** 2
    samples = np.asarray(samples, dtype=float).reshape(-1, 3)
    contacts = np.asarray(contacts, dtype=float).reshape(-1, 3)
    result = {'coverage_gap_limit_mm': gap_limit, 'contact_load_limit_mm2': load_limit,
              'coverage_basis': 'sampled downward faces; geometric reach, not strength'}
    if not len(samples):
        return {**result, 'coverage': 'not_applicable', 'anchor_load': 'not_applicable'}
    if not len(contacts):
        return {**result, 'coverage': 'fail', 'anchor_load': 'not_applicable',
                'uncovered_samples': int(len(samples)), 'uncovered_fraction': 1.0,
                'max_contact_gap_mm': None, 'coverage_reason': 'no contacts for downward samples'}
    from scipy.spatial import cKDTree
    tree = cKDTree(contacts)
    distance, nearest = tree.query(samples)
    uncovered = int((distance > gap_limit).sum())
    carried = np.bincount(nearest, minlength=len(contacts))
    per_contact = carried * (float(downward_area_mm2) / len(samples))
    overloaded = int((per_contact > load_limit).sum())
    return {**result,
            'max_contact_gap_mm': float(distance.max()),
            'mean_contact_gap_mm': float(distance.mean()),
            'uncovered_samples': uncovered,
            'uncovered_fraction': uncovered / len(samples),
            'coverage': 'fail' if uncovered else 'pass',
            'max_contact_load_mm2_actual': float(per_contact.max()),
            'overloaded_contacts': overloaded,
            'anchor_load': 'fail' if overloaded else 'pass'}


def _model_anchor_clear(field, column, x, y, surface_z, length, depth, radius, clearance, cancel):
    """Bound a new bottom connector on the existing column analysis grid.

    The lower endpoint must stay in the central column's immediately preceding
    material run. Above that surface the full connector envelope plus XY
    clearance must be empty. This is deliberately conservative for a taper,
    and only as accurate as the column lattice.
    """
    low, high = field.runs(column)
    # surface_z was produced by z_of(run_top); floor can lose one index on
    # a floating-point round trip (for example 43 * .05 / .05).
    top = int(round((surface_z - field.z0) / field.dz))
    position = int(np.searchsorted(high, top, side='right')) - 1
    if position < 0 or surface_z - depth < field.z_of(low[position]) - 1e-9:
        return False
    if not length:
        return True
    grid = field.grid
    reach = radius + float(clearance)
    c0 = max(0, int(math.floor((x - reach - grid.x0) / grid.dx)))
    c1 = min(grid.width, int(math.ceil((x + reach - grid.x0) / grid.dx)))
    r0 = max(0, int(math.floor((y - reach - grid.y0) / grid.dy)))
    r1 = min(grid.height, int(math.ceil((y + reach - grid.y0) / grid.dy)))
    last = int(math.ceil((surface_z + length - field.z0) / field.dz - 1e-9))
    for row in range(r0, r1):
        cancel.check()
        for col in range(c0, c1):
            # Distance to the nearest point of this cell, not its center.
            dx = max(grid.x0 + col * grid.dx - x, x - (grid.x0 + (col + 1) * grid.dx), 0.)
            dy = max(grid.y0 + row * grid.dy - y, y - (grid.y0 + (row + 1) * grid.dy), 0.)
            if dx * dx + dy * dy <= reach * reach and field.blocked(field.column(row, col), top, last):
                return False
    return True


def route_contacts(contacts, field, settings, *, cancel=None, branch_attempts=8, max_diagnostics=256,
                   contact_parameters=(), density_exempt=()):
    """Route given contacts to the plate or to already-printed model material."""
    cancel = cancel or CancellationToken()
    global_support = settings['support']
    normalized_parameters = normalize_contact_parameters(contact_parameters, settings)
    support = global_support
    started = time.monotonic()
    field.occupied_capsules = []
    exempt_keys = {contact_key(point) for point in density_exempt}
    spacing = float(support['spacing_mm'])
    pillar_r = float(support['pillar_diameter_mm']) / 2
    contact_r = float(support['contact_diameter_mm']) / 2
    # The tip cone's lower radius is its own parameter. It defaulted to the
    # pillar radius, and in the known-good CHITUBOX profile the two are equal,
    # which is exactly why conflating them went unnoticed.
    tip_base_r = (float(support['tip_base_diameter_mm']) / 2
                  if support['tip_base_diameter_mm'] else pillar_r)
    small_r = float(support['small_pillar_diameter_mm']) / 2
    small_limit = float(support['small_pillar_max_length_mm'])
    small_mode = support['small_pillar_mode']
    branch_tangent = math.tan(math.radians(float(support['pillar_angle_deg'])))
    tip = float(support['tip_length_mm'])
    min_tip = float(support['min_tip_length_mm'])
    penetration = float(support['penetration_mm'])
    anchor_length = float(support['model_anchor_length_mm'])
    anchor_depth = float(support['model_anchor_penetration_mm'])
    global_pillar_r = pillar_r
    global_anchor_length, global_anchor_depth = anchor_length, anchor_depth
    global_small_r, global_small_limit = small_r, small_limit
    global_tip, global_min_tip = tip, min_tip
    global_tip_base_r = tip_base_r
    clearance = max(1, int(math.ceil(float(support['support_clearance_mm']) / field.dz)))
    contacts = np.asarray(contacts, dtype=float).reshape(-1, 3)
    diagnostics, solids = [], []
    graph = SupportGraph()
    feet, pillars, foot_radii = [], [], []
    routed = {'vertical': 0, 'branched': 0, 'model_anchor': 0}
    failures = sealed = shortened = small_pillars = policy_blocked = 0
    density_skipped = 0
    unroutable_positions = []
    routed_automatic = []
    tree_jobs = []
    heights = []
    tips_used = []
    anchor_rejected = 0
    anchor_examples = []
    small_models = 0
    matched_override_keys = set()
    for order, point in enumerate(contacts):
        cancel.check()
        local_parameters = parameters_for_contact(normalized_parameters, point)
        key = contact_key(point)
        if key in normalized_parameters.by_position:
            matched_override_keys.add(key)
        support = dict(global_support)
        support.update(local_parameters)
        pillar_r = float(support['pillar_diameter_mm']) / 2
        contact_r = float(support['contact_diameter_mm']) / 2
        tip_base_r = (float(support['tip_base_diameter_mm']) / 2
                      if support['tip_base_diameter_mm'] else pillar_r)
        small_r = float(support['small_pillar_diameter_mm']) / 2
        small_limit = float(support['small_pillar_max_length_mm'])
        small_mode = support['small_pillar_mode']
        branch_tangent = math.tan(math.radians(float(support['pillar_angle_deg'])))
        tip = float(support['tip_length_mm'])
        min_tip = float(support['min_tip_length_mm'])
        penetration = float(support['penetration_mm'])
        anchor_length = float(support['model_anchor_length_mm'])
        anchor_depth = float(support['model_anchor_penetration_mm'])
        clearance = max(1, int(math.ceil(float(support['support_clearance_mm']) / field.dz)))
        x, y, z = (float(v) for v in point)
        column = field.index_of(x, y)
        if column is None:
            diagnostics.append(Diagnostic('support_outside_analysis',
                                          'Contact fell outside the analysis grid', position_mm=[x, y, z]))
            failures += 1
            continue
        contact_index = field.layer_of(z)
        if not field.reachable(column, contact_index):
            if len(diagnostics) < max_diagnostics:
                diagnostics.append(Diagnostic(
                    'support_in_sealed_cavity',
                    'Contact lies in empty space that never reaches the exterior; no support there '
                    'could be reached or removed', severity='warning', position_mm=[x, y, z]))
            sealed += 1
            continue
        # Too close to the plate for a full tapered tip means the cone simply
        # starts at the plate; the contact is still reached.
        tip_used = tip
        base_z = max(0.0, z - tip)
        kind = None
        anchor = None
        elbow = None
        is_small_model = False
        bottom_used = 0.0
        full_middle = True
        exempt = key in exempt_keys
        clearance_mm = float(support['support_clearance_mm'])
        # The shaft capsule inflates by radius+clearance past the junction, which
        # would otherwise reject the intended tip sitting in the model.
        grid_pad = math.hypot(field.grid.dx, field.grid.dy) / 2
        contact_pad = pillar_r + clearance_mm + grid_pad
        # Local curvature next to a tip (a sphere, a fillet) sits in the
        # neighbouring analysis cells. Treat the tip plus a pad around the
        # junction as intended contact, not a shaft collision. A hole halfway
        # down the pillar is still a collision.
        extra = []
        lo, hi = base_z - contact_pad, z + max(penetration, support['break_point_diameter_mm'] / 2)
        for step in range(7):
            extra.append(((x, y, lo + (hi - lo) * step / 6), contact_pad))
        exclude = _exclude_spheres(
            (x, y, z), penetration, support['break_point_diameter_mm'], extra=extra)

        # Called only within this iteration, so the late-bound loop variables
        # below are the current contact's.
        def _usable_shaft(start, end, radius):
            if end[2] - start[2] <= 1e-9 and math.hypot(end[0] - start[0], end[1] - start[1]) <= 1e-9:
                return True
            if not _shaft_clear(field, start, end, radius, clearance_mm, cancel, exclude):  # noqa: B023
                return False
            return not _hits_occupied(field, start, end, radius)

        plate_kind = plate_anchor = plate_elbow = None
        # A free column is a vertical plate route. Capsule-testing that run
        # against neighbouring cells treats local curvature (a sphere) as a
        # collision and forces a 45° branch that then fails drainage. The
        # hole-clip case is an angled shaft; those still use _usable_shaft.
        if _free_to_plate(field, column, contact_index, clearance):
            occupied = _hits_occupied(field, (x, y, 0.0), (x, y, base_z), pillar_r)
            if occupied and not exempt:
                # A second vertical on top of an existing shaft. Skip it rather
                # than weaving a 45° branch that fails drainage.
                density_skipped += 1
                continue
            plate_kind, plate_anchor = 'vertical', (x, y, 0.0)
        if plate_kind is None:
            best = None
            radius = max(1, int(math.ceil(min(2 * spacing, max(0.0, base_z)) / field.grid.dx)))
            row, col = divmod(column, field.grid.width)
            r0, r1 = max(0, row - radius), min(field.grid.height, row + radius + 1)
            c0, c1 = max(0, col - radius), min(field.grid.width, col + radius + 1)
            window = field.first_material[r0:r1, c0:c1] >= max(0, contact_index - clearance)
            if window.any():
                rows, cols = np.nonzero(window)
                px = field.grid.x0 + (cols + c0 + .5) * field.grid.dx
                py = field.grid.y0 + (rows + r0 + .5) * field.grid.dy
                lateral = np.hypot(px - x, py - y)
                # A branch runs at support.pillar_angle_deg from horizontal, so
                # reaching sideways by `lateral` costs `lateral * tan(angle)` of
                # drop. At the historical 45 degrees that is one lateral
                # distance; a steeper angle buys stiffness and costs reach.
                drop = lateral * branch_tangent
                usable = (lateral > 1e-9) & (lateral <= 2 * spacing) & (drop < base_z)
                tested = 0
                limit = max(int(branch_attempts) * 8, 256)
                for pick in np.argsort(np.where(usable, lateral, np.inf)):
                    if not usable[pick] or tested >= limit:
                        break
                    nx, ny, distance = float(px[pick]), float(py[pick]), float(lateral[pick])
                    elbow_z = base_z - float(drop[pick])
                    # Cheap reject: a vertical run whose neighbourhood is occupied
                    # below the elbow cannot be a plate branch.
                    if elbow_z > 1e-9:
                        br = int(math.floor((ny - field.grid.y0) / field.grid.dy))
                        bc = int(math.floor((nx - field.grid.x0) / field.grid.dx))
                        reach_cells = max(1, int(math.ceil(
                            (pillar_r + clearance_mm) / min(field.grid.dx, field.grid.dy))))
                        elbow_index = max(0, field.layer_of(elbow_z))
                        rr0, rr1 = max(0, br - reach_cells), min(field.grid.height, br + reach_cells + 1)
                        cc0, cc1 = max(0, bc - reach_cells), min(field.grid.width, bc + reach_cells + 1)
                        if np.any(field.first_material[rr0:rr1, cc0:cc1] < elbow_index):
                            continue
                    tested += 1
                    angled = ((nx, ny, elbow_z), (x, y, base_z))
                    vertical = ((nx, ny, 0.0), (nx, ny, elbow_z))
                    if (_usable_shaft(*angled, pillar_r)
                            and (elbow_z <= 1e-9 or _usable_shaft(*vertical, pillar_r))):
                        best = (distance, nx, ny, elbow_z)
                        break
            if best is not None:
                plate_kind = 'branched'
                plate_anchor = (best[1], best[2], 0.0)
                plate_elbow = (best[1], best[2], best[3])
        if plate_kind is not None:
            kind, anchor, elbow = plate_kind, plate_anchor, plate_elbow
        # Evaluate a model anchor even when a plate branch exists. At zero
        # avoidance both candidates compete on total centerline length; at one
        # the historical plate-first order is preserved exactly. A plate
        # candidate whose capsule hit the model or an existing shaft already
        # fell through, so this is "the other of plate vs model".
        below = field.top_below(column, contact_index)
        model_anchor = None
        candidate_small = False
        if below is not None:
            anchor_z = field.z_of(below)
            gap = z - anchor_z
            candidate_small = small_mode == 'model' and small_r > 0 and 1e-9 < gap <= small_limit
            min_anchor_gap = (2 * min_tip) if anchor_length else min_tip
            if candidate_small:
                low_runs, high_runs = field.runs(column)
                at_top = int(np.searchsorted(high_runs, field.layer_of(z), side='right'))
                upper_fits = (at_top < len(low_runs) and field.z_of(low_runs[at_top]) <= z + 1e-9 and
                              z + support['small_pillar_upper_depth_mm'] <= field.z_of(high_runs[at_top]) + 1e-9)
                if upper_fits and _model_anchor_clear(field, column, x, y, anchor_z, gap,
                        support['small_pillar_lower_depth_mm'], small_r,
                        support['support_clearance_mm'], cancel):
                    model_anchor = (x, y, anchor_z)
                else:
                    anchor_rejected += 1
            elif gap >= min_anchor_gap:
                fitted = (_fit_anchor_tips(gap, tip, anchor_length, min_tip, pillar_r)
                          if anchor_length else (min(tip, gap), 0.0, True))
                if fitted is None:
                    anchor_rejected += 1
                else:
                    fit_tip, fit_bottom, fit_full = fitted
                    candidate_run = max(0., gap - fit_tip - fit_bottom)
                    if not fit_full:
                        candidate_r = small_r if small_r > 0 else contact_r
                    elif small_mode == 'middle' and small_r > 0 and candidate_run <= small_limit:
                        candidate_r = small_r
                    else:
                        candidate_r = pillar_r
                    bottom_r = float(support['model_anchor_diameter_mm']) / 2 or candidate_r
                    # Only the new connector is examined here; the historical
                    # direct attachment remains unchanged when both dimensions are 0.
                    if (not (fit_bottom or anchor_depth) or _model_anchor_clear(
                            field, column, x, y, anchor_z, fit_bottom, anchor_depth,
                            max(bottom_r, candidate_r), support['support_clearance_mm'], cancel)):
                        middle_lo, middle_hi = anchor_z + fit_bottom, z - fit_tip
                        extra_exclude = [((x, y, anchor_z),
                                          max(anchor_depth, support['break_point_diameter_mm'] / 2,
                                              candidate_r + clearance_mm + grid_pad))]
                        exclude_both = exclude + extra_exclude
                        middle_ok = (middle_hi - middle_lo <= 1e-9 or _shaft_clear(
                            field, (x, y, middle_lo), (x, y, middle_hi), candidate_r, clearance_mm,
                            cancel, exclude_both))
                        occupied = (middle_hi - middle_lo > 1e-9 and _hits_occupied(
                            field, (x, y, middle_lo), (x, y, middle_hi), candidate_r))
                        if middle_ok and not occupied:
                            model_anchor = (x, y, anchor_z)
                        else:
                            anchor_rejected += 1
                    else:
                        anchor_rejected += 1
            elif anchor_length:
                anchor_rejected += 1
        if model_anchor is not None:
            if not support['allow_part_to_part']:
                if kind is None:
                    policy_blocked += 1
            else:
                plate_length = (math.inf if kind is None else
                                (elbow[2] + math.dist(elbow, (x, y, base_z)) + tip
                                 if elbow is not None else z))
                avoidance = float(support['part_to_part_avoidance'])
                choose_model = kind is None or (avoidance < 1 and
                    z - model_anchor[2] < plate_length * (1 - avoidance))
                if choose_model:
                    kind, anchor, elbow = 'model_anchor', model_anchor, None
                    is_small_model = candidate_small
                    gap = z - anchor[2]
                    if not is_small_model and anchor_length:
                        fitted = _fit_anchor_tips(gap, tip, anchor_length, min_tip, pillar_r)
                        if fitted is None:
                            kind = None
                        else:
                            tip_used, bottom_used, full_middle = fitted
                            base_z = z - tip_used
                            if abs(tip_used - tip) > 1e-9:
                                shortened += 1
                    elif not is_small_model and gap <= tip:
                        tip_used, base_z = gap, anchor[2]
                        shortened += 1
        if kind is None:
            # Internal overhangs inside a porous part are genuinely unreachable;
            # they are counted in full and sampled in the diagnostics.
            unroutable_positions.append([x, y, z])
            if len(diagnostics) < max_diagnostics:
                diagnostics.append(Diagnostic('support_unroutable',
                                              'No permitted collision-free route to plate or model',
                                              severity='warning', position_mm=[x, y, z]))
            failures += 1
            continue
        routed[kind] += 1
        if is_small_model:
            from .support_segments import small_model_pillar
            if local_parameters:
                graph.overrides.append({'contact': f'contact{order}', 'reason': 'effective_parameters',
                                        'parameters': dict(local_parameters)})
            solids.append(small_model_pillar(anchor, (x, y, z), small_r,
                          support['small_pillar_shape'], support['small_pillar_upper_depth_mm'],
                          support['small_pillar_lower_depth_mm']))
            foot, head = f'foot{order}', f'contact{order}'
            graph.nodes.extend([SupportNode(foot, list(anchor), 'model_anchor'),
                                SupportNode(head, [x, y, z], 'contact')])
            graph.edges.append(SupportEdge(foot, head, small_r, 'small_model'))
            _mark_occupied(field, anchor, (x, y, z), small_r)
            if not exempt:
                routed_automatic.append((x, y, z))
            small_pillars += 1
            small_models += 1
            heights.append(gap)
            slenderness = gap / (2 * small_r)
            if slenderness > float(support['max_slenderness']):
                if len(diagnostics) < max_diagnostics:
                    diagnostics.append(Diagnostic('support_slenderness',
                        'Small model pillar exceeds the configured slenderness limit (heuristic)',
                        severity='warning', position_mm=[x, y, z],
                        details={'slenderness': slenderness, 'limit': float(support['max_slenderness'])}))
                graph.overrides.append({'contact': head, 'reason': 'slenderness', 'value': slenderness})
            continue
        # A short run may use the thinner pillar class. The length is known
        # before any geometry or graph edge is emitted, so the choice is made
        # once and the whole pillar, elbow included, is built at that radius.
        run_length = (elbow[2] + math.dist(elbow, (x, y, base_z)) if elbow is not None
                      else max(0., base_z - anchor[2] - (bottom_used if kind == 'model_anchor' else 0.)))
        if kind == 'model_anchor' and not full_middle:
            run_r = small_r if small_r > 0 else contact_r
            if small_r > 0:
                small_pillars += 1
        elif small_mode == 'middle' and small_r > 0 and run_length <= small_limit:
            run_r = small_r
            small_pillars += 1
        else:
            run_r = pillar_r
        foot = f'foot{order}'
        junction = f'joint{order}'
        head = f'contact{order}'
        if local_parameters:
            graph.overrides.append({'contact': head, 'reason': 'effective_parameters',
                                    'parameters': dict(local_parameters)})
        graph.nodes.append(SupportNode(foot, [anchor[0], anchor[1], anchor[2]],
                                       'foot' if kind != 'model_anchor' else 'model_anchor'))
        graph.nodes.append(SupportNode(junction, [x, y, base_z], 'junction'))
        graph.nodes.append(SupportNode(head, [x, y, z], 'contact'))
        middle_start = foot
        if kind == 'model_anchor' and bottom_used:
            middle_start = f'anchor_joint{order}'
            graph.nodes.append(SupportNode(middle_start, [x, y, anchor[2] + bottom_used], 'anchor_junction'))
            graph.edges.append(SupportEdge(foot, middle_start,
                               float(support['model_anchor_diameter_mm']) / 2 or run_r, 'bottom'))
        if elbow is None:
            graph.edges.append(SupportEdge(middle_start, junction, run_r, kind))
        else:
            graph.edges.extend([SupportEdge(middle_start, f'elbow{order}', run_r, kind),
                                SupportEdge(f'elbow{order}', junction, run_r, kind)])
        graph.edges.append(SupportEdge(junction, head, contact_r, 'tip'))
        from .support_segments import tip_segment, elbow_sphere
        if elbow is not None:
            graph.nodes.append(SupportNode(f'elbow{order}', list(elbow), 'elbow'))
            if elbow[2] > 1e-9:
                solids.append(cylinder_between(anchor, elbow, run_r))
                # Branched vertical runs brace with plate pillars.
                pillars.append((anchor[0], anchor[1], elbow[2], run_r))
                _mark_occupied(field, anchor, elbow, run_r)
            solids.append(cylinder_between(elbow, (x, y, base_z), run_r))
            solids.append(elbow_sphere(elbow, run_r))
            _mark_occupied(field, elbow, (x, y, base_z), run_r)
            length = run_length
        else:
            length = run_length
            middle_z = anchor[2]
            if kind == 'model_anchor':
                middle_z += bottom_used
                if bottom_used:
                    bottom_contact_r = float(support['model_anchor_diameter_mm']) / 2 or contact_r
                    bottom_span = bottom_used + anchor_depth
                    bottom_ball = (support['break_point_diameter_mm']
                                   if support['break_point_diameter_mm'] <= bottom_span else 0.0)
                    bottom_base = run_r if support['model_anchor_shape'] == 'cone' else bottom_contact_r
                    solids.append(tip_segment(
                        (x, y, middle_z), (x, y, anchor[2] - anchor_depth),
                        bottom_base, bottom_contact_r, support['model_anchor_shape'], bottom_ball))
                    collar_h = min(bottom_used + anchor_depth, z + penetration - middle_z) * .01
                    collar_r = min(bottom_contact_r, bottom_base, run_r, tip_base_r, contact_r) * .5
                    if collar_h > 1e-9 and collar_r > 1e-9:
                        solids.append(cylinder_between((x, y, middle_z - collar_h),
                                                      (x, y, middle_z + collar_h), collar_r))
                else:
                    middle_z -= anchor_depth
                if len(anchor_examples) < max_diagnostics:
                    anchor_examples.append({'contact': head, 'surface_z_mm': anchor[2],
                        'bottom_z_mm': anchor[2] - anchor_depth,
                        'junction_z_mm': anchor[2] + bottom_used,
                        'diameter_mm': float(support['model_anchor_diameter_mm']) or run_r * 2})
            if base_z - middle_z > 1e-9:
                if kind == 'vertical' and support['tree_supports'] and base_z > 1e-9:
                    tree_jobs.append({'x': x, 'y': y, 'base_z': base_z, 'run_r': run_r,
                                      'middle_z': middle_z, 'order': order,
                                      'tip_radius': min(tip_base_r, contact_r),
                                      'tip_span': float(z + penetration - base_z)})
                else:
                    solids.append(cylinder_between((x, y, middle_z), (x, y, base_z), run_r))
                    _mark_occupied(field, (x, y, middle_z), (x, y, base_z), run_r)
                    if kind == 'vertical':
                        pillars.append((x, y, base_z, run_r))
        heights.append(length)
        tip_span = float(z + penetration - base_z)
        top_ball = (support['break_point_diameter_mm']
                    if support['break_point_diameter_mm'] and support['break_point_diameter_mm'] <= tip_span
                    else 0.0)
        top_base = run_r if (kind == 'model_anchor' and not full_middle) else tip_base_r
        solids.append(tip_segment((x, y, base_z), (x, y, z + penetration), top_base, contact_r,
                                  support['tip_shape'], top_ball))
        if elbow is not None:
            from .support_segments import shoulder_joint
            solids.append(shoulder_joint((x, y, base_z), run_r, min(top_base, contact_r), tip_span))
        tips_used.append(tip_used)
        if not exempt:
            routed_automatic.append((x, y, z))
        if kind != 'model_anchor':
            if not (kind == 'vertical' and support['tree_supports'] and base_z > 1e-9):
                feet.append((anchor[0], anchor[1]))
                foot_radii.append(contact_r if kind == 'vertical' and base_z <= 1e-9 else run_r)
        slenderness = length / (2 * run_r) if run_r > 0 else math.inf
        if slenderness > float(support['max_slenderness']):
            if len(diagnostics) < max_diagnostics:
                diagnostics.append(Diagnostic('support_slenderness',
                                          'Pillar exceeds the configured slenderness limit (heuristic)',
                                              severity='warning', position_mm=[x, y, z],
                                              details={'slenderness': slenderness,
                                                       'limit': float(support['max_slenderness'])}))
            graph.overrides.append({'contact': head, 'reason': 'slenderness', 'value': slenderness})

    support = global_support
    pillar_r = global_pillar_r
    anchor_length, anchor_depth = global_anchor_length, global_anchor_depth
    small_r, small_limit = global_small_r, global_small_limit
    tip, min_tip = global_tip, global_min_tip
    tip_base_r = global_tip_base_r
    unmatched = [row for row in normalized_parameters
                  if contact_key(row['position_mm']) not in matched_override_keys]
    for row in unmatched[:max_diagnostics]:
        diagnostics.append(Diagnostic('contact_parameters_unmatched',
            'Authored contact parameters did not match a routed contact; orientation or coordinates may have changed',
            severity='warning', position_mm=list(row['position_mm']), details={'parameters': row['parameters']}))
    tree_metrics = _emit_tree_supports(tree_jobs, solids, pillars, feet, foot_radii,
                                       field, settings, cancel=cancel, graph=graph)
    brace_evidence = {'collision_rejected': 0}
    braces = (_brace(pillars, settings, solids, field=field, evidence=brace_evidence,
                     cancel=cancel, graph=graph, feet=feet, foot_radii=foot_radii)
              if support['auto_bracing'] else 0)
    raft = None
    base = build_base(feet, settings, pillar_r, foot_radii=foot_radii, cancel=cancel)
    raft = base['solid']
    if not feet and len(contacts):
        diagnostics.append(Diagnostic('support_no_feet',
                                      'Every routed support anchors on the model; no base was generated',
                                      severity='warning'))
    metrics = {
        'contacts_requested': int(len(contacts)),
        'contacts_routed': int(sum(routed.values())),
        'contacts_failed': failures,
        'unroutable_positions': unroutable_positions,
        'contacts_dropped_attached': 0,
        'tree': tree_metrics,
        'contacts_in_sealed_cavities': sealed,
        'routing': routed,
        'allow_part_to_part': support['allow_part_to_part'],
        'part_to_part_avoidance': support['part_to_part_avoidance'],
        'contacts_blocked_by_policy': policy_blocked,
        'contact_parameters': {'authored': len(normalized_parameters),
                               'matched': len(matched_override_keys),
                               'unmatched': len(unmatched)},
        'model_anchor': {'shape': support['model_anchor_shape'] if anchor_length else 'direct',
                         'length_mm': anchor_length, 'penetration_mm': anchor_depth,
                         'diameter_mm': float(support['model_anchor_diameter_mm']),
                         'diameter_basis': 'configured endpoint diameter; 0 derives the chosen middle diameter',
                         'candidates_rejected': anchor_rejected, 'examples': anchor_examples,
                         'examples_capped_at': max_diagnostics,
                         'clearance_basis': 'column-grid footprint and central-column penetration; not exact surface clearance'},
        'contacts_skipped_density': int(density_skipped),
        'contacts_with_shortened_tip': int(shortened),
        'min_tip_used_mm': min(tips_used, default=0.0),
        'tip_length_mm': tip,
        'min_tip_length_mm': min_tip,
        'braces': braces,
        'braces_collision_rejected': brace_evidence['collision_rejected'],
        'brace_diameter_mm': float(support['brace_diameter_mm']) or pillar_r,
        'brace_max_distance_mm': (float(support['brace_max_distance_mm'])
                                  or spacing * 1.5),
        'braces_capped': brace_evidence.get('capped', False),
        'brace_candidates_examined': brace_evidence.get('examined', 0),
        'brace_origins_examined': brace_evidence.get('origins_examined', 0),
        'brace_spacing_mm': brace_geometry(settings, pillar_r)[0],
        'brace_max_length_mm': brace_geometry(settings, pillar_r)[1],
        'brace_geometry': f"downward {support['brace_angle_deg']:g} degrees; origins descend from full-width shoulders",
        **{key: support[key] for key in ('brace_destination', 'brace_pattern',
            'brace_branches_per_node', 'brace_angle_deg', 'brace_min_height_mm', 'brace_azimuth_deg')},
        'brace_rejections': {key: value for key, value in brace_evidence.items()
                             if key.endswith('_rejected')},
        'brace_new_feet': brace_evidence.get('new_feet', 0),
        'tip_base_diameter_mm': tip_base_r * 2,
        'pillar_angle_deg': float(support['pillar_angle_deg']),
        'small_pillars': int(small_pillars),
        'small_model_pillars': small_models,
        'small_pillar': {'mode': small_mode, 'shape': support['small_pillar_shape'],
                         'diameter_mm': small_r * 2, 'max_length_mm': small_limit,
                         'upper_depth_mm': support['small_pillar_upper_depth_mm'],
                         'lower_depth_mm': support['small_pillar_lower_depth_mm'],
                         'selection_basis': ('whole surface-to-surface gap, model anchors only'
                                             if small_mode == 'model' else 'middle centerline length'),
                         'enabled': bool(small_r and small_limit)},
        'base': base['record'],
        'raster_islands': len(field.islands),
        'spacing_mm': spacing,
        'overhang_angle_deg': float(support['overhang_angle_deg']),
        'max_pillar_length_mm': max(heights, default=0.0),
        'diagnostics_capped_at': max_diagnostics,
        'mechanics': 'configured heuristic limits; not a strength proof',
        'seconds': time.monotonic() - started,
        'analysis': field.metrics,
    }
    graph.diagnostics = [d.__dict__ for d in diagnostics]
    return SupportPlan(graph, np.asarray(feet, dtype=float).reshape(-1, 2), solids, diagnostics, metrics), raft


def _emit_tree_supports(jobs, solids, pillars, feet, foot_radii, field, settings, *, cancel=None, graph=None):
    """Replace nearby vertical shafts with one trunk and branches.

    Clusters whose trunk column is not free, or whose branches collide, keep
    independent pillars. Tips are already emitted; this only owns the shafts.
    """
    cancel = cancel or CancellationToken()
    support = settings['support']
    if not jobs or not support.get('tree_supports'):
        for job in jobs:
            solids.append(cylinder_between((job['x'], job['y'], job['middle_z']),
                                           (job['x'], job['y'], job['base_z']), job['run_r']))
            pillars.append((job['x'], job['y'], job['base_z'], job['run_r']))
            feet.append((job['x'], job['y']))
            foot_radii.append(job['run_r'])
        return {'enabled': bool(support.get('tree_supports')), 'clusters': 0,
                'trunks': 0, 'contacts_in_trees': 0, 'kept_independent': len(jobs)}
    spacing = float(support['spacing_mm'])
    radius = float(support['tree_cluster_mm']) or 2 * spacing
    points = np.array([[job['x'], job['y']] for job in jobs], dtype=float)
    from scipy.spatial import cKDTree
    tree = cKDTree(points)
    assigned = np.full(len(jobs), -1, dtype=int)
    clusters = []
    for index in range(len(jobs)):
        cancel.check()
        if assigned[index] >= 0:
            continue
        members = [int(i) for i in tree.query_ball_point(points[index], radius)]
        label = len(clusters)
        for member in members:
            if assigned[member] < 0:
                assigned[member] = label
        clusters.append([member for member in members if assigned[member] == label])
    trunks = independent = 0
    contacts_in_trees = 0
    clearance_mm = float(support['support_clearance_mm'])
    branch_tangent = math.tan(math.radians(float(support['pillar_angle_deg'])))
    for members in clusters:
        group = [jobs[i] for i in members]
        if len(group) < 2:
            job = group[0]
            solids.append(cylinder_between((job['x'], job['y'], job['middle_z']),
                                           (job['x'], job['y'], job['base_z']), job['run_r']))
            pillars.append((job['x'], job['y'], job['base_z'], job['run_r']))
            feet.append((job['x'], job['y']))
            foot_radii.append(job['run_r'])
            independent += 1
            continue
        tx, ty = float(np.mean([job['x'] for job in group])), float(np.mean([job['y'] for job in group]))
        # Leave enough rise for every branch to meet the configured angle.
        trunk_top = min(job['base_z'] - math.hypot(job['x'] - tx, job['y'] - ty)
                        * branch_tangent for job in group)
        run_r = max(job['run_r'] for job in group)
        column = field.index_of(tx, ty)
        free = (trunk_top > run_r and column is not None
                and _brace_clear(field, (tx, ty, 0.0), (tx, ty, trunk_top),
                                 run_r, clearance_mm, cancel))
        branches_clear = free
        if free:
            for job in group:
                if not _brace_clear(field, (tx, ty, trunk_top),
                                    (job['x'], job['y'], job['base_z']),
                                    job['run_r'], clearance_mm, cancel):
                    branches_clear = False
                    break
        if not branches_clear:
            for job in group:
                solids.append(cylinder_between((job['x'], job['y'], job['middle_z']),
                                               (job['x'], job['y'], job['base_z']), job['run_r']))
                pillars.append((job['x'], job['y'], job['base_z'], job['run_r']))
                feet.append((job['x'], job['y']))
                foot_radii.append(job['run_r'])
                independent += 1
            continue
        solids.append(cylinder_between((tx, ty, 0.0), (tx, ty, trunk_top), run_r))
        pillars.append((tx, ty, trunk_top, run_r))
        feet.append((tx, ty))
        foot_radii.append(run_r)
        from .support_segments import elbow_sphere
        solids.append(elbow_sphere((tx, ty, trunk_top), run_r))
        for job in group:
            if math.hypot(job['x'] - tx, job['y'] - ty) > 1e-9:
                solids.append(cylinder_between((tx, ty, trunk_top),
                                               (job['x'], job['y'], job['base_z']), job['run_r']))
            elif job['base_z'] - trunk_top > 1e-9:
                solids.append(cylinder_between((tx, ty, trunk_top),
                                               (tx, ty, job['base_z']), job['run_r']))
        from .support_segments import shoulder_joint
        for job in group:
            solids.append(shoulder_joint((job['x'], job['y'], job['base_z']), job['run_r'],
                job.get('tip_radius', min(job['run_r'], float(support['contact_diameter_mm']) / 2)),
                job.get('tip_span', float(support['tip_length_mm']))))
        if graph is not None:
            old_feet = {f"foot{job['order']}" for job in group}
            graph.nodes[:] = [node for node in graph.nodes if node.id not in old_feet]
            graph.edges[:] = [edge for edge in graph.edges if edge.start not in old_feet]
            foot_id, joint_id = f'tree_foot{trunks}', f'tree_joint{trunks}'
            graph.nodes.extend([SupportNode(foot_id, [tx, ty, 0.0], 'foot'),
                                SupportNode(joint_id, [tx, ty, trunk_top], 'junction')])
            graph.edges.append(SupportEdge(foot_id, joint_id, run_r, 'tree_trunk'))
            for job in group:
                graph.edges.append(SupportEdge(joint_id, f"joint{job['order']}",
                                               job['run_r'], 'tree_branch'))
        trunks += 1
        contacts_in_trees += len(group)
    return {'enabled': True, 'clusters': len(clusters), 'trunks': trunks,
            'contacts_in_trees': contacts_in_trees, 'kept_independent': independent}


def brace_geometry(settings, pillar_radius):
    """Vertical shoulder-to-shoulder interval and maximum actual branch length."""
    del pillar_radius
    support = settings['support']
    return float(support['brace_spacing_mm']), float(support['brace_max_length_mm'])


def plan_supports(triangles, bounds, settings, *, field=None, budget=None, cancel=None,
                  progress=no_progress, extra_contacts=(), removed_contacts=(),
                  branch_attempts=8, max_diagnostics=256, contact_parameters=(),
                  paint=None, object_groups=None):
    """Detect, route and size supports. Returns ``(SupportPlan, raft_or_None)``."""
    cancel = cancel or CancellationToken()
    budget = budget or ResourceBudget(**settings['resources'])
    if field is None:
        field = build_column_field(triangles, bounds, settings, budget=budget, cancel=cancel,
                                   progress=progress)
    contacts, selection = select_contacts(triangles, field, settings, cancel=cancel, progress=progress,
                                          extra_contacts=extra_contacts,
                                          removed_contacts=removed_contacts, paint=paint,
                                          object_groups=object_groups)
    records = list(contact_parameters)
    extra_records = selection.pop('object_contact_parameters', []) or []
    selection['object_override_contacts'] = len(extra_records)
    if extra_records:
        from .contact_parameters import contact_key, normalize_contact_parameters
        seen = {contact_key(row['position_mm']) for row in records}
        merged = list(records)
        for row in extra_records:
            key = contact_key(row['position_mm'])
            if key not in seen:
                merged.append(row)
                seen.add(key)
        records = normalize_contact_parameters(merged, settings)
    plan, raft = route_contacts(contacts, field, settings, cancel=cancel,
                                branch_attempts=branch_attempts, max_diagnostics=max_diagnostics,
                                contact_parameters=records,
                                density_exempt=selection.get('density_exempt_positions') or ())
    plan.metrics.update(selection)
    if settings['support']['drop_attached_unroutable']:
        protected = [tuple(point) for point in extra_contacts]
        protected.extend((*island['position_mm'], island['z_mm']) for island in field.islands)
        apply_attached_unroutable_drops(plan, triangles, settings, protected=protected,
                                        cancel=cancel)
    return plan, raft


def attached_below(triangles, contacts, settings, *, cancel=None):
    """Printer-pitch 3x3 occupancy one layer below each contact.

    The router uses a coarser analysis grid, so a sample on a near-vertical wall
    can sit just outside the occupied cell and still rest on material that the
    layer analysis will treat as connected. This asks the same question on the
    printer lattice ``raster_connectivity`` uses. Measurement, then a drop
    decision in ``apply_attached_unroutable_drops``; it does not emit geometry.
    """
    from . import _native

    cancel = cancel or CancellationToken()
    contacts = np.asarray(list(contacts), dtype=float).reshape(-1, 3)
    height = float(settings['process']['layer_height_mm'])
    if not len(contacts):
        return []
    from .geometry import triangle_bounds
    bounds = triangle_bounds(np.asarray(triangles))
    grid = RasterGrid.for_bounds(bounds, settings, crop=True)
    order = sorted(range(len(contacts)), key=lambda i: contacts[i][2])
    raster = _native.Rasterizer(np.asarray(triangles, dtype=np.float32), cancel.check)
    results = [None] * len(contacts)
    cache_index, cache_mask = None, None
    for position in order:
        cancel.check()
        x, y, z = (float(v) for v in contacts[position])
        index = max(0, int(math.floor(z / height)))
        below = index - 1
        if below < 0:
            results[position] = False
            continue
        if cache_index != below:
            cache_mask = raster.slice((below + 0.5) * height, grid.width, grid.height,
                                      grid.x0, grid.y0, grid.dx, grid.dy,
                                      cancel.check, 'nonzero')['mask']
            cache_index = below
        col = int(math.floor((x - grid.x0) / grid.dx))
        row = int(math.floor((y - grid.y0) / grid.dy))
        if not (0 <= row < grid.height and 0 <= col < grid.width):
            results[position] = False
            continue
        r0, r1 = max(0, row - 1), min(grid.height, row + 2)
        c0, c1 = max(0, col - 1), min(grid.width, col + 2)
        results[position] = bool(cache_mask[r0:r1, c0:c1].any())
    return results


def apply_attached_unroutable_drops(plan, triangles, settings, *, protected=(), cancel=None):
    """Reclassify unroutable contacts that already rest on printed material.

    Island births and manual/correction contacts in ``protected`` stay failed.
    True free overhangs stay failed. Coverage is left against the original
    selection so attached wall samples do not become uncovered by the drop.
    """
    positions = list(plan.metrics.get('unroutable_positions') or [])
    if not positions:
        plan.metrics['contacts_dropped_attached'] = 0
        return plan
    protected_keys = {contact_key(point) for point in protected}
    attached = attached_below(triangles, positions, settings, cancel=cancel)
    dropped_keys = set()
    kept = []
    for point, is_attached in zip(positions, attached):
        key = contact_key(point)
        if is_attached and key not in protected_keys:
            dropped_keys.add(key)
        else:
            kept.append(point)
    if not dropped_keys:
        plan.metrics['contacts_dropped_attached'] = 0
        return plan
    plan.metrics['unroutable_positions'] = kept
    plan.metrics['contacts_failed'] = int(plan.metrics.get('contacts_failed', 0)) - len(dropped_keys)
    plan.metrics['contacts_dropped_attached'] = len(dropped_keys)
    rewritten = []
    for diagnostic in plan.diagnostics:
        if (diagnostic.code == 'support_unroutable'
                and contact_key(diagnostic.position_mm) in dropped_keys):
            rewritten.append(Diagnostic(
                'support_dropped_attached',
                'Unroutable contact already has material one printer layer below; no pillar emitted',
                severity='warning', position_mm=list(diagnostic.position_mm),
                details={'basis': '3x3 printer-pitch neighbourhood one layer below',
                         'reason': 'near-vertical or already-attached surface'}))
        else:
            rewritten.append(diagnostic)
    for key in dropped_keys:
        if not any(d.code == 'support_dropped_attached' and contact_key(d.position_mm) == key
                   for d in rewritten):
            rewritten.append(Diagnostic(
                'support_dropped_attached',
                'Unroutable contact already has material one printer layer below; no pillar emitted',
                severity='warning', position_mm=list(key),
                details={'basis': '3x3 printer-pitch neighbourhood one layer below'}))
    plan.diagnostics[:] = rewritten
    plan.graph.diagnostics = [d.__dict__ for d in plan.diagnostics]
    return plan


def _brace_clear(field, start, end, radius, clearance, cancel=None, exclude=None):
    """Conservative capsule clearance against occupied column-grid cells.

    Examine every XY cell intersecting the strut plus clearance and every Z
    layer its section spans. This uses the existing analysis lattice, whose
    sub-grid limitations remain; it is not a mechanical strength proof.
    ``exclude`` is an optional list of ``(center, radius)`` spheres for the
    intended tip/anchor volumes, so a real contact is not rejected.
    """
    reach = float(radius) + float(clearance)
    cancel = cancel or CancellationToken()
    grid = field.grid
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    x0, x1 = min(start[0], end[0]) - reach, max(start[0], end[0]) + reach
    y0, y1 = min(start[1], end[1]) - reach, max(start[1], end[1]) + reach
    c0 = max(0, int(math.floor((x0 - grid.x0) / grid.dx)))
    c1 = min(grid.width, int(math.ceil((x1 - grid.x0) / grid.dx)))
    r0 = max(0, int(math.floor((y0 - grid.y0) / grid.dy)))
    r1 = min(grid.height, int(math.ceil((y1 - grid.y0) / grid.dy)))
    direction = end[:2] - start[:2]
    length2 = float(direction @ direction)
    allowance = reach + math.hypot(grid.dx, grid.dy) / 2
    for row in range(r0, r1):
        cancel.check()
        for col in range(c0, c1):
            point = np.array([grid.x0 + (col + .5) * grid.dx,
                              grid.y0 + (row + .5) * grid.dy])
            if length2 <= 1e-20:
                if np.linalg.norm(point - start[:2]) > allowance:
                    continue
                t0, t1 = 0.0, 1.0
            else:
                t = float((point - start[:2]) @ direction / length2)
                distance2 = float(np.sum((point - (start[:2] + t * direction)) ** 2))
                if distance2 > allowance ** 2:
                    continue
                span = math.sqrt(max(0.0, allowance ** 2 - distance2) / length2)
                t0, t1 = max(0.0, t - span), min(1.0, t + span)
                if t0 > t1:
                    continue
            z_a = start[2] + t0 * (end[2] - start[2])
            z_b = start[2] + t1 * (end[2] - start[2])
            z_lo, z_hi = min(z_a, z_b) - reach, max(z_a, z_b) + reach
            if _cell_in_exclude(point[0], point[1], z_lo, z_hi, exclude):
                continue
            lo = max(0, field.layer_of(z_lo))
            hi = field.layer_of(z_hi) + 1
            if hi > lo:
                if field.blocked(row * grid.width + col, lo, hi):
                    return False
    return True


def _cone_intersections(origin, start, end, tangent=1.0):
    """Points on a segment meeting a cone with rise/run equal to tangent."""
    offset, direction = start - origin, end - start
    metric = np.array([tangent * tangent, tangent * tangent, -1.])
    a = float((direction * metric) @ direction)
    b = float(2 * (offset * metric) @ direction)
    c = float((offset * metric) @ offset)
    if abs(a) < 1e-12:
        roots = [-c / b] if abs(b) > 1e-12 else ([0., 1.] if abs(c) < 1e-10 else [])
    else:
        discriminant = b * b - 4 * a * c
        if discriminant < -1e-10:
            return []
        root = math.sqrt(max(0., discriminant))
        roots = [(-b - root) / (2 * a), (-b + root) / (2 * a)]
    points = []
    for t in roots:
        if -1e-9 <= t <= 1 + 1e-9:
            point = start + min(1., max(0., t)) * direction
            if origin[2] - point[2] > 1e-6:
                points.append(point)
    return points


def _brace_base_clear(field, bounds, clearance, cancel):
    """Conservatively reject model material in a new base's full envelope."""
    low, high = bounds[0] - clearance, bounds[1] + clearance
    grid = field.grid
    c0 = max(0, int(math.floor((low[0] - grid.x0) / grid.dx)))
    c1 = min(grid.width, int(math.ceil((high[0] - grid.x0) / grid.dx)))
    r0 = max(0, int(math.floor((low[1] - grid.y0) / grid.dy)))
    r1 = min(grid.height, int(math.ceil((high[1] - grid.y0) / grid.dy)))
    z0, z1 = max(0, field.layer_of(low[2])), field.layer_of(high[2]) + 1
    for row in range(r0, r1):
        cancel.check()
        for col in range(c0, c1):
            if field.blocked(row * grid.width + col, z0, z1):
                return False
    return True


def _brace(pillars, settings, solids, limit=20000, *, field=None, evidence=None,
           cancel=None, graph=None, feet=None, foot_radii=None):
    """Grow configurable grounded branches downward, preserving the primary shafts.

    Only shaft edges reachable from plate feet without passing through a model
    contact are admitted. Every accepted branch joins that grounded network or
    adds a checked plate foot. Origin and destination junctions split graph
    edges, so graph connectivity describes the emitted solids.
    """
    import heapq
    support = settings['support']
    cancel = cancel or CancellationToken()
    cancel.check()
    evidence = evidence if evidence is not None else {}
    for key in ('collision_rejected', 'bounds_rejected', 'foot_rejected',
                'length_rejected', 'spacing_rejected', 'ungrounded_rejected',
                'no_destination_rejected', 'pattern_rejected', 'duplicate_rejected',
                'examined', 'origins_examined', 'new_feet'):
        evidence.setdefault(key, 0)
    evidence.setdefault('capped', False)
    radius = float(support['pillar_diameter_mm']) / 2
    interval, max_length = brace_geometry(settings, radius)
    if interval <= 0 or max_length <= 0:
        return 0
    max_distance = float(support['brace_max_distance_mm']) or float(support['spacing_mm']) * 1.5
    destination_mode = support['brace_destination']
    pattern = support['brace_pattern']
    quota = support['brace_branches_per_node']
    tangent = math.tan(math.radians(support['brace_angle_deg']))
    azimuth = math.radians(support['brace_azimuth_deg'])
    minimum_height = support['brace_min_height_mm']
    connections = []
    used_rays = {}
    clearance = float(support['support_clearance_mm'])
    build = np.asarray(settings['printer']['build_mm'], dtype=float)
    margin = float(settings['printer']['edge_clearance_mm'])
    lower = np.array([-build[0] / 2 + margin, -build[1] / 2 + margin, 0.])
    upper = np.array([build[0] / 2 - margin, build[1] / 2 - margin, build[2]])
    feet = feet if feet is not None else []
    foot_radii = foot_radii if foot_radii is not None else []
    if graph is None:
        graph = SupportGraph()
        for index, pillar in enumerate(pillars):
            x, y, height = pillar[:3]
            r = pillar[3] if len(pillar) > 3 else radius
            graph.nodes.extend([SupportNode(f'bf{index}', [x, y, 0.], 'foot'),
                                SupportNode(f'bj{index}', [x, y, height], 'junction')])
            graph.edges.append(SupportEdge(f'bf{index}', f'bj{index}', r))
    nodes = {node.id: node for node in graph.nodes}
    # A part contact is never an edge in the grounding walk, even when primary
    # part-to-part routing is enabled. This also excludes disconnected shafts.
    shaft_edges = [edge for edge in graph.edges
                   if edge.kind not in ('tip', 'bottom', 'small_model', 'model_anchor')
                   and nodes[edge.start].kind not in ('contact', 'model_anchor')
                   and nodes[edge.end].kind not in ('contact', 'model_anchor')]
    grounded = {node.id for node in graph.nodes
                if node.kind == 'foot' and abs(node.position_mm[2]) <= 1e-8}
    remaining = list(shaft_edges)
    admitted = []
    while remaining:
        cancel.check()
        found = [edge for edge in remaining if edge.start in grounded or edge.end in grounded]
        if not found:
            break
        for edge in found:
            grounded.update((edge.start, edge.end))
            admitted.append(edge)
            remaining.remove(edge)
    evidence['ungrounded_rejected'] += len(remaining) + sum(
        edge.kind in ('model_anchor', 'small_model') for edge in graph.edges)
    segments, origins, connected = [], [], {}
    # The two edges of a routed elbow are one primary support. Carry its
    # shoulder schedule and shared-connection interval through that elbow.
    owners = list(range(len(admitted)))
    for index, edge in enumerate(admitted):
        if edge.kind != 'branched':
            continue
        for previous, other in enumerate(admitted[:index]):
            if other.kind == 'branched' and {edge.start, edge.end} & {other.start, other.end}:
                owners[index] = owners[previous]
                break
    shoulders = {}
    for owner, edge in zip(owners, admitted):
        shoulders[owner] = max(shoulders.get(owner, 0.), nodes[edge.start].position_mm[2],
                               nodes[edge.end].position_mm[2])
    for owner, edge in zip(owners, admitted):
        start, end = (np.asarray(nodes[name].position_mm, dtype=float)
                      for name in (edge.start, edge.end))
        if end[2] < start[2]:
            start, end = end, start
        if end[2] - start[2] <= 1e-8:
            continue
        segments.append({'start': start, 'end': end, 'edge': edge, 'owners': {owner}})
        connected.setdefault(owner, [])
        top = shoulders[owner]
        if top - interval >= top:
            raise VoxelMillError('invalid_support', 'Brace spacing is too small to advance the height')
        level = top - max(0, math.ceil((top - end[2] - 1e-8) / interval)) * interval
        if level <= start[2] + 1e-6:
            continue
        point = start + (end - start) * ((level - start[2]) / (end[2] - start[2]))
        heapq.heappush(origins, (-float(level), float(point[0]), float(point[1]), owner,
                                tuple(start), tuple(end), float(edge.radius_mm)))
    added = 0

    def close_interval(owners, z):
        def same_interval(owner, previous):
            if pattern == 'single':
                return abs(z - previous) < interval - 1e-8
            # Alternating and X patterns share shoulder-derived level bands.
            # Counting an incoming lower end against the next level would
            # suppress every second alternating diagonal.
            band = lambda height: math.floor((shoulders[owner] - height + 1e-8) / interval)
            return band(z) == band(previous)
        return any(sum(any(same_interval(owner, height) for height in previous)
                       for previous in connected.get(owner, ())) >= quota for owner in owners)

    def attempt():
        cancel.check()
        if evidence['examined'] >= limit:
            evidence['capped'] = True
            return False
        evidence['examined'] += 1
        return True

    def inside(start, end, r):
        # Conservative capsule envelope includes every cylinder vertex.
        return bool(np.all(np.minimum(start, end) - r >= lower - 1e-8)
                    and np.all(np.maximum(start, end) + r <= upper + 1e-8))

    def split(segment, point):
        edge = segment['edge']
        for name in (edge.start, edge.end):
            if np.linalg.norm(point - nodes[name].position_mm) < 1e-7:
                return name
        name = f'brace_joint{len(nodes)}'
        node = SupportNode(name, point.tolist(), 'brace_junction')
        nodes[name] = node
        graph.nodes.append(node)
        graph.edges.remove(edge)
        first = SupportEdge(edge.start, name, edge.radius_mm, edge.kind)
        second = SupportEdge(name, edge.end, edge.radius_mm, edge.kind)
        graph.edges.extend((first, second))
        segments.pop(next(index for index, item in enumerate(segments) if item is segment))
        for replacement in (first, second):
            segments.append({'start': np.asarray(nodes[replacement.start].position_mm),
                             'end': np.asarray(nodes[replacement.end].position_mm),
                             'edge': replacement, 'owners': segment['owners']})
        return name

    def segment_at(owner, point):
        for segment in segments:
            if owner not in segment['owners'] or segment['edge'].kind == 'brace':
                continue
            a, b = segment['start'], segment['end']
            if abs(np.linalg.norm(point - a) + np.linalg.norm(point - b) - np.linalg.norm(b - a)) < 1e-6:
                return segment
        return None

    def duplicate(owner, target_owners, z, destination_z):
        return any(((owner in first and target_owners & second
                     and abs(z - high) < interval - 1e-8)
                    or (owner in second and target_owners & first
                        and abs(destination_z - high) < interval - 1e-8))
                   for first, second, high in connections)

    def allowed_direction(origin, destination, level):
        if pattern != 'alternating':
            return True
        delta = destination[:2] - origin[:2]
        rotated = np.array([math.cos(azimuth), math.sin(azimuth)])
        forward = float(delta @ rotated)
        if abs(forward) < 1e-8:
            forward = float(delta @ np.array([-rotated[1], rotated[0]]))
        return forward * (-1 if level % 2 else 1) > 1e-8

    def reciprocal(origin, destination, owner, target):
        # Paired Xs need two complete, parallel vertical shaft spans. Tree or
        # elbow spans that do not have this geometry are rejected explicitly.
        if target['edge'].kind == 'brace' or len(target['owners']) != 1:
            return None
        target_owner = next(iter(target['owners']))
        high = np.array([*destination[:2], origin[2]])
        low = np.array([*origin[:2], destination[2]])
        for who, a, b in ((owner, origin, low), (target_owner, high, destination)):
            midpoint = (a + b) / 2
            if any(segment_at(who, point) is None for point in (a, midpoint, b)):
                return None
        # A reciprocal diagonal cannot bypass a different grounded shaft.
        distance = float(np.linalg.norm(low - high))
        direction = (low - high) / distance
        for segment in segments:
            if segment['owners'] & {owner, target_owner}:
                continue
            for point in _cone_intersections(high, segment['start'], segment['end'], tangent):
                delta = point - high
                if (np.linalg.norm(delta) < distance - 1e-7
                        and np.linalg.norm(delta / np.linalg.norm(delta) - direction) < 1e-7):
                    return None
        return high, low, target_owner

    while origins:
        cancel.check()
        if evidence['origins_examined'] >= limit:
            evidence['capped'] = True
            return added
        evidence['origins_examined'] += 1
        negative_z, x, y, owner, lo_tuple, hi_tuple, shaft_r = heapq.heappop(origins)
        z = -negative_z
        lo, hi = np.asarray(lo_tuple), np.asarray(hi_tuple)
        origin = lo + (hi - lo) * ((z - lo[2]) / (hi[2] - lo[2]))
        next_z = z - interval
        if next_z > lo[2] + 1e-6:
            next_point = lo + (hi - lo) * ((next_z - lo[2]) / (hi[2] - lo[2]))
            heapq.heappush(origins, (-next_z, float(next_point[0]), float(next_point[1]),
                                    owner, lo_tuple, hi_tuple, shaft_r))
        if z < minimum_height - 1e-8:
            continue
        level = round((shoulders[owner] - z) / interval)
        if close_interval({owner}, z):
            evidence['spacing_rejected'] += 1
            continue
        for _ in range(quota):
            if close_interval({owner}, z):
                break
            strut_r = float(support['brace_diameter_mm']) / 2 or shaft_r * .5
            candidates = []
            ray_key = (owner, tuple(origin))
            previous_rays = used_rays.setdefault(ray_key, [])
            for index, segment in enumerate(segments):
                if index % 64 == 0:
                    cancel.check()
                if destination_mode == 'base' or owner in segment['owners']:
                    continue
                a, b = segment['start'], segment['end']
                if np.linalg.norm(np.maximum(np.maximum(np.minimum(a[:2], b[:2]) - origin[:2],
                                                       origin[:2] - np.maximum(a[:2], b[:2])), 0)) > max_distance:
                    continue
                for point in _cone_intersections(origin, a, b, tangent):
                    distance = float(np.linalg.norm(point - origin))
                    if np.linalg.norm(point[:2] - origin[:2]) <= max_distance + 1e-8:
                        candidates.append((distance, index, tuple(point)))
            # Keep the first centerline crossing on each downward ray, even when
            # its spacing interval is occupied: continuing through it would add an
            # unrecorded junction and bypass shared-connection suppression.
            rays = []
            first_candidates = []
            for candidate in sorted(candidates):
                delta = np.asarray(candidate[2]) - origin
                direction = delta / candidate[0]
                if any(np.linalg.norm(direction - ray) < 1e-7 for ray in rays):
                    continue
                rays.append(direction)
                first_candidates.append(candidate)
            accepted = None
            reverse = None
            for distance, index, destination in first_candidates:
                if not attempt():
                    return added
                segment = segments[index]
                destination = np.asarray(destination)
                if not allowed_direction(origin, destination, level):
                    evidence['pattern_rejected'] += 1
                    continue
                direction = (destination - origin) / distance
                if (any(np.linalg.norm(direction - ray) < 1e-7 for ray in previous_rays)
                        or duplicate(owner, segment['owners'], z, destination[2])):
                    evidence['duplicate_rejected'] += 1
                    continue
                strut_r = (float(support['brace_diameter_mm']) / 2
                           or min(shaft_r, segment['edge'].radius_mm) * .5)
                if distance > max_length + 1e-8:
                    evidence['length_rejected'] += 1
                    continue
                if close_interval(segment['owners'], destination[2]):
                    evidence['spacing_rejected'] += 1
                    continue
                destination = np.asarray(destination)
                if not inside(origin, destination, strut_r):
                    evidence['bounds_rejected'] += 1
                    continue
                if field is not None and not _brace_clear(field, origin, destination, strut_r, clearance, cancel):
                    evidence['collision_rejected'] += 1
                    continue
                if pattern == 'x':
                    if not attempt():
                        return added
                    reverse = reciprocal(origin, destination, owner, segment)
                    if reverse is None:
                        evidence['pattern_rejected'] += 1
                        continue
                    high, low, target_owner = reverse
                    if close_interval({target_owner}, high[2]) or close_interval({owner}, low[2]):
                        evidence['spacing_rejected'] += 1
                        continue
                    if not inside(high, low, strut_r):
                        evidence['bounds_rejected'] += 1
                        continue
                    if field is not None and not _brace_clear(field, high, low, strut_r, clearance, cancel):
                        evidence['collision_rejected'] += 1
                        continue
                accepted = (destination, segment)
                break
            if accepted is None and destination_mode != 'supports':
                reverse = None
                # A short vertical landing stem keeps the entire diagonal cylinder
                # above Z=0, including its tilted end disc. It joins the configured
                # base and is also valid with bare feet (base_type=none).
                strut_r = float(support['brace_diameter_mm']) / 2 or shaft_r * .5
                landing_z = strut_r
                drop = z - landing_z
                travel = drop / tangent
                if drop > 1e-6 and math.hypot(travel, drop) <= max_length + 1e-8:
                    for spoke in (0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15):
                        angle = azimuth + spoke * (2 * math.pi / 16)
                        if not attempt():
                            return added
                        destination = np.array([origin[0] + travel * math.cos(angle),
                                                origin[1] + travel * math.sin(angle), landing_z])
                        direction = (destination - origin) / np.linalg.norm(destination - origin)
                        if any(np.linalg.norm(direction - ray) < 1e-7 for ray in previous_rays):
                            evidence['duplicate_rejected'] += 1
                            continue
                        if not allowed_direction(origin, destination, level):
                            evidence['pattern_rejected'] += 1
                            continue
                        # Existing intersections already failed their policy or
                        # clearance checks above. A new foot must not pass through
                        # one of them and silently make an extra connection.
                        crossing = False
                        for segment in segments:
                            if owner in segment['owners']:
                                continue
                            for point in _cone_intersections(origin, segment['start'], segment['end'], tangent):
                                delta = point - origin
                                if (np.linalg.norm(delta) < np.linalg.norm(destination - origin) - 1e-7
                                        and np.linalg.norm(delta / np.linalg.norm(delta) - direction) < 1e-7):
                                    crossing = True
                                    break
                            if crossing:
                                break
                        if crossing:
                            evidence['spacing_rejected'] += 1
                            continue
                        if not inside(origin, destination, strut_r):
                            evidence['bounds_rejected'] += 1
                            continue
                        bottom = np.array([*destination[:2], 0.])
                        pad = build_base([destination[:2]], settings, radius,
                                         foot_radii=[strut_r], cancel=cancel)['solid']
                        if pad is not None:
                            bounds = np.asarray(pad.bounding_box()).reshape(2, 3)
                            if np.any(bounds[0] < lower - 1e-8) or np.any(bounds[1] > upper + 1e-8):
                                evidence['foot_rejected'] += 1
                                continue
                            if field is not None and not _brace_base_clear(field, bounds, clearance, cancel):
                                evidence['foot_rejected'] += 1
                                continue
                        if field is not None and (not _brace_clear(field, origin, destination, strut_r, clearance, cancel)
                                or not _brace_clear(field, bottom, destination, strut_r, clearance, cancel)):
                            evidence['collision_rejected'] += 1
                            continue
                        accepted = (destination, None)
                        break
                elif drop > 1e-6:
                    evidence['length_rejected'] += 1
            if accepted is None:
                evidence['no_destination_rejected'] += 1
                break
            destination, target = accepted
            origin_segment = segment_at(owner, origin)
            if origin_segment is None:
                evidence['ungrounded_rejected'] += 1
                break
            origin_id = split(origin_segment, origin)
            if target is None:
                foot_id, destination_id = f'brace_foot{added}', f'brace_landing{added}'
                bottom = [*destination[:2], 0.]
                for node in (SupportNode(foot_id, bottom, 'foot'),
                             SupportNode(destination_id, destination.tolist(), 'brace_junction')):
                    graph.nodes.append(node)
                    nodes[node.id] = node
                stem = SupportEdge(foot_id, destination_id, strut_r, 'brace_foot')
                graph.edges.append(stem)
                solids.append(cylinder_between(bottom, destination, strut_r))
                feet.append(tuple(destination[:2]))
                foot_radii.append(strut_r)
                evidence['new_feet'] += 1
                owners = {owner}
                segments.append({'start': np.asarray(bottom), 'end': destination,
                                 'edge': stem, 'owners': owners})
            else:
                connections.append(({owner}, set(target['owners']), z))
                destination_id = split(target, destination)
                owners = {owner} | target['owners']
                for target_owner in target['owners']:
                    connected[target_owner].append((float(destination[2]),))
            connected[owner].append((z,))
            edge = SupportEdge(origin_id, destination_id, strut_r, 'brace')
            graph.edges.append(edge)
            segments.append({'start': origin, 'end': destination, 'edge': edge, 'owners': owners})
            solids.append(cylinder_between(origin, destination, strut_r))
            previous_rays.append((destination - origin) / np.linalg.norm(destination - origin))
            added += 1
            if reverse is not None:
                high, low, target_owner = reverse
                # Both diagonals are checked before changing geometry. The crossing
                # is a shared graph junction, not two unrecorded overlapping edges.
                center = (origin + destination) / 2
                center_id = split(segments[-1], center)
                high_id = split(segment_at(target_owner, high), high)
                low_id = split(segment_at(owner, low), low)
                for first, second in ((high_id, center_id), (center_id, low_id)):
                    cross_edge = SupportEdge(first, second, strut_r, 'brace')
                    graph.edges.append(cross_edge)
                    segments.append({'start': np.asarray(nodes[first].position_mm),
                                     'end': np.asarray(nodes[second].position_mm),
                                     'edge': cross_edge, 'owners': owners})
                solids.append(cylinder_between(high, low, strut_r))
                # An X is one connection group per neighbour and interval. Use the
                # upper endpoints for scheduling both supports consistently.
                connected[target_owner][-1] = (z, float(destination[2]))
                connected[owner][-1] = (z, float(low[2]))
                added += 1
    return added


def apply_support_validation(report, plan, settings):
    """Carry routing evidence into export decisions, including capped failures.

    Slenderness and brace evidence remain mechanical heuristics. Failed requested
    routes are blockers even if raster overlap happens to connect the whole part.
    """
    report.diagnostics.extend(plan.diagnostics)
    failed = plan.metrics.get('contacts_failed', 0)
    sealed = plan.metrics.get('contacts_in_sealed_cavities', 0)
    report.checks['support_routes'] = 'fail' if failed or sealed else 'pass'
    report.metrics['supports'] = plan.metrics
    if failed or sealed:
        report.diagnostics.append(Diagnostic(
            'incomplete_support_routes', 'Some requested contacts could not be routed or removed',
            details={'unroutable': failed, 'sealed': sealed}))
    slender = any(d.code == 'support_slenderness' for d in plan.diagnostics)
    report.checks['support_slenderness'] = 'warn' if slender else 'pass'
    coverage = plan.metrics.get('coverage', 'not_run')
    report.checks['support_coverage'] = coverage
    if coverage == 'fail':
        report.diagnostics.append(Diagnostic(
            'support_coverage', 'Downward faces lie farther from a contact than the configured gap',
            details={key: plan.metrics.get(key) for key in (
                'uncovered_samples', 'uncovered_fraction', 'max_contact_gap_mm',
                'coverage_gap_limit_mm', 'coverage_basis')}))
    load = plan.metrics.get('anchor_load', 'not_run')
    # A load limit is a geometric share of area, not a strength result, so an
    # exceedance is a warning until the pillar mechanics are calibrated.
    report.checks['support_anchor_load'] = 'warn' if load == 'fail' else load
    if load == 'fail':
        report.diagnostics.append(Diagnostic(
            'support_anchor_load', 'A contact carries more downward area than the configured limit',
            severity='warning',
            details={key: plan.metrics.get(key) for key in (
                'overloaded_contacts', 'max_contact_load_mm2_actual', 'contact_load_limit_mm2')}))
    return report
