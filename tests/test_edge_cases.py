"""Edge cases: no-overhang and all-overhang extremes, out-of-volume parts,
sub-layer features, the settings-schema bounds, and format round trips.
"""
from __future__ import annotations

import manifold3d as m
import pytest

from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.ctb import convert_slices, verify_ctb
from voxelmill.geometry import manifold_triangles
from voxelmill.goo import slice_stl
from voxelmill.mesh import write_stl
from voxelmill.pipeline import prepare
from voxelmill.settings_schema import FIELDS

PRINTER = {'pixels': [1000, 800], 'pixel_pitch_mm': [0.1, 0.1], 'build_mm': [100.0, 80.0, 165.0]}


def small(**overrides):
    base = {'process': {'layer_height_mm': 0.2}, 'printer': dict(PRINTER)}
    for section, values in overrides.items():
        base.setdefault(section, {}).update(values)
    return resolve_settings(overrides=base)


def write_shape(path, solid):
    write_stl(path, manifold_triangles(solid))
    return path


def test_cube_flush_on_the_plate_needs_no_routed_pillar_to_reach_it(tmp_path):
    # A cube placed with lift_mm=0 sits with its bottom face exactly at z=0.
    # The router still emits a trivial contact/base interface there (the
    # bottom face is still a downward-facing sample; see
    # src/voxelmill/overhangs.py, which explicitly excludes samples within one
    # layer height of the plate from ever being reported as *unsupported*).
    # What "needs no support" means for a flush face is that nothing is left
    # uncovered and the plate still validates -- not that zero contacts exist.
    cube = m.Manifold.cube((8.0, 8.0, 8.0), True).translate((0.0, 0.0, 4.0))
    path = write_shape(tmp_path / 'cube.stl', cube)
    settings = small()
    report = prepare(path, settings, lift_mm=0.0)
    supports = report['passes'][-1]['supports']
    assert supports['contacts_failed'] == 0
    assert supports['uncovered_samples'] == 0
    assert report['validation']['passed'], report['validation']['checks']


def test_part_taller_than_the_printer_reports_a_structured_failure(tmp_path):
    tall = m.Manifold.cube((5.0, 5.0, 200.0), True).translate((0.0, 0.0, 100.0))
    path = write_shape(tmp_path / 'tall.stl', tall)
    # build_mm z is 165 mm; the part is 200 mm tall. Without clipping enabled,
    # placement refuses a part that cannot fit at all (a VoxelMillError, not a
    # crash); with clipping enabled the pipeline instead returns a structured
    # report that says so.
    settings = small()
    with pytest.raises(VoxelMillError) as excinfo:
        prepare(path, settings)
    assert isinstance(excinfo.value.code, str)

    clipped = small(assembly={'clip_to_build_volume': True})
    report = prepare(path, clipped, allow_unresolved=True)
    assert not report['stages']['build_volume']['fits']
    assert not report['validation']['passed']


def test_sub_layer_thick_feature_produces_a_structured_result(tmp_path):
    # A 0.05 mm plate is a quarter of the 0.2 mm layer height: thinner than
    # one printed layer can resolve.
    thin = m.Manifold.cube((10.0, 10.0, 0.05), True).translate((0.0, 0.0, 0.025))
    path = write_shape(tmp_path / 'thin.stl', thin)
    settings = small()
    report = prepare(path, settings, lift_mm=0.0, allow_unresolved=True)
    assert 'validation' in report and 'checks' in report['validation']
    assert isinstance(report['validation']['passed'], bool)


def overhang_bracket_path(tmp_path):
    post = m.Manifold.cube((3.0, 3.0, 6.0))
    arm = m.Manifold.cube((3.0, 3.0, 2.0)).translate((3.0, 0.0, 4.0))
    return write_shape(tmp_path / 'edge.stl', post + arm)


def _support_range_fields():
    fields = []
    for path, field in FIELDS.items():
        if path.startswith('support.') and field.range is not None:
            key = path.split('.', 1)[1]
            fields.append(pytest.param(path, key, field.range[0], id=f'{path}-min'))
            fields.append(pytest.param(path, key, field.range[1], id=f'{path}-max'))
    return fields


@pytest.mark.parametrize('path, key, value', _support_range_fields())
def test_support_field_bounds_never_crash(tmp_path, path, key, value):
    printer = {'pixels': [200, 160], 'pixel_pitch_mm': [0.1, 0.1], 'build_mm': [20.0, 16.0, 40.0]}
    shape_path = overhang_bracket_path(tmp_path)
    try:
        settings = resolve_settings(overrides={
            'printer': printer, 'process': {'layer_height_mm': 0.2}, 'support': {key: value}})
    except VoxelMillError:
        pytest.skip(f'{path}={value} is invalid in combination with the other defaults')
        return
    try:
        report = prepare(shape_path, settings, allow_unresolved=True, drainage=False)
        assert isinstance(report, dict)
        assert 'validation' in report
    except VoxelMillError as error:
        assert isinstance(error.code, str)


def test_mirrored_printer_and_four_level_antialias_slice_round_trip(tmp_path):
    post = m.Manifold.cube((4.0, 8.0, 16.0)).translate((0.0, -4.0, 0.0))
    arm = m.Manifold.cube((18.0, 8.0, 3.0)).translate((4.0, -4.0, 13.0))
    path = write_shape(tmp_path / 'bracket.stl', post + arm)
    settings = small(printer={'image_mirror_x': True, 'image_mirror_y': True,
                              'image_mirror_verified': True},
                     process={'antialias_levels': 4})
    prepared = tmp_path / 'prepared.stl'
    report = prepare(path, settings, output=prepared, allow_unresolved=True)
    assert report['validation']['passed'], report['validation']['checks']

    goo_path = tmp_path / 'out.goo'
    result = slice_stl(prepared, goo_path, settings, allow_unresolved=True)
    assert result['written']
    assert result['verification']['decoded_pixels'] == 'pass'
    assert result['verification']['framing'] == 'pass'
    assert result['antialias']['levels'] == 4


def test_ctb_convert_round_trip_from_a_supported_part(tmp_path):
    post = m.Manifold.cube((4.0, 8.0, 16.0)).translate((0.0, -4.0, 0.0))
    arm = m.Manifold.cube((18.0, 8.0, 3.0)).translate((4.0, -4.0, 13.0))
    path = write_shape(tmp_path / 'bracket.stl', post + arm)
    settings = small()
    prepared = tmp_path / 'prepared.stl'
    report = prepare(path, settings, output=prepared, allow_unresolved=True)
    assert report['validation']['passed'], report['validation']['checks']

    goo_path = tmp_path / 'out.goo'
    ctb_path = tmp_path / 'converted.ctb'
    result = slice_stl(prepared, goo_path, settings, allow_unresolved=True)
    assert result['written']
    convert_slices(goo_path, ctb_path, settings)
    verified = verify_ctb(ctb_path, settings)
    assert verified['format'] == 'ctb'
    assert verified['report']['checks']['raster_connectivity'] == 'pass'
    assert verified['report']['checks']['overlap'] == 'pass'
