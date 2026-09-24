"""A lenient support_void_policy where one file cannot attribute a void.

prepare classifies every void as support or model. validate, slice and verify
see one STL or slice file and cannot, so they used to fail an export prepare
had just passed, on single-voxel crevices under the support tips. They now
warn on crevice-sized findings and still fail anything a model cavity could be.
"""
import manifold3d as m
import pytest

from voxelmill.config import resolve_settings
from voxelmill.contracts import Diagnostic, ValidationReport
from voxelmill.geometry import manifold_triangles
from voxelmill.mesh import write_stl
from voxelmill.pipeline import validate_stl
from voxelmill.validation import soften_unattributed_voids


def failing_report(void_volumes=(), drainage=None):
    report = ValidationReport()
    if void_volumes:
        report.metrics['enclosed_voids'] = {
            'count': len(void_volumes), 'volume_mm3': sum(void_volumes),
            'examples': [{'component': i, 'volume_mm3': v, 'first_layer': 1}
                         for i, v in enumerate(sorted(void_volumes, reverse=True))]}
        report.fail('enclosed_voids', 'Enclosed empty-space components remain')
    if drainage is not None:
        report.metrics['drainage'] = drainage
        report.checks['drainage_bottlenecks'] = 'fail'
        report.diagnostics.append(Diagnostic('drainage_bottleneck', 'neck', details=drainage))
    return report


def policy(name):
    return resolve_settings(overrides={'repair': {'support_void_policy': name}})


def test_crevice_sized_voids_warn_under_the_default_policy():
    report = soften_unattributed_voids(failing_report([1.6e-5] * 12), resolve_settings())
    assert report.checks['enclosed_voids'] == 'warn'
    assert report.passed
    assert [d.code for d in report.diagnostics] == ['unattributed_enclosed_voids']


def test_a_void_larger_than_a_contact_still_fails():
    # A 0.35 mm contact sphere holds about 0.022 mm3; a model cavity is far larger.
    report = soften_unattributed_voids(failing_report([1.6e-5, 0.5]), resolve_settings())
    assert report.checks['enclosed_voids'] == 'fail'
    assert not report.passed


def test_the_fail_policy_changes_nothing():
    report = soften_unattributed_voids(
        failing_report([1.6e-5], {'bottlenecked_components': 1, 'enclosed_components': 0}),
        policy('fail'))
    assert report.checks == {'enclosed_voids': 'fail', 'drainage_bottlenecks': 'fail'}


def test_a_drainage_neck_warns_but_a_sealed_chamber_fails():
    neck = soften_unattributed_voids(
        failing_report(drainage={'bottlenecked_components': 1, 'enclosed_components': 0}),
        resolve_settings())
    assert neck.checks['drainage_bottlenecks'] == 'warn'
    assert [d.code for d in neck.diagnostics] == ['unattributed_drainage_bottleneck']
    sealed = soften_unattributed_voids(
        failing_report(drainage={'bottlenecked_components': 1, 'enclosed_components': 1,
                                 'enclosed_volume_mm3': 2.0, 'analysis_pitch_mm': 0.2}),
        resolve_settings())
    assert sealed.checks['drainage_bottlenecks'] == 'fail'


def test_single_cell_sealed_chambers_are_crevices():
    cell = 0.2 ** 3
    report = soften_unattributed_voids(
        failing_report(drainage={'bottlenecked_components': 3, 'enclosed_components': 4,
                                 'enclosed_volume_mm3': 4 * cell, 'analysis_pitch_mm': 0.2}),
        resolve_settings())
    assert report.checks['drainage_bottlenecks'] == 'warn'


def test_classified_findings_are_left_to_the_prepare_policy():
    report = failing_report([1.6e-5])
    report.metrics['enclosed_voids']['classified'] = True
    assert soften_unattributed_voids(report, resolve_settings()).checks['enclosed_voids'] == 'fail'


@pytest.fixture
def sealed_box(tmp_path):
    outer = m.Manifold.cube([12, 12, 12], center=True).translate([0, 0, 6])
    inner = m.Manifold.cube([6, 6, 6], center=True).translate([0, 0, 6])
    path = tmp_path / 'sealed.stl'
    write_stl(path, manifold_triangles(outer - inner))
    return path


def test_validate_still_fails_a_sealed_model_cavity(sealed_box):
    report = validate_stl(sealed_box, resolve_settings(), drainage=True)
    assert report.checks['enclosed_voids'] == 'fail'
    assert report.checks['drainage_bottlenecks'] == 'fail'
    assert not report.passed
