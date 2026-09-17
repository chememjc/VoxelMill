"""Downward samples no routed contact reaches, reported as advice."""
import numpy as np

from voxelmill.config import resolve_settings
from voxelmill.contracts import ValidationReport
from voxelmill.overhangs import MAX_EXAMPLES, analyze_overhangs, apply_overhang_check


def settings(**overrides):
    base = {'process': {'layer_height_mm': .2},
            'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [.1, .1], 'build_mm': [100., 80., 165.]}}
    for section, values in overrides.items():
        base.setdefault(section, {}).update(values)
    return resolve_settings(overrides=base)


def ceiling(half=5.0, z=10.0):
    """Two triangles forming a horizontal square whose normal points down."""
    a = (-half, -half, z)
    b = (half, -half, z)
    c = (half, half, z)
    d = (-half, half, z)
    return np.array([[a, c, b], [a, d, c]], dtype=np.float64)


def wall(half=5.0, height=10.0):
    """A vertical square in the XZ plane; nothing here needs support."""
    a = (-half, 0.0, 0.0)
    b = (half, 0.0, 0.0)
    c = (half, 0.0, height)
    d = (-half, 0.0, height)
    return np.array([[a, b, c], [a, c, d]], dtype=np.float64)


def lattice(spacing, half=5.0, z=10.0):
    axis = np.arange(-half, half + spacing, spacing)
    grid = np.stack(np.meshgrid(axis, axis, indexing='ij'), axis=-1).reshape(-1, 2)
    return np.column_stack((grid, np.full(len(grid), z)))


def test_an_unsupported_ceiling_reports_its_samples_with_a_layer():
    s = settings()
    result = analyze_overhangs(ceiling(), s, np.empty((0, 3)))
    assert result['status'] == 'complete'
    assert result['sample_count'] > 0
    assert result['unsupported_count'] == result['sample_count']
    assert result['unsupported_fraction'] == 1.0
    # No contacts means no distance to report, and saying nothing beats
    # inventing a number.
    assert result['max_gap_mm'] is None
    first = result['samples'][0]
    assert first['layer'] == int(10.0 / s['process']['layer_height_mm'])
    assert first['position_mm'][2] == 10.0
    assert first['gap_mm'] is None


def test_contacts_on_a_spacing_lattice_cover_the_same_ceiling():
    s = settings()
    spacing = s['support']['spacing_mm']
    result = analyze_overhangs(ceiling(), s, lattice(spacing))
    assert result['status'] == 'complete' and result['sample_count'] > 0
    assert result['unsupported_count'] == 0 and result['samples'] == []
    assert result['max_gap_mm'] <= spacing


def test_a_vertical_wall_is_never_an_overhang_whatever_the_contacts():
    s = settings()
    for contacts in (np.empty((0, 3)), lattice(s['support']['spacing_mm'], z=5.0)):
        result = analyze_overhangs(wall(), s, contacts)
        assert result['sample_count'] == 0
        assert result['unsupported_count'] == 0 and result['downward_area_mm2'] == 0.0


def test_a_downward_face_resting_on_the_plate_needs_nothing_under_it():
    s = settings()
    result = analyze_overhangs(ceiling(z=0.0), s, np.empty((0, 3)))
    assert result['sample_count'] > 0
    assert result['on_plate_count'] == result['sample_count']
    assert result['unsupported_count'] == 0


def test_the_diagnostic_list_is_capped_but_the_count_is_the_true_one():
    s = settings()
    result = analyze_overhangs(ceiling(half=30.0), s, np.empty((0, 3)))
    assert result['unsupported_count'] > MAX_EXAMPLES
    assert len(result['samples']) == MAX_EXAMPLES and result['samples_truncated']
    report = ValidationReport()
    apply_overhang_check(report, ceiling(half=30.0), s, np.empty((0, 3)))
    assert report.checks['unsupported_overhangs'] == 'warn'
    diagnostics = [d for d in report.diagnostics if d.code == 'unsupported_overhang']
    assert len(diagnostics) == MAX_EXAMPLES
    assert report.metrics['unsupported_overhangs']['unsupported_count'] > MAX_EXAMPLES
    # A warning, not an error: the report still passes on this evidence alone.
    assert all(d.severity == 'warning' for d in diagnostics)
    assert diagnostics[0].layer is not None and diagnostics[0].position_mm is not None


def test_the_check_passes_when_every_sample_is_reached_and_works_on_a_dict():
    s = settings()
    report = ValidationReport()
    apply_overhang_check(report, ceiling(), s, lattice(s['support']['spacing_mm']))
    assert report.checks['unsupported_overhangs'] == 'pass'
    assert not [d for d in report.diagnostics if d.code == 'unsupported_overhang']
    plain = {}
    apply_overhang_check(plain, ceiling(), s, np.empty((0, 3)))
    assert plain['checks']['unsupported_overhangs'] == 'warn'
    assert plain['metrics']['unsupported_overhangs']['unsupported_count'] > 0
    assert plain['diagnostics'][0]['code'] == 'unsupported_overhang'
    assert plain['diagnostics'][0]['severity'] == 'warning'


def test_a_check_that_could_not_run_is_not_reported_as_a_pass():
    s = settings()
    report = ValidationReport()
    apply_overhang_check(report, ceiling().reshape(-1, 9), s, np.empty((0, 3)))
    assert report.checks['unsupported_overhangs'] == 'not_run'
    assert report.metrics['unsupported_overhangs']['error_code'] == 'overhang_input'
