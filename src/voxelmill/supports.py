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
from .collisions import segment_distances

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
    #: Shaft capsules already emitted in this routing pass (a
    #: :class:`CapsuleIndex`). Later candidates that intersect one (except
    #: at an endpoint / planned brace joint) are skipped or rerouted.
    occupied_capsules: 'CapsuleIndex' = field(default_factory=lambda: CapsuleIndex())
    #: Contact tips routed in this pass, junction to contact at the contact
    #: radius: the capsule the collision audit tests a tip edge with.
    tip_capsules: 'CapsuleIndex' = field(default_factory=lambda: CapsuleIndex())

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


#: Largest margin, in mm, the column field keeps around the part for branches.
BRANCH_MARGIN_CAP_MM = 12.0


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
    # Past the footprint by a branch's whole reach, so a contact on an edge
    # over lower material can still branch out to free plate beside the part.
    # Capped: a coarse spacing would otherwise grow the field quadratically
    # for branches that long routes rarely use.
    support = settings['support']
    reach = min(BRANCH_MARGIN_CAP_MM,
                2 * float(support['spacing_mm']) + float(support['pillar_diameter_mm']) / 2
                + float(support['support_clearance_mm']))
    margin = 2 + int(math.ceil(reach / pitch))
    x0 = bounds[0][0] - margin * pitch
    y0 = bounds[0][1] - margin * pitch
    width = int(math.ceil((bounds[1][0] - bounds[0][0]) / pitch)) + 2 * margin
    height = int(math.ceil((bounds[1][1] - bounds[0][1]) / pitch)) + 2 * margin
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
        # Explicit elementwise sums, not mean() or matmul: those round
        # differently across NumPy/BLAS builds and CPUs, and thinning rounds
        # samples to spacing cells, so a one-ulp change moved contacts.
        points.append((faces[:, 0] + faces[:, 1] + faces[:, 2]) / 3.0)
        # A face wider than the sample pitch needs a lattice of samples, not a
        # centroid: a single flat underside is often one pair of triangles, and
        # sampling it once leaves its whole perimeter unsupported. The pitch is
        # a fraction of the spacing so coverage can be measured, and contacts
        # chosen, to within a small part of a contact's reach.
        points.extend(_face_lattices(faces, sample_pitch_mm(settings), max_lattice_faces, cancel))
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


#: Downward faces are sampled at this fraction of the contact spacing.
SAMPLE_PITCH_FRACTION = 1 / 8
#: Largest lattice (steps per edge) laid on one face.
MAX_FACE_STEPS = 64


def sample_pitch_mm(settings):
    """Distance between downward-face samples for the current spacing."""
    return float(settings['support']['spacing_mm']) * SAMPLE_PITCH_FRACTION


def _face_lattices(faces, pitch, max_faces, cancel):
    """Barycentric lattices on faces whose longest edge exceeds ``pitch``.

    Faces are grouped by step count so each group is one vectorized product.
    Lattice points use explicit weighted sums (see ``downward_contacts``).
    """
    faces = np.asarray(faces, dtype=np.float64).reshape(-1, 3, 3)
    edges = np.stack([np.linalg.norm(faces[:, 1] - faces[:, 0], axis=1),
                      np.linalg.norm(faces[:, 2] - faces[:, 1], axis=1),
                      np.linalg.norm(faces[:, 0] - faces[:, 2], axis=1)], axis=1).max(axis=1)
    steps = np.minimum(MAX_FACE_STEPS, np.ceil(edges / pitch)).astype(np.int64)
    big = np.flatnonzero(steps > 1)[:max_faces]
    out = []
    for count in np.unique(steps[big]):
        cancel.check()
        chosen = faces[big[steps[big] == count]]
        lattice = np.array([(i, j, count - i - j) for i in range(count + 1)
                            for j in range(count - i + 1)], dtype=np.float64) / count
        w0, w1, w2 = lattice[:, 0][None, :, None], lattice[:, 1][None, :, None], lattice[:, 2][None, :, None]
        out.append((w0 * chosen[:, None, 0] + w1 * chosen[:, None, 1]
                    + w2 * chosen[:, None, 2]).reshape(-1, 3))
    return out


def _perimeter_samples(faces, spacing):
    """Samples along edges that belong to only one downward face (the contour).

    Edges are matched on vertices rounded to 1e-5 mm, regardless of direction.
    Samples come out in face order, then edge order within a face, each
    segment walked from its first vertex as the face lists it.
    """
    faces = np.asarray(faces, dtype=np.float64).reshape(-1, 3, 3)
    if not len(faces):
        return np.empty((0, 3))
    starts = faces.reshape(-1, 3)                       # edge i of face f: f*3 + i
    ends = faces[:, [1, 2, 0]].reshape(-1, 3)
    ra, rb = np.round(starts, 5), np.round(ends, 5)
    # Undirected key: the lexicographically smaller rounded endpoint first.
    differs = ra != rb
    first = np.argmax(differs, axis=1)
    rows = np.arange(len(ra))
    swap = differs.any(axis=1) & (ra[rows, first] > rb[rows, first])
    keys = np.where(swap[:, None], np.hstack([rb, ra]), np.hstack([ra, rb]))
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    single = counts[inverse.reshape(-1)] == 1
    if not single.any():
        return np.empty((0, 3))
    return _sample_segments(starts[single], ends[single], spacing)


def _sample_segments(starts, ends, spacing):
    """Points every ``spacing`` or closer along each segment, both ends included, in order."""
    lengths = np.linalg.norm(ends - starts, axis=1)
    steps = np.maximum(1, np.ceil(lengths / max(float(spacing), 1e-9)).astype(np.int64))
    per = steps + 1
    owner = np.repeat(np.arange(len(steps)), per)
    local = np.arange(int(per.sum())) - np.repeat(np.cumsum(per) - per, per)
    # np.linspace(0, 1, n): arange(n) * (1 / (n - 1)), with the last value exactly 1.
    ts = local * (1.0 / steps[owner])
    ts[np.cumsum(per) - 1] = 1.0
    return (1.0 - ts)[:, None] * starts[owner] + ts[:, None] * ends[owner]


def _open_boundary_samples(triangles, spacing, min_z=0.2):
    """Samples along open mesh boundary edges that sit above the plate."""
    from .ops import _directed_boundary_edges
    tris = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
    if not len(tris):
        return np.empty((0, 3))
    rounded = np.round(tris.reshape(-1, 3), 5)
    uniq, inverse = np.unique(rounded, axis=0, return_inverse=True)
    directed, _count = _directed_boundary_edges(inverse.reshape(-1, 3))
    edges = np.asarray(directed, dtype=np.int64).reshape(-1, 2)
    pa, pb = uniq[edges[:, 0]], uniq[edges[:, 1]]
    above = (pa[:, 2] + pb[:, 2]) / 2 >= min_z
    if not above.any():
        return np.empty((0, 3))
    return _sample_segments(pa[above], pb[above], spacing)


def _cells(points, spacing):
    """XY spacing cells. Z is ignored so a tilted face cannot stack a forest."""
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    if not len(pts):
        return np.empty((0, 2), dtype=np.int64)
    return np.rint(pts[:, :2] / spacing).astype(np.int64)


def contact_reach_mm(settings):
    """Farthest a downward point may sit from its nearest contact.

    The smaller of the spacing and the growth-span limit less the tip radius,
    less the sampling pitch's own uncertainty, so a surface point between two
    samples is covered too and the first layer of an overhang never grows
    past ``max_span_mm`` from a tip.
    """
    support = settings['support']
    spacing = float(support['spacing_mm'])
    span = float(support['max_span_mm']) - float(support['contact_diameter_mm']) / 2
    pitch = sample_pitch_mm(settings)
    return max(pitch, min(spacing, span) - pitch * 0.6)


def _hex_nodes(xy, spacing):
    """Nearest node of a hexagonal lattice with ``spacing`` between neighbours."""
    row_pitch = spacing * math.sqrt(3) / 2
    base = np.floor(xy[:, 1] / row_pitch).astype(np.int64)
    best = best_i = best_j = None
    for j in (base - 1, base, base + 1):
        shift = (j & 1) * (spacing / 2)
        i = np.rint((xy[:, 0] - shift) / spacing).astype(np.int64)
        node = np.stack([i * spacing + shift, j * row_pitch], axis=1)
        gap = np.hypot(xy[:, 0] - node[:, 0], xy[:, 1] - node[:, 1])
        if best is None:
            best, best_i, best_j = gap, i, j
        else:
            closer = gap < best
            best = np.where(closer, gap, best)
            best_i = np.where(closer, i, best_i)
            best_j = np.where(closer, j, best_j)
    return best_i, best_j, best


def _thin(candidates, mandatory, spacing, reach=None):
    """Choose automatic contacts from dense samples; mandatory always kept.

    Contacts sit on a hexagonal XY lattice at ``spacing``: in each lattice
    cell and ``spacing``-tall Z band, the sample nearest the node is taken, so
    stacked and sloped surfaces each get their own. Picks nearer than half a
    spacing to an earlier (lower) one are dropped. A repair pass then adds a
    contact at every sample still farther than ``reach`` from all contacts,
    lowest and farthest first, which is what guarantees coverage at rims and
    on features smaller than a lattice cell. Mandatory contacts (raster
    islands, island-guard extras, manual/paint enforcers) bypass the density
    cap and count towards coverage.
    """
    from scipy.spatial import cKDTree
    mandatory = np.asarray(mandatory, dtype=float).reshape(-1, 3)
    candidates = np.asarray(candidates, dtype=float).reshape(-1, 3)
    if not len(candidates):
        return mandatory
    reach = float(spacing if reach is None else reach)
    i, j, gap = _hex_nodes(candidates[:, :2], spacing)
    band = np.floor(candidates[:, 2] / spacing).astype(np.int64)
    # Nearest the node first, then lowest, then input order: a total order,
    # so the choice never depends on sort stability or the platform.
    order = np.lexsort((np.arange(len(candidates)), candidates[:, 2], gap, band, j, i))
    keys = np.stack([i[order], j[order], band[order]], axis=1)
    first = np.ones(len(order), dtype=bool)
    first[1:] = np.any(keys[1:] != keys[:-1], axis=1)
    # A node far from any sample belongs to a neighbouring surface patch's
    # rim; leave that area to the repair pass rather than planting a tip on
    # the very edge.
    picks = order[first & (gap[order] <= spacing * 0.5)]
    picks = picks[np.lexsort((picks, candidates[picks, 2]))]
    chosen = []
    kept = list(mandatory)
    if len(picks):
        tree = cKDTree(candidates[picks])
        neighbours = tree.query_ball_point(candidates[picks], spacing * 0.5)
        blocked = np.zeros(len(picks), dtype=bool)
        if len(mandatory):
            near = cKDTree(mandatory).query(candidates[picks], distance_upper_bound=spacing * 0.5)[0]
            blocked |= np.isfinite(near)
        for index in range(len(picks)):
            if blocked[index]:
                continue
            chosen.append(picks[index])
            for other in neighbours[index]:
                if other > index:
                    blocked[other] = True
    kept.extend(candidates[chosen])
    kept = np.asarray(kept, dtype=float).reshape(-1, 3)
    distance = (cKDTree(kept).query(candidates)[0] if len(kept)
                else np.full(len(candidates), np.inf))
    uncovered = np.flatnonzero(distance > reach)
    added = []
    if len(uncovered):
        pitch = max(reach / 8, 1e-6)
        order = uncovered[np.lexsort((uncovered, -distance[uncovered],
                                      np.floor(candidates[uncovered, 2] / pitch)))]
        cells = {}
        cell = reach
        for index in order:
            point = candidates[index]
            key = tuple(np.floor(point / cell).astype(np.int64))
            hit = False
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for other in cells.get((key[0] + dx, key[1] + dy, key[2] + dz), ()):
                            if np.dot(other - point, other - point) <= reach * reach:
                                hit = True
                                break
                        if hit:
                            break
                    if hit:
                        break
                if hit:
                    break
            if hit:
                continue
            cells.setdefault(key, []).append(point)
            added.append(index)
    automatic = np.concatenate([candidates[np.sort(np.asarray(chosen, dtype=np.int64))],
                                candidates[np.sort(np.asarray(added, dtype=np.int64))]])
    automatic = automatic[np.lexsort((automatic[:, 1], automatic[:, 0], automatic[:, 2]))]
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


