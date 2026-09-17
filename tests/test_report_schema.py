"""H3: the published report JSON Schema accepts a real report envelope."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxelmill.contracts import Diagnostic, SCHEMA_VERSION, ValidationReport


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / 'schemas' / 'voxelmill-report.schema.json'


def _tiny_validate_report():
    validation = ValidationReport()
    validation.checks['closed_surface'] = 'pass'
    validation.metrics['layers'] = 3
    validation.diagnostics.append(Diagnostic(
        'example_warning', 'synthetic diagnostic for schema coverage', severity='warning'))
    return {
        'schema_version': SCHEMA_VERSION,
        'settings': {'schema_version': SCHEMA_VERSION, 'process': {'layer_height_mm': 0.05}},
        'diagnostics': [
            {'code': 'prepare_note', 'message': 'top-level note', 'severity': 'info'},
        ],
        'command': 'validate',
        'validation': validation.to_dict(),
        'passed': validation.passed,
    }


def test_report_schema_file_exists_and_is_draft07():
    assert SCHEMA_PATH.is_file()
    schema = json.loads(SCHEMA_PATH.read_text())
    assert 'draft-07' in schema['$schema']
    assert schema['required'] == ['schema_version']
    assert 'settings' in schema['properties']
    assert 'diagnostics' in schema['properties']
    assert schema.get('additionalProperties') is True


def test_tiny_validate_report_matches_schema():
    jsonschema = pytest.importorskip(
        'jsonschema', reason='jsonschema is not installed; schema file is still published')
    schema = json.loads(SCHEMA_PATH.read_text())
    report = _tiny_validate_report()
    jsonschema.Draft7Validator(schema).validate(report)
    assert report['schema_version'] == SCHEMA_VERSION
    assert report['diagnostics'][0]['code'] == 'prepare_note'
