"""CPU topology detection and the affinity mask it produces.

The regression these guard is concrete: `execution_limits` used to pin every
thread to the lowest `workers` cpu numbers, which on a machine that numbers SMT
siblings adjacently is the two hyperthreads of a single physical core.
"""
import os
import pytest
from voxelmill import topology as t
from voxelmill.contracts import ResourceBudget, VoxelMillError


def _topology(cores, perf, eff, source='test'):
    allowed = tuple(sorted(c for core in cores for c in core))
    return t.Topology(allowed, tuple(perf), tuple(eff), tuple(cores), source)


#: Eight SMT performance cores then sixteen single-thread efficiency cores,
#: numbered the way a 13900HX numbers them.
HYBRID = _topology([(i, i + 1) for i in range(0, 16, 2)] + [(i,) for i in range(16, 32)],
                   range(16), range(16, 32))


def test_parse_cpu_list_handles_ranges_and_mixtures():
    assert t._parse_cpu_list('0-3') == {0, 1, 2, 3}
    assert t._parse_cpu_list('0,2,4') == {0, 2, 4}
    assert t._parse_cpu_list('0-3,8-9') == {0, 1, 2, 3, 8, 9}
    assert t._parse_cpu_list('') == set()


def test_hybrid_topology_reports_its_shape():
    assert HYBRID.n_logical == 32
    assert HYBRID.n_physical == 24
    assert HYBRID.n_perf == 16 and HYBRID.n_eff == 16
    # The widest core, not the 32/24 average, which describes no real core.
    assert HYBRID.smt_factor == 2
    assert HYBRID.describe()['source'] == 'test'


def test_two_workers_land_on_two_physical_cores_not_one():
    """The whole point. Adjacent cpu numbers here are siblings of one core."""
    chosen = t.select_cpus(2, topology=HYBRID)
    assert chosen == (0, 2)
    cores = {core for core in HYBRID.cores if set(chosen) & set(core)}
    assert len(cores) == 2


def test_selection_fills_physical_cores_before_taking_siblings():
    chosen = t.select_cpus(8, topology=HYBRID)
    assert chosen == (0, 2, 4, 6, 8, 10, 12, 14)
    assert len(chosen) == len({c // 2 for c in chosen})


def test_selection_prefers_performance_cores_then_spills_to_efficiency():
    chosen = t.select_cpus(12, topology=HYBRID)
    assert chosen[:8] == (0, 2, 4, 6, 8, 10, 12, 14)
    assert set(chosen[8:]) <= set(HYBRID.eff_cpus)


def test_efficiency_policy_inverts_the_preference():
    chosen = t.select_cpus(4, policy='efficiency', topology=HYBRID)
    assert set(chosen) <= set(HYBRID.eff_cpus)


def test_all_policy_and_oversized_requests_return_every_allowed_cpu():
    assert t.select_cpus(2, policy='all', topology=HYBRID) == HYBRID.allowed
    assert t.select_cpus(999, topology=HYBRID) == HYBRID.allowed


def test_uniform_topology_still_spreads_across_cores():
    uniform = _topology([(i, i + 1) for i in range(0, 8, 2)], range(8), [])
    assert t.select_cpus(4, topology=uniform) == (0, 2, 4, 6)


def test_single_core_machine_degrades_cleanly():
    lone = _topology([(0,)], [0], [])
    assert t.select_cpus(4, topology=lone) == (0,)
    assert t.default_workers(lone) == 1


def test_fallback_is_used_when_detection_is_refused(monkeypatch):
    """Mac, Windows and containers must all work with no topology at all."""
    monkeypatch.setenv('VOXELMILL_NO_TOPOLOGY', '1')
    detected = t.detect(refresh=True)
    assert detected.source == 'fallback'
    assert detected.n_logical == detected.n_physical >= 1
    assert detected.n_eff == 0
    assert t.select_cpus(2, topology=detected)[:1] == detected.allowed[:1]
    t.detect(refresh=True)


def test_detection_on_this_machine_is_self_consistent():
    detected = t.detect(refresh=True)
    assert detected.n_logical >= 1
    assert detected.n_perf + detected.n_eff == detected.n_logical
    assert sorted(c for core in detected.cores for c in core) == list(detected.allowed)
    assert 1 <= t.default_workers(detected) <= 32


def test_default_workers_counts_physical_cores_not_threads():
    assert t.default_workers(HYBRID) == 24


def test_default_workers_is_clamped_to_the_budget_range():
    huge = _topology([(i,) for i in range(64)], range(64), [])
    assert t.default_workers(huge) == 32


@pytest.mark.skipif(not hasattr(os, 'sched_setaffinity'), reason='needs Linux affinity')
def test_execution_limits_pins_to_distinct_cores_and_restores(tmp_path):
    from voxelmill.resources import execution_limits
    before = os.sched_getaffinity(0)
    detected = t.detect(refresh=True)
    with execution_limits(ResourceBudget(memory_gib=1, workers=2), hard_memory=False):
        during = os.sched_getaffinity(0)
    assert os.sched_getaffinity(0) == before
    assert during == set(t.select_cpus(2, topology=detected))
    if detected.smt_factor > 1 and detected.n_physical > 1:
        siblings = [core for core in detected.cores if during <= set(core)]
        assert not siblings, 'both workers landed on one physical core'


def test_budget_rejects_an_unknown_worker_policy():
    with pytest.raises(VoxelMillError):
        ResourceBudget(worker_policy='bogus')
    assert ResourceBudget().worker_policy == 'performance'


def _resolved(argv):
    from voxelmill.cli import build_parser, _overrides
    from voxelmill.config import resolve_settings
    args = build_parser().parse_args(argv)
    return resolve_settings(overrides=_overrides(args))['resources']


def test_workers_auto_resolves_to_physical_cores():
    assert _resolved(['prepare', 'x.stl', '--workers', 'auto'])['workers'] == t.default_workers()


def test_workers_still_accepts_a_plain_integer_and_defaults_to_two():
    assert _resolved(['prepare', 'x.stl', '--workers', '7'])['workers'] == 7
    assert _resolved(['prepare', 'x.stl'])['workers'] == 2


def test_worker_policy_reaches_the_resources_table():
    resolved = _resolved(['prepare', 'x.stl', '--worker-policy', 'efficiency'])
    assert resolved['worker_policy'] == 'efficiency'
    assert _resolved(['prepare', 'x.stl'])['worker_policy'] == 'performance'


def test_a_bad_workers_value_is_a_reported_error_not_a_traceback(capsys):
    """argparse `type=` callables run outside main's handler; this must not."""
    from voxelmill.cli import main
    assert main(['prepare', 'fixtures/shapes/sphere.stl', '--workers', 'bogus']) == 3
    import json
    error = json.loads(capsys.readouterr().err)['error']
    assert error['code'] == 'invalid_option'
    assert 'auto' in error['message']


def test_worker_policy_is_a_dropdown_in_the_generated_settings_table():
    pytest.importorskip('PySide6')
    from voxelmill.gui.settings_table import build_descriptors
    descriptor = next(d for d in build_descriptors() if d.path == 'resources.worker_policy')
    assert descriptor.choices == ('performance', 'efficiency', 'all')