class CapsuleIndex:
    """Routed shaft capsules, bucketed by XY cell for overlap queries.

    Each capsule is filed under every cell its XY bounding box, grown by its
    radius, touches. Two capsules can only come within ``r1 + r2`` in 3-D if
    their XY boxes, each grown by its own radius, overlap, so a query that
    reads the cells under the candidate's grown box sees every capsule that
    could possibly hit it. Routing used to scan every capsule for every
    candidate, which is quadratic in the number of contacts.
    """

    def __init__(self, cell_mm=3.0):
        self.cell = max(float(cell_mm), 1e-3)
        self.capsules = []
        self._buckets = {}

    def __len__(self):
        return len(self.capsules)

    def __iter__(self):
        return iter(self.capsules)

    def _cells(self, low, high):
        i0, j0 = (int(math.floor(v / self.cell)) for v in low)
        i1, j1 = (int(math.floor(v / self.cell)) for v in high)
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                yield i, j

    def add(self, start, end, radius):
        start, end = np.asarray(start, dtype=float), np.asarray(end, dtype=float)
        radius = float(radius)
        index = len(self.capsules)
        self.capsules.append((start, end, radius))
        low = np.minimum(start[:2], end[:2]) - radius
        high = np.maximum(start[:2], end[:2]) + radius
        for cell in self._cells(low, high):
            self._buckets.setdefault(cell, []).append(index)

    def near(self, points, radius):
        """Capsules, in insertion order, that could lie within reach of ``points``."""
        points = np.asarray(points, dtype=float).reshape(-1, 3)
        low = points[:, :2].min(axis=0) - float(radius)
        high = points[:, :2].max(axis=0) + float(radius)
        found = set()
        for cell in self._cells(low, high):
            found.update(self._buckets.get(cell, ()))
        return [self.capsules[index] for index in sorted(found)]


def _hits_occupied(field, start, end, radius, clearance=0.0, skip=(), index=None):
    """True when this capsule overlaps an already-routed shaft along its length.

    Exact segment distance over the whole length. A pair joined on purpose,
    a T-joint or a shared foot, has an endpoint of one lying on the other's
    axis, and is not an overlap. Parallel shafts that share a run are
    overlaps wherever they meet, including near an elbow. Capsules in
    ``skip`` (compared by identity) are ones the caller joins on purpose.
    ``index`` replaces the field's own shaft index.
    """
    start, end = np.asarray(start, dtype=float), np.asarray(end, dtype=float)
    axis = end - start
    length = math.sqrt(float(axis @ axis))
    if length < 1e-10:
        return False
    index = field.occupied_capsules if index is None else index
    others = index.near(np.stack([start, end]), float(radius) + float(clearance))
    if skip:
        others = [item for item in others if not any(item is other for other in skip)]
    if not others:
        return False
    low = np.array([item[0] for item in others], dtype=float)
    high = np.array([item[1] for item in others], dtype=float)
    limit = float(radius) + float(clearance) + np.array([item[2] for item in others], dtype=float)
    count = len(others)
    starts, ends = np.broadcast_to(start, (count, 3)), np.broadcast_to(end, (count, 3))
    gaps = segment_distances(starts, ends, low, high)
    hit = gaps < limit - 1e-9
    if not hit.any():
        return False
    # A joint only touches near the joint: trim the joined end by the radii
    # and test again, so a shaft that continues along the other is still hit.
    unit = axis / length
    for which, point in ((0, start), (1, end)):
        here = np.broadcast_to(point, (count, 3))
        joined = hit & (segment_distances(here, here, low, high) < 1e-6)
        if not joined.any():
            continue
        trim = np.minimum(limit, length)[:, None] * unit[None, :]
        rest_start = starts + trim if which == 0 else starts
        rest_end = ends if which == 0 else ends - trim
        hit &= ~joined | (segment_distances(rest_start, rest_end, low, high) < limit - 1e-9)
    for other in (low, high):
        joined = hit & (segment_distances(other, other, starts, ends) < 1e-6)
        if joined.any():
            hit &= ~joined | ~_joint_only(starts, ends, low, high, other, limit)
    return bool(hit.any())


def _joint_only(starts, ends, low, high, joint, limit):
    """Whether the other capsule touches this one only within ``limit`` of ``joint``."""
    axis = high - low
    length = np.linalg.norm(axis, axis=1)
    unit = axis / np.maximum(length, 1e-12)[:, None]
    trim = np.minimum(limit, length)[:, None] * unit
    at_low = np.all(np.isclose(joint, low), axis=1)
    rest_low = np.where(at_low[:, None], low + trim, low)
    rest_high = np.where(at_low[:, None], high, high - trim)
    return segment_distances(rest_low, rest_high, starts, ends) >= limit - 1e-9


def _tip_capsules(base, contact, base_r, contact_r):
    """The capsule a tip reserves: its graph edge, junction to contact.

    The radius is the contact radius, exactly what the collision audit tests
    the ``tip`` edge with, so router and audit agree. The cone is wider at its
    base (``base_r``), but a neighbour grazing that base is a fusion between
    two supports, which the audit does not report either; reserving the whole
    cone rejected routes on real parts that both HEAD and the audit accept.
    """
    del base_r
    return [(tuple(float(v) for v in base), tuple(float(v) for v in contact), float(contact_r))]


def _hits_tips(field, start, end, radius, skip=()):
    """True when this capsule overlaps a routed contact tip.

    The rule is the collision audit's: the two radii, no clearance. A tip is
    fused into the model at its contact and is meant to be snapped off, so
    what must not happen is another support passing through it; a joint at
    an endpoint on the tip's axis is not an overlap, as for shafts.
    """
    tips = getattr(field, 'tip_capsules', None)
    if tips is None or not len(tips):
        return False
    return _hits_occupied(field, start, end, radius, 0.0, skip=skip, index=tips)


def _tip_blocked(field, start, end, radius):
    """Whether a new tip overlaps a routed shaft or tip (the audit's rule)."""
    return (_hits_occupied(field, start, end, radius, 0.0)
            or _hits_tips(field, start, end, radius))


def _mark_occupied(field, start, end, radius):
    field.occupied_capsules.add(start, end, radius)


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
                thinned = _thin(samples, [], spacing, contact_reach_mm(group_settings))
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
    # The same island found by two scans differs only in float noise; one
    # contact is enough, and two would route (or fail) twice.
    unique, seen = [], set()
    for point in mandatory:
        key = contact_key(point)
        if key not in seen:
            seen.add(key)
            unique.append(point)
    mandatory = unique
    contacts = _thin(candidates, mandatory, spacing, contact_reach_mm(settings))
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


#: Steepest surface a model anchor may land on, from horizontal. Material
#: that rises from the landing point no faster than this is the surface the
#: anchor embeds in, as a tip does on a slope; anything steeper is a wall.
ANCHOR_MAX_SLOPE_DEG = 60.0


