"""Authored support parameters survive export, projects and CLI preparation."""
import json

import manifold3d as m
import pytest

from voxelmill.cli import main
from voxelmill.config import resolve_settings
from voxelmill.contact_parameters import normalize_contact_parameters, parameters_for_contact
from voxelmill.contracts import VoxelMillError
from voxelmill.geometry import manifold_triangles
from voxelmill.mesh import write_stl
from voxelmill.pipeline import prepare
from voxelmill.project import load_project


def settings():
    return resolve_settings(overrides={
        'printer': {'build_mm': [40., 40., 40.], 'pixels': [200, 200],
                    'pixel_pitch_mm': [.2, .2]},
        'process': {'layer_height_mm': .1},
        'support': {'automatic': False, 'auto_bracing': False, 'base_type': 'none'},
        'resources': {'workers': 1}})


def test_parameters_are_validated_without_mutating_global_settings():
    cfg = settings()
    records = [{'position_mm': [0, 0, 5], 'parameters': {'pillar_diameter_mm': .8}}]
    normalized = normalize_contact_parameters(records, cfg)
    assert parameters_for_contact(normalized, [0, 0, 5]) == records[0]['parameters']
    assert cfg['support']['pillar_diameter_mm'] == 0.9
    records[0]['parameters']['pillar_diameter_mm'] = 2
    assert parameters_for_contact(normalized, [0, 0, 5])['pillar_diameter_mm'] == .8
    for bad in ([{'position_mm': [0, 0, 5], 'parameters': {'automatic': False}}],
                [{'position_mm': [0, 0, 5], 'parameters': {'penetration_mm': 10}}],
                list(normalized) * 2):
        with pytest.raises(VoxelMillError):
            normalize_contact_parameters(bad, cfg)


def test_prepare_and_cli_project_reopen_keep_individual_parameters(tmp_path):
    source = tmp_path / 'source.stl'
    write_stl(source, manifold_triangles(m.Manifold.cube((8, 8, 2))))
    records = [{'position_mm': [-2, 0, 5], 'parameters': {'pillar_diameter_mm': .8}},
               {'position_mm': [2, 0, 5], 'parameters': {'tip_shape': 'cylinder'}}]
    project, output = tmp_path / 'part.voxmil', tmp_path / 'part.stl'
    report = prepare(source, settings(), manual_contacts=[[-2, 0, 5], [2, 0, 5]],
                     contact_parameters=records, output=output, project=project,
                     allow_unresolved=True, max_passes=1)
    assert output.is_file()
    state = load_project(project)
    assert state['edits']['contact_parameters'] == records
    assert report['passes'][0]['supports']['contacts_routed'] == 2
    authored = [row for row in state['support_graph']['overrides'] if row['reason'] == 'effective_parameters']
    assert len(authored) == 2
    cli_report = tmp_path / 'reopened.json'
    assert main(['prepare', str(project), '--report', str(cli_report), '--allow-unresolved', '--max-passes', '1']) in (0, 2)
    reopened = json.loads(cli_report.read_text())
    assert reopened['passes'][0]['supports']['contact_parameters']['authored'] == 2
    assert reopened['passes'][0]['supports']['contacts_routed'] == 2


def test_cli_contact_parameters_file_and_tip_shape_flag(tmp_path):
    source = tmp_path / 'source.stl'
    write_stl(source, manifold_triangles(m.Manifold.cube((8, 8, 2))))
    contacts = tmp_path / 'contacts.json'
    contacts.write_text(json.dumps([[-2, 0, 5], [2, 0, 5]]))
    parameters = tmp_path / 'parameters.json'
    parameters.write_text(json.dumps([
        {'position_mm': [-2, 0, 5], 'parameters': {'pillar_diameter_mm': .8}},
        {'position_mm': [2, 0, 5], 'parameters': {'tip_shape': 'cylinder'}},
    ]))
    report = tmp_path / 'cli.json'
    output = tmp_path / 'out.stl'
    assert main([
        'prepare', str(source), '--contacts', str(contacts),
        '--contact-parameters', str(parameters), '--tip-shape', 'cone',
        '--output', str(output), '--report', str(report),
        '--allow-unresolved', '--max-passes', '1', '--no-auto-supports',
        '--base-type', 'none',
    ]) in (0, 2)
    payload = json.loads(report.read_text())
    assert payload['passes'][0]['supports']['contact_parameters'] == {
        'authored': 2, 'matched': 2, 'unmatched': 0}
    assert payload['passes'][0]['supports']['contacts_routed'] == 2
