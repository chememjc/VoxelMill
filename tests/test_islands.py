"""Island-only scanning, shared by ``voxelmill islands`` and the editor badge."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from voxelmill.cli import main
from voxelmill.config import resolve_settings
from voxelmill.mesh import write_stl
from voxelmill.pipeline import scan_islands, validate_stl
from voxelmill.validation import island_summary

from test_goo import cube_triangles, small_settings

ROOT = Path(__file__).resolve().parents[1]


def floating_pair(gap_mm=0.4):
    """A cube on the plate plus a second one floating above it with a gap."""
    lower = cube_triangles()
    upper = lower.copy()
    upper[..., 2] += float(lower[..., 2].max()) + gap_mm
    return np.concatenate([lower, upper])


def test_a_clean_part_reports_no_islands_and_names_what_it_did_not_check(tmp_path):
    source = tmp_path / 'cube.stl'
    write_stl(source, cube_triangles())
    summary = scan_islands(source, small_settings())
    assert summary['check'] == 'pass' and summary['island_count'] == 0
    assert summary['closed_surface'] == 'pass'
    assert summary['min_overlap_pixels'] == 1 and summary['layer_height_mm'] == 0.1
    # Void tracking is off, so it says not_run rather than pass.
    assert summary['other_checks']['enclosed_voids'] == 'not_run'
    assert summary['other_checks']['transient_traps'] == 'not_run'
    # Overlap and growth come free with the same labeling pass.
    assert summary['other_checks']['overlap'] == 'pass'
    assert 'drainage_bottlenecks' in summary['not_examined']
    assert 'support_routes' in summary['not_examined']


def test_floating_material_is_found_with_a_layer_and_a_position(tmp_path):
    source = tmp_path / 'pair.stl'
    write_stl(source, floating_pair())
    summary = scan_islands(source, small_settings())
    assert summary['check'] == 'fail' and summary['island_count'] >= 1
    first = summary['islands'][0]
    assert first['layer'] is not None and first['position_mm'] is not None
    assert not summary['truncated']


def test_the_scan_agrees_with_the_full_validation_on_islands(tmp_path):
    """The badge and the authoritative check must not disagree about islands.

    They run the same ``analyze_layers`` pass; only void tracking and drainage
    differ, and neither of those touches connectivity.
    """
    source = tmp_path / 'pair.stl'
    write_stl(source, floating_pair())
    settings = small_settings()
    fast = scan_islands(source, settings)
    full = validate_stl(source, settings, drainage=False)
    reference = island_summary(full, settings)
    assert fast['check'] == reference['check']
    assert fast['island_count'] == reference['island_count']
    assert [i['layer'] for i in fast['islands']] == [i['layer'] for i in reference['islands']]


def test_the_position_list_is_capped_but_the_count_never_is(tmp_path):
    source = tmp_path / 'pair.stl'
    write_stl(source, floating_pair())
    settings = small_settings()
    full = scan_islands(source, settings)
    capped = scan_islands(source, settings, limit=0)
    assert capped['island_count'] == full['island_count'] >= 1
    assert capped['islands'] == [] and capped['truncated'] is True


def test_the_cli_exits_two_on_islands_and_zero_on_a_clean_part(tmp_path, capsys):
    clean = tmp_path / 'cube.stl'
    write_stl(clean, cube_triangles())
    broken = tmp_path / 'pair.stl'
    write_stl(broken, floating_pair())
    printer = ['--printer', str(ROOT / 'profiles/mars5-ultra.ptr'),
               '--set', 'printer.build_mm=[3.2, 2.4, 10.0]',
               '--set', 'printer.pixels=[32, 24]',
               '--set', 'printer.pixel_pitch_mm=[0.1, 0.1]',
               '--set', 'printer.edge_clearance_mm=0',
               '--set', 'process.layer_height_mm=0.1']
    assert main(['islands', str(clean), *printer]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['command'] == 'islands' and payload['island_count'] == 0

    assert main(['islands', str(broken), *printer]) == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload['island_count'] >= 1
    # The warning names the checks this scan did not make, so a green exit is
    # never mistaken for a passing validation.
    assert 'does not examine' in captured.err


def test_pin_array_fixture_births_nine_islands(tmp_path):
    """Plate plus nine mid-air pins: each pin is its own island birth."""
    from voxelmill.geometry import triangle_bounds
    from voxelmill.mesh import open_stl
    from voxelmill.raster import MeshLayerStream
    from voxelmill.validation import analyze_layers
    from voxelmill.contracts import ResourceBudget

    with open_stl(ROOT / 'fixtures/shapes/pin_array.stl') as mesh:
        triangles = np.asarray(mesh.triangles, dtype=np.float32).copy()
    # Fixture sits in [0, 30]^2; printer grids are centered on the origin.
    bounds = triangle_bounds(triangles)
    triangles[..., 0] -= 0.5 * (bounds[0][0] + bounds[1][0])
    triangles[..., 1] -= 0.5 * (bounds[0][1] + bounds[1][1])
    centered = tmp_path / 'pin_array_centered.stl'
    write_stl(centered, triangles)

    settings = resolve_settings(
        overrides={'process': {'layer_height_mm': 0.2},
                   'printer': {'pixels': [200, 200], 'pixel_pitch_mm': [0.2, 0.2],
                               'build_mm': [40., 40., 165.], 'edge_clearance_mm': 0},
                   'resources': {'workers': 1}})
    summary = scan_islands(centered, settings)
    assert summary['check'] == 'fail'
    assert summary['island_count'] == 9

    # Same evidence through analyze_layers on a birth-window stream.
    stream = MeshLayerStream(triangles, triangle_bounds(triangles), settings,
                             layer_range=(49, 51),
                             budget=ResourceBudget(workers=1, memory_gib=1))
    report = analyze_layers(stream, stream.grid, settings, track_voids=False,
                            budget=ResourceBudget(workers=1, memory_gib=1))
    assert report.metrics['island_components'] == 9