def _model_anchor_clear(field, column, x, y, surface_z, length, depth, radius, clearance, cancel,
                        top_radius=None):
    """Bound a new bottom connector on the existing column analysis grid.

    The lower endpoint must stay in the central column's immediately preceding
    material run. Above that surface the connector, a taper from ``radius`` at
    the surface to ``top_radius`` at ``length``, plus XY clearance must be
    empty, except for the landing surface itself rising under the anchor no
    steeper than ``ANCHOR_MAX_SLOPE_DEG``. Only as accurate as the column
    lattice.
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
    bottom_r = float(radius)
    top_r = bottom_r if top_radius is None else float(top_radius)
    grid = field.grid
    reach = max(bottom_r, top_r) + float(clearance)
    slope = math.tan(math.radians(ANCHOR_MAX_SLOPE_DEG))
    c0 = max(0, int(math.floor((x - reach - grid.x0) / grid.dx)))
    c1 = min(grid.width, int(math.ceil((x + reach - grid.x0) / grid.dx)))
    r0 = max(0, int(math.floor((y - reach - grid.y0) / grid.dy)))
    r1 = min(grid.height, int(math.ceil((y + reach - grid.y0) / grid.dy)))
    last = int(math.ceil((surface_z + length - field.z0) / field.dz - 1e-9))
    # The landing height was sampled at the centre of the anchor's own cell,
    # so slopes are measured from there, not from the contact point.
    home_row, home_col = divmod(int(column), grid.width)
    home_x = grid.x0 + (home_col + .5) * grid.dx
    home_y = grid.y0 + (home_row + .5) * grid.dy
    for row in range(r0, r1):
        cancel.check()
        for col in range(c0, c1):
            # Distance to the nearest point of this cell, not its center.
            dx = max(grid.x0 + col * grid.dx - x, x - (grid.x0 + (col + 1) * grid.dx), 0.)
            dy = max(grid.y0 + row * grid.dy - y, y - (grid.y0 + (row + 1) * grid.dy), 0.)
            distance = math.hypot(dx, dy)
            if distance > reach:
                continue
            lows, highs = field.runs(field.column(row, col))
            # Column tops are sampled at cell centres, so the slope allowance
            # is measured there; the nearest-point distance still decides
            # whether the connector reaches the cell at all.
            centre = math.hypot(grid.x0 + (col + .5) * grid.dx - home_x,
                                grid.y0 + (row + .5) * grid.dy - home_y)
            rise = top + 1 + int(math.floor(centre * slope / field.dz + 1e-9))
            for run_lo, run_hi in zip(lows.tolist(), highs.tolist()):
                if run_lo >= last or run_hi <= top:
                    continue
                if run_lo <= rise and run_hi <= rise:
                    # The landing surface itself, rising no steeper than the
                    # limit all the way to its top. A run that climbs past the
                    # limit anywhere is a wall, even beyond the anchor's reach.
                    continue
                seg_lo, seg_hi = max(run_lo, top), min(run_hi, last)
                if seg_hi <= seg_lo:
                    continue
                height = (seg_hi - top) * field.dz
                taper = bottom_r + (top_r - bottom_r) * min(1.0, height / length)
                if distance <= taper + float(clearance):
                    return False
    return True


def _plate_route(field, column, contact_index, point, base_z, spec, spacing, clearance_mm,
                 branch_attempts, usable_shaft, exempt, model_shaft_clear=None, evidence=None):
    """A route from one contact's tip base to the plate.

    Returns ``(kind, anchor, elbow, skipped)``: kind is ``'vertical'`` (a free
    column), ``'branched'`` (an angled run to a free column at
    ``support.pillar_angle_deg``), or None when neither exists. ``skipped``
    means the free column already carries a shaft, so a density-exempt-free
    contact is dropped rather than woven around it.

    ``evidence``, when given, is filled with what decided the route: why the
    contact's own column was not a vertical (``plate``) and how far the branch
    search got (``branch``). It is what an unroutable diagnostic explains.
    """
    x, y, _z = point
    pillar_r, clearance = spec.pillar_r, spec.clearance
    if evidence is None:
        evidence = {}
    if model_shaft_clear is None:
        def model_shaft_clear(start, end, radius):
            return True
    branch_tangent = spec.branch_tangent
    _usable_shaft = usable_shaft
    plate_kind = plate_anchor = plate_elbow = None
    # A free column is a vertical plate route. Capsule-testing that run
    # against neighbouring cells treats local curvature (a sphere) as a
    # collision and forces a 45° branch that then fails drainage. The
    # hole-clip case is an angled shaft; those still use _usable_shaft.
    if _free_to_plate(field, column, contact_index, clearance):
        occupied = _hits_occupied(field, (x, y, 0.0), (x, y, base_z), pillar_r, clearance_mm)
        if occupied and not exempt:
            # A second vertical on top of an existing shaft. Skip it rather
            # than weaving a 45° branch that fails drainage.
            return None, None, None, True
        # The central column being free is not enough: a contact on a part's
        # edge would stand its pillar half inside the wall below. Test the
        # pillar's own radius, without clearance, so curvature beside the
        # tip (excluded around it) still allows a vertical.
        if _hits_tips(field, (x, y, 0.0), (x, y, base_z), pillar_r):
            # Another contact's tip stands in this column: the pillar would
            # pass through it. Not a density skip; the contact still needs one.
            evidence['plate'] = {'blocked_by': 'existing_tip'}
        elif model_shaft_clear((x, y, 0.0), (x, y, base_z), pillar_r):
            plate_kind, plate_anchor = 'vertical', (x, y, 0.0)
        else:
            evidence['plate'] = {'blocked_by': 'wall_beside_pillar',
                                 'pillar_diameter_mm': 2 * pillar_r}
    else:
        row, col = divmod(int(column), field.grid.width)
        below = field.top_below(column, contact_index)
        plate = {'blocked_by': 'model_below' if below is not None else 'contact_inside_material',
                 'material_from_z_mm': round(field.z_of(int(field.first_material[row, col])), 4)}
        if below is not None:
            plate['material_top_z_mm'] = round(field.z_of(below), 4)
        evidence['plate'] = plate
    if plate_kind is None:
        best = None
        radius = max(1, int(math.ceil(min(2 * spacing, max(0.0, base_z)) / field.grid.dx)))
        branch = evidence['branch'] = {
            'tip_base_z_mm': round(base_z, 4),
            'search_radius_mm': round(min(2 * spacing, max(0.0, base_z)), 4),
            'free_columns': 0, 'within_angle': 0, 'tested': 0,
            'blocked_near_elbow': 0, 'shaft_blocked': 0}
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
            branch['free_columns'] = int(np.count_nonzero((lateral > 1e-9) & (lateral <= 2 * spacing)))
            branch['within_angle'] = int(np.count_nonzero(usable))
            tested = 0
            limit = max(int(branch_attempts) * 8, 256)
            # Stable: equal distances (symmetric cells) must resolve the same way
            # on every NumPy build, or the same part routes differently.
            for pick in np.argsort(np.where(usable, lateral, np.inf), kind='stable'):
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
                        branch['blocked_near_elbow'] += 1
                        continue
                tested += 1
                angled = ((nx, ny, elbow_z), (x, y, base_z))
                vertical = ((nx, ny, 0.0), (nx, ny, elbow_z))
                if (_usable_shaft(*angled, pillar_r)
                        and (elbow_z <= 1e-9 or _usable_shaft(*vertical, pillar_r))):
                    best = (distance, nx, ny, elbow_z)
                    break
                branch['shaft_blocked'] += 1
            branch['tested'] = tested
        if best is not None:
            plate_kind = 'branched'
            plate_anchor = (best[1], best[2], 0.0)
            plate_elbow = (best[1], best[2], best[3])
    return plate_kind, plate_anchor, plate_elbow, False


def _model_anchor_candidate(field, column, contact_index, point, spec, support, clearance_mm,
                            grid_pad, exclude, cancel, *, info=None):
    """A route down to model material directly below the contact.

    Returns ``(anchor, small, rejected)``: ``anchor`` is the surface point to
    land on, or None; ``small`` says it is a small model-to-model pillar;
    ``rejected`` says a candidate existed but did not fit or collided.

    ``info``, when given, records why: ``why`` names the failed test and
    ``gap_mm`` / ``surface_z_mm`` the material below.
    """
    if info is None:
        info = {}
    x, y, z = point
    small_mode, small_r, small_limit = spec.small_mode, spec.small_r, spec.small_limit
    tip, min_tip, pillar_r, contact_r = spec.tip, spec.min_tip, spec.pillar_r, spec.contact_r
    anchor_length, anchor_depth = spec.anchor_length, spec.anchor_depth
    rejected = False
    below = field.top_below(column, contact_index)
    model_anchor = None
    candidate_small = False
    if below is None:
        info['why'] = 'no_material_below'
        return model_anchor, candidate_small, rejected
    anchor_z = field.z_of(below)
    gap = z - anchor_z
    info['gap_mm'] = round(gap, 4)
    info['surface_z_mm'] = round(anchor_z, 4)
    candidate_small = small_mode == 'model' and small_r > 0 and 1e-9 < gap <= small_limit
    min_anchor_gap = (2 * min_tip) if anchor_length else min_tip
    info['min_gap_mm'] = round(min_anchor_gap, 4)
    if candidate_small:
        low_runs, high_runs = field.runs(column)
        at_top = int(np.searchsorted(high_runs, field.layer_of(z), side='right'))
        upper_fits = (at_top < len(low_runs) and field.z_of(low_runs[at_top]) <= z + 1e-9 and
                      z + support['small_pillar_upper_depth_mm'] <= field.z_of(high_runs[at_top]) + 1e-9)
        if upper_fits and _model_anchor_clear(field, column, x, y, anchor_z, gap,
                support['small_pillar_lower_depth_mm'], small_r,
                support['support_clearance_mm'], cancel):
            if _tip_blocked(field, (x, y, anchor_z), (x, y, z), small_r):
                rejected = True
                info['why'] = 'existing_support'
            else:
                model_anchor = (x, y, anchor_z)
        else:
            rejected = True
            info['why'] = 'small_pillar_no_fit'
    elif gap >= min_anchor_gap:
        fitted = (_fit_anchor_tips(gap, tip, anchor_length, min_tip, pillar_r)
                  if anchor_length else (min(tip, gap), 0.0, True))
        if fitted is None:
            rejected = True
            info['why'] = 'tip_no_fit'
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
                    bottom_r, support['support_clearance_mm'], cancel,
                    top_radius=candidate_r)):
                middle_lo, middle_hi = anchor_z + fit_bottom, z - fit_tip
                extra_exclude = [((x, y, anchor_z),
                                  max(anchor_depth, support['break_point_diameter_mm'] / 2,
                                      candidate_r + clearance_mm + grid_pad))]
                exclude_both = exclude + extra_exclude
                middle_ok = (middle_hi - middle_lo <= 1e-9 or _shaft_clear(
                    field, (x, y, middle_lo), (x, y, middle_hi), candidate_r, clearance_mm,
                    cancel, exclude_both))
                occupied = (middle_hi - middle_lo > 1e-9 and _hits_occupied(
                    field, (x, y, middle_lo), (x, y, middle_hi), candidate_r, clearance_mm))
                # The shaft and the bottom connector, whose foot sits on the
                # same surface a neighbour's tip may touch, must miss every
                # routed tip (the collision audit's rule).
                tip_in_way = ((middle_hi - middle_lo > 1e-9 and _hits_tips(
                    field, (x, y, middle_lo), (x, y, middle_hi), candidate_r))
                    or (fit_bottom + anchor_depth > 1e-9 and _tip_blocked(
                        field, (x, y, anchor_z - anchor_depth), (x, y, anchor_z + fit_bottom),
                        bottom_r)))
                if middle_ok and not occupied and not tip_in_way:
                    model_anchor = (x, y, anchor_z)
                else:
                    rejected = True
                    info['why'] = ('shaft_blocked' if not middle_ok else
                                   'existing_support' if occupied else 'existing_tip')
            else:
                rejected = True
                info['why'] = _anchor_connector_why(field, column, anchor_z, anchor_depth)
    else:
        info['why'] = 'gap_too_short'
        rejected = bool(anchor_length)
    return model_anchor, candidate_small, rejected


def _anchor_connector_why(field, column, surface_z, depth):
    """Name why ``_model_anchor_clear`` refused a bottom connector."""
    low, high = field.runs(column)
    top = int(round((surface_z - field.z0) / field.dz))
    position = int(np.searchsorted(high, top, side='right')) - 1
    if position < 0 or surface_z - depth < field.z_of(low[position]) - 1e-9:
        return 'penetration_exceeds_material'
    return 'connector_blocked'


def stub_reach_mm(spec, field):
    """How far a stub may reach: no farther than a tip-and-anchor pair could not.

    A model anchor needs ``2 * min_tip_length_mm`` of gap (``min_tip`` with no
    bottom connector); a stub serves only material closer than that. The
    floor of two analysis-cell diagonals keeps the cells next to the
    contact's own within reach on a coarse grid.
    """
    need = 2 * spec.min_tip if spec.anchor_length else spec.min_tip
    return max(need, 2 * math.hypot(field.grid.dx, field.grid.dy))


def _model_stub(field, column, contact_index, point, spec, tips=None):
    """A thin rod from the contact into the nearest material printed before it.

    For island births and manual or correction contacts only, after every
    plate route and model anchor failed. On a sloped or near-vertical edge
    that is not aligned with the pixel grid, the printer raster can leave a
    one-pixel island that touches the layer below only at a corner. Its
    material below or beside is closer than a tip can span, so no pillar or
    anchor fits, and without the stub the island stays unsupported.

    Candidate targets are analysis cells within :func:`stub_reach_mm` of the
    contact whose column is solid on a layer below the contact's; the nearest
    wins, the contact's own column on a tie. The rod has the contact diameter,
    runs from ``penetration_mm`` above the contact to the target cell's centre
    sunk ``max(model_anchor_penetration_mm, penetration_mm)`` into that
    material, and that lower end must stay inside the same run. It must not
    overlap another routed shaft. ``support_clearance_mm`` is not added: the
    stub is shorter than a tip and fused to the part along its length, so
    there is no shaft to cut free beside it, and neighbouring islands on one
    edge sit closer together than two clearances. ``tips`` (a
    :class:`CapsuleIndex` of routed tips at their base radius) is avoided too.

    Returns ``(stub, why)``: ``stub`` is a dict or None, ``why`` says what
    refused it.
    """
    x, y, z = point
    grid = field.grid
    reach = stub_reach_mm(spec, field)
    depth = max(spec.anchor_depth, spec.penetration)
    row, col = divmod(int(column), grid.width)
    span = int(math.ceil(reach / min(grid.dx, grid.dy))) + 1
    candidates = []
    for r in range(max(0, row - span), min(grid.height, row + span + 1)):
        for c in range(max(0, col - span), min(grid.width, col + span + 1)):
            cx, cy = grid.x0 + (c + .5) * grid.dx, grid.y0 + (r + .5) * grid.dy
            lateral = 0.0 if (r, c) == (row, col) else math.hypot(cx - x, cy - y)
            if lateral > reach:
                continue
            lows, highs = field.runs(field.column(r, c))
            # The highest solid layer below the contact's own layer.
            position = int(np.searchsorted(lows, contact_index, side='left')) - 1
            if position < 0:
                continue
            top = min(int(highs[position]), contact_index)
            surface = field.z_of(top)
            distance = math.hypot(lateral, max(0.0, z - surface))
            if distance > reach:
                continue
            candidates.append((distance, (r, c) != (row, col), r, c, cx, cy, surface,
                               field.z_of(int(lows[position]))))
    if not candidates:
        return None, 'no_material_within_reach'
    why = 'stub_depth'
    for distance, _other, r, c, cx, cy, surface, run_bottom in sorted(candidates):
        if (r, c) == (row, col):
            cx, cy = x, y
        bottom = surface - depth
        if bottom < run_bottom - 1e-9:
            continue
        foot, top_point = (cx, cy, bottom), (x, y, z + spec.penetration)
        if _stub_hits(field, foot, (x, y, z), top_point, spec.contact_r, tips=tips):
            why = 'stub_crosses_support'
            continue
        return {'foot': foot, 'surface': (cx, cy, surface), 'top': top_point,
                'radius_mm': spec.contact_r,
                'length_mm': math.dist(foot, (x, y, z)) + spec.penetration,
                'reach_mm': distance}, None
    return None, why


def stub_solid(foot, contact, top, radius, segments=24):
    """The hull of three horizontal discs: at the foot, the contact and above it.

    The rod must hold the contact at the contact's own layer, not only near
    its top, so it runs foot to contact and then straight up by the
    penetration. Horizontal caps matter: a tilted round cylinder's lowest
    layer is a sliver at one side of its end cap, which can sit outside the
    material the stub was sunk into and print as a new island. With flat caps
    the lowest layer is the whole foot disc around the sampled solid point,
    and each layer above overlaps the one below.
    """
    import manifold3d as m
    angles = np.linspace(0.0, 2 * math.pi, int(segments), endpoint=False)
    ring = np.stack([np.cos(angles), np.sin(angles), np.zeros_like(angles)], axis=1) * float(radius)
    points = np.concatenate([ring + np.asarray(center, dtype=float)
                             for center in (foot, contact, top)])
    return m.Manifold.hull_points(points.tolist())


def _stub_hits(field, foot, contact, top, radius, skip=(), tips=None):
    """Whether a stub's two axis segments overlap a routed shaft or tip (no clearance)."""
    indexes = [field.occupied_capsules] + ([tips] if tips is not None else [])
    return any(_hits_occupied(field, start, end, radius, 0.0, skip=skip, index=index)
               for index in indexes for start, end in ((foot, contact), (contact, top)))


