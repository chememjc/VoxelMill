"""Shared exact assembly and explicitly reported, grouped raster fallback.

The raster path preserves authored model triangles. Its STL is a soup carrier;
only comparison with a fresh grouped raster establishes what that carrier slices
into. It is never presented as a repaired or boolean-resolved solid.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np

from . import geometry
from .contracts import CancellationToken, VoxelMillError, Diagnostic, Layer, ResourceBudget, no_progress
from .raster import (RasterGrid, antialias_factor, require_supersample_buffer,
                     resolve_layer_range, slice_coverage)
from .repair import voxel_repair

EXACT_FAILURES = frozenset(('degenerate_triangles', 'weld_rejected', 'invalid_solid',
                            'self_intersections', 'union_failed'))


@dataclass
class PartGroup:
    name: str
    triangles: np.ndarray
    orientation: str


@dataclass
class PreparedModel:
    triangles: np.ndarray
    solid: object
    settings: dict
    repair: dict
    cavity_fill: dict | None
    exact_union_attempted: bool
    blocked_by: list

    @property
    def bounds(self):
        return geometry.triangle_bounds(self.triangles)


def prepare_model(triangles, settings, *, budget=None, cancel=None, progress=no_progress,
                  source_volume=None):
    """Apply one ingestion/repair policy for both the CLI and the editor."""
    cancel = cancel or CancellationToken()
    budget = budget or ResourceBudget(**settings['resources'])
    cancel.check()
    policy = settings['repair']['aggressiveness']
    attempted, solid, blocked = policy != 'none', None, []
    repair = {'operation': 'none', 'max_displacement_mm': 0.0,
              'volumetric_repair': 'not_run'}
    if policy == 'aggressive':
        solid, repair = voxel_repair(triangles, geometry.triangle_bounds(triangles, cancel=cancel),
                                     settings, budget=budget, cancel=cancel, progress=progress,
                                     source_volume_mm3=source_volume)
    elif policy == 'none':
        blocked.append({'code': 'repair_disabled', 'message': 'repair=none uses authored triangles without solid conversion'})
    else:
        try:
            solid, repair = geometry.mesh_to_manifold(triangles, budget, settings['repair'], cancel)
        except VoxelMillError as error:
            if error.code not in EXACT_FAILURES:
                raise
            blocked.append(error.to_dict())
    if solid is None and settings['assembly']['union'] == 'exact':
        raise VoxelMillError('exact_union_unavailable', 'Exact union required but model solid conversion is unavailable',
                        {'findings': blocked, 'exact_union_attempted': attempted})
    cavity = None
    if settings['repair']['seal_voids']:
        if solid is None:
            cavity = {'status': 'not_run',
                      'reason': 'the raster union path has no solid to decompose; seal_voids requires the exact path or repair.aggressiveness = "aggressive"',
                      'consequence': 'enclosed voids are reported by the layer analysis rather than filled'}
        else:
            solid, cavity = geometry.fill_enclosed_cavities(solid)
    model_triangles = (geometry.manifold_triangles(solid).astype(np.float32)
                       if solid is not None else triangles)
    return PreparedModel(model_triangles, solid, settings, repair, cavity, attempted, blocked)


@dataclass
class Assembly:
    groups: tuple[PartGroup, ...]
    solid: object
    settings: dict
    report: dict

    @property
    def triangle_arrays(self):
        if self.solid is not None:
            return (geometry.manifold_triangles(self.solid),)
        return tuple(g.triangles for g in self.groups)

    @property
    def bounds(self):
        if self.solid is not None:
            return np.asarray(self.solid.bounding_box()).reshape(2, 3)
        bounds = np.asarray([geometry.triangle_bounds(g.triangles) for g in self.groups])
        return np.stack((bounds[:, 0].min(axis=0), bounds[:, 1].max(axis=0)))

    def num_tri(self):
        return int(self.solid.num_tri()) if self.solid is not None else sum(len(g.triangles) for g in self.groups)

    def soup_triangles(self, budget):
        """Bound the single-array allocation required by coarse drainage."""
        budget.require(self.num_tri() * 160 + 192 * 1024**2, 'assembly drainage triangles')
        arrays = self.triangle_arrays
        return arrays[0] if len(arrays) == 1 else np.concatenate(arrays)

    def bounding_box(self):
        return self.bounds.ravel().tolist()

    def volume(self):
        return float(self.solid.volume()) if self.solid is not None else None

    def diagnostics(self):
        if self.solid is not None:
            return []
        return [Diagnostic('exact_union_unavailable',
                           'Raster union used; exported STL is a triangle soup, not a certified solid',
                           severity='warning', details=self.report)]


def assemble(model, support_solids, raft, *, budget=None, cancel=None, exact=True):
    """Try the exact union; otherwise OR model and trusted generated occupancy.

    ``exact=False`` skips the boolean union and returns the grouped raster
    assembly only. The island scan reads nothing but the groups, so a search
    pass that is not the final answer need not pay for the union.
    """
    import manifold3d as m
    cancel = cancel or CancellationToken()
    budget = budget or ResourceBudget(**model.settings['resources'])
    cancel.check()
    generated = [*support_solids] + ([raft] if raft is not None else [])
    # Extraction yields float64 arrays, concatenation and float32 conversion
    # briefly coexist. Account for them before allocating the trusted group.
    budget.require(len(model.triangles) * 36 + sum(s.num_tri() for s in generated) * 180
                   + 192 * 1024**2, 'assembly triangle groups')
    groups = [PartGroup('model', model.triangles, 'untrusted' if model.solid is None else 'accepted_solid')]
    if generated:
        groups.append(PartGroup('supports_and_raft',
                                np.concatenate([geometry.manifold_triangles(s) for s in generated]).astype(np.float32),
                                'closed_positive'))
    solid, blocked = None, list(model.blocked_by)
    if model.solid is not None and exact:
        try:
            solid = m.Manifold.batch_boolean([model.solid, *generated], m.OpType.Add)
        except ValueError as error:
            blocked.append({'code': 'union_failed', 'message': str(error)})
        cancel.check()
        if solid is not None and (solid.status() != m.Error.NoError or solid.is_empty()):
            blocked.append({'code': 'union_failed', 'manifold_status': str(solid.status())})
            solid = None
    if exact and solid is None and model.settings['assembly']['union'] == 'exact':
        raise VoxelMillError('exact_union_unavailable', 'Exact boolean union is unavailable', {'findings': blocked})
    support_fill = None
    if model.settings['repair'].get('support_void_policy') == 'fill':
        if solid is not None:
            # All enclosed shells after union, including support-created pockets.
            # Drainage necks / tip crevices are not closed shells and stay.
            solid, support_fill = geometry.fill_enclosed_cavities(solid)
        else:
            support_fill = {
                'status': 'not_run',
                'operation': 'fill_enclosed_shells',
                'reason': 'support_void_policy=fill needs an exact union solid to decompose',
                'consequence': 'enclosed voids remain reported by layer analysis; drainage necks are never filled by this policy',
            }
    report = {'union': 'exact' if solid is not None else 'raster',
              'exact_union_attempted': model.exact_union_attempted,
              'exact_union_blocked_by': blocked,
              'cavity_fill': model.cavity_fill,
              'support_cavity_fill': support_fill,
              'groups': [{'name': g.name, 'triangles': len(g.triangles), 'orientation': g.orientation} for g in groups],
              'max_displacement_mm': 0.0,
              'establishes': 'boolean-resolved solid' if solid is not None else 'independent occupancy OR on the printer lattice; exported carrier requires raster parity',
              'does_not_establish': 'physical printability' if solid is not None else 'the exported STL is not a certified valid closed solid; it is not boolean-resolved or repaired'}
    return Assembly(tuple(groups), solid, model.settings, report)


class UnionLayerStream:
    """One layer at a time, without signed-winding cancellation between groups.

    With ``process.antialias_levels`` > 1, model groups receive coverage
    grayscale. Support/raft groups stay binary unless
    ``process.antialias_supports`` is true, then OR'd as full 255 onto the
    model coverage. A soup with no separate support group sets
    ``antialias_supports_unseparated`` when supports would otherwise stay binary.

    ``layer_range`` is an inclusive ``(first_index, last_index)`` pair of
    absolute build layer indices, or ``None`` for the whole build. Only that
    range is yielded and every ``Layer.index`` stays absolute, so a finding from
    a partial scan still names the layer the printer will expose. Island
    detection compares a layer against the one below it, so a caller that wants
    a correct verdict at layer ``lo`` must ask for ``lo - 1`` as the first
    index; ``analyze_layers`` skips the check on the first layer it sees,
    having no predecessor to compare it against.
    """
    def __init__(self, groups, bounds, settings, *, grid=None, layer_count=None,
                 layer_range=None, budget=None, cancel=None, progress=no_progress):
        from . import _native
        self.cancel = cancel or CancellationToken()
        self.budget = budget or ResourceBudget(**settings['resources'])
        self.settings = settings
        self.grid = grid if grid is not None else RasterGrid.for_bounds(bounds, settings)
        self.layer_height = settings['process']['layer_height_mm']
        self.layer_count = (max(0, math.ceil(bounds[1][2] / self.layer_height))
                            if layer_count is None else layer_count)
        self.first_index, self.last_index = resolve_layer_range(layer_range, self.layer_count)
        self.scan_count = max(0, self.last_index - self.first_index + 1)
        groups = tuple(groups)
        if not groups:
            raise VoxelMillError('invalid_mesh', 'Raster union needs at least one group')
        self.groups = groups
        self.antialias_levels = antialias_factor(settings)
        self.antialias_supports = bool(settings['process'].get('antialias_supports', False))
        self._support_names = frozenset({'supports_and_raft', 'supports', 'raft'})
        has_support = any(g.name in self._support_names or g.name.startswith('support')
                          for g in groups)
        has_model = any(g.name == 'model' or g.name not in self._support_names
                        for g in groups)
        # Separated model+support lets us keep tips binary; a single group cannot.
        self.antialias_supports_unseparated = (
            self.antialias_levels > 1 and not self.antialias_supports
            and not (has_support and has_model and len(groups) > 1))
        self.budget.require(self.grid.width * self.grid.height * 74 +
                            sum(len(g.triangles) for g in groups) * 80 + 192 * 1024**2,
                            'grouped raster validation')
        if self.antialias_levels > 1:
            require_supersample_buffer(self.budget, self.grid, self.antialias_levels)
        self.cancel.check()
        self.native = [_native.Rasterizer(g.triangles, self.cancel.check) for g in groups]
        self.progress = progress
        self.consumed = False
        self.open_rows = self.open_layers = self.negative_winding_crossings = self.filled_pixels = 0

    def _group_levels(self, group):
        if self.antialias_levels <= 1:
            return 1
        is_support = (group.name in self._support_names or group.name.startswith('support'))
        if is_support and not self.antialias_supports:
            return 1
        return self.antialias_levels

    def __iter__(self):
        if self.consumed:
            raise VoxelMillError('stream_consumed', 'Create a fresh layer stream for another pass')
        self.consumed = True
        g = self.grid
        levels = self.antialias_levels
        last = min(self.last_index, self.layer_count - 1)
        self.scan_count = max(0, last - self.first_index + 1)
        scanned = 0
        # Binary groups on an AA lattice OR in as full intensity, not occupancy 1.
        fill = 255 if levels > 1 else 1
        for index in range(self.first_index, last + 1):
            self.cancel.check()
            z = (index + .5) * self.layer_height
            mask = None
            open_rows = 0
            for group, native in zip(self.groups, self.native):
                group_levels = self._group_levels(group)
                if group_levels > 1:
                    if mask is None:
                        mask = np.zeros((g.height, g.width), dtype=np.uint8)
                    result = slice_coverage(native, z, g, group_levels,
                                            self.cancel.check, 'nonzero', budget=self.budget)
                    np.maximum(mask, result['mask'], out=mask)
                else:
                    if mask is None:
                        mask = np.empty((g.height, g.width), dtype=np.uint8)
                        combine = 'replace'
                    else:
                        combine = 'or'
                    result = native.slice_into(z, mask, g.x0, g.y0, g.dx, g.dy,
                                               self.cancel.check, 'nonzero', combine, fill)
                open_rows += int(result['odd_rows'])
                self.negative_winding_crossings += int(result['negative_winding_crossings'])
            self.open_rows += open_rows
            self.open_layers += bool(open_rows)
            self.filled_pixels += int(np.count_nonzero(mask))
            scanned += 1
            self.progress('union_raster', scanned, self.scan_count)
            yield Layer(index, z, mask)

    def diagnostics(self):
        if not self.open_rows:
            return []
        return [Diagnostic('open_contours', 'A raster union group has unclosed contours',
                           details={'open_rows': self.open_rows, 'open_layers': self.open_layers,
                                    'layers': self.layer_count})]


class RasterParity:
    """Compare reopened bytes against a fresh grouped raster on its exact grid."""
    def __init__(self, actual, assembly, *, budget, cancel):
        self.actual = actual
        self.assembly = assembly
        # Both triangle indexes are live together; admission must cover both.
        budget.require(actual.grid.width * actual.grid.height * 76 +
                       assembly.num_tri() * 160 + 192 * 1024**2, 'raster parity')
        self.reference = UnionLayerStream(assembly.groups, assembly.bounds, assembly.settings,
                                          grid=actual.grid, layer_count=actual.layer_count,
                                          budget=budget, cancel=cancel)
        self.mismatched_pixels = self.mismatched_layers = 0
        self.examples = []

    def __iter__(self):
        limit = self.assembly.settings['assembly']['max_parity_examples']
        for layer, expected in zip(self.actual, self.reference):
            mismatch = layer.mask != expected.mask
            count = int(np.count_nonzero(mismatch))
            if count:
                self.mismatched_layers += 1
                self.mismatched_pixels += count
                # Scan rows to avoid allocating coordinates for a whole LCD.
                for row in range(mismatch.shape[0]):
                    if len(self.examples) >= limit:
                        break
                    for col in np.flatnonzero(mismatch[row])[:limit - len(self.examples)]:
                        self.examples.append({'layer': layer.index, 'pixel': [row, int(col)],
                                              'position_mm': [*self.actual.grid.xy(row, int(col)), layer.z_mm]})
            yield layer

    def apply(self, report):
        metrics = {'mismatched_pixels': self.mismatched_pixels,
                   'mismatched_layers': self.mismatched_layers, 'examples': self.examples,
                   'reference_open_rows': self.reference.open_rows}
        report.metrics['union_raster_parity'] = metrics
        report.checks['union_raster_parity'] = 'fail' if self.mismatched_pixels else 'pass'
        if self.mismatched_pixels:
            report.diagnostics.append(Diagnostic('union_raster_parity',
                'Exported soup differs from grouped occupancy; overlapping windings may cancel',
                position_mm=self.examples[0]['position_mm'] if self.examples else None, details=metrics))
        report.diagnostics.extend(self.reference.diagnostics())
        if self.reference.open_rows:
            report.checks['closed_surface'] = 'fail'
