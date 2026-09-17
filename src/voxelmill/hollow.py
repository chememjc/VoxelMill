"""Voxel hollowing, drain/vent holes, wall-thickness analysis, and lattice infill.

Hollowing reuses the repair occupancy path: rasterize, erode by wall thickness,
then either Manifold-subtract the eroded core from a solid or remesh the shell
occupancy for soup input. Drain and vent cylinders are paired per enclosed void.
Infill is sampled on the same grid and unioned before the holes punch through.
"""
from __future__ import annotations

import math
import time

import numpy as np
from scipy import ndimage as ndi

from . import geometry
from .assembly import PreparedModel, prepare_model
from .contracts import CancellationToken, VoxelMillError, ResourceBudget, no_progress
from .repair import _grid

CROSS3 = ndi.generate_binary_structure(3, 1)
MAX_VOXEL_BYTES_FRACTION = 0.35


def choose_hollow_voxel_size(bounds, settings, budget):
    """Pitch that resolves the wall with several voxels, within the memory budget."""
    hollow = settings['hollow']
    wall = float(hollow['wall_thickness_mm'])
    requested = float(hollow.get('voxel_size_mm') or 0.0)
    derived = max(wall / 4.0, 1e-3)
    size = requested if requested > 0 else derived
    if not math.isfinite(size) or size <= 0:
        raise VoxelMillError('invalid_hollow', 'hollow.voxel_size_mm must be positive or 0 to derive',
                        {'voxel_size_mm': hollow.get('voxel_size_mm')})
    ceiling = budget.memory_gib * 1024**3 * MAX_VOXEL_BYTES_FRACTION
    _, dims = _grid(bounds, size)
    needed = float(np.prod(dims + 2))
    if needed <= ceiling:
        return size, dims, needed
    fitting = size * (needed / ceiling) ** (1 / 3)
    if requested > 0:
        raise VoxelMillError('hollow_budget', 'Hollow voxel grid exceeds the memory budget', {
            'voxel_size_mm': size, 'voxel_bytes': needed, 'budget_bytes': ceiling,
            'smallest_affordable_voxel_size_mm': fitting,
            'remedy': 'raise hollow.voxel_size_mm or resources.memory_gib'})
    # Derived: coarsen, but keep at least one voxel across the wall when possible.
    max_pitch = max(wall / 2.0, derived)
    _, dims_cap = _grid(bounds, max_pitch)
    needed_cap = float(np.prod(dims_cap + 2))
    if needed_cap > ceiling:
        raise VoxelMillError('hollow_budget', 'Hollow voxel grid exceeds the memory budget', {
            'voxel_size_mm': size, 'voxel_bytes': needed, 'budget_bytes': ceiling,
            'smallest_affordable_voxel_size_mm': fitting})
    lo, hi = size, max_pitch
    best_dims, best_needed = dims_cap, needed_cap
    for _ in range(48):
        mid = (lo + hi) * 0.5
        _, mid_dims = _grid(bounds, mid)
        mid_needed = float(np.prod(mid_dims + 2))
        if mid_needed <= ceiling:
            hi = mid
            best_dims, best_needed = mid_dims, mid_needed
        else:
            lo = mid
    return hi, best_dims, best_needed


def _voxelize(triangles, bounds, size, cancel, progress, budget):
    from . import _native
    low, dims = _grid(bounds, size)
    nx, ny, nz = (int(v) for v in dims)
    budget.require(int(np.prod(dims + 2)) + len(triangles) * 96, 'hollow occupancy')
    volume = _native.VoxelVolume(nx, ny, nz, float(low[0]), float(low[1]), float(low[2]),
                                 size, size, size)
    raster = _native.Rasterizer(np.asarray(triangles), cancel.check)
    occupancy = np.zeros((nz, ny, nx), dtype=bool)
    odd_rows = filled = 0
    for k in range(nz):
        cancel.check()
        result = raster.slice(float(low[2] + (k + 0.5) * size), nx, ny,
                              float(low[0]), float(low[1]), size, size, cancel.check, 'nonzero')
        odd_rows += int(result['odd_rows'])
        mask = result['mask'] != 0
        occupancy[k] = mask
        filled += int(mask.sum())
        volume.set_slice(k, result['mask'])
        progress('hollow_voxelize', k + 1, nz)
    return occupancy, volume, low, (nx, ny, nz), odd_rows, filled