def _emit_stub(stub, order, point, graph, solids, field, records, local_parameters):
    """Add one stub's rod, graph nodes and edges, and reserve its capsule."""
    head = f'contact{order}'
    if local_parameters:
        graph.overrides.append({'contact': head, 'reason': 'effective_parameters',
                                'parameters': dict(local_parameters)})
    solids.append(stub_solid(stub['foot'], point, stub['top'], stub['radius_mm']))
    joined = stub.get('joins')
    if joined is None:
        foot = f'foot{order}'
        graph.nodes.extend([SupportNode(foot, list(stub['surface']), 'model_anchor'),
                            SupportNode(head, list(point), 'contact')])
    else:
        # Split the earlier stub at the junction, so the graph says the two
        # meet there on purpose. It may already be split; pick the piece
        # that holds the junction.
        foot = f'stub_joint{order}'
        junction_z = stub['foot'][2]
        graph.nodes.extend([SupportNode(foot, list(stub['foot']), 'junction'),
                            SupportNode(head, list(point), 'contact')])
        where = {node.id: node.position_mm for node in graph.nodes}

        def miss(item):
            low, high = sorted((where[item.start][2], where[item.end][2]))
            return abs(min(max(junction_z, low), high) - junction_z)

        old_edge = min(joined['edges'], key=miss)
        lower = SupportEdge(old_edge.start, foot, old_edge.radius_mm, old_edge.kind)
        upper = SupportEdge(foot, old_edge.end, old_edge.radius_mm, old_edge.kind)
        graph.edges[graph.edges.index(old_edge)] = lower
        graph.edges.append(upper)
        joined['edges'][joined['edges'].index(old_edge)] = lower
        joined['edges'].append(upper)
    edge = SupportEdge(foot, head, stub['radius_mm'], 'model_stub')
    graph.edges.append(edge)
    _mark_occupied(field, stub['foot'], point, stub['radius_mm'])
    _mark_occupied(field, point, stub['top'], stub['radius_mm'])
    capsules = field.occupied_capsules.capsules[-2:]
    # Stubs joined to one another are one lump fused to the part; a later
    # stub joining any of them may touch all of them.
    group = joined['group'] if joined is not None else []
    group.extend(capsules)
    records.append({'segments': ((stub['foot'], tuple(point)), (tuple(point), stub['top'])),
                    'edges': [edge], 'group': group})


def _record_shaft(records, start, end, edge, field):
    """Remember an emitted shaft segment so a mandatory contact may hang from it."""
    records.append({'pieces': [(tuple(float(v) for v in start), tuple(float(v) for v in end), edge)],
                    'group': [field.occupied_capsules.capsules[-1]],
                    'radius': float(edge.radius_mm)})


def _join_shaft(field, point, spec, support, shafts, cancel):
    """An angled tip from a routed shaft to a contact nothing else can reach.

    For island, manual and correction contacts only. When the contact stands
    beside another route's pillar, its own pillar and every branch from its
    tip base collide with that pillar, yet the geometry genuinely meets: the
    contact can hang from the pillar. The tip runs from a point on the shaft's
    axis, below the contact, whose free length beyond the shaft's surface is
    between ``min_tip_length_mm`` and ``tip_length_mm``, rising at least ``pillar_angle_deg``; it must
    miss the model beyond the contact itself, every other shaft and every
    tip, with the audit's no-clearance rule. Returns the nearest such joint,
    or None.
    """
    x, y, z = point
    here = np.array([x, y, z], dtype=float)
    reach, shortest, tangent = float(spec.tip), float(spec.min_tip), spec.branch_tangent
    exclude = _exclude_spheres((x, y, z), spec.penetration, support['break_point_diameter_mm'])
    best = None
    for record in shafts:
        cancel.check()
        for piece, (low, high, _edge) in enumerate(record['pieces']):
            low, high = np.asarray(low, dtype=float), np.asarray(high, dtype=float)
            if (min(low[2], high[2]) >= z or np.linalg.norm((low + high) / 2 - here)
                    > reach + np.linalg.norm(high - low) / 2 + record['radius']):
                continue
            for t in np.linspace(1.0, 0.0, 25):
                junction = low + (high - low) * t
                rise = z - junction[2]
                lateral = math.hypot(junction[0] - x, junction[1] - y)
                length = math.hypot(rise, lateral)
                # The tip leaves the shaft at its surface, so its free length
                # is measured from there.
                free = length - record['radius']
                if rise <= 0 or not shortest <= free <= reach or rise < lateral * tangent - 1e-9:
                    continue
                if best is not None and length >= best['length_mm']:
                    continue
                joint = tuple(float(v) for v in junction)
                if (_hits_occupied(field, joint, (x, y, z), spec.contact_r, 0.0, skip=record['group'])
                        or _hits_tips(field, joint, (x, y, z), spec.contact_r, skip=record['group'])
                        or not _shaft_clear(field, joint, (x, y, z), spec.contact_r, 0.0, cancel,
                                            exclude + [(junction, record['radius'])])):
                    continue
                best = {'junction': joint, 'record': record, 'piece': piece, 'length_mm': length,
                        'base_r': min(record['radius'], spec.tip_base_r)}
    return best


def _emit_joined_tip(joint, order, point, spec, support, graph, solids, field, records,
                     local_parameters):
    """Emit an angled tip hanging from a routed shaft; split that shaft's edge."""
    from .support_segments import tip_segment
    x, y, z = point
    head, name = f'contact{order}', f'tip_joint{order}'
    if local_parameters:
        graph.overrides.append({'contact': head, 'reason': 'effective_parameters',
                                'parameters': dict(local_parameters)})
    top = (x, y, z + spec.penetration)
    span = math.dist(joint['junction'], top)
    ball = float(support['break_point_diameter_mm'])
    solids.append(tip_segment(joint['junction'], top, max(joint['base_r'], spec.contact_r),
                              spec.contact_r, support['tip_shape'],
                              ball if ball and ball <= span else 0.0))
    graph.nodes.extend([SupportNode(name, list(joint['junction']), 'junction'),
                        SupportNode(head, [x, y, z], 'contact')])
    record = joint['record']
    low, high, old = record['pieces'][joint['piece']]
    lower = SupportEdge(old.start, name, old.radius_mm, old.kind)
    upper = SupportEdge(name, old.end, old.radius_mm, old.kind)
    graph.edges[graph.edges.index(old)] = lower
    graph.edges.append(upper)
    # Later joins onto this shaft land on whichever half holds them.
    record['pieces'][joint['piece']:joint['piece'] + 1] = [
        (low, joint['junction'], lower), (joint['junction'], high, upper)]
    edge = SupportEdge(name, head, spec.contact_r, 'tip')
    graph.edges.append(edge)
    field.tip_capsules.add(joint['junction'], (x, y, z), spec.contact_r)
    records.append({'segments': ((joint['junction'], (x, y, z)),), 'edges': [edge],
                    'group': [field.tip_capsules.capsules[-1]]})


def _join_stub(field, point, spec, stubs, tips=None):
    """A stub that joins an earlier stub or a routed tip instead of the model.

    Islands along one stepped edge can sit closer together than two stub
    radii, so the second stub would overlap the first; an island can also
    sit beside another contact's tip. A stub may then end on that support's
    axis, below the contact (printed before it) and within
    :func:`stub_reach_mm`, provided it overlaps nothing outside the group it
    joins. Returns ``(record, junction)``, or ``(None, None)``.
    """
    x, y, z = point
    reach = stub_reach_mm(spec, field)
    here = np.array([x, y, z], dtype=float)
    top = (x, y, z + spec.penetration)
    best = None
    for record in stubs:
        for low, high in record['segments']:
            low, high = np.asarray(low, dtype=float), np.asarray(high, dtype=float)
            axis = high - low
            length2 = float(axis @ axis)
            t = 0.0 if length2 < 1e-12 else float(np.clip((here - low) @ axis / length2, 0.0, 1.0))
            junction = low + axis * t
            # Printed before the contact: strictly below its layer.
            if junction[2] > z - field.dz / 2:
                if abs(axis[2]) < 1e-12:
                    continue
                t = min(t, (z - field.dz / 2 - low[2]) / axis[2])
                if t < 0:
                    continue
                junction = low + axis * t
            distance = float(np.linalg.norm(junction - here))
            if distance > reach or (best is not None and distance >= best[0]):
                continue
            if _stub_hits(field, tuple(junction), (x, y, z), top, spec.contact_r,
                          skip=record['group'], tips=tips):
                continue
            best = (distance, record, tuple(float(v) for v in junction))
    return (None, None) if best is None else (best[1], best[2])


#: What each unroutable reason asks the user to try, as ``support`` keys.
UNROUTABLE_SUGGESTIONS = {
    'policy_blocked': ('allow_part_to_part',),
    'tip_no_fit': ('min_tip_length_mm', 'model_anchor_length_mm', 'tip_length_mm'),
    'anchor_rejected:gap_too_short': ('min_tip_length_mm', 'model_anchor_length_mm'),
    'anchor_rejected:tip_no_fit': ('min_tip_length_mm', 'model_anchor_length_mm', 'tip_length_mm'),
    'anchor_rejected:connector_blocked': ('model_anchor_diameter_mm', 'model_anchor_length_mm',
                                          'support_clearance_mm'),
    'anchor_rejected:penetration_exceeds_material': ('model_anchor_penetration_mm',),
    'anchor_rejected:shaft_blocked': ('support_clearance_mm', 'pillar_diameter_mm',
                                      'small_pillar_diameter_mm'),
    'anchor_rejected:existing_support': ('spacing_mm', 'support_clearance_mm'),
    'anchor_rejected:existing_tip': ('spacing_mm', 'model_anchor_diameter_mm', 'tip_base_diameter_mm'),
    'tip_blocked': ('spacing_mm', 'tip_base_diameter_mm', 'tip_length_mm'),
    'anchor_rejected:small_pillar_no_fit': ('small_pillar_upper_depth_mm',
                                            'small_pillar_lower_depth_mm',
                                            'small_pillar_diameter_mm', 'small_pillar_mode'),
    'plate_blocked': ('spacing_mm', 'pillar_angle_deg', 'allow_part_to_part'),
    'branch_exhausted': ('pillar_angle_deg', 'support_clearance_mm', 'spacing_mm',
                         'allow_part_to_part'),
}

