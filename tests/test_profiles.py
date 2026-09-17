"""Profile discovery, provenance, comparison, saving and resin binding."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from voxelmill import profiles
from voxelmill.cli import main
from voxelmill.config import DEFAULTS, resin_usage, resolve_settings, validate_settings
from voxelmill.contracts import VoxelMillError

ROOT = Path(__file__).resolve().parents[1]
PRINTER = ROOT / 'profiles/mars5-ultra.ptr'
RESIN = ROOT / 'profiles/sunlu-abs-like-gray.res'


@pytest.fixture
def library(tmp_path, monkeypatch):
    """An isolated search path so a real user config cannot change a result."""
    top = tmp_path / 'top'
    top.mkdir()
    monkeypatch.setenv(profiles.PATH_VARIABLE, str(top))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    return top


def test_packaged_profiles_match_the_repository_copies():
    """The built-in layer is a real copy of the tracked profiles, not a stale one.

    The packaged ``mars5-ultra.ptr`` had already drifted: it still carried the
    empty ``[printer.motion]`` table from before the reference values were
    recorded, so a user resolving the built-in identifier would have been
    refused a GOO export that the repository profile completes.
    """
    packaged = profiles.builtin_directory()
    for source in (PRINTER, RESIN):
        assert (packaged / source.name).read_bytes() == source.read_bytes(), source.name


def test_search_path_is_ordered_and_env_wins(library, tmp_path):
    layers = [layer for layer, _ in profiles.search_directories()]
    assert layers == [f'{profiles.PATH_VARIABLE}[0]', 'user', 'system', 'builtin']
    directories = dict(profiles.search_directories())
    assert directories['user'] == tmp_path / 'xdg' / 'voxelmill' / 'profiles'


def test_a_higher_layer_shadows_a_builtin_and_says_so(library):
    (library / 'mars5-ultra.ptr').write_bytes(PRINTER.read_bytes())
    found = [e for e in profiles.discover('printer') if e['id'] == 'mars5-ultra']
    assert len(found) == 1
    entry = found[0]
    assert entry['layer'] == f'{profiles.PATH_VARIABLE}[0]'
    assert entry['path'] == str(library / 'mars5-ultra.ptr')
    # The shadowed file is named rather than silently dropped.
    assert [Path(p).name for p in entry['shadows']] == ['mars5-ultra.ptr']
    assert profiles.resolve_profile_path('mars5-ultra', 'printer') == library / 'mars5-ultra.ptr'


def test_a_malformed_library_profile_is_listed_and_refused_by_name(library):
    (library / 'broken.ptr').write_text('this is not toml =\n')
    entry = next(e for e in profiles.discover('printer') if e['id'] == 'broken')
    assert entry['error'] and entry['name'] is None
    with pytest.raises(VoxelMillError) as raised:
        profiles.resolve_profile_path('broken', 'printer')
    assert 'is invalid' in str(raised.value)


def test_identifiers_and_paths_are_distinguished_by_shape(library, tmp_path):
    """A bare identifier is never opened as a path and a path is never searched.

    ``os.altsep`` is None on POSIX, and the empty string is a substring of
    every string, so a naive separator test treats every identifier as a path.
    """
    assert profiles.resolve_profile_path('mars5-ultra', 'printer').name == 'mars5-ultra.ptr'
    assert profiles.resolve_profile_path(str(PRINTER), 'printer') == PRINTER
    assert profiles.resolve_profile_path(None, 'printer') is None
    with pytest.raises(VoxelMillError) as missing_path:
        profiles.resolve_profile_path(str(tmp_path / 'absent.ptr'), 'printer')
    assert 'No printer profile at' in str(missing_path.value)
    with pytest.raises(VoxelMillError) as missing_id:
        profiles.resolve_profile_path('no-such-machine', 'printer')
    assert 'known identifiers' in str(missing_id.value)


def test_provenance_attributes_each_value_to_the_layer_that_last_set_it():
    record = profiles.provenance(PRINTER, RESIN, {'process': {'normal_exposure_s': 4.0}})
    assert record['process']['normal_exposure_s'] == 'override'
    assert record['printer']['motion']['lift_speed'] == 'default'
    # Every leaf of the resolved settings is attributed, none omitted.
    resolved = resolve_settings(PRINTER, RESIN, {'process': {'normal_exposure_s': 4.0}})
    assert set(profiles._leaves(record)) and \
        {path for path, _ in profiles._leaves(record)} == \
        {path for path, _ in profiles._leaves(resolved)}


def test_provenance_reports_a_printer_layer_when_the_profile_changes_a_value(tmp_path):
    changed = tmp_path / 'changed.ptr'
    changed.write_text(PRINTER.read_text().replace('edge_clearance_mm = 2.0',
                                                   'edge_clearance_mm = 3.0'))
    record = profiles.provenance(changed, None, None)
    assert record['printer']['edge_clearance_mm'] == 'printer'
    assert record['printer']['build_mm'] == 'default'


def test_diff_reports_both_sides_and_absent_keys():
    left = resolve_settings(PRINTER, RESIN, None)
    right = resolve_settings(PRINTER, RESIN, {'support': {'spacing_mm': 4.0}})
    changes = profiles.diff_settings(left, right)
    assert changes == {'support': {'spacing_mm': {'left': 3.0, 'right': 4.0}}}
    assert profiles.diff_settings({'a': 1}, {})['a'] == {'left': 1, 'right': '<absent>'}
    assert profiles.diff_settings(left, left) == {}


def test_saving_a_printer_profile_reloads_to_the_same_settings(tmp_path):
    settings = resolve_settings(PRINTER, RESIN, {'support': {'spacing_mm': 4.25},
                                                 'assembly': {'union': 'exact'}})
    destination = tmp_path / 'written.ptr'
    payload = profiles.save_printer_profile(destination, settings, name='Bench Machine')
    assert payload['round_trip'] == 'identical' and payload['printer_name'] == 'Bench Machine'
    reloaded = resolve_settings(destination, None, None)
    assert reloaded['support']['spacing_mm'] == 4.25
    assert reloaded['assembly']['union'] == 'exact'
    assert reloaded['printer']['name'] == 'Bench Machine'
    # scratch_dir is the one optional with no TOML representation; it comes
    # back as the default null rather than as some placeholder string.
    assert reloaded['resources']['scratch_dir'] is None
    # Every other value survives, including the whole motion table.
    expected = dict(settings)
    expected['printer'] = dict(settings['printer'], name='Bench Machine')
    assert profiles.diff_settings(expected, reloaded) == {}


def test_a_saved_profile_is_written_atomically_and_leaves_no_temporary(tmp_path):
    destination = tmp_path / 'atomic.ptr'
    profiles.save_printer_profile(destination, resolve_settings(PRINTER, None, None))
    assert [p.name for p in sorted(tmp_path.iterdir())] == ['atomic.ptr']


def test_resin_bind_copies_a_process_and_refuses_to_overwrite_its_source(tmp_path):
    bound = tmp_path / 'bound.res'
    payload = profiles.bind_resin_process(RESIN, bound, target_printer='saturn4-ultra')
    assert payload['source_printer'] == 'mars5-ultra'
    assert payload['bound_printers'] == ['mars5-ultra', 'saturn4-ultra']
    assert 'starting point, not a calibration' in payload['calibration']
    # The copy resolves for both printers and carries the same process values.
    for printer_id in ('mars5-ultra', 'saturn4-ultra'):
        printer = tmp_path / f'{printer_id}.ptr'
        printer.write_text(PRINTER.read_text().replace('id = "mars5-ultra"',
                                                       f'id = "{printer_id}"'))
        settings = resolve_settings(printer, bound, None)
        assert settings['process']['normal_exposure_s'] == 3.5
    with pytest.raises(VoxelMillError) as raised:
        profiles.bind_resin_process(bound, bound, source_printer='mars5-ultra',
                                    target_printer='mars5-clone')
    assert 'Refusing to overwrite the source' in str(raised.value)


def test_resin_bind_requires_an_unambiguous_source_and_a_distinct_target(tmp_path):
    bound = tmp_path / 'two.res'
    profiles.bind_resin_process(RESIN, bound, target_printer='saturn4-ultra')
    with pytest.raises(VoxelMillError) as ambiguous:
        profiles.bind_resin_process(bound, tmp_path / 'x.res', target_printer='third')
    assert '--from is required' in str(ambiguous.value)
    with pytest.raises(VoxelMillError) as same:
        profiles.bind_resin_process(RESIN, tmp_path / 'y.res',
                                    source_printer='mars5-ultra', target_printer='mars5-ultra')
    assert 'must differ' in str(same.value)
    with pytest.raises(VoxelMillError) as unknown:
        profiles.bind_resin_process(RESIN, tmp_path / 'z.res',
                                    source_printer='nope', target_printer='other')
    assert 'has no process for printer' in str(unknown.value)


# ---- resin usage -----------------------------------------------------------

def test_unsupplied_density_and_price_report_null_rather_than_a_guess():
    settings = resolve_settings(PRINTER, RESIN, None)
    assert settings['resin']['density_g_cm3'] == 0.0
    usage = resin_usage(settings, 25000.0)
    assert usage['volume_ml'] == 25.0
    assert usage['mass_g'] is None and usage['cost'] is None
    assert usage['density_g_cm3'] is None and usage['cost_per_liter'] is None
    assert resin_usage(settings, None)['source'] == 'unavailable'


def test_supplied_density_and_price_produce_weight_and_cost():
    settings = resolve_settings(PRINTER, RESIN, {
        'resin': {'density_g_cm3': 1.1, 'cost_per_liter': 45.0, 'currency': 'EUR'}})
    usage = resin_usage(settings, 25000.0)
    assert usage['volume_ml'] == 25.0
    assert usage['mass_g'] == pytest.approx(27.5)
    assert usage['cost'] == pytest.approx(25.0 / 1000.0 * 45.0)
    assert usage['currency'] == 'EUR'


def test_currency_must_fit_the_eight_ascii_byte_goo_field():
    for bad in ('', '   ', 'euro-cents-per-liter', 'e€u'):
        with pytest.raises(VoxelMillError):
            resolve_settings(PRINTER, RESIN, {'resin': {'currency': bad}})
    assert resolve_settings(PRINTER, RESIN,
                            {'resin': {'currency': 'CAD$'}})['resin']['currency'] == 'CAD$'


def test_goo_header_carries_the_derived_weight_and_cost():
    from voxelmill.goo import header_from_settings
    plain = resolve_settings(PRINTER, RESIN, None)
    header = header_from_settings(plain, 10, volume_mm3=25000.0)
    # Unset stays exactly what every export wrote before these fields existed.
    assert header['material_grams'] == 0.0 and header['material_cost'] == 0.0
    assert header['price_currency'] == '$'
    priced = resolve_settings(PRINTER, RESIN, {
        'resin': {'density_g_cm3': 1.1, 'cost_per_liter': 45.0, 'currency': 'EUR'}})
    header = header_from_settings(priced, 10, volume_mm3=25000.0)
    assert header['material_grams'] == pytest.approx(27.5)
    assert header['material_cost'] == pytest.approx(1.125)
    assert header['price_currency'] == 'EUR'


def test_resin_fields_are_additive_and_leave_existing_profiles_resolving():
    assert resolve_settings(PRINTER, RESIN) == DEFAULTS
    assert set(DEFAULTS['resin']) == {'id', 'name', 'density_g_cm3', 'cost_per_liter', 'currency'}
    validate_settings(resolve_settings(None, None, None))


# ---- command line ----------------------------------------------------------

def _run(capsys, argv):
    assert main(argv) == 0
    return json.loads(capsys.readouterr().out)


def test_cli_accepts_library_identifiers_for_both_profiles(capsys):
    payload = _run(capsys, ['profile', '--printer', 'mars5-ultra',
                            '--resin', 'sunlu-abs-like-gray'])
    assert payload['settings']['printer']['id'] == 'mars5-ultra'
    assert payload['settings']['resin']['id'] == 'sunlu-abs-like-gray'
    assert payload['resin_usage_per_ml']['volume_ml'] == 1.0


def test_cli_profile_list_reports_the_search_path_and_the_builtins(capsys, library):
    payload = _run(capsys, ['profile', 'list'])
    assert payload['command'] == 'profile list'
    assert [e['layer'] for e in payload['search_path']][-1] == 'builtin'
    identifiers = {e['id'] for e in payload['profiles']}
    assert {'mars5-ultra', 'sunlu-abs-like-gray'} <= identifiers
    printers = _run(capsys, ['profile', 'list', '--kind', 'printer'])
    assert {e['kind'] for e in printers['profiles']} == {'printer'}


def test_cli_profile_diff_show_provenance_and_save(capsys, tmp_path):
    diff = _run(capsys, ['profile', 'diff', '--printer', str(PRINTER), '--against', 'defaults'])
    assert diff['differences'] == {}
    diff = _run(capsys, ['profile', 'diff', '--printer', str(PRINTER),
                         '--set', 'support.spacing_mm=6', '--against', 'defaults'])
    assert diff['differences']['support']['spacing_mm'] == {'left': 6, 'right': 3.0}
    shown = _run(capsys, ['profile', '--printer', str(PRINTER), '--provenance'])
    assert shown['provenance']['support']['spacing_mm'] == 'default'
    saved = _run(capsys, ['profile', 'save', '--printer', str(PRINTER),
                          '--output', str(tmp_path / 'out.ptr')])
    assert saved['round_trip'] == 'identical'
    assert (tmp_path / 'out.ptr').is_file()


def test_cli_profile_diff_requires_a_comparison_target(capsys):
    assert main(['profile', 'diff']) != 0
    error = json.loads(capsys.readouterr().err)['error']
    assert error['code'] == 'invalid_option' and '--against' in error['message']


def test_cli_resin_list_show_and_bind(capsys, tmp_path):
    listing = _run(capsys, ['resin', 'list'])
    assert listing['command'] == 'resin list'
    shown = _run(capsys, ['resin', 'show', 'sunlu-abs-like-gray'])
    assert shown['bound_printers'] == ['mars5-ultra']
    bound = _run(capsys, ['resin', 'bind', 'sunlu-abs-like-gray', '--to', 'saturn4-ultra',
                          '--output', str(tmp_path / 'bound.res')])
    assert bound['target_printer'] == 'saturn4-ultra'
    assert (tmp_path / 'bound.res').is_file()


def test_cli_resin_show_falls_back_to_the_common_resin_flag(capsys):
    shown = _run(capsys, ['resin', 'show', '--resin', str(RESIN)])
    assert shown['resin']['id'] == 'sunlu-abs-like-gray'


def test_the_positional_resin_wins_over_the_shared_flag(capsys, tmp_path):
    """``voxelmill resin show X`` names X, whatever ``--resin`` happens to say."""
    bound = tmp_path / 'other.res'
    profiles.bind_resin_process(RESIN, bound, target_printer='saturn4-ultra')
    shown = _run(capsys, ['resin', 'show', str(bound), '--resin', str(RESIN)])
    assert shown['bound_printers'] == ['mars5-ultra', 'saturn4-ultra']


def test_save_resin_profile_round_trips_with_current_printer(tmp_path):
    settings = resolve_settings(PRINTER, RESIN, {
        'process': {'normal_exposure_s': 4.25},
        'support': {'spacing_mm': 4.0},
    })
    output = tmp_path / 'saved.res'
    payload = profiles.save_resin_profile(output, settings, name='My resin')
    assert payload['round_trip'] == 'identical'
    assert payload['printer_id'] == 'mars5-ultra'
    assert payload['resin_name'] == 'My resin'
    reloaded = resolve_settings(PRINTER, output, None)
    assert reloaded['resin']['name'] == 'My resin'
    assert reloaded['process']['normal_exposure_s'] == 4.25
    assert reloaded['support']['spacing_mm'] == 4.0


def test_printer_save_ignores_resin_metadata_and_can_save_hardware_only(tmp_path):
    settings = resolve_settings(PRINTER, RESIN, {
        'resin': {'name': 'Custom resin', 'density_g_cm3': 1.1},
    })
    full = profiles.save_printer_profile(tmp_path / 'full.ptr', settings)
    assert full['round_trip'] == 'identical'
    hardware = profiles.save_printer_profile(tmp_path / 'hardware.ptr', settings,
                                             hardware_only=True)
    assert hardware['hardware_only'] is True
    assert resolve_settings(tmp_path / 'hardware.ptr', None)['printer'] == settings['printer']


def test_hardware_only_save_uses_current_process_for_custom_layer_range(tmp_path):
    settings = resolve_settings(PRINTER, RESIN, {
        'printer': {'layer_height_range_mm': [0.1, 0.2]},
        'process': {'layer_height_mm': 0.1},
    })
    output = tmp_path / 'hardware.ptr'
    profiles.save_printer_profile(output, settings, hardware_only=True)
    assert resolve_settings(output, None, {
        'process': {'layer_height_mm': 0.1}})['printer']['layer_height_range_mm'] == [0.1, 0.2]


def test_printer_save_preserves_existing_output_when_round_trip_fails(tmp_path, monkeypatch):
    settings = resolve_settings(PRINTER, RESIN, None)
    output = tmp_path / 'existing.ptr'
    output.write_text('sentinel')
    original = profiles.resolve_settings

    def fail_staged(printer_path=None, resin_path=None, overrides=None):
        if printer_path is not None and Path(printer_path).parent == tmp_path and Path(printer_path) != output:
            raise VoxelMillError('invalid_profile', 'forced round-trip failure')
        return original(printer_path, resin_path, overrides)

    monkeypatch.setattr(profiles, 'resolve_settings', fail_staged)
    with pytest.raises(VoxelMillError, match='forced round-trip failure'):
        profiles.save_printer_profile(output, settings)
    assert output.read_text() == 'sentinel'


def test_cli_resin_save_uses_shared_resolved_settings(capsys, tmp_path):
    output = tmp_path / 'saved.res'
    payload = _run(capsys, ['resin', 'save', '--printer', str(PRINTER),
                            '--resin', str(RESIN), '--set',
                            'process.normal_exposure_s=4.25',
                            '--output', str(output), '--name', 'CLI resin'])
    assert payload['command'] == 'resin save'
    assert payload['resin_name'] == 'CLI resin'
    assert resolve_settings(PRINTER, output, None)['process']['normal_exposure_s'] == 4.25


def test_resin_save_round_trips_custom_printer_id_range_and_quoted_id(tmp_path):
    settings = resolve_settings(PRINTER, None, {
        'printer': {'id': 'printer.dot"quoted', 'layer_height_range_mm': [0.1, 0.2]},
        'process': {'layer_height_mm': 0.15},
        'support': {'spacing_mm': 4.0, 'max_contact_gap_mm': 5.0,
                    'tip_base_diameter_mm': 0.8},
    })
    # Keep the resin metadata while changing only the machine/process context.
    settings['resin'] = resolve_settings(PRINTER, RESIN, None)['resin']
    validate_settings(settings)
    printer = tmp_path / 'printer.ptr'
    profiles.save_printer_profile(printer, settings)
    resin = tmp_path / 'resin.res'
    profiles.save_resin_profile(resin, settings)
    reloaded = resolve_settings(printer, resin, None)
    assert reloaded['printer']['id'] == 'printer.dot"quoted'
    assert reloaded['process']['layer_height_mm'] == 0.15
    assert reloaded['support']['max_contact_gap_mm'] == 5.0
    assert reloaded['support']['tip_base_diameter_mm'] == 0.8


def test_printer_roundtrip_mismatch_preserves_existing_output(tmp_path, monkeypatch):
    output = tmp_path / 'existing.ptr'
    output.write_text('keep existing configuration')
    original = profiles.dumps_printer_profile
    def corrupt(settings, *args, **kwargs):
        changed = deepcopy(settings)
        changed['support']['spacing_mm'] = 7.0
        return original(changed, *args, **kwargs)
    monkeypatch.setattr(profiles, 'dumps_printer_profile', corrupt)
    with pytest.raises(VoxelMillError, match='does not reload identically'):
        profiles.save_printer_profile(output, resolve_settings())
    assert output.read_text() == 'keep existing configuration'


def test_profile_roundtrip_quotes_scalar_motion_keys(tmp_path):
    settings = resolve_settings(overrides={'printer': {'motion': {'custom.setting': 2.5}}})
    output = tmp_path / 'motion.ptr'
    assert profiles.save_printer_profile(output, settings)['round_trip'] == 'identical'
    assert resolve_settings(output)['printer']['motion']['custom.setting'] == 2.5
