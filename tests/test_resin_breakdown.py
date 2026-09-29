"""Resin usage split into model and supports from the reslice pixels."""
from pathlib import Path

import manifold3d as m
import numpy as np
import pytest

from voxelmill import geometry
from voxelmill.assembly import Assembly, GroupVolumeTap, PartGroup, volume_tap
from voxelmill.config import resin_usage, resin_usage_from_metrics, resolve_settings
from voxelmill.contracts import CancellationToken, ResourceBudget, no_progress
from voxelmill.mesh import write_stl
from voxelmill.pipeline import _reslice
from voxelmill.report_html import render_report

ROOT = Path(__file__).resolve().parents[1]
PITCH, LAYER = 0.1, 0.1
# Box edges sit on pixel edges (grid origin -50, -40 mm at 0.1 mm pitch), so
# the raster is exact up to float rounding at a pixel center.
MODEL_MM3 = 10 * 10 * 5          # z 3..8
SUPPORT_MM3 = 2 * 2 * 3          # z 0..3.2, the 0.2 mm inside the model is model
ONE_RING = 4 * 10 * PITCH * 5 + 10 * 10 * LAYER   # one pixel ring plus one layer


def settings(**process):
    return resolve_settings(overrides={
        'process': {'layer_height_mm': LAYER, **process},
        'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [PITCH, PITCH],
                    'build_mm': [100., 80., 165.]}})


def box(size, low):
    return m.Manifold.cube(size).translate(low)


def tri(solid):
    return geometry.manifold_triangles(solid).astype(np.float32)


def model_and_support():
    return box((10, 10, 5), (-5, -5, 3)), box((2, 2, 3.2), (-1, -1, 0))


def assembly(s, exact):
    model, support = model_and_support()
    groups = (PartGroup('model', tri(model), 'accepted_solid'),
              PartGroup('supports_and_raft', tri(support), 'closed_positive'))
    solid = m.Manifold.batch_boolean([model, support], m.OpType.Add) if exact else None
    return Assembly(groups, solid, s, {'union': 'exact' if exact else 'raster'})


def reslice(s, union, path):
    write_stl(path, union.triangle_arrays, CancellationToken(), no_progress)
    return _reslice(path, s, ResourceBudget(**s['resources']), CancellationToken(), no_progress,
                    track_voids=False, assembly=union)


@pytest.mark.parametrize('exact', [True, False], ids=['exact_union', 'raster_parity'])
def test_split_matches_analytic_volumes_and_adds_up(tmp_path, exact):
    s = settings()
    report = reslice(s, assembly(s, exact), tmp_path / 'union.stl')
    groups = report.metrics['raster_volume_by_group']
    assert groups['model_pixels'] + groups['supports_pixels'] == groups['total_pixels']
    assert groups['total_pixels'] == report.metrics['exposed_pixels']
    assert groups['total_mm3'] == report.metrics['raster_volume_mm3']
    assert groups['model_mm3'] == pytest.approx(MODEL_MM3, abs=ONE_RING)
    assert groups['supports_mm3'] == pytest.approx(SUPPORT_MM3, abs=4 * 2 * PITCH * 3 + 4 * LAYER)
    usage = resin_usage_from_metrics(s, report.metrics)
    rows = usage['breakdown']
    assert rows['unavailable_reason'] is None
    assert rows['model']['volume_ml'] + rows['supports']['volume_ml'] == pytest.approx(
        rows['total']['volume_ml'], rel=1e-12)
    assert usage['volume_ml'] == rows['total']['volume_ml']
    assert usage['mass_g'] is None and usage['note']


def test_antialiased_split_still_adds_up(tmp_path):
    s = settings(antialias_levels=2)
    report = reslice(s, assembly(s, True), tmp_path / 'aa.stl')
    groups = report.metrics['raster_volume_by_group']
    assert groups['total_pixels'] == report.metrics['exposed_pixels']
    assert groups['model_pixels'] + groups['supports_pixels'] == groups['total_pixels']
    assert groups['model_mm3'] == pytest.approx(MODEL_MM3, abs=2 * ONE_RING)


