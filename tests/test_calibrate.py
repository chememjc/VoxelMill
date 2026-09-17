"""Offline exposure / tolerance calibration GOO generator."""
from __future__ import annotations

from pathlib import Path

import pytest

from voxelmill.calibrate import parse_range, step_values, write_calibration
from voxelmill.cli import build_parser, main
from voxelmill.config import resolve_settings
from voxelmill.goo import GooReader


ROOT = Path(__file__).resolve().parents[1]


def tiny_settings():
    return resolve_settings(
        ROOT / 'profiles/mars5-ultra.ptr', ROOT / 'profiles/sunlu-abs-like-gray.res',
        {'printer': {'build_mm': [3.2, 2.4, 10.0], 'pixels': [32, 24],
                     'pixel_pitch_mm': [0.1, 0.1], 'edge_clearance_mm': 0.0},
         'process': {'layer_height_mm': 0.1, 'bottom_layers': 1, 'transition_layers': 0,
                     'bottom_exposure_s': 8.0, 'normal_exposure_s': 2.0}})


def test_step_values_and_range_parsing():
    assert parse_range('2.0:5.0') == (2.0, 5.0)
    assert step_values(2.0, 5.0, 3) == pytest.approx([2.0, 3.5, 5.0])


def test_exposure_calibration_matrix_opens_and_matches(tmp_path):
    settings = tiny_settings()
    output = tmp_path / 'expo.goo'
    report = write_calibration(
        'exposure', settings, output,
        range_mm=(2.0, 5.0), steps=3, layers=2,
    )
    assert output.is_file()
    assert len(report['cells']) == 3
    assert [cell['exposure_s'] for cell in report['cells']] == pytest.approx([2.0, 3.5, 5.0])
    assert all('center_mm' in cell and 'row' in cell and 'col' in cell
               for cell in report['cells'])
    with GooReader(output) as reader:
        assert reader.header['layer_count'] == 2
        assert len(reader.layers) == 2
        for layer in reader.layers:
            assert layer.values['exposure_time'] == pytest.approx(5.0)
            assert reader.decode(layer.index).max() == 255


def test_tolerance_calibration_matrix(tmp_path):
    settings = tiny_settings()
    output = tmp_path / 'tol.goo'
    report = write_calibration(
        'tolerance', settings, output,
        range_mm=(-0.05, 0.05), steps=3, layers=2,
    )
    assert len(report['cells']) == 3
    assert [cell['tolerance_offset_mm'] for cell in report['cells']] == pytest.approx(
        [-0.05, 0.0, 0.05])
    with GooReader(output) as reader:
        assert len(reader.layers) == 2
        assert reader.decode(0).max() == 255


def test_calibrate_cli_help_and_exposure(tmp_path):
    parser = build_parser()
    assert 'calibrate' in parser.parse_args(['calibrate', 'exposure',
                                             '--range', '2:4', '--steps', '2',
                                             '--output', str(tmp_path / 'x.goo')]).command
    calibrate = parser._subparsers._group_actions[0].choices['calibrate']
    help_text = calibrate.format_help()
    assert 'exposure' in help_text and 'tolerance' in help_text

    output = tmp_path / 'cli-expo.goo'
    report = tmp_path / 'cli-expo.json'
    code = main([
        'calibrate', 'exposure',
        '--printer', str(ROOT / 'profiles/mars5-ultra.ptr'),
        '--resin', str(ROOT / 'profiles/sunlu-abs-like-gray.res'),
        '--set', 'printer.build_mm=[3.2,2.4,10.0]',
        '--set', 'printer.pixels=[32,24]',
        '--set', 'printer.pixel_pitch_mm=[0.1,0.1]',
        '--set', 'printer.edge_clearance_mm=0',
        '--set', 'process.layer_height_mm=0.1',
        '--range', '2.0:5.0', '--steps', '3', '--layers', '2',
        '--output', str(output), '--report', str(report),
    ])
    assert code == 0
    assert output.is_file() and report.is_file()
