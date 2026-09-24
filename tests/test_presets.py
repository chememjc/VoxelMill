"""Portable support preset API tests."""
from copy import deepcopy
import json

import pytest

from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.presets import apply_preset, list_presets, load_preset, save_preset


def test_builtins_are_named_and_independent():
    # The three intensity presets come first; chitubox-mars5 is a transcribed
    # machine configuration rather than a point on that scale.
    assert list_presets() == ('light', 'medium', 'heavy', 'chitubox-mars5')
    light = load_preset('light')
    assert light['schema_version'] == 1
    assert light['name'] == 'light'
    assert light['support'] == {'spacing_mm': 4.0, 'pillar_diameter_mm': 0.7,
                                'contact_diameter_mm': 0.3}
    assert load_preset('medium')['support']['spacing_mm'] == 3.0
    assert load_preset('heavy')['support']['pillar_diameter_mm'] == 1.3
    light['support']['spacing_mm'] = 99
    assert load_preset('light')['support']['spacing_mm'] == 4.0


def test_apply_overlays_support_and_preserves_other_sections():
    settings = resolve_settings()
    original = deepcopy(settings)
    applied = apply_preset(settings, 'heavy')

    assert applied['support']['spacing_mm'] == 2.5
    assert applied['support']['pillar_diameter_mm'] == 1.3
    assert applied['support']['tip_length_mm'] == original['support']['tip_length_mm']
    assert applied['printer'] == original['printer']
    assert applied['process'] == original['process']
    assert settings == original


def test_custom_json_round_trip_and_convenient_save_form(tmp_path):
    path = tmp_path / 'custom.json'
    saved = save_preset(path, 'custom', {'spacing_mm': 4.0, 'max_span_mm': 6.0})
    assert saved == load_preset(path)
    assert json.loads(path.read_text()) == {
        'name': 'custom', 'schema_version': 1,
        'support': {'max_span_mm': 6.0, 'spacing_mm': 4.0},
    }
    applied = apply_preset(resolve_settings(), path)
    assert applied['support']['spacing_mm'] == 4.0
    assert applied['support']['max_span_mm'] == 6.0


@pytest.mark.parametrize('document', [
    {'schema_version': 1, 'name': 'bad', 'support': {'unknown': 1}},
    {'schema_version': 1, 'name': 'bad', 'support': {'spacing_mm': float('nan')}},
    {'schema_version': 1, 'name': 'bad', 'support': {'spacing_mm': float('inf')}},
    {'schema_version': 2, 'name': 'bad', 'support': {}},
    {'schema_version': 1, 'name': '', 'support': {}},
    {'schema_version': 1, 'name': 'bad', 'support': {'contact_diameter_mm': 3.0}},
])
def test_invalid_preset_data_is_rejected(document, tmp_path):
    with pytest.raises(VoxelMillError):
        save_preset(tmp_path / 'bad.json', document)


def test_unknown_and_nonfinite_json_are_rejected(tmp_path):
    unknown = tmp_path / 'unknown.json'
    unknown.write_text('{"schema_version":1,"name":"x","support":{},"extra":1}')
    with pytest.raises(VoxelMillError):
        load_preset(unknown)

    nonfinite = tmp_path / 'nonfinite.json'
    nonfinite.write_text('{"schema_version":1,"name":"x","support":{"spacing_mm":NaN}}')
    with pytest.raises(VoxelMillError):
        load_preset(nonfinite)


def test_failed_save_leaves_existing_destination_untouched(tmp_path):
    path = tmp_path / 'existing.json'
    save_preset(path, 'light')
    before = path.read_bytes()
    with pytest.raises(VoxelMillError):
        save_preset(path, 'bad', {'spacing_mm': float('nan')})
    assert path.read_bytes() == before


def test_embedded_resin_support_preset_overrides_builtin(tmp_path):
    """A8: resin processes.*.support_presets.NAME wins over builtin light=4."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    resin = tmp_path / 'embedded.res'
    resin.write_text(
        'schema_version = 1\n'
        '[resin]\nid = "embedded"\nname = "Embedded"\n'
        '[processes.mars5-ultra.process]\nnormal_exposure_s = 3.5\n'
        '[processes.mars5-ultra.support]\nspacing_mm = 3.0\n'
        '[processes.mars5-ultra.support_presets.light]\nspacing_mm = 9.0\n'
    )
    assert load_preset('light')['support']['spacing_mm'] == 4.0
    settings = resolve_settings(
        root / 'profiles/mars5-ultra.ptr', resin, support_preset='light')
    assert settings['support']['spacing_mm'] == 9.0
    assert 'support_presets' not in settings
    # Unmatched embedded name still resolves the portable builtin.
    fallback = resolve_settings(
        root / 'profiles/mars5-ultra.ptr', resin, support_preset='heavy')
    assert fallback['support']['spacing_mm'] == 2.5
    # JSON path presets still work beside an embedded table.
    custom = tmp_path / 'custom.json'
    save_preset(custom, 'custom', {'spacing_mm': 4.25})
    from_json = resolve_settings(
        root / 'profiles/mars5-ultra.ptr', resin, support_preset=str(custom))
    assert from_json['support']['spacing_mm'] == 4.25
