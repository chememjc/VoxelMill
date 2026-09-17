import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError, SupportGraph, ValidationReport
from voxelmill.project import SCHEMA_VERSION, load_project, save_project


def test_round_trip_preserves_original_and_failed_validation(tmp_path):
    source = tmp_path / 'original.stl'
    original = b'opaque fixture data' * 100000
    source.write_bytes(original)
    report = ValidationReport(checks={'islands': 'fail'})
    project = tmp_path / 'part.voxmil'
    state = {'settings': resolve_settings(), 'support_graph': SupportGraph(), 'validation': report.to_dict(), 'edits': [{'operation': 'rotate', 'degrees': [0, 45, 90]}]}
    saved = save_project(project, state, source)
    result = load_project(project, tmp_path / 'extract')
    assert source.read_bytes() == original
    assert Path(result['source']['extracted_path']).read_bytes() == original
    assert result['source']['sha256'] == hashlib.sha256(original).hexdigest()
    assert result['validation']['passed'] is False
    assert result['settings'] == state['settings']
    assert result['edits'] == state['edits']
    assert load_project(project) == json.loads(json.dumps(saved, default=lambda x: x.__dict__))
    assert 'source' not in state


def test_manifest_only(tmp_path):
    path = tmp_path / 'empty.voxmil'
    save_project(path, {'edits': []})
    assert load_project(path) == {'schema_version': SCHEMA_VERSION, 'edits': []}


def test_hash_failure_does_not_replace_existing_project(tmp_path):
    source = tmp_path / 'source.stl'
    source.write_bytes(b'unchanged')
    path = tmp_path / 'part.voxmil'
    save_project(path, {'edits': []})
    original_project = path.read_bytes()
    with pytest.raises(VoxelMillError, match='SHA-256'):
        save_project(path, {'source': {'sha256': '0' * 64}}, source)
    assert path.read_bytes() == original_project
    assert not list(tmp_path.glob('.*.tmp'))


@pytest.mark.parametrize('manifest', [{'schema_version': True}, {'schema_version': 1}, {'schema_version': 3}, {'x': float('nan')}, {'x': float('inf')}])
def test_invalid_state_cannot_serialize(tmp_path, manifest):
    with pytest.raises(VoxelMillError):
        save_project(tmp_path / 'bad.voxmil', manifest)
    assert not (tmp_path / 'bad.voxmil').exists()


@pytest.mark.parametrize('name', ['../escape.stl', '/absolute.stl', 'source\\original.stl', 'unrecognized'])
def test_archive_member_paths_rejected(tmp_path, name):
    path = tmp_path / 'bad.voxmil'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('manifest.json', '{"schema_version":2}')
        archive.writestr(name, 'data')
    with pytest.raises(VoxelMillError):
        load_project(path, tmp_path / 'extract')
    assert not (tmp_path / 'extract').exists()


@pytest.mark.parametrize('body', ['{"schema_version":2,"x":NaN}', '{"schema_version":2,"x":1e999}', '{"schema_version":2,"schema_version":2}'])
def test_nonfinite_or_duplicate_json_rejected(tmp_path, body):
    path = tmp_path / 'bad.voxmil'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('manifest.json', body)
    with pytest.raises(VoxelMillError):
        load_project(path)


def test_corrupt_source_hash_rejected_before_extract(tmp_path):
    path = tmp_path / 'bad.voxmil'
    manifest = {'schema_version': SCHEMA_VERSION, 'source': {'archive_member': 'source/original.stl', 'sha256': '0' * 64, 'size_bytes': 3}}
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        archive.writestr('source/original.stl', b'bad')
    with pytest.raises(VoxelMillError, match='hash'):
        load_project(path, tmp_path / 'extract')
    assert not list((tmp_path / 'extract').iterdir())


def test_zip_bomb_and_truncation_rejected(tmp_path):
    path = tmp_path / 'bad.voxmil'
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', ' ' * 1000000)
    with pytest.raises(VoxelMillError, match='compression-ratio'):
        load_project(path)
    path.write_bytes(path.read_bytes()[:100])
    with pytest.raises(VoxelMillError):
        load_project(path)


def test_symlink_member_rejected(tmp_path):
    path = tmp_path / 'bad.voxmil'
    member = zipfile.ZipInfo('manifest.json')
    member.create_system = 3
    member.external_attr = 0o120777 << 16
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(member, '{"schema_version":2}')
    with pytest.raises(VoxelMillError, match='regular file'):
        load_project(path)


def test_symlink_extraction_directory_rejected(tmp_path):
    source = tmp_path / 'source.stl'
    source.write_bytes(b'source')
    project = tmp_path / 'part.voxmil'
    save_project(project, {}, source)
    target = tmp_path / 'target'
    target.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(VoxelMillError, match='symlinks'):
        load_project(project, link)


def test_added_models_are_embedded_and_extracted_portably(tmp_path):
    primary = tmp_path / 'primary.stl'
    extra = tmp_path / 'extra.stl'
    primary.write_bytes(b'primary mesh')
    extra.write_bytes(b'extra mesh')
    state = {'edits': {'extra_models': [{'path': str(extra), 'rotate': [1, 2, 3]}]}}
    project = tmp_path / 'portable.voxmil'
    saved = save_project(project, state, primary)
    extra.unlink()
    loaded = load_project(project, tmp_path / 'extract')
    row = loaded['edits']['extra_models'][0]
    assert Path(row['path']).read_bytes() == b'extra mesh'
    assert row['rotate'] == [1, 2, 3]
    assert saved['edits']['extra_models'][0]['archive_member'] == 'source/models/0.stl'
    with zipfile.ZipFile(project) as archive:
        assert 'source/models/0.stl' in archive.namelist()
