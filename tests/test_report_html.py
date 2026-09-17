"""I4: JSON reports render as readable static HTML."""
from __future__ import annotations

import json
from pathlib import Path

from voxelmill.cli import main
from voxelmill.report_html import render_report


def _synthetic_report():
    return {
        'schema_version': 1,
        'command': 'validate',
        'passed': False,
        'settings': {'schema_version': 1},
        'diagnostics': [],
        'validation': {
            'schema_version': 1,
            'passed': False,
            'checks': {'closed_surface': 'fail'},
            'metrics': {'layers': 4, 'open_rows': 2},
            'diagnostics': [
                {'code': 'raster_island', 'message': 'unsupported island', 'severity': 'error',
                 'layer': 2},
                {'code': 'goo_orientation_unverified', 'message': 'mirror unknown',
                 'severity': 'warning', 'layer': None},
            ],
        },
    }


def test_render_report_includes_diagnostic_code_and_verdict():
    html = render_report(_synthetic_report())
    assert '<!DOCTYPE html>' in html
    assert 'FAIL' in html
    assert 'raster_island' in html
    assert 'goo_orientation_unverified' in html
    assert 'layers' in html
    assert '4' in html
    assert '<script' not in html.lower()


def test_report_html_cli_writes_output(tmp_path, capsys):
    report_path = tmp_path / 'report.json'
    html_path = tmp_path / 'out.html'
    report_path.write_text(json.dumps(_synthetic_report()) + '\n')
    assert main(['report-html', str(report_path), '--output', str(html_path)]) == 0
    text = html_path.read_text()
    assert 'raster_island' in text
    assert Path(html_path).is_file()
