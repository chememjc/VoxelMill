"""Portable process preset API and CLI tests."""
from copy import deepcopy
import json

import pytest

from voxelmill.cli import main
from voxelmill.config import DEFAULTS, resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.presets import (
    apply_process_preset,
    list_process_presets,
    load_process_preset,
    save_process_preset,
)


def test_process_builtins_are_named_uncalibrated_and_independent():
    assert list_process_presets() == ('fine', 'default', 'fast')
    fine = load_process_preset('fine')
    assert fine['schema_version'] == 1
    assert fine['name'] == 'fine'
    assert fine['process'] == {'layer_height_mm': 0.03}
    assert any('ncalibrated' in note for note in fine['notes'])
    assert load_process_preset('default')['process'] == DEFAULTS['process']
    fast = load_process_preset('fast')
    assert fast['process']['layer_height_mm'] == 0.1
    assert fast['process']['bottom_layers'] == 3
    fine['process']['layer_height_mm'] = 99
    assert load_process_preset('fine')['process']['layer_height_mm'] == 0.03


def test_apply_process_overlays_process_and_preserves_other_sections():
    settings = resolve_settings()
    original = deepcopy(settings)
    applied = apply_process_preset(settings, 'fast')

    assert applied['process']['layer_height_mm'] == 0.1
    assert applied['process']['bottom_layers'] == 3
    assert applied['process']['bottom_exposure_s'] == original['process']['bottom_exposure_s']
    assert applied['printer'] == original['printer']
    assert applied['support'] == original['support']
    assert settings == original


def test_fine_adapts_layer_height_to_printer_range():
    settings = resolve_settings()
    settings['printer']['layer_height_range_mm'] = [0.05, 0.2]
    applied = apply_process_preset(settings, 'fine')
    assert applied['process']['layer_height_mm'] == 0.05

    settings['printer']['layer_height_range_mm'] = [0.01, 0.2]
    applied = apply_process_preset(settings, 'fine')
    assert applied['process']['layer_height_mm'] == 0.03


def test_process_json_round_trip(tmp_path):
    path = tmp_path / 'custom.json'
    saved = save_process_preset(path, 'custom', {
        'layer_height_mm': 0.08, 'bottom_layers': 2,
    })
    assert saved == load_process_preset(path)
    assert json.loads(path.read_text()) == {
        'name': 'custom', 'schema_version': 1,
        'process': {'bottom_layers': 2, 'layer_height_mm': 0.08},
    }
    applied = apply_process_preset(resolve_settings(), path)
    assert applied['process']['layer_height_mm'] == 0.08
    assert applied['process']['bottom_layers'] == 2


@pytest.mark.parametrize('document', [
    {'schema_version': 1, 'name': 'bad', 'process': {'unknown': 1}},
    {'schema_version': 1, 'name': 'bad', 'process': {'layer_height_mm': float('nan')}},
    {'schema_version': 2, 'name': 'bad', 'process': {}},
    {'schema_version': 1, 'name': '', 'process': {}},
    {'schema_version': 1, 'name': 'bad', 'support': {}},
])
def test_invalid_process_preset_data_is_rejected(document, tmp_path):
    with pytest.raises(VoxelMillError):
        save_process_preset(tmp_path / 'bad.json', document)


def test_process_preset_cli_list_show_save(tmp_path, capsys):
    assert main(['preset', 'list', '--kind', 'process']) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed['presets'] == ['fine', 'default', 'fast']
    assert listed['kind'] == 'process'

    assert main(['preset', 'show', 'fine', '--kind', 'process']) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown['name'] == 'fine'
    assert shown['process']['layer_height_mm'] == 0.03

    out = tmp_path / 'myfine.json'
    assert main(['preset', 'save', '--kind', 'process', '--name', 'myfine',
                 '--process-preset', 'fine', '--output', str(out)]) == 0
    saved = json.loads(out.read_text())
    assert saved['name'] == 'myfine'
    assert saved['process']['layer_height_mm'] == 0.03
    assert 'support' not in saved


def test_support_preset_cli_remains_default_kind(capsys):
    assert main(['preset', 'list']) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed == {
        'schema_version': 1,
        'presets': ['light', 'medium', 'heavy', 'chitubox-mars5'],
    }
    assert 'kind' not in listed


def test_process_preset_flag_applies_before_set(capsys):
    assert main(['profile', '--process-preset', 'fast',
                 '--set', 'process.layer_height_mm=0.05']) == 0
    process = json.loads(capsys.readouterr().out)['settings']['process']
    assert process['layer_height_mm'] == 0.05
    assert process['bottom_layers'] == 3