def test_density_turns_the_split_into_grams(tmp_path):
    s = resolve_settings(ROOT / 'profiles/mars5-ultra.ptr', ROOT / 'profiles/sunlu-abs-like-gray.res',
                         {'process': {'layer_height_mm': LAYER},
                          'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [PITCH, PITCH],
                                      'build_mm': [100., 80., 165.]}})
    assert s['resin']['density_g_cm3'] == 1.10
    report = reslice(s, assembly(s, True), tmp_path / 'union.stl')
    usage = resin_usage_from_metrics(s, report.metrics)
    for name in ('total', 'model', 'supports'):
        row = usage['breakdown'][name]
        assert row['mass_g'] == pytest.approx(row['volume_ml'] * 1.10)
    assert usage['mass_g'] == pytest.approx(usage['volume_ml'] * 1.10)
    assert usage['note'] is None


def test_no_support_group_is_all_model_and_no_assembly_is_no_tap(tmp_path):
    s = settings()
    model, _ = model_and_support()
    union = Assembly((PartGroup('model', tri(model), 'accepted_solid'),), model, s, {})
    report = reslice(s, union, tmp_path / 'model.stl')
    groups = report.metrics['raster_volume_by_group']
    assert groups['supports_pixels'] == 0 and groups['model_pixels'] == groups['total_pixels']
    assert volume_tap(iter(()), None, None, s) is None


def test_mismatched_pixel_counts_do_not_claim_a_split():
    s = settings()
    metrics = {'raster_volume_mm3': 10.0, 'exposed_pixels': 5,
               'raster_volume_by_group': {'total_pixels': 6, 'model_mm3': 1.0, 'supports_mm3': 9.0}}
    rows = resin_usage_from_metrics(s, metrics)['breakdown']
    assert rows['model']['volume_ml'] is None and rows['unavailable_reason']


def test_tap_passes_layers_through_unchanged():
    s = settings()
    from voxelmill.raster import MeshLayerStream
    model, support = model_and_support()
    union = tri(m.Manifold.batch_boolean([model, support], m.OpType.Add))
    bounds = geometry.triangle_bounds(union)
    plain = [layer.mask.copy() for layer in MeshLayerStream(union, bounds, s)]
    stream = MeshLayerStream(union, bounds, s)
    tap = GroupVolumeTap(stream, tri(model), stream.grid, s)
    tapped = [layer.mask for layer in tap]
    assert len(tapped) == len(plain)
    assert all(np.array_equal(a, b) for a, b in zip(plain, tapped))


def test_html_report_shows_the_resin_rows():
    s = resolve_settings(overrides={'resin': {'density_g_cm3': 1.1, 'cost_per_liter': 40.0,
                                              'currency': 'EUR'}})
    usage = resin_usage(s, 25000.0, model_mm3=20000.0, supports_mm3=5000.0)
    html = render_report({'command': 'prepare', 'stages': {'resin_usage': usage}})
    assert '<h2>Resin usage</h2>' in html
    assert '<td>model</td><td>20.00</td><td>22.00</td><td>0.80</td>' in html
    assert '<td>supports (incl. raft/base)</td><td>5.00</td><td>5.50</td><td>0.20</td>' in html
    assert '<td>total</td><td>25.00</td><td>27.50</td><td>1.00</td>' in html
    assert 'cost (EUR)' in html
    unset = resin_usage(resolve_settings(), 25000.0, breakdown_unavailable='merged STL')
    html = render_report({'command': 'slice', 'resin_usage': unset})
    assert '<td>total</td><td>25.00</td><td>—</td></tr>' in html
    assert 'set resin density_g_cm3 to compute grams' in html
    assert 'Model/support split unavailable: merged STL' in html
    assert 'cost (' not in html


def test_gui_export_report_carries_the_breakdown(tmp_path):
    from voxelmill.gui import services
    from voxelmill.gui.document import Document
    s = settings()
    union = assembly(s, True)
    report = services.export_and_validate(Document(s), union, tmp_path / 'gui.stl',
                                          CancellationToken(), no_progress, drainage=False)
    usage = report.metrics['resin_usage']
    rows = usage['breakdown']
    assert rows['unavailable_reason'] is None
    assert rows['model']['volume_ml'] * 1000 == pytest.approx(MODEL_MM3, abs=ONE_RING)
    assert rows['total']['volume_ml'] == pytest.approx(report.metrics['raster_volume_mm3'] / 1000)
    assert report.to_dict()['metrics']['resin_usage']['breakdown']['supports']['volume_ml'] > 0