_ANCHOR_WHY_TEXT = {
    'gap_too_short': ('model material {gap:.2f} mm below (surface at Z {surface:.2f}) is closer '
                      'than the {need:.2f} mm a tip and a bottom connector need'),
    'tip_no_fit': ('the {gap:.2f} mm gap to the material at Z {surface:.2f} cannot hold both '
                   'tips at their minimum length'),
    'connector_blocked': ('the bottom connector onto the material at Z {surface:.2f} would hit '
                          'a wall beside it'),
    'penetration_exceeds_material': ('the bottom connector would sink through the material at '
                                     'Z {surface:.2f}, which is thinner than '
                                     'model_anchor_penetration_mm'),
    'shaft_blocked': 'the shaft down to the material at Z {surface:.2f} would pass through the model',
    'existing_support': ('the shaft down to the material at Z {surface:.2f} would cross a '
                         'support already routed there'),
    'existing_tip': ("the support down to the material at Z {surface:.2f} would pass through "
                     "another contact's tip"),
    'small_pillar_no_fit': ('the small model pillar down to Z {surface:.2f} does not fit its '
                            'configured depths or clearance'),
}


_STUB_WHY_TEXT = {
    'no_material_within_reach': 'no material printed before it lies within {reach:.2f} mm to fuse a stub to',
    'stub_depth': 'the material within {reach:.2f} mm is too thin to sink a stub into',
    'stub_crosses_support': 'a stub to the material within {reach:.2f} mm would cross another support',
}


def explain_unroutable(evidence, support, *, policy_refused=False, fit_failed=False):
    """Name why a contact has no route, in words and as data.

    Returns ``(reason, message, details)``. ``reason`` is one of
    ``policy_blocked``, ``tip_no_fit``, ``anchor_rejected:<why>``,
    ``plate_blocked`` or ``branch_exhausted``; ``details`` carries the plate,
    branch and anchor evidence, the obstructing height where there is one, and
    ``suggest``, the support settings worth changing.
    """
    plate = dict(evidence.get('plate') or {})
    branch = dict(evidence.get('branch') or {})
    anchor = {key: value for key, value in (evidence.get('anchor') or {}).items() if key != 'stub'}
    why = anchor.get('why')
    radius = branch.get('search_radius_mm', 0.0)
    if plate.get('blocked_by') == 'model_below':
        plate_text = (f"model material under it from Z {plate['material_from_z_mm']:.2f} "
                      f"to Z {plate['material_top_z_mm']:.2f} blocks a vertical pillar")
    elif plate.get('blocked_by') == 'contact_inside_material':
        plate_text = ('its analysis-grid cell is already solid at the contact height (it sits on a '
                      f"wall or edge), with material down to Z {plate['material_from_z_mm']:.2f}")
    elif plate.get('blocked_by') == 'wall_beside_pillar':
        plate_text = 'a vertical pillar would stand inside the wall beside the contact'
    elif plate.get('blocked_by') == 'existing_tip':
        plate_text = "a vertical pillar would pass through another contact's tip"
    else:
        plate_text = 'no vertical pillar fits'
    if not branch.get('free_columns'):
        branch_text = f'no column within {radius:.1f} mm is free down to the plate'
    elif not branch.get('within_angle'):
        branch_text = (f"{branch['free_columns']} free column(s) within {radius:.1f} mm, but none "
                       'reachable at pillar_angle_deg before the plate')
    elif (plate.get('material_top_z_mm') is not None
          and plate['material_top_z_mm'] > branch.get('tip_base_z_mm', math.inf)):
        branch_text = (f"every angled branch would start at the tip base (Z "
                       f"{branch['tip_base_z_mm']:.2f}, one tip_length_mm below the contact), "
                       'which is inside that material')
    else:
        branch_text = (f"all {branch['within_angle']} angled branch(es) within {radius:.1f} mm "
                       'hit the model or another support')
    tip = evidence.get('tip') or {}
    if tip.get('blocked'):
        reason = 'tip_blocked'
        headline = ("its own tip, from Z {:.2f} up to the contact, would pass through a support or "
                    'tip routed before it'.format(tip.get('tip_base_z_mm', 0.0)))
    elif policy_refused:
        reason = 'policy_blocked'
        headline = ('only a support standing on the model below fits here, and '
                    'allow_part_to_part is off')
    elif fit_failed:
        reason = 'tip_no_fit'
        headline = (f"the {anchor.get('gap_mm', 0.0):.2f} mm gap to the material below cannot hold "
                    'a top tip and a bottom connector at their minimum lengths')
    elif why and why != 'no_material_below':
        reason = f'anchor_rejected:{why}'
        headline = _ANCHOR_WHY_TEXT.get(why, 'the support onto the model below was rejected').format(
            gap=anchor.get('gap_mm', 0.0), surface=anchor.get('surface_z_mm', 0.0),
            need=anchor.get('min_gap_mm', 0.0))
    elif branch.get('within_angle'):
        reason = 'branch_exhausted'
        headline = branch_text
    else:
        reason = 'plate_blocked'
        headline = plate_text
    parts = [headline]
    for text in (plate_text, branch_text):
        if text != headline:
            parts.append(text)
    stub = dict(evidence.get('stub') or {})
    if stub:
        parts.append(_STUB_WHY_TEXT.get(stub.get('why'), 'no stub fits').format(
            reach=stub.get('reach_mm', 0.0)))
    message = 'No route: ' + '; '.join(parts) + '.'
    suggest = [key for key in UNROUTABLE_SUGGESTIONS.get(reason, ())
               if key in support and not (key == 'allow_part_to_part' and support[key])]
    details = {'reason': reason, 'plate': plate, 'branch': branch, 'anchor': anchor,
               'suggest': suggest}
    if stub:
        details['stub'] = stub
    obstruction = anchor.get('surface_z_mm', plate.get('material_top_z_mm'))
    if obstruction is not None:
        details['obstruction_z_mm'] = obstruction
    return reason, message, details


@dataclass(frozen=True)
class ContactSpec:
    """Support dimensions for one contact, after any per-contact override."""
    pillar_r: float
    contact_r: float
    tip_base_r: float
    small_r: float
    small_limit: float
    small_mode: str
    branch_tangent: float
    tip: float
    min_tip: float
    penetration: float
    anchor_length: float
    anchor_depth: float
    clearance: int      # support_clearance_mm in analysis layers, at least one


def _contact_spec(support, field):
    pillar_r = float(support['pillar_diameter_mm']) / 2
    return ContactSpec(
        pillar_r=pillar_r,
        contact_r=float(support['contact_diameter_mm']) / 2,
        # The tip cone's lower radius is its own parameter. It defaulted to the
        # pillar radius, and in the known-good CHITUBOX profile the two are
        # equal, which is exactly why conflating them went unnoticed.
        tip_base_r=(float(support['tip_base_diameter_mm']) / 2
                    if support['tip_base_diameter_mm'] else pillar_r),
        small_r=float(support['small_pillar_diameter_mm']) / 2,
        small_limit=float(support['small_pillar_max_length_mm']),
        small_mode=support['small_pillar_mode'],
        branch_tangent=math.tan(math.radians(float(support['pillar_angle_deg']))),
        tip=float(support['tip_length_mm']),
        min_tip=float(support['min_tip_length_mm']),
        penetration=float(support['penetration_mm']),
        anchor_length=float(support['model_anchor_length_mm']),
        anchor_depth=float(support['model_anchor_penetration_mm']),
        clearance=max(1, int(math.ceil(float(support['support_clearance_mm']) / field.dz))))


