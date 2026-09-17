"""Per-axis scale, mirror and measurement, shared by the CLI and the editor."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from voxelmill import geometry
from voxelmill.cli import main
from voxelmill.contracts import CancellationToken, VoxelMillError
from voxelmill.mesh import write_stl
from voxelmill.pipeline import measure_stl

from test_goo import cube_triangles, small_settings

ROOT = Path(__file__).resolve().parents[1]
CANCEL = CancellationToken()


def signed_volume(triangles):
    t = np.asarray(triangles, dtype=float)
    return float(np.einsum('ij,ij->i', t[:, 0], np.cross(t[:, 1], t[:, 2])).sum()) / 6.0


def place(scale=(1.0, 1.0, 1.0), mirror=(False, False, False), triangles=None):
    triangles = cube_triangles() if triangles is None else triangles
    placement, fits, overflow = geometry.placement_or_overflow(
        triangles, small_settings(), (0, 0, 0), (0, 0), 0.0, CANCEL, scale, mirror)
    return placement


# ---- the transform itself --------------------------------------------------

def test_the_identity_pose_is_recorded_and_carries_no_note():
    placement = place()
    assert placement.scale == [1.0, 1.0, 1.0]
    assert placement.mirror == [False, False, False]
    assert geometry.scale_note(placement.scale, placement.mirror) is None


def test_uniform_and_per_axis_scale_change_the_placed_size():
    plain = np.asarray(place().bounds, dtype=float)
    plain_size = plain[1] - plain[0]
    uniform = np.asarray(place(scale=(2.0, 2.0, 2.0)).bounds, dtype=float)
    np.testing.assert_allclose(uniform[1] - uniform[0], plain_size * 2)
    per_axis = np.asarray(place(scale=(2.0, 1.0, 0.5)).bounds, dtype=float)
    np.testing.assert_allclose(per_axis[1] - per_axis[0], plain_size * [2.0, 1.0, 0.5])
    # Scaling still lands the part on the plate at the requested lift.
    assert per_axis[0][2] == pytest.approx(0.0)


def test_a_mirror_reverses_winding_so_the_part_stays_solid():
    """A negative determinant turns every outward normal inward.

    Without the reversal the nonzero-winding raster reads the whole part as
    empty and the exact union reads it as a hole, so this is correctness, not
    cosmetics.
    """
    triangles = cube_triangles()
    before = signed_volume(triangles)
    assert before > 0
    matrix = np.asarray(place(mirror=(True, False, False)).matrix)
    assert np.linalg.det(matrix[:3, :3]) < 0
    after = signed_volume(np.concatenate(list(
        geometry.iter_transformed_triangles(triangles, matrix))))
    assert after == pytest.approx(before)


def test_a_proper_rotation_is_left_alone():
    triangles = cube_triangles()
    matrix = np.asarray(place().matrix)
    assert np.linalg.det(matrix[:3, :3]) > 0
    out = np.concatenate(list(geometry.iter_transformed_triangles(triangles, matrix)))
    np.testing.assert_allclose(out[0, 0], (np.asarray(triangles[0, 0], dtype=float)
                                           @ matrix[:3, :3].T + matrix[:3, 3]))


def test_mirroring_two_axes_is_a_rotation_and_keeps_the_original_order():
    """Two flips compose to a proper motion, so nothing needs reversing."""
    matrix = np.asarray(place(mirror=(True, True, False)).matrix)
    assert np.linalg.det(matrix[:3, :3]) > 0
    triangles = cube_triangles()
    out = np.concatenate(list(geometry.iter_transformed_triangles(triangles, matrix)))
    assert signed_volume(out) == pytest.approx(signed_volume(triangles))


def test_scale_guardrails_reject_the_shapes_that_are_almost_always_mistakes():
    for bad in ((0.0, 1.0, 1.0), (-1.0, 1.0, 1.0)):
        with pytest.raises(VoxelMillError, match='must be positive'):
            geometry.scale_matrix(bad)
    for bad in ((1000.0, 1.0, 1.0), (0.001, 1.0, 1.0)):
        with pytest.raises(VoxelMillError, match='must lie between'):
            geometry.scale_matrix(bad)
    with pytest.raises(VoxelMillError, match='one or three finite factors'):
        geometry.scale_matrix((1.0, 1.0))
    with pytest.raises(VoxelMillError, match='one or three finite factors'):
        geometry.scale_matrix((float('nan'), 1.0, 1.0))
    with pytest.raises(VoxelMillError, match='three booleans'):
        geometry.scale_matrix((1.0, 1.0, 1.0), (True, False))
    # A single factor is accepted as a uniform scale.
    matrix, factors, flips = geometry.scale_matrix(np.float64(2.0))
    np.testing.assert_allclose(np.diag(matrix), [2.0, 2.0, 2.0])


def test_the_note_names_the_hazard_rather_than_only_the_numbers():
    assert geometry.scale_note([2, 2, 2], [False] * 3) == 'scaled by 2'
    note = geometry.scale_note([2, 1, 1], [False] * 3)
    assert 'non-uniform' in note and 'thread pitch' in note
    note = geometry.scale_note([1, 1, 1], [False, True, False])
    assert 'mirrored on Y' in note and 'will not assemble' in note


# ---- measurement -----------------------------------------------------------

def test_measure_reports_source_and_placed_sizes_without_writing(tmp_path):
    source = tmp_path / 'cube.stl'
    write_stl(source, cube_triangles())
    payload = measure_stl(source, small_settings(), scale=(2.0, 1.0, 1.0))
    np.testing.assert_allclose(payload['source_size_mm'], [0.8, 0.8, 0.4])
    np.testing.assert_allclose(payload['placed_size_mm'], [1.6, 0.8, 0.4])
    assert payload['diagonal_mm'] == pytest.approx(float(np.linalg.norm([1.6, 0.8, 0.4])))
    assert payload['fits'] is True and payload['overflow_mm'] == [0.0, 0.0, 0.0]
    assert 'non-uniform' in payload['transform_note']
    assert payload['does_not_establish'].startswith('printability')
    assert sorted(p.name for p in tmp_path.iterdir()) == ['cube.stl']


def test_measure_solves_for_the_factor_that_reaches_a_target(tmp_path):
    source = tmp_path / 'cube.stl'
    write_stl(source, cube_triangles())
    payload = measure_stl(source, small_settings(), target_mm=[1.6, 1.6, 1.6])
    np.testing.assert_allclose(payload['per_axis_factor'], [2.0, 2.0, 4.0])
    # The uniform factor is the smallest, so no constrained axis overshoots.
    assert payload['uniform_factor'] == pytest.approx(2.0)
    # A zero leaves that axis unconstrained rather than demanding zero size.
    partial = measure_stl(source, small_settings(), target_mm=[1.6, 0.0, 0.0])
    np.testing.assert_allclose(partial['per_axis_factor'], [2.0, 1.0, 1.0])
    assert partial['uniform_factor'] == pytest.approx(2.0)
    with pytest.raises(VoxelMillError, match='every requested target axis'):
        measure_stl(source, small_settings(), target_mm=[0.0, 0.0, 0.0])
    with pytest.raises(VoxelMillError, match='three finite nonnegative'):
        measure_stl(source, small_settings(), target_mm=[-1.0, 1.0, 1.0])


def test_measure_refuses_an_automatic_search_because_it_is_not_a_chosen_pose(tmp_path):
    source = tmp_path / 'cube.stl'
    write_stl(source, cube_triangles())
    with pytest.raises(VoxelMillError, match='explicit angles'):
        measure_stl(source, small_settings(), rotate='auto')


# ---- command line ----------------------------------------------------------

SMALL = ['--set', 'printer.build_mm=[3.2, 2.4, 10.0]', '--set', 'printer.pixels=[32, 24]',
         '--set', 'printer.pixel_pitch_mm=[0.1, 0.1]', '--set', 'printer.edge_clearance_mm=0',
         '--set', 'process.layer_height_mm=0.1']


def test_cli_measure_prints_the_sizes_and_warns_about_the_transform(tmp_path, capsys):
    source = tmp_path / 'cube.stl'
    write_stl(source, cube_triangles())
    assert main(['measure', str(source), '--printer', str(ROOT / 'profiles/mars5-ultra.ptr'),
                 *SMALL, '--scale', '2', '--mirror', 'x']) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    np.testing.assert_allclose(payload['placed_size_mm'], [1.6, 1.6, 0.8])
    assert payload['mirror'] == [True, False, False]
    assert 'mirrored on X' in captured.err


def test_cli_scale_takes_one_or_three_factors_and_rejects_the_rest():
    from voxelmill.cli import _mirror, _scale
    assert _scale(['2']) == (2.0, 2.0, 2.0)
    assert _scale(['2', '1', '0.5']) == (2.0, 1.0, 0.5)
    assert _scale(None) == (1.0, 1.0, 1.0)
    with pytest.raises(VoxelMillError, match='one uniform factor or three'):
        _scale(['1', '2'])
    with pytest.raises(VoxelMillError, match='finite factors'):
        _scale(['wide'])
    assert _mirror(['x', 'z']) == (True, False, True)
    assert _mirror(None) == (False, False, False)
    with pytest.raises(VoxelMillError, match='takes x, y or z'):
        _mirror(['w'])


def test_prepare_records_the_transform_and_warns_but_does_not_block(tmp_path, capsys):
    source = tmp_path / 'cube.stl'
    write_stl(source, cube_triangles())
    code = main(['prepare', str(source), '--printer', str(ROOT / 'profiles/mars5-ultra.ptr'),
                 *SMALL, '--scale', '1.5', '--mirror', 'y',
                 '--output', str(tmp_path / 'out.stl'), '--max-passes', '1',
                 '--report', str(tmp_path / 'report.json')])
    report = json.loads((tmp_path / 'report.json').read_text())
    transform = report['stages']['transform']
    assert transform['scale'] == [1.5, 1.5, 1.5] and transform['mirror'] == [False, True, False]
    assert 'mirrored on Y' in transform['note']
    np.testing.assert_allclose(transform['placed_size_mm'],
                               np.asarray(transform['source_size_mm']) * 1.5)
    warnings = [d for d in report['validation']['diagnostics'] if d['code'] == 'model_transformed']
    assert len(warnings) == 1 and warnings[0]['severity'] == 'warning'
    # A warning must not turn a passing export into a refusal.
    assert code in (0, 2)
    assert 'mirrored on Y' in capsys.readouterr().err


def test_prepare_refuses_to_combine_a_transform_with_the_automatic_search(tmp_path, capsys):
    source = tmp_path / 'cube.stl'
    write_stl(source, cube_triangles())
    assert main(['prepare', str(source), '--printer', str(ROOT / 'profiles/mars5-ultra.ptr'),
                 *SMALL, '--rotate', 'auto', '--scale', '2']) != 0
    error = json.loads(capsys.readouterr().err)['error']
    assert error['code'] == 'invalid_placement'
    assert 'does not yet consider scale or mirror' in error['message']


def test_a_cli_saved_project_reopens_at_the_scale_it_was_saved_with(tmp_path, capsys):
    """A project that forgets its scale silently prepares a different part.

    ``placement.scale`` is history of the matrix that was used; the top-level
    keys are the authored decision the reload restores, and only the editor
    used to write them.
    """
    source = tmp_path / 'cube.stl'
    write_stl(source, cube_triangles())
    project = tmp_path / 'scaled.voxmil'
    common = ['--printer', str(ROOT / 'profiles/mars5-ultra.ptr'), *SMALL, '--max-passes', '1']
    main(['prepare', str(source), *common, '--scale', '1.5', '--mirror', 'z',
          '--project', str(project), '--report', str(tmp_path / 'first.json')])
    capsys.readouterr()
    saved = json.loads((tmp_path / 'first.json').read_text())
    assert saved['stages']['transform']['scale'] == [1.5, 1.5, 1.5]

    main(['prepare', str(project), *common, '--report', str(tmp_path / 'second.json')])
    capsys.readouterr()
    reopened = json.loads((tmp_path / 'second.json').read_text())['stages']['transform']
    assert reopened['scale'] == [1.5, 1.5, 1.5]
    assert reopened['mirror'] == [False, False, True]
    # An explicit flag still wins over the stored decision.
    main(['prepare', str(project), *common, '--scale', '1',
          '--report', str(tmp_path / 'third.json')])
    capsys.readouterr()
    overridden = json.loads((tmp_path / 'third.json').read_text())['stages']['transform']
    assert overridden['scale'] == [1.0, 1.0, 1.0]
    # --scale alone does not clear a stored mirror; that needs its own flag.
    assert overridden['mirror'] == [False, False, True]
