"""STEP import via FreeCAD headless tessellation (N5)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.importers import (
    apply_step_weld, resolve_freecad, tessellate_step, write_box_step,
)
from voxelmill.mesh import open_stl, inspect_mesh, write_stl


def _freecad_or_skip():
    """Skip when FreeCAD is not configured (CI without VOXELMILL_FREECAD/PATH)."""
    try:
        return resolve_freecad()
    except VoxelMillError as exc:
        pytest.skip(str(exc))


def _settings(**repair):
    overrides = {'repair': repair} if repair else None
    return resolve_settings(None, None, overrides)


def test_resolve_freecad_finds_engine():
    engine = _freecad_or_skip()
    assert engine.is_file()
    assert 'FreeCAD' in engine.name or 'freecad' in engine.name.lower()


def test_tessellate_box_step(tmp_path):
    engine = _freecad_or_skip()
    step = tmp_path / 'box.step'
    stl = tmp_path / 'box.stl'
    write_box_step(step, (10.0, 20.0, 30.0))
    assert step.is_file() and step.stat().st_size > 0

    report = tessellate_step(step, _settings(), stl)
    assert report['engine'] == str(engine.resolve())
    assert report['linear_deflection_mm'] == pytest.approx(0.1)
    assert report['angular_deflection_deg'] == pytest.approx(15.0)
    assert report['triangle_count'] == 12
    assert report['bounds'][0] == pytest.approx([0.0, 0.0, 0.0])
    assert report['bounds'][1] == pytest.approx([10.0, 20.0, 30.0])
    assert report['freecad_version'] is None or isinstance(report['freecad_version'], list)
    assert stl.is_file()

    with open_stl(stl) as mesh:
        inventory = inspect_mesh(mesh)
    assert inventory['triangle_count'] == 12
    assert inventory['boundary_edges'] == 0


def test_tessellate_respects_linear_deflection_override(tmp_path):
    _freecad_or_skip()
    step = tmp_path / 'box.step'
    stl = tmp_path / 'box.stl'
    write_box_step(step, (10.0, 20.0, 30.0))
    report = tessellate_step(step, _settings(step_linear_deflection_mm=0.05), stl)
    assert report['linear_deflection_mm'] == pytest.approx(0.05)
    assert report['triangle_count'] >= 12


def test_tessellate_rejects_missing_and_wrong_suffix(tmp_path):
    _freecad_or_skip()
    settings = _settings()
    with pytest.raises(VoxelMillError, match='not found') as missing:
        tessellate_step(tmp_path / 'absent.step', settings, tmp_path / 'out.stl')
    assert missing.value.code == 'step_import'
    stl = tmp_path / 'cube.stl'
    stl.write_bytes(b'not-a-step')
    with pytest.raises(VoxelMillError, match='expects a .step') as bad:
        tessellate_step(stl, settings, tmp_path / 'out.stl')
    assert bad.value.code == 'step_import'


def test_cli_import_step(tmp_path):
    _freecad_or_skip()
    from voxelmill.cli import main

    step = tmp_path / 'box.stp'
    stl = tmp_path / 'out.stl'
    report_path = tmp_path / 'report.json'
    write_box_step(step, (5.0, 5.0, 5.0))
    code = main(['import-step', str(step), '--output', str(stl), '--report', str(report_path)])
    assert code == 0
    payload = json.loads(report_path.read_text())
    assert payload['command'] == 'import-step'
    assert payload['triangle_count'] == 12
    assert payload['weld_tolerance_mm'] == 0.0
    assert Path(payload['output']) == stl.resolve()
    assert stl.is_file()


def test_apply_step_weld_merges_near_duplicates(tmp_path):
    a = [0.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    c = [0.0, 1.0, 0.0]
    b2 = [1.0 + 1e-6, 0.0, 0.0]
    d = [1.0, 1.0, 0.0]
    tris = np.array([[a, b, c], [b2, d, c]], dtype=np.float64)
    stl = tmp_path / 'near.stl'
    write_stl(stl, tris.astype(np.float32))
    with open_stl(stl) as mesh:
        before = inspect_mesh(mesh, self_intersections=False)
    assert before['unique_vertices'] == 5
    assert before['boundary_edges'] > 0

    report = apply_step_weld(stl, 1e-5)
    assert report['rewrote'] is True
    assert report['unique_vertices'] == 4
    with open_stl(stl) as mesh:
        after = inspect_mesh(mesh, self_intersections=False)
    assert after['unique_vertices'] == 4


def test_tessellate_applies_weld_tolerance_setting(tmp_path, monkeypatch):
    step = tmp_path / 'box.step'
    step.write_text('ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n')
    stl = tmp_path / 'box.stl'
    a = [0.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    c = [0.0, 1.0, 0.0]
    b2 = [1.0 + 1e-6, 0.0, 0.0]
    d = [1.0, 1.0, 0.0]
    tris = np.array([[a, b, c], [b2, d, c]], dtype=np.float64)

    def fake_helper(argv, *, cancel=None, timeout_s=600.0):
        write_stl(Path(argv[1]), tris.astype(np.float32))
        return {
            'mode': 'tessellate',
            'engine': '/fake/FreeCAD.AppImage',
            'freecad_version': None,
            'linear_deflection_mm': float(argv[2]),
            'angular_deflection_deg': float(argv[3]),
            'triangle_count': 2,
            'bounds': [[0.0, 0.0, 0.0], [1.0, 1.0, 0.0]],
        }

    monkeypatch.setattr('voxelmill.importers.run_freecad_helper', fake_helper)
    report = tessellate_step(step, _settings(weld_tolerance_mm=1e-5), stl)
    assert report['weld_tolerance_mm'] == pytest.approx(1e-5)
    assert report['triangle_count'] == 2
    with open_stl(stl) as mesh:
        inventory = inspect_mesh(mesh, self_intersections=False)
    assert inventory['unique_vertices'] == 4
