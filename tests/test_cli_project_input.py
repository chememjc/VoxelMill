"""CLI preparation from GUI project archives without requiring Qt."""
import json
import os
from pathlib import Path
import subprocess
import sys

import manifold3d as m
import pytest

from voxelmill.cli import main
from voxelmill.config import resolve_settings
from voxelmill.geometry import manifold_triangles
from voxelmill.mesh import write_stl
from voxelmill.project import SCHEMA_VERSION, save_project


def small_settings():
    return resolve_settings(overrides={
        'process': {'layer_height_mm': 0.2, 'normal_exposure_s': 6.5},
        'printer': {
            'pixels': [1000, 800],
            'pixel_pitch_mm': [0.1, 0.1],
            'build_mm': [100.0, 80.0, 165.0],
        },
    })


@pytest.fixture
def source(tmp_path):
    path = tmp_path / 'part.stl'
    write_stl(path, manifold_triangles(m.Manifold.sphere(4, 32)))
    return path


def make_project(path, source, settings=None):
    settings = settings or small_settings()
    save_project(path, {
        'schema_version': SCHEMA_VERSION,
        'settings': settings,
        'rotation_deg': [17.0, -8.0, 23.0],
        'center_offset_mm': [1.25, -2.5],
        'model_lift_mm': 0.0,
        'edits': {
            'manual_contacts': [[1.25, -2.5, 0.0]],
            'removed_contacts': [[0.0, 0.0, 3.0]],
        },
    }, source)


def read_report(path):
    return json.loads(path.read_text())


def test_prepare_project_uses_stored_pose_settings_and_edits(tmp_path, source):
    project = tmp_path / 'gui.voxmil'
    report_path = tmp_path / 'report.json'
    settings = small_settings()
    make_project(project, source, settings)

    code = main(['prepare', str(project), '--report', str(report_path), '--no-drainage'])

    assert code == 2  # drainage was explicitly skipped, so validation is incomplete
    report = read_report(report_path)
    assert report['settings'] == settings
    assert report['placement']['rotation_deg'] == [17.0, -8.0, 23.0]
    assert report['placement']['center_offset_mm'] == [1.25, -2.5]
    assert report['placement']['model_lift_mm'] == 0.0
    support = report['passes'][-1]['supports']
    assert support['manual_contacts'] == 1
    assert support['suppressed_contacts'] == 1


def test_explicit_pose_contacts_and_settings_override_project_state(tmp_path, source):
    project = tmp_path / 'gui.voxmil'
    report_path = tmp_path / 'override.json'
    empty = tmp_path / 'empty.json'
    empty.write_text('[]')
    make_project(project, source)

    code = main([
        'prepare', str(project), '--report', str(report_path), '--no-drainage',
        '--rotate', '0', '0', '0', '--center-offset', '0', '0', '--model-lift-mm', '0',
        '--contacts', str(empty), '--removed-contacts', str(empty),
        '--support-spacing-mm', '2.25', '--set', 'process.normal_exposure_s=4.25',
    ])

    assert code == 2
    report = read_report(report_path)
    assert report['settings']['support']['spacing_mm'] == 2.25
    assert report['settings']['process']['normal_exposure_s'] == 4.25
    assert report['placement']['rotation_deg'] == [0.0, 0.0, 0.0]
    assert report['placement']['center_offset_mm'] == [0.0, 0.0]
    assert report['placement']['model_lift_mm'] == 0.0
    support = report['passes'][-1]['supports']
    assert support['manual_contacts'] == 0
    assert support['suppressed_contacts'] == 0


def test_explicit_profiles_replace_project_settings(tmp_path, source):
    project = tmp_path / 'gui.voxmil'
    report_path = tmp_path / 'profile.json'
    printer = tmp_path / 'override.ptr'
    make_project(project, source)
    printer.write_text('schema_version=1\n[printer]\nid="mars5-ultra"\n[process]\nnormal_exposure_s=8.0\n')

    code = main(['prepare', str(project), '--printer', str(printer), '--report', str(report_path),
                 '--no-drainage'])

    assert code == 2
    report = read_report(report_path)
    assert report['settings']['process']['normal_exposure_s'] == 8.0