def _occupancy_to_volume(occupancy, low, size):
    from . import _native
    nz, ny, nx = occupancy.shape
    volume = _native.VoxelVolume(nx, ny, nz, float(low[0]), float(low[1]), float(low[2]),
                                 size, size, size)
    for k in range(nz):
        volume.set_slice(k, occupancy[k].astype(np.uint8))
    return volume


def _surface_solid(occupancy, low, size, cancel):
    """Well-composed boundary of an occupancy mask as a Manifold solid."""
    from . import _native
    m = geometry._manifold()
    if not occupancy.any():
        raise VoxelMillError('hollow_empty', 'Hollow occupancy produced an empty volume')
    volume = _occupancy_to_volume(occupancy, low, size)
    composed = volume.make_well_composed(64, lambda stage, done, total: cancel.check())
    if composed['boundary_blocked']:
        raise VoxelMillError('hollow_boundary', 'Hollow remesh reached the volume border',
                        dict(composed))
    if not composed['converged']:
        raise VoxelMillError('hollow_not_converged', 'Hollow well-composedness did not converge',
                        dict(composed))
    vertices, faces = volume.extract_surface(lambda stage, done, total: cancel.check())
    if not len(faces):
        raise VoxelMillError('hollow_empty', 'Hollow remesh produced no surface')
    solid = m.Manifold(m.Mesh64(np.ascontiguousarray(vertices, dtype=np.float64),
                                np.ascontiguousarray(faces, dtype=np.uint64)))
    if solid.status() != m.Error.NoError or solid.is_empty() or not solid.volume() > 0:
        raise VoxelMillError('hollow_invalid_solid', 'Hollow remesh was not a valid solid',
                        {'manifold_status': str(solid.status())})
    return solid, composed


def wall_thickness_field(occupancy, pitch_mm):
    """Local wall thickness from the solid distance transform.

    ``distance_transform_edt`` measures center-to-center distance to empty
    voxels. Subtract half a pitch so the value is a face-boundary distance;
    thickness at medial samples is ``2 * half``.

    Outer-corner voxels of a thick shell are local EDT maxima but are not on
    the medial axis; samples are taken from the once-eroded interior when that
    exists, and from the whole component when the feature is only one or two
    voxels thick.
    """
    pitch = float(pitch_mm)
    if not occupancy.any() or occupancy.all():
        return {
            'min_thickness_mm': 0.0 if occupancy.any() else None,
            'max_thickness_mm': None,
            'histogram': {'edges_mm': [], 'counts': []},
            'regions_below': [],
            'medial_samples': 0,
            'method': '2 * (solid EDT - pitch/2) at interior medial samples',
            'pitch_mm': pitch,
        }
    edt = ndi.distance_transform_edt(occupancy, sampling=pitch)
    half = np.maximum(edt - 0.5 * pitch, 0.0)
    dilated = ndi.maximum_filter(half, footprint=CROSS3)
    labels, count = ndi.label(occupancy, CROSS3)
    medial = np.zeros_like(occupancy, dtype=bool)
    for label in range(1, count + 1):
        component = labels == label
        interior = component & ndi.binary_erosion(component, structure=CROSS3)
        domain = interior if interior.any() else component
        local = domain & (half > 0) & (half >= dilated - 1e-15)
        if not local.any():
            local = domain & (half > 0)
        medial |= local
    samples = half[medial]
    if samples.size == 0:
        samples = half[occupancy & (half > 0)]
        medial = occupancy & (half > 0)
    thickness = 2.0 * samples
    if thickness.size:
        thickness = np.maximum(thickness, pitch * 0.5)
    min_t = float(thickness.min()) if thickness.size else 0.0
    max_t = float(thickness.max()) if thickness.size else 0.0
    if thickness.size:
        edges = np.linspace(0.0, max(max_t, pitch), num=min(17, max(4, int(thickness.size ** 0.5) + 1)))
        counts, edges = np.histogram(thickness, bins=edges)
    else:
        edges, counts = np.array([0.0, pitch]), np.array([0], dtype=int)
    thickness_volume = np.where(occupancy, np.maximum(2.0 * half, pitch * 0.5), 0.0)
    return {
        'min_thickness_mm': min_t,
        'max_thickness_mm': max_t,
        'mean_thickness_mm': float(thickness.mean()) if thickness.size else 0.0,
        'histogram': {'edges_mm': edges.tolist(), 'counts': counts.astype(int).tolist()},
        'medial_samples': int(thickness.size),
        'method': '2 * (solid EDT - pitch/2) at interior medial samples',
        'pitch_mm': pitch,
        '_half': half,
        '_medial': medial,
        '_thickness_volume': thickness_volume,
    }


