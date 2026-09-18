"""Grouped occupancy must match exported bytes, including reversed contacts."""
import numpy as np
import pytest
import manifold3d as m

from voxelmill import geometry, _native
from voxelmill.assembly import PartGroup, UnionLayerStream, assemble, prepare_model
from voxelmill.config import resolve_settings
from voxelmill.contracts import CancellationToken, Canceled, VoxelMillError, ResourceBudget, no_progress
from voxelmill.mesh import write_stl
from voxelmill.pipeline import _reslice, prepare
from voxelmill.raster import MeshLayerStream


def settings(**overrides):
    base = {'process': {'layer_height_mm': .2},
            'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [.1, .1], 'build_mm': [100., 80., 165.]}}
    for section, values in overrides.items():
        base.setdefault(section, {}).update(values)
    return resolve_settings(overrides=base)


def triangles(solid):
    return geometry.manifold_triangles(solid).astype(np.float32)


def test_grouped_or_is_bit_identical_to_exact_supported_sphere():
    from voxelmill.supports import plan_supports
    s = settings()
    solid = m.Manifold.sphere(6, 48).translate((0, 0, 11))
    model = prepare_model(triangles(solid), s)
    plan, raft = plan_supports(model.triangles, model.bounds, s)
    assembly = assemble(model, plan.solids, raft)
    exact = MeshLayerStream(assembly.triangle_arrays[0], assembly.bounds, s)
    grouped = UnionLayerStream(assembly.groups, assembly.bounds, s,
                               grid=exact.grid, layer_count=exact.layer_count)
    for actual, reference in zip(exact, grouped):
        np.testing.assert_array_equal(actual.mask, reference.mask)
    assert exact.open_rows == grouped.open_rows == 0
    with pytest.raises(VoxelMillError, match='fresh layer stream'):
        list(grouped)


def test_antialias_model_ors_binary_supports_as_full_intensity():
    """AA model coverage stays gray; binary support tips OR in as 255."""
    s = settings(process={'antialias_levels': 2, 'antialias_supports': False},
                 printer={'pixels': [80, 80], 'pixel_pitch_mm': [.1, .1],
                          'build_mm': [8., 8., 165.], 'edge_clearance_mm': 0})
    solid = m.Manifold.cube((1.2, 1.2, 1.2)).translate((-0.6, -0.6, 0))
    tri = triangles(solid)
    # 45° about Z so the model edge produces partial coverage.
    c = s45 = np.float32(np.sqrt(0.5))
    x, y = tri[..., 0].copy(), tri[..., 1].copy()
    tri[..., 0] = c * x - s45 * y
    tri[..., 1] = s45 * x + c * y
    model = prepare_model(tri, s)
    # Tip outside the cube so its pixels come only from the support group.
    support = geometry.cylinder_between((2.5, 0, 0), (2.5, 0, 1.2), 0.25)
    assembly = assemble(model, [support], None)
    assert [g.name for g in assembly.groups] == ['model', 'supports_and_raft']
    budget = ResourceBudget(workers=1, memory_gib=1)
    model_only = list(UnionLayerStream((assembly.groups[0],), assembly.bounds, s,
                                       budget=budget))
    union = UnionLayerStream(assembly.groups, assembly.bounds, s, budget=budget)
    assert union.antialias_levels == 2 and union.antialias_supports is False
    assert union.antialias_supports_unseparated is False
    saw_model_gray = saw_support_tip = False
    for model_layer, union_layer in zip(model_only, union):
        model_vals = {int(v) for v in np.unique(model_layer.mask)}
        if any(0 < v < 255 for v in model_vals):
            saw_model_gray = True
        only_support = (union_layer.mask != 0) & (model_layer.mask == 0)
        if only_support.any():
            tip_vals = {int(v) for v in np.unique(union_layer.mask[only_support])}
            assert tip_vals == {255}
            saw_support_tip = True
        assert np.count_nonzero(union_layer.mask) >= np.count_nonzero(model_layer.mask)
    assert saw_model_gray and saw_support_tip


def test_parity_catches_penetrating_support_cancellation_with_position(tmp_path):
    s = settings(repair={'aggressiveness': 'none'})
    inverted = triangles(m.Manifold.cube((4, 4, 4)).translate((-2, -2, 1)))[:, ::-1].copy()
    model = prepare_model(inverted, s)
    support = geometry.cylinder_between((0, 0, 0), (0, 0, 2), .6)
    assembly = assemble(model, [support], None)
    path = tmp_path / 'canceled.stl'
    write_stl(path, assembly.triangle_arrays)
    report = _reslice(path, s, ResourceBudget(), CancellationToken(), no_progress, assembly=assembly)
    assert report.checks['closed_surface'] == 'pass'
    assert report.checks['union_raster_parity'] == 'fail'
    evidence = report.metrics['union_raster_parity']
    assert evidence['mismatched_pixels'] > 0
    assert 0 < len(evidence['examples']) <= 16
    assert len(evidence['examples'][0]['position_mm']) == 3
    assert any(d.code == 'union_raster_parity' and d.position_mm for d in report.diagnostics)
    assert not report.passed


def test_degenerate_does_not_change_occupancy_and_cavity_fill_is_honest(tmp_path):
    s = settings()
    clean = triangles(m.Manifold.cube((4, 4, 4)).translate((-2, -2, 0)))
    soup = np.concatenate((clean, clean[:1, :1].repeat(3, axis=1)))
    model = prepare_model(soup, s)
    assembly = assemble(model, [], None)
    assert assembly.report['union'] == 'raster'
    assert model.cavity_fill['status'] == 'not_run'
    assert 'seal_voids' in model.cavity_fill['reason']
    assert assembly.volume() is None
    path = tmp_path / 'degenerate.stl'
    write_stl(path, assembly.triangle_arrays)
    report = _reslice(path, s, ResourceBudget(), CancellationToken(), no_progress, assembly=assembly)
    assert report.checks['union_raster_parity'] == 'pass'
    assert report.passed, report.to_dict()
    expected = MeshLayerStream(clean, geometry.triangle_bounds(clean), s)
    actual = UnionLayerStream(assembly.groups, assembly.bounds, s, grid=expected.grid)
    for a, b in zip(actual, expected):
        np.testing.assert_array_equal(a.mask, b.mask)


def test_none_skips_conversion_and_exact_refuses_it(monkeypatch):
    monkeypatch.setattr(geometry, 'mesh_to_manifold', lambda *a, **kw: pytest.fail('solid conversion ran'))
    tri = triangles(m.Manifold.cube((4, 4, 4)))
    model = prepare_model(tri, settings(repair={'aggressiveness': 'none'}))
    assert model.solid is None and not model.exact_union_attempted
    with pytest.raises(VoxelMillError) as error:
        prepare_model(tri, settings(repair={'aggressiveness': 'none'}, assembly={'union': 'exact'}))
    assert error.value.code == 'exact_union_unavailable'


def test_cancellation_and_budget_never_fall_back(monkeypatch):
    tri = triangles(m.Manifold.cube((2, 2, 2)))
    token = CancellationToken(); token.cancel()
    with pytest.raises(Canceled):
        prepare_model(tri, settings(), cancel=token)
    def limited(*args, **kwargs):
        raise VoxelMillError('memory_budget', 'budget exhausted')
    monkeypatch.setattr(geometry, 'mesh_to_manifold', limited)
    with pytest.raises(VoxelMillError, match='budget exhausted'):
        prepare_model(tri, settings())


def test_clean_prepare_keeps_exact_check_surface_and_none_passes(tmp_path):
    source = tmp_path / 'sphere.stl'
    write_stl(source, triangles(m.Manifold.sphere(6, 48)))
    exact = prepare(source, settings())
    assert exact['validation']['passed']
    assert exact['stages']['assembly']['union'] == 'exact'
    assert 'union_raster_parity' not in exact['validation']['checks']
    raster = prepare(source, settings(repair={'aggressiveness': 'none'}), output=tmp_path / 'out.stl',
                     components=True, project=tmp_path / 'out.voxmil')
    assert raster['stages']['assembly']['union'] == 'raster'
    assert raster['validation']['checks']['union_raster_parity'] == 'pass'
    assert raster['validation']['passed'], raster['validation']
    assert raster['export']['written']
    assert raster['passes'][0]['union_volume_mm3'] is None
    assert raster['passes'][0]['raster_volume_mm3'] > 0


def test_self_intersecting_prepare_produces_passing_export(tmp_path):
    source = tmp_path / 'overlap.stl'
    # Two positively oriented overlapping shells; they must OR, not cancel.
    a = m.Manifold.cube((4, 4, 4))
    write_stl(source, (triangles(a), triangles(a.translate((1, 1, 1)))))
    report = prepare(source, settings(), output=tmp_path / 'prepared.stl', lift_mm=0)
    assert report['stages']['assembly']['union'] == 'raster'
    assert report['validation']['checks']['union_raster_parity'] == 'pass'
    assert report['validation']['passed'], report['validation']
    assert report['export']['written']


def test_open_group_is_fatal_even_when_another_group_fills_its_pixels(tmp_path):
    s = settings(repair={'aggressiveness': 'none'})
    box = triangles(m.Manifold.cube((4, 4, 4)))
    # Remove an upright side face, which opens scanline contours.
    normals = np.cross(box[:, 1] - box[:, 0], box[:, 2] - box[:, 0])
    hole = np.flatnonzero(normals[:, 0] != 0)[0]
    model = prepare_model(np.delete(box, hole, axis=0), s)
    assembly = assemble(model, [m.Manifold.cube((4, 4, 4))], None)
    path = tmp_path / 'open.stl'; write_stl(path, assembly.triangle_arrays)
    report = _reslice(path, s, ResourceBudget(), CancellationToken(), no_progress, assembly=assembly)
    assert report.checks['closed_surface'] == 'fail'
    assert not report.passed


@pytest.mark.parametrize('override', [{'union': 'magic'}, {'require_raster_parity': 1},
                                     {'max_parity_examples': True}, {'max_parity_examples': -1}])
def test_assembly_config_rejects_invalid_values(override):
    with pytest.raises(VoxelMillError):
        settings(assembly=override)


def test_boolean_exception_falls_back_but_exact_policy_refuses(monkeypatch):
    tri = triangles(m.Manifold.cube((4, 4, 4)))
    auto = prepare_model(tri, settings())
    strict = prepare_model(tri, settings(assembly={'union': 'exact'}))
    def rejected(*args):
        raise ValueError('boolean input rejected')
    monkeypatch.setattr(m.Manifold, 'batch_boolean', rejected)
    result = assemble(auto, [], None)
    assert result.report['union'] == 'raster'
    assert result.report['exact_union_blocked_by'][-1]['code'] == 'union_failed'
    with pytest.raises(VoxelMillError) as error:
        assemble(strict, [], None)
    assert error.value.code == 'exact_union_unavailable'


def test_gui_services_share_raster_model_and_export_evidence(tmp_path):
    from voxelmill.gui import services
    from voxelmill.gui.document import Document
    from voxelmill.supports import plan_supports
    s = settings()
    tri = triangles(m.Manifold.sphere(6, 48).translate((0, 0, 11)))
    soup = np.concatenate((tri, tri[:1, :1].repeat(3, axis=1)))
    doc = Document(s)
    token = CancellationToken()
    built = services.build_model(doc, soup, None, token, no_progress)
    assert built['solid'].solid is None
    plan, raft = plan_supports(built['triangles'], built['bounds'], s)
    doc.derived.plan = plan
    union = services.assemble(built['solid'], plan, raft)
    report = services.export_and_validate(doc, union, tmp_path / 'gui.stl', token, no_progress)
    assert report.passed, report.to_dict()
    assert report.checks['union_raster_parity'] == 'pass'
    assert any(d.code == 'exact_union_unavailable' and d.severity == 'warning' for d in report.diagnostics)
    payload = services.slice_layer(union, s, 10, ResourceBudget(), token)
    fresh = UnionLayerStream(union.groups, union.bounds, s)
    layer = next(layer for layer in fresh if layer.index == 10)
    np.testing.assert_array_equal(payload['mask'], layer.mask)
    doc.saved_validation = report.to_dict()
    doc.save(tmp_path / 'gui.voxmil')
    restored = Document.load(tmp_path / 'gui.voxmil')
    assert restored.saved_validation['checks']['union_raster_parity'] == 'pass'
    assert any(d['code'] == 'exact_union_unavailable' for d in restored.saved_validation['diagnostics'])
    warning = next(d for d in restored.saved_validation['diagnostics'] if d['code'] == 'exact_union_unavailable')
    assert warning['details']['cavity_fill']['status'] == 'not_run'


def test_old_schema_one_project_inherits_only_missing_assembly_defaults(tmp_path):
    from voxelmill.gui.document import Document
    from voxelmill.project import SCHEMA_VERSION, save_project
    s = settings()
    legacy = settings(); del legacy['assembly']
    path = tmp_path / 'legacy.voxmil'
    save_project(path, {'schema_version': SCHEMA_VERSION, 'settings': legacy})
    assert Document.load(path).settings == s
    legacy['assembly'] = {'union': 'exact'}
    save_project(path, {'schema_version': SCHEMA_VERSION, 'settings': legacy})
    with pytest.raises(VoxelMillError, match='Incomplete resolved assembly'):
        Document.load(path)


def test_parity_disabled_is_not_a_passing_validation(tmp_path):
    s = settings(repair={'aggressiveness': 'none'}, assembly={'require_raster_parity': False})
    model = prepare_model(triangles(m.Manifold.cube((4, 4, 4))), s)
    union = assemble(model, [], None)
    path = tmp_path / 'unverified.stl'; write_stl(path, union.triangle_arrays)
    report = _reslice(path, s, ResourceBudget(), CancellationToken(), no_progress, assembly=union)
    assert report.checks['union_raster_parity'] == 'not_run'
    assert not report.passed


def test_cli_reports_raster_banner_and_ptr_accepts_assembly(tmp_path, capsys):
    import json
    from voxelmill.cli import main
    source = tmp_path / 'sphere.stl'
    write_stl(source, triangles(m.Manifold.sphere(6, 48)))
    profile = tmp_path / 'small.ptr'
    profile.write_text('schema_version=1\n[printer]\npixels=[1000,800]\npixel_pitch_mm=[0.1,0.1]\nbuild_mm=[100.0,80.0,165.0]\n[process]\nlayer_height_mm=0.2\n[assembly]\nmax_parity_examples=2\n')
    code = main(['prepare', str(source), '--printer', str(profile), '--repair', 'none',
                 '--set', 'assembly.union=auto', '--output', str(tmp_path / 'prepared.stl')])
    captured = capsys.readouterr()
    assert code == 0
    assert 'WARNING' in captured.err and 'raster union' in captured.err
    report = json.loads(captured.out)
    assert report['stages']['assembly']['union'] == 'raster'
    assert report['validation']['checks']['union_raster_parity'] == 'pass'