def route_contacts(contacts, field, settings, *, cancel=None, branch_attempts=8, max_diagnostics=256,
                   contact_parameters=(), density_exempt=()):
    """Route given contacts to the plate or to already-printed model material."""
    cancel = cancel or CancellationToken()
    global_support = settings['support']
    normalized_parameters = normalize_contact_parameters(contact_parameters, settings)
    started = time.monotonic()
    field.occupied_capsules = CapsuleIndex(settings['support']['spacing_mm'])
    field.tip_capsules = CapsuleIndex(settings['support']['spacing_mm'])
    exempt_keys = {contact_key(point) for point in density_exempt}
    spacing = float(global_support['spacing_mm'])
    base_spec = _contact_spec(global_support, field)
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
    anchor_rejections = {}
    anchor_examples = []
    small_models = 0
    stubs = 0
    stub_records = []
    shaft_records = []
    tips_joined = 0
    tip_index = field.tip_capsules
    duplicates = 0
    failure_reasons = {}
    seen_keys = set()
    matched_override_keys = set()
    deferred_stubs = []

    def record_failure(position, evidence, support, *, policy_refused=False, fit_failed=False):
        # Internal overhangs inside a porous part are genuinely unreachable;
        # they are counted in full and sampled in the diagnostics.
        nonlocal failures
        reason, message, details = explain_unroutable(
            evidence, support, policy_refused=policy_refused, fit_failed=fit_failed)
        failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
        unroutable_positions.append(list(position))
        if len(diagnostics) < max_diagnostics:
            diagnostics.append(Diagnostic('support_unroutable', message, severity='warning',
                                          position_mm=list(position), details=details))
        failures += 1

    for order, point in enumerate(contacts):
        cancel.check()
        key = contact_key(point)
        if key in seen_keys:
            # The same point twice (an island the correction loop found again
            # on a second scan) would route twice, or fail twice.
            duplicates += 1
            continue
        seen_keys.add(key)
        local_parameters = parameters_for_contact(normalized_parameters, point)
        if key in normalized_parameters.by_position:
            matched_override_keys.add(key)
        support = dict(global_support)
        support.update(local_parameters)
        spec = _contact_spec(support, field)
        pillar_r, contact_r, tip_base_r = spec.pillar_r, spec.contact_r, spec.tip_base_r
        small_r, small_limit, small_mode = spec.small_r, spec.small_limit, spec.small_mode
        tip, min_tip = spec.tip, spec.min_tip
        penetration, anchor_length, anchor_depth = spec.penetration, spec.anchor_length, spec.anchor_depth
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
            return not (_hits_occupied(field, start, end, radius, clearance_mm)  # noqa: B023
                        or _hits_tips(field, start, end, radius))

        def _model_shaft_clear(start, end, radius):
            return _shaft_clear(field, start, end, radius, 0.0, cancel, exclude)  # noqa: B023

        evidence = {}
        plate_kind, plate_anchor, plate_elbow, skipped = _plate_route(
            field, column, contact_index, (x, y, z), base_z, spec, spacing, clearance_mm,
            branch_attempts, _usable_shaft, exempt, _model_shaft_clear, evidence)
        if skipped:
            density_skipped += 1
            continue
        if plate_kind is not None:
            kind, anchor, elbow = plate_kind, plate_anchor, plate_elbow
        # Evaluate a model anchor even when a plate branch exists. At zero
        # avoidance both candidates compete on total centerline length; at one
        # the historical plate-first order is preserved exactly. A plate
        # candidate whose capsule hit the model or an existing shaft already
        # fell through, so this is "the other of plate vs model".
        anchor_info = evidence['anchor'] = {}
        model_anchor, candidate_small, rejected = _model_anchor_candidate(
            field, column, contact_index, (x, y, z), spec, support, clearance_mm, grid_pad,
            exclude, cancel, info=anchor_info)
        anchor_rejected += int(rejected)
        if rejected:
            why = anchor_info.get('why', 'rejected')
            anchor_rejections[why] = anchor_rejections.get(why, 0) + 1
        policy_refused = False
        if model_anchor is not None:
            if not support['allow_part_to_part']:
                if kind is None:
                    policy_blocked += 1
                    policy_refused = True
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
        tip_conflict = False
        if kind is not None and not is_small_model:
            # The tip base the emitted tip will have (see top_base below).
            thin_run = small_r if small_r > 0 else contact_r
            top_base = thin_run if (kind == 'model_anchor' and not full_middle) else tip_base_r
            if any(_tip_blocked(field, start, end, radius) for start, end, radius in
                   _tip_capsules((x, y, base_z), (x, y, z), top_base, contact_r)):
                # This contact's own tip would pass through a support routed
                # before it. The column is fixed by the contact, so no other
                # plate or anchor route moves the tip; only a stub can help.
                tip_conflict = True
                evidence['tip'] = {'blocked': True, 'tip_base_z_mm': round(base_z, 4),
                                   'route': kind}
                kind = None
        if kind is None and exempt and (model_anchor is None or tip_conflict):
            # An island (or a manual/correction contact) may never be dropped.
            # When nothing else fits it is fused to the material beside or
            # below it, after every other contact, lowest first, so a stub
            # never has to cross one placed above it.
            deferred_stubs.append((z, order, (x, y, z), column, contact_index, spec, support,
                                   local_parameters, evidence))
            continue
        if kind is None:
            record_failure((x, y, z), evidence, support, policy_refused=policy_refused,
                           fit_failed=model_anchor is not None and not policy_refused)
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
            shaft_edges = [SupportEdge(middle_start, junction, run_r, kind)]
        else:
            shaft_edges = [SupportEdge(middle_start, f'elbow{order}', run_r, kind),
                           SupportEdge(f'elbow{order}', junction, run_r, kind)]
        graph.edges.extend(shaft_edges)
        shaft_mark = len(shaft_records)
        tip_edge = SupportEdge(junction, head, contact_r, 'tip')
        graph.edges.append(tip_edge)
        from .support_segments import tip_segment, elbow_sphere
        if elbow is not None:
            graph.nodes.append(SupportNode(f'elbow{order}', list(elbow), 'elbow'))
            if elbow[2] > 1e-9:
                solids.append(cylinder_between(anchor, elbow, run_r))
                # Branched vertical runs brace with plate pillars.
                pillars.append((anchor[0], anchor[1], elbow[2], run_r))
                _mark_occupied(field, anchor, elbow, run_r)
                _record_shaft(shaft_records, anchor, elbow, shaft_edges[0], field)
            solids.append(cylinder_between(elbow, (x, y, base_z), run_r))
            solids.append(elbow_sphere(elbow, run_r))
            _mark_occupied(field, elbow, (x, y, base_z), run_r)
            _record_shaft(shaft_records, elbow, (x, y, base_z), shaft_edges[1], field)
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
                    # Reserve the vertical: it is what a tree falls back to,
                    # and later routes must not be woven through it.
                    _mark_occupied(field, (x, y, middle_z), (x, y, base_z), run_r)
                    # The tree that replaces it must not collide with its own
                    # reservation, only with everything else.
                    tree_jobs[-1]['capsule'] = field.occupied_capsules.capsules[-1]
                else:
                    solids.append(cylinder_between((x, y, middle_z), (x, y, base_z), run_r))
                    _mark_occupied(field, (x, y, middle_z), (x, y, base_z), run_r)
                    _record_shaft(shaft_records, (x, y, middle_z), (x, y, base_z), shaft_edges[0], field)
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
        # Reserve the tip, so later shafts, anchors, trees, braces and stubs
        # keep out of it and later tips do not cross it.
        tip_group = []
        for start, end, radius in _tip_capsules((x, y, base_z), (x, y, z), top_base, contact_r):
            tip_index.add(start, end, radius)
            tip_group.append(tip_index.capsules[-1])
        stub_records.append({'segments': (((x, y, base_z), (x, y, z)),), 'edges': [tip_edge],
                             'group': tip_group})
        if tree_jobs and tree_jobs[-1]['order'] == order:
            tree_jobs[-1]['tip_capsules'] = tip_group
        # A contact's tip and its own shaft are one support: a tip hanging
        # from that shaft may touch both.
        for record in shaft_records[shaft_mark:]:
            record['group'].extend(tip_group)
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

    for _z, order, point, column, contact_index, spec, support, local_parameters, evidence in sorted(
            deferred_stubs, key=lambda item: item[:2]):
        cancel.check()
        stub, why = _model_stub(field, column, contact_index, point, spec, tips=tip_index)
        if stub is None:
            joined, junction = _join_stub(field, point, spec, stub_records, tips=tip_index)
            if joined is not None:
                top_point = (point[0], point[1], point[2] + spec.penetration)
                stub = {'foot': junction, 'surface': junction, 'top': top_point,
                        'radius_mm': spec.contact_r,
                        'length_mm': math.dist(junction, point) + spec.penetration,
                        'joins': joined}
        if stub is None:
            # A pillar or anchor shaft beside the contact: hang the contact
            # from it with an angled tip. This is not a model anchor, so
            # allow_part_to_part does not apply.
            joint = _join_shaft(field, point, spec, support, shaft_records, cancel)
            if joint is not None:
                _emit_joined_tip(joint, order, point, spec, support, graph, solids, field,
                                 stub_records, local_parameters)
                routed['branched'] += 1
                tips_joined += 1
                heights.append(joint['length_mm'])
                continue
        if stub is None:
            evidence['stub'] = {'why': why, 'reach_mm': round(stub_reach_mm(spec, field), 4)}
            record_failure(point, evidence, support)
            continue
        if not support['allow_part_to_part']:
            policy_blocked += 1
            record_failure(point, evidence, support, policy_refused=True)
            continue
        routed['model_anchor'] += 1
        stubs += 1
        _emit_stub(stub, order, point, graph, solids, field, stub_records, local_parameters)
        heights.append(stub['length_mm'])
        if len(anchor_examples) < max_diagnostics:
            anchor_examples.append({'contact': f'contact{order}', 'surface_z_mm': stub['surface'][2],
                                    'bottom_z_mm': stub['foot'][2], 'junction_z_mm': point[2],
                                    'diameter_mm': 2 * stub['radius_mm'], 'stub': True,
                                    'length_mm': round(stub['length_mm'], 4)})

    # Everything below describes the run as configured, not the last contact:
    # per-contact overrides applied only inside the loop.
    support = global_support
    pillar_r, tip_base_r = base_spec.pillar_r, base_spec.tip_base_r
    anchor_length, anchor_depth = base_spec.anchor_length, base_spec.anchor_depth
    small_r, small_limit, small_mode = base_spec.small_r, base_spec.small_limit, base_spec.small_mode
    tip, min_tip = base_spec.tip, base_spec.min_tip
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
        'unroutable_reasons': dict(sorted(failure_reasons.items())),
        'contacts_duplicate': duplicates,
        'tips_joined_to_supports': tips_joined,
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
                         'candidates_rejected': anchor_rejected,
                         'rejections': dict(sorted(anchor_rejections.items())),
                         'stubs': stubs,
                         'stub_basis': ('island, manual or correction contacts with no other route, '
                                        'fused by one rod of contact diameter to material printed '
                                        'before them within a tip-and-anchor gap'),
                         'examples': anchor_examples,
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
        'unbraced': unbraced_lengths(graph),
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
                'trunks': 0, 'contacts_in_trees': 0, 'kept_independent': len(jobs),
                'trunk_diameter_mm': float(support.get('trunk_diameter_mm', 0.0)),
                'fallbacks': {}, 'trunk_limited_clusters': 0}
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
    fallbacks = {}
    trunk_limited = 0
    clearance_mm = float(support['support_clearance_mm'])
    branch_tangent = math.tan(math.radians(float(support['pillar_angle_deg'])))
    trunk_setting_r = float(support.get('trunk_diameter_mm', 0.0)) / 2
    # Shafts a new tree must not touch: every other contact's vertical run
    # (routing already cleared those against each other) and the trees
    # accepted so far. A tree is new geometry the router never saw.
    verticals = [((job['x'], job['y'], 0.0), (job['x'], job['y'], job['base_z']), job['run_r'])
                 for job in jobs]
    accepted = []

    def tree_hits(segments, members):
        """Whether a tree comes within clearance of another shaft or tree."""
        others = [capsule for index, capsule in enumerate(verticals) if index not in members]
        others += accepted
        if others:
            low = np.array([capsule[0] for capsule in others], dtype=float)
            high = np.array([capsule[1] for capsule in others], dtype=float)
            radii = np.array([capsule[2] for capsule in others], dtype=float)
            for start, end, r in segments:
                count = len(others)
                gaps = segment_distances(np.broadcast_to(np.asarray(start, dtype=float), (count, 3)),
                                         np.broadcast_to(np.asarray(end, dtype=float), (count, 3)),
                                         low, high)
                if np.any(gaps < radii + r + clearance_mm - 1e-9):
                    return True
        # Every other routed shaft: angled branches, model anchors, stubs.
        # The members' own reserved verticals are what the tree replaces.
        skip = [jobs[index]['capsule'] for index in members if jobs[index].get('capsule') is not None]
        own_tips = [capsule for index in members for capsule in jobs[index].get('tip_capsules', ())]
        return any(_hits_occupied(field, start, end, r, clearance_mm, skip=skip)
                   or _hits_tips(field, start, end, r, skip=own_tips)
                   for start, end, r in segments)

    def trunk_blocked(tx, ty, trunk_top, trunk_r):
        """Why a trunk of this radius does not fit, or None."""
        if trunk_top <= trunk_r:
            return 'trunk_too_short'
        if field.index_of(tx, ty) is None:
            return 'trunk_outside_field'
        if not _brace_clear(field, (tx, ty, 0.0), (tx, ty, trunk_top), trunk_r, clearance_mm, cancel):
            return 'trunk_hits_model'
        return None

    def keep_independent(group, reason):
        nonlocal independent
        if reason is not None:
            fallbacks[reason] = fallbacks.get(reason, 0) + len(group)
        for job in group:
            solids.append(cylinder_between((job['x'], job['y'], job['middle_z']),
                                           (job['x'], job['y'], job['base_z']), job['run_r']))
            pillars.append((job['x'], job['y'], job['base_z'], job['run_r']))
            feet.append((job['x'], job['y']))
            foot_radii.append(job['run_r'])
            independent += 1

    for members in clusters:
        group = [jobs[i] for i in members]
        if len(group) < 2:
            keep_independent(group, None)    # nothing to cluster with
            continue
        tx, ty = float(np.mean([job['x'] for job in group])), float(np.mean([job['y'] for job in group]))
        # Leave enough rise for every branch to meet the configured angle.
        trunk_top = min(job['base_z'] - math.hypot(job['x'] - tx, job['y'] - ty)
                        * branch_tangent for job in group)
        branch_r = max(job['run_r'] for job in group)
        # support.trunk_diameter_mm, never thinner than the branches it carries.
        run_r = max(trunk_setting_r, branch_r)
        reason = trunk_blocked(tx, ty, trunk_top, run_r)
        if reason is None:
            for job in group:
                if not _brace_clear(field, (tx, ty, trunk_top),
                                    (job['x'], job['y'], job['base_z']),
                                    job['run_r'], clearance_mm, cancel):
                    reason = 'branch_hits_model'
                    break
        tree_segments = [((tx, ty, 0.0), (tx, ty, trunk_top), run_r)] + [
            ((tx, ty, trunk_top), (job['x'], job['y'], job['base_z']), job['run_r'])
            for job in group]
        if reason is None and tree_hits(tree_segments, set(members)):
            reason = 'hits_support'
        if reason is not None:
            # Say when only the thicker trunk was in the way, so the report
            # points at trunk_diameter_mm rather than at the geometry.
            if run_r > branch_r + 1e-12 and reason in ('trunk_too_short', 'trunk_hits_model',
                                                       'hits_support'):
                thin = [((tx, ty, 0.0), (tx, ty, trunk_top), branch_r)] + tree_segments[1:]
                if (trunk_blocked(tx, ty, trunk_top, branch_r) is None
                        and not tree_hits(thin, set(members))
                        and all(_brace_clear(field, (tx, ty, trunk_top),
                                             (job['x'], job['y'], job['base_z']),
                                             job['run_r'], clearance_mm, cancel) for job in group)):
                    reason = 'trunk_diameter'
                    trunk_limited += 1
            keep_independent(group, reason)
            continue
        accepted.extend(tree_segments)
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
            'contacts_in_trees': contacts_in_trees, 'kept_independent': independent,
            'trunk_diameter_mm': 2 * trunk_setting_r,
            'trunk_basis': 'max(trunk_diameter_mm, the thickest branch diameter) per tree',
            'fallbacks': dict(sorted(fallbacks.items())),
            'fallback_basis': ('contacts kept on independent pillars, by why their cluster did not '
                               'become a tree; trunk_diameter means a trunk as thick as its '
                               'branches would have fitted'),
            'trunk_limited_clusters': trunk_limited}


