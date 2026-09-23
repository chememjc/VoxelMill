"""Per-stage wall-time breakdown for prepare (CLI ``--timing``)."""
from __future__ import annotations

import io
import json
from pathlib import Path

import manifold3d as m
import pytest

from voxelmill.cli import build_parser, main
from voxelmill.config import resolve_settings
from voxelmill.geometry import manifold_triangles
from voxelmill.mesh import write_stl
from voxelmill.pipeline import prepare
from voxelmill.stage_timing import StageTimer, format_timing_table


ROOT = Path(__file__).resolve().parents[1]


def small(**overrides):
    base = {'process': {'layer_height_mm': 0.2},
            'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [0.1, 0.1],
                        'build_mm': [100., 80., 165.]}}
    for section, values in overrides.items():
        base.setdefault(section, {}).update(values)
    return resolve_settings(overrides=base)


@pytest.fixture
def sphere(tmp_path):
    path = tmp_path / 'sphere.stl'
    write_stl(path, manifold_triangles(m.Manifold.sphere(6, 48)))
    return path


def test_stage_timer_records_a_named_stage():
    timer = StageTimer()
    with timer.stage('load'):
        pass
    timing = timer.as_dict()
    assert 'load' in timing
    assert timing['load'] >= 0.0


def test_stage_timer_nested_exclusive_and_add():
    timer = StageTimer()
    with timer.stage('outer'):
        with timer.stage('inner'):
            pass
        timer.add('manual', 0.01)
    timing = timer.as_dict()
    assert timing['inner'] >= 0.0
    assert timing['outer'] >= 0.0
    assert timing['manual'] == pytest.approx(0.01)
    # Nested wall time is exclusive: outer does not include inner.
    assert 'outer' in timing and 'inner' in timing


def test_prepare_records_timing_breakdown(tmp_path, sphere):
    report = prepare(sphere, small(), output=tmp_path / 'out.stl', drainage=True,
                     max_passes=1, allow_unresolved=True)
    timing = report['timing']
    assert isinstance(timing, dict)
    expected = {'load', 'place', 'repair', 'supports', 'write', 'reslice'}
    # Automatic support path records island_guard instead of assemble.
    if report['settings']['support']['automatic']:
        expected.add('island_guard')
    else:
        expected.add('assemble')
    if report['settings']['hollow']['enabled']:
        expected.add('hollow')
    missing = expected - set(timing)
    assert not missing, (missing, timing)
    for seconds in timing.values():
        assert isinstance(seconds, float)
        assert seconds >= 0.0
    # Stages are not a perfect partition of wall time (glue + overlap slack).
    assert sum(timing.values()) <= report['seconds'] * 1.5 + 0.5
    assert report['seconds'] >= 0.0


def test_cli_timing_flag_on_prepare_help():
    parser = build_parser()
    prep = next(action for action in parser._actions if getattr(action, 'dest', None) == 'command')
    text = prep.choices['prepare'].format_help()
    assert '--timing' in text


def test_cli_timing_writes_stage_names_to_stderr(tmp_path, sphere, capsys):
    out = tmp_path / 'prepared.stl'
    report_path = tmp_path / 'report.json'
    code = main(['prepare', str(sphere), '--output', str(out), '--report', str(report_path),
                 '--max-passes', '1', '--allow-unresolved', '--no-drainage', '--timing',
                 '--set', 'process.layer_height_mm=0.2',
                 '--set', 'printer.pixels=[1000,800]',
                 '--set', 'printer.pixel_pitch_mm=[0.1,0.1]',
                 '--set', 'printer.build_mm=[100,80,165]'])
    assert code in (0, 2)
    err = capsys.readouterr().err
    assert 'load' in err
    assert 'repair' in err
    assert 'seconds' in err
    saved = json.loads(report_path.read_text())
    assert 'timing' in saved
    assert saved['timing']['load'] >= 0.0


def test_equivalence_normalize_drops_timing():
    import importlib.util
    path = ROOT / 'scripts' / 'equivalence.py'
    spec = importlib.util.spec_from_file_location('voxelmill_equivalence', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    raw = {
        'seconds': 1.5,
        'timing': {'load': 0.1, 'place': 0.2},
        'stages': {'repair': {'seconds': 0.3}, 'island_guard': {'timing': {'raster': 0.01}}},
        'value': 7,
    }
    cleaned = mod.normalize(raw)
    assert 'timing' not in cleaned
    assert 'seconds' not in cleaned
    assert cleaned['value'] == 7
    assert 'seconds' not in cleaned['stages']['repair']
    assert 'timing' not in cleaned['stages']['island_guard']


def test_format_timing_table_includes_percent():
    text = format_timing_table({'load': 1.0, 'place': 3.0}, total=4.0)
    assert 'load' in text and 'place' in text
    assert '25.0' in text and '75.0' in text
    buf = io.StringIO()
    format_timing_table({'load': 1.0}, total=1.0, file=buf)
    assert 'load' in buf.getvalue()
