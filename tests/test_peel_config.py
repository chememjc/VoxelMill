import copy

import pytest

from voxelmill.cli import _overrides, build_parser
from voxelmill.config import DEFAULTS, resolve_settings, validate_settings
from voxelmill.contracts import VoxelMillError


def test_support_defaults_are_point_to_point_with_early_braces():
    support = resolve_settings()['support']
    assert support['model_anchor_length_mm'] == 2.0
    assert support['model_anchor_diameter_mm'] == 0.4
    assert support['model_anchor_penetration_mm'] == 0.15
    assert support['break_point_diameter_mm'] == 0.8
    assert support['allow_part_to_part'] is True


def test_peel_defaults_and_cli_toggle():
    settings = resolve_settings()
    assert settings['peel'] == {
        'enabled': True, 'max_angle_deg': 10.0,
        'area_threshold_mm2': 100.0, 'reference_lift_speed': 0.05}
    args = build_parser().parse_args(['profile', '--no-peel-analysis'])
    assert _overrides(args)['peel']['enabled'] is False
    args = build_parser().parse_args(['profile', '--peel-analysis'])
    assert _overrides(args)['peel']['enabled'] is True


@pytest.mark.parametrize(('key', 'value'), [
    ('enabled', 1), ('max_angle_deg', 90), ('max_angle_deg', -1),
    ('area_threshold_mm2', 0), ('reference_lift_speed', 0),
])
def test_peel_validation_is_strict(key, value):
    settings = copy.deepcopy(DEFAULTS)
    settings['peel'][key] = value
    with pytest.raises(VoxelMillError):
        validate_settings(settings)


def test_peel_round_trips_in_full_profile_but_not_hardware_only(tmp_path):
    from voxelmill.profiles import dumps_printer_profile, save_printer_profile
    settings = resolve_settings(overrides={'peel': {'max_angle_deg': 8.0}})
    assert settings['peel']['max_angle_deg'] == 8.0
    path = tmp_path / 'printer.ptr'
    save_printer_profile(path, settings)
    assert resolve_settings(path)['peel']['max_angle_deg'] == 8.0
    hardware = dumps_printer_profile(settings, hardware_only=True)
    assert '[peel]' not in hardware


def test_legacy_project_settings_acquire_defaults_in_editor_and_cli(tmp_path):
    from voxelmill.gui.document import Document
    from voxelmill.project import save_project
    from voxelmill.cli import _settings
    legacy = resolve_settings(overrides={'support': {'spacing_mm': 7.0}})
    legacy.pop('peel')
    project = tmp_path / 'legacy.voxmil'
    save_project(project, {'settings': legacy})
    document = Document.load(project)
    assert document.settings['peel'] == DEFAULTS['peel']
    assert document.settings['support']['spacing_mm'] == 7.0
    args = build_parser().parse_args(['prepare', str(project)])
    assert _settings(args, legacy) == document.settings

    legacy['peel'] = {'enabled': False}
    save_project(project, {'settings': legacy})
    with pytest.raises(VoxelMillError, match='Incomplete resolved peel'):
        Document.load(project)
    with pytest.raises(VoxelMillError, match='Incomplete resolved peel'):
        _settings(args, legacy)


def test_legacy_support_keys_are_filled_but_unknown_keys_still_fail(tmp_path):
    from voxelmill.gui.document import Document
    from voxelmill.project import save_project
    from voxelmill.cli import _settings
    from voxelmill.config import DEFAULTS, fill_legacy_settings
    legacy = resolve_settings(overrides={'support': {'spacing_mm': 7.0}})
    legacy['support'].pop('tip_shape')
    legacy['support'].pop('break_point_diameter_mm')
    legacy['support'].pop('model_anchor_length_mm')
    legacy['support'].pop('model_anchor_diameter_mm')
    legacy['support'].pop('model_anchor_penetration_mm')
    project = tmp_path / 'legacy-support.voxmil'
    save_project(project, {'settings': legacy})
    document = Document.load(project)
    assert document.settings['support']['tip_shape'] == DEFAULTS['support']['tip_shape']
    assert document.settings['support']['break_point_diameter_mm'] == 0.0
    assert document.settings['support']['model_anchor_length_mm'] == 0.0
    assert document.settings['support']['model_anchor_diameter_mm'] == 0.0
    assert document.settings['support']['model_anchor_penetration_mm'] == 0.0
    assert document.settings['support']['spacing_mm'] == 7.0
    args = build_parser().parse_args(['prepare', str(project)])
    assert _settings(args, copy.deepcopy(legacy))['support']['tip_shape'] == 'cone'
    filled = fill_legacy_settings(copy.deepcopy(legacy))
    assert filled['support']['tip_shape'] == 'cone'
    assert filled['support']['break_point_diameter_mm'] == 0.0
    assert filled['support']['model_anchor_length_mm'] == 0.0
    legacy['support']['not_a_real_key'] = 1
    save_project(project, {'settings': legacy})
    with pytest.raises(VoxelMillError, match='Unknown support'):
        Document.load(project)