def unbraced_lengths(graph):
    """Longest run of each vertical pillar between brace joints, foot and top.

    A pillar's buckling risk grows with the square of its unsupported length
    over its diameter, so the worst run, and that run over the pillar's
    diameter (its slenderness), say how well a plate is braced. A pillar is a
    chain of vertical edges joined end to end at one XY position; one that
    stands on the model is reported apart, because bracing only grounds a
    model pillar through its own bottom connector when
    support.brace_model_pillars is enabled, never through the plate.
    """
    positions = {node.id: np.asarray(node.position_mm, dtype=float) for node in graph.nodes}
    kinds = {node.id: node.kind for node in graph.nodes}
    parent = {}

    def find(name):
        while parent.setdefault(name, name) != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    vertical = []
    for edge in graph.edges:
        if edge.kind not in ('vertical', 'branched', 'tree_trunk', 'bottom', 'model_anchor'):
            continue
        a, b = positions.get(edge.start), positions.get(edge.end)
        if a is None or b is None or np.hypot(*(a[:2] - b[:2])) > 1e-6 or abs(a[2] - b[2]) < 1e-9:
            continue
        vertical.append(edge)
        parent[find(edge.start)] = find(edge.end)
    chains = {}
    for edge in vertical:
        chain = chains.setdefault(find(edge.start), {'z': [], 'braced': set(), 'radius': 0.0,
                                                     'anchored': False})
        chain['radius'] = max(chain['radius'], float(edge.radius_mm))
        chain['anchored'] |= edge.kind in ('bottom', 'model_anchor')
        for name in (edge.start, edge.end):
            chain['z'].append(float(positions[name][2]))
            if kinds[name] == 'brace_junction':
                chain['braced'].add(round(float(positions[name][2]), 6))
    result = {'basis': 'longest vertical run between brace joints, the foot and the top of each '
                       'pillar; pillars standing on the model are braced only when '
                       'support.brace_model_pillars is enabled'}
    for label, anchored in (('plate', False), ('model', True)):
        runs = []
        for chain in chains.values():
            if chain['anchored'] != anchored:
                continue
            stops = sorted({min(chain['z']), max(chain['z']), *chain['braced']})
            gap = max(upper - lower for lower, upper in zip(stops, stops[1:]))
            runs.append((gap, gap / (2 * chain['radius']), bool(chain['braced'])))
        if not runs:
            result[label] = {'pillars': 0}
            continue
        gaps = np.array([run[0] for run in runs])
        ratios = np.array([run[1] for run in runs])
        result[label] = {'pillars': len(runs), 'braced_pillars': int(sum(run[2] for run in runs)),
                         'max_mm': round(float(gaps.max()), 3),
                         'median_mm': round(float(np.median(gaps)), 3),
                         'max_slenderness': round(float(ratios.max()), 2)}
    return result


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
                                        slack_mm=math.hypot(field.grid.dx, field.grid.dy),
                                        cancel=cancel)
    return plan, raft


def attached_reach_mm(settings):
    """How close material one layer below must be for a contact to count as attached.

    Within a pillar's radius plus clearance of a wall no pillar can be routed
    anyway, and the overhang there cantilevers less than a pillar's width from
    material that is already printed.
    """
    support = settings['support']
    pillar = float(support['pillar_diameter_mm']) / 2
    tip_base = float(support.get('tip_base_diameter_mm') or 0.0) / 2
    return max(pillar, tip_base) + float(support['support_clearance_mm'])


