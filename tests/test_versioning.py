"""One place decides what schema versions each file kind accepts."""
import pytest

from voxelmill import versioning
from voxelmill.contracts import VoxelMillError
from voxelmill.versioning import CURRENT_VERSIONS, upgrade


def test_current_documents_pass_through_unchanged():
    for kind, version in CURRENT_VERSIONS.items():
        data = {'schema_version': version, 'x': 1}
        assert upgrade(kind, data, code='bad') == {'schema_version': version, 'x': 1}


def test_a_newer_file_is_refused_as_newer():
    with pytest.raises(VoxelMillError) as error:
        upgrade('preset', {'schema_version': 99}, code='invalid_preset')
    assert error.value.code == 'invalid_preset' and 'newer VoxelMill' in str(error.value)


def test_a_missing_or_non_integer_version_is_refused():
    for data in ({}, {'schema_version': '1'}, {'schema_version': 1.0}, []):
        with pytest.raises(VoxelMillError):
            upgrade('profile', data, code='invalid_profile')


def test_a_deliberately_refused_version_says_why():
    with pytest.raises(VoxelMillError) as error:
        upgrade('project', {'schema_version': 1}, code='invalid_project')
    assert 'cannot be attributed to a part' in str(error.value)


def test_migrations_run_one_step_at_a_time(monkeypatch):
    monkeypatch.setitem(CURRENT_VERSIONS, 'preset', 3)
    monkeypatch.setitem(versioning.MIGRATIONS, 'preset', {
        1: lambda d: {'renamed': d.pop('old'), **d},
        2: lambda d: {**d, 'added': True},
    })
    upgraded = upgrade('preset', {'schema_version': 1, 'old': 'value'}, code='invalid_preset')
    assert upgraded == {'schema_version': 3, 'renamed': 'value', 'added': True}
