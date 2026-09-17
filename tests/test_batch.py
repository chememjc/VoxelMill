"""Running one operation over many inputs, keeping every item's evidence."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from voxelmill.cli import BATCH_OUTPUTS, main

from test_goo import cube_triangles
from voxelmill.mesh import write_stl

ROOT = Path(__file__).resolve().parents[1]
SMALL = ['--printer', str(ROOT / 'profiles/mars5-ultra.ptr'),
         '--set', 'printer.build_mm=[3.2, 2.4, 10.0]', '--set', 'printer.pixels=[32, 24]',
         '--set', 'printer.pixel_pitch_mm=[0.1, 0.1]', '--set', 'printer.edge_clearance_mm=0',
         '--set', 'process.layer_height_mm=0.1']


@pytest.fixture
def inputs(tmp_path):
    clean = tmp_path / 'clean.stl'
    write_stl(clean, cube_triangles())
    lower = cube_triangles()
    upper = lower.copy()
    upper[..., 2] += float(lower[..., 2].max()) + 0.4
    islanded = tmp_path / 'islanded.stl'
    write_stl(islanded, np.concatenate([lower, upper]))
    return clean, islanded


def manifest(directory):
    return json.loads((Path(directory) / 'batch-manifest.json').read_text())


def test_a_clean_batch_writes_one_report_per_input_and_exits_zero(tmp_path, inputs, capsys):
    clean, _ = inputs
    out = tmp_path / 'out'
    assert main(['batch', 'islands', str(clean), str(clean), '--output-dir', str(out),
                 *SMALL]) == 0
    capsys.readouterr()
    record = manifest(out)
    assert record['operation'] == 'islands' and record['failed'] == 0
    assert record['ran'] == record['requested'] == 2 and not record['stopped_early']
    # Both items share a stem here, so the second report overwrites the first;
    # what matters is that each item is named and recorded separately.
    assert [item['exit_code'] for item in record['items']] == [0, 0]
    assert (out / 'clean.islands.json').is_file()
    assert json.loads((out / 'clean.islands.json').read_text())['island_count'] == 0


def test_a_failing_item_stops_the_batch_unless_told_to_continue(tmp_path, inputs, capsys):
    clean, islanded = inputs
    stopped = tmp_path / 'stopped'
    assert main(['batch', 'islands', str(islanded), str(clean),
                 '--output-dir', str(stopped), *SMALL]) == 2
    capsys.readouterr()
    record = manifest(stopped)
    assert record['ran'] == 1 and record['requested'] == 2
    assert record['stopped_early'] and record['failed'] == 1
    assert not (stopped / 'clean.islands.json').exists()

    kept = tmp_path / 'kept'
    assert main(['batch', 'islands', str(islanded), str(clean), '--output-dir', str(kept),
                 '--continue-on-error', *SMALL]) == 2
    capsys.readouterr()
    record = manifest(kept)
    assert record['ran'] == 2 and record['failed'] == 1 and not record['stopped_early']
    assert [item['exit_code'] for item in record['items']] == [2, 0]
    # The one that passed still produced its own evidence.
    assert (kept / 'clean.islands.json').is_file()


def test_settings_flags_and_pass_through_arguments_reach_every_item(tmp_path, inputs, capsys):
    clean, _ = inputs
    out = tmp_path / 'out'
    assert main(['batch', 'islands', str(clean), '--output-dir', str(out), *SMALL,
                 '--extra', '--max-examples', '3']) == 0
    capsys.readouterr()
    argv = manifest(out)['items'][0]['argv']
    assert argv[:2] == ['islands', str(clean)]
    assert '--max-examples' in argv and '3' in argv
    # The batch's own settings are re-emitted for the item, so an item cannot
    # resolve different settings from the batch that launched it.
    assert argv.count('--set') == SMALL.count('--set')
    assert 'process.layer_height_mm=0.1' in argv


def test_every_settings_flag_reaches_the_item_including_the_boolean_ones(tmp_path, inputs,
                                                                        capsys):
    """A hand-listed forwarding table drops whatever nobody remembered.

    The overrides dictionary is derived from every settings flag at once, so a
    toggle cannot be accepted by the batch and then silently dropped.
    """
    from voxelmill.cli import _settings, build_parser
    clean, _ = inputs
    out = tmp_path / 'out'
    extras = ['--no-auto-supports', '--no-auto-bracing', '--seal-voids',
              '--min-orifice-area-mm2', '2.5', '--max-deviation-mm', '0.04',
              '--clip-to-build-volume', '--repair', 'none', '--workers', '2',
              '--elephant-foot-mm', '0.02', '--elephant-foot-layers', '3']
    assert main(['batch', 'islands', str(clean), '--output-dir', str(out),
                 *SMALL, *extras]) == 0
    capsys.readouterr()
    argv = manifest(out)['items'][0]['argv']
    item = build_parser().parse_args(argv)
    batch = build_parser().parse_args(['islands', str(clean), *SMALL, *extras])
    # The item resolves exactly the settings the batch itself would have.
    assert _settings(item) == _settings(batch)
    resolved = _settings(item)
    assert resolved['support']['automatic'] is False
    assert resolved['support']['auto_bracing'] is False
    assert resolved['repair']['aggressiveness'] == 'none'
    assert resolved['repair']['min_orifice_area_mm2'] == 2.5
    assert resolved['assembly']['clip_to_build_volume'] is True
    assert resolved['resources']['workers'] == 2
    assert resolved['process']['elephant_foot_layers'] == 3


def test_the_report_flag_names_where_the_manifest_goes(tmp_path, inputs, capsys):
    clean, _ = inputs
    out = tmp_path / 'out'
    destination = tmp_path / 'elsewhere' / 'run.json'
    assert main(['batch', 'islands', str(clean), '--output-dir', str(out),
                 '--report', str(destination), *SMALL]) == 0
    capsys.readouterr()
    record = json.loads(destination.read_text())
    assert record['command'] == 'batch' and record['manifest_path'] == str(destination)
    # The default location is not also written when another one was named.
    assert not (out / 'batch-manifest.json').exists()


def test_the_batch_names_every_output_and_refuses_a_conflicting_one(tmp_path, inputs, capsys):
    clean, _ = inputs
    out = tmp_path / 'out'
    assert main(['batch', 'islands', str(clean), '--output-dir', str(out), *SMALL,
                 '--extra', '--report', 'mine.json']) != 0
    error = json.loads(capsys.readouterr().err)['error']
    assert error['code'] == 'invalid_option' and '--report' in error['message']


def test_prepare_and_slice_get_named_geometry_outputs(tmp_path, inputs, capsys):
    clean, _ = inputs
    out = tmp_path / 'out'
    main(['batch', 'prepare', str(clean), '--output-dir', str(out), *SMALL,
          '--extra', '--max-passes', '1'])
    capsys.readouterr()
    argv = manifest(out)['items'][0]['argv']
    assert str(out / 'clean-supported.stl') in argv
    assert BATCH_OUTPUTS['prepare'] == '{stem}-supported.stl'
    assert BATCH_OUTPUTS['validate'] is None


def test_the_manifest_states_its_own_isolation_limit(tmp_path, inputs, capsys):
    clean, _ = inputs
    out = tmp_path / 'out'
    main(['batch', 'measure', str(clean), '--output-dir', str(out), *SMALL])
    capsys.readouterr()
    record = manifest(out)
    # Items share a process, so a segfault ends the batch. Saying so beats
    # implying an isolation the code does not provide.
    assert 'share one process' in record['isolation']


def test_batch_is_reachable_from_the_editor_operation_form():
    """Run operation is generated from the parser, so batch is offered there."""
    pytest.importorskip('PySide6')
    from voxelmill.gui.operations import operation_parsers
    assert 'batch' in operation_parsers()


def test_integer_settings_flags_survive_re_parsing_by_each_item(tmp_path, inputs, capsys):
    """An integer flag must reach the item still typed as an integer.

    An earlier forwarding table emitted ``repr(float(2))``, which ``type=int``
    rejects, failing the whole run on a flag the batch itself accepted.
    """
    clean, _ = inputs
    out = tmp_path / 'out'
    assert main(['batch', 'islands', str(clean), '--output-dir', str(out), *SMALL,
                 '--workers', '2', '--elephant-foot-layers', '3',
                 '--memory-gib', '8']) == 0
    capsys.readouterr()
    argv = manifest(out)['items'][0]['argv']
    from voxelmill.cli import _settings, build_parser
    resolved = _settings(build_parser().parse_args(argv))
    assert resolved['resources']['workers'] == 2
    assert resolved['process']['elephant_foot_layers'] == 3
    assert resolved['resources']['memory_gib'] == 8.0