def test_ordinary_stl_keeps_cli_pose_defaults(tmp_path, source):
    report_path = tmp_path / 'stl.json'
    code = main(['prepare', str(source), '--report', str(report_path), '--no-drainage'])

    assert code == 2
    placement = read_report(report_path)['placement']
    assert placement['rotation_deg'] == [0.0, 0.0, 0.0]
    assert placement['center_offset_mm'] == [0.0, 0.0]
    assert placement['model_lift_mm'] == 5.0


def test_project_input_cannot_be_replaced_by_output(tmp_path, source, capsys):
    project = tmp_path / 'gui.voxmil'
    make_project(project, source)

    assert main(['prepare', str(project), '--output', str(project)]) == 3
    assert json.loads(capsys.readouterr().err)['error']['code'] == 'source_overwrite'


def test_cli_import_does_not_import_qt():
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
    result = subprocess.run(
        [sys.executable, '-c', 'import sys; import voxelmill.cli; print("PySide6" in sys.modules)'],
        env=env, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == 'False'


def test_prepare_restores_added_parts_from_a_project(tmp_path, monkeypatch):
    """Every other edit fell back to the project; added parts did not.

    A multi-part plate saved from the editor and prepared from the command line
    silently produced a single-part result.
    """
    import manifold3d as m
    from voxelmill.geometry import manifold_triangles
    from voxelmill.gui.document import Document

    primary = tmp_path / 'primary.stl'
    added = tmp_path / 'added.stl'
    write_stl(primary, manifold_triangles(m.Manifold.cube((8, 8, 8), True).translate((0, 0, 5))))
    write_stl(added, manifold_triangles(m.Manifold.cube((6, 6, 6), True).translate((0, 0, 4))))

    document = Document(small_settings(), primary)
    document.add_extra_model({'path': str(added), 'center_offset': (25.0, 0.0)})
    project = tmp_path / 'plate.voxmil'
    document.save(project)

    seen = {}
    from voxelmill import pipeline as pipeline_module
    real = pipeline_module.prepare

    def spy(source, settings, **kwargs):
        extras = list(kwargs.get('extra_models') or ())
        seen['extra_models'] = extras
        # The extraction directory only lives for the duration of the run, so
        # the embedded mesh is checked here rather than after main() returns.
        seen['exists'] = [Path(spec['path']).exists() for spec in extras]
        return real(source, settings, **kwargs)

    monkeypatch.setattr(pipeline_module, 'prepare', spy)
    main(['prepare', str(project), '--output', str(tmp_path / 'out.stl'),
          '--report', str(tmp_path / 'r.json')])
    assert len(seen['extra_models']) == 1
    assert seen['extra_models'][0]['center_offset'] == [25.0, 0.0]
    # The archive member is what makes this work after the original moves.
    assert Path(seen['extra_models'][0]['path']) != added
    assert seen['exists'] == [True]


def test_an_explicit_add_model_replaces_the_project_parts(tmp_path, monkeypatch):
    import manifold3d as m
    from voxelmill.geometry import manifold_triangles
    from voxelmill.gui.document import Document

    primary = tmp_path / 'primary.stl'
    added = tmp_path / 'added.stl'
    other = tmp_path / 'other.stl'
    for path, size in ((primary, 8), (added, 6), (other, 5)):
        write_stl(path, manifold_triangles(
            m.Manifold.cube((size, size, size), True).translate((0, 0, size))))

    document = Document(small_settings(), primary)
    document.add_extra_model({'path': str(added), 'center_offset': (25.0, 0.0)})
    project = tmp_path / 'plate.voxmil'
    document.save(project)

    seen = {}
    from voxelmill import pipeline as pipeline_module
    real = pipeline_module.prepare

    def spy(source, settings, **kwargs):
        seen['extra_models'] = list(kwargs.get('extra_models') or ())
        return real(source, settings, **kwargs)

    monkeypatch.setattr(pipeline_module, 'prepare', spy)
    main(['prepare', str(project), '--output', str(tmp_path / 'out.stl'),
          '--add-model', str(other), '--report', str(tmp_path / 'r.json')])
    assert [Path(spec['path']).name for spec in seen['extra_models']] == ['other.stl']
