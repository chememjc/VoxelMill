"""Runtime-selected acceleration with an exact CPU fallback."""
from __future__ import annotations

import numpy as np

from .contracts import VoxelMillError


def cuda_status():
    """Return compile/runtime/device state without treating toolkit presence as a GPU."""
    try:
        from . import _native
        if not hasattr(_native, 'cuda_status'):
            return {'compiled': False, 'available': False, 'device_count': 0,
                    'reason': 'native extension was built without CUDA'}
        return dict(_native.cuda_status())
    except Exception as exc:
        return {'compiled': True, 'available': False, 'device_count': 0,
                'reason': str(exc)}


def resolve_backend(resources):
    requested = resources.get('acceleration', 'auto')
    if requested == 'cpu':
        return 'cpu'
    status = cuda_status()
    device = resources.get('cuda_device', 0)
    available = status['available'] and device < status['device_count']
    if requested == 'cuda' and not available:
        raise VoxelMillError('cuda_unavailable', 'CUDA acceleration was requested but is unavailable',
                        {**status, 'requested_device': device})
    return 'cuda' if available else 'cpu'


def binary_morphology(mask, rx, ry, *, erode, resources):
    """Apply the same inclusive elliptical footprint on CPU or CUDA."""
    backend = resolve_backend(resources)
    occupied = np.asarray(mask, dtype=np.uint8, order='C')
    if backend == 'cuda':
        from . import _native
        try:
            result = _native.cuda_binary_morphology(
                occupied, rx, ry, erode, resources.get('cuda_device', 0))
        except Exception as exc:
            if resources.get('acceleration', 'auto') == 'cuda':
                raise VoxelMillError('cuda_failed', 'CUDA morphology failed', {'reason': str(exc)}) from exc
            backend = 'cpu'
        else:
            return np.asarray(result, dtype=np.uint8), backend
    from scipy import ndimage as ndi
    # Local import avoids a goo dependency cycle.
    yy, xx = np.ogrid[-ry:ry + 1, -rx:rx + 1]
    footprint = ((xx / rx) ** 2 if rx else (xx != 0) * 4) + (
        (yy / ry) ** 2 if ry else (yy != 0) * 4) <= 1
    operation = ndi.binary_erosion if erode else ndi.binary_dilation
    return operation(occupied != 0, structure=footprint, border_value=0).astype(np.uint8), backend
