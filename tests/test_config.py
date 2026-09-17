from copy import deepcopy
from pathlib import Path

import pytest

from voxelmill.config import DEFAULTS, layer_exposure, resolve_settings, validate_settings
from voxelmill.contracts import VoxelMillError

PROFILES = Path(__file__).resolve().parents[1] / 'profiles'


def test_profiles_match_defaults_and_do_not_mutate():
    settings = resolve_settings(PROFILES / 'mars5-ultra.ptr', PROFILES / 'sunlu-abs-like-gray.res')
    assert settings == DEFAULTS
    settings['printer']['build_mm'][0] = 1
    assert resolve_settings()['printer']['build_mm'][0] == 153.36


def test_precedence_and_independent_waits(tmp_path):
    printer = tmp_path / 'printer.ptr'
    printer.write_text('schema_version=1\n[printer]\nid="mars5-ultra"\n[process]\nnormal_exposure_s=8.0\nnormal_wait_after_lift_s=2.0\n')
    settings = resolve_settings(printer, PROFILES / 'sunlu-abs-like-gray.res', {'process': {'normal_exposure_s': 7, 'normal_wait_after_lift_s': 3}})
    assert settings['process']['normal_exposure_s'] == 7
    assert settings['process']['bottom_exposure_s'] == 35
    assert settings['process']['bottom_wait_after_lift_s'] == 0
    assert settings['process']['normal_wait_after_lift_s'] == 3


def test_linear_transition_excludes_endpoints():
    settings = resolve_settings()
    assert [layer_exposure(settings, i) for i in range(11)] == [35, 35, 35, 35, 29.75, 24.5, 19.25, 14, 8.75, 3.5, 3.5]
    settings['process']['transition_layers'] = 0
    assert layer_exposure(settings, 4) == 3.5
    with pytest.raises(VoxelMillError):
        layer_exposure(settings, -1)


@pytest.mark.parametrize('overrides', [
    {'printer': {'build_mm': [153.36, 77.76, float('nan')]}},
    {'printer': {'pixels': [8520.0, 4320]}},
    {'printer': {'edge_clearance_mm': 40}},
    {'printer': {'build_mm': [100, 100, 100]}},
    {'process': {'bottom_layers': True}},
    {'process': {'normal_exposure_s': float('inf')}},
    {'process': {'layer_height_mm': 1}},
    {'support': {'contact_diameter_mm': 3}},
    {'support': {'penetration_mm': 3}},
    {'repair': {'seal_voids': 1}},
    {'repair': {'aggressiveness': 'magic'}},
    {'repair': {'support_void_policy': 'skip'}},
    {'repair': {'weld_tolerance_mm': 0.06}},
    {'repair': {'weld_tolerance_mm': -0.001}},
    {'support': {'drop_attached_unroutable': 1}},
    {'support': {'contour_supports': 1}},
    {'support': {'boundary_supports': 1}},
    {'resources': {'workers': -1}},
    {'resources': {'workers': 33}},
    {'resources': {'worker_policy': 'fastest'}},
    {'resources': {'memory_gib': .1}},
    {'process': {'antialias_levels': 3}},
    {'process': {'antialias_supports': 1}},
    {'hollow': {'mode': 'outer'}},
    {'hollow': {'infill': 'gyroidx'}},
    {'printer': {'motion': {'lift_height': 1.0, 'retract_height': 0.5}}},
    {'resin': {'build_mm': [1, 2, 3]}},
    {'typo': {}},
])
def test_reject_invalid_overrides(overrides):
    with pytest.raises(VoxelMillError):
        resolve_settings(overrides=overrides)


def test_zero_workers_is_the_derive_sentinel_not_an_error():
    """0 means "ask the machine", the same convention the brace sizes use."""
    from voxelmill.contracts import ResourceBudget
    from voxelmill.topology import default_workers
    settings = resolve_settings(overrides={'resources': {'workers': 0}})
    assert settings['resources']['workers'] == 0
    assert ResourceBudget(**settings['resources']).workers == default_workers()
    assert resolve_settings()['resources']['workers'] == 0


@pytest.mark.parametrize('body', [
    'schema_version=true\n',
    'schema_version=2\n',
    'schema_version=1\n[resin]\nid="test"\n[processes.mars5-ultra.printer]\nbuild_mm=[1,2,3]\n',
    'schema_version=1\n[resin]\nid="test"\n[processes.mars5-ultra.process]\nbuild_mm=[1,2,3]\n',
    'schema_version=1\n[resin]\nid="test"\n[processes.other.process]\nnormal_exposure_s=2.0\n',
])
def test_reject_bad_resin_profiles(tmp_path, body):
    path = tmp_path / 'resin.res'
    path.write_text(body)
    with pytest.raises(VoxelMillError):
        resolve_settings(resin_path=path)


def test_exact_orifice_threshold_and_zero_deviation_allowed():
    settings = resolve_settings(overrides={'repair': {'min_orifice_area_mm2': 1., 'max_deviation_mm': 0}})
    assert settings['repair']['min_orifice_area_mm2'] == 1


def test_tsmc_retract_must_match_lift_and_stage2_needs_speed():
    with pytest.raises(VoxelMillError, match='retract heights must sum'):
        resolve_settings(overrides={'printer': {'motion': {'lift_height': 1.0, 'retract_height': 0.4}}})
    with pytest.raises(VoxelMillError, match='speed2'):
        resolve_settings(overrides={'printer': {'motion': {
            'lift_height': 1.0, 'lift_height2': 0.5, 'lift_speed2': 0.0,
            'retract_height': 1.5}}})
    settings = resolve_settings(overrides={'printer': {'motion': {
        'lift_height': 1.0, 'lift_height2': 0.5, 'lift_speed2': 2.0,
        'retract_height': 1.0, 'retract_height2': 0.5, 'retract_speed2': 4.0}}})
    assert settings['printer']['motion']['lift_height2'] == 0.5


def test_antialias_and_hollow_defaults():
    settings = resolve_settings()
    assert settings['process']['antialias_levels'] == 1
    assert settings['process']['antialias_supports'] is False
    assert settings['hollow']['enabled'] is False
    assert settings['hollow']['infill'] == 'none'


def test_weld_tolerance_default_and_cap():
    assert resolve_settings()['repair']['weld_tolerance_mm'] == 0.0
    settings = resolve_settings(overrides={'repair': {'weld_tolerance_mm': 0.05}})
    assert settings['repair']['weld_tolerance_mm'] == 0.05
    from voxelmill.config import DEFAULTS, fill_legacy_settings
    legacy = deepcopy(DEFAULTS)
    legacy['repair'].pop('weld_tolerance_mm')
    filled = fill_legacy_settings(legacy)
    assert filled['repair']['weld_tolerance_mm'] == 0.0


def test_incomplete_resolved_config_rejected():
    settings = deepcopy(DEFAULTS)
    del settings['support']['spacing_mm']
    with pytest.raises(VoxelMillError):
        validate_settings(settings)
