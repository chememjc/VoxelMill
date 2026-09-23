"""H2: optional post-slice hooks record success and failure without deleting output."""
from __future__ import annotations


from voxelmill.hooks import run_post_slice_hook


def test_hook_none_is_noop():
    settings = {'resources': {'post_slice_hook': None}}
    assert run_post_slice_hook(settings, '/tmp/out.goo', '/tmp/out.json') is None


def test_true_hook_runs(tmp_path):
    marker = tmp_path / 'hooked'
    settings = {'resources': {'post_slice_hook': f'/bin/touch {marker}'}}
    output = tmp_path / 'part.goo'
    report = tmp_path / 'part.json'
    output.write_text('goo')
    report.write_text('{}')
    result = run_post_slice_hook(settings, output, report)
    assert result is not None
    assert result['ok'] is True
    assert result['returncode'] == 0
    assert marker.is_file()
    assert output.is_file()


def test_missing_command_is_recorded(tmp_path):
    missing = tmp_path / 'no-such-hook-binary'
    settings = {'resources': {'post_slice_hook': str(missing)}}
    output = tmp_path / 'kept.goo'
    output.write_text('published')
    result = run_post_slice_hook(settings, output, tmp_path / 'report.json')
    assert result is not None
    assert result['ok'] is False
    assert result['error'] == 'command_not_found'
    assert output.read_text() == 'published'
