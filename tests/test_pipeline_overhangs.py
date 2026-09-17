"""The overhang-coverage advisory, wired into ``prepare`` as a warning."""
import manifold3d as m
import pytest

from voxelmill.config import resolve_settings
from voxelmill.contracts import ValidationReport
from voxelmill.geometry import manifold_triangles
from voxelmill.mesh import write_stl
from voxelmill.overhangs import apply_overhang_check
from voxelmill.pipeline import prepare


def small(**overrides):
    base = {'process': {'layer_height_mm': 0.2},
            'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [0.1, 0.1], 'build_mm': [100., 80., 165.]}}
    for section, values in overrides.items():
        base.setdefault(section, {}).update(values)
    return resolve_settings(overrides=base)


@pytest.fixture
def floating_block(tmp_path):
    """A box with no contact under it once lifted clear of the plate."""
    path = tmp_path / 'block.stl'
    write_stl(path, manifold_triangles(m.Manifold.cube((10, 10, 4), True)))
    return path


def test_an_unsupported_ceiling_reports_warn_with_a_layer(tmp_path, floating_block):
    # automatic=False and no manual contacts: nothing is routed under the
    # floating underside, so every downward sample there is a real gap.
    settings = small(support={'automatic': False, 'base_type': 'none'})
    report = prepare(floating_block, settings, drainage=False)
    checks = report['validation']['checks']
    assert checks['unsupported_overhangs'] == 'warn'
    metrics = report['validation']['metrics']['unsupported_overhangs']
    assert metrics['unsupported_count'] > 0
    diagnostics = [d for d in report['validation']['diagnostics'] if d['code'] == 'unsupported_overhang']
    assert diagnostics and diagnostics[0]['layer'] is not None
    assert all(d['severity'] == 'warning' for d in diagnostics)


def test_supports_routed_under_it_report_pass(tmp_path, floating_block):
    # Default automatic supports cover the underside at the same spacing the
    # overhang sampler checks against, so nothing should be left uncovered.
    report = prepare(floating_block, small(), drainage=False)
    assert report['validation']['checks']['unsupported_overhangs'] == 'pass'


def test_the_skip_flag_produces_not_run(tmp_path, floating_block):
    report = prepare(floating_block, small(), drainage=False, overhang_check=False)
    assert report['validation']['checks']['unsupported_overhangs'] == 'not_run'
    assert not any(d['code'] == 'unsupported_overhang' for d in report['validation']['diagnostics'])


def test_a_warn_does_not_flip_the_report_passed_on_its_own():
    """The check is advice about orientation and support density, never a gate."""
    import numpy as np
    # A horizontal ceiling well above the plate, normal pointing down, with no
    # contacts anywhere near it: every sample on it is a real gap.
    half, z = 5.0, 10.0
    a, b, c, d = (-half, -half, z), (half, -half, z), (half, half, z), (-half, half, z)
    ceiling = np.array([[a, c, b], [a, d, c]], dtype=np.float64)
    report = ValidationReport()
    apply_overhang_check(report, ceiling, small(), [])
    assert report.checks == {'unsupported_overhangs': 'warn'}
    assert all(d.severity == 'warning' for d in report.diagnostics)
    # warn is an acceptable outcome for ValidationReport.passed; only a missing
    # check or an error-severity diagnostic would fail it.
    assert report.passed
