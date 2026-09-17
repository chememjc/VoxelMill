"""Pixel-center, nonzero-winding scan conversion of STL triangle soups.

The crop is aligned to the physical printer grid, never downsampled. Masks use
[y,x] indexing with positive Y up; image mirroring is a later format concern.

When ``process.antialias_levels`` is 2 or 4, occupancy is coverage grayscale:
the same Rasterizer runs at ``levels`` times the printer pitch (same origin),
then each levels×levels block is box-averaged into 0–255. This is supersample
coverage, not a blur. Levels 1 keeps binary 0/1 occupancy.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
from .contracts import CancellationToken, VoxelMillError, Diagnostic, Layer, ResourceBudget, no_progress

@dataclass(frozen=True)
class RasterGrid:
    width: int
    height: int
    x0: float
    y0: float
    dx: float
    dy: float
    column_offset: int = 0
    row_offset: int = 0
    def xy(self, row, column):
        return [self.x0 + (column + .5)*self.dx, self.y0 + (row + .5)*self.dy]

    @classmethod
    def for_bounds(cls, bounds, settings, crop=True):
        p = settings['printer']
        w, h = p['pixels']
        dx, dy = p['pixel_pitch_mm']
        x0, y0 = -p['build_mm'][0]/2, -p['build_mm'][1]/2
        if not crop:
            return cls(w,h,x0,y0,dx,dy)
        # One empty border where plate limits permit it aids exterior tracking.
        xmin,ymin,_ = bounds[0]; xmax,ymax,_ = bounds[1]
        c0 = max(0, min(w-1, math.floor((xmin-x0)/dx)-1))
        r0 = max(0, min(h-1, math.floor((ymin-y0)/dy)-1))
        c1 = max(c0+1, min(w, math.ceil((xmax-x0)/dx)+1))
        r1 = max(r0+1, min(h, math.ceil((ymax-y0)/dy)+1))
        return cls(c1-c0,r1-r0,x0+c0*dx,y0+r0*dy,dx,dy,c0,r0)


def resolve_layer_range(layer_range, layer_count):
    """Clamp an inclusive absolute ``(first, last)`` request to the build.

    ``None`` means the whole build. Indices stay absolute: a partial scan must
    not renumber its layers, because every diagnostic's layer number is read
    against the printed build, not against the window that produced it.
    """
    if layer_range is None:
        return 0, layer_count - 1
    first, last = (int(v) for v in layer_range)
    if last < first:
        raise VoxelMillError('layer_range', 'layer_range last index precedes its first index',
                             {'layer_range': [first, last]})
    return max(0, first), min(layer_count - 1, last)


def antialias_factor(settings):
    """Return 1 (binary), 2, or 4 from ``process.antialias_levels``."""
    return int(settings.get('process', {}).get('antialias_levels', 1))


def box_average_coverage(fine_mask, levels):
    """Box-average each levels×levels binary block into uint8 coverage 0–255.

    Occupied fraction 1.0 maps to 255. ``fine_mask`` is 0/1 occupancy at the
    supersampled lattice; shape must be an exact multiple of ``levels``.
    """
    levels = int(levels)
    if levels <= 1:
        return np.asarray(fine_mask, dtype=np.uint8)
    fine = np.asarray(fine_mask)
    height, width = fine.shape
    if height % levels or width % levels:
        raise VoxelMillError('antialias_shape',
                        'Supersampled mask is not an exact levels×levels multiple',
                        {'shape': [height, width], 'levels': levels})
    blocks = fine.reshape(height // levels, levels, width // levels, levels)
    occupied = blocks != 0
    total = float(levels * levels)
    # Round half-up via rint so full coverage is exactly 255.
    return np.rint(occupied.sum(axis=(1, 3), dtype=np.float64) * (255.0 / total)).astype(np.uint8)


def require_supersample_buffer(budget, grid, levels, operation='antialias supersample buffer'):
    """Refuse AA that does not fit rather than silently falling back to binary."""
    levels = int(levels)
    if levels <= 1:
        return 0
    needed = int(grid.width) * levels * int(grid.height) * levels
    budget.require(needed, operation)
    return needed


def slice_coverage(native, z, grid, levels, cancel_check, rule='nonzero', *, budget=None):
    """Raster one Z plane; supersample+box-average when ``levels`` > 1.

    Same origin as ``grid``; fine pitch is ``dx/levels``, ``dy/levels``. When
    ``budget`` is set, the supersampled buffer is admitted before allocation.
    """
    levels = int(levels)
    if levels <= 1:
        return native.slice(float(z), grid.width, grid.height, grid.x0, grid.y0,
                            grid.dx, grid.dy, cancel_check, rule)
    if budget is not None:
        require_supersample_buffer(budget, grid, levels)
    fine_w = grid.width * levels
    fine_h = grid.height * levels
    result = native.slice(float(z), fine_w, fine_h, grid.x0, grid.y0,
                          grid.dx / levels, grid.dy / levels, cancel_check, rule)
    coverage = box_average_coverage(result['mask'], levels)
    out = dict(result)
    out['mask'] = coverage
    return out


class MeshLayerStream:
    """Pixel-center layers for a placed model, one at a time.

    The default ``nonzero`` winding rule tolerates inverted components,
    overlapping shells and self-intersections, which every supplied original
    fixture contains. Rows whose contour does not close are counted in
    ``open_rows`` and surfaced by :meth:`diagnostics`; they are never filled to
    the crop edge, so an open mesh under-fills rather than inventing material.
    Set ``strict=True`` to refuse an unsliceable mesh instead.

    A single soup cannot separate model from support occupancy. When AA is on
    and ``process.antialias_supports`` is false, the whole mask is still
    grayscaled and ``antialias_supports_unseparated`` is set so the report can
    say so.

    ``layer_range`` is an inclusive ``(first_index, last_index)`` pair of
    absolute build layer indices, or ``None`` for the whole build. Only that
    range is yielded and every ``Layer.index`` stays absolute, so a diagnostic
    taken from a partial scan still names the layer the printer will expose.
    Island detection compares a layer against the one below it, so a caller
    that wants a correct verdict at layer ``lo`` must ask for ``lo - 1`` as the
    first index: ``analyze_layers`` skips the check on the first layer it sees
    because that layer has no predecessor to compare against.
    """
    def __init__(self, triangles, bounds, settings, *, budget=None, cancel=None, progress=no_progress,
                 crop=True, rule='nonzero', strict=False, layer_range=None):
        from . import _native
        self.cancel = cancel or CancellationToken()
        self.budget = budget or ResourceBudget(**settings['resources'])
        self.settings = settings
        self.grid = RasterGrid.for_bounds(bounds,settings,crop)
        self.layer_height = settings['process']['layer_height_mm']
        self.layer_count = max(0, math.ceil(bounds[1][2]/self.layer_height))
        self.first_index, self.last_index = resolve_layer_range(layer_range, self.layer_count)
        self.scan_count = max(0, self.last_index - self.first_index + 1)
        self.rule = rule
        self.strict = strict
        self.antialias_levels = antialias_factor(settings)
        self.antialias_supports = bool(settings['process'].get('antialias_supports', False))
        # Single soup: cannot keep supports binary separately.
        self.antialias_supports_unseparated = (
            self.antialias_levels > 1 and not self.antialias_supports)
        # Includes labels, distance fields, previous layer, and triangle index.
        self.budget.require(self.grid.width*self.grid.height*72+len(triangles)*80+192*1024**2,'raster validation')
        require_supersample_buffer(self.budget, self.grid, self.antialias_levels)
        self.cancel.check()
        self.native = _native.Rasterizer(triangles,self.cancel.check)
        self.progress = progress
        self.consumed = False
        self.open_rows = 0
        self.open_layers = 0
        self.negative_winding_crossings = 0
        self.filled_pixels = 0
    def __iter__(self):
        if self.consumed:
            raise VoxelMillError('stream_consumed','Create a fresh layer stream for another pass')
        self.consumed = True
        # ``layer_count`` may be lowered after construction to clip an export to
        # the machine height, so the window is resolved when iteration starts
        # rather than when the stream was built.
        last = min(self.last_index, self.layer_count - 1)
        self.scan_count = max(0, last - self.first_index + 1)
        scanned = 0
        for index in range(self.first_index, last + 1):
            self.cancel.check()
            z=(index+.5)*self.layer_height
            g=self.grid
            result = slice_coverage(self.native, z, g, self.antialias_levels,
                                    self.cancel.check, self.rule, budget=self.budget)
            if result['odd_rows']:
                if self.strict:
                    raise VoxelMillError('open_slice','Unpaired scanline intersections; mesh is not sliceable',{'layer':index,'odd_rows':result['odd_rows']})
                self.open_rows += int(result['odd_rows'])
                self.open_layers += 1
            self.negative_winding_crossings += int(result['negative_winding_crossings'])
            self.filled_pixels += int(result['filled_pixels'])
            scanned += 1
            self.progress('raster',scanned,self.scan_count)
            yield Layer(index,z,result['mask'])
    def diagnostics(self):
        """Slice-level evidence; empty when every contour closed."""
        if not self.open_rows:
            return []
        return [Diagnostic('open_contours',
                           'Scanline contours did not close; the source is not a closed surface',
                           severity='error',
                           details={'open_rows': self.open_rows, 'open_layers': self.open_layers,
                                    'layers': self.layer_count, 'rule': self.rule})]