def regions_below_threshold(thickness_volume, occupancy, pitch_mm, threshold_mm, origin, limit=32):
    """Connected solid components where the thickness field is below threshold."""
    thin = occupancy & (thickness_volume < float(threshold_mm)) & (thickness_volume > 0)
    if not thin.any():
        return []
    labels, count = ndi.label(thin, CROSS3)
    sizes = np.bincount(labels.ravel())
    boxes = ndi.find_objects(labels)
    regions = []
    order = np.argsort(-sizes[1:count + 1]) + 1 if count else []
    for label in order[:limit]:
        box = boxes[label - 1]
        local = np.argwhere(labels[box] == label)
        center = local.mean(axis=0)
        zyx = [float(center[axis] + box[axis].start) for axis in range(3)]
        xyz = [origin[i] + (zyx[2 - i] + 0.5) * pitch_mm for i in range(3)]
        regions.append({
            'component': int(label),
            'voxel_count': int(sizes[label]),
            'volume_mm3': float(sizes[label]) * pitch_mm ** 3,
            'centroid_mm': xyz,
            'min_thickness_mm': float(thickness_volume[labels == label].min()),
        })
    return regions


def analyze_wall_thickness(triangles, settings, *, threshold_mm=None, budget=None,
                           cancel=None, progress=no_progress, bounds=None):
    """Report min thickness, histogram and thin regions for a triangle mesh."""
    cancel = cancel or CancellationToken()
    budget = budget or ResourceBudget(**settings['resources'])
    started = time.monotonic()
    hollow = settings['hollow']
    threshold = float(threshold_mm if threshold_mm is not None else hollow['min_wall_thickness_mm'])
    bounds = np.asarray(bounds if bounds is not None else geometry.triangle_bounds(triangles, cancel=cancel),
                        dtype=float)
    size, dims, needed = choose_hollow_voxel_size(bounds, settings, budget)
    # Thickness-only runs may use a finer derived pitch when wall_thickness is large.
    if float(hollow.get('voxel_size_mm') or 0.0) <= 0:
        size = min(size, max(threshold / 4.0, 0.05))
        _, dims = _grid(bounds, size)
        needed = float(np.prod(dims + 2))
    occupancy, _volume, low, grid, odd_rows, filled = _voxelize(
        triangles, bounds, size, cancel, progress, budget)
    field = wall_thickness_field(occupancy, size)
    thickness_volume = field.pop('_thickness_volume', None)
    medial = field.pop('_medial', None)
    field.pop('_half', None)
    regions = []
    if thickness_volume is not None:
        mask = medial if medial is not None and medial.any() else occupancy
        regions = regions_below_threshold(thickness_volume, mask, size, threshold, low)
    field['regions_below_threshold'] = regions
    field['threshold_mm'] = threshold
    field['regions_below_count'] = len(regions)
    field['voxel_size_mm'] = size
    field['voxel_grid'] = list(grid)
    field['occupied_voxels'] = int(filled)
    field['open_contour_rows'] = int(odd_rows)
    field['seconds'] = time.monotonic() - started
    min_t = field['min_thickness_mm']
    if min_t is None:
        status = 'not_run'
    elif min_t + 1e-9 < threshold:
        status = 'warn'
    else:
        status = 'pass'
    field['status'] = status
    return field


