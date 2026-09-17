import numpy as np
import pytest

from voxelmill.acceleration import binary_morphology, resolve_backend
from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError, ResourceBudget


def test_auto_has_a_real_cpu_fallback_and_matches_reference(monkeypatch):
    monkeypatch.setattr('voxelmill.acceleration.cuda_status',
                        lambda: {'compiled': True, 'available': False,
                                 'device_count': 0, 'reason': 'driver absent'})
    mask = np.zeros((9, 11), np.uint8)
    mask[2:7, 3:9] = 1
    out, backend = binary_morphology(
        mask, 2, 1, erode=True,
        resources={'acceleration': 'auto', 'cuda_device': 0})
    from scipy import ndimage as ndi
    yy, xx = np.ogrid[-1:2, -2:3]
    expected = ndi.binary_erosion(mask, structure=(xx / 2) ** 2 + yy ** 2 <= 1)
    assert backend == 'cpu'
    assert np.array_equal(out, expected)


def test_explicit_cuda_fails_instead_of_silently_ignoring_preference(monkeypatch):
    monkeypatch.setattr('voxelmill.acceleration.cuda_status',
                        lambda: {'compiled': False, 'available': False,
                                 'device_count': 0, 'reason': 'CPU-only build'})
    with pytest.raises(VoxelMillError, match='requested but is unavailable'):
        resolve_backend({'acceleration': 'cuda', 'cuda_device': 0})


def test_acceleration_settings_validate_and_reach_budget():
    settings = resolve_settings(overrides={
        'resources': {'acceleration': 'cpu', 'cuda_device': 2}})
    budget = ResourceBudget(**settings['resources'])
    assert budget.acceleration == 'cpu' and budget.cuda_device == 2


def test_cuda_auto_matches_cpu_when_a_device_is_present():
    from voxelmill.acceleration import cuda_status
    status = cuda_status()
    if not status['available']:
        pytest.skip(status.get('reason') or 'no CUDA device')
    mask = np.zeros((17, 19), np.uint8)
    mask[3:12, 4:15] = 1
    gpu, backend = binary_morphology(
        mask, 2, 2, erode=False,
        resources={'acceleration': 'auto', 'cuda_device': 0})
    cpu, _ = binary_morphology(
        mask, 2, 2, erode=False,
        resources={'acceleration': 'cpu', 'cuda_device': 0})
    assert backend == 'cuda'
    assert np.array_equal(gpu, cpu)