def attached_below(triangles, contacts, settings, *, cancel=None, slack_mm=0.0):
    """Printer-pitch occupancy one layer below each contact, within ``attached_reach_mm``.

    The router uses a coarser analysis grid, so a sample on a near-vertical wall
    can sit just outside the occupied cell and still rest on material that the
    layer analysis will treat as connected, and a sample beside a wall has no
    room for a pillar. This asks on the printer lattice ``raster_connectivity``
    uses. Measurement, then a drop decision in
    ``apply_attached_unroutable_drops``; it does not emit geometry.
    ``slack_mm`` widens the reach by the routing grid's cell, whose clearance
    tests block that much earlier than the exact distance.
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
    reach = attached_reach_mm(settings) + float(slack_mm)
    reach_cols = max(1, int(math.ceil(reach / grid.dx)) + 1)
    reach_rows = max(1, int(math.ceil(reach / grid.dy)) + 1)
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
        r0, r1 = max(0, row - reach_rows), min(grid.height, row + reach_rows + 1)
        c0, c1 = max(0, col - reach_cols), min(grid.width, col + reach_cols + 1)
        window = cache_mask[r0:r1, c0:c1] != 0
        if window.any():
            rows, cols = np.nonzero(window)
            px = grid.x0 + (cols + c0 + .5) * grid.dx
            py = grid.y0 + (rows + r0 + .5) * grid.dy
            # One pixel of slack keeps the historical 3x3 neighbourhood inside.
            limit = reach + math.hypot(grid.dx, grid.dy)
            results[position] = bool(np.any((px - x) ** 2 + (py - y) ** 2 <= limit * limit))
        else:
            results[position] = False
    return results


def apply_attached_unroutable_drops(plan, triangles, settings, *, protected=(), cancel=None,
                                    slack_mm=0.0):
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
    attached = attached_below(triangles, positions, settings, cancel=cancel, slack_mm=slack_mm)
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
                details={'basis': 'printer-pitch material one layer below within the attach reach',
                         'attach_reach_mm': attached_reach_mm(settings),
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
                details={'basis': 'printer-pitch material one layer below within the attach reach',
                         'attach_reach_mm': attached_reach_mm(settings)}))
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
    cancel.check()
    grid = field.grid
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    x0, x1 = min(start[0], end[0]) - reach, max(start[0], end[0]) + reach
    y0, y1 = min(start[1], end[1]) - reach, max(start[1], end[1]) + reach
    c0 = max(0, int(math.floor((x0 - grid.x0) / grid.dx)))
    c1 = min(grid.width, int(math.ceil((x1 - grid.x0) / grid.dx)))
    r0 = max(0, int(math.floor((y0 - grid.y0) / grid.dy)))
    r1 = min(grid.height, int(math.ceil((y1 - grid.y0) / grid.dy)))
    if c1 <= c0 or r1 <= r0:
        return True
    rows, cols = np.meshgrid(np.arange(r0, r1), np.arange(c0, c1), indexing='ij')
    rows, cols = rows.ravel(), cols.ravel()
    px = grid.x0 + (cols + .5) * grid.dx
    py = grid.y0 + (rows + .5) * grid.dy
    direction = end[:2] - start[:2]
    length2 = float(direction @ direction)
    allowance = reach + math.hypot(grid.dx, grid.dy) / 2
    pad = np.full(len(px), reach)
    if length2 <= 1e-20:
        lateral = np.hypot(px - start[0], py - start[1])
        keep = lateral <= allowance
        t0 = np.zeros(len(px))
        t1 = np.ones(len(px))
        # A vertical capsule's end caps reach only sqrt(r^2 - d^2) past its
        # ends at lateral distance d; padding every cell by the full radius
        # made a shaft standing on a surface collide with that surface.
        nearest = np.maximum(0.0, lateral - math.hypot(grid.dx, grid.dy) / 2)
        pad = np.sqrt(np.maximum(0.0, reach ** 2 - np.minimum(nearest, reach) ** 2))
    else:
        t = ((px - start[0]) * direction[0] + (py - start[1]) * direction[1]) / length2
        distance2 = (px - (start[0] + t * direction[0])) ** 2 + (py - (start[1] + t * direction[1])) ** 2
        keep = distance2 <= allowance ** 2
        span = np.sqrt(np.maximum(0.0, allowance ** 2 - distance2) / length2)
        t0, t1 = np.maximum(0.0, t - span), np.minimum(1.0, t + span)
        keep &= t0 <= t1
    if not keep.any():
        return True
    rows, cols, px, py, t0, t1 = rows[keep], cols[keep], px[keep], py[keep], t0[keep], t1[keep]
    pad = pad[keep]
    z_a = start[2] + t0 * (end[2] - start[2])
    z_b = start[2] + t1 * (end[2] - start[2])
    z_lo, z_hi = np.minimum(z_a, z_b) - pad, np.maximum(z_a, z_b) + pad
    # Every cell whose runs meet the swept Z range, ignoring exclusions: the
    # common case is none, and then nothing below needs a Python loop.
    lo_q = np.maximum(0, np.floor((z_lo - field.z0) / field.dz).astype(np.int64))
    hi_q = np.floor((z_hi - field.z0) / field.dz).astype(np.int64) + 1
    columns = rows * grid.width + cols
    first, stop = field.ptr[columns].astype(np.int64), field.ptr[columns + 1].astype(np.int64)
    counts = stop - first
    hit = np.zeros(len(columns), dtype=bool)
    live = (hi_q > lo_q) & (counts > 0)
    for step in range(int(counts[live].max()) if live.any() else 0):
        index = first + step
        valid = live & (index < stop)
        safe = np.where(valid, index, 0)
        hit |= valid & (field.lo[safe] < hi_q) & (field.hi[safe] > lo_q)
    if not hit.any():
        return True
    if not exclude:
        return False
    for position in np.flatnonzero(hit):
        cancel.check()
        for part_lo, part_hi in _outside_exclude(px[position], py[position], z_lo[position],
                                                 z_hi[position], exclude):
            lo = max(0, field.layer_of(part_lo))
            hi = field.layer_of(part_hi) + 1
            if hi > lo and field.blocked(int(columns[position]), lo, hi):
                return False
    return True


def _outside_exclude(x, y, z_lo, z_hi, exclude):
    """Parts of ``[z_lo, z_hi]`` in column ``(x, y)`` outside every exclusion sphere.

    Each sphere removes only its own chord through the column, so material
    below or above an intended tip is still seen by the shaft passing it.
    """
    spans = [(z_lo, z_hi)]
    for center, radius in exclude or ():
        dx, dy = x - float(center[0]), y - float(center[1])
        half2 = float(radius) ** 2 - dx * dx - dy * dy
        if half2 < 0:
            continue
        half = math.sqrt(half2)
        cut_lo, cut_hi = float(center[2]) - half, float(center[2]) + half
        kept = []
        for lo, hi in spans:
            if cut_hi <= lo or cut_lo >= hi:
                kept.append((lo, hi))
                continue
            if lo < cut_lo:
                kept.append((lo, cut_lo))
            if cut_hi < hi:
                kept.append((cut_hi, hi))
        spans = kept
        if not spans:
            break
    return spans


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

    With ``support.brace_model_pillars`` on, a model-anchored pillar's own
    bottom connector also seeds grounding, so its vertical shaft is admitted
    the same way a plate foot admits a plate pillar's shaft; see the comments
    at the shaft-edge filter below for what stays excluded either way.
    """
    import heapq
    support = settings['support']
    cancel = cancel or CancellationToken()
    cancel.check()
    evidence = evidence if evidence is not None else {}
    for key in ('collision_rejected', 'support_collision_rejected', 'bounds_rejected', 'foot_rejected',
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
    #
    # 'model_anchor' is the kind route_contacts gives the vertical (or angled)
    # shaft of a pillar that anchors on the model, not just its buried bottom
    # connector — the two share a kind because both come from the same route.
    # brace_model_pillars lets that shaft into the grounding walk too, seeded
    # below from the top of the pillar's own bottom connector rather than from
    # a plate foot. 'small_model' (a whole thin model-to-model pillar) is left
    # excluded unconditionally: its one edge runs directly between a buried
    # 'model_anchor' foot and the top 'contact' node, so admitting it would
    # always mean grounding through a contact — exactly what this walk must
    # never do, with or without the option. A model pillar with no separate
    # bottom connector (bottom_used == 0) has the same shape for the same
    # reason: its shaft edge starts at the buried 'model_anchor' node itself,
    # which the node-kind filter below excludes regardless of the option.
    admit_model_pillars = bool(support.get('brace_model_pillars', False))
    excluded_shaft_kinds = (('tip', 'bottom', 'small_model') if admit_model_pillars
                            else ('tip', 'bottom', 'small_model', 'model_anchor'))
    shaft_edges = [edge for edge in graph.edges
                   if edge.kind not in excluded_shaft_kinds
                   and nodes[edge.start].kind not in ('contact', 'model_anchor')
                   and nodes[edge.end].kind not in ('contact', 'model_anchor')]
    grounded = {node.id for node in graph.nodes
                if node.kind == 'foot' and abs(node.position_mm[2]) <= 1e-8}
    if admit_model_pillars:
        # 'anchor_junction' is the node route_contacts places at the top of a
        # model pillar's own bottom connector, above its buried foot — the
        # same role a plate foot plays for a plate-grounded pillar.
        grounded.update(node.id for node in graph.nodes if node.kind == 'anchor_junction')
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
    # Edges kept out of shaft_edges above (model_anchor with the option off,
    # or the node-kind exclusions that always apply) are rejected for being
    # ungrounded by construction; a model_anchor edge admitted into
    # shaft_edges and then into admitted must not also be counted here.
    shaft_edge_ids = {id(edge) for edge in shaft_edges}
    evidence['ungrounded_rejected'] += len(remaining) + sum(
        edge.kind in ('model_anchor', 'small_model') and id(edge) not in shaft_edge_ids
        for edge in graph.edges)
    segments, origins, connected = [], [], {}
    # The two edges of a routed elbow, plate or model, are one primary
    # support. Carry its shoulder schedule and shared-connection interval
    # through that elbow.
    owners = list(range(len(admitted)))
    for index, edge in enumerate(admitted):
        if edge.kind not in ('branched', 'model_anchor'):
            continue
        for previous, other in enumerate(admitted[:index]):
            if other.kind == edge.kind and {edge.start, edge.end} & {other.start, other.end}:
                owners[index] = owners[previous]
                break
    shoulders = {}
    for owner, edge in zip(owners, admitted):
        shoulders[owner] = max(shoulders.get(owner, 0.), nodes[edge.start].position_mm[2],
                               nodes[edge.end].position_mm[2])
    # Minimum origin height is measured above each owner's own grounding
    # point, not the absolute plate height. A plate-grounded owner's foot
    # already sits at Z=0, so this reproduces the old plate-relative check
    # exactly. A model-standing owner's foot may sit well above Z=0 (partway
    # up the model), and measuring from the plate there would silently exempt
    # the pillar's whole height from the minimum instead of applying it
    # locally to its own run, which is what "minimum height above the
    # support's own base" is meant to mean.
    owner_floor = {}
    for owner, edge in zip(owners, admitted):
        floor = min(nodes[edge.start].position_mm[2], nodes[edge.end].position_mm[2])
        owner_floor[owner] = min(owner_floor.get(owner, floor), floor)
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

    def near_other(start, end, r, skip):
        # A capsule check, not a centreline one: a diagonal or foot stem that
        # passes beside another support fuses with it as surely as one that
        # crosses its axis, and the graph would never record that joint.
        start, end = np.asarray(start, dtype=float), np.asarray(end, dtype=float)
        # Contact tips are not brace segments; a brace must still miss them.
        if field is not None and _hits_tips(field, start, end, r):
            return True
        others = [segment for segment in segments if not segment['owners'] & skip]
        if not others:
            return False
        low = np.array([segment['start'] for segment in others])
        high = np.array([segment['end'] for segment in others])
        reach = np.array([segment['edge'].radius_mm for segment in others]) + r + clearance
        gaps = segment_distances(np.broadcast_to(start, low.shape), np.broadcast_to(end, low.shape),
                                  low, high)
        return bool(np.any(gaps < reach - 1e-7))

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
        if pattern != 'alternating' or state['rescue']:
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

    # In the rescue pass (below) an origin is tried once, not stepped down its
    # shaft, and neither the alternating direction nor the target's own
    # connection quota may veto it; every clearance check still applies.
    state = {'rescue': False}

    def drain():
        nonlocal added
        while origins:
            cancel.check()
            if evidence['origins_examined'] >= limit:
                evidence['capped'] = True
                return False
            evidence['origins_examined'] += 1
            negative_z, x, y, owner, lo_tuple, hi_tuple, shaft_r = heapq.heappop(origins)
            z = -negative_z
            lo, hi = np.asarray(lo_tuple), np.asarray(hi_tuple)
            origin = lo + (hi - lo) * ((z - lo[2]) / (hi[2] - lo[2]))
            next_z = z - interval
            if not state['rescue'] and next_z > lo[2] + 1e-6:
                next_point = lo + (hi - lo) * ((next_z - lo[2]) / (hi[2] - lo[2]))
                heapq.heappush(origins, (-next_z, float(next_point[0]), float(next_point[1]),
                                        owner, lo_tuple, hi_tuple, shaft_r))
            if z - owner_floor.get(owner, 0.) < minimum_height - 1e-8:
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
                        return False
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
                    if not state['rescue'] and close_interval(segment['owners'], destination[2]):
                        evidence['spacing_rejected'] += 1
                        continue
                    destination = np.asarray(destination)
                    if not inside(origin, destination, strut_r):
                        evidence['bounds_rejected'] += 1
                        continue
                    if field is not None and not _brace_clear(field, origin, destination, strut_r, clearance, cancel):
                        evidence['collision_rejected'] += 1
                        continue
                    if near_other(origin, destination, strut_r, {owner} | segment['owners']):
                        evidence['support_collision_rejected'] += 1
                        continue
                    if pattern == 'x':
                        if not attempt():
                            return False
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
                        if near_other(high, low, strut_r, {owner, target_owner}):
                            evidence['support_collision_rejected'] += 1
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
                                return False
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
                            if (near_other(origin, destination, strut_r, {owner})
                                    or near_other(bottom, destination, strut_r, {owner})):
                                evidence['support_collision_rejected'] += 1
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
        return True

    if not drain():
        return added
    # A pillar in a dense row can end up with no brace over most of its
    # height: every neighbour already has its one connection per interval
    # from its own origins, and the alternating rule forbids the rest. Retry
    # such a run at its schedule levels with those two rules relaxed. Seen on
    # the bracket, where one pillar stood 25.9 mm unbraced beside neighbours
    # braced every 5 mm.
    state['rescue'] = True
    for owner in sorted(shoulders):
        floor, top = owner_floor.get(owner, 0.), shoulders[owner]
        joints = [height for entry in connected.get(owner, ()) for height in entry]
        stops = sorted({floor, top, *joints})
        for lower_z, upper_z in zip(stops, stops[1:]):
            if upper_z - lower_z <= 2 * interval + 1e-8:
                continue
            level = top - math.ceil((top - upper_z + 1e-8) / interval) * interval
            while level > lower_z + 1e-6:
                if level - floor >= minimum_height - 1e-8:
                    for segment in segments:
                        if owner not in segment['owners'] or segment['edge'].kind in ('brace', 'brace_foot'):
                            continue
                        lo, hi = sorted((segment['start'], segment['end']), key=lambda point: point[2])
                        if lo[2] + 1e-6 < level < hi[2] - 1e-6:
                            point = lo + (hi - lo) * ((level - lo[2]) / (hi[2] - lo[2]))
                            heapq.heappush(origins, (-float(level), float(point[0]), float(point[1]), owner,
                                                    tuple(lo), tuple(hi), float(segment['edge'].radius_mm)))
                            break
                level -= interval
    drain()
    return added


#: Unroutable positions listed in the ``incomplete_support_routes`` diagnostic.
INCOMPLETE_EXAMPLES = 16


def _reasons_after_drops(plan):
    """Unroutable counts by reason for the contacts still failed after drops."""
    reasons = {}
    for diagnostic in plan.diagnostics:
        if diagnostic.code == 'support_unroutable':
            reason = (diagnostic.details or {}).get('reason', 'unknown')
            reasons[reason] = reasons.get(reason, 0) + 1
    # Diagnostics are capped; the metric counted every failure before drops.
    failed = int(plan.metrics.get('contacts_failed', 0) or 0)
    listed = sum(reasons.values())
    if failed > listed:
        reasons['not_itemized'] = failed - listed
    return dict(sorted(reasons.items()))


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
        positions = list(plan.metrics.get('unroutable_positions') or [])
        reasons = _reasons_after_drops(plan)
        summary = ', '.join(f'{count} {reason}' for reason, count in
                            sorted(reasons.items(), key=lambda item: (-item[1], item[0])))
        parts = []
        if failed:
            parts.append(f'{failed} contact(s) have no route' + (f' ({summary})' if summary else ''))
        if sealed:
            parts.append(f'{sealed} lie in sealed cavities')
        report.diagnostics.append(Diagnostic(
            'incomplete_support_routes',
            'Some requested contacts could not be routed or removed: ' + '; '.join(parts),
            position_mm=[float(v) for v in positions[0]] if positions else None,
            details={'unroutable': failed, 'sealed': sealed, 'by_reason': reasons,
                     'positions_mm': [[round(float(v), 4) for v in point]
                                      for point in positions[:INCOMPLETE_EXAMPLES]],
                     'positions_truncated': len(positions) > INCOMPLETE_EXAMPLES}))
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