def _erode_core(occupancy, iterations):
    if iterations < 1:
        raise VoxelMillError('invalid_hollow', 'Wall thickness must cover at least one voxel',
                        {'erosion_iterations': iterations})
    eroded = ndi.binary_erosion(occupancy, structure=CROSS3, iterations=int(iterations))
    return eroded


def _bottom_open(occupancy, core):
    """Extend the cavity through the bottom of each column that contains core."""
    removal = core.copy()
    nz = occupancy.shape[0]
    for j in range(occupancy.shape[1]):
        for i in range(occupancy.shape[2]):
            column = core[:, j, i]
            if not column.any():
                continue
            top = int(np.flatnonzero(column)[-1])
            for k in range(0, top + 1):
                if occupancy[k, j, i]:
                    removal[k, j, i] = True
    # Also clear any solid voxel on the padded low-Z face under the part.
    if nz:
        removal[0] |= occupancy[0]
    return removal


def _infill_mask(cavity, pitch_mm, origin, kind, infill_pitch_mm):
    """Strut voxels inside ``cavity`` for grid / hex / gyroid lattices."""
    if kind == 'none' or not cavity.any():
        return np.zeros_like(cavity, dtype=bool)
    nz, ny, nx = cavity.shape
    period = max(1, int(round(float(infill_pitch_mm) / pitch_mm)))
    kk, jj, ii = np.ogrid[0:nz, 0:ny, 0:nx]
    if kind == 'grid':
        struts = ((ii % period) == 0) | ((jj % period) == 0) | ((kk % period) == 0)
    elif kind == 'hex':
        # Vertical honeycomb: pointy-top hex centers on a triangular lattice in XY.
        row_pitch = period
        col_pitch = max(1, int(round(period * math.sqrt(3) / 2)))
        struts = np.zeros_like(cavity, dtype=bool)
        for j in range(ny):
            phase = (j // max(row_pitch, 1)) % 2
            for i in range(nx):
                if ((i - phase * (col_pitch // 2)) % max(col_pitch, 1)) == 0:
                    struts[:, j, i] = True
                if (j % max(row_pitch, 1)) == 0:
                    struts[:, j, i] = True
        # Horizontal decks every period in Z keep the lattice printable.
        struts[(kk % period) == 0] = True
    elif kind == 'gyroid':
        scale = (2.0 * math.pi) / max(float(infill_pitch_mm), pitch_mm)
        x = origin[0] + (ii + 0.5) * pitch_mm
        y = origin[1] + (jj + 0.5) * pitch_mm
        z = origin[2] + (kk + 0.5) * pitch_mm
        sx, sy, sz = scale * x, scale * y, scale * z
        field = np.sin(sx) * np.cos(sy) + np.sin(sy) * np.cos(sz) + np.sin(sz) * np.cos(sx)
        # Half-voxel band ≈ one-voxel-thick sheet.
        band = 0.6
        struts = np.abs(field) <= band
    else:
        raise VoxelMillError('invalid_hollow', f'Unknown infill kind {kind!r}')
    return cavity & struts


def _cavity_extrema(cavity, origin, pitch_mm):
    """Per enclosed cavity component, lowest and highest voxel centers in mm."""
    labels, count = ndi.label(cavity, CROSS3)
    holes = []
    for label in range(1, count + 1):
        coords = np.argwhere(labels == label)  # z,y,x
        if len(coords) == 0:
            continue
        low_idx = coords[coords[:, 0].argmin()]
        high_idx = coords[coords[:, 0].argmax()]
        def center(zyx):
            return [float(origin[i] + (zyx[2 - i] + 0.5) * pitch_mm) for i in range(3)]
        holes.append({
            'component': int(label),
            'voxel_count': int(len(coords)),
            'volume_mm3': float(len(coords)) * pitch_mm ** 3,
            'drain_position_mm': center(low_idx),
            'vent_position_mm': center(high_idx),
            'drain_toward': [0.0, 0.0, -1.0],
            'vent_toward': [0.0, 0.0, 1.0],
        })
    return holes, labels, count


def _hole_tool(position, toward, diameter_mm, length_mm, m):
    direction = np.asarray(toward, dtype=float)
    norm = float(np.linalg.norm(direction))
    if norm <= 0:
        direction = np.array([0.0, 0.0, -1.0])
    else:
        direction = direction / norm
    start = np.asarray(position, dtype=float) - direction * (0.5 * length_mm)
    end = start + direction * length_mm
    radius = float(diameter_mm) / 2.0
    return geometry.cylinder_between(start, end, radius, radius, segments=24)


def _apply_drain_vents(solid, cavity, origin, pitch_mm, settings, cancel):
    hollow = settings['hollow']
    drain_d = float(hollow['drain_diameter_mm'])
    vent_d = float(hollow['vent_diameter_mm'])
    holes, _labels, count = _cavity_extrema(cavity, origin, pitch_mm)
    if not holes:
        return solid, {'holes': [], 'enclosed_voids_before_holes': 0}
    box = np.asarray(solid.bounding_box(), dtype=float).reshape(2, 3)
    extent = float(np.linalg.norm(box[1] - box[0])) + 2.0 * max(drain_d, vent_d)
    m = geometry._manifold()
    tools = []
    records = []
    for hole in holes:
        cancel.check()
        drain = _hole_tool(hole['drain_position_mm'], hole['drain_toward'], drain_d, extent, m)
        vent = _hole_tool(hole['vent_position_mm'], hole['vent_toward'], vent_d, extent, m)
        tools.extend((drain, vent))
        records.append({
            **hole,
            'drain_diameter_mm': drain_d,
            'vent_diameter_mm': vent_d,
        })
    before = float(solid.volume())
    try:
        result = m.Manifold.batch_boolean([solid, *tools], m.OpType.Subtract)
    except ValueError as error:
        raise VoxelMillError('drain_hole_failed', f'Drain/vent boolean failed: {error}') from error
    if result.status() != m.Error.NoError or result.is_empty() or not result.volume() > 0:
        raise VoxelMillError('drain_hole_failed', 'Drain/vent boolean produced an empty solid',
                        {'manifold_status': str(result.status())})
    return result, {
        'holes': records,
        'enclosed_voids_before_holes': int(count),
        'volume_before_holes_mm3': before,
        'volume_after_holes_mm3': float(result.volume()),
        'removed_by_holes_mm3': max(0.0, before - float(result.volume())),
    }


def _verify_drainage(triangles, settings, budget, cancel, progress):
    from .validation import analyze_drainage, drainage_check
    bounds = geometry.triangle_bounds(triangles, cancel=cancel)
    drain = analyze_drainage(triangles, bounds, settings, budget=budget, cancel=cancel,
                             progress=progress)
    status = drainage_check(drain)
    return drain, status


def hollow_mesh(triangles, settings, *, budget=None, cancel=None, progress=no_progress,
                solid=None, add_holes=True, force=False):
    """Hollow ``triangles`` according to ``settings['hollow']``.

    ``force=True`` runs even when ``hollow.enabled`` is false (CLI ``hollow``
    command). ``add_holes`` controls automatic drain/vent pairs.
    """
    cancel = cancel or CancellationToken()
    budget = budget or ResourceBudget(**settings['resources'])
    hollow = settings['hollow']
    if not force and not hollow['enabled']:
        return np.asarray(triangles, dtype=np.float32), {
            'operation': 'hollow', 'status': 'skipped', 'reason': 'hollow.enabled is false'}
    started = time.monotonic()
    triangles = np.asarray(triangles, dtype=np.float32)
    bounds = geometry.triangle_bounds(triangles, cancel=cancel)
    size, dims, needed = choose_hollow_voxel_size(bounds, settings, budget)
    wall = float(hollow['wall_thickness_mm'])
    iterations = max(1, int(round(wall / size)))
    occupancy, _vol, low, grid, odd_rows, filled = _voxelize(
        triangles, bounds, size, cancel, progress, budget)
    if not occupancy.any():
        raise VoxelMillError('hollow_empty', 'Model rasterized empty; nothing to hollow')
    core = _erode_core(occupancy, iterations)
    if hollow['mode'] == 'bottom_open':
        removal = _bottom_open(occupancy, core)
    else:
        removal = core
    if not removal.any():
        raise VoxelMillError('hollow_empty_core',
                        'Erosion removed nothing; increase wall thickness resolution '
                        '(smaller hollow.voxel_size_mm) or check the model size',
                        {'wall_thickness_mm': wall, 'voxel_size_mm': size,
                         'erosion_iterations': iterations})
    cavity = removal & occupancy
    shell_occupancy = occupancy & ~removal
    if not shell_occupancy.any():
        raise VoxelMillError('hollow_consumed', 'Hollowing removed the entire solid',
                        {'wall_thickness_mm': wall, 'voxel_size_mm': size})

    thickness = wall_thickness_field(shell_occupancy, size)
    thickness_volume = thickness.pop('_thickness_volume', None)
    medial = thickness.pop('_medial', None)
    thickness.pop('_half', None)
    min_wall = float(hollow['min_wall_thickness_mm'])
    regions = []
    if thickness_volume is not None:
        mask = medial if medial is not None and medial.any() else shell_occupancy
        regions = regions_below_threshold(thickness_volume, mask, size, min_wall, low)
    measured = thickness.get('min_thickness_mm')
    if measured is not None and measured + 1e-9 < min_wall:
        raise VoxelMillError(
            'thin_wall',
            'Hollowed shell is thinner than hollow.min_wall_thickness_mm',
            {'min_thickness_mm': measured, 'min_wall_thickness_mm': min_wall,
             'wall_thickness_mm': wall, 'voxel_size_mm': size,
             'regions_below_threshold': regions})

    infill_kind = hollow['infill']
    infill_voxels = _infill_mask(cavity, size, low, infill_kind, hollow['infill_pitch_mm'])
    material = shell_occupancy | infill_voxels

    # Prefer exact boolean when a solid is available; otherwise remesh occupancy.
    method = 'manifold_subtract'
    volume_before = None
    if solid is None:
        try:
            solid, _ = geometry.mesh_to_manifold(triangles, budget, settings['repair'], cancel)
        except VoxelMillError:
            solid = None
    if solid is not None:
        volume_before = float(solid.volume())
        core_for_boolean = removal
        # Infill stays as occupancy union after subtract: subtract full cavity,
        # then union strut solid.
        try:
            tool, composed = _surface_solid(core_for_boolean, low, size, cancel)
            m = geometry._manifold()
            result = m.Manifold.batch_boolean([solid, tool], m.OpType.Subtract)
            if result.status() != m.Error.NoError or result.is_empty() or not result.volume() > 0:
                raise VoxelMillError('hollow_boolean_failed', 'Hollow subtract produced an empty solid',
                                {'manifold_status': str(result.status())})
            if infill_kind != 'none' and infill_voxels.any():
                infill_solid, _ = _surface_solid(infill_voxels, low, size, cancel)
                result = m.Manifold.batch_boolean([result, infill_solid], m.OpType.Add)
                if result.status() != m.Error.NoError or result.is_empty():
                    raise VoxelMillError('infill_failed', 'Infill union produced an invalid solid',
                                    {'manifold_status': str(result.status())})
            solid = result
        except VoxelMillError as error:
            if error.code in {'hollow_boolean_failed', 'hollow_empty', 'hollow_invalid_solid',
                              'hollow_boundary', 'hollow_not_converged', 'infill_failed'}:
                # Fall back to occupancy remesh of the finished material.
                method = 'raster_occupancy_difference'
                solid, composed = _surface_solid(material, low, size, cancel)
            else:
                raise
    else:
        method = 'raster_occupancy_difference'
        solid, composed = _surface_solid(material, low, size, cancel)
        volume_before = filled * size ** 3

    holes_report = {'holes': [], 'enclosed_voids_before_holes': 0}
    if add_holes and hollow['mode'] == 'inner':
        # Cavity for hole placement: empty inside the shell before holes.
        post = material
        # Approximate cavity as original occupancy minus current material,
        # restricted to the eroded core region (ignore exterior air).
        hole_cavity = cavity & ~infill_voxels
        if not hole_cavity.any():
            hole_cavity = cavity
        solid, holes_report = _apply_drain_vents(solid, hole_cavity, low, size, settings, cancel)
    elif add_holes and hollow['mode'] == 'bottom_open':
        # Bottom already opens; still add a top vent per cavity component.
        hole_cavity = cavity & ~infill_voxels
        if hole_cavity.any():
            # Temporarily use vent for both; drain toward bottom is already open.
            solid, holes_report = _apply_drain_vents(solid, hole_cavity, low, size, settings, cancel)

    out_triangles = geometry.manifold_triangles(solid).astype(np.float32)
    drain_result, drain_status = _verify_drainage(out_triangles, settings, budget, cancel, progress)
    if add_holes and drain_status == 'fail':
        raise VoxelMillError(
            'drain_path_failed',
            'Drain/vent holes did not connect the cavity to the exterior',
            {'drainage': drain_result, 'holes': holes_report})

    volume_after = float(solid.volume())
    report = {
        'operation': 'hollow',
        'status': 'complete',
        'method': method,
        'mode': hollow['mode'],
        'wall_thickness_mm': wall,
        'min_wall_thickness_mm': min_wall,
        'measured_min_wall_thickness_mm': measured,
        'voxel_size_mm': size,
        'voxel_grid': list(grid),
        'voxel_grid_bytes': int(needed),
        'erosion_iterations': iterations,
        'open_contour_rows': int(odd_rows),
        'occupied_voxels_before': int(filled),
        'cavity_voxels': int(cavity.sum()),
        'cavity_volume_mm3': float(cavity.sum()) * size ** 3,
        'infill': infill_kind,
        'infill_pitch_mm': float(hollow['infill_pitch_mm']),
        'infill_voxels': int(infill_voxels.sum()),
        'well_composed': composed,
        'volume_before_mm3': float(volume_before) if volume_before is not None else None,
        'volume_after_mm3': volume_after,
        'removed_volume_mm3': (max(0.0, float(volume_before) - volume_after)
                               if volume_before is not None else None),
        'triangles': int(solid.num_tri()),
        'genus': int(solid.genus()),
        'wall_thickness': {**thickness, 'regions_below_threshold': regions,
                           'threshold_mm': min_wall},
        'holes': holes_report,
        'drainage': drain_result,
        'drainage_check': drain_status,
        'seconds': time.monotonic() - started,
    }
    return out_triangles, report


def apply_hollow(model: PreparedModel, settings, *, budget=None, cancel=None,
                 progress=no_progress, add_holes=True):
    """Hollow a PreparedModel in place when enabled; no-op otherwise."""
    if not settings['hollow']['enabled']:
        return model, {'operation': 'hollow', 'status': 'skipped', 'reason': 'hollow.enabled is false'}
    triangles, report = hollow_mesh(
        model.triangles, settings, budget=budget, cancel=cancel, progress=progress,
        solid=model.solid, add_holes=add_holes, force=True)
    solid = None
    try:
        solid, _ = geometry.mesh_to_manifold(triangles, budget or ResourceBudget(**settings['resources']),
                                             settings['repair'], cancel or CancellationToken())
    except VoxelMillError:
        solid = None
    return PreparedModel(triangles, solid, settings, model.repair, model.cavity_fill,
                         model.exact_union_attempted, model.blocked_by), report


def hollow_stl_triangles(triangles, settings, *, budget=None, cancel=None, progress=no_progress,
                         add_holes=True):
    """CLI/prepare helper: ingest like boolean ops, then hollow."""
    cancel = cancel or CancellationToken()
    budget = budget or ResourceBudget(**settings['resources'])
    model = prepare_model(triangles, settings, budget=budget, cancel=cancel, progress=progress)
    # Hollowing after optional seal_voids so the cavity is the one we create.
    triangles_out, report = hollow_mesh(
        model.triangles, settings, budget=budget, cancel=cancel, progress=progress,
        solid=model.solid, add_holes=add_holes, force=True)
    report['repair'] = model.repair
    report['cavity_fill'] = model.cavity_fill
    return triangles_out, report
