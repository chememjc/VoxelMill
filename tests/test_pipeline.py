import json

import numpy as np
import pytest
import manifold3d as m

from voxelmill.cli import main
from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.geometry import manifold_triangles
from voxelmill.mesh import open_stl, write_stl
from voxelmill.pipeline import prepare
from voxelmill.project import load_project


@pytest.fixture
def sphere(tmp_path):
    path = tmp_path / 'sphere.stl'
    write_stl(path, manifold_triangles(m.Manifold.sphere(6, 48)))
    return path


def small(**overrides):
    base = {'process': {'layer_height_mm': 0.2},
            'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [0.1, 0.1], 'build_mm': [100., 80., 165.]}}
    for section, values in overrides.items():
        base.setdefault(section, {}).update(values)
    return resolve_settings(overrides=base)


def test_prepare_writes_a_verified_supported_export(tmp_path, sphere):
    output = tmp_path / 'supported.stl'
    report = prepare(sphere, small(), output=output, components=True,
                     project=tmp_path / 'part.voxmil', drainage=True)
    assert report['validation']['passed'], report['validation']['checks']
    assert output.exists()
    assert report['export']['written'] and not report['export']['warned']
    for name in ('model', 'supports', 'raft'):
        assert (tmp_path / f'supported-{name}.stl').exists()
    # The evidence describes the file on disk, not the in-memory solid.
    with open_stl(output) as reopened:
        assert report['validation']['metrics']['reopened']['sha256'] == reopened.asset.sha256
        assert report['validation']['metrics']['reopened']['triangles'] == reopened.asset.triangle_count
    state = load_project(tmp_path / 'part.voxmil')
    assert state['placement']['model_lift_mm'] == 5.0
    assert state['validation']['passed']
    assert state['source']['sha256']


@pytest.fixture
def hollow(tmp_path):
    solid = m.Manifold.cube((12, 12, 12), True) - m.Manifold.cube((6, 6, 6), True)
    path = tmp_path / 'hollow.stl'
    write_stl(path, manifold_triangles(solid))
    return path


def test_export_is_withheld_when_validation_fails_but_evidence_is_kept(tmp_path, hollow):
    output = tmp_path / 'blocked.stl'
    report = prepare(hollow, small(repair={'seal_voids': False}), output=output)
    assert not report['validation']['passed']
    assert not output.exists()
    assert report['export']['written'] is False
    assert 'allow-unresolved' in report['export']['reason']
    assert 'enclosed_voids' in report['export']['failed_checks']
    assert report['validation']['metrics']['enclosed_voids']['count'] == 1
    assert report['validation']['metrics']['enclosed_voids']['volume_mm3'] == pytest.approx(216, rel=.05)


def test_sealing_removes_the_cavity_that_blocked_the_export(tmp_path, hollow):
    output = tmp_path / 'sealed.stl'
    report = prepare(hollow, small(repair={'seal_voids': True}), output=output)
    assert report['stages']['cavity_fill']['filled_shell_count'] == 1
    assert report['stages']['cavity_fill']['added_volume_mm3'] == pytest.approx(216, rel=1e-6)
    assert report['validation']['metrics']['enclosed_voids']['count'] == 0
    assert report['validation']['passed'] and output.exists()


def test_allow_unresolved_keeps_a_warned_export_with_its_diagnostics(tmp_path, hollow):
    output = tmp_path / 'warned.stl'
    report = prepare(hollow, small(repair={'seal_voids': False}), output=output,
                     allow_unresolved=True)
    assert output.exists()
    assert report['export']['warned'] is True
    assert not report['validation']['passed']
    assert report['validation']['metrics']['enclosed_voids']['count'] == 1
    assert any(d['code'] == 'enclosed_voids' for d in report['validation']['diagnostics'])


def test_a_check_that_did_not_run_never_reports_as_passed(tmp_path, sphere):
    report = prepare(sphere, small(), drainage=False)
    assert report['validation']['checks']['drainage_bottlenecks'] == 'not_run'
    assert not report['validation']['passed']


def test_support_void_policy_ignore_drops_tip_crevices_but_not_model_cavities(tmp_path):
    """Strict policy fails support drainage; ignore (the default) keeps model cavities failing."""
    sphere = tmp_path / 'sphere.stl'
    write_stl(sphere, manifold_triangles(m.Manifold.sphere(6, 64)))
    supported = resolve_settings(overrides={'support': {'base_type': 'none'},
                                            'repair': {'seal_voids': False,
                                                       'support_void_policy': 'fail'}})
    failed = prepare(sphere, supported, drainage=True, track_voids=False)
    assert failed['validation']['checks']['drainage_bottlenecks'] == 'fail'
    assert failed['validation']['metrics']['drainage']['bottlenecked_components'] >= 1

    ignored = prepare(sphere, resolve_settings(overrides={
        'support': {'base_type': 'none'},
        'repair': {'seal_voids': False, 'support_void_policy': 'ignore'},
    }), drainage=True, track_voids=False)
    assert ignored['validation']['checks']['drainage_bottlenecks'] == 'pass'
    assert ignored['validation']['metrics']['ignored_support_bottlenecks']['bottlenecked_components'] >= 1
    assert ignored['validation']['metrics']['drainage']['support_bottlenecked_components'] >= 1
    assert ignored['validation']['metrics']['drainage']['model_bottlenecked_components'] == 0

    hollow = tmp_path / 'hollow.stl'
    solid = m.Manifold.cube((12, 12, 12), True) - m.Manifold.cube((6, 6, 6), True)
    write_stl(hollow, manifold_triangles(solid))
    model_void = prepare(hollow, resolve_settings(overrides={
        'support': {'automatic': False, 'base_type': 'none'},
        'repair': {'seal_voids': False, 'support_void_policy': 'ignore'},
    }), drainage=False, lift_mm=0.0)
    assert model_void['validation']['checks']['enclosed_voids'] == 'fail'
    assert model_void['validation']['metrics']['enclosed_voids']['count'] >= 1


def test_support_void_policy_fill_seals_exact_pockets_and_raster_reports_not_run(tmp_path):
    from voxelmill.assembly import assemble, prepare_model
    from voxelmill.geometry import fill_enclosed_cavities

    model = m.Manifold.cube([6, 6, 6], True)
    shell = m.Manifold.cube([12, 12, 12], True) - m.Manifold.cube([8, 8, 8], True)
    union = model + shell
    assert any(c.volume() < 0 for c in union.decompose())
    filled, report = fill_enclosed_cavities(union)
    assert report['filled_shell_count'] == 1
    assert filled.volume() == pytest.approx(12 ** 3)
    assert all(c.volume() > 0 for c in filled.decompose())

    settings = small(repair={'seal_voids': False, 'support_void_policy': 'fill',
                             'aggressiveness': 'none'},
                     assembly={'union': 'auto'})
    path = tmp_path / 'cube.stl'
    write_stl(path, manifold_triangles(model))
    # Raster path: no solid to fill after union when repair is none.
    prepared = prepare_model(manifold_triangles(model).astype(np.float32), settings)
    assert prepared.solid is None
    assembly = assemble(prepared, [shell], None)
    assert assembly.solid is None
    assert assembly.report['support_cavity_fill']['status'] == 'not_run'
    assert 'drainage necks' in assembly.report['support_cavity_fill']['consequence']

    exact = small(repair={'seal_voids': False, 'support_void_policy': 'fill'})
    prepared = prepare_model(manifold_triangles(model).astype(np.float32), exact)
    assembly = assemble(prepared, [shell], None)
    assert assembly.solid is not None
    assert assembly.report['support_cavity_fill']['filled_shell_count'] >= 1
    assert assembly.solid.volume() == pytest.approx(12 ** 3, rel=1e-6)


def test_placement_failure_is_named_exactly(tmp_path, sphere):
    with pytest.raises(VoxelMillError, match='no feasible placement found'):
        prepare(sphere, small(printer={'pixels': [80, 80], 'pixel_pitch_mm': [0.1, 0.1],
                                       'build_mm': [8., 8., 165.]}), drainage=False)


def test_aggressive_repair_rescues_a_mesh_manifold_rejects(tmp_path):
    source = tmp_path / 'holed.stl'
    triangles = manifold_triangles(m.Manifold.cube((8, 8, 8)).refine_to_length(1.0))
    write_stl(source, np.delete(triangles, [0, 1], axis=0))
    with pytest.raises(VoxelMillError, match='Exact union required'):
        prepare(source, small(assembly={'union': 'exact'}), drainage=False)
    fallback = prepare(source, small(), drainage=False)
    assert fallback['stages']['assembly']['union'] == 'raster'
    assert not fallback['validation']['passed']
    settings = small(repair={'aggressiveness': 'aggressive', 'voxel_size_mm': 0.4,
                             'max_deviation_mm': 0.4})
    report = prepare(source, settings, drainage=False)
    assert report['stages']['repair']['operation'] == 'occupancy_voxel_repair'
    assert report['stages']['repair']['deviation_within_limit']
    assert report['stages']['model']['volume_mm3'] == pytest.approx(512, rel=0.05)


def test_cli_prepare_round_trip_and_exit_codes(tmp_path, sphere, capsys):
    report = tmp_path / 'report.json'
    output = tmp_path / 'cli.stl'
    code = main(['prepare', str(sphere), '--output', str(output), '--report', str(report),
                 '--layer-height-mm', '0.2',
                 '--set', 'printer.pixels=[1000,800]',
                 '--set', 'printer.pixel_pitch_mm=[0.1,0.1]',
                 '--set', 'printer.build_mm=[100.0,80.0,165.0]'])
    assert code == 0
    payload = json.loads(report.read_text())
    assert payload['validation']['passed']
    assert output.exists()
    code = main(['validate', str(output), '--report', str(tmp_path / 'v.json'),
                 '--layer-height-mm', '0.2',
                 '--set', 'printer.pixels=[1000,800]',
                 '--set', 'printer.pixel_pitch_mm=[0.1,0.1]',
                 '--set', 'printer.build_mm=[100.0,80.0,165.0]'])
    assert code == 0
    assert json.loads((tmp_path / 'v.json').read_text())['report']['checks']['closed_surface'] == 'pass'


def test_cli_reports_structured_errors(tmp_path, capsys):
    code = main(['inspect', str(tmp_path / 'missing.stl')])
    assert code == 3
    error = json.loads(capsys.readouterr().err)['error']
    assert error['code'] == 'mesh_io'
    assert code == 3
    assert main(['prepare', 'x', '--set', 'nonsense']) == 3
    assert main(['prepare', 'x', '--set', 'printer.build_mm=oops']) == 3


def test_failed_export_preserves_existing_file(tmp_path, hollow):
    output = tmp_path / 'existing.stl'
    output.write_bytes(b'previous validated output')
    report = prepare(hollow, small(repair={'seal_voids': False}), output=output)
    assert not report['export']['written']
    assert output.read_bytes() == b'previous validated output'


def test_export_cannot_replace_original(sphere):
    original = sphere.read_bytes()
    with pytest.raises(VoxelMillError, match='original source'):
        prepare(sphere, small(), output=sphere)
    assert sphere.read_bytes() == original


def test_correction_records_one_entry_per_search_pass_and_a_full_reslice_final(tmp_path, sphere, monkeypatch):
    """``prepare`` now finds its contact set via ``island_guard.route_without_islands``,
    which rasterizes the in-memory assembly and never writes anything, then gives
    the accepted result the same write-and-reslice treatment as before. A fake
    guard stands in for a real multi-pass search so the passes it produced can be
    checked without needing geometry that genuinely fools the router.
    """
    import voxelmill.pipeline as pipeline
    real_assemble = pipeline.assemble
    calls = []
    def fake_route(model, settings, *, replan, budget=None, cancel=None,
                   progress=pipeline.no_progress, max_passes=None, extra_contacts=()):
        calls.append(list(extra_contacts))
        plan, raft = replan(list(extra_contacts) + [(0., 0., 5.)])
        union = real_assemble(model, plan.solids, raft, budget=budget, cancel=cancel)
        passes = [
            {'scan': 'full', 'islands': 1, 'positions': [(0., 0., 5.)], 'positions_truncated': False,
             'discarded_on_crop_edge': 0, 'layers_scanned': 1, 'layer_range': [0, 1], 'grid': [1, 1],
             'crop_bounds_mm': None, 'islands_by_layer': [0], 'pass': 1, 'contacts_added': 1,
             'stopped': None},
            {'scan': 'full', 'islands': 0, 'positions': [], 'positions_truncated': False,
             'discarded_on_crop_edge': 0, 'layers_scanned': 1, 'layer_range': [0, 1], 'grid': [1, 1],
             'crop_bounds_mm': None, 'islands_by_layer': [], 'pass': 2, 'contacts_added': 0,
             'stopped': 'no_islands_found'},
        ]
        return {'plan': plan, 'raft': raft, 'union': union, 'contacts': [(0., 0., 5.)],
                'passes': passes, 'islands_remaining': 0, 'island_positions': [],
                'max_passes': max_passes or 5, 'resolved': True}
    monkeypatch.setattr(pipeline, 'route_without_islands', fake_route)
    report = prepare(sphere, small())
    assert calls == [[]]
    # One entry per search pass the guard reported, plus the final full reslice.
    assert len(report['passes']) == 3
    first, second, final = report['passes']
    assert first['kind'] == 'search' and first['islands'] == 1 and first['contacts_added'] == 1
    assert second['kind'] == 'search' and second['islands'] == 0
    # A search pass never wrote or reread an STL, so it must say so rather than
    # carry a stale or missing validation.
    assert isinstance(first['validation'], str) and 'not_run' in first['validation']
    assert isinstance(second['validation'], str) and 'not_run' in second['validation']
    assert final['kind'] == 'full_reslice'
    assert 'reopened' in final['validation']['metrics']
    assert final['validation']['checks']['closed_surface'] == 'pass'


def test_correction_is_skipped_entirely_when_automatic_support_is_off(tmp_path, sphere, monkeypatch):
    import voxelmill.pipeline as pipeline
    planner = pipeline.plan_supports
    calls = []
    def capture(*args, **kwargs):
        calls.append(list(kwargs.get('extra_contacts', [])))
        return planner(*args, **kwargs)
    guard_calls = []
    def unexpected_route(*args, **kwargs):
        guard_calls.append(1)
        raise AssertionError('route_without_islands must not run when automatic support is off')
    monkeypatch.setattr(pipeline, 'plan_supports', capture)
    monkeypatch.setattr(pipeline, 'route_without_islands', unexpected_route)
    report = prepare(sphere, small(support={'automatic': False}))
    assert not guard_calls
    assert calls == [[]]
    assert len(report['passes']) == 1 and report['passes'][0]['kind'] == 'full_reslice'
    assert not report['validation']['passed']


def test_cleanup_scratch_ignores_windows_sharing_violations():
    from voxelmill.pipeline import _cleanup_scratch

    class Scratch:
        def cleanup(self):
            raise PermissionError('WinError 32')

    _cleanup_scratch(Scratch())
